import asyncio
import json

from arma3_ia.comunicacion import RedRadio, TipoMensaje
from arma3_ia.llm import LLMFalso
from arma3_ia.memoria import Leccion, Memoria, Recuperador, Reflexion, puntuar
from arma3_ia.modelo import Evento, MensajeChat, ResultadoMision


def resultado(estado="victoria", puntuacion=0.8, bajas=1, **extra) -> ResultadoMision:
    datos = dict(estado=estado, motivo="objetivos cumplidos", duracion_s=900, objetivos_completados=1,
                 objetivos_totales=1, efectivos_iniciales=16, bajas_propias=bajas, bajas_enemigas=8,
                 puntuacion=puntuacion)
    datos.update(extra)
    return ResultadoMision(**datos)


# ------------------------------------------------------------------ radio
def test_radio_resuelve_indicativos_como_los_escribe_el_modelo():
    radio = RedRadio()
    radio.registrar("mando", "Mando", ("comandante", "hq"))
    radio.registrar("alfa", "Alfa-1")
    radio.registrar("halcon", "Halcón-1")
    assert radio.resolver("ALFA-1") == ["alfa"]
    assert radio.resolver("escuadra alfa 1") == ["alfa"]
    assert radio.resolver("halcon 1") == ["halcon"]
    assert radio.resolver("HQ") == ["mando"]
    assert sorted(radio.resolver("todos")) == ["alfa", "halcon", "mando"]
    assert radio.resolver("Zulú-9") == []


def test_radio_entrega_con_retardo_y_guarda_historial():
    radio = RedRadio(retardo_s=30)
    radio.registrar("mando", "Mando")
    radio.registrar("alfa", "Alfa-1")
    radio.registrar("bravo", "Bravo-1")
    entregados = radio.enviar("mando", "todos", "Avanzad", TipoMensaje.ORDEN, tiempo=100)
    assert {m.para for m in entregados} == {"alfa", "bravo"}  # el emisor no se lo envía a sí mismo
    assert radio.recibir("alfa", 110) == []
    recibidos = radio.recibir("alfa", 130)
    assert [m.texto for m in recibidos] == ["Avanzad"] and recibidos[0].de_indicativo == "Mando"
    assert radio.recibir("alfa", 200) == []
    assert len(radio.historial) == 1 and radio.historial[0].para_indicativo == "todos"
    assert len(radio.tomar_nuevos()) == 1 and radio.tomar_nuevos() == []


def test_radio_baja_de_agente():
    radio = RedRadio()
    radio.registrar("alfa", "Alfa-1")
    radio.dar_de_baja("alfa")
    assert radio.enviar("mando", "Alfa-1", "¿Me recibís?") == []


# ------------------------------------------------------------------ almacén
def test_ciclo_de_mision(memoria):
    mid = memoria.iniciar_mision("moderno", "asalto_aldea", "blufor")
    memoria.registrar_eventos(mid, [Evento(tiempo=30, tipo="contacto", descripcion="Contacto", unidad="alfa",
                                           datos={"contacto": "C1"})])
    memoria.registrar_decision(mision_id=mid, tiempo=30, agente="Alfa-1", rol="lider", sistema="s",
                               usuario="u", respuesta='{"accion": "mover"}', origen="llm", lecciones=[3, 1])
    memoria.registrar_chat(mid, [MensajeChat(autor="López", texto="Hola", tiempo=60)])
    memoria.cerrar_mision(mid, resultado(), "Todo bien")
    m = memoria.mision(mid)
    assert m["estado"] == "victoria" and m["resumen"] == "Todo bien"
    assert json.loads(m["resultado"])["bajas_propias"] == 1
    assert memoria.eventos(mid)[0]["datos"] == {"contacto": "C1"}
    assert memoria.eventos(mid, tipos=["bajas"]) == []
    assert memoria.decisiones(mid)[0]["lecciones"] == [1, 3]
    assert memoria.conversaciones(mid)[0]["autor"] == "López"
    assert memoria.estadisticas()["victorias"] == 1


def test_lecciones_utilidad_y_olvido(memoria):
    lid = memoria.anadir_leccion(Leccion(rol="piloto", situacion="LZ caliente", leccion="No aterrizar"))
    general = memoria.anadir_leccion(Leccion(rol="todos", situacion="General", leccion="Reconocer", campana=None))
    otra = memoria.anadir_leccion(Leccion(rol="piloto", situacion="1944", leccion="x", campana="ww2"))
    assert {l.id for l in memoria.lecciones(rol="piloto", campana="moderno")} == {lid, general}  # noqa: E741
    assert {l.id for l in memoria.lecciones(rol="piloto", campana="ww2")} == {lid, general, otra}  # noqa: E741
    for _ in range(5):
        memoria.registrar_uso_lecciones([lid], exito=False)
    leccion = memoria.leccion(lid)
    assert leccion.veces_usada == 5 and leccion.utilidad < 0.25
    assert memoria.revisar_lecciones() == [lid]
    assert not memoria.leccion(lid).activa
    memoria.reforzar_leccion(lid)
    reforzada = memoria.leccion(lid)
    assert reforzada.activa and reforzada.confirmaciones == 2 and reforzada.peso == 1.25


def test_memoria_persiste_en_disco(tmp_path):
    ruta = tmp_path / "sub" / "memoria.db"
    m = Memoria(ruta)
    m.anadir_leccion(Leccion(rol="lider", situacion="s", leccion="l"))
    m.guardar_habilidad("vuelo", {"media": [0.1, 0.2]})
    m.cerrar()
    m2 = Memoria(ruta)
    assert len(m2.lecciones()) == 1
    assert m2.cargar_habilidad("vuelo") == {"media": [0.1, 0.2]}
    m2.guardar_habilidad("vuelo", {"media": [0.3]})
    assert m2.cargar_habilidad("vuelo") == {"media": [0.3]}
    m2.cerrar()


def test_exportar_dataset(memoria, tmp_path):
    buena = memoria.iniciar_mision("moderno", "a")
    mala = memoria.iniciar_mision("moderno", "b")
    for mid in (buena, mala):
        memoria.registrar_decision(mision_id=mid, tiempo=0, agente="Mando", rol="comandante", sistema="S",
                                   usuario="U", respuesta="{}", origen="llm")
        memoria.registrar_decision(mision_id=mid, tiempo=0, agente="Mando", rol="comandante", sistema="S",
                                   usuario="U", respuesta="{}", origen="reglas")
    memoria.cerrar_mision(buena, resultado(puntuacion=0.9))
    memoria.cerrar_mision(mala, resultado(estado="derrota", puntuacion=0.1))
    ruta = tmp_path / "dataset.jsonl"
    assert memoria.exportar_dataset(ruta) == 1
    ejemplo = json.loads(ruta.read_text().splitlines()[0])
    assert [m["role"] for m in ejemplo["messages"]] == ["system", "user", "assistant"]
    assert ejemplo["metadata"]["mision"] == buena


# ------------------------------------------------------------------ recuperación
def test_recuperador_prioriza_lo_relevante_y_lo_util(memoria):
    memoria.anadir_leccion(Leccion(rol="piloto", situacion="Aterrizaje en LZ caliente",
                                   leccion="Elegir una LZ alternativa a más de 500 m", etiquetas=["aterrizaje"]))
    memoria.anadir_leccion(Leccion(rol="piloto", situacion="Vuelo nocturno", leccion="Volar a media altura"))
    inutil = Leccion(rol="piloto", situacion="Aterrizaje en LZ caliente", leccion="Aterrizar rápido sin mirar",
                     veces_usada=10, exitos=0)
    memoria.anadir_leccion(inutil)
    encontradas = Recuperador(memoria).buscar("piloto", "moderno", "voy a aterrizar en una zona caliente", k=2)
    assert encontradas[0].leccion.startswith("Elegir una LZ")
    assert all(l.leccion != "Aterrizar rápido sin mirar" for l in encontradas[:1])  # noqa: E741
    assert Recuperador(memoria).buscar("lider", "moderno", "aterrizar", k=3) == []
    assert puntuar([], "x") == []


# ------------------------------------------------------------------ reflexión
def test_reflexion_guarda_lecciones_del_modelo_y_deduplica(memoria, perfil):
    mid = memoria.iniciar_mision("moderno", "asalto_aldea")
    memoria.registrar_decision(mision_id=mid, tiempo=60, agente="Halcón-1", rol="piloto", sistema="s",
                               usuario="u", respuesta='{"accion": "aterrizar", "destino": "LZ Norte"}', origen="llm")
    propuesta = {
        "resumen": "Aterrizamos en una LZ caliente.",
        "lecciones": [{"situacion": "LZ con enemigos cerca", "leccion": "Pedir apoyo aéreo antes de aterrizar",
                       "tipo": "error", "alcance": "general", "etiquetas": ["aterrizaje", "LZ"]}],
    }
    llm = LLMFalso([propuesta, propuesta])
    reflexion = Reflexion(llm, memoria, perfil)
    informe = asyncio.run(reflexion.analizar(mid, resultado(estado="derrota", puntuacion=0.2)))
    assert len(informe.nuevas) == 1 and informe.origen == {"piloto": "llm"}
    assert informe.nuevas[0].campana is None and informe.nuevas[0].etiquetas == ["aterrizaje", "lz"]
    # La llamada incluye el contexto de la misión y las decisiones del piloto.
    assert "LZ Norte" in llm.llamadas[0].usuario and "DERROTA" in llm.llamadas[0].usuario
    # Segunda misión con la misma lección: se refuerza en vez de duplicarse.
    mid2 = memoria.iniciar_mision("moderno", "asalto_aldea")
    memoria.registrar_decision(mision_id=mid2, tiempo=60, agente="Halcón-1", rol="piloto", sistema="s",
                               usuario="u", respuesta="{}", origen="llm")
    informe2 = asyncio.run(reflexion.analizar(mid2, resultado()))
    assert informe2.nuevas == [] and len(informe2.reforzadas) == 1
    assert informe2.reforzadas[0].confirmaciones == 2
    assert "LECCIONES YA CONOCIDAS" in llm.llamadas[1].usuario


def test_reflexion_sin_modelo_usa_reglas_y_actualiza_utilidad(memoria, perfil):
    lid = memoria.anadir_leccion(Leccion(rol="comandante", situacion="s", leccion="l"))
    mid = memoria.iniciar_mision("moderno", "asalto_aldea")
    memoria.registrar_decision(mision_id=mid, tiempo=0, agente="Mando", rol="comandante", sistema="s",
                               usuario="u", respuesta="{}", origen="llm")
    llm = LLMFalso(fallos=5)
    malo = resultado(estado="derrota", puntuacion=0.1, bajas=10, motivo="se agotó el tiempo")
    informe = asyncio.run(Reflexion(llm, memoria, perfil).analizar(mid, malo, lecciones_usadas=[lid]))
    assert informe.origen == {"comandante": "reglas"} and not informe.exito
    textos = " ".join(l.leccion for l in informe.nuevas)  # noqa: E741
    assert "tiempo" in textos and "Reconocer" in textos
    assert memoria.leccion(lid).veces_usada == 1 and memoria.leccion(lid).exitos == 0
    assert memoria.mision(mid)["resumen"].startswith("Misión terminada en derrota")
