"""Enlace con los jugadores: lee el chat del juego, responde en personaje y reenvía peticiones."""

from __future__ import annotations

from collections import deque

from ..comunicacion import TipoMensaje
from ..modelo import MensajeChat, Observacion
from . import prompts, reglas
from .base import Agente, ContextoAgentes, Saliente
from .esquemas import RespuestaEnlace


class Enlace(Agente):
    rol = "enlace"

    def __init__(self, ctx: ContextoAgentes, agente_id: str, indicativo: str) -> None:
        super().__init__(ctx, agente_id, indicativo)
        self.conversacion: deque[MensajeChat] = deque(maxlen=12)

    @property
    def usar_llm(self) -> bool:
        return self.ctx.config.usar_llm_enlace

    async def atender(
        self, mensajes: list[MensajeChat], obs: Observacion
    ) -> tuple[list[MensajeChat], list[Saliente]]:
        """Responde a los mensajes de los jugadores. Devuelve (respuestas de chat, mensajes de radio)."""
        respuestas: list[MensajeChat] = []
        salientes: list[Saliente] = []
        for mensaje in mensajes[: self.ctx.config.max_mensajes_chat_por_paso]:
            historial = list(self.conversacion)
            self.conversacion.append(mensaje)

            def fallback(mensaje: MensajeChat = mensaje) -> RespuestaEnlace:
                return reglas.respuesta_enlace(mensaje, obs, self.indicativo)

            decision = await self.consultar(
                tiempo=obs.tiempo,
                sistema=prompts.sistema_enlace(self.indicativo, self.ctx.perfil,
                                               self.ctx.perfil.indicativo_mando),
                usuario=prompts.usuario_enlace(mensaje, obs, historial),
                esquema=RespuestaEnlace,
                fallback=fallback,
                consulta_lecciones=mensaje.texto,
            )
            respuesta = MensajeChat(autor=self.indicativo, texto=decision.respuesta,
                                    canal=mensaje.canal, tiempo=obs.tiempo, es_ia=True)
            respuestas.append(respuesta)
            self.conversacion.append(respuesta)
            for reenvio in decision.reenviar:
                salientes.append(
                    Saliente(reenvio.para, f"De {mensaje.autor} (jugador): {reenvio.texto}",
                             TipoMensaje.JUGADOR, {"autor": mensaje.autor})
                )
        return respuestas, salientes
