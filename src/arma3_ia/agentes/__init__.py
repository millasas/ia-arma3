"""Agentes de la IA: comandante, líderes de escuadra, pilotos y enlace con jugadores."""

from .base import Agente, ContextoAgentes, ResultadoAgente, Saliente
from .comandante import Comandante
from .enlace import Enlace
from .esquemas import (
    AccionAire,
    AccionTierra,
    DecisionComandante,
    DecisionLider,
    DecisionPiloto,
    MensajeSaliente,
    OrdenMando,
    RespuestaEnlace,
    Solicitud,
    TareaMando,
    TipoSolicitud,
)
from .lider import AgenteUnidad, LiderEscuadra
from .piloto import Piloto

__all__ = [
    "AccionAire",
    "AccionTierra",
    "Agente",
    "AgenteUnidad",
    "Comandante",
    "ContextoAgentes",
    "DecisionComandante",
    "DecisionLider",
    "DecisionPiloto",
    "Enlace",
    "LiderEscuadra",
    "MensajeSaliente",
    "OrdenMando",
    "Piloto",
    "RespuestaEnlace",
    "ResultadoAgente",
    "Saliente",
    "Solicitud",
    "TareaMando",
    "TipoSolicitud",
]
