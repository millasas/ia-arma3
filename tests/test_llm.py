import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from arma3_ia.agentes import AccionAire, AccionTierra, DecisionComandante, DecisionLider, DecisionPiloto, TareaMando
from arma3_ia.config import ConfigLLM
from arma3_ia.llm import (
    ClienteOllama,
    ErrorConexionLLM,
    ErrorLLM,
    ErrorRespuestaLLM,
    LLMFalso,
    MensajeLLM,
    crear_cliente,
    esquema_para_llm,
    extraer_json,
    generar_estructurado,
    quitar_pensamiento,
)

MENSAJES = [MensajeLLM("system", "Eres un test."), MensajeLLM("user", "Hola")]


# ------------------------------------------------------------------ JSON
def test_quitar_pensamiento():
    assert quitar_pensamiento("<think>bla bla</think>\n{\"a\": 1}") == '{"a": 1}'
    assert quitar_pensamiento('{"a": 1} <think>truncado') == '{"a": 1}'


@pytest.mark.parametrize(
    "texto",
    [
        '{"a": 1, "b": [1, 2]}',
        'Claro, aquí tienes:\n```json\n{"a": 1, "b": [1, 2]}\n```',
        'Respuesta: {"a": 1, "b": [1, 2],} y nada más',
        '<think>Pienso {mucho}</think>{"a": 1, "b": [1, 2]}',
    ],
)
def test_extraer_json_tolerante(texto):
    assert extraer_json(texto) == {"a": 1, "b": [1, 2]}


def test_extraer_json_respeta_llaves_dentro_de_cadenas():
    assert extraer_json('texto {"t": "una } llave", "n": 2} fin') == {"t": "una } llave", "n": 2}


def test_extraer_json_falla_si_no_hay_json():
    with pytest.raises(ValueError):
        extraer_json("Lo siento, no puedo.")


def test_esquema_sin_referencias():
    esquema = esquema_para_llm(DecisionComandante)
    texto = json.dumps(esquema)
    assert "$ref" not in texto and "$defs" not in texto
    orden = esquema["properties"]["ordenes"]["items"]
    assert set(orden["properties"]["tarea"]["enum"]) == {t.value for t in TareaMando}
    assert esquema["properties"]["ordenes"]["maxItems"] == 8


# ------------------------------------------------------------------ esquemas tolerantes
def test_esquemas_aceptan_variantes_del_modelo():
    d = DecisionLider.model_validate({"evaluacion": "x", "accion": "Atacar", "postura": "Sigilo"})
    assert d.accion == AccionTierra.ATACAR and d.postura.value == "sigilo"
    p = DecisionPiloto.model_validate({"evaluacion": "x", "accion": "RTB", "altitud": "muy alta"})
    assert p.accion == AccionAire.REGRESAR_BASE and p.altitud is None
    c = DecisionComandante.model_validate({
        "evaluacion": "x" * 900,
        "intencion": "y",
        "ordenes": [{"unidad": "Alfa-1", "tarea": "apoyo aéreo", "destino": ["045", "123"]}],
    })
    assert c.ordenes[0].tarea == TareaMando.APOYO_AEREO
    assert c.ordenes[0].destino == "045, 123"
    assert len(c.evaluacion) <= 400


# ------------------------------------------------------------------ generar_estructurado
class Simple(BaseModel):
    respuesta: str
    numero: int


def test_generar_estructurado_corrige_respuestas_invalidas():
    llm = LLMFalso(["esto no es json", {"respuesta": "ok"}, {"respuesta": "ok", "numero": 3}])
    resultado, _ = asyncio.run(generar_estructurado(llm, MENSAJES, Simple, reintentos=2))
    assert resultado == Simple(respuesta="ok", numero=3)
    assert len(llm.llamadas) == 3
    # El segundo intento incluye el error para que el modelo lo corrija.
    assert "no válida" in llm.llamadas[1].usuario
    assert llm.llamadas[0].esquema["required"] == ["respuesta", "numero"]


def test_generar_estructurado_se_rinde():
    llm = LLMFalso(["mal", "mal"])
    with pytest.raises(ErrorRespuestaLLM):
        asyncio.run(generar_estructurado(llm, MENSAJES, Simple, reintentos=1))


def test_llm_falso_tactico_usa_el_fallback():
    llm = LLMFalso()
    resultado, _ = asyncio.run(
        generar_estructurado(llm, MENSAJES, Simple, metadatos={"fallback": lambda: Simple(respuesta="r", numero=1)})
    )
    assert resultado.numero == 1
    assert llm.estadisticas["llamadas"] == 1


# ------------------------------------------------------------------ Ollama (transporte simulado)
def _cliente(manejador, **kwargs) -> ClienteOllama:
    return ClienteOllama("http://ollama.test", "qwen3:4b", transporte=httpx.MockTransport(manejador),
                         espera_reintento_s=0, **kwargs)


def _respuesta_chat(contenido: str) -> httpx.Response:
    return httpx.Response(200, json={
        "model": "qwen3:4b", "message": {"role": "assistant", "content": contenido},
        "done": True, "prompt_eval_count": 50, "eval_count": 10,
    })


def test_ollama_envia_el_formato_correcto():
    peticiones = []

    def manejador(request: httpx.Request) -> httpx.Response:
        peticiones.append(json.loads(request.content))
        return _respuesta_chat('<think>razonando</think>{"respuesta": "ok", "numero": 7}')

    async def probar():
        cliente = _cliente(manejador, num_ctx=4096, keep_alive="10m")
        resultado, bruta = await generar_estructurado(cliente, MENSAJES, Simple)
        await cliente.cerrar()
        return resultado, bruta, cliente

    resultado, bruta, cliente = asyncio.run(probar())
    assert resultado.numero == 7
    p = peticiones[0]
    assert p["model"] == "qwen3:4b" and p["stream"] is False
    assert p["messages"][0] == {"role": "system", "content": "Eres un test."}
    assert p["format"]["type"] == "object"
    assert p["options"]["num_ctx"] == 4096
    assert p["think"] is False and p["keep_alive"] == "10m"
    assert bruta.tokens_entrada == 50 and cliente.estadisticas["tokens_salida"] == 10


def test_ollama_reintenta_sin_think_si_el_modelo_no_lo_admite():
    peticiones = []

    def manejador(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        peticiones.append(cuerpo)
        if "think" in cuerpo:
            return httpx.Response(400, json={"error": '"llama3.1:8b" does not support thinking'})
        return _respuesta_chat('{"respuesta": "ok", "numero": 1}')

    async def probar():
        cliente = _cliente(manejador)
        await cliente.chat(MENSAJES)
        await cliente.chat(MENSAJES)
        await cliente.cerrar()

    asyncio.run(probar())
    assert ["think" in p for p in peticiones] == [True, False, False]


def test_ollama_modelo_no_descargado():
    def manejador(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found, try pulling it first"})

    with pytest.raises(ErrorLLM, match="ollama pull qwen3:4b"):
        asyncio.run(_cliente(manejador).chat(MENSAJES))


def test_ollama_sin_conexion_reintenta_y_falla():
    intentos = []

    def manejador(request: httpx.Request) -> httpx.Response:
        intentos.append(1)
        raise httpx.ConnectError("conexión rechazada")

    with pytest.raises(ErrorConexionLLM, match="systemctl status ollama"):
        asyncio.run(_cliente(manejador, reintentos_conexion=2).chat(MENSAJES))
    assert len(intentos) == 3


def test_ollama_error_500_se_reintenta():
    respuestas = [httpx.Response(500, text="fallo"), _respuesta_chat('{"respuesta": "ok", "numero": 2}')]

    def manejador(request: httpx.Request) -> httpx.Response:
        return respuestas.pop(0)

    resultado = asyncio.run(_cliente(manejador).chat(MENSAJES))
    assert json.loads(resultado.texto)["numero"] == 2


def test_ollama_contra_servidor_http_real(servidor_ollama):
    async def probar():
        cliente = ClienteOllama(servidor_ollama.url, "qwen3:4b")
        informe = await cliente.comprobar()
        resultado, _ = await generar_estructurado(cliente, MENSAJES, DecisionLider)
        await cliente.cerrar()
        return informe, resultado

    informe, resultado = asyncio.run(probar())
    assert informe.conectado and informe.modelo_disponible and informe.version == "0.12.0"
    assert resultado.accion == AccionTierra.MOVER
    assert servidor_ollama.peticiones[0]["format"]["properties"]["accion"]["enum"][0] == "mover"


def test_comprobar_sin_servidor():
    informe = asyncio.run(ClienteOllama("http://127.0.0.1:9", "qwen3:4b").comprobar())
    assert not informe.conectado and informe.error


def test_crear_cliente():
    assert isinstance(crear_cliente(ConfigLLM(proveedor="falso")), LLMFalso)
    cliente = crear_cliente(ConfigLLM(modelo="llama3.1:8b", pensar=None))
    assert isinstance(cliente, ClienteOllama) and cliente.nombre_modelo == "llama3.1:8b"
    assert "think" not in cliente._payload(MENSAJES, None, None, None)
    asyncio.run(cliente.cerrar())
