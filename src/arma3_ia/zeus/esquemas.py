"""Lo que Zeus escribe: campañas, misiones e intervenciones en directo (JSON validado).

Igual que los NPC, Zeus responde con un esquema fijo que se envía a Ollama. Los validadores
toleran lo que suele escribir un modelo pequeño (mayúsculas, sinónimos, números como texto).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..agentes.esquemas import TareaMando, normalizar_enum
from ..modelo import Persona, TipoObjetivo, TipoUnidad
from ..texto import normalizar, recortar

BANDOS = ("blufor", "opfor", "independent")
SINONIMOS_BANDO = {
    "azul": "blufor", "west": "blufor", "otan": "blufor", "aliados": "blufor", "jugadores": "blufor",
    "rojo": "opfor", "east": "opfor", "enemigo": "opfor", "enemigos": "opfor",
    "verde": "independent", "guer": "independent", "resistance": "independent", "independiente": "independent",
}
SINONIMOS_UNIDAD = {
    "infanteria": "infanteria", "escuadra": "infanteria", "peloton": "infanteria", "soldados": "infanteria",
    "motorizada": "motorizada", "vehiculo": "motorizada", "camion": "motorizada", "tecnica": "motorizada",
    "jeep": "motorizada", "convoy": "motorizada",
    "blindado": "blindado", "tanque": "blindado", "carro": "blindado", "apc": "blindado",
    "helicoptero": "helicoptero", "heli": "helicoptero",
}


def _bando(valor: Any) -> Any:
    if not isinstance(valor, str):
        return valor
    norm = normalizar(valor)
    return norm if norm in BANDOS else SINONIMOS_BANDO.get(norm, norm)


def _tipo_unidad(valor: Any) -> Any:
    if not isinstance(valor, str):
        return valor
    norm = normalizar(valor)
    for clave, tipo in SINONIMOS_UNIDAD.items():
        if norm.startswith(clave):
            return tipo
    return norm


class _BaseZeus(BaseModel):
    @field_validator("nombre", "titulo", "sinopsis", "premisa", "briefing", "descripcion", "texto", "razon",
                     mode="before", check_fields=False)
    @classmethod
    def _recortar(cls, valor: Any) -> Any:
        return recortar(valor, 600) if isinstance(valor, str) else valor

    @field_validator("bando", "bando_jugadores", mode="before", check_fields=False)
    @classmethod
    def _normalizar_bando(cls, valor: Any) -> Any:
        return _bando(valor)


# ---------------------------------------------------------------------------------- misión
class LugarPlan(_BaseZeus):
    nombre: str
    tipo: str = Field(default="campo", description="urbano, bosque, colina, campo o lz")
    posicion: str = Field(description="Cuadrícula de 6 cifras")
    radio_m: float = 250.0

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        norm = normalizar(str(valor or "campo"))
        for clave, tipo in (("pueblo", "urbano"), ("aldea", "urbano"), ("ciudad", "urbano"), ("urban", "urbano"),
                            ("bosque", "bosque"), ("selva", "bosque"), ("colina", "colina"), ("monte", "colina"),
                            ("cerro", "colina"), ("lz", "lz"), ("helipuerto", "lz"), ("aterrizaje", "lz")):
            if norm.startswith(clave):
                return tipo
        return "campo"


class UnidadPlan(_BaseZeus):
    indicativo: str
    tipo: TipoUnidad = TipoUnidad.INFANTERIA
    efectivos: int = 6
    posicion: str = Field(description="Lugar o cuadrícula donde empieza")
    tarea: TareaMando = TareaMando.DEFENDER
    destino: str | None = None
    ruta: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 4})
    armada: bool = False
    jugadores: bool = Field(default=False, description="true si es el grupo de los jugadores humanos")
    lider: Persona | None = None

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        return _tipo_unidad(valor)

    @field_validator("tarea", mode="before")
    @classmethod
    def _tarea(cls, valor: Any) -> Any:
        return normalizar_enum(valor, TareaMando)

    @field_validator("efectivos", mode="before")
    @classmethod
    def _efectivos(cls, valor: Any) -> Any:
        try:
            return max(1, min(12, int(float(valor))))
        except (TypeError, ValueError):
            return 6

    @field_validator("ruta", mode="before")
    @classmethod
    def _ruta(cls, valor: Any) -> Any:
        if not isinstance(valor, list):
            return []
        return [", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v) for v in valor][:4]


class ObjetivoPlan(_BaseZeus):
    nombre: str
    tipo: TipoObjetivo
    lugar: str = Field(description="Lugar o cuadrícula del objetivo")
    descripcion: str = ""
    unidades: list[str] = Field(default_factory=list, description="Indicativos implicados (extraer/destruir)")
    obligatorio: bool = True

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        sinonimos = {"tomar": "capturar", "asaltar": "capturar", "conquistar": "capturar", "eliminar": "destruir",
                     "aguantar": "defender", "rescatar": "extraer", "evacuar": "extraer", "explorar": "reconocer"}
        if isinstance(valor, str):
            norm = normalizar(valor)
            return sinonimos.get(norm, norm)
        return valor


class BandoPlan(_BaseZeus):
    bando: str
    nombre: str = ""
    mando: bool = Field(default=True, description="false = cada grupo actúa por su cuenta, sin comandante")
    comandante: Persona | None = None
    base: str | None = None
    unidades: list[UnidadPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 10})
    objetivos: list[ObjetivoPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 3})


class PlanMision(_BaseZeus):
    nombre: str
    briefing: str = Field(description="Lo que se cuenta a los jugadores al empezar")
    hora: str = "dia"
    clima: str = "despejado"
    duracion_min: int = 45
    tamano_km: int = 4
    lugares: list[LugarPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 8})
    bandos: list[BandoPlan] = Field(json_schema_extra={"minItems": 2, "maxItems": 3})
    bando_jugadores: str = "blufor"

    @field_validator("hora", mode="before")
    @classmethod
    def _hora(cls, valor: Any) -> Any:
        norm = normalizar(str(valor or "dia"))
        for clave, hora in (("noche", "noche"), ("nocturn", "noche"), ("amanec", "amanecer"), ("alba", "amanecer"),
                            ("atardec", "atardecer"), ("anochec", "atardecer"), ("crepus", "atardecer")):
            if clave in norm:
                return hora
        return "dia"

    @field_validator("clima", mode="before")
    @classmethod
    def _clima(cls, valor: Any) -> Any:
        norm = normalizar(str(valor or "despejado"))
        for clave, clima in (("lluv", "lluvia"), ("torment", "lluvia"), ("niebla", "niebla"), ("bruma", "niebla"),
                             ("nub", "nubes"), ("cubierto", "nubes")):
            if clave in norm:
                return clima
        return "despejado"

    @field_validator("duracion_min", "tamano_km", mode="before")
    @classmethod
    def _entero(cls, valor: Any) -> Any:
        try:
            return int(float(valor))
        except (TypeError, ValueError):
            return None

    @field_validator("duracion_min")
    @classmethod
    def _duracion(cls, valor: int | None) -> int:
        return max(10, min(180, valor or 45))

    @field_validator("tamano_km")
    @classmethod
    def _tamano(cls, valor: int | None) -> int:
        return max(2, min(10, valor or 4))


# ---------------------------------------------------------------------------------- campaña
class TipoMision(str, Enum):
    ASALTO = "asalto"
    DEFENSA = "defensa"
    EMBOSCADA = "emboscada"
    RESCATE = "rescate"
    RECONOCIMIENTO = "reconocimiento"
    DESTRUCCION = "destruccion"


class CapituloPlan(_BaseZeus):
    titulo: str
    sinopsis: str
    tipo: TipoMision = TipoMision.ASALTO

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        if not isinstance(valor, str):
            return valor
        norm = normalizar(valor)
        for clave, tipo in (("asalt", "asalto"), ("ataq", "asalto"), ("tom", "asalto"), ("captur", "asalto"),
                            ("defen", "defensa"), ("resist", "defensa"), ("embosc", "emboscada"),
                            ("convoy", "emboscada"), ("rescat", "rescate"), ("extrac", "rescate"),
                            ("evacu", "rescate"), ("recon", "reconocimiento"), ("explor", "reconocimiento"),
                            ("patrull", "reconocimiento"), ("destru", "destruccion"), ("sabot", "destruccion"),
                            ("volar", "destruccion")):
            if norm.startswith(clave):
                return tipo
        return norm


class PlanCampana(_BaseZeus):
    nombre: str
    premisa: str = Field(description="De qué va la campaña, en 2-4 frases")
    perfil: str = Field(description="Perfil de época: moderno, ww2, insurgencia, contrainsurgencia o guerra_fria")
    nombre_jugadores: str = Field(description="Facción de los jugadores")
    nombre_enemigo: str
    capitulos: list[CapituloPlan] = Field(json_schema_extra={"minItems": 3, "maxItems": 6})
    personajes: list[Persona] = Field(default_factory=list, description="NPC aliados que aparecen en toda la campaña",
                                      json_schema_extra={"maxItems": 6})
    antagonista: Persona | None = None

    @field_validator("perfil", mode="before")
    @classmethod
    def _perfil(cls, valor: Any) -> Any:
        norm = normalizar(str(valor or "moderno")).replace(" ", "_")
        for clave, perfil in (("ww", "ww2"), ("segunda", "ww2"), ("194", "ww2"), ("contra", "contrainsurgencia"),
                              ("insurg", "insurgencia"), ("guerrill", "insurgencia"), ("fria", "guerra_fria"),
                              ("guerra_fria", "guerra_fria"), ("moder", "moderno")):
            if clave in norm:
                return perfil
        return norm


# ---------------------------------------------------------------------------------- en directo
class TipoIntervencion(str, Enum):
    REFUERZOS = "refuerzos"
    CLIMA = "clima"
    HORA = "hora"
    MENSAJE = "mensaje"
    ORDEN = "orden"
    NADA = "nada"


class AccionZeus(_BaseZeus):
    tipo: TipoIntervencion
    bando: str | None = None
    texto: str = Field(default="", description="Mensaje a los jugadores o motivo")
    unidad: UnidadPlan | None = Field(default=None, description="Para refuerzos: el grupo que aparece")
    destinatario: str | None = Field(default=None, description="Para orden: indicativo del grupo NPC")
    tarea: TareaMando | None = None
    destino: str | None = None
    clima: str | None = None
    hora: str | None = None

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        if not isinstance(valor, str):
            return valor
        norm = normalizar(valor)
        for clave, tipo in (("refuer", "refuerzos"), ("spawn", "refuerzos"), ("crear", "refuerzos"),
                            ("clima", "clima"), ("tiempo", "clima"), ("hora", "hora"), ("mensaje", "mensaje"),
                            ("aviso", "mensaje"), ("orden", "orden"), ("mover", "orden")):
            if norm.startswith(clave):
                return tipo
        return norm if norm in {t.value for t in TipoIntervencion} else "nada"

    @field_validator("tarea", mode="before")
    @classmethod
    def _tarea(cls, valor: Any) -> Any:
        return normalizar_enum(valor, TareaMando) if valor else None


class IntervencionZeus(_BaseZeus):
    razon: str = ""
    acciones: list[AccionZeus] = Field(default_factory=list, json_schema_extra={"maxItems": 3})
