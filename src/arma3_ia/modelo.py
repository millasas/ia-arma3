"""Modelo de datos compartido entre la IA, el simulador y el futuro puente con ARMA 3.

Todo lo que cruza la frontera "mundo <-> IA" está definido aquí y se serializa a JSON
sin pérdidas, de modo que el puente con ARMA 3 solo tendrá que transportar estos objetos.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .geo import Posicion
from .texto import normalizar


class TipoUnidad(str, Enum):
    INFANTERIA = "infanteria"
    MOTORIZADA = "motorizada"
    BLINDADO = "blindado"
    HELICOPTERO = "helicoptero"
    DESCONOCIDO = "desconocido"

    @property
    def es_aerea(self) -> bool:
        return self is TipoUnidad.HELICOPTERO


NOMBRE_TIPO = {
    TipoUnidad.INFANTERIA: "infantería",
    TipoUnidad.MOTORIZADA: "motorizada",
    TipoUnidad.BLINDADO: "blindado",
    TipoUnidad.HELICOPTERO: "helicóptero",
    TipoUnidad.DESCONOCIDO: "desconocido",
}


class Postura(str, Enum):
    SIGILO = "sigilo"
    NORMAL = "normal"
    COMBATE = "combate"


class Altitud(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class TipoAccion(str, Enum):
    # Unidades de tierra
    MOVER = "mover"
    ATACAR = "atacar"
    DEFENDER = "defender"
    RECONOCER = "reconocer"
    RETIRARSE = "retirarse"
    ESPERAR = "esperar"
    EMBARCAR = "embarcar"
    # Aeronaves
    VOLAR = "volar"
    ATERRIZAR = "aterrizar"
    DESEMBARCAR = "desembarcar"
    APOYO_AEREO = "apoyo_aereo"
    REGRESAR_BASE = "regresar_base"


ACCIONES_TIERRA = frozenset(
    {
        TipoAccion.MOVER,
        TipoAccion.ATACAR,
        TipoAccion.DEFENDER,
        TipoAccion.RECONOCER,
        TipoAccion.RETIRARSE,
        TipoAccion.ESPERAR,
        TipoAccion.EMBARCAR,
    }
)
ACCIONES_AIRE = frozenset(
    {
        TipoAccion.VOLAR,
        TipoAccion.ATERRIZAR,
        TipoAccion.DESEMBARCAR,
        TipoAccion.APOYO_AEREO,
        TipoAccion.REGRESAR_BASE,
        TipoAccion.ESPERAR,
    }
)


class Accion(BaseModel):
    """Orden concreta para una unidad del juego (lo que ejecuta el motor: ARMA 3 o el simulador)."""

    unidad: str
    tipo: TipoAccion
    destino: Posicion | None = None
    objetivo: str | None = None
    postura: Postura | None = None
    altitud: Altitud | None = None
    parametros: dict[str, float] = Field(default_factory=dict)
    motivo: str = ""


class ResultadoAccion(BaseModel):
    accion: Accion
    aceptada: bool
    motivo: str = ""


class EstadoUnidad(str, Enum):
    ACTIVA = "activa"
    EMBARCADA = "embarcada"
    DESTRUIDA = "destruida"


class UnidadPropia(BaseModel):
    """Estado de una unidad (grupo) del bando que controla la IA."""

    id: str
    indicativo: str
    tipo: TipoUnidad
    posicion: Posicion
    efectivos: int
    efectivos_max: int
    municion: float = 1.0
    moral: float = 1.0
    estado: EstadoUnidad = EstadoUnidad.ACTIVA
    postura: Postura = Postura.NORMAL
    accion_actual: TipoAccion | None = None
    destino_actual: Posicion | None = None
    en_contacto: bool = False
    bajo_fuego: bool = False
    # Solo aeronaves
    en_vuelo: bool = False
    altitud_m: float = 0.0
    combustible: float = 1.0
    danos: float = 0.0
    armada: bool = False
    capacidad: int = 0
    pasajeros: list[str] = Field(default_factory=list)
    # Solo unidades de tierra transportadas
    embarcada_en: str | None = None

    @property
    def es_aerea(self) -> bool:
        return self.tipo.es_aerea

    @property
    def operativa(self) -> bool:
        return self.estado != EstadoUnidad.DESTRUIDA and self.efectivos > 0

    @property
    def proporcion_efectivos(self) -> float:
        return self.efectivos / self.efectivos_max if self.efectivos_max else 0.0


class Contacto(BaseModel):
    """Enemigo detectado (posición estimada, no exacta)."""

    id: str
    tipo: TipoUnidad = TipoUnidad.DESCONOCIDO
    posicion: Posicion
    efectivos_estimados: int | None = None
    visto_hace_s: float = 0.0
    precision_m: float = 50.0
    en_vuelo: bool = False


class Rasgo(str, Enum):
    """Rasgos de carácter de un NPC: cambian cómo decide y cómo habla."""

    VETERANO = "veterano"
    NOVATO = "novato"
    VALIENTE = "valiente"
    PRUDENTE = "prudente"
    AGRESIVO = "agresivo"
    DISCIPLINADO = "disciplinado"
    NERVIOSO = "nervioso"
    IMPULSIVO = "impulsivo"


# Cuánto aguanta bajo el fuego cada rasgo (positivo = más valor, negativo = se rompe antes).
VALOR_RASGO = {
    Rasgo.VETERANO: 0.15,
    Rasgo.NOVATO: -0.15,
    Rasgo.VALIENTE: 0.3,
    Rasgo.PRUDENTE: -0.15,
    Rasgo.AGRESIVO: 0.2,
    Rasgo.DISCIPLINADO: 0.1,
    Rasgo.NERVIOSO: -0.3,
    Rasgo.IMPULSIVO: 0.1,
}


# Rangos que el modelo a veces mete dentro del nombre ("Colonel Varga"), en varios idiomas y épocas.
RANGOS_CONOCIDOS = {
    "soldado", "private", "cabo", "corporal", "sargento", "sergeant", "sgt", "teniente", "lieutenant",
    "lt", "capitan", "captain", "capt", "comandante", "commander", "mayor", "major", "coronel",
    "colonel", "col", "general", "almirante", "admiral", "gefreiter", "unteroffizier", "feldwebel",
    "leutnant", "oberleutnant", "hauptmann", "oberst", "jefe", "lider", "emir", "comisario",
}


class Persona(BaseModel):
    """El jefe de un grupo de NPC visto como una persona: nombre, rango, carácter e historia."""

    nombre: str
    rango: str = ""
    rasgos: list[Rasgo] = Field(default_factory=list)
    historia: str = ""
    # Lo que este personaje ha vivido en misiones anteriores de la campaña (lo más reciente al final).
    recuerdos: list[str] = Field(default_factory=list)
    femenina: bool = False

    @field_validator("rasgos", mode="before")
    @classmethod
    def _rasgos(cls, valor: Any) -> Any:
        if isinstance(valor, str):
            valor = [valor]
        if not isinstance(valor, list):
            return valor
        validos = {r.value for r in Rasgo}
        limpios: list[str] = []
        for v in valor:
            texto = str(getattr(v, "value", v)).strip().lower()
            texto = texto.rstrip("a") + "o" if texto.rstrip("a") + "o" in validos else texto
            if texto in validos and texto not in limpios:
                limpios.append(texto)
        return limpios[:3]

    @model_validator(mode="after")
    def _rango_fuera_del_nombre(self) -> Persona:
        """'Colonel Varga' con rango 'Sargento' -> nombre 'Varga', rango 'Colonel' (no 'Sargento Colonel')."""
        partes = self.nombre.split()
        rangos: list[str] = []
        while len(partes) > 1 and normalizar(partes[0]).replace(" ", "") in RANGOS_CONOCIDOS:
            rangos.append(partes.pop(0).rstrip("."))
        if rangos:
            self.nombre = " ".join(partes)
            self.rango = " ".join(rangos)
        return self

    @field_validator("historia", mode="before")
    @classmethod
    def _historia(cls, valor: Any) -> Any:
        return valor[:300] if isinstance(valor, str) else valor

    @property
    def valor(self) -> float:
        """Entre -0.5 y 0.5: cuánto aguanta antes de romper el contacto."""
        return max(-0.5, min(0.5, sum(VALOR_RASGO[r] for r in self.rasgos)))

    def tiene(self, *rasgos: Rasgo) -> bool:
        return any(r in self.rasgos for r in rasgos)

    def rasgo_texto(self, rasgo: Rasgo) -> str:
        """El rasgo concordado con la persona: 'veterano' o 'veterana'."""
        return rasgo.value[:-1] + "a" if self.femenina and rasgo.value.endswith("o") else rasgo.value

    def presentacion(self) -> str:
        nombre = f"{self.rango} {self.nombre}".strip()
        caracter = ", ".join(self.rasgo_texto(r) for r in self.rasgos)
        texto = f"{nombre} ({caracter})" if caracter else nombre
        if self.historia:
            texto += f". {self.historia.rstrip('.')}."
        if self.recuerdos:
            texto += " Recuerdas: " + " ".join(r.rstrip(".") + "." for r in self.recuerdos[-3:])
        return texto


class TipoObjetivo(str, Enum):
    CAPTURAR = "capturar"
    DESTRUIR = "destruir"
    DEFENDER = "defender"
    EXTRAER = "extraer"
    RECONOCER = "reconocer"


class EstadoObjetivo(str, Enum):
    PENDIENTE = "pendiente"
    COMPLETADO = "completado"
    FALLIDO = "fallido"


class Objetivo(BaseModel):
    id: str
    nombre: str
    tipo: TipoObjetivo
    posicion: Posicion
    radio_m: float = 200.0
    estado: EstadoObjetivo = EstadoObjetivo.PENDIENTE
    prioridad: int = 1
    obligatorio: bool = True
    descripcion: str = ""
    # EXTRAER: unidades propias que deben llegar; DESTRUIR: unidades enemigas (solo las ve el mundo)
    unidades: list[str] = Field(default_factory=list)
    # DEFENDER: hasta qué segundo de misión hay que aguantar
    hasta_s: float | None = None


class ZonaAterrizaje(BaseModel):
    id: str
    nombre: str
    posicion: Posicion


class Referencia(BaseModel):
    """Accidente del terreno con nombre (bosque, pueblo, colina...)."""

    nombre: str
    tipo: str
    posicion: Posicion
    radio_m: float = 200.0


class Condiciones(BaseModel):
    hora: str = "dia"
    clima: str = "despejado"
    visibilidad_m: float = 1500.0
    viento_ms: float = 3.0


class Evento(BaseModel):
    tiempo: float
    tipo: str
    descripcion: str
    unidad: str | None = None
    datos: dict[str, Any] = Field(default_factory=dict)


class Mapa(BaseModel):
    nombre: str = ""
    ancho_m: float
    alto_m: float


class Observacion(BaseModel):
    """Lo que un bando sabe en un instante: sus unidades, los contactos y la misión."""

    tiempo: float
    bando: str
    mapa: Mapa | None = None
    unidades: list[UnidadPropia] = Field(default_factory=list)
    contactos: list[Contacto] = Field(default_factory=list)
    objetivos: list[Objetivo] = Field(default_factory=list)
    zonas_aterrizaje: list[ZonaAterrizaje] = Field(default_factory=list)
    referencias: list[Referencia] = Field(default_factory=list)
    base: Posicion | None = None
    condiciones: Condiciones = Field(default_factory=Condiciones)
    eventos: list[Evento] = Field(default_factory=list)

    def unidad(self, id_unidad: str) -> UnidadPropia | None:
        return next((u for u in self.unidades if u.id == id_unidad), None)

    def buscar_unidad(self, texto: str | None) -> UnidadPropia | None:
        """Busca una unidad propia por id o indicativo, tolerando mayúsculas, tildes y guiones."""
        if not texto:
            return None
        norm = normalizar(texto)
        for u in self.unidades:
            if norm in (normalizar(u.id), normalizar(u.indicativo)):
                return u
        candidatas = [
            u
            for u in self.unidades
            if normalizar(u.indicativo) in norm or (len(norm) >= 3 and norm in normalizar(u.indicativo))
        ]
        return candidatas[0] if len(candidatas) == 1 else None

    def contacto(self, texto: str | None) -> Contacto | None:
        if not texto:
            return None
        norm = normalizar(texto)
        return next((c for c in self.contactos if normalizar(c.id) == norm), None)

    def operativas(self, aereas: bool | None = None) -> list[UnidadPropia]:
        return [
            u for u in self.unidades if u.operativa and (aereas is None or u.es_aerea == aereas)
        ]

    def lugares(self) -> dict[str, Posicion]:
        """Nombres que el modelo puede usar como destino, con su posición."""
        lugares: dict[str, Posicion] = {}
        for r in self.referencias:
            lugares[r.nombre] = r.posicion
        for o in self.objetivos:
            lugares[o.nombre] = o.posicion
            lugares[o.id] = o.posicion
        for z in self.zonas_aterrizaje:
            lugares[z.nombre] = z.posicion
            lugares[z.id] = z.posicion
        for c in self.contactos:
            lugares[c.id] = c.posicion
        for u in self.unidades:
            if u.operativa:
                lugares[u.indicativo] = u.posicion
                lugares[u.id] = u.posicion
        if self.base is not None:
            lugares["base"] = self.base
        return lugares

    def limites(self) -> tuple[float, float] | None:
        return (self.mapa.ancho_m, self.mapa.alto_m) if self.mapa else None


class ResultadoMision(BaseModel):
    estado: Literal["victoria", "derrota", "parcial"]
    motivo: str
    duracion_s: float
    objetivos_completados: int
    objetivos_totales: int
    efectivos_iniciales: int
    bajas_propias: int
    bajas_enemigas: int
    puntuacion: float
    metricas: dict[str, Any] = Field(default_factory=dict)

    @property
    def exito(self) -> bool:
        return self.estado == "victoria"


class MensajeChat(BaseModel):
    """Mensaje de chat del juego (de un jugador o de la IA)."""

    autor: str
    texto: str
    canal: str = "lado"
    tiempo: float = 0.0
    es_ia: bool = False


# ---------------------------------------------------------------------------------- Zeus
class GrupoNuevo(BaseModel):
    """Grupo que Zeus hace aparecer en mitad de una misión (refuerzos)."""

    id: str
    indicativo: str
    tipo: TipoUnidad = TipoUnidad.INFANTERIA
    posicion: Posicion
    efectivos: int = 6
    armada: bool = False
    capacidad: int = 0


class GrupoGlobal(BaseModel):
    id: str
    indicativo: str
    bando: str
    tipo: TipoUnidad
    posicion: Posicion
    efectivos: int
    efectivos_max: int
    estado: EstadoUnidad
    accion: TipoAccion | None = None
    en_combate: bool = False


class VistaGlobal(BaseModel):
    """Lo que ve Zeus: toda la verdad del mundo, sin niebla de guerra."""

    tiempo: float
    duracion_max_s: float | None = None
    bando_jugadores: str | None = None
    grupos: list[GrupoGlobal] = Field(default_factory=list)
    objetivos: dict[str, list[Objetivo]] = Field(default_factory=dict)
    condiciones: Condiciones = Field(default_factory=Condiciones)
    mapa: Mapa | None = None

    def fuerza(self, bando: str) -> tuple[int, int]:
        """(efectivos actuales, efectivos máximos) de las unidades de tierra de un bando."""
        grupos = [g for g in self.grupos if g.bando == bando and not g.tipo.es_aerea]
        return sum(g.efectivos for g in grupos), sum(g.efectivos_max for g in grupos)

    def bandos(self) -> list[str]:
        return sorted({g.bando for g in self.grupos} | set(self.objetivos))
