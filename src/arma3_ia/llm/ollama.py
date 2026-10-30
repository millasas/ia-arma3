"""Cliente para Ollama (API nativa ``/api/chat``).

Usa las "structured outputs" de Ollama (campo ``format`` con un JSON Schema) para que
modelos de 7-8B devuelvan siempre JSON válido, y limita las peticiones simultáneas para
no saturar la GPU.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .base import ClienteLLM, ErrorConexionLLM, ErrorLLM, ErrorRespuestaLLM, MensajeLLM, RespuestaLLM
from .json_util import quitar_pensamiento

log = logging.getLogger(__name__)

_ERRORES_RED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.PoolTimeout)


@dataclass
class InformeOllama:
    conectado: bool
    version: str | None = None
    modelos: list[str] = field(default_factory=list)
    modelo_disponible: bool = False
    error: str | None = None


class ClienteOllama(ClienteLLM):
    def __init__(
        self,
        url: str = "http://localhost:11434",
        modelo: str = "qwen3:4b",
        *,
        temperatura: float = 0.4,
        num_ctx: int = 8192,
        max_tokens: int = 700,
        timeout_s: float = 120.0,
        pensar: bool | None = False,
        keep_alive: str | None = "30m",
        max_concurrencia: int = 2,
        reintentos_conexion: int = 2,
        espera_reintento_s: float = 1.0,
        transporte: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__()
        self.url = url.rstrip("/")
        self.nombre_modelo = modelo
        self.temperatura = temperatura
        self.num_ctx = num_ctx
        self.max_tokens = max_tokens
        self.pensar = pensar
        self.keep_alive = keep_alive
        self.reintentos_conexion = reintentos_conexion
        self.espera_reintento_s = espera_reintento_s
        self._enviar_think = pensar is not None
        self._semaforo = asyncio.Semaphore(max(1, max_concurrencia))
        self._http = httpx.AsyncClient(
            base_url=self.url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            transport=transporte,
        )

    def _payload(
        self,
        mensajes: list[MensajeLLM],
        esquema: dict[str, Any] | None,
        temperatura: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.nombre_modelo,
            "messages": [{"role": m.rol, "content": m.contenido} for m in mensajes],
            "stream": False,
            "options": {
                "temperature": self.temperatura if temperatura is None else temperatura,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if esquema is not None:
            payload["format"] = esquema
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        if self._enviar_think:
            payload["think"] = self.pensar
        return payload

    async def chat(
        self,
        mensajes: list[MensajeLLM],
        *,
        esquema: dict[str, Any] | None = None,
        temperatura: float | None = None,
        max_tokens: int | None = None,
        metadatos: dict[str, Any] | None = None,
    ) -> RespuestaLLM:
        payload = self._payload(mensajes, esquema, temperatura, max_tokens)
        inicio = time.monotonic()
        async with self._semaforo:
            datos = await self._post_chat(payload)
        mensaje = datos.get("message") or {}
        respuesta = RespuestaLLM(
            texto=quitar_pensamiento(str(mensaje.get("content", ""))),
            modelo=str(datos.get("model", self.nombre_modelo)),
            tokens_entrada=int(datos.get("prompt_eval_count") or 0),
            tokens_salida=int(datos.get("eval_count") or 0),
            duracion_s=time.monotonic() - inicio,
        )
        self._contabilizar(respuesta)
        return respuesta

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        intento = 0
        while True:
            try:
                r = await self._http.post("/api/chat", json=payload)
            except httpx.ReadTimeout as e:
                raise ErrorConexionLLM(
                    f"Ollama tardó demasiado en responder ({self.nombre_modelo}). "
                    "Prueba un modelo más pequeño o sube timeout_s."
                ) from e
            except _ERRORES_RED as e:
                if intento < self.reintentos_conexion:
                    intento += 1
                    await asyncio.sleep(self.espera_reintento_s * intento)
                    continue
                raise ErrorConexionLLM(
                    f"No se pudo conectar con Ollama en {self.url} ({type(e).__name__}). "
                    "¿Está en marcha? Comprueba: sudo systemctl status ollama"
                ) from e

            if r.status_code == 400 and "think" in payload and "think" in r.text.lower():
                # El modelo no admite el parámetro "think": se repite sin él y se recuerda.
                log.info("El modelo %s no admite 'think'; se desactiva.", self.nombre_modelo)
                self._enviar_think = False
                payload = {k: v for k, v in payload.items() if k != "think"}
                continue
            if r.status_code == 404:
                raise ErrorLLM(
                    f"El modelo '{self.nombre_modelo}' no está descargado en Ollama. "
                    f"Ejecuta: ollama pull {self.nombre_modelo}"
                )
            if r.status_code >= 500 and intento < self.reintentos_conexion:
                intento += 1
                await asyncio.sleep(self.espera_reintento_s * intento)
                continue
            if r.status_code >= 400:
                raise ErrorRespuestaLLM(f"Ollama respondió {r.status_code}: {r.text[:300]}")
            try:
                datos = r.json()
            except ValueError as e:
                raise ErrorRespuestaLLM("Ollama devolvió una respuesta que no es JSON") from e
            if not isinstance(datos, dict):
                raise ErrorRespuestaLLM("Respuesta de Ollama con formato inesperado")
            if datos.get("error"):
                raise ErrorRespuestaLLM(f"Ollama: {datos['error']}")
            return datos

    async def modelos(self) -> list[str]:
        r = await self._http.get("/api/tags")
        r.raise_for_status()
        return [str(m.get("name", "")) for m in r.json().get("models", [])]

    async def en_gpu(self) -> float | None:
        """Qué parte del modelo cargado está en la VRAM (1.0 = entero). None si no está cargado."""
        r = await self._http.get("/api/ps")
        r.raise_for_status()
        for m in r.json().get("models", []):
            if m.get("name") == self.nombre_modelo or m.get("model") == self.nombre_modelo:
                total = float(m.get("size") or 0)
                return float(m.get("size_vram") or 0) / total if total else None
        return None

    async def comprobar(self) -> InformeOllama:
        """Comprueba la conexión, la versión y si el modelo configurado está descargado."""
        try:
            r = await self._http.get("/api/version")
            r.raise_for_status()
            version = r.json().get("version")
            modelos = await self.modelos()
        except (httpx.HTTPError, ValueError) as e:
            return InformeOllama(conectado=False, error=f"{type(e).__name__}: {e}")
        buscado = self.nombre_modelo if ":" in self.nombre_modelo else f"{self.nombre_modelo}:latest"
        return InformeOllama(
            conectado=True,
            version=version,
            modelos=modelos,
            modelo_disponible=buscado in modelos or self.nombre_modelo in modelos,
        )

    async def cerrar(self) -> None:
        await self._http.aclose()
