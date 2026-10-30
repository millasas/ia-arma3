"""El piloto: transporte de tropas, apoyo aéreo y vuelo seguro."""

from __future__ import annotations

from ..modelo import Accion, Observacion, TipoAccion, UnidadPropia
from ..texto import recortar
from . import prompts, reglas
from .base import ResultadoAgente
from .esquemas import AccionAire, DecisionPiloto
from .lider import AgenteUnidad


class Piloto(AgenteUnidad):
    rol = "piloto"
    radio_contactos = 2000.0
    intervalo_attr = "intervalo_piloto_s"

    @property
    def usar_llm(self) -> bool:
        return self.ctx.config.usar_llm_pilotos

    async def decidir_y_actuar(self, obs: Observacion, unidad: UnidadPropia) -> ResultadoAgente:
        consulta = self._consulta(obs, unidad) + " helicoptero vuelo aterrizaje lz"
        mensajes = self.tomar_bandeja()
        avisos, self.avisos = self.avisos, []
        self.orden_nueva = False
        orden = self.orden
        decision = await self.consultar(
            tiempo=obs.tiempo,
            sistema=prompts.sistema_piloto(self.indicativo, unidad, self.ctx.perfil,
                                           self.ctx.perfil.indicativo_mando),
            usuario=prompts.usuario_unidad(obs, unidad, orden, self.orden_texto, self.orden_jugador,
                                           mensajes, self.historial, avisos, self.radio_contactos),
            esquema=DecisionPiloto,
            fallback=lambda: reglas.decision_piloto(unidad, orden, obs),
            consulta_lecciones=consulta,
        )
        self._apuntar(obs, decision)
        acciones = self.a_acciones(decision, obs, unidad)
        return ResultadoAgente(acciones, self._salientes(obs.tiempo, decision.informe, None, decision.mensajes))

    def a_acciones(self, decision: DecisionPiloto, obs: Observacion, unidad: UnidadPropia) -> list[Accion]:
        if decision.accion == AccionAire.CONTINUAR:
            return []
        tipo = TipoAccion(decision.accion.value)
        destino = self._destino(decision.destino, obs)
        objetivo = decision.objetivo
        orden_destino = self._destino(self.orden.destino, obs) if self.orden else None

        if tipo == TipoAccion.DESEMBARCAR:
            if unidad.en_vuelo:
                self.avisos.append("No se puede desembarcar en vuelo: primero aterriza.")
                tipo, destino = TipoAccion.ATERRIZAR, destino or unidad.posicion
            elif not unidad.pasajeros:
                self.avisos.append("No llevas pasajeros que desembarcar.")
                return []
            else:
                destino = None
        elif tipo == TipoAccion.APOYO_AEREO:
            contacto = obs.contacto(objetivo)
            destino = destino or (contacto.posicion if contacto else None) or orden_destino
            if not unidad.armada or destino is None:
                self.avisos.append("Apoyo aéreo imposible: sin armamento o sin blanco.")
                return [Accion(unidad=unidad.id, tipo=TipoAccion.ESPERAR, motivo="Sin blanco")]
            objetivo = contacto.id if contacto else objetivo
        elif tipo in (TipoAccion.VOLAR, TipoAccion.ATERRIZAR):
            if destino is None and decision.destino:
                self.avisos.append(f"No reconocí el destino '{recortar(decision.destino, 40)}'.")
            destino = destino or orden_destino
            if destino is None:
                return [Accion(unidad=unidad.id, tipo=TipoAccion.ESPERAR, motivo="Sin destino")]
        else:
            destino = None

        return [
            Accion(
                unidad=unidad.id,
                tipo=tipo,
                destino=destino,
                objetivo=objetivo,
                altitud=decision.altitud,
                motivo=recortar(decision.evaluacion, 200),
            )
        ]
