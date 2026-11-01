"""La interfaz "Mundo": lo único que la IA necesita de un juego.

La implementan el simulador de texto y, más adelante, el puente con ARMA 3. El cerebro de
la IA no sabe (ni le importa) cuál de los dos tiene delante.

Ciclo de una partida (ver ``partida.Partida``)::

    while not mundo.terminado():
        for bando in bandos:
            obs = await mundo.observar(bando)          # qué sabe ese bando ahora
            chat = await mundo.recibir_chat(bando)     # mensajes nuevos de los jugadores
            salida = await cerebro.paso(obs, chat)     # la IA piensa
            await mundo.ejecutar(bando, salida.acciones)
            await mundo.enviar_chat(bando, salida.chat)
        await mundo.avanzar()                          # simulador: un paso; ARMA: esperar
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..modelo import (
    Accion,
    GrupoNuevo,
    MensajeChat,
    ObjetoMapa,
    Observacion,
    ResultadoAccion,
    ResultadoMision,
    VistaGlobal,
)


class Mundo(ABC):
    @abstractmethod
    async def observar(self, bando: str) -> Observacion:
        """Estado conocido por `bando`, con los eventos ocurridos desde la última observación."""

    @abstractmethod
    async def ejecutar(self, bando: str, acciones: list[Accion]) -> list[ResultadoAccion]:
        """Aplica las órdenes. Las inválidas se rechazan con un motivo (no lanzan excepción)."""

    @abstractmethod
    async def recibir_chat(self, bando: str) -> list[MensajeChat]:
        """Mensajes de jugadores humanos pendientes para ese bando."""

    @abstractmethod
    async def enviar_chat(self, bando: str, mensajes: list[MensajeChat]) -> None:
        """Muestra a los jugadores los mensajes de la IA (en ARMA: sideChat / globalChat)."""

    @abstractmethod
    async def avanzar(self) -> None:
        """Deja pasar el tiempo: un paso en el simulador, unos segundos reales en ARMA 3."""

    @abstractmethod
    def terminado(self) -> bool: ...

    @abstractmethod
    def resultado(self) -> ResultadoMision | None:
        """Resultado final, o None si la misión no ha terminado."""

    @abstractmethod
    def finalizar(self, motivo: str) -> ResultadoMision:
        """Termina la misión ahora (p. ej. al interrumpirla) y devuelve el resultado."""


class ControlZeus(ABC):
    """Los poderes de Zeus (el director de juego): ver todo el mundo y cambiarlo en directo.

    El simulador los implementa; en ARMA 3 corresponderán a órdenes de la extensión
    (createGroup/createUnit, setOvercast/skipTime, composiciones con createVehicle y
    hideObjectGlobal, mensajes globales).
    """

    @abstractmethod
    def vista_global(self) -> VistaGlobal:
        """Toda la verdad del mundo, sin niebla de guerra."""

    @abstractmethod
    async def crear_grupo(self, bando: str, grupo: GrupoNuevo) -> str:
        """Hace aparecer un grupo y devuelve su id."""

    @abstractmethod
    async def cambiar_condiciones(self, hora: str | None = None, clima: str | None = None) -> None:
        """Cambia la hora del día o el clima."""

    @abstractmethod
    async def colocar_objeto(self, objeto: ObjetoMapa) -> None:
        """Coloca un elemento del decorado (fortificación, minas, barricada, humo...)."""

    @abstractmethod
    async def anunciar(self, texto: str) -> None:
        """Mensaje de Zeus a todos los jugadores."""
