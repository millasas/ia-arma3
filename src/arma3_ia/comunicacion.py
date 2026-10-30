"""Red de radio entre agentes.

Cada agente (mando, enlace, escuadras, pilotos) tiene una bandeja de entrada. Los mensajes
se direccionan por id o indicativo, tolerando cómo los escriba el modelo ("Alfa-1",
"escuadra alfa 1", "ALFA"). Con ``retardo_s`` se simulan comunicaciones lentas (WWII).
Todo lo transmitido queda en ``historial`` para el informe post-misión.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .texto import normalizar

TODOS = frozenset({"*", "todos", "todas", "a todos", "todas las unidades", "todas las estaciones"})


class TipoMensaje(str, Enum):
    ORDEN = "orden"
    INFORME = "informe"
    SOLICITUD = "solicitud"
    COORDINACION = "coordinacion"
    JUGADOR = "jugador"
    GENERAL = "general"


class MensajeRadio(BaseModel):
    de: str
    de_indicativo: str
    para: str
    para_indicativo: str = ""
    texto: str
    tipo: TipoMensaje = TipoMensaje.GENERAL
    tiempo: float = 0.0
    entrega: float = 0.0
    datos: dict[str, Any] = Field(default_factory=dict)


class RedRadio:
    def __init__(self, retardo_s: float = 0.0) -> None:
        self.retardo_s = retardo_s
        self._alias: dict[str, str] = {}
        self._indicativos: dict[str, str] = {}
        self._bandejas: dict[str, list[MensajeRadio]] = {}
        self.historial: list[MensajeRadio] = []
        self._nuevos: list[MensajeRadio] = []

    @property
    def agentes(self) -> list[str]:
        return list(self._indicativos)

    def indicativo(self, agente_id: str) -> str:
        return self._indicativos.get(agente_id, agente_id)

    def registrar(self, agente_id: str, indicativo: str, alias: Iterable[str] = ()) -> None:
        self._indicativos[agente_id] = indicativo
        self._bandejas.setdefault(agente_id, [])
        for nombre in (agente_id, indicativo, *alias):
            clave = normalizar(nombre)
            if clave:
                self._alias[clave] = agente_id

    def dar_de_baja(self, agente_id: str) -> None:
        self._indicativos.pop(agente_id, None)
        self._bandejas.pop(agente_id, None)
        self._alias = {k: v for k, v in self._alias.items() if v != agente_id}

    def resolver(self, destino: str) -> list[str]:
        """Ids de los agentes a los que va dirigido `destino` (vacío si no se reconoce)."""
        norm = normalizar(destino)
        if destino.strip() in TODOS or norm in TODOS:
            return list(self._indicativos)
        if norm in self._alias:
            return [self._alias[norm]]
        # Coincidencia parcial ("escuadra alfa 1" -> "alfa 1"): gana el alias más largo.
        for alias, agente in sorted(self._alias.items(), key=lambda par: -len(par[0])):
            if len(alias) >= 3 and (f" {alias} " in f" {norm} " or (len(norm) >= 3 and norm in alias)):
                return [agente]
        return []

    def enviar(
        self,
        de: str,
        para: str,
        texto: str,
        tipo: TipoMensaje = TipoMensaje.GENERAL,
        tiempo: float = 0.0,
        datos: dict[str, Any] | None = None,
        retardo_s: float | None = None,
    ) -> list[MensajeRadio]:
        """Transmite un mensaje. Devuelve las copias entregadas (vacío si nadie lo recibe)."""
        destinos = [d for d in self.resolver(para) if d != de]
        if not destinos or not texto.strip():
            return []
        retardo = self.retardo_s if retardo_s is None else retardo_s
        entregados = [
            MensajeRadio(
                de=de,
                de_indicativo=self.indicativo(de),
                para=d,
                para_indicativo=self.indicativo(d),
                texto=texto.strip(),
                tipo=tipo,
                tiempo=tiempo,
                entrega=tiempo + retardo,
                datos=datos or {},
            )
            for d in destinos
        ]
        for mensaje in entregados:
            self._bandejas.setdefault(mensaje.para, []).append(mensaje)
        transmision = entregados[0].model_copy(
            update={
                "para": ",".join(destinos),
                "para_indicativo": "todos"
                if len(destinos) > 1
                else self.indicativo(destinos[0]),
            }
        )
        self.historial.append(transmision)
        self._nuevos.append(transmision)
        return entregados

    def recibir(self, agente_id: str, tiempo: float) -> list[MensajeRadio]:
        """Saca de la bandeja los mensajes ya entregados en `tiempo`."""
        bandeja = self._bandejas.get(agente_id, [])
        listos = [m for m in bandeja if m.entrega <= tiempo]
        self._bandejas[agente_id] = [m for m in bandeja if m.entrega > tiempo]
        return listos

    def pendientes(self, agente_id: str) -> int:
        return len(self._bandejas.get(agente_id, []))

    def tomar_nuevos(self) -> list[MensajeRadio]:
        """Transmisiones desde la última llamada (para registrarlas o mostrarlas)."""
        nuevos, self._nuevos = self._nuevos, []
        return nuevos
