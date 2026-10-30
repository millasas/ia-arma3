"""Utilidades de texto en español: normalización, tokens y similitud.

Se usan para reconocer indicativos y lugares escritos por el modelo ("Halcón-1", "halcon 1")
y para buscar lecciones relevantes en la memoria sin necesitar embeddings.
"""

from __future__ import annotations

import re
import unicodedata

_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")

STOPWORDS = frozenset(
    [
        "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos", "ante", "antes", "aqui",
        "asi", "aun", "cada", "como", "con", "contra", "cual", "cuando", "de", "del", "desde", "donde",
        "dos", "durante", "e", "el", "ella", "ellas", "ello", "ellos", "en", "entre", "era", "eran", "es",
        "esa", "esas", "ese", "eso", "esos", "esta", "estaba", "estado", "estan", "estar", "estas", "este",
        "esto", "estos", "fue", "fueron", "ha", "habia", "han", "hasta", "hay", "la", "las", "le", "les",
        "lo", "los", "mas", "me", "mi", "mientras", "muy", "nada", "ni", "no", "nos", "nosotros", "o",
        "otra", "otras", "otro", "otros", "para", "pero", "poco", "por", "porque", "que", "quien", "se",
        "ser", "si", "sin", "sobre", "solo", "son", "su", "sus", "tambien", "tan", "tanto", "te", "tiene",
        "tienen", "todo", "todos", "tras", "tu", "un", "una", "unas", "uno", "unos", "usar", "y", "ya"
    ]
)


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con cualquier signo convertido en un espacio."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return _NO_ALFANUMERICO.sub(" ", sin_tildes).strip()


def raiz(palabra: str) -> str:
    """Raíz aproximada: quita el plural y recorta a 6 letras ("aterrizajes" -> "aterri")."""
    if palabra.isdigit():
        return palabra
    if len(palabra) > 5 and palabra.endswith("es"):
        palabra = palabra[:-2]
    elif len(palabra) > 3 and palabra.endswith("s"):
        palabra = palabra[:-1]
    return palabra[:6]


def tokens(texto: str) -> list[str]:
    """Palabras significativas del texto, ya normalizadas y reducidas a su raíz."""
    return [
        raiz(p)
        for p in normalizar(texto).split()
        if p not in STOPWORDS and (len(p) > 2 or p.isdigit())
    ]


def similitud(a: str, b: str) -> float:
    """Similitud de Jaccard (0..1) entre los conjuntos de raíces de dos textos."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def reloj(segundos: float) -> str:
    """Tiempo de misión como "mm:ss" (o "h:mm:ss" a partir de una hora)."""
    total = max(0, int(segundos))
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{segs:02d}" if horas else f"{minutos:02d}:{segs:02d}"


def recortar(texto: str, maximo: int) -> str:
    """Recorta un texto a `maximo` caracteres añadiendo "…" si hace falta."""
    texto = " ".join(texto.split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"
