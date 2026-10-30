import asyncio

import pytest

from arma3_ia.geo import Posicion
from arma3_ia.modelo import Accion, EstadoObjetivo, EstadoUnidad, TipoAccion
from arma3_ia.simulador import Escenario, SimuladorTexto, cargar_escenario, escenarios_disponibles
from arma3_ia.simulador.render import mapa_ascii, parte_situacion

ATERRIZAJE_SEGURO = {"velocidad_aproximacion": 10, "tasa_descenso": 1.5, "altura_recogida": 20,
                     "intensidad_recogida": 0.6}


def escenario_mini(**extra) -> Escenario:
    """Mapa vacío con una escuadra, un helicóptero y un objetivo lejano."""
    datos = {
        "id": "mini", "nombre": "Mini", "mapa": {"ancho_m": 4000, "alto_m": 4000},
        "duracion_max_s": 3600, "paso_s": 30, "tiempo_control_s": 60,
        "condiciones": {"viento_ms": 0},
        "zonas_aterrizaje": [{"id": "lz", "nombre": "LZ Lejana", "posicion": [3000, 3000]}],
        "bandos": {
            "blufor": {
                "controlador": "ia", "base": [500, 500],
                "unidades": [
                    {"id": "alfa", "posicion": [1000, 1000]},
                    {"id": "halcon", "tipo": "helicoptero", "posicion": [1000, 1040], "efectivos": 4,
                     "capacidad": 1},
                ],
                "objetivos": [{"id": "obj", "nombre": "Tomar el cruce", "tipo": "capturar",
                               "posicion": [3000, 3000], "radio_m": 200}],
            },
            "opfor": {"controlador": "guion", "unidades": [
                {"id": "centinela", "posicion": [3900, 100], "efectivos": 2},
            ]},
        },
    }
    datos.update(extra)
    return Escenario.model_validate(datos)


def correr(sim: SimuladorTexto, pasos: int) -> None:
    for _ in range(pasos):
        sim.paso()


def test_escenarios_incluidos_cargan_y_arrancan():
    escenarios = escenarios_disponibles()
    assert {"asalto_aldea", "rescate_helicoptero", "defensa_granja", "emboscada_convoy"} <= set(escenarios)
    for escenario in escenarios.values():
        sim = SimuladorTexto(escenario, semilla=1)
        obs = sim.observacion(sim.bando_principal)
        assert obs.unidades and obs.objetivos, escenario.id
    with pytest.raises(KeyError, match="Disponibles"):
        cargar_escenario("no_existe")


def test_simulacion_determinista_con_semilla(escenario):
    def jugar(semilla: int) -> list[str]:
        sim = SimuladorTexto(escenario, semilla=semilla)
        sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.ATACAR, destino=Posicion(x=2600, y=2700)))
        sim.aplicar("blufor", Accion(unidad="bravo", tipo=TipoAccion.MOVER, destino=Posicion(x=1700, y=1800)))
        correr(sim, 60)
        return [f"{e.tiempo}:{e.tipo}:{e.descripcion}" for e in sim.registro]

    assert jugar(7) == jugar(7)


def test_movimiento_y_captura_de_objetivo():
    sim = SimuladorTexto(escenario_mini(), semilla=1)
    assert sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.MOVER,
                                        destino=Posicion(x=3000, y=3000))).aceptada
    correr(sim, 3)
    alfa = sim.unidades["alfa"]
    assert 1000 < alfa.pos.x < 3000 and alfa.pos.distancia(Posicion(x=1000, y=1000)) > 100
    correr(sim, 110)
    assert sim.objetivos["blufor"][0].estado == EstadoObjetivo.COMPLETADO
    resultado = sim.resultado()
    assert resultado is not None and resultado.estado == "victoria" and resultado.puntuacion > 0.8
    assert resultado.metricas["unidades"]["alfa"]["efectivos_finales"] == 8


def test_transporte_en_helicoptero():
    sim = SimuladorTexto(escenario_mini(), semilla=3)
    assert sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.EMBARCAR, objetivo="Halcon")).aceptada
    sim.paso()
    alfa, halcon = sim.unidades["alfa"], sim.unidades["halcon"]
    assert alfa.estado == EstadoUnidad.EMBARCADA and halcon.pasajeros == ["alfa"]
    # Embarcada no se le pueden dar órdenes.
    assert not sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.DEFENDER)).aceptada
    vuelo = Accion(unidad="halcon", tipo=TipoAccion.ATERRIZAR, destino=Posicion(x=3000, y=3000),
                   parametros=ATERRIZAJE_SEGURO)
    assert sim.aplicar("blufor", vuelo).aceptada
    sim.paso()
    assert halcon.en_vuelo
    assert not sim.aplicar("blufor", Accion(unidad="halcon", tipo=TipoAccion.DESEMBARCAR)).aceptada
    for _ in range(20):
        sim.paso()
        if not halcon.en_vuelo:
            break
    assert not halcon.en_vuelo and halcon.pos.distancia(Posicion(x=3000, y=3000)) < 1
    aterrizajes = [e for e in sim.registro if e.tipo == "aterrizaje"]
    assert aterrizajes[-1].datos["parametros"] == ATERRIZAJE_SEGURO
    assert 0 <= aterrizajes[-1].datos["puntuacion"] <= 1 and aterrizajes[-1].datos["zona_caliente"] is False
    assert sim.aplicar("blufor", Accion(unidad="halcon", tipo=TipoAccion.DESEMBARCAR)).aceptada
    assert alfa.estado == EstadoUnidad.ACTIVA and alfa.pos == halcon.pos
    assert halcon.combustible < 1.0


@pytest.mark.parametrize(
    ("accion", "motivo"),
    [
        (Accion(unidad="zulu", tipo=TipoAccion.MOVER, destino=Posicion(x=1, y=1)), "desconocida"),
        (Accion(unidad="alfa", tipo=TipoAccion.VOLAR, destino=Posicion(x=1, y=1)), "no es una acción de tierra"),
        (Accion(unidad="halcon", tipo=TipoAccion.DEFENDER), "no es una acción de aeronave"),
        (Accion(unidad="alfa", tipo=TipoAccion.MOVER), "falta el destino"),
        (Accion(unidad="alfa", tipo=TipoAccion.EMBARCAR, objetivo="alfa"), "helicóptero válido"),
        (Accion(unidad="halcon", tipo=TipoAccion.DESEMBARCAR), "no lleva pasajeros"),
        (Accion(unidad="halcon", tipo=TipoAccion.APOYO_AEREO, destino=Posicion(x=1, y=1)), "no va armada"),
        (Accion(unidad="centinela", tipo=TipoAccion.ESPERAR), "desconocida"),  # unidad del otro bando
    ],
)
def test_acciones_imposibles_se_rechazan_con_motivo(accion, motivo):
    sim = SimuladorTexto(escenario_mini(), semilla=1)
    resultado = sim.aplicar("blufor", accion)
    assert not resultado.aceptada and motivo in resultado.motivo


def test_niebla_de_guerra_no_filtra_identidades(escenario):
    sim = SimuladorTexto(escenario, semilla=2)
    obs = sim.observacion("blufor")
    assert obs.contactos == []  # al inicio no se ve al enemigo
    sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.MOVER, destino=Posicion(x=2600, y=2700)))
    correr(sim, 30)
    obs = sim.observacion("blufor")
    assert obs.contactos and all(c.id.startswith("C") for c in obs.contactos)
    ids_enemigos = {u.id for u in sim.unidades.values() if u.bando == "opfor"}
    texto = obs.model_dump_json()
    assert not any(i in texto for i in ids_enemigos)


def test_objetivo_destruir_no_revela_unidades():
    ataque = escenario_mini()
    ataque.bandos["blufor"].objetivos[0] = ataque.bandos["blufor"].objetivos[0].model_copy(
        update={"tipo": "destruir", "unidades": ["centinela"]}
    )
    sim = SimuladorTexto(ataque, semilla=1)
    assert sim.observacion("blufor").objetivos[0].unidades == []
    sim.unidades["centinela"].estado = EstadoUnidad.DESTRUIDA
    sim.paso()
    assert sim.objetivos["blufor"][0].estado == EstadoObjetivo.COMPLETADO


def test_combate_causa_bajas_y_eventos(escenario):
    sim = SimuladorTexto(escenario, semilla=4)
    sim.aplicar("blufor", Accion(unidad="alfa", tipo=TipoAccion.ATACAR, destino=Posicion(x=2600, y=2700)))
    correr(sim, 40)
    tipos = {e.tipo for e in sim.registro if e.unidad == "alfa"}
    assert "contacto" in tipos and ("bajas" in tipos or "bajo_fuego" in tipos)
    assert sim.unidades["alfa"].efectivos < 8 or sim.unidades["guarnicion"].efectivos < 6


def test_chat_guionizado_y_fin_por_tiempo(escenario):
    sim = SimuladorTexto(escenario, semilla=1)
    assert asyncio.run(sim.recibir_chat("blufor")) == []
    correr(sim, 2)
    chat = asyncio.run(sim.recibir_chat("blufor"))
    assert [m.autor for m in chat] == ["Sargento López"]
    assert asyncio.run(sim.recibir_chat("blufor")) == []
    correr(sim, 200)
    resultado = sim.resultado()
    assert resultado is not None and resultado.motivo == "se agotó el tiempo"
    assert sim.terminado()


def test_render_ascii(escenario, perfil):
    sim = SimuladorTexto(escenario, perfil, semilla=1)
    mapa = mapa_ascii(sim, "blufor")
    assert "@" in mapa and "A" in mapa
    assert "Alfa-1" in parte_situacion(sim, "blufor")
