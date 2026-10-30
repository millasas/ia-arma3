from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from arma3_ia.campanas import PerfilCampana, cargar_perfil
from arma3_ia.geo import Posicion
from arma3_ia.memoria import Memoria
from arma3_ia.modelo import (
    Contacto,
    Mapa,
    Objetivo,
    Observacion,
    TipoObjetivo,
    TipoUnidad,
    UnidadPropia,
    ZonaAterrizaje,
)
from arma3_ia.simulador import Escenario, cargar_escenario


@pytest.fixture
def perfil() -> PerfilCampana:
    return cargar_perfil("moderno")


@pytest.fixture
def escenario() -> Escenario:
    return cargar_escenario("asalto_aldea")


@pytest.fixture
def memoria() -> Iterator[Memoria]:
    m = Memoria(":memory:")
    yield m
    m.cerrar()


def unidad(id_: str, indicativo: str, x: float, y: float, **extra: Any) -> UnidadPropia:
    datos: dict[str, Any] = {
        "id": id_,
        "indicativo": indicativo,
        "tipo": TipoUnidad.INFANTERIA,
        "posicion": Posicion(x=x, y=y),
        "efectivos": 8,
        "efectivos_max": 8,
    }
    datos.update(extra)
    return UnidadPropia(**datos)


@pytest.fixture
def obs() -> Observacion:
    """Situación pequeña y conocida: dos escuadras, un helicóptero, un contacto y una aldea."""
    return Observacion(
        tiempo=120.0,
        bando="blufor",
        mapa=Mapa(ancho_m=4000, alto_m=4000),
        unidades=[
            unidad("alfa", "Alfa-1", 900, 800),
            unidad("bravo", "Bravo-1", 700, 1000),
            unidad("halcon", "Halcón-1", 500, 500, tipo=TipoUnidad.HELICOPTERO, efectivos=4,
                   efectivos_max=4, capacidad=1),
        ],
        contactos=[Contacto(id="C1", tipo=TipoUnidad.INFANTERIA, posicion=Posicion(x=2600, y=2700),
                            efectivos_estimados=6)],
        objetivos=[Objetivo(id="obj_aldea", nombre="Tomar la aldea", tipo=TipoObjetivo.CAPTURAR,
                            posicion=Posicion(x=2600, y=2700), radio_m=250)],
        zonas_aterrizaje=[ZonaAterrizaje(id="lz_norte", nombre="LZ Norte", posicion=Posicion(x=2700, y=3500))],
        base=Posicion(x=500, y=500),
    )


def instancia_minima(esquema: dict[str, Any]) -> Any:
    """Genera un valor mínimo que cumple un JSON Schema (para el servidor Ollama falso)."""
    if "enum" in esquema:
        return esquema["enum"][0]
    if "const" in esquema:
        return esquema["const"]
    if "anyOf" in esquema:
        opciones = [o for o in esquema["anyOf"] if o.get("type") != "null"] or esquema["anyOf"]
        return instancia_minima(opciones[0])
    tipo = esquema.get("type")
    if tipo == "object":
        return {
            k: instancia_minima(v)
            for k, v in esquema.get("properties", {}).items()
            if k in esquema.get("required", [])
        }
    if tipo == "array":
        return []
    if tipo == "string":
        return "Recibido."
    if tipo in ("number", "integer"):
        return 0
    if tipo == "boolean":
        return False
    return None


class ServidorOllamaFalso:
    """Servidor HTTP real que imita la API de Ollama (/api/chat, /api/tags, /api/version)."""

    def __init__(self, modelo: str = "qwen3:8b") -> None:
        self.modelo = modelo
        self.peticiones: list[dict[str, Any]] = []
        self.respuestas: list[str] = []
        servidor = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return None

            def _responder(self, codigo: int, cuerpo: dict[str, Any]) -> None:
                datos = json.dumps(cuerpo).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            def do_GET(self) -> None:
                if self.path == "/api/version":
                    self._responder(200, {"version": "0.12.0"})
                elif self.path == "/api/tags":
                    self._responder(200, {"models": [{"name": servidor.modelo}]})
                else:
                    self._responder(404, {"error": "not found"})

            def do_POST(self) -> None:
                largo = int(self.headers.get("Content-Length", 0))
                peticion = json.loads(self.rfile.read(largo))
                servidor.peticiones.append(peticion)
                if peticion.get("model") != servidor.modelo:
                    self._responder(404, {"error": f"model '{peticion.get('model')}' not found"})
                    return
                if servidor.respuestas:
                    contenido = servidor.respuestas.pop(0)
                else:
                    contenido = json.dumps(instancia_minima(peticion.get("format") or {"type": "object"}))
                self._responder(200, {
                    "model": servidor.modelo,
                    "message": {"role": "assistant", "content": contenido},
                    "done": True,
                    "prompt_eval_count": 100,
                    "eval_count": 20,
                })

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        self._hilo = threading.Thread(target=self._http.serve_forever, daemon=True)

    def __enter__(self) -> ServidorOllamaFalso:
        self._hilo.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._http.shutdown()
        self._http.server_close()


@pytest.fixture
def servidor_ollama() -> Iterator[ServidorOllamaFalso]:
    with ServidorOllamaFalso() as servidor:
        yield servidor
