"""Escenarios del simulador de texto (ficheros YAML)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..agentes.esquemas import OrdenMando
from ..geo import Posicion
from ..modelo import Condiciones, Mapa, Objetivo, Persona, Postura, Referencia, TipoUnidad, ZonaAterrizaje

DIRECTORIO_ESCENARIOS = Path(__file__).parent / "escenarios"


class UnidadEscenario(BaseModel):
    id: str
    indicativo: str | None = None
    tipo: TipoUnidad = TipoUnidad.INFANTERIA
    posicion: Posicion
    efectivos: int = 8
    armada: bool = False
    capacidad: int = 0
    municion: float = 1.0
    combustible: float = 1.0
    postura: Postura = Postura.NORMAL
    # Solo bandos controlados por guion
    comportamiento: Literal["defender", "patrulla", "emboscada", "qrf", "asalto"] = "defender"
    ruta: list[Posicion] = Field(default_factory=list)
    radio_activacion: float = 300.0
    # Bandos controlados por la IA: el jefe del grupo como persona y su tarea inicial (las pone Zeus).
    persona: Persona | None = None
    orden: OrdenMando | None = None
    # En ARMA 3 este grupo lo llevan jugadores humanos (en el simulador lo suple la IA).
    jugadores: bool = False


class BandoEscenario(BaseModel):
    controlador: Literal["ia", "guion"] = "guion"
    nombre: str = ""
    # Sin mando, cada grupo de NPC decide por su cuenta con la tarea que le dio Zeus.
    mando: bool = True
    comandante: Persona | None = None
    base: Posicion | None = None
    unidades: list[UnidadEscenario] = Field(default_factory=list)
    objetivos: list[Objetivo] = Field(default_factory=list)


class ChatGuion(BaseModel):
    """Mensaje de un "jugador" programado para probar el enlace sin escribir a mano."""

    tiempo: float
    autor: str
    texto: str
    bando: str = "blufor"


class Escenario(BaseModel):
    id: str
    nombre: str
    descripcion: str = ""
    perfil_recomendado: str = "moderno"
    mapa: Mapa
    duracion_max_s: float = 3600.0
    paso_s: float = 30.0
    tiempo_control_s: float = 60.0
    condiciones: Condiciones = Field(default_factory=Condiciones)
    referencias: list[Referencia] = Field(default_factory=list)
    zonas_aterrizaje: list[ZonaAterrizaje] = Field(default_factory=list)
    bandos: dict[str, BandoEscenario]
    chat: list[ChatGuion] = Field(default_factory=list)
    # Bando de los jugadores humanos: su resultado es el de la misión.
    bando_jugadores: str | None = None
    briefing: str = ""

    @property
    def bandos_ia(self) -> list[str]:
        return [nombre for nombre, b in self.bandos.items() if b.controlador == "ia"]


def guardar_escenario(escenario: Escenario, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = escenario.model_dump(mode="json", exclude_defaults=True)
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ruta


def cargar_escenario_fichero(ruta: str | Path) -> Escenario:
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}
    return Escenario.model_validate(datos)


def escenarios_disponibles(directorio_extra: str | Path | None = None) -> dict[str, Escenario]:
    escenarios: dict[str, Escenario] = {}
    directorios = [DIRECTORIO_ESCENARIOS]
    if directorio_extra and Path(directorio_extra).is_dir():
        directorios.append(Path(directorio_extra))
    for directorio in directorios:
        for ruta in sorted(directorio.glob("*.yaml")):
            escenario = cargar_escenario_fichero(ruta)
            escenarios[escenario.id] = escenario
    return escenarios


def cargar_escenario(id_o_ruta: str, directorio_extra: str | Path | None = None) -> Escenario:
    ruta = Path(id_o_ruta)
    if ruta.suffix in (".yaml", ".yml") and ruta.is_file():
        return cargar_escenario_fichero(ruta)
    escenarios = escenarios_disponibles(directorio_extra)
    if id_o_ruta not in escenarios:
        disponibles = ", ".join(sorted(escenarios))
        raise KeyError(f"No existe el escenario '{id_o_ruta}'. Disponibles: {disponibles}")
    return escenarios[id_o_ruta]
