"""Habilidad: aterrizaje de helicóptero aprendido por ensayo y error.

El modelo de lenguaje decide DÓNDE y CUÁNDO aterrizar; esta habilidad aprende CÓMO:
velocidad de aproximación, tasa de descenso y la recogida final (flare). Se entrena con
miles de aterrizajes simulados (sin GPU, en segundos) y sigue aprendiendo de los
aterrizajes reales de cada misión. Hay un perfil distinto para LZ frías y calientes:
en una LZ caliente compensa entrar más rápido aunque el aterrizaje sea más brusco.

El modelo físico es deliberadamente simple (no es el de ARMA 3), pero recoge los
fenómenos clave: llegar demasiado rápido, descender demasiado deprisa, el anillo de
vórtices (descenso rápido a poca velocidad), la sobrerrecogida, el viento, la noche y el
tiempo de exposición al fuego enemigo. Cuando exista el puente con ARMA 3, el mismo
optimizador podrá aprender con los aterrizajes reales del juego.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from ..memoria import Memoria
from .optimizador import OptimizadorCEM, Parametro

PARAMETROS_ATERRIZAJE = (
    Parametro("velocidad_aproximacion", 5.0, 40.0),
    Parametro("tasa_descenso", 0.5, 8.0),
    Parametro("altura_recogida", 3.0, 60.0),
    Parametro("intensidad_recogida", 0.1, 1.0),
)
# Lo que haría un piloto novato: entra rápido y recoge tarde.
PARAMETROS_POR_DEFECTO = {
    "velocidad_aproximacion": 18.0,
    "tasa_descenso": 3.5,
    "altura_recogida": 12.0,
    "intensidad_recogida": 0.5,
}
PUNTUACION_BASE = {"exito": 1.0, "abortado": 0.45, "duro": 0.2, "accidente": 0.0}


@dataclass(frozen=True)
class CondicionesAterrizaje:
    viento_ms: float = 3.0
    zona_caliente: bool = False
    noche: bool = False
    en_base: bool = False


@dataclass(frozen=True)
class ResultadoAterrizaje:
    resultado: str  # exito | abortado | duro | accidente
    danos: float
    tiempo_exposicion_s: float
    severidad: float
    puntuacion: float

    @property
    def exito(self) -> bool:
        return self.resultado == "exito"


def _sigmoide(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def simular_aterrizaje(
    parametros: dict[str, float], condiciones: CondicionesAterrizaje, rng: random.Random
) -> ResultadoAterrizaje:
    p = {**PARAMETROS_POR_DEFECTO, **parametros}
    v = p["velocidad_aproximacion"]
    vs = max(p["tasa_descenso"], 0.3)
    h = p["altura_recogida"]
    k = p["intensidad_recogida"]

    tiempo_recogida = h / vs
    deceleracion = 1.2 + 3.0 * k
    v_toque = max(0.0, v - deceleracion * tiempo_recogida)
    sobrerrecogida = max(0.0, k * h - 18.0) / 12.0
    vs_toque = vs * (1.0 - 0.75 * k) + 0.5 * sobrerrecogida
    anillo_vortices = _sigmoide((vs - 4.5) * 2.5) * _sigmoide((14.0 - v) * 0.5)

    rafagas = condiciones.viento_ms * (1.0 + 0.5 * rng.random())
    severidad = (v_toque / 10.0) ** 2 + (vs_toque / 3.0) ** 2 + 0.6 * sobrerrecogida
    severidad += (rafagas / 12.0) ** 2 * (1.6 if condiciones.noche else 1.0) * abs(rng.gauss(1.0, 0.5))
    if anillo_vortices > 0.15 and rng.random() < anillo_vortices:
        severidad += 2.5
    severidad *= rng.uniform(0.8, 1.25)
    if condiciones.en_base:
        severidad *= 0.6

    exposicion = 600.0 / max(v, 1.0) + 60.0 / vs + tiempo_recogida
    danos = 0.0
    if condiciones.zona_caliente and rng.random() < min(0.95, exposicion / 90.0):
        danos += rng.uniform(0.08, 0.3)

    if severidad > 3.5:
        resultado, danos = "accidente", 1.0
    elif severidad > 1.8:
        resultado = "duro"
        danos += rng.uniform(0.15, 0.35)
    elif severidad > 1.2 and rng.random() < 0.6:
        resultado = "abortado"
    else:
        resultado = "exito"

    puntuacion = PUNTUACION_BASE[resultado]
    if resultado != "accidente":
        penal_tiempo = 0.0045 if condiciones.zona_caliente else 0.0015
        puntuacion -= 0.5 * min(danos, 1.0) + penal_tiempo * exposicion
    return ResultadoAterrizaje(
        resultado=resultado,
        danos=round(min(danos, 1.0), 3),
        tiempo_exposicion_s=round(exposicion, 1),
        severidad=round(severidad, 3),
        puntuacion=round(max(0.0, puntuacion), 4),
    )


def condiciones_aleatorias(rng: random.Random, zona_caliente: bool) -> CondicionesAterrizaje:
    return CondicionesAterrizaje(
        viento_ms=rng.uniform(0.0, 9.0),
        zona_caliente=zona_caliente,
        noche=rng.random() < 0.3,
    )


def evaluar(
    parametros: dict[str, float], zona_caliente: bool, episodios: int, rng: random.Random
) -> dict[str, float]:
    """Tasa de éxito y puntuación media de unos parámetros en condiciones variadas."""
    resultados = [
        simular_aterrizaje(parametros, condiciones_aleatorias(rng, zona_caliente), rng)
        for _ in range(episodios)
    ]
    return {
        "exito": sum(r.exito for r in resultados) / episodios,
        "accidentes": sum(r.resultado == "accidente" for r in resultados) / episodios,
        "puntuacion": sum(r.puntuacion for r in resultados) / episodios,
    }


def contexto(zona_caliente: bool) -> str:
    return "caliente" if zona_caliente else "fria"


class HabilidadAterrizaje:
    NOMBRE = "aterrizaje_helicoptero"

    def __init__(self, memoria: Memoria | None = None, rng: random.Random | None = None) -> None:
        self.memoria = memoria
        self.rng = rng or random.Random()
        self.optimizadores = {
            nombre: OptimizadorCEM(list(PARAMETROS_ATERRIZAJE), PARAMETROS_POR_DEFECTO, rng=self.rng)
            for nombre in ("fria", "caliente")
        }
        if memoria is not None:
            estado = memoria.cargar_habilidad(self.NOMBRE) or {}
            for nombre, optimizador in self.optimizadores.items():
                if nombre in estado:
                    optimizador.restaurar(estado[nombre])

    def parametros(self, zona_caliente: bool, explorar: bool = False) -> dict[str, float]:
        optimizador = self.optimizadores[contexto(zona_caliente)]
        return optimizador.proponer() if explorar else optimizador.actuales()

    def informar(self, parametros: dict[str, float], zona_caliente: bool, puntuacion: float) -> None:
        conocidos = {p.nombre for p in PARAMETROS_ATERRIZAJE}
        valores = {k: float(v) for k, v in parametros.items() if k in conocidos}
        if len(valores) == len(conocidos):
            self.optimizadores[contexto(zona_caliente)].informar(valores, puntuacion)

    def entrenar(self, episodios: int, repeticiones: int = 8) -> list[dict[str, Any]]:
        """Entrena con aterrizajes simulados. Devuelve el progreso por generación."""
        progreso: list[dict[str, Any]] = []
        for i in range(episodios // repeticiones):
            caliente = i % 2 == 1
            optimizador = self.optimizadores[contexto(caliente)]
            candidato = optimizador.proponer()
            puntos = [
                simular_aterrizaje(candidato, condiciones_aleatorias(self.rng, caliente), self.rng).puntuacion
                for _ in range(repeticiones)
            ]
            if optimizador.informar(candidato, sum(puntos) / len(puntos)):
                progreso.append(
                    {
                        "contexto": contexto(caliente),
                        "generacion": optimizador.generacion,
                        "puntuacion_media": round(optimizador.historial[-1], 3),
                        "parametros": optimizador.actuales(),
                    }
                )
        self.guardar()
        return progreso

    def guardar(self) -> None:
        if self.memoria is not None:
            self.memoria.guardar_habilidad(
                self.NOMBRE, {n: o.estado() for n, o in self.optimizadores.items()}
            )
