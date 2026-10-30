"""El comandante: visión de conjunto, intención y órdenes a cada unidad."""

from __future__ import annotations

from collections.abc import Iterator

from ..comunicacion import TipoMensaje
from ..modelo import Observacion
from ..texto import normalizar, reloj
from . import prompts, reglas
from .base import Agente, ContextoAgentes, Saliente
from .esquemas import TAREAS_AEREAS, DecisionComandante, OrdenMando, TareaMando

EVENTOS_MANDO = frozenset(
    {"objetivo_completado", "objetivo_fallido", "unidad_destruida", "desembarco", "contacto_destruido"}
)
NOMBRE_TAREA = {
    TareaMando.MOVER: "muévase a",
    TareaMando.ATACAR: "ataque",
    TareaMando.DEFENDER: "defienda",
    TareaMando.RECONOCER: "reconozca",
    TareaMando.RETIRARSE: "repliéguese a",
    TareaMando.ESPERAR: "espere",
    TareaMando.EMBARCAR: "embarque en",
    TareaMando.TRANSPORTE: "transporte",
    TareaMando.APOYO_AEREO: "apoyo aéreo sobre",
    TareaMando.REGRESAR_BASE: "regrese a la base",
}
REPETIR_ORDEN_S = 300.0


class Comandante(Agente):
    rol = "comandante"

    def __init__(self, ctx: ContextoAgentes, agente_id: str, indicativo: str) -> None:
        super().__init__(ctx, agente_id, indicativo)
        self.intencion = ""
        self._ultimas: dict[str, tuple[tuple[str, str, str], float]] = {}
        self._contactos_vistos: set[str] = set()

    @property
    def usar_llm(self) -> bool:
        return self.ctx.config.usar_llm_comandante

    def necesita_decidir(self, obs: Observacion) -> bool:
        if self.ultima_decision_t is None:
            return True
        transcurrido = obs.tiempo - self.ultima_decision_t
        if transcurrido >= self.ctx.config.intervalo_comandante_s:
            return True
        if transcurrido < self.ctx.config.intervalo_minimo_s:
            return False
        if any(e.tipo in EVENTOS_MANDO for e in obs.eventos):
            return True
        if any(m.tipo in (TipoMensaje.SOLICITUD, TipoMensaje.JUGADOR) for m in self.bandeja):
            return True
        # Contactos que el mando todavía no ha tenido en cuenta.
        return bool({c.id for c in obs.contactos} - self._contactos_vistos)

    def _consulta(self, obs: Observacion) -> str:
        partes = [f"{o.tipo.value} {o.nombre} {o.descripcion}" for o in obs.objetivos]
        partes += [c.tipo.value for c in obs.contactos]
        partes += [e.tipo for e in obs.eventos]
        partes += [m.texto for m in self.bandeja]
        return " ".join(partes)

    async def decidir(self, obs: Observacion) -> DecisionComandante:
        consulta = self._consulta(obs)
        mensajes = self.tomar_bandeja()
        self._contactos_vistos |= {c.id for c in obs.contactos}
        decision = await self.consultar(
            tiempo=obs.tiempo,
            sistema=prompts.sistema_comandante(self.indicativo, self.ctx.perfil),
            usuario=prompts.usuario_comandante(obs, mensajes, self.intencion, self.avisos),
            esquema=DecisionComandante,
            fallback=lambda: reglas.decision_comandante(obs, self.ctx.perfil),
            consulta_lecciones=consulta,
        )
        self.intencion = decision.intencion
        self.avisos = []
        self.historial.append(f"[{reloj(obs.tiempo)}] {decision.intencion}")
        return decision

    def _texto_orden(self, indicativo: str, orden: OrdenMando) -> str:
        texto = f"{indicativo}, aquí {self.indicativo}: {NOMBRE_TAREA[orden.tarea]}"
        if orden.tarea == TareaMando.TRANSPORTE and orden.objetivo:
            texto += f" a {orden.objetivo}"
        elif orden.objetivo and orden.tarea != TareaMando.EMBARCAR:
            texto += f" {orden.objetivo}"
        if orden.tarea == TareaMando.EMBARCAR and orden.objetivo:
            texto += f" {orden.objetivo}"
        if orden.destino:
            if orden.tarea == TareaMando.TRANSPORTE:
                conector = " hasta "
            elif NOMBRE_TAREA[orden.tarea].endswith(" a"):
                conector = " "
            else:
                conector = " en "
            texto += conector + orden.destino
        return texto + "." + (f" {orden.detalles}" if orden.detalles else "")

    def _repetida(self, unidad_id: str, orden: OrdenMando, tiempo: float) -> bool:
        firma = (orden.tarea.value, normalizar(orden.destino or ""), normalizar(orden.objetivo or ""))
        previa = self._ultimas.get(unidad_id)
        if previa and previa[0] == firma and tiempo - previa[1] < REPETIR_ORDEN_S:
            return True
        self._ultimas[unidad_id] = (firma, tiempo)
        return False

    def mensajes_de_ordenes(self, decision: DecisionComandante, obs: Observacion) -> Iterator[Saliente]:
        """Convierte las órdenes en mensajes de radio, validando cada una."""
        for orden in decision.ordenes:
            unidad = obs.buscar_unidad(orden.unidad)
            if unidad is None or not unidad.operativa:
                self.avisos.append(f"La unidad '{orden.unidad}' no existe o no está operativa.")
                continue
            aerea = orden.tarea in TAREAS_AEREAS
            if aerea and not unidad.es_aerea:
                self.avisos.append(f"'{orden.tarea.value}' solo vale para helicópteros, no para {unidad.indicativo}.")
                continue
            if unidad.es_aerea and orden.tarea in (TareaMando.DEFENDER, TareaMando.EMBARCAR):
                self.avisos.append(f"'{orden.tarea.value}' no vale para el helicóptero {unidad.indicativo}.")
                continue
            if orden.tarea == TareaMando.APOYO_AEREO and not unidad.armada:
                self.avisos.append(f"{unidad.indicativo} no va armado: no puede dar apoyo aéreo.")
                continue
            orden = orden.model_copy(update={"unidad": unidad.indicativo})
            if self._repetida(unidad.id, orden, obs.tiempo):
                continue
            yield Saliente(unidad.id, self._texto_orden(unidad.indicativo, orden), TipoMensaje.ORDEN,
                           {"orden": orden.model_dump(mode="json")})
            if orden.tarea == TareaMando.TRANSPORTE:
                carga = obs.buscar_unidad(orden.objetivo)
                if carga is not None and not carga.es_aerea and carga.operativa:
                    embarque = OrdenMando(
                        unidad=carga.indicativo,
                        tarea=TareaMando.EMBARCAR,
                        objetivo=unidad.indicativo,
                        detalles=f"{unidad.indicativo} os llevará a {orden.destino or 'vuestro destino'}.",
                    )
                    self._repetida(carga.id, embarque, obs.tiempo)
                    yield Saliente(carga.id, self._texto_orden(carga.indicativo, embarque), TipoMensaje.ORDEN,
                                   {"orden": embarque.model_dump(mode="json")})
        if decision.mensaje_general:
            yield Saliente("todos", decision.mensaje_general, TipoMensaje.GENERAL)
