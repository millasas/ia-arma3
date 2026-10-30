"""Protocolo de mensajes JSON entre ARMA 3 y la IA (versión 1).

El futuro puente (extensión de ARMA 3 + servidor de red) solo tiene que transportar estas
líneas JSON. Cada mensaje es un "sobre" con versión, tipo, bando y datos::

    {"v": 1, "tipo": "observacion", "bando": "blufor", "datos": {...Observacion...}}
    {"v": 1, "tipo": "acciones", "bando": "blufor", "datos": [{...Accion...}, ...]}
    {"v": 1, "tipo": "chat", "bando": "blufor", "datos": [{...MensajeChat...}, ...]}

Las posiciones admiten el formato nativo de ARMA (``[x, y, z]``). Ver docs/protocolo-puente.md.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from ..modelo import Accion, MensajeChat, Observacion, ResultadoAccion, ResultadoMision

VERSION_PROTOCOLO = 1


class TipoSobre(str, Enum):
    HOLA = "hola"
    OBSERVACION = "observacion"
    ACCIONES = "acciones"
    RESULTADOS = "resultados"
    CHAT = "chat"
    FIN = "fin"
    ERROR = "error"


class Sobre(BaseModel):
    v: int = VERSION_PROTOCOLO
    tipo: TipoSobre
    bando: str | None = None
    datos: Any = None


class ErrorProtocolo(ValueError):
    pass


def _codificar(tipo: TipoSobre, bando: str | None, datos: Any) -> str:
    return Sobre(tipo=tipo, bando=bando, datos=datos).model_dump_json()


def codificar_observacion(obs: Observacion) -> str:
    return _codificar(TipoSobre.OBSERVACION, obs.bando, obs.model_dump(mode="json"))


def codificar_acciones(bando: str, acciones: list[Accion]) -> str:
    return _codificar(TipoSobre.ACCIONES, bando, [a.model_dump(mode="json") for a in acciones])


def codificar_resultados(bando: str, resultados: list[ResultadoAccion]) -> str:
    return _codificar(TipoSobre.RESULTADOS, bando, [r.model_dump(mode="json") for r in resultados])


def codificar_chat(bando: str, mensajes: list[MensajeChat]) -> str:
    return _codificar(TipoSobre.CHAT, bando, [m.model_dump(mode="json") for m in mensajes])


def codificar_fin(bando: str, resultado: ResultadoMision) -> str:
    return _codificar(TipoSobre.FIN, bando, resultado.model_dump(mode="json"))


def codificar_error(mensaje: str) -> str:
    return _codificar(TipoSobre.ERROR, None, {"mensaje": mensaje})


def decodificar(linea: str | bytes) -> Sobre:
    """Lee una línea JSON del puente y valida el sobre (no el contenido)."""
    try:
        sobre = Sobre.model_validate(json.loads(linea))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ErrorProtocolo(f"mensaje no válido: {e}") from e
    if sobre.v != VERSION_PROTOCOLO:
        raise ErrorProtocolo(f"versión de protocolo {sobre.v} no soportada (se espera {VERSION_PROTOCOLO})")
    return sobre


def leer_observacion(sobre: Sobre) -> Observacion:
    _exigir(sobre, TipoSobre.OBSERVACION)
    return Observacion.model_validate(sobre.datos)


def leer_acciones(sobre: Sobre) -> list[Accion]:
    _exigir(sobre, TipoSobre.ACCIONES)
    return [Accion.model_validate(a) for a in sobre.datos or []]


def leer_chat(sobre: Sobre) -> list[MensajeChat]:
    _exigir(sobre, TipoSobre.CHAT)
    return [MensajeChat.model_validate(m) for m in sobre.datos or []]


def _exigir(sobre: Sobre, tipo: TipoSobre) -> None:
    if sobre.tipo != tipo:
        raise ErrorProtocolo(f"se esperaba un mensaje '{tipo.value}' y llegó '{sobre.tipo.value}'")
