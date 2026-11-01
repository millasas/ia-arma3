"""Respuestas estructuradas: pedir JSON con esquema, validarlo y corregir si el modelo se equivoca."""

from __future__ import annotations

import copy
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .base import ClienteLLM, ErrorRespuestaLLM, MensajeLLM, RespuestaLLM
from .json_util import extraer_json

T = TypeVar("T", bound=BaseModel)

_CLAVES_SOBRANTES = {"title", "default", "examples"}


def _resolver(
    nodo: Any, definiciones: dict[str, Any], profundidad: int = 0, propiedades: bool = False
) -> Any:
    if profundidad > 20:
        return {}
    if isinstance(nodo, dict):
        if "$ref" in nodo and not propiedades:
            nombre = nodo["$ref"].split("/")[-1]
            definicion = copy.deepcopy(definiciones.get(nombre, {}))
            resuelto = _resolver(definicion, definiciones, profundidad + 1)
            extra = {k: v for k, v in nodo.items() if k != "$ref"}
            return {**resuelto, **_resolver(extra, definiciones, profundidad + 1)}
        # Dentro de "properties" las claves son nombres de campos: no se filtran.
        return {
            k: _resolver(v, definiciones, profundidad + 1, propiedades=(k == "properties"))
            for k, v in nodo.items()
            if propiedades or (k not in _CLAVES_SOBRANTES and k != "$defs")
        }
    if isinstance(nodo, list):
        return [_resolver(v, definiciones, profundidad + 1) for v in nodo]
    return nodo


def esquema_para_llm(modelo: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema autocontenido (sin $ref) apto para las "structured outputs" de Ollama."""
    esquema = modelo.model_json_schema()
    return _resolver(esquema, esquema.get("$defs", {}))


def _resumen_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        partes = []
        for e in error.errors()[:3]:
            ruta = ".".join(str(p) for p in e["loc"]) or "raíz"
            partes.append(f"{ruta}: {e['msg']}")
        return "; ".join(partes)
    return str(error)[:200]


async def generar_estructurado(
    llm: ClienteLLM,
    mensajes: list[MensajeLLM],
    modelo: type[T],
    *,
    reintentos: int = 1,
    temperatura: float | None = None,
    max_tokens: int | None = None,
    metadatos: dict[str, Any] | None = None,
) -> tuple[T, RespuestaLLM]:
    """Pide al modelo un objeto `modelo` en JSON y lo valida.

    Si la respuesta no es válida se le muestra el error y se le pide que la corrija
    (hasta `reintentos` veces). `max_tokens` limita la longitud de la respuesta (las de Zeus
    son largas). Lanza ErrorRespuestaLLM si no lo consigue.
    """
    esquema = esquema_para_llm(modelo)
    historial = list(mensajes)
    ultimo_error: Exception | None = None
    for _ in range(reintentos + 1):
        respuesta = await llm.chat(
            historial, esquema=esquema, temperatura=temperatura, max_tokens=max_tokens, metadatos=metadatos
        )
        try:
            return modelo.model_validate(extraer_json(respuesta.texto)), respuesta
        except (ValueError, ValidationError) as error:
            ultimo_error = error
            historial = [
                *historial,
                MensajeLLM("assistant", respuesta.texto[:2000]),
                MensajeLLM(
                    "user",
                    f"Respuesta no válida ({_resumen_error(error)}). Responde SOLO con un objeto "
                    "JSON que cumpla exactamente el formato pedido, sin texto adicional.",
                ),
            ]
    raise ErrorRespuestaLLM(f"el modelo no devolvió un JSON válido: {_resumen_error(ultimo_error or ValueError())}")
