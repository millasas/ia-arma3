"""Frontera entre la IA y el juego: la interfaz Mundo y el protocolo JSON del futuro puente."""

from .interfaz import Mundo
from .protocolo import (
    VERSION_PROTOCOLO,
    ErrorProtocolo,
    Sobre,
    TipoSobre,
    codificar_acciones,
    codificar_chat,
    codificar_error,
    codificar_fin,
    codificar_observacion,
    codificar_resultados,
    decodificar,
    leer_acciones,
    leer_chat,
    leer_observacion,
)

__all__ = [
    "VERSION_PROTOCOLO",
    "ErrorProtocolo",
    "Mundo",
    "Sobre",
    "TipoSobre",
    "codificar_acciones",
    "codificar_chat",
    "codificar_error",
    "codificar_fin",
    "codificar_observacion",
    "codificar_resultados",
    "decodificar",
    "leer_acciones",
    "leer_chat",
    "leer_observacion",
]
