"""Lo que Zeus escribe: ambientación, campañas, misiones, decorado e intervenciones (JSON validado).

Igual que los NPC, Zeus responde con un esquema fijo que se envía a Ollama. Los validadores
toleran lo que suele escribir un modelo pequeño (mayúsculas, sinónimos, números como texto).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..agentes.esquemas import TareaMando, normalizar_enum
from ..ambientacion import ERAS, InfoBando, PerfilCampana, normalizar_era
from ..modelo import Persona, TipoObjetivo, TipoObjeto, TipoUnidad
from ..texto import normalizar, recortar

BANDOS = ("blufor", "opfor", "independent")
SINONIMOS_BANDO = {
    "azul": "blufor", "west": "blufor", "otan": "blufor", "aliados": "blufor", "jugadores": "blufor",
    "propio": "blufor", "nuestro": "blufor",
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
SINONIMOS_OBJETO = (
    ("fortif", "fortificacion"), ("trinch", "fortificacion"), ("bunker", "fortificacion"), ("sacos", "fortificacion"),
    ("nido", "fortificacion"), ("posicion defensiva", "fortificacion"),
    ("checkpoint", "puesto_control"), ("puesto", "puesto_control"), ("control", "puesto_control"),
    ("torre", "torre_vigilancia"), ("vigia", "torre_vigilancia"), ("atalaya", "torre_vigilancia"),
    ("campamento", "campamento"), ("tienda", "campamento"), ("base", "campamento"),
    ("deposito", "deposito"), ("almacen", "deposito"), ("municion", "deposito"), ("suministro", "deposito"),
    ("combustible", "deposito"),
    ("mina", "minas"), ("ied", "ied"), ("explosivo", "ied"), ("trampa", "ied"), ("bomba", "ied"),
    ("barricada", "barricada"), ("bloqueo", "barricada"), ("carretera cortada", "barricada"),
    ("alambr", "alambrada"), ("concertina", "alambrada"),
    ("escombro", "escombros"), ("derrumb", "escombros"), ("ruina", "ruinas"), ("destruid", "ruinas"),
    ("restos", "restos_vehiculos"), ("chatarra", "restos_vehiculos"), ("crater", "crateres"),
    ("humo", "humo"), ("fuego", "humo"), ("incendio", "humo"),
)


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


def _lista_textos(valor: Any, maximo: int, largo: int = 200) -> Any:
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return []
    return [recortar(str(v), largo) for v in valor if str(v).strip()][:maximo]


class _BaseZeus(BaseModel):
    @field_validator("nombre", "titulo", "sinopsis", "premisa", "briefing", "descripcion", "texto", "razon",
                     "objetivo", "complicacion", "objetivo_final", "cronica", mode="before", check_fields=False)
    @classmethod
    def _recortar(cls, valor: Any) -> Any:
        return recortar(valor, 600) if isinstance(valor, str) else valor

    @field_validator("bando", "bando_jugadores", mode="before", check_fields=False)
    @classmethod
    def _normalizar_bando(cls, valor: Any) -> Any:
        return _bando(valor)


# ---------------------------------------------------------------------------------- ambientación
class BandoAmbiente(_BaseZeus):
    nombre: str
    descripcion: str = ""


class AmbientacionPlan(_BaseZeus):
    """La ambientación que Zeus inventa para la campaña (se convierte en un PerfilCampana)."""

    era: str = Field(description="Era: " + ", ".join(ERAS))
    epoca: str = Field(description="Dónde y cuándo, p. ej. 'Normandía, junio de 1944'")
    descripcion: str
    terreno: str = Field(description="Terreno de ARMA 3 principal, de la lista de instalados")
    jugadores: BandoAmbiente
    enemigo: BandoAmbiente
    doctrina_enemiga: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 3})
    doctrina: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 4})
    restricciones: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 3})
    estilo_radio: str = ""
    medios_aereos: bool = True
    retardo_radio_s: int = 0
    indicativo_mando: str = "Mando"
    indicativos: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 6})
    indicativo_mando_enemigo: str = "Mando enemigo"
    indicativos_enemigos: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 6})
    vocabulario: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 6})

    @field_validator("era", mode="before")
    @classmethod
    def _era(cls, valor: Any) -> str:
        return normalizar_era(valor)

    @field_validator("doctrina_enemiga", "doctrina", "restricciones", mode="before")
    @classmethod
    def _listas(cls, valor: Any) -> Any:
        return _lista_textos(valor, 4)

    @field_validator("indicativos", "indicativos_enemigos", "vocabulario", mode="before")
    @classmethod
    def _cortos(cls, valor: Any) -> Any:
        return _lista_textos(valor, 6, 30)

    @field_validator("retardo_radio_s", mode="before")
    @classmethod
    def _retardo(cls, valor: Any) -> int:
        try:
            return max(0, min(120, int(float(valor))))
        except (TypeError, ValueError):
            return 0

    def a_perfil(self, nombre: str) -> PerfilCampana:
        return PerfilCampana(
            id=self.era,
            nombre=nombre,
            epoca=self.epoca,
            descripcion=self.descripcion,
            bando_ia=InfoBando(nombre=self.jugadores.nombre, descripcion=self.jugadores.descripcion),
            enemigo=InfoBando(
                nombre=self.enemigo.nombre, descripcion=self.enemigo.descripcion, doctrina=self.doctrina_enemiga,
                indicativo_mando=self.indicativo_mando_enemigo,
                indicativos={"infanteria": self.indicativos_enemigos},
            ),
            estilo_comunicacion=self.estilo_radio or "Radio militar: frases breves, indicativos y cuadrículas.",
            doctrina=self.doctrina,
            restricciones=self.restricciones,
            medios_aereos=self.medios_aereos,
            retardo_radio_s=self.retardo_radio_s,
            indicativo_mando=self.indicativo_mando,
            indicativo_enlace=f"{self.indicativo_mando}-Radio",
            indicativos={"infanteria": self.indicativos},
            vocabulario=self.vocabulario,
        )


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

    @field_validator("radio_m", mode="before")
    @classmethod
    def _radio(cls, valor: Any) -> float:
        try:
            return max(50.0, min(1000.0, float(valor)))
        except (TypeError, ValueError):
            return 250.0


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


class ObjetoPlan(_BaseZeus):
    """Un elemento del decorado que Zeus coloca o un cambio que hace en el terreno."""

    tipo: TipoObjeto
    lugar: str = Field(description="Lugar o cuadrícula")
    bando: str | None = Field(default=None, description="Quién lo ha puesto (blufor, opfor) o null si es neutral")
    radio_m: float = 60.0
    descripcion: str = ""

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        if not isinstance(valor, str):
            return valor
        norm = normalizar(valor)
        if norm.replace(" ", "_") in {t.value for t in TipoObjeto}:
            return norm.replace(" ", "_")
        return next((tipo for clave, tipo in SINONIMOS_OBJETO if clave in norm), "ruinas")

    @field_validator("radio_m", mode="before")
    @classmethod
    def _radio(cls, valor: Any) -> float:
        try:
            return max(10.0, min(400.0, float(valor)))
        except (TypeError, ValueError):
            return 60.0


class BandoPlan(_BaseZeus):
    bando: str
    nombre: str = ""
    mando: bool = Field(default=True, description="false = cada grupo actúa por su cuenta, sin comandante")
    comandante: Persona | None = None
    base: str | None = None
    unidades: list[UnidadPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 10})
    objetivos: list[ObjetivoPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 3})


class TipoIntervencion(str, Enum):
    REFUERZOS = "refuerzos"
    CLIMA = "clima"
    HORA = "hora"
    MENSAJE = "mensaje"
    ORDEN = "orden"
    DECORADO = "decorado"
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
    objeto: ObjetoPlan | None = Field(default=None, description="Para decorado: lo que coloca en el mapa")

    @field_validator("tipo", mode="before")
    @classmethod
    def _tipo(cls, valor: Any) -> Any:
        if not isinstance(valor, str):
            return valor
        norm = normalizar(valor)
        for clave, tipo in (("refuer", "refuerzos"), ("spawn", "refuerzos"), ("crear", "refuerzos"),
                            ("clima", "clima"), ("tiempo", "clima"), ("hora", "hora"), ("mensaje", "mensaje"),
                            ("aviso", "mensaje"), ("orden", "orden"), ("mover", "orden"), ("decor", "decorado"),
                            ("objeto", "decorado"), ("colocar", "decorado"), ("terreno", "decorado")):
            if norm.startswith(clave):
                return tipo
        return norm if norm in {t.value for t in TipoIntervencion} else "nada"

    @field_validator("tarea", mode="before")
    @classmethod
    def _tarea(cls, valor: Any) -> Any:
        return normalizar_enum(valor, TareaMando) if valor else None


class SucesoPlan(AccionZeus):
    """Algo que Zeus deja preparado para que ocurra en un minuto concreto de la misión."""

    minuto: int = Field(default=20, description="Minuto de la misión en que ocurre")

    @field_validator("minuto", mode="before")
    @classmethod
    def _minuto(cls, valor: Any) -> int:
        try:
            return max(1, int(float(valor)))
        except (TypeError, ValueError):
            return 20


class PlanMision(_BaseZeus):
    nombre: str
    briefing: str = Field(description="Lo que se cuenta a los jugadores al empezar")
    terreno: str = Field(default="", description="Terreno de ARMA 3 de la lista de instalados")
    zona: str = Field(default="", description="Zona concreta del terreno donde ocurre")
    hora: str = "dia"
    clima: str = "despejado"
    tamano_km: int = 4
    lugares: list[LugarPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 8})
    decorado: list[ObjetoPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 10})
    bandos: list[BandoPlan] = Field(json_schema_extra={"minItems": 2, "maxItems": 3})
    bando_jugadores: str = "blufor"
    sucesos: list[SucesoPlan] = Field(default_factory=list, json_schema_extra={"maxItems": 4})

    @field_validator("hora", mode="before")
    @classmethod
    def _hora(cls, valor: Any) -> Any:
        return normalizar_hora(valor)

    @field_validator("clima", mode="before")
    @classmethod
    def _clima(cls, valor: Any) -> Any:
        return normalizar_clima(valor)

    @field_validator("tamano_km", mode="before")
    @classmethod
    def _tamano(cls, valor: Any) -> int:
        try:
            return max(2, min(10, int(float(valor))))
        except (TypeError, ValueError):
            return 4


def normalizar_hora(valor: Any) -> str:
    norm = normalizar(str(valor or "dia"))
    for clave, hora in (("noche", "noche"), ("nocturn", "noche"), ("amanec", "amanecer"), ("alba", "amanecer"),
                        ("atardec", "atardecer"), ("anochec", "atardecer"), ("crepus", "atardecer")):
        if clave in norm:
            return hora
    return "dia"


def normalizar_clima(valor: Any) -> str:
    norm = normalizar(str(valor or "despejado"))
    for clave, clima in (("lluv", "lluvia"), ("torment", "lluvia"), ("niebla", "niebla"), ("bruma", "niebla"),
                         ("nub", "nubes"), ("cubierto", "nubes")):
        if clave in norm:
            return clima
    return "despejado"


# ---------------------------------------------------------------------------------- campaña
class TipoMision(str, Enum):
    ASALTO = "asalto"
    DEFENSA = "defensa"
    EMBOSCADA = "emboscada"
    RESCATE = "rescate"
    RECONOCIMIENTO = "reconocimiento"
    DESTRUCCION = "destruccion"


class CapituloPlan(_BaseZeus):
    """Un paso de la campaña (una misión) hacia el objetivo final."""

    titulo: str
    sinopsis: str
    tipo: TipoMision = TipoMision.ASALTO
    objetivo: str = Field(default="", description="Qué hay que conseguir en esta misión")
    complicacion: str = Field(default="", description="El problema o inconveniente que lo complica")
    critico: bool = Field(default=False, description="true si perderla hace imposible el objetivo final")

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
                            ("patrull", "reconocimiento"), ("infiltr", "reconocimiento"), ("destru", "destruccion"),
                            ("sabot", "destruccion"), ("volar", "destruccion")):
            if norm.startswith(clave):
                return tipo
        return norm if norm in {t.value for t in TipoMision} else "asalto"


class PlanCampana(_BaseZeus):
    nombre: str
    premisa: str = Field(description="De qué va la campaña, en 2-4 frases")
    objetivo_final: str = Field(description="El único gran objetivo de toda la campaña")
    capitulos: list[CapituloPlan] = Field(description="Exactamente las misiones pedidas, en orden")
    personajes: list[Persona] = Field(default_factory=list, description="NPC aliados que aparecen en toda la campaña",
                                      json_schema_extra={"maxItems": 6})
    antagonista: Persona | None = None


# ---------------------------------------------------------------------------------- en directo y después
class IntervencionZeus(_BaseZeus):
    razon: str = ""
    acciones: list[AccionZeus] = Field(default_factory=list, json_schema_extra={"maxItems": 3})


class DesenlaceZeus(_BaseZeus):
    """Lo que Zeus escribe al terminar una misión: la crónica y cómo cambia el siguiente paso."""

    cronica: str = Field(description="Lo que ha pasado, contado como historia en 3-5 frases")
    sinopsis_siguiente: str = Field(default="", description="Nueva sinopsis de la siguiente misión (o vacío)")
    complicacion_siguiente: str = Field(default="", description="Nuevo problema de la siguiente misión (o vacío)")
