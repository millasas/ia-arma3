"""Extracción robusta de JSON de las respuestas de modelos pequeños."""

from __future__ import annotations

import json
import re
from typing import Any

_PENSAMIENTO = re.compile(r"<think>.*?</think>", re.S | re.I)
_BLOQUE_CODIGO = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_COMA_FINAL = re.compile(r",\s*([}\]])")


def quitar_pensamiento(texto: str) -> str:
    """Elimina los bloques <think>...</think> de los modelos con razonamiento (qwen3, deepseek-r1)."""
    texto = _PENSAMIENTO.sub("", texto)
    abierto = texto.lower().find("<think>")
    if abierto != -1:
        # Bloque sin cerrar (respuesta truncada): todo lo que sigue es razonamiento.
        texto = texto[:abierto]
    return texto.strip()


def _primer_objeto(texto: str) -> str | None:
    """Devuelve el primer objeto o lista JSON equilibrado, respetando las cadenas."""
    inicio = next((i for i, c in enumerate(texto) if c in "{["), None)
    if inicio is None:
        return None
    pila: list[str] = []
    en_cadena = False
    escape = False
    for i in range(inicio, len(texto)):
        c = texto[i]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c in "{[":
            pila.append("}" if c == "{" else "]")
        elif c in "}]":
            if not pila or pila.pop() != c:
                return None
            if not pila:
                return texto[inicio : i + 1]
    return None


def _reparar(candidato: str) -> str:
    reparado = _COMA_FINAL.sub(r"\1", candidato)
    reparado = re.sub(r"\bTrue\b", "true", reparado)
    reparado = re.sub(r"\bFalse\b", "false", reparado)
    return re.sub(r"\bNone\b", "null", reparado)


def extraer_json(texto: str) -> Any:
    """Obtiene el JSON de una respuesta aunque venga con texto, ``` o comas sobrantes."""
    limpio = quitar_pensamiento(texto)
    candidatos = [limpio]
    candidatos += [m.group(1).strip() for m in _BLOQUE_CODIGO.finditer(limpio)]
    objeto = _primer_objeto(limpio)
    if objeto:
        candidatos.append(objeto)
    for candidato in candidatos:
        for version in (candidato, _reparar(candidato)):
            try:
                return json.loads(version)
            except (json.JSONDecodeError, ValueError):
                continue
    raise ValueError("la respuesta no contiene JSON válido")
