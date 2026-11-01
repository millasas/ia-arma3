from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from arma3_ia.agentes.esquemas import OrdenMando, TareaMando
from arma3_ia.ambientacion import InfoBando, PerfilCampana
from arma3_ia.geo import Posicion
from arma3_ia.memoria import Memoria
from arma3_ia.modelo import (
    Condiciones,
    Contacto,
    Mapa,
    Objetivo,
    Observacion,
    Referencia,
    TipoObjetivo,
    TipoUnidad,
    UnidadPropia,
    ZonaAterrizaje,
)
from arma3_ia.simulador import BandoEscenario, Escenario, UnidadEscenario


def P(x: float, y: float) -> Posicion:
    return Posicion(x=x, y=y)


def perfil_moderno() -> PerfilCampana:
    """Una ambientación de prueba (en el programa las crea Zeus para cada campaña)."""
    return PerfilCampana(
        id="moderna", nombre="Prueba moderna", epoca="Actualidad",
        descripcion="Fuerza conjunta moderna con radios cifradas y helicópteros.",
        bando_ia=InfoBando(nombre="Fuerza de Tarea Aliada", descripcion="Infantería ligera con aviación."),
        enemigo=InfoBando(nombre="Fuerzas hostiles", descripcion="Infantería y vehículos en posiciones preparadas.",
                          doctrina=["Defensa en profundidad."], indicativo_mando="Fortaleza",
                          indicativos={"infanteria": ["Rojo-1", "Rojo-2", "Rojo-3", "Rojo-4"],
                                       "motorizada": ["Ariete-1"]}),
        estilo_comunicacion="Procedimiento de radio militar en español: frases breves con indicativos.",
        doctrina=["Reconocer antes de actuar.", "Fijar y flanquear."],
        reglas_enfrentamiento=["Identificación positiva antes de abrir fuego."],
        personalidad_mando="Metódico y profesional.",
        medios_aereos=True, retardo_radio_s=0, indicativo_mando="Mando", indicativo_enlace="Mando-Radio",
        indicativos={"infanteria": ["Alfa-1", "Bravo-1", "Charlie-1"], "helicoptero": ["Halcón-1", "Búho-1"]},
        vocabulario=["cambio", "recibido"],
    )


def escenario_prueba() -> Escenario:
    """Una aldea defendida con un bosque peligroso en medio (como las que prepara Zeus)."""
    def orden(ind: str, tarea: TareaMando, destino: str | None = None, ruta: list[str] | None = None) -> OrdenMando:
        return OrdenMando(unidad=ind, tarea=tarea, destino=destino, ruta=ruta or [])

    return Escenario(
        id="prueba_aldea", nombre="Asalto a Aldea Vieja", briefing="Tomar la aldea.", terreno="Altis",
        zona="Aldea Vieja", mapa=Mapa(nombre="Valle de prueba", ancho_m=4000, alto_m=4000), duracion_max_s=3600,
        condiciones=Condiciones(hora="dia", clima="despejado", visibilidad_m=1500, viento_ms=4),
        referencias=[
            Referencia(nombre="Aldea Vieja", tipo="urbano", posicion=P(2600, 2700), radio_m=250),
            Referencia(nombre="Bosque Negro", tipo="bosque", posicion=P(1700, 1800), radio_m=400),
            Referencia(nombre="Colina 214", tipo="colina", posicion=P(2000, 2900), radio_m=250),
        ],
        zonas_aterrizaje=[ZonaAterrizaje(id="lz_norte", nombre="LZ Norte", posicion=P(2700, 3500)),
                          ZonaAterrizaje(id="lz_este", nombre="LZ Este", posicion=P(3400, 2100))],
        bandos={
            "blufor": BandoEscenario(nombre="Fuerza de Tarea Aliada", base=P(500, 500), unidades=[
                UnidadEscenario(id="alfa", indicativo="Alfa-1", posicion=P(900, 800)),
                UnidadEscenario(id="bravo", indicativo="Bravo-1", posicion=P(700, 1000)),
                UnidadEscenario(id="halcon", indicativo="Halcón-1", tipo=TipoUnidad.HELICOPTERO,
                                posicion=P(500, 500), efectivos=4, capacidad=1),
                UnidadEscenario(id="buho", indicativo="Búho-1", tipo=TipoUnidad.HELICOPTERO,
                                posicion=P(550, 450), efectivos=2, armada=True),
            ], objetivos=[
                Objetivo(id="obj_aldea", nombre="Tomar Aldea Vieja", tipo=TipoObjetivo.CAPTURAR,
                         posicion=P(2600, 2700), radio_m=250),
                Objetivo(id="obj_colina", nombre="Reconocer la Colina 214", tipo=TipoObjetivo.RECONOCER,
                         posicion=P(2000, 2900), radio_m=250, prioridad=2, obligatorio=False),
            ]),
            "opfor": BandoEscenario(nombre="Fuerzas hostiles", mando=False, unidades=[
                UnidadEscenario(id="guarnicion", indicativo="Rojo-1", posicion=P(2600, 2700), efectivos=6,
                                orden=orden("Rojo-1", TareaMando.DEFENDER, "026027")),
                UnidadEscenario(id="patrulla_norte", indicativo="Rojo-2", posicion=P(2700, 3300), efectivos=4,
                                orden=orden("Rojo-2", TareaMando.PATRULLAR, ruta=["027033", "032029", "027028"])),
                UnidadEscenario(id="emboscada_bosque", indicativo="Rojo-3", posicion=P(1650, 1750), efectivos=4,
                                orden=orden("Rojo-3", TareaMando.EMBOSCAR, "016017")),
                UnidadEscenario(id="qrf_motorizada", indicativo="Ariete-1", tipo=TipoUnidad.MOTORIZADA,
                                posicion=P(3600, 3600), efectivos=4, orden=orden("Ariete-1", TareaMando.ESPERAR)),
            ]),
        },
        bando_jugadores="blufor",
    )


@pytest.fixture
def perfil() -> PerfilCampana:
    return perfil_moderno()


@pytest.fixture
def escenario() -> Escenario:
    return escenario_prueba()


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

    def __init__(self, modelo: str = "qwen3:4b") -> None:
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
                elif self.path == "/api/ps":
                    self._responder(200, {"models": [{"name": servidor.modelo, "model": servidor.modelo,
                                                      "size": 4000, "size_vram": 3000}]})
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


def construir_mision(tipo: Any, perfil: PerfilCampana, *, semilla: int = 1, duracion: int = 60,
                     dificultad: float = 1.0) -> Any:
    """Una misión preparada por Zeus con sus reglas (sin LLM): devuelve MisionConstruida."""
    from arma3_ia.zeus import reglas
    from arma3_ia.zeus.construir import construir_escenario

    rng = reglas.rng_de("prueba", semilla)
    capitulo = reglas.capitulo_por_reglas(tipo, "Kavala", rng)
    personajes = [reglas.crear_persona(rng, perfil.id, "jugadores", set()) for _ in range(2)]
    plan = reglas.mision_por_reglas(perfil, capitulo, nombre_campana="Prueba", terreno="Altis",
                                    personajes=personajes, antagonista=None, dificultad=dificultad,
                                    duracion_min=duracion, semilla=semilla)
    return construir_escenario(plan, perfil, duracion_min=duracion, terrenos=["Altis"], semilla=semilla)
