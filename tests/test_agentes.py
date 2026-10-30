import asyncio

from arma3_ia.agentes import (
    Comandante,
    ContextoAgentes,
    DecisionComandante,
    DecisionLider,
    DecisionPiloto,
    Enlace,
    LiderEscuadra,
    Piloto,
    TareaMando,
    reglas,
)
from arma3_ia.comunicacion import TipoMensaje
from arma3_ia.config import ConfigAgentes
from arma3_ia.geo import Posicion
from arma3_ia.llm import LLMFalso
from arma3_ia.mando import CerebroBando
from arma3_ia.memoria import Leccion
from arma3_ia.modelo import Evento, MensajeChat, TipoAccion


def contexto(perfil, llm=None, memoria=None, **config) -> ContextoAgentes:
    return ContextoAgentes.crear(llm or LLMFalso(), perfil, "blufor", ConfigAgentes(**config), memoria)


# ------------------------------------------------------------------ comandante
def test_comandante_convierte_ordenes_en_radio(perfil, obs):
    decision = DecisionComandante.model_validate({
        "evaluacion": "x", "intencion": "Tomar la aldea",
        "ordenes": [
            {"unidad": "alfa 1", "tarea": "atacar", "objetivo": "C1", "destino": "obj_aldea"},
            {"unidad": "Halcón-1", "tarea": "transporte", "objetivo": "Bravo-1", "destino": "LZ Norte"},
            {"unidad": "Zulú-9", "tarea": "mover", "destino": "026027"},
            {"unidad": "Alfa-1", "tarea": "apoyo_aereo", "objetivo": "C1"},
        ],
        "mensaje_general": "A todas las unidades: luz verde.",
    })
    mando = Comandante(contexto(perfil), "mando", "Mando")
    salientes = list(mando.mensajes_de_ordenes(decision, obs))
    por_destino = {s.para: s for s in salientes}
    assert set(por_destino) == {"alfa", "halcon", "bravo", "todos"}
    assert por_destino["alfa"].texto.startswith("Alfa-1, aquí Mando: ataque C1")
    assert por_destino["alfa"].datos["orden"]["unidad"] == "Alfa-1"  # indicativo normalizado
    # El transporte genera automáticamente la orden de embarque de la carga.
    embarque = por_destino["bravo"].datos["orden"]
    assert embarque["tarea"] == "embarcar" and embarque["objetivo"] == "Halcón-1"
    assert "LZ Norte" in por_destino["bravo"].texto
    # Las órdenes imposibles se devuelven como avisos para la próxima decisión.
    assert any("Zulú-9" in a for a in mando.avisos)
    assert any("solo vale para helicópteros" in a for a in mando.avisos)
    # Repetir la misma orden poco después no vuelve a saturar la radio.
    repetidas = [s.para for s in mando.mensajes_de_ordenes(decision, obs.model_copy(update={"tiempo": 200}))]
    assert repetidas == ["todos"]


def test_comandante_decide_al_inicio_y_ante_eventos(perfil, obs):
    mando = Comandante(contexto(perfil), "mando", "Mando")
    assert mando.necesita_decidir(obs)
    asyncio.run(mando.decidir(obs))
    assert mando.intencion
    despues = obs.model_copy(update={"tiempo": obs.tiempo + 30})
    assert not mando.necesita_decidir(despues)
    evento = Evento(tiempo=150, tipo="unidad_destruida", descripcion="Bravo-1 destruida")
    assert mando.necesita_decidir(despues.model_copy(update={"eventos": [evento]}))
    assert mando.necesita_decidir(obs.model_copy(update={"tiempo": obs.tiempo + 120}))
    nuevo = obs.contactos[0].model_copy(update={"id": "C2"})
    assert mando.necesita_decidir(despues.model_copy(update={"contactos": [*obs.contactos, nuevo]}))


def test_reglas_del_comandante_cubren_el_objetivo(perfil, obs):
    decision = reglas.decision_comandante(obs, perfil)
    ordenes = {o.unidad: o for o in decision.ordenes}
    assert ordenes, "la doctrina siempre da alguna orden"
    tierra = [o for u, o in ordenes.items() if u in ("Alfa-1", "Bravo-1")]
    assert tierra and all(o.tarea != TareaMando.TRANSPORTE for o in tierra)
    assert DecisionComandante.model_validate(decision.model_dump()) == decision


# ------------------------------------------------------------------ líder y piloto
def test_lider_traduce_decisiones_a_acciones(perfil, obs):
    lider = LiderEscuadra(contexto(perfil), "alfa", "Alfa-1")
    alfa = obs.unidad("alfa")
    assert alfa is not None
    atacar = lider.a_acciones(DecisionLider(evaluacion="e", accion="atacar", objetivo="c1"), obs, alfa)[0]
    assert atacar.tipo == TipoAccion.ATACAR and atacar.objetivo == "C1"
    assert atacar.destino == obs.contactos[0].posicion
    mover = lider.a_acciones(DecisionLider(evaluacion="e", accion="mover", destino="la LZ Norte"), obs, alfa)[0]
    assert mover.destino == Posicion(x=2700, y=3500)
    perdido = lider.a_acciones(DecisionLider(evaluacion="e", accion="mover", destino="Marte"), obs, alfa)[0]
    assert perdido.tipo == TipoAccion.ESPERAR and any("Marte" in a for a in lider.avisos)
    reco = lider.a_acciones(DecisionLider(evaluacion="e", accion="reconocer", destino="026027"), obs, alfa)[0]
    assert reco.postura is not None and reco.postura.value == "sigilo"
    embarque = lider.a_acciones(DecisionLider(evaluacion="e", accion="embarcar", objetivo="halcon"), obs, alfa)[0]
    assert embarque.tipo == TipoAccion.EMBARCAR and embarque.objetivo == "halcon"
    assert lider.a_acciones(DecisionLider(evaluacion="e", accion="continuar"), obs, alfa) == []


def test_piloto_no_desembarca_en_vuelo(perfil, obs):
    piloto = Piloto(contexto(perfil), "halcon", "Halcón-1")
    halcon = obs.unidad("halcon")
    assert halcon is not None
    volando = halcon.model_copy(update={"en_vuelo": True, "pasajeros": ["bravo"]})
    accion = piloto.a_acciones(DecisionPiloto(evaluacion="e", accion="desembarcar"), obs, volando)[0]
    assert accion.tipo == TipoAccion.ATERRIZAR
    apoyo = piloto.a_acciones(DecisionPiloto(evaluacion="e", accion="apoyo_aereo", objetivo="C1"), obs, halcon)
    assert apoyo[0].tipo == TipoAccion.ESPERAR  # Halcón-1 no va armado
    vuelo = piloto.a_acciones(DecisionPiloto(evaluacion="e", accion="volar", destino="LZ Norte",
                                             altitud="baja"), obs, halcon)[0]
    assert vuelo.destino == Posicion(x=2700, y=3500) and vuelo.altitud is not None


def test_lider_obedece_la_orden_del_mando_y_disciplina_de_radio(perfil, obs):
    llm = LLMFalso()
    ctx = contexto(perfil, llm)
    mando = Comandante(ctx, "mando", "Mando")
    lider = LiderEscuadra(ctx, "alfa", "Alfa-1")
    decision = DecisionComandante.model_validate({
        "evaluacion": "x", "intencion": "y",
        "ordenes": [{"unidad": "Alfa-1", "tarea": "reconocer", "destino": "LZ Norte"}],
    })
    from arma3_ia.comunicacion import RedRadio

    radio = RedRadio()
    radio.registrar("mando", "Mando")
    radio.registrar("alfa", "Alfa-1")
    for s in mando.mensajes_de_ordenes(decision, obs):
        radio.enviar("mando", s.para, s.texto, s.tipo, obs.tiempo, s.datos)
    lider.recibir(radio.recibir("alfa", obs.tiempo))
    alfa = obs.unidad("alfa")
    assert alfa is not None and lider.necesita_decidir(obs, alfa)
    resultado = asyncio.run(lider.decidir_y_actuar(obs, alfa))
    assert resultado.acciones[0].tipo == TipoAccion.RECONOCER
    assert resultado.acciones[0].destino == Posicion(x=2700, y=3500)
    # El prompt incluye la orden y la situación.
    llamada = llm.llamadas_de("lider")[0]
    assert "reconozca" in llamada.usuario and "Alfa-1" in llamada.sistema
    # Disciplina de radio: el mismo informe no se repite antes de 2 minutos.
    salientes = lider._salientes(obs.tiempo, "Sin novedad", None, [])
    assert [s.texto for s in salientes] == ["Sin novedad"]
    assert lider._salientes(obs.tiempo + 60, "Sin novedad", None, []) == []
    assert len(lider._salientes(obs.tiempo + 200, "Sin novedad", None, [])) == 1


# ------------------------------------------------------------------ plan B y lecciones
def test_si_el_modelo_falla_decide_por_reglas(perfil, obs, memoria):
    llm = LLMFalso(fallos=10)
    lider = LiderEscuadra(contexto(perfil, llm, memoria), "alfa", "Alfa-1")
    alfa = obs.unidad("alfa")
    assert alfa is not None
    resultado = asyncio.run(lider.decidir_y_actuar(obs, alfa))
    assert resultado.acciones, "nunca se queda congelado"
    assert lider.estadisticas["reglas"] == 1 and lider.estadisticas["errores_llm"] == 1
    assert memoria.decisiones()[0]["origen"] == "reglas"


def test_si_el_modelo_tarda_demasiado_decide_por_reglas(perfil, obs):
    llm = LLMFalso(latencia_s=1.0)
    ctx = contexto(perfil, llm, timeout_decision_s=0.05)
    mando = Comandante(ctx, "mando", "Mando")
    decision = asyncio.run(mando.decidir(obs))
    assert decision.ordenes and mando.estadisticas["reglas"] == 1


def test_respuestas_basura_del_modelo_se_corrigen_o_caen_a_reglas(perfil, obs):
    llm = LLMFalso(["no sé", '{"evaluacion": "ok", "accion": "defender"}'])
    lider = LiderEscuadra(contexto(perfil, llm), "alfa", "Alfa-1")
    alfa = obs.unidad("alfa")
    assert alfa is not None
    resultado = asyncio.run(lider.decidir_y_actuar(obs, alfa))
    assert resultado.acciones[0].tipo == TipoAccion.DEFENDER
    assert lider.estadisticas["llm"] == 1


def test_las_lecciones_llegan_al_prompt(perfil, obs, memoria):
    lid = memoria.anadir_leccion(Leccion(rol="piloto", situacion="Aterrizaje en LZ caliente",
                                         leccion="Aterrizar a más de 500 m del enemigo", tipo="error"))
    memoria.anadir_leccion(Leccion(rol="lider", situacion="Emboscada", leccion="No ir por la carretera"))
    llm = LLMFalso()
    piloto = Piloto(contexto(perfil, llm, memoria), "halcon", "Halcón-1")
    halcon = obs.unidad("halcon")
    assert halcon is not None
    asyncio.run(piloto.decidir_y_actuar(obs, halcon))
    sistema = llm.llamadas[0].sistema
    assert "EVITA" in sistema and "500 m del enemigo" in sistema
    assert "carretera" not in sistema  # lección de otro rol
    assert piloto.lecciones_usadas == {lid}
    assert memoria.decisiones()[0]["lecciones"] == [lid]


# ------------------------------------------------------------------ enlace con jugadores
def test_enlace_responde_y_reenvia_al_mando(perfil, obs):
    llm = LLMFalso([{
        "respuesta": "Recibido, Teniente. Pasamos la petición al mando.",
        "reenviar": [{"para": "mando", "texto": "Piden apoyo en la aldea"}],
    }])
    enlace = Enlace(contexto(perfil, llm), "enlace", "Enlace")
    respuestas, salientes = asyncio.run(
        enlace.atender([MensajeChat(autor="Teniente Vega", texto="Necesitamos apoyo en la aldea")], obs)
    )
    assert respuestas[0].es_ia and respuestas[0].autor == "Enlace"
    assert salientes[0].para == "mando" and salientes[0].tipo == TipoMensaje.JUGADOR
    assert "Teniente Vega" in salientes[0].texto
    assert "Teniente Vega" in llm.llamadas[0].usuario
    assert "CAMPAÑA" in llm.llamadas[0].sistema


def test_enlace_por_reglas_entiende_peticiones(perfil, obs):
    enlace = Enlace(contexto(perfil, usar_llm_enlace=False), "enlace", "Enlace")
    respuestas, _ = asyncio.run(
        enlace.atender([MensajeChat(autor="López", texto="¿Cuál es la situación?")], obs)
    )
    assert respuestas and respuestas[0].texto


# ------------------------------------------------------------------ cerebro completo
def test_cerebro_un_paso_coordina_a_todos(perfil, obs, memoria):
    llm = LLMFalso()
    cerebro = CerebroBando("blufor", llm, perfil, memoria=memoria)
    cerebro.iniciar_mision(memoria.iniciar_mision("moderno", "test"))
    salida = asyncio.run(cerebro.paso(obs, [MensajeChat(autor="López", texto="¿Qué hacemos?")]))
    roles = {ll.metadatos["rol"] for ll in llm.llamadas}
    assert {"comandante", "enlace", "lider", "piloto"} <= roles
    assert salida.acciones and salida.decisiones >= 4
    assert any(m.autor == perfil.indicativo_enlace for m in salida.chat)
    assert any(m.canal == "radio" for m in salida.chat)  # tráfico de radio retransmitido
    assert set(cerebro.unidades) == {"alfa", "bravo", "halcon"}
    # Una unidad destruida deja de recibir órdenes.
    sin_bravo = obs.model_copy(update={
        "tiempo": obs.tiempo + 30,
        "unidades": [u if u.id != "bravo" else u.model_copy(update={"efectivos": 0, "estado": "destruida"})
                     for u in obs.unidades],
    })
    asyncio.run(cerebro.paso(sin_bravo))
    assert "bravo" not in cerebro.unidades and "bravo" in cerebro.caidos
    assert cerebro.radio.resolver("Bravo-1") == []


def test_jugadores_al_mando(perfil, obs):
    from arma3_ia.config import Config

    config = Config()
    config.agentes.jugadores_al_mando = True
    llm = LLMFalso()
    cerebro = CerebroBando("blufor", llm, perfil, config)
    asyncio.run(cerebro.paso(obs))
    lider = cerebro.unidades["alfa"]
    lider.recibir([cerebro.radio.enviar("enlace", "Alfa-1", "De López (jugador): cubridnos",
                                        TipoMensaje.JUGADOR, obs.tiempo)[0]])
    assert lider.orden_jugador.endswith("cubridnos") and lider.orden_nueva
