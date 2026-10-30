"""LLM simulado: permite probar todo el sistema sin GPU ni Ollama.

Modos:

- ``respuestas``: devuelve esas respuestas en orden (texto o dict, que se convierte a JSON).
- ``responder``: una función que genera la respuesta a partir de los mensajes.
- Por defecto ("táctico"): responde lo mismo que decidirían las reglas de doctrina del agente
  que pregunta (``metadatos["fallback"]``), de modo que una misión completa funciona sin modelo.

Todas las llamadas quedan registradas en ``llamadas`` para inspeccionarlas en los tests.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .base import ClienteLLM, ErrorConexionLLM, MensajeLLM, RespuestaLLM

Responder = Callable[
    [list[MensajeLLM], "dict[str, Any] | None", "dict[str, Any]"], "str | Awaitable[str]"
]


@dataclass
class LlamadaRegistrada:
    mensajes: list[MensajeLLM]
    esquema: dict[str, Any] | None
    metadatos: dict[str, Any]

    @property
    def sistema(self) -> str:
        return next((m.contenido for m in self.mensajes if m.rol == "system"), "")

    @property
    def usuario(self) -> str:
        return next((m.contenido for m in reversed(self.mensajes) if m.rol == "user"), "")


def responder_tactico(
    mensajes: list[MensajeLLM], esquema: dict[str, Any] | None, metadatos: dict[str, Any]
) -> str:
    fallback = metadatos.get("fallback")
    if fallback is None:
        return json.dumps({"respuesta": "Recibido."}, ensure_ascii=False)
    decision = fallback()
    if isinstance(decision, BaseModel):
        return decision.model_dump_json()
    return json.dumps(decision, ensure_ascii=False)


class LLMFalso(ClienteLLM):
    nombre_modelo = "falso"

    def __init__(
        self,
        respuestas: Iterable[str | dict[str, Any]] | None = None,
        responder: Responder | None = None,
        *,
        latencia_s: float = 0.0,
        fallos: int = 0,
    ) -> None:
        super().__init__()
        self._respuestas: deque[str | dict[str, Any]] = deque(respuestas or [])
        self._responder = responder
        self.latencia_s = latencia_s
        self.fallos_pendientes = fallos
        self.llamadas: list[LlamadaRegistrada] = []

    async def chat(
        self,
        mensajes: list[MensajeLLM],
        *,
        esquema: dict[str, Any] | None = None,
        temperatura: float | None = None,
        max_tokens: int | None = None,
        metadatos: dict[str, Any] | None = None,
    ) -> RespuestaLLM:
        metadatos = metadatos or {}
        self.llamadas.append(LlamadaRegistrada(list(mensajes), esquema, metadatos))
        if self.latencia_s:
            await asyncio.sleep(self.latencia_s)
        if self.fallos_pendientes > 0:
            self.fallos_pendientes -= 1
            raise ErrorConexionLLM("fallo simulado del LLM falso")
        if self._respuestas:
            r = self._respuestas.popleft()
            texto = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        elif self._responder is not None:
            resultado = self._responder(mensajes, esquema, metadatos)
            texto = await resultado if inspect.isawaitable(resultado) else resultado
        else:
            texto = responder_tactico(mensajes, esquema, metadatos)
        respuesta = RespuestaLLM(
            texto=texto,
            modelo="falso",
            tokens_entrada=sum(len(m.contenido) for m in mensajes) // 4,
            tokens_salida=len(texto) // 4,
        )
        self._contabilizar(respuesta)
        return respuesta

    def llamadas_de(self, rol: str) -> list[LlamadaRegistrada]:
        return [ll for ll in self.llamadas if ll.metadatos.get("rol") == rol]
