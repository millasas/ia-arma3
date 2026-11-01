import asyncio
import json

import pytest

from arma3_ia.comunicacion import RedRadio, TipoMensaje
from arma3_ia.llm import LLMFalso
from arma3_ia.memoria import ErrorBaseDatos, Leccion, Memoria, Recuperador, Reflexion, VigilanteEspacio, puntuar
from arma3_ia.memoria.espacio import MB
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
    mid = memoria.iniciar_mision("asalto_aldea", epoca="moderna", bando="blufor")
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
    general = memoria.anadir_leccion(Leccion(rol="todos", situacion="General", leccion="Reconocer", epoca=None))
    otra = memoria.anadir_leccion(Leccion(rol="piloto", situacion="1944", leccion="x", epoca="segunda_guerra_mundial"))
    assert {l.id for l in memoria.lecciones(rol="piloto", epoca="moderna")} == {lid, general}  # noqa: E741
    assert {l.id for l in memoria.lecciones(rol="piloto", epoca="segunda_guerra_mundial")} == {lid, general, otra}  # noqa: E741
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
    buena = memoria.iniciar_mision("a")
    mala = memoria.iniciar_mision("b")
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
    encontradas = Recuperador(memoria).buscar("piloto", "moderna", "voy a aterrizar en una zona caliente", k=2)
    assert encontradas[0].leccion.startswith("Elegir una LZ")
    assert all(l.leccion != "Aterrizar rápido sin mirar" for l in encontradas[:1])  # noqa: E741
    assert Recuperador(memoria).buscar("lider", "moderna", "aterrizar", k=3) == []
    assert puntuar([], "x") == []


# ------------------------------------------------------------------ reflexión
def test_reflexion_guarda_lecciones_del_modelo_y_deduplica(memoria, perfil):
    mid = memoria.iniciar_mision("asalto_aldea", epoca="moderna")
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
    assert informe.nuevas[0].epoca is None and informe.nuevas[0].etiquetas == ["aterrizaje", "lz"]
    # La llamada incluye el contexto de la misión y las decisiones del piloto.
    assert "LZ Norte" in llm.llamadas[0].usuario and "DERROTA" in llm.llamadas[0].usuario
    # Segunda misión con la misma lección: se refuerza en vez de duplicarse.
    mid2 = memoria.iniciar_mision("asalto_aldea", epoca="moderna")
    memoria.registrar_decision(mision_id=mid2, tiempo=60, agente="Halcón-1", rol="piloto", sistema="s",
                               usuario="u", respuesta="{}", origen="llm")
    informe2 = asyncio.run(reflexion.analizar(mid2, resultado()))
    assert informe2.nuevas == [] and len(informe2.reforzadas) == 1
    assert informe2.reforzadas[0].confirmaciones == 2
    assert "LECCIONES YA CONOCIDAS" in llm.llamadas[1].usuario


def test_reflexion_sin_modelo_usa_reglas_y_actualiza_utilidad(memoria, perfil):
    lid = memoria.anadir_leccion(Leccion(rol="comandante", situacion="s", leccion="l"))
    mid = memoria.iniciar_mision("asalto_aldea", epoca="moderna")
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


# ------------------------------------------------------------------ campañas en la base de datos
def campana_con_datos(memoria, cid="op_prueba"):
    memoria.guardar_campana(cid, nombre="Op. Prueba", encargo="una prueba", objetivo="ganar", terreno="Altis",
                            datos={"x": 1})
    mid = memoria.iniciar_mision("m1", campana_id=cid, capitulo=0, titulo="Primer paso", terreno="Altis")
    memoria.registrar_eventos(mid, [Evento(tiempo=30, tipo="contacto", descripcion="Contacto")])
    memoria.registrar_decision(mision_id=mid, tiempo=30, agente="Alfa-1", rol="lider", sistema="s" * 5000,
                               usuario="u", respuesta="{}", origen="llm")
    memoria.registrar_chat(mid, [MensajeChat(autor="López", texto="Hola")])
    memoria.cerrar_mision(mid, resultado(), "Bien")
    memoria.anadir_lore(cid, "inicio", "Op. Prueba", "Empieza la historia.")
    memoria.anadir_lore(cid, "mision", "Misión 1", "Se tomó el puente.", mid)
    return mid


def test_la_base_de_datos_guarda_campana_misiones_lo_ocurrido_y_lore(memoria):
    mid = campana_con_datos(memoria)
    assert memoria.cargar_campana("op_prueba") == {"x": 1}
    fila = memoria.campanas()[0]
    assert (fila["id"], fila["estado"], fila["objetivo"]) == ("op_prueba", "en_curso", "ganar")
    assert fila["terreno"] == "Altis"
    assert [m["id"] for m in memoria.misiones(campana_id="op_prueba")] == [mid]
    assert memoria.mision(mid)["titulo"] == "Primer paso"
    assert [e["texto"] for e in memoria.lore("op_prueba")] == ["Empieza la historia.", "Se tomó el puente."]
    memoria.guardar_campana("op_prueba", nombre="Op. Prueba", estado="cumplida", datos={"x": 2})
    assert memoria.campanas()[0]["estado"] == "cumplida" and memoria.cargar_campana("op_prueba") == {"x": 2}
    assert memoria.uso_por_campana()["op_prueba"] > 0
    assert memoria.estadisticas()["campanas"] == 1 and memoria.estadisticas()["lore"] == 2


def test_borrar_una_campana_borra_todo_lo_suyo_pero_no_lo_aprendido(tmp_path):
    memoria = Memoria(tmp_path / "BBDD" / "ia.db")
    mid = campana_con_datos(memoria)
    otra = campana_con_datos(memoria, "otra")
    lid = memoria.anadir_leccion(Leccion(rol="lider", situacion="s", leccion="l", mision_origen=mid))
    conteo = memoria.borrar_campana("op_prueba")
    assert conteo == {"misiones": 1, "eventos": 1, "decisiones": 1, "conversaciones": 1, "lore": 2}
    assert memoria.cargar_campana("op_prueba") is None and memoria.mision(mid) is None
    assert memoria.eventos(mid) == [] and memoria.decisiones(mid) == [] and memoria.lore("op_prueba") == []
    assert memoria.mision(otra) is not None and memoria.lore("otra")  # la otra campaña sigue intacta
    assert memoria.leccion(lid) is not None
    memoria.cerrar()


def test_una_base_de_datos_antigua_avisa_en_vez_de_romperse(tmp_path):
    import sqlite3

    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT)")
    con.execute("INSERT INTO meta VALUES ('version_esquema', '1')")
    con.commit()
    con.close()
    with pytest.raises(ErrorBaseDatos, match="versión 1"):
        Memoria(ruta)


# ------------------------------------------------------------------ vigilante del espacio
def test_el_vigilante_revisa_el_espacio_en_cada_cambio(tmp_path):
    avisos = []
    ruta = tmp_path / "BBDD" / "ia.db"
    vigilante = VigilanteEspacio(ruta, carpeta_campanas=tmp_path / "CAMPAÑAS", limite_mb=500, minimo_libre_mb=0,
                                 al_cambiar=avisos.append)
    memoria = Memoria(ruta, vigilante=vigilante)
    antes = vigilante.comprobaciones
    memoria.anadir_leccion(Leccion(rol="lider", situacion="s", leccion="l"))
    memoria.guardar_campana("c", nombre="c", datos={})
    assert vigilante.comprobaciones >= antes + 2
    assert vigilante.ultimo.nivel == "ok" and avisos == []  # si todo va bien no molesta
    assert "Espacio correcto" in vigilante.ultimo.mensaje()
    memoria.cerrar()


def test_sin_espacio_avisa_y_entra_en_modo_ahorro(tmp_path, monkeypatch):
    avisos = []
    ruta = tmp_path / "ia.db"
    vigilante = VigilanteEspacio(ruta, limite_mb=1000, minimo_libre_mb=100, al_cambiar=avisos.append)
    memoria = Memoria(ruta, vigilante=vigilante)
    libre = {"mb": 150.0}  # menos del doble del mínimo: aviso
    monkeypatch.setattr(vigilante, "_libre_mb", lambda: libre["mb"])
    vigilante.comprobar()
    assert [a.nivel for a in avisos] == ["aviso"] and not memoria.ahorro
    libre["mb"] = 50.0  # por debajo del mínimo: crítico
    mid = memoria.iniciar_mision("m")
    assert avisos[-1].nivel == "critico" and memoria.ahorro and "campana borrar" in avisos[-1].mensaje()
    memoria.registrar_decision(mision_id=mid, tiempo=0, agente="A", rol="lider", sistema="largo" * 100,
                               usuario="largo", respuesta="{}", origen="llm")
    memoria.registrar_eventos(mid, [Evento(tiempo=0, tipo="radio", descripcion="bla"),
                                    Evento(tiempo=0, tipo="bajas", descripcion="1 baja")])
    decision = memoria.decisiones(mid)[0]
    assert decision["sistema"] == "" and decision["respuesta"] == "{}"  # se guarda lo esencial
    assert [e["tipo"] for e in memoria.eventos(mid)] == ["bajas"]
    n = len(avisos)
    memoria.registrar_chat(mid, [MensajeChat(autor="x", texto="y")])
    assert len(avisos) == n  # solo avisa cuando cambia el nivel
    libre["mb"] = 10_000.0
    vigilante.comprobar()
    assert avisos[-1].nivel == "ok" and not memoria.ahorro
    memoria.cerrar()


def test_el_limite_de_la_base_de_datos_cuenta_tambien_las_carpetas(tmp_path):
    carpeta = tmp_path / "CAMPAÑAS"
    carpeta.mkdir()
    (carpeta / "grande.txt").write_bytes(b"x" * int(0.9 * MB))
    vigilante = VigilanteEspacio(tmp_path / "ia.db", carpeta_campanas=carpeta, limite_mb=1, minimo_libre_mb=0)
    assert vigilante.comprobar().nivel == "aviso"
    (carpeta / "otro.txt").write_bytes(b"x" * int(0.2 * MB))
    assert vigilante.comprobar().nivel == "aviso"  # no se vuelve a medir la carpeta hasta que se avisa
    vigilante.carpetas_cambiadas()
    assert vigilante.ultimo.nivel == "critico" and vigilante.ultimo.carpetas_mb > 1
