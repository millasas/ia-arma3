"""Agentes de unidad: base común y líder de escuadra (tierra)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..comunicacion import MensajeRadio, TipoMensaje
from ..geo import Posicion, parsear_posicion
from ..modelo import Accion, EstadoUnidad, MensajeChat, Observacion, Persona, Postura, TipoAccion, UnidadPropia
from ..texto import normalizar, recortar, reloj
from . import prompts, reglas
from .base import Agente, ContextoAgentes, ResultadoAgente, Saliente
from .esquemas import AccionTierra, DecisionLider, MensajeSaliente, OrdenMando, RespuestaEnlace, Solicitud

REPETICION_RADIO_S = 120.0
EVENTOS_UNIDAD = frozenset(
    {
        "contacto",
        "bajo_fuego",
        "bajas",
        "accion_completada",
        "accion_fallida",
        "desembarco",
        "embarque",
        "aterrizaje",
        "danos_aeronave",
        "municion_baja",
    }
)


class AgenteUnidad(Agente):
    """Agente que manda una unidad concreta del juego (escuadra o aeronave)."""

    radio_contactos = 800.0
    intervalo_attr = "intervalo_lider_s"

    def __init__(self, ctx: ContextoAgentes, agente_id: str, indicativo: str,
                 persona: Persona | None = None, superior: str | None = "mando") -> None:
        super().__init__(ctx, agente_id, indicativo)
        self.persona = persona
        # A quién informa por radio. None = no hay mando: informa a todas las unidades del bando.
        self.superior = superior
        self.orden: OrdenMando | None = None
        self.orden_texto = ""
        self.orden_jugador = ""
        self.orden_nueva = False
        self._ultimos_envios: dict[str, float] = {}

    async def decidir_y_actuar(self, obs: Observacion, unidad: UnidadPropia) -> ResultadoAgente:
        raise NotImplementedError

    def recibir(self, mensajes: list[MensajeRadio]) -> None:
        super().recibir(mensajes)
        for m in mensajes:
            if m.tipo == TipoMensaje.ORDEN and "orden" in m.datos:
                self.orden = OrdenMando.model_validate(m.datos["orden"])
                self.orden_texto = m.texto
                self.orden_jugador = ""
                self.orden_nueva = True
            elif m.tipo == TipoMensaje.JUGADOR and self.ctx.config.jugadores_al_mando:
                self.orden_jugador = m.texto
                self.orden_nueva = True

    def asignar_orden(self, orden: OrdenMando, texto: str = "") -> None:
        """Tarea propia del NPC (la que le da Zeus al crear la misión), sin pasar por la radio."""
        self.orden = orden
        self.orden_texto = texto
        self.orden_nueva = True

    def necesita_decidir(self, obs: Observacion, unidad: UnidadPropia) -> bool:
        if not unidad.operativa:
            return False
        if unidad.estado == EstadoUnidad.EMBARCADA:
            return False
        if self.ultima_decision_t is None or self.orden_nueva or self.bandeja:
            return True
        if any(e.unidad == unidad.id and e.tipo in EVENTOS_UNIDAD for e in obs.eventos):
            return True
        transcurrido = obs.tiempo - self.ultima_decision_t
        if (
            unidad.accion_actual is None
            and self.orden is not None
            and transcurrido >= self.ctx.config.intervalo_minimo_s
        ):
            return True
        return transcurrido >= float(getattr(self.ctx.config, self.intervalo_attr))

    def _consulta(self, obs: Observacion, unidad: UnidadPropia) -> str:
        partes = [unidad.tipo.value]
        if self.orden is not None:
            partes += [self.orden.tarea.value, self.orden.detalles]
        partes += [e.tipo for e in obs.eventos if e.unidad == unidad.id]
        cerca = [c for c in obs.contactos if c.posicion.distancia(unidad.posicion) < self.radio_contactos]
        partes += [c.tipo.value for c in cerca]
        if cerca:
            partes.append("contacto enemigo")
        partes += [r.tipo for r in obs.referencias if r.posicion.distancia(unidad.posicion) < 1000]
        partes += [m.texto for m in self.bandeja]
        return " ".join(partes)

    def _destino(self, texto: str | None, obs: Observacion) -> Posicion | None:
        return parsear_posicion(texto, obs.lugares(), obs.limites())

    def _salientes(self, tiempo: float, informe: str | None, solicitud: Solicitud | None,
                   mensajes: list[MensajeSaliente]) -> list[Saliente]:
        salientes = []
        destino = self.superior or "todos"
        if informe:
            salientes.append(Saliente(destino, informe, TipoMensaje.INFORME))
        if solicitud is not None:
            texto = f"Solicito {solicitud.tipo.value.replace('_', ' ')}"
            texto += f" en {solicitud.destino}" if solicitud.destino else ""
            texto += f". {solicitud.detalles}" if solicitud.detalles else "."
            salientes.append(Saliente(destino, texto, TipoMensaje.SOLICITUD,
                                      {"solicitud": solicitud.model_dump(mode="json")}))
        for m in mensajes:
            salientes.append(Saliente(m.para, m.texto, TipoMensaje.COORDINACION))
        return [s for s in salientes if not self._repetido(s, tiempo)]

    def _repetido(self, saliente: Saliente, tiempo: float) -> bool:
        """Disciplina de radio: no repetir el mismo mensaje al mismo destino en 2 minutos."""
        clave = f"{normalizar(saliente.para)}|{normalizar(saliente.texto)}"
        previo = self._ultimos_envios.get(clave)
        if previo is not None and tiempo - previo < REPETICION_RADIO_S:
            return True
        self._ultimos_envios[clave] = tiempo
        return False

    @property
    def nombre_chat(self) -> str:
        if self.persona is None:
            return self.indicativo
        return f"{f'{self.persona.rango} ' if self.persona.rango else ''}{self.persona.nombre} ({self.indicativo})"

    def nombres(self) -> list[str]:
        """Cómo pueden llamarle los jugadores: indicativo, nombre, apellido, rango + apellido."""
        nombres = [self.indicativo]
        if self.persona is not None:
            partes = self.persona.nombre.split()
            nombres += [self.persona.nombre, partes[-1]]
            if self.persona.rango:
                nombres.append(f"{self.persona.rango} {partes[-1]}")
        return [normalizar(n) for n in nombres if len(normalizar(n)) >= 3]

    async def charlar(
        self, mensaje: MensajeChat, obs: Observacion, unidad: UnidadPropia
    ) -> tuple[MensajeChat, list[Saliente]]:
        """Un jugador habla directamente con este NPC: contesta en personaje."""
        if self.ctx.config.jugadores_al_mando:
            self.orden_jugador = mensaje.texto
            self.orden_nueva = True
        orden = self.orden
        decision = await self.consultar(
            tiempo=obs.tiempo,
            sistema=prompts.sistema_npc(self.indicativo, unidad, self.ctx.perfil, self.persona),
            usuario=prompts.usuario_npc(mensaje, obs, unidad, orden, self.historial),
            esquema=RespuestaEnlace,
            fallback=lambda: reglas.respuesta_npc(mensaje, unidad, obs, self.indicativo, orden, self.persona),
            consulta_lecciones=mensaje.texto,
            rol="npc",
        )
        respuesta = MensajeChat(autor=self.nombre_chat, texto=decision.respuesta, canal=mensaje.canal,
                                tiempo=obs.tiempo, es_ia=True)
        salientes = [Saliente(r.para, f"De {mensaje.autor} (jugador): {r.texto}", TipoMensaje.JUGADOR,
                              {"autor": mensaje.autor}) for r in decision.reenviar]
        return respuesta, salientes

    def _apuntar(self, obs: Observacion, decision: BaseModel, extra: str = "") -> None:
        datos: dict[str, Any] = decision.model_dump()
        accion = datos.get("accion")
        texto = getattr(accion, "value", accion)
        destino = f" → {datos['destino']}" if datos.get("destino") else ""
        self.historial.append(f"[{reloj(obs.tiempo)}] {texto}{destino}{extra}")


class LiderEscuadra(AgenteUnidad):
    rol = "lider"
    radio_contactos = 800.0
    intervalo_attr = "intervalo_lider_s"

    @property
    def usar_llm(self) -> bool:
        return self.ctx.config.usar_llm_lideres

    async def decidir_y_actuar(self, obs: Observacion, unidad: UnidadPropia) -> ResultadoAgente:
        consulta = self._consulta(obs, unidad)
        mensajes = self.tomar_bandeja()
        avisos, self.avisos = self.avisos, []
        self.orden_nueva = False
        orden = self.orden
        decision = await self.consultar(
            tiempo=obs.tiempo,
            sistema=prompts.sistema_lider(self.indicativo, unidad, self.ctx.perfil,
                                          self.superior and self.ctx.perfil.indicativo_mando, self.persona),
            usuario=prompts.usuario_unidad(obs, unidad, orden, self.orden_texto, self.orden_jugador,
                                           mensajes, self.historial, avisos, self.radio_contactos),
            esquema=DecisionLider,
            fallback=lambda: reglas.decision_lider(unidad, orden, obs, self.persona),
            consulta_lecciones=consulta,
        )
        self._apuntar(obs, decision)
        acciones = self.a_acciones(decision, obs, unidad)
        return ResultadoAgente(acciones, self._salientes(obs.tiempo, decision.informe,
                                                         decision.solicitud, decision.mensajes))

    def a_acciones(self, decision: DecisionLider, obs: Observacion, unidad: UnidadPropia) -> list[Accion]:
        """Traduce la decisión del líder a una acción concreta del motor."""
        if decision.accion == AccionTierra.CONTINUAR:
            return []
        tipo = TipoAccion(decision.accion.value)
        destino = self._destino(decision.destino, obs)
        objetivo = decision.objetivo
        orden_destino = self._destino(self.orden.destino, obs) if self.orden else None

        if tipo == TipoAccion.ATACAR:
            contacto = obs.contacto(objetivo)
            if destino is None and contacto is not None:
                destino = contacto.posicion
            destino = destino or orden_destino
            if destino is None:
                cercano = min(obs.contactos, key=lambda c: c.posicion.distancia(unidad.posicion), default=None)
                destino = cercano.posicion if cercano else None
                objetivo = cercano.id if cercano else None
            if destino is None:
                self.avisos.append("Ordenaste atacar sin blanco ni destino: me quedo defendiendo.")
                tipo, destino = TipoAccion.DEFENDER, unidad.posicion
            elif contacto is not None:
                objetivo = contacto.id
        elif tipo in (TipoAccion.MOVER, TipoAccion.RECONOCER, TipoAccion.RETIRARSE):
            if destino is None and decision.destino:
                self.avisos.append(f"No reconocí el destino '{recortar(decision.destino, 40)}'.")
            destino = destino or orden_destino
            if destino is None and tipo == TipoAccion.RETIRARSE:
                destino = reglas.punto_seguro(obs, unidad)
            if destino is None:
                tipo = TipoAccion.ESPERAR
        elif tipo == TipoAccion.DEFENDER:
            destino = destino or unidad.posicion
        elif tipo == TipoAccion.EMBARCAR:
            nombre = objetivo or (self.orden.objetivo if self.orden else None)
            helicoptero = obs.buscar_unidad(nombre)
            if helicoptero is None or not helicoptero.es_aerea:
                self.avisos.append(f"No encuentro el helicóptero '{nombre}' para embarcar.")
                return [Accion(unidad=unidad.id, tipo=TipoAccion.ESPERAR, motivo="Sin transporte")]
            objetivo, destino = helicoptero.id, None
        else:
            destino = None

        postura = decision.postura
        if postura is None and tipo == TipoAccion.RECONOCER:
            postura = Postura.SIGILO
        return [
            Accion(
                unidad=unidad.id,
                tipo=tipo,
                destino=destino,
                objetivo=objetivo,
                postura=postura,
                motivo=recortar(decision.evaluacion, 200),
            )
        ]
