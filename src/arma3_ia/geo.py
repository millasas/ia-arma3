"""Geometría del mapa: posiciones en metros y referencias de cuadrícula al estilo de ARMA 3.

En ARMA 3 el origen (0, 0) está en la esquina suroeste del mapa, X crece hacia el este
e Y hacia el norte. Una cuadrícula de 6 cifras ("045123") indica un cuadrado de 100 m:
las 3 primeras cifras son la X en hectómetros y las 3 últimas la Y.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from .texto import normalizar

_PUNTOS_CARDINALES = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")
_NUMERO = re.compile(r"\d+(?:\.\d+)?")
_VACIOS = {"", "none", "null", "ninguno", "ninguna", "nada", "n a", "na"}


class Posicion(BaseModel):
    """Punto del mapa en metros (x = este, y = norte).

    Acepta también los formatos de ARMA 3: ``[x, y]``, ``[x, y, z]`` o una cuadrícula ``"045123"``.
    """

    model_config = ConfigDict(frozen=True)

    x: float
    y: float

    @model_validator(mode="before")
    @classmethod
    def _aceptar_formatos(cls, valor: Any) -> Any:
        if isinstance(valor, (list, tuple)) and len(valor) >= 2:
            return {"x": float(valor[0]), "y": float(valor[1])}
        if isinstance(valor, str):
            pos = desde_cuadricula(valor)
            if pos is None:
                raise ValueError(f"cuadrícula no válida: {valor!r}")
            return {"x": pos.x, "y": pos.y}
        return valor

    def distancia(self, otra: Posicion) -> float:
        return math.hypot(otra.x - self.x, otra.y - self.y)

    def rumbo(self, otra: Posicion) -> float:
        """Rumbo en grados hacia `otra` (0 = norte, 90 = este)."""
        return math.degrees(math.atan2(otra.x - self.x, otra.y - self.y)) % 360.0

    def hacia(self, otra: Posicion, metros: float) -> Posicion:
        """Punto a `metros` de distancia en dirección a `otra` (sin pasarse)."""
        d = self.distancia(otra)
        if d <= metros or d == 0:
            return otra
        f = metros / d
        return Posicion(x=self.x + (otra.x - self.x) * f, y=self.y + (otra.y - self.y) * f)

    def desplazar(self, rumbo: float, metros: float) -> Posicion:
        rad = math.radians(rumbo)
        return Posicion(x=self.x + math.sin(rad) * metros, y=self.y + math.cos(rad) * metros)

    def limitar(self, ancho: float, alto: float) -> Posicion:
        """Devuelve la posición recortada a los bordes del mapa."""
        x = min(max(self.x, 0.0), ancho)
        y = min(max(self.y, 0.0), alto)
        return self if (x, y) == (self.x, self.y) else Posicion(x=x, y=y)

    def cuadricula(self, digitos: int = 6) -> str:
        if digitos not in (4, 6, 8, 10):
            raise ValueError("una cuadrícula tiene 4, 6, 8 o 10 cifras")
        n = digitos // 2
        escala = 10 ** (5 - n)
        cx = max(0, int(self.x // escala))
        cy = max(0, int(self.y // escala))
        return f"{cx:0{n}d}{cy:0{n}d}"

    def __str__(self) -> str:
        return self.cuadricula()


def desde_cuadricula(texto: str) -> Posicion | None:
    """Convierte "045123", "045 123" u "04501230" en el centro del cuadrado correspondiente."""
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) not in (4, 6, 8, 10):
        return None
    n = len(digitos) // 2
    escala = 10 ** (5 - n)
    return Posicion(
        x=int(digitos[:n]) * escala + escala / 2,
        y=int(digitos[n:]) * escala + escala / 2,
    )


def _dentro(pos: Posicion, limites: tuple[float, float] | None) -> bool:
    if limites is None:
        return True
    return 0 <= pos.x <= limites[0] and 0 <= pos.y <= limites[1]


def _buscar_lugar(texto_norm: str, lugares: Mapping[str, Posicion], exacto: bool) -> Posicion | None:
    mejor: tuple[int, Posicion] | None = None
    for nombre, pos in lugares.items():
        clave = normalizar(nombre)
        if not clave:
            continue
        if exacto:
            if clave == texto_norm:
                return pos
            continue
        coincide = len(clave) >= 3 and (clave in texto_norm or (len(texto_norm) >= 3 and texto_norm in clave))
        if coincide and (mejor is None or len(clave) > mejor[0]):
            mejor = (len(clave), pos)
    return mejor[1] if mejor else None


def _por_numeros(texto: str, limites: tuple[float, float] | None) -> Posicion | None:
    numeros = _NUMERO.findall(texto)
    candidatas: list[Posicion] = []
    if len(numeros) == 1 and "." not in numeros[0]:
        pos = desde_cuadricula(numeros[0])
        if pos is not None:
            candidatas.append(pos)
    elif len(numeros) >= 2:
        a, b = numeros[0], numeros[1]
        enteros = "." not in a and "." not in b
        if enteros and len(a) == len(b) and 2 <= len(a) <= 5:
            pos = desde_cuadricula(a + b)
            if pos is not None:
                candidatas.append(pos)
        candidatas.append(Posicion(x=float(a), y=float(b)))
    for pos in candidatas:
        if _dentro(pos, limites):
            return pos
    return candidatas[0] if candidatas and limites is None else None


def parsear_posicion(
    valor: Any,
    lugares: Mapping[str, Posicion] | None = None,
    limites: tuple[float, float] | None = None,
) -> Posicion | None:
    """Interpreta un destino escrito por el modelo o por un jugador.

    Admite cuadrículas ("045123", "045-123"), metros ("4500, 12300", ``[4500, 12300]``)
    y nombres de lugares conocidos ("LZ Norte", "obj_aldea", "Alfa-1"). Devuelve ``None``
    si no se reconoce nada.
    """
    if valor is None:
        return None
    if isinstance(valor, Posicion):
        return valor
    if isinstance(valor, (list, tuple, dict)):
        try:
            return Posicion.model_validate(valor)
        except ValueError:
            return None
    texto = str(valor).strip()
    norm = normalizar(texto)
    if norm in _VACIOS:
        return None
    lugares = lugares or {}
    pos = _buscar_lugar(norm, lugares, exacto=True)
    if pos is None and _NUMERO.search(texto):
        pos = _por_numeros(texto, limites)
    if pos is None:
        pos = _buscar_lugar(norm, lugares, exacto=False)
    return pos


def direccion_cardinal(rumbo: float) -> str:
    return _PUNTOS_CARDINALES[int(((rumbo % 360) + 22.5) // 45) % 8]


def describir_relativa(origen: Posicion, destino: Posicion) -> str:
    """Texto del tipo "450 m al NE"."""
    d = origen.distancia(destino)
    if d < 25:
        return "en la misma posición"
    metros = int(round(d, -1)) if d < 1000 else int(round(d, -2))
    return f"{metros} m al {direccion_cardinal(origen.rumbo(destino))}"
