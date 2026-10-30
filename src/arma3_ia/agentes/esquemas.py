"""Formato JSON de las decisiones de cada agente.

Los esquemas se envían a Ollama para forzar JSON válido. Los validadores son tolerantes con
lo que escriben los modelos pequeños ("Atacar", "apoyo aéreo", "RTB"...) y recortan textos
demasiado largos en lugar de rechazar la respuesta.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator

from ..modelo import Altitud, Postura
from ..texto import normalizar, recortar

E = TypeVar("E", bound=Enum)

SINONIMOS: dict[str, str] = {
    "avanzar": "mover",
    "moverse": "mover",
    "desplazarse": "mover",
    "asaltar": "atacar",
    "asalto": "atacar",
    "ataque": "atacar",
    "atacar objetivo": "atacar",
    "enfrentar": "atacar",
    "defensa": "defender",
    "mantener": "defender",
    "mantener posicion": "defender",
    "cubrirse": "defender",
    "reconocimiento": "reconocer",
    "explorar": "reconocer",
    "patrullar": "reconocer",
    "retirada": "retirarse",
    "replegarse": "retirarse",
    "repliegue": "retirarse",
    "retirar": "retirarse",
    "aguardar": "esperar",
    "espera": "esperar",
    "embarque": "embarcar",
    "subir": "embarcar",
    "seguir": "continuar",
    "continuar mision": "continuar",
    "mantener rumbo": "continuar",
    "despegar": "volar",
    "vuelo": "volar",
    "aterrizaje": "aterrizar",
    "desembarco": "desembarcar",
    "descargar": "desembarcar",
    "apoyo aereo": "apoyo_aereo",
    "apoyo aereo cercano": "apoyo_aereo",
    "cas": "apoyo_aereo",
    "regresar": "regresar_base",
    "regresar a base": "regresar_base",
    "volver a base": "regresar_base",
    "rtb": "regresar_base",
    "transportar": "transporte",
    "insercion": "transporte",
    "extraccion": "transporte",
    "evacuacion medica": "evacuacion",
    "medevac": "evacuacion",
}


def normalizar_enum(valor: Any, enum: type[E]) -> Any:
    """Convierte variantes escritas por el modelo en un valor válido del enum (si puede)."""
    if not isinstance(valor, str):
        return valor
    norm = normalizar(valor)
    candidatos = (norm.replace(" ", "_"), SINONIMOS.get(norm, ""), norm.split(" ")[0] if norm else "")
    validos = {e.value for e in enum}
    for candidato in candidatos:
        if candidato in validos:
            return candidato
    return valor


class _Base(BaseModel):
    """Recorta los textos libres para que un modelo locuaz no rompa la validación."""

    @field_validator("evaluacion", "intencion", "detalles", "informe", "texto", "respuesta",
                     "mensaje_general", mode="before", check_fields=False)
    @classmethod
    def _recortar_textos(cls, valor: Any) -> Any:
        if isinstance(valor, str):
            return recortar(valor, 400)
        return valor

    @field_validator("destino", "objetivo", mode="before", check_fields=False)
    @classmethod
    def _texto_opcional(cls, valor: Any) -> Any:
        if valor is None:
            return None
        if isinstance(valor, (list, tuple)):
            return ", ".join(str(v) for v in valor)
        texto = str(valor).strip()
        return texto or None


# ---------------------------------------------------------------------------------- comandante
class TareaMando(str, Enum):
    MOVER = "mover"
    ATACAR = "atacar"
    DEFENDER = "defender"
    RECONOCER = "reconocer"
    RETIRARSE = "retirarse"
    ESPERAR = "esperar"
    EMBARCAR = "embarcar"
    TRANSPORTE = "transporte"
    APOYO_AEREO = "apoyo_aereo"
    REGRESAR_BASE = "regresar_base"


TAREAS_AEREAS = frozenset(
    {TareaMando.TRANSPORTE, TareaMando.APOYO_AEREO, TareaMando.REGRESAR_BASE}
)


class OrdenMando(_Base):
    unidad: str = Field(description="Indicativo exacto de la unidad que recibe la orden")
    tarea: TareaMando
    destino: str | None = Field(default=None, description="Cuadrícula de 6 cifras o nombre de un lugar")
    objetivo: str | None = Field(default=None, description="Contacto, unidad o helicóptero implicado")
    detalles: str = ""

    @field_validator("tarea", mode="before")
    @classmethod
    def _tarea(cls, valor: Any) -> Any:
        return normalizar_enum(valor, TareaMando)


class DecisionComandante(_Base):
    evaluacion: str
    intencion: str
    ordenes: list[OrdenMando] = Field(default_factory=list, json_schema_extra={"maxItems": 8})
    mensaje_general: str | None = None

    @field_validator("ordenes")
    @classmethod
    def _maximo(cls, valor: list[OrdenMando]) -> list[OrdenMando]:
        return valor[:8]


# ---------------------------------------------------------------------------------- comunes
class TipoSolicitud(str, Enum):
    APOYO_AEREO = "apoyo_aereo"
    TRANSPORTE = "transporte"
    REFUERZOS = "refuerzos"
    EVACUACION = "evacuacion"
    MUNICION = "municion"


class Solicitud(_Base):
    tipo: TipoSolicitud
    destino: str | None = None
    detalles: str = ""

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        return normalizar_enum(valor, TipoSolicitud)


class MensajeSaliente(_Base):
    para: str = Field(description="Indicativo del destinatario, 'mando' o 'todos'")
    texto: str


def _limitar_mensajes(valor: list[MensajeSaliente]) -> list[MensajeSaliente]:
    return valor[:3]


# ---------------------------------------------------------------------------------- líder
class AccionTierra(str, Enum):
    MOVER = "mover"
    ATACAR = "atacar"
    DEFENDER = "defender"
    RECONOCER = "reconocer"
    RETIRARSE = "retirarse"
    ESPERAR = "esperar"
    EMBARCAR = "embarcar"
    CONTINUAR = "continuar"


class DecisionLider(_Base):
    evaluacion: str
    accion: AccionTierra
    destino: str | None = None
    objetivo: str | None = None
    postura: Postura | None = None
    informe: str | None = None
    solicitud: Solicitud | None = None
    mensajes: list[MensajeSaliente] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("accion", mode="before")
    @classmethod
    def _accion(cls, valor: Any) -> Any:
        return normalizar_enum(valor, AccionTierra)

    @field_validator("postura", mode="before")
    @classmethod
    def _postura(cls, valor: Any) -> Any:
        valor = normalizar_enum(valor, Postura)
        return valor if valor in {p.value for p in Postura} else None

    @field_validator("mensajes")
    @classmethod
    def _limitar(cls, valor: list[MensajeSaliente]) -> list[MensajeSaliente]:
        return _limitar_mensajes(valor)


# ---------------------------------------------------------------------------------- piloto
class AccionAire(str, Enum):
    VOLAR = "volar"
    ATERRIZAR = "aterrizar"
    DESEMBARCAR = "desembarcar"
    APOYO_AEREO = "apoyo_aereo"
    REGRESAR_BASE = "regresar_base"
    ESPERAR = "esperar"
    CONTINUAR = "continuar"


class DecisionPiloto(_Base):
    evaluacion: str
    accion: AccionAire
    destino: str | None = None
    objetivo: str | None = None
    altitud: Altitud | None = None
    informe: str | None = None
    mensajes: list[MensajeSaliente] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("accion", mode="before")
    @classmethod
    def _accion(cls, valor: Any) -> Any:
        return normalizar_enum(valor, AccionAire)

    @field_validator("altitud", mode="before")
    @classmethod
    def _altitud(cls, valor: Any) -> Any:
        valor = normalizar_enum(valor, Altitud)
        return valor if valor in {a.value for a in Altitud} else None

    @field_validator("mensajes")
    @classmethod
    def _limitar(cls, valor: list[MensajeSaliente]) -> list[MensajeSaliente]:
        return _limitar_mensajes(valor)


# ---------------------------------------------------------------------------------- enlace
class RespuestaEnlace(_Base):
    respuesta: str
    reenviar: list[MensajeSaliente] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("reenviar")
    @classmethod
    def _limitar(cls, valor: list[MensajeSaliente]) -> list[MensajeSaliente]:
        return _limitar_mensajes(valor)
