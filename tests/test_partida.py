"""Pruebas de extremo a extremo: misiones completas con el LLM falso y aprendizaje entre misiones."""

import asyncio

import pytest

from arma3_ia.campanas import cargar_perfil
from arma3_ia.config import Config
from arma3_ia.habilidades import HabilidadAterrizaje, Habilidades
from arma3_ia.llm import LLMFalso
from arma3_ia.mando import CerebroBando
from arma3_ia.memoria import Leccion, Reflexion
from arma3_ia.modelo import TipoAccion
from arma3_ia.partida import Partida
from arma3_ia.simulador import SimuladorTexto, cargar_escenario


def mision(escenario_id: str, memoria, llm=None, *, semilla=1, perfil_id=None, config=None, explorar=False,
           max_pasos=None, al_paso=None):
    escenario = cargar_escenario(escenario_id)
    perfil = cargar_perfil(perfil_id or escenario.perfil_recomendado)
    llm = llm or LLMFalso()
    config = config or Config()
    habilidades = Habilidades(memoria, explorar=explorar, semilla=semilla)
    sim = SimuladorTexto(escenario, perfil, semilla=semilla)
    cerebros = {b: CerebroBando(b, llm, perfil, config, memoria, habilidades) for b in escenario.bandos_ia}
    partida = Partida(sim, cerebros, memoria=memoria, reflexion=Reflexion(llm, memoria, perfil),
                      habilidades=habilidades, campana=perfil.id, escenario=escenario.id,
                      max_pasos=max_pasos, al_paso=al_paso)
    return asyncio.run(partida.jugar()), sim, llm


@pytest.mark.parametrize("escenario_id", ["asalto_aldea", "rescate_helicoptero", "defensa_granja",
                                          "emboscada_convoy"])
def test_mision_completa_sin_gpu(escenario_id, memoria):
    informe, sim, llm = mision(escenario_id, memoria)
    assert sim.terminado()
    assert informe.resultado.estado in ("victoria", "parcial", "derrota")
    assert informe.pasos > 3 and informe.decisiones > 5
    assert informe.estadisticas.get("llm", 0) > 0 and informe.estadisticas.get("errores_llm", 0) == 0
    # Todo queda registrado para reflexionar y para un futuro ajuste fino.
    assert memoria.mision(informe.mision_id)["estado"] == informe.resultado.estado
    assert memoria.decisiones(informe.mision_id)
    assert memoria.eventos(informe.mision_id, tipos=["radio"])
    assert informe.resumen
    assert {"comandante", "lider"} <= {ll.metadatos.get("rol") for ll in llm.llamadas}


def test_las_misiones_de_referencia_se_ganan(memoria):
    for escenario_id in ("asalto_aldea", "rescate_helicoptero", "defensa_granja"):
        informe, _, _ = mision(escenario_id, memoria, semilla=1)
        assert informe.resultado.estado == "victoria", (escenario_id, informe.resultado)


def test_el_chat_de_los_jugadores_recibe_respuesta(memoria):
    informe, sim, _ = mision("asalto_aldea", memoria)
    autores = [m.autor for m in sim.chat_log]
    assert "Sargento López" in autores
    indice = autores.index("Sargento López")
    assert any(m.es_ia and m.canal != "radio" for m in sim.chat_log[indice:])
    assert memoria.conversaciones(informe.mision_id)


def test_aprende_de_una_mision_y_lo_usa_en_la_siguiente(memoria):
    """El bucle de aprendizaje: reflexión → lección guardada → aparece en el prompt de la siguiente."""
    propuesta = {
        "resumen": "Nos emboscaron en el bosque.",
        "lecciones": [{"situacion": "Avance de infantería hacia la aldea por el bosque",
                       "leccion": "Rodear el Bosque Negro por el norte en vez de cruzarlo", "tipo": "error",
                       "alcance": "campana", "etiquetas": ["bosque", "emboscada", "infanteria"]}],
    }

    def responder(mensajes, esquema, metadatos):
        if metadatos.get("rol") == "reflexion":
            import json
            return json.dumps(propuesta if metadatos.get("agente") == "lider" else {"resumen": "ok", "lecciones": []})
        from arma3_ia.llm.falso import responder_tactico
        return responder_tactico(mensajes, esquema, metadatos)

    llm = LLMFalso(responder=responder)
    informe1, _, _ = mision("asalto_aldea", memoria, llm, semilla=1)
    nuevas = [l.leccion for l in informe1.lecciones_nuevas]  # noqa: E741
    assert "Rodear el Bosque Negro por el norte en vez de cruzarlo" in nuevas
    guardada = next(l for l in memoria.lecciones(rol="lider") if l.leccion.startswith("Rodear"))  # noqa: E741
    assert guardada.campana == "moderno"

    llm2 = LLMFalso(responder=responder)
    informe2, _, _ = mision("asalto_aldea", memoria, llm2, semilla=2)
    prompts_lider = [ll.sistema for ll in llm2.llamadas_de("lider")]
    assert any("Rodear el Bosque Negro" in s for s in prompts_lider)
    # La lección se usó en una misión y su utilidad se actualizó con el resultado.
    assert memoria.leccion(guardada.id).veces_usada == 1
    # La misma lección propuesta otra vez se refuerza, no se duplica.
    assert any(l.id == guardada.id for l in informe2.lecciones_reforzadas)  # noqa: E741
    assert sum(l.leccion.startswith("Rodear") for l in memoria.lecciones(rol="lider")) == 1  # noqa: E741


def test_las_lecciones_de_otra_campana_no_se_mezclan(memoria):
    memoria.anadir_leccion(Leccion(rol="lider", situacion="Granja en 1944", leccion="Cavar trincheras en el seto",
                                   campana="ww2", etiquetas=["infanteria"]))
    _, _, llm = mision("asalto_aldea", memoria, max_pasos=3)
    assert not any("trincheras" in ll.sistema for ll in llm.llamadas_de("lider"))


def test_retardo_de_radio_en_ww2(memoria):
    perfil = cargar_perfil("ww2")
    escenario = cargar_escenario("defensa_granja")
    sim = SimuladorTexto(escenario, perfil, semilla=1)
    cerebro = CerebroBando("blufor", LLMFalso(), perfil)
    asyncio.run(cerebro.paso(sim.observacion("blufor")))
    assert cerebro.radio.retardo_s == 30
    assert all(a.orden is None for a in cerebro.unidades.values())  # las órdenes aún "viajan"
    sim.paso()
    asyncio.run(cerebro.paso(sim.observacion("blufor")))
    assert any(a.orden is not None for a in cerebro.unidades.values())
    assert not any(u.es_aerea for u in sim.observacion("blufor").unidades)  # 1944: sin helicópteros


def test_los_helicopteros_vuelan_con_lo_aprendido_y_aprenden_al_aterrizar(memoria):
    HabilidadAterrizaje(memoria).entrenar(160)
    acciones_aterrizaje = []

    def al_paso(_paso, salidas):
        acciones_aterrizaje.extend(a for s in salidas.values() for a in s.acciones
                                   if a.tipo in (TipoAccion.ATERRIZAR, TipoAccion.REGRESAR_BASE))

    _, sim, _ = mision("rescate_helicoptero", memoria, explorar=True, al_paso=al_paso)
    assert acciones_aterrizaje and all(a.parametros for a in acciones_aterrizaje)
    aterrizajes = [e for e in sim.registro if e.tipo == "aterrizaje"]
    assert aterrizajes
    estado = memoria.cargar_habilidad(HabilidadAterrizaje.NOMBRE)
    total = sum(estado[c]["evaluaciones"] for c in ("fria", "caliente"))
    assert total >= 160 // 8 + len(aterrizajes)


def test_partida_se_puede_detener(memoria):
    informe, _, _ = mision("asalto_aldea", memoria, max_pasos=2)
    assert informe.pasos == 2 and informe.resultado.motivo == "partida detenida"


def test_mision_sin_modelo_disponible_sigue_funcionando(memoria):
    llm = LLMFalso(fallos=10_000)
    informe, _, _ = mision("rescate_helicoptero", memoria, llm)
    assert informe.estadisticas["reglas"] > 0 and informe.estadisticas.get("llm", 0) == 0
    assert informe.resultado.estado == "victoria"
