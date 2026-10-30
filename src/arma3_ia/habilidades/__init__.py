"""Habilidades numéricas que se aprenden por práctica (sin modelo de lenguaje)."""

from __future__ import annotations

import random
from typing import Any

from ..memoria import Memoria
from .aterrizaje import (
    PARAMETROS_ATERRIZAJE,
    PARAMETROS_POR_DEFECTO,
    CondicionesAterrizaje,
    HabilidadAterrizaje,
    ResultadoAterrizaje,
    condiciones_aleatorias,
    evaluar,
    simular_aterrizaje,
)
from .optimizador import OptimizadorCEM, Parametro

__all__ = [
    "PARAMETROS_ATERRIZAJE",
    "PARAMETROS_POR_DEFECTO",
    "CondicionesAterrizaje",
    "HabilidadAterrizaje",
    "Habilidades",
    "OptimizadorCEM",
    "Parametro",
    "ResultadoAterrizaje",
    "condiciones_aleatorias",
    "evaluar",
    "simular_aterrizaje",
]


class Habilidades:
    """Conjunto de habilidades de un bando, guardadas en la memoria."""

    def __init__(
        self, memoria: Memoria | None = None, *, explorar: bool = False, semilla: int | None = None
    ) -> None:
        self.explorar = explorar
        self.aterrizaje = HabilidadAterrizaje(memoria, random.Random(semilla))

    def parametros_aterrizaje(self, zona_caliente: bool) -> dict[str, float]:
        return self.aterrizaje.parametros(zona_caliente, explorar=self.explorar)

    def informar_aterrizaje(self, datos: dict[str, Any]) -> None:
        """Aprende de un aterrizaje real (evento "aterrizaje" del mundo)."""
        parametros = datos.get("parametros")
        if isinstance(parametros, dict) and "puntuacion" in datos:
            self.aterrizaje.informar(
                parametros, bool(datos.get("zona_caliente")), float(datos["puntuacion"])
            )

    def guardar(self) -> None:
        self.aterrizaje.guardar()
