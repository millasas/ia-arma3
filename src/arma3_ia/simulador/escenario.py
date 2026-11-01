"""La misión que prepara Zeus, lista para jugarse (en el simulador o, más adelante, en ARMA 3).

No hay misiones fijas: todas las crea Zeus (``zeus.construir_escenario``). Cada misión se
guarda en YAML dentro de la carpeta de su campaña para poder revisarla o repetirla.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..agentes.esquemas import OrdenMando
from ..geo import Posicion
from ..modelo import Condiciones, Mapa, Objetivo, ObjetoMapa, Persona, Postura, Referencia, TipoUnidad, ZonaAterrizaje


class UnidadEscenario(BaseModel):
    id: str
    indicativo: str
    tipo: TipoUnidad = TipoUnidad.INFANTERIA
    posicion: Posicion
    efectivos: int = 8
    armada: bool = False
    capacidad: int = 0
    municion: float = 1.0
    combustible: float = 1.0
    postura: Postura = Postura.NORMAL
    # El jefe del grupo como persona y su tarea inicial (las pone Zeus).
    persona: Persona | None = None
    orden: OrdenMando | None = None
    # En ARMA 3 este grupo lo llevan jugadores humanos (en el simulador lo suple la IA).
    jugadores: bool = False


class BandoEscenario(BaseModel):
    nombre: str = ""
    # Sin mando, cada grupo de NPC decide por su cuenta con la tarea que le dio Zeus.
    mando: bool = True
    comandante: Persona | None = None
    base: Posicion | None = None
    unidades: list[UnidadEscenario] = Field(default_factory=list)
    objetivos: list[Objetivo] = Field(default_factory=list)


class Escenario(BaseModel):
    id: str
    nombre: str
    briefing: str = ""
    # Terreno de ARMA 3 que ha elegido Zeus ("Altis") y la zona concreta ("alrededores de Kavala").
    terreno: str = ""
    zona: str = ""
    mapa: Mapa
    duracion_max_s: float = 3600.0
    paso_s: float = 30.0
    tiempo_control_s: float = 60.0
    condiciones: Condiciones = Field(default_factory=Condiciones)
    referencias: list[Referencia] = Field(default_factory=list)
    zonas_aterrizaje: list[ZonaAterrizaje] = Field(default_factory=list)
    # Decorado y cambios en el terreno que ha hecho Zeus (fortificaciones, minas, barricadas...).
    objetos: list[ObjetoMapa] = Field(default_factory=list)
    bandos: dict[str, BandoEscenario]
    # Bando de los jugadores humanos: su resultado es el de la misión.
    bando_jugadores: str | None = None


def guardar_escenario(escenario: Escenario, ruta: str | Path, extra: dict | None = None) -> Path:
    """Guarda la misión en YAML. `extra` añade datos que no forman parte del escenario (p. ej. sucesos)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = escenario.model_dump(mode="json", exclude_defaults=True)
    if extra:
        datos.update(extra)
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ruta


def cargar_escenario(ruta: str | Path) -> Escenario:
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}
    return Escenario.model_validate(datos)
