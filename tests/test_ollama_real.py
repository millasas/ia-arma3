"""Prueba opcional contra un Ollama de verdad (en el PC de la IA).

Se salta si no hay servidor. Para lanzarla: ``pytest -m ollama`` (usa ARMA3IA_LLM_URL y
ARMA3IA_LLM_MODELO si están definidas).
"""

import asyncio
import os

import pytest

from arma3_ia.agentes import DecisionComandante, reglas
from arma3_ia.agentes.prompts import sistema_comandante, usuario_comandante
from arma3_ia.llm import ClienteOllama, MensajeLLM, generar_estructurado

pytestmark = pytest.mark.ollama

URL = os.environ.get("ARMA3IA_LLM_URL", "http://localhost:11434")
MODELO = os.environ.get("ARMA3IA_LLM_MODELO", "qwen3:4b")


def test_el_modelo_real_da_ordenes_validas(obs, perfil):
    async def probar():
        cliente = ClienteOllama(URL, MODELO, timeout_s=300)
        try:
            informe = await cliente.comprobar()
            if not (informe.conectado and informe.modelo_disponible):
                pytest.skip(f"Ollama o {MODELO} no disponible en {URL}")
            mensajes = [MensajeLLM("system", sistema_comandante("Mando", perfil)),
                        MensajeLLM("user", usuario_comandante(obs, [], "", []))]
            decision, _ = await generar_estructurado(cliente, mensajes, DecisionComandante, reintentos=2)
            return decision
        finally:
            await cliente.cerrar()

    decision = asyncio.run(probar())
    assert decision.ordenes, "el comandante debe dar al menos una orden"
    validas = {u.indicativo.lower() for u in obs.unidades}
    assert any(o.unidad.lower() in validas for o in decision.ordenes)
    assert reglas.decision_comandante(obs, perfil).ordenes  # referencia
