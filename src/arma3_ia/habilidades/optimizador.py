"""Optimizador por entropía cruzada (CEM) con estado persistente.

Aprende parámetros numéricos de una maniobra a base de ensayo y error: prueba variantes,
se queda con las mejores y desplaza su "idea" de cómo hacerlo hacia ellas. Es robusto con
resultados ruidosos (como un aterrizaje con viento) y no necesita gradientes ni GPU.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Parametro:
    nombre: str
    minimo: float
    maximo: float

    def normalizar(self, valor: float) -> float:
        return (valor - self.minimo) / (self.maximo - self.minimo)

    def desnormalizar(self, valor: float) -> float:
        return self.minimo + min(max(valor, 0.0), 1.0) * (self.maximo - self.minimo)


class OptimizadorCEM:
    def __init__(
        self,
        parametros: list[Parametro],
        iniciales: dict[str, float] | None = None,
        *,
        poblacion: int = 16,
        elite: float = 0.25,
        suavizado: float = 0.7,
        sigma_inicial: float = 0.25,
        sigma_minima: float = 0.03,
        rng: random.Random | None = None,
    ) -> None:
        self.parametros = parametros
        self.poblacion = poblacion
        self.elite = elite
        self.suavizado = suavizado
        self.sigma_minima = sigma_minima
        self.rng = rng or random.Random()
        iniciales = iniciales or {}
        self.media = [
            p.normalizar(iniciales[p.nombre]) if p.nombre in iniciales else 0.5 for p in parametros
        ]
        self.sigma = [sigma_inicial] * len(parametros)
        self.generacion = 0
        self.evaluaciones = 0
        self.mejor: tuple[float, dict[str, float]] | None = None
        self.historial: list[float] = []
        self._lote: list[tuple[list[float], float]] = []

    def _a_dict(self, vector: list[float]) -> dict[str, float]:
        return {p.nombre: round(p.desnormalizar(v), 4) for p, v in zip(self.parametros, vector)}

    def _a_vector(self, valores: dict[str, float]) -> list[float]:
        return [
            min(max(p.normalizar(valores.get(p.nombre, p.desnormalizar(m))), 0.0), 1.0)
            for p, m in zip(self.parametros, self.media)
        ]

    def proponer(self) -> dict[str, float]:
        """Variante a probar, muestreada alrededor de la mejor idea actual."""
        vector = [
            min(max(self.rng.gauss(m, s), 0.0), 1.0) for m, s in zip(self.media, self.sigma)
        ]
        return self._a_dict(vector)

    def actuales(self) -> dict[str, float]:
        """Lo que mejor sabe hacer ahora mismo (la media de la distribución)."""
        return self._a_dict(self.media)

    def informar(self, valores: dict[str, float], puntuacion: float) -> bool:
        """Registra el resultado de una prueba. Devuelve True si se cerró una generación."""
        self.evaluaciones += 1
        if self.mejor is None or puntuacion > self.mejor[0]:
            self.mejor = (puntuacion, dict(valores))
        self._lote.append((self._a_vector(valores), puntuacion))
        if len(self._lote) < self.poblacion:
            return False
        self._lote.sort(key=lambda par: par[1], reverse=True)
        n_elite = max(2, round(self.poblacion * self.elite))
        elite = [v for v, _ in self._lote[:n_elite]]
        for i in range(len(self.parametros)):
            valores_i = [v[i] for v in elite]
            media_i = sum(valores_i) / len(valores_i)
            var_i = sum((x - media_i) ** 2 for x in valores_i) / len(valores_i)
            self.media[i] = self.suavizado * media_i + (1 - self.suavizado) * self.media[i]
            sigma_nueva = self.suavizado * var_i**0.5 + (1 - self.suavizado) * self.sigma[i]
            self.sigma[i] = max(self.sigma_minima, sigma_nueva)
        self.historial.append(sum(p for _, p in self._lote) / len(self._lote))
        self.generacion += 1
        self._lote = []
        return True

    def estado(self) -> dict[str, Any]:
        return {
            "media": self.media,
            "sigma": self.sigma,
            "generacion": self.generacion,
            "evaluaciones": self.evaluaciones,
            "mejor": list(self.mejor) if self.mejor else None,
            "historial": self.historial[-200:],
        }

    def restaurar(self, estado: dict[str, Any]) -> None:
        if len(estado.get("media", [])) != len(self.parametros):
            return
        self.media = [float(x) for x in estado["media"]]
        self.sigma = [float(x) for x in estado["sigma"]]
        self.generacion = int(estado.get("generacion", 0))
        self.evaluaciones = int(estado.get("evaluaciones", 0))
        mejor = estado.get("mejor")
        self.mejor = (float(mejor[0]), dict(mejor[1])) if mejor else None
        self.historial = [float(x) for x in estado.get("historial", [])]
