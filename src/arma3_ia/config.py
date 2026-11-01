"""Configuración: un fichero YAML opcional más variables de entorno.

Orden de búsqueda del fichero: ruta indicada -> ``$ARMA3IA_CONFIG`` -> ``config/config.yaml``
-> ``config.yaml``. Si no existe ninguno se usan los valores por defecto.

Las rutas relativas (base de datos, carpeta de campañas) se cuentan desde la carpeta del
proyecto (``ia-arma3``), da igual desde dónde se lance la orden.

Variables de entorno que sobrescriben el fichero:
``ARMA3IA_LLM_URL``, ``ARMA3IA_LLM_MODELO``, ``ARMA3IA_LLM_PROVEEDOR``, ``ARMA3IA_DB``,
``ARMA3IA_CAMPANAS`` y ``ARMA3IA_RAIZ`` (carpeta del proyecto).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr


class ConfigLLM(BaseModel):
    proveedor: Literal["ollama", "falso"] = "ollama"
    url: str = "http://localhost:11434"
    modelo: str = "qwen3:4b"
    temperatura: float = 0.4
    num_ctx: int = 8192
    max_tokens: int = 700
    # Tiempo máximo de una respuesta de Ollama. Las de Zeus son largas; los NPC tienen su propio
    # límite (agentes.timeout_decision_s), así que esto no frena la partida.
    timeout_s: float = 300.0
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


class ConfigZeus(BaseModel):
    # Terrenos de ARMA 3 instalados en el PC de juego: Zeus solo elige entre estos.
    # Si tienes DLC o mods de mapas, añádelos (ver zeus/mapas.py para los nombres conocidos).
    terrenos: list[str] = Field(default_factory=lambda: ["Altis", "Stratis", "Malden", "Tanoa", "Livonia"])
    max_tokens: int = 3000           # las respuestas de Zeus (campaña, misión) son largas
    timeout_s: float = 400.0         # si el modelo tarda más, Zeus usa sus reglas
    intervenir_cada_s: float = 300.0  # cada cuánto se plantea intervenir durante la misión


class ConfigBBDD(BaseModel):
    ruta: str = "BBDD/ia-arma3.db"
    # Tamaño máximo para la base de datos más la carpeta de campañas, y disco libre mínimo.
    limite_mb: float = 2048.0
    minimo_libre_mb: float = 2048.0
    reflexion: bool = True           # informe post-acción y lecciones al final de cada misión
    # Puntuación (0..1) a partir de la cual una misión cuenta como éxito para las lecciones usadas.
    umbral_exito: float = 0.6


class ConfigCampanas(BaseModel):
    carpeta: str = "CAMPAÑAS"
    # Solo se usan si el encargo no dice cuántas misiones ni cuánto dura cada una y no se puede
    # preguntar (p. ej. al lanzar la orden desde un script).
    misiones_por_defecto: int = 4
    duracion_mision_min: int = 60


class ConfigHabilidades(BaseModel):
    # Explorar = probar variantes de las maniobras durante las misiones para seguir aprendiendo.
    explorar: bool = False


class Config(BaseModel):
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    agentes: ConfigAgentes = Field(default_factory=ConfigAgentes)
    zeus: ConfigZeus = Field(default_factory=ConfigZeus)
    bbdd: ConfigBBDD = Field(default_factory=ConfigBBDD)
    campanas: ConfigCampanas = Field(default_factory=ConfigCampanas)
    habilidades: ConfigHabilidades = Field(default_factory=ConfigHabilidades)
    nivel_registro: str = "INFO"
    _raiz: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def raiz(self) -> Path:
        return self._raiz

    def ruta(self, relativa: str) -> Path:
        """Una ruta de la configuración, contada desde la carpeta del proyecto si es relativa."""
        ruta = Path(relativa).expanduser()
        return ruta if ruta.is_absolute() or relativa == ":memory:" else self._raiz / ruta

    @property
    def ruta_bbdd(self) -> Path:
        return self.ruta(self.bbdd.ruta)

    @property
    def carpeta_campanas(self) -> Path:
        return self.ruta(self.campanas.carpeta)


RUTAS_POR_DEFECTO = ("config/config.yaml", "config.yaml")


def raiz_proyecto() -> Path:
    """La carpeta ia-arma3: la del repositorio si el paquete se instaló desde él, si no la actual."""
    entorno = os.environ.get("ARMA3IA_RAIZ")
    if entorno:
        return Path(entorno).expanduser()
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").is_file() and (repo / "src" / "arma3_ia").is_dir():
        return repo
    return Path.cwd()


def _ruta_config(ruta: str | os.PathLike[str] | None, raiz: Path) -> Path | None:
    if ruta:
        return Path(ruta)
    entorno = os.environ.get("ARMA3IA_CONFIG")
    if entorno:
        return Path(entorno)
    for base in (Path.cwd(), raiz):
        for candidata in RUTAS_POR_DEFECTO:
            if (base / candidata).is_file():
                return base / candidata
    return None


def cargar_config(ruta: str | os.PathLike[str] | None = None) -> Config:
    raiz = raiz_proyecto()
    datos: dict[str, Any] = {}
    fichero = _ruta_config(ruta, raiz)
    if fichero is not None:
        if not fichero.is_file():
            raise FileNotFoundError(f"No existe el fichero de configuración: {fichero}")
        datos = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
    config = Config.model_validate(datos)
    config._raiz = raiz
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
        config.bbdd.ruta = entorno["ARMA3IA_DB"]
    if "ARMA3IA_CAMPANAS" in entorno:
        config.campanas.carpeta = entorno["ARMA3IA_CAMPANAS"]
    return config
