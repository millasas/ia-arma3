"""Pruebas de extremo a extremo: misiones completas que prepara y dirige Zeus, con el LLM falso."""

import asyncio
import json

import pytest

from arma3_ia.config import Config
from arma3_ia.habilidades import HabilidadAterrizaje, Habilidades
from arma3_ia.llm import LLMFalso
from arma3_ia.llm.falso import responder_tactico
from arma3_ia.mando import CerebroBando
from arma3_ia.memoria import Leccion, Reflexion
from arma3_ia.modelo import TipoAccion
from arma3_ia.partida import Partida, preparar_cerebros
from arma3_ia.simulador import SimuladorTexto
from arma3_ia.zeus import DirectorPartida, TipoMision, Zeus
from conftest import construir_mision, escenario_prueba


def jugar(escenario, perfil, memoria, llm=None, *, semilla=1, sucesos=None, explorar=False, max_pasos=None,
          al_paso=None, zeus=True):
    llm = llm or LLMFalso()
    habilidades = Habilidades(memoria, explorar=explorar, semilla=semilla)
    sim = SimuladorTexto(escenario, semilla=semilla)
    cerebros = preparar_cerebros(escenario, llm, perfil, Config(), memoria, habilidades)
    director = DirectorPartida(Zeus(llm, memoria, semilla=semilla), sim, cerebros, escenario, perfil,
                               sucesos=sucesos, semilla=semilla) if zeus else None
    partida = Partida(sim, cerebros, memoria=memoria, reflexion=Reflexion(llm, memoria, perfil),
                      habilidades=habilidades, escenario=escenario, max_pasos=max_pasos, al_paso=al_paso,
                      director=director)
    return asyncio.run(partida.jugar()), sim, llm


@pytest.mark.parametrize("tipo", list(TipoMision))
def test_toda_mision_de_zeus_se_juega_entera_sin_gpu(tipo, perfil, memoria):
    mision = construir_mision(tipo, perfil)
    informe, sim, _llm = jugar(mision.escenario, perfil, memoria, sucesos=mision.sucesos)
    assert sim.terminado()
    assert informe.resultado.estado in ("victoria", "parcial", "derrota")
    assert informe.pasos > 3 and informe.decisiones > 3
    assert informe.estadisticas.get("llm", 0) > 0 and informe.estadisticas.get("errores_llm", 0) == 0
    # Todo queda registrado: la misión, las decisiones y la radio.
    fila = memoria.mision(informe.mision_id)
    assert fila["estado"] == informe.resultado.estado and fila["terreno"] == "Altis"
    assert fila["epoca"] == "moderna"
    assert memoria.decisiones(informe.mision_id)
    if mision.escenario.bandos["blufor"].mando:
        assert memoria.eventos(informe.mision_id, tipos=["radio"])
    assert informe.resumen
    # Los dos bandos los llevan NPC con su propio cerebro.
    assert set(sim.bandos) == {"blufor", "opfor"}


def test_las_misiones_de_zeus_son_ganables(perfil, memoria):
    ganadas = 0
    for tipo in (TipoMision.ASALTO, TipoMision.DEFENSA, TipoMision.RESCATE, TipoMision.RECONOCIMIENTO):
        mision = construir_mision(tipo, perfil)
        informe, _, _ = jugar(mision.escenario, perfil, memoria, sucesos=mision.sucesos)
        ganadas += informe.resultado.estado == "victoria"
    assert ganadas >= 3


def test_mas_dificultad_lo_pone_mas_dificil(perfil, memoria):
    facil = construir_mision(TipoMision.ASALTO, perfil, dificultad=0.8)
    dura = construir_mision(TipoMision.ASALTO, perfil, dificultad=1.6)
    enemigos = [sum(u.efectivos for u in m.escenario.bandos["opfor"].unidades) for m in (facil, dura)]
    assert enemigos[1] > enemigos[0] * 1.5
    r_facil, _, _ = jugar(facil.escenario, perfil, memoria)
    r_dura, _, _ = jugar(dura.escenario, perfil, memoria)
    assert r_facil.resultado.puntuacion > r_dura.resultado.puntuacion


def test_los_npc_contestan_a_los_jugadores_por_radio(perfil, memoria):
    escenario = escenario_prueba()
    _informe, sim, _ = jugar(escenario, perfil, memoria, max_pasos=4, zeus=False)
    sim.inyectar_chat("blufor", "Sargento López", "Mando, aquí López. ¿Cuál es el plan para la aldea?")
    llm = LLMFalso()
    cerebro = CerebroBando("blufor", llm, perfil)
    respuestas, _ = asyncio.run(cerebro.enlace.atender(asyncio.run(sim.recibir_chat("blufor")),
                                                       sim.observacion("blufor")))
    assert respuestas and respuestas[0].es_ia


def test_aprende_de_una_mision_y_lo_usa_en_la_siguiente(perfil, memoria):
    """El bucle de aprendizaje: reflexión → lección guardada → aparece en el prompt de la siguiente."""
    propuesta = {
        "resumen": "Nos emboscaron en el bosque.",
        "lecciones": [{"situacion": "Avance de infantería hacia la aldea por el bosque",
                       "leccion": "Rodear el Bosque Negro por el norte en vez de cruzarlo", "tipo": "error",
                       "alcance": "epoca", "etiquetas": ["bosque", "emboscada", "infanteria"]}],
    }

    def responder(mensajes, esquema, metadatos):
        if metadatos.get("rol") == "reflexion":
            return json.dumps(propuesta if metadatos.get("agente") == "lider" else {"resumen": "ok", "lecciones": []})
        return responder_tactico(mensajes, esquema, metadatos)

    informe1, _, _ = jugar(escenario_prueba(), perfil, memoria, LLMFalso(responder=responder), zeus=False)
    nuevas = [l.leccion for l in informe1.lecciones_nuevas]  # noqa: E741
    assert "Rodear el Bosque Negro por el norte en vez de cruzarlo" in nuevas
    guardada = next(l for l in memoria.lecciones(rol="lider") if l.leccion.startswith("Rodear"))  # noqa: E741
    assert guardada.epoca == "moderna"

    llm2 = LLMFalso(responder=responder)
    informe2, _, _ = jugar(escenario_prueba(), perfil, memoria, llm2, semilla=2, zeus=False)
    assert any("Rodear el Bosque Negro" in ll.sistema for ll in llm2.llamadas_de("lider"))
    assert memoria.leccion(guardada.id).veces_usada == 1
    # La misma lección propuesta otra vez se refuerza, no se duplica.
    assert any(l.id == guardada.id for l in informe2.lecciones_reforzadas)  # noqa: E741
    assert sum(l.leccion.startswith("Rodear") for l in memoria.lecciones(rol="lider")) == 1  # noqa: E741


def test_las_lecciones_de_otra_epoca_no_se_mezclan(perfil, memoria):
    memoria.anadir_leccion(Leccion(rol="lider", situacion="Granja en 1944", leccion="Cavar trincheras en el seto",
                                   epoca="segunda_guerra_mundial", etiquetas=["infanteria"]))
    _, _, llm = jugar(escenario_prueba(), perfil, memoria, max_pasos=3, zeus=False)
    assert not any("trincheras" in ll.sistema for ll in llm.llamadas_de("lider"))


def test_retardo_de_radio_en_1944(perfil, memoria):
    ww2 = perfil.model_copy(update={"id": "segunda_guerra_mundial", "retardo_radio_s": 30, "medios_aereos": False})
    mision = construir_mision(TipoMision.DEFENSA, ww2)
    assert not any(u.tipo.value == "helicoptero" for b in mision.escenario.bandos.values() for u in b.unidades)
    sim = SimuladorTexto(mision.escenario, semilla=1)
    cerebro = CerebroBando("blufor", LLMFalso(), ww2, con_mando=True)
    asyncio.run(cerebro.paso(sim.observacion("blufor")))
    assert cerebro.radio.retardo_s == 30
    assert all(a.orden is None for a in cerebro.unidades.values())  # las órdenes aún "viajan"
    sim.paso()
    asyncio.run(cerebro.paso(sim.observacion("blufor")))
    assert any(a.orden is not None for a in cerebro.unidades.values())


def test_los_helicopteros_vuelan_con_lo_aprendido_y_aprenden_al_aterrizar(perfil, memoria):
    HabilidadAterrizaje(memoria).entrenar(160)
    acciones = []

    def al_paso(_paso, salidas):
        acciones.extend(a for s in salidas.values() for a in s.acciones
                        if a.tipo in (TipoAccion.ATERRIZAR, TipoAccion.REGRESAR_BASE))

    mision = construir_mision(TipoMision.RESCATE, perfil)
    _, sim, _ = jugar(mision.escenario, perfil, memoria, sucesos=mision.sucesos, explorar=True, al_paso=al_paso)
    assert acciones and all(a.parametros for a in acciones)
    aterrizajes = [e for e in sim.registro if e.tipo == "aterrizaje"]
    assert aterrizajes
    estado = memoria.cargar_habilidad(HabilidadAterrizaje.NOMBRE)
    assert sum(estado[c]["evaluaciones"] for c in ("fria", "caliente")) >= 160 // 8 + len(aterrizajes)


def test_partida_se_puede_detener(perfil, memoria):
    informe, _, _ = jugar(escenario_prueba(), perfil, memoria, max_pasos=2)
    assert informe.pasos == 2 and informe.resultado.motivo == "partida detenida"


def test_mision_sin_modelo_disponible_sigue_funcionando(perfil, memoria):
    llm = LLMFalso(fallos=10_000)
    mision = construir_mision(TipoMision.RESCATE, perfil)
    informe, _, _ = jugar(mision.escenario, perfil, memoria, llm, sucesos=mision.sucesos)
    assert informe.estadisticas["reglas"] > 0 and informe.estadisticas.get("llm", 0) == 0
    assert informe.resultado.estado == "victoria"
