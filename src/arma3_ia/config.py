"""Configuración: un fichero YAML opcional más variables de entorno.

Orden de búsqueda del fichero: ruta indicada -> ``$ARMA3IA_CONFIG`` -> ``config/config.yaml``
-> ``config.yaml``. Si no existe ninguno se usan los valores por defecto.

Variables de entorno que sobrescriben el fichero:
``ARMA3IA_LLM_URL``, ``ARMA3IA_LLM_MODELO``, ``ARMA3IA_LLM_PROVEEDOR`` y ``ARMA3IA_DB``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class ConfigLLM(BaseModel):
    proveedor: Literal["ollama", "falso"] = "ollama"
    url: str = "http://localhost:11434"
    modelo: str = "qwen3:8b"
    temperatura: float = 0.4
    num_ctx: int = 8192
    max_tokens: int = 700
    timeout_s: float = 120.0
    # True/False envía el parámetro "think" de Ollama (modelos con razonamiento, p. ej. qwen3).
    # None no lo envía. Desactivarlo hace las decisiones mucho más rápidas.
    pensar: bool | None = False
    keep_alive: str | None = "30m"
    max_concurrencia: int = 2
    reintentos: int = 1


class ConfigAgentes(BaseModel):
    intervalo_comandante_s: float = 120.0
    intervalo_lider_s: float = 90.0
    intervalo_piloto_s: float = 60.0
    intervalo_minimo_s: float = 25.0
    timeout_decision_s: float = 90.0
    lecciones_por_decision: int = 4
    usar_llm_comandante: bool = True
    usar_llm_lideres: bool = True
    usar_llm_pilotos: bool = True
    usar_llm_enlace: bool = True
    # Si es True, los jugadores pueden dar órdenes directas a las unidades de la IA por chat.
    jugadores_al_mando: bool = False
    # Muestra a los jugadores el tráfico de radio entre agentes (inmersión).
    retransmitir_radio: bool = True
    max_mensajes_chat_por_paso: int = 3


class ConfigMemoria(BaseModel):
    ruta_db: str = "datos/memoria.db"
    reflexion: bool = True
    # Puntuación (0..1) a partir de la cual una misión cuenta como éxito para las lecciones usadas.
    umbral_exito: float = 0.6


class ConfigHabilidades(BaseModel):
    # Explorar = probar variantes de las maniobras durante las misiones para seguir aprendiendo.
    explorar: bool = False


class Config(BaseModel):
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    agentes: ConfigAgentes = Field(default_factory=ConfigAgentes)
    memoria: ConfigMemoria = Field(default_factory=ConfigMemoria)
    habilidades: ConfigHabilidades = Field(default_factory=ConfigHabilidades)
    perfil: str = "moderno"
    nivel_registro: str = "INFO"


RUTAS_POR_DEFECTO = ("config/config.yaml", "config.yaml")


def _ruta_config(ruta: str | os.PathLike[str] | None) -> Path | None:
    if ruta:
        return Path(ruta)
    entorno = os.environ.get("ARMA3IA_CONFIG")
    if entorno:
        return Path(entorno)
    for candidata in RUTAS_POR_DEFECTO:
        if Path(candidata).is_file():
            return Path(candidata)
    return None


def cargar_config(ruta: str | os.PathLike[str] | None = None) -> Config:
    datos: dict[str, Any] = {}
    fichero = _ruta_config(ruta)
    if fichero is not None:
        if not fichero.is_file():
            raise FileNotFoundError(f"No existe el fichero de configuración: {fichero}")
        datos = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
    config = Config.model_validate(datos)
    entorno = os.environ
    if "ARMA3IA_LLM_URL" in entorno:
        config.llm.url = entorno["ARMA3IA_LLM_URL"]
    if "ARMA3IA_LLM_MODELO" in entorno:
        config.llm.modelo = entorno["ARMA3IA_LLM_MODELO"]
    if "ARMA3IA_LLM_PROVEEDOR" in entorno:
        config.llm.proveedor = ConfigLLM.model_validate(
            {"proveedor": entorno["ARMA3IA_LLM_PROVEEDOR"]}
        ).proveedor
    if "ARMA3IA_DB" in entorno:
        config.memoria.ruta_db = entorno["ARMA3IA_DB"]
    return config
