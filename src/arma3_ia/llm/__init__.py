"""Clientes de modelos de lenguaje."""

from __future__ import annotations

from ..config import ConfigLLM
from .base import ClienteLLM, ErrorConexionLLM, ErrorLLM, ErrorRespuestaLLM, MensajeLLM, RespuestaLLM
from .estructurado import esquema_para_llm, generar_estructurado
from .falso import LLMFalso
from .json_util import extraer_json, quitar_pensamiento
from .ollama import ClienteOllama, InformeOllama

__all__ = [
    "ClienteLLM",
    "ClienteOllama",
    "ErrorConexionLLM",
    "ErrorLLM",
    "ErrorRespuestaLLM",
    "InformeOllama",
    "LLMFalso",
    "MensajeLLM",
    "RespuestaLLM",
    "crear_cliente",
    "esquema_para_llm",
    "extraer_json",
    "generar_estructurado",
    "quitar_pensamiento",
]


def crear_cliente(config: ConfigLLM) -> ClienteLLM:
    """Crea el cliente indicado en la configuración."""
    if config.proveedor == "falso":
        return LLMFalso()
    return ClienteOllama(
        config.url,
        config.modelo,
        temperatura=config.temperatura,
        num_ctx=config.num_ctx,
        max_tokens=config.max_tokens,
        timeout_s=config.timeout_s,
        pensar=config.pensar,
        keep_alive=config.keep_alive,
        max_concurrencia=config.max_concurrencia,
    )
