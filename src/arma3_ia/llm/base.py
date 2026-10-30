"""Interfaz común para cualquier modelo de lenguaje."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

Rol = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class MensajeLLM:
    rol: Rol
    contenido: str


@dataclass
class RespuestaLLM:
    texto: str
    modelo: str = ""
    tokens_entrada: int = 0
    tokens_salida: int = 0
    duracion_s: float = 0.0


class ErrorLLM(Exception):
    """Fallo al hablar con el modelo."""


class ErrorConexionLLM(ErrorLLM):
    """No se pudo contactar con el servidor del modelo."""


class ErrorRespuestaLLM(ErrorLLM):
    """El servidor respondió con un error o con algo que no se puede usar."""


class ClienteLLM(ABC):
    """Cliente de chat. `esquema` es un JSON Schema que la respuesta debe cumplir.

    `metadatos` es información opcional sobre quién pregunta (rol, agente y una función
    `fallback` con la decisión por reglas). Los clientes reales la ignoran; sirve para el
    registro y para el LLM falso que se usa en las pruebas.
    """

    nombre_modelo: str = "?"

    def __init__(self) -> None:
        self.estadisticas: dict[str, float] = {
            "llamadas": 0,
            "tokens_entrada": 0,
            "tokens_salida": 0,
            "segundos": 0.0,
        }

    @abstractmethod
    async def chat(
        self,
        mensajes: list[MensajeLLM],
        *,
        esquema: dict[str, Any] | None = None,
        temperatura: float | None = None,
        max_tokens: int | None = None,
        metadatos: dict[str, Any] | None = None,
    ) -> RespuestaLLM: ...

    def _contabilizar(self, respuesta: RespuestaLLM) -> None:
        self.estadisticas["llamadas"] += 1
        self.estadisticas["tokens_entrada"] += respuesta.tokens_entrada
        self.estadisticas["tokens_salida"] += respuesta.tokens_salida
        self.estadisticas["segundos"] += respuesta.duracion_s

    async def cerrar(self) -> None:
        return None

    async def __aenter__(self) -> ClienteLLM:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.cerrar()
