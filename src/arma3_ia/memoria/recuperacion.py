"""Búsqueda de las lecciones más útiles para la situación actual de un agente.

No necesita embeddings: combina coincidencia de palabras ponderada (estilo TF-IDF),
la utilidad demostrada de cada lección, su peso (cuántas veces se ha confirmado) y
si pertenece a la era de la campaña en curso.
"""

from __future__ import annotations

import math
from collections import Counter

from ..texto import tokens
from .almacen import Leccion, Memoria

RELEVANCIA_MINIMA = 0.15


def _documento(leccion: Leccion) -> set[str]:
    return set(tokens(f"{leccion.situacion} {leccion.leccion} {' '.join(leccion.etiquetas)}"))


def puntuar(
    lecciones: list[Leccion], consulta: str, epoca: str | None = None
) -> list[tuple[float, Leccion]]:
    if not lecciones:
        return []
    documentos = [_documento(leccion) for leccion in lecciones]
    frecuencias = Counter(t for doc in documentos for t in doc)
    total = len(documentos)
    idf = {t: math.log(1 + total / n) for t, n in frecuencias.items()}
    buscado = set(tokens(consulta))
    resultado = []
    for leccion, doc in zip(lecciones, documentos):
        norma = math.sqrt(sum(idf[t] for t in doc)) or 1.0
        relevancia = sum(idf[t] for t in buscado & doc) / norma
        bonus_epoca = 1.15 if epoca and leccion.epoca == epoca else 1.0
        puntuacion = (relevancia + RELEVANCIA_MINIMA) * (0.5 + leccion.utilidad) * leccion.peso * bonus_epoca
        resultado.append((puntuacion, leccion))
    resultado.sort(key=lambda par: par[0], reverse=True)
    return resultado


class Recuperador:
    def __init__(self, memoria: Memoria) -> None:
        self.memoria = memoria

    def buscar(self, rol: str, epoca: str | None, consulta: str, k: int = 4) -> list[Leccion]:
        if k <= 0:
            return []
        candidatas = self.memoria.lecciones(rol=rol, epoca=epoca)
        return [leccion for _, leccion in puntuar(candidatas, consulta, epoca)[:k]]
