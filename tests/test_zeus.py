"""Zeus como director de juego: campañas por encargo, misiones que prepara él, decorado, sorpresas y final."""

import asyncio
import json

import pytest

from arma3_ia.config import Config
from arma3_ia.llm import LLMFalso
from arma3_ia.memoria import Memoria
from arma3_ia.modelo import (
    GrupoNuevo,
    Persona,
    Rasgo,
    ResultadoMision,
    TipoAccion,
    TipoObjeto,
    TipoUnidad,
)
from arma3_ia.partida import Partida, preparar_cerebros
from arma3_ia.simulador import SimuladorTexto
from arma3_ia.zeus import (
    DirectorPartida,
    EstadoCampana,
    IntervencionZeus,
    PlanMision,
    SucesoPlan,
    TipoIntervencion,
    TipoMision,
    Zeus,
    campana,
    construir_escenario,
    reglas,
)
from arma3_ia.zeus.mapas import elegir_terreno, terreno_por_reglas
from conftest import construir_mision, escenario_prueba


def correr(coro):
    return asyncio.run(coro)


def nueva_campana(memoria, encargo, *, misiones=3, duracion=30, llm=None, semilla=1):
    zeus = Zeus(llm or LLMFalso(), memoria, semilla=semilla)
    return zeus, correr(zeus.crear_campana(encargo, misiones=misiones, duracion_mision_min=duracion))


def resultado(estado="victoria", puntuacion=0.7, metricas=None):
    return ResultadoMision(estado=estado, motivo="prueba", duracion_s=900,
                           objetivos_completados=int(estado != "derrota"), objetivos_totales=1,
                           efectivos_iniciales=20, bajas_propias=3, bajas_enemigas=8,
                           puntuacion=puntuacion, metricas={"unidades": metricas or {}})


def jugar(zeus, estado, *, semilla=1, max_pasos=None):
    """Prepara, juega y cierra la misión actual de la campaña con Zeus dirigiendo."""
    mision = correr(zeus.preparar_mision(estado))
    escenario, perfil = mision.escenario, estado.perfil
    sim = SimuladorTexto(escenario, semilla=semilla)
    cerebros = preparar_cerebros(escenario, zeus.llm, perfil, Config(), zeus.memoria)
    director = DirectorPartida(zeus, sim, cerebros, escenario, perfil, sucesos=mision.sucesos, cada_s=120,
                               semilla=semilla)
    informe = correr(Partida(sim, cerebros, memoria=zeus.memoria, director=director, escenario=escenario,
                             campana_id=estado.id, capitulo=estado.capitulo, max_pasos=max_pasos).jugar())
    return escenario, sim, informe


# ---------------------------------------------------------------------------------- el encargo
@pytest.mark.parametrize(("texto", "esperado"), [
    ("campaña de 5 misiones de 90 minutos", (5, 90)),
    ("tres misiones de hora y media cada una", (3, 90)),
    ("campaña de 6 horas con 4 misiones", (4, 90)),
    ("una campaña de 2 misiones de 45 min sobre un puente", (2, 45)),
    ("una campaña en Takistan", (None, None)),
    ("100 misiones de 2 minutos", (reglas.MAX_MISIONES, reglas.DURACION_MIN)),
])
def test_entiende_cuantas_misiones_y_cuanto_duran(texto, esperado):
    assert reglas.interpretar_encargo(texto) == esperado


def test_saca_el_objetivo_del_encargo():
    assert reglas.proposito_encargo("campaña de 1944 para volar un puente, 3 misiones de 30 minutos") == \
        "volar un puente"
    assert reglas.proposito_encargo("campaña sobre la defensa de una aldea") == "la defensa de una aldea"
    assert reglas.proposito_encargo("una campaña de guerra fría") == ""


def test_detecta_epoca_terreno_y_tipo_de_mision():
    assert reglas.detectar_era("una campaña en Normandía, segunda guerra mundial") == "segunda_guerra_mundial"
    assert reglas.detectar_era("misiones en el futuro") == "futura"
    assert reglas.detectar_era("algo actual") == "moderna"
    tipos = reglas.detectar_misiones("primero una emboscada a un convoy y luego rescatar a los pilotos")
    assert tipos[:2] == [TipoMision.EMBOSCADA, TipoMision.RESCATE]
    # Zeus solo elige terrenos que están instalados.
    assert terreno_por_reglas("selva y arrozales", ["Altis", "Tanoa"]) == "Tanoa"
    assert elegir_terreno("Chernarus", ["Altis", "Livonia"], "Altis") == "Altis"
    assert elegir_terreno("livonia", ["Altis", "Livonia"], "Altis") == "Livonia"


# ---------------------------------------------------------------------------------- crear la campaña
@pytest.mark.parametrize("misiones", [1, 3, 7])
def test_la_campana_tiene_las_misiones_que_se_piden_y_un_solo_objetivo(memoria, misiones):
    zeus, estado = nueva_campana(memoria, "campaña de 1944 para volar un puente en Normandía", misiones=misiones,
                                 duracion=45)
    plan = estado.plan
    assert len(plan.capitulos) == estado.misiones_total == misiones
    assert estado.duracion_mision_min == 45
    assert "volar un puente" in plan.objetivo_final.lower()
    assert plan.capitulos[-1].critico  # la última misión decide la campaña
    assert all(c.objetivo and c.complicacion for c in plan.capitulos)  # cada paso tiene su problema
    assert estado.perfil.id == "segunda_guerra_mundial" and not estado.perfil.medios_aereos
    assert estado.terreno in zeus.terrenos
    assert len(estado.vivos()) >= 2 and plan.antagonista is not None
    assert estado.margen == estado.margen_inicial == misiones // 3
    # Queda guardada en la base de datos con el principio de su historia.
    assert zeus.cargar(estado.id).plan.nombre == plan.nombre
    fila = next(c for c in memoria.campanas() if c["id"] == estado.id)
    assert fila["estado"] == "en_curso" and fila["objetivo"] == plan.objetivo_final
    assert [e["tipo"] for e in memoria.lore(estado.id)] == ["inicio"]


def test_dos_campanas_con_el_mismo_nombre_no_se_pisan(memoria):
    _, a = nueva_campana(memoria, "campaña moderna")
    _, b = nueva_campana(memoria, "campaña moderna")
    assert a.id != b.id and len(memoria.campanas()) == 2


def test_campana_desde_el_llm_se_ajusta_a_lo_pedido(memoria):
    ambiente = {"era": "Guerra moderna", "epoca": "Altis, 2035", "descripcion": "Guerra en la isla.",
                "terreno": "Malden", "jugadores": {"nombre": "Fuerza Azul"}, "enemigo": {"nombre": "Brigada Roja"},
                "indicativos": ["Lobo-1", "Lobo-2"], "medios_aereos": True}
    plan = {
        "nombre": "Operación Tormenta", "premisa": "Recuperar el valle.", "objetivo_final": "Liberar el valle.",
        "capitulos": [{"titulo": f"Paso {i}", "sinopsis": "Avanzar.", "tipo": t}
                      for i, t in enumerate(["Asaltar", "defensa", "explorar", "emboscar", "rescate"])],
        "personajes": [{"nombre": "Ana Ruiz", "rango": "Sargento", "rasgos": ["Veterana", "valiente"]}],
    }
    llm = LLMFalso(respuestas=[ambiente, plan])
    _zeus, estado = nueva_campana(memoria, "lo que quieras", misiones=3, llm=llm)
    assert estado.plan.nombre == "Operación Tormenta" and estado.perfil.id == "moderna"
    assert estado.terreno == "Malden" and estado.perfil.indicativos["infanteria"][:2] == ["Lobo-1", "Lobo-2"]
    # El modelo escribió 5 pasos pero se pidieron 3.
    assert [c.tipo for c in estado.plan.capitulos] == [TipoMision.ASALTO, TipoMision.DEFENSA,
                                                       TipoMision.RECONOCIMIENTO]
    ana = estado.vivos()[0]
    assert ana.nombre == "Ana Ruiz" and ana.rasgos == [Rasgo.VETERANO, Rasgo.VALIENTE]
    assert len(estado.vivos()) >= 2  # Zeus completa el reparto si el modelo se queda corto
    assert len(llm.llamadas_de("zeus")) == 2


# ---------------------------------------------------------------------------------- éxito y fracaso
def avanzar(estado, *resultados):
    esc = escenario_prueba()
    for r in resultados:
        estado.registrar(esc, resultado(r), semilla=1)


def sin_imprevistos(monkeypatch):
    monkeypatch.setattr(campana, "PROB_IMPREVISTO", 0.0)


def test_el_objetivo_se_cumple_ganando_la_ultima_mision(memoria, monkeypatch):
    sin_imprevistos(monkeypatch)
    _, estado = nueva_campana(memoria, "campaña moderna", misiones=3)
    avanzar(estado, "victoria", "parcial", "victoria")
    assert estado.terminada and estado.resultado_final == "cumplida"
    assert estado.estado_texto == "OBJETIVO CUMPLIDO"


def test_la_campana_puede_fracasar_por_derrotas(memoria, monkeypatch):
    sin_imprevistos(monkeypatch)
    # Perder la misión final.
    _, estado = nueva_campana(memoria, "campaña moderna", misiones=2)
    avanzar(estado, "victoria", "derrota")
    assert estado.resultado_final == "fracasada"
    # Perder un paso crítico.
    _, estado = nueva_campana(memoria, "campaña moderna", misiones=4)
    estado.plan.capitulos[0].critico = True
    avanzar(estado, "derrota")
    assert estado.terminada and estado.resultado_final == "fracasada" and "crítico" in estado.motivo_final
    # Quedarse sin margen de error: 6 misiones dan margen para 2 derrotas.
    _, estado = nueva_campana(memoria, "campaña moderna", misiones=6)
    for c in estado.plan.capitulos[:-1]:
        c.critico = False
    avanzar(estado, "derrota", "derrota")
    assert not estado.terminada and estado.margen == 0
    avanzar(estado, "derrota")
    assert estado.resultado_final == "fracasada" and "Demasiadas derrotas" in estado.motivo_final


def test_los_imprevistos_complican_y_pueden_hundir_la_campana(memoria, monkeypatch):
    monkeypatch.setattr(campana, "PROB_IMPREVISTO", 1.0)
    monkeypatch.setattr(campana, "imprevisto_por_reglas", lambda rng: ("El enemigo descubre el plan.", True))
    _, estado = nueva_campana(memoria, "campaña moderna", misiones=6)  # margen 2
    for c in estado.plan.capitulos[:-1]:
        c.critico = False
    registro = estado.registrar(escenario_prueba(), resultado("victoria"), semilla=1)
    assert registro.imprevisto is not None and registro.imprevisto.grave
    assert estado.margen == 1 and "El enemigo descubre el plan." in estado.plan.capitulos[1].complicacion
    avanzar(estado, "victoria")
    assert estado.margen == 0 and not estado.terminada
    # Sin margen y tras una victoria: el siguiente paso pasa a ser a vida o muerte.
    avanzar(estado, "victoria")
    assert estado.plan.capitulos[3].critico and not estado.terminada
    # Sin margen y tras un tropiezo: la campaña se hunde.
    avanzar(estado, "parcial")
    assert estado.resultado_final == "fracasada" and "imprevisto" in estado.motivo_final


def test_registrar_muertes_recuerdos_reclutas_y_dificultad(memoria, monkeypatch):
    sin_imprevistos(monkeypatch)
    zeus, estado = nueva_campana(memoria, "campaña moderna")
    estado.plan.capitulos[0].tipo = TipoMision.ASALTO
    esc = correr(zeus.preparar_mision(estado)).escenario
    con_persona = [u for u in esc.bandos["blufor"].unidades if u.persona is not None]
    caido, superviviente = con_persona[0], con_persona[1]
    metricas = {caido.id: {"destruida": True, "efectivos_iniciales": 6, "efectivos_finales": 0},
                superviviente.id: {"efectivos_iniciales": 6, "efectivos_finales": 4}}
    vivos_antes = len(estado.vivos())
    registro = estado.registrar(esc, resultado("victoria", 0.9, metricas), semilla=1)
    assert any("ha caído" in c for c in registro.cambios) and registro.caidos
    assert len(estado.vivos()) >= 2 and len(estado.vivos()) >= vivos_antes - 1
    assert estado.dificultad > 1.0
    persona = next(p.persona for p in estado.personajes if p.persona.nombre == superviviente.persona.nombre)
    assert "perdiste a 2" in persona.recuerdos[-1] and "Recuerdas" in persona.presentacion()
    assert EstadoCampana.model_validate_json(estado.model_dump_json()) == estado


# ---------------------------------------------------------------------------------- las misiones de Zeus
@pytest.mark.parametrize("tipo", list(TipoMision))
def test_cada_tipo_de_mision_da_un_escenario_completo(memoria, tipo):
    zeus, estado = nueva_campana(memoria, "campaña moderna", duracion=40)
    estado.plan.capitulos[0].tipo = tipo
    mision = correr(zeus.preparar_mision(estado))
    esc = mision.escenario
    assert esc.bando_jugadores == "blufor" and esc.briefing and esc.terreno in zeus.terrenos and esc.zona
    assert esc.duracion_max_s == 40 * 60
    jugadores, enemigo = esc.bandos["blufor"], esc.bandos["opfor"]
    assert jugadores.objetivos and enemigo.objetivos  # el enemigo también tiene su propio plan
    assert any(u.jugadores for u in jugadores.unidades)
    assert all(u.orden is not None for b in esc.bandos.values() for u in b.unidades)
    assert any(u.persona is not None for b in esc.bandos.values() for u in b.unidades)
    assert esc.objetos  # Zeus siempre moldea el terreno
    assert all(0 < s.minuto < 40 for s in mision.sucesos)
    *_, informe = jugar(zeus, estado, max_pasos=30)
    assert informe.pasos > 0


def test_construir_repara_planes_malos(perfil):
    ww2 = perfil.model_copy(update={"id": "segunda_guerra_mundial", "medios_aereos": False})
    plan = PlanMision.model_validate({
        "nombre": "Prueba", "briefing": "Hola", "hora": "de noche", "clima": "tormenta", "terreno": "Marte",
        "lugares": [{"nombre": "Molino", "tipo": "aldea", "posicion": "020020"}],
        "decorado": [{"tipo": "Campo de minas", "lugar": "Molino"}, {"tipo": "castillo", "lugar": "Molino"}],
        "sucesos": [{"minuto": 500, "tipo": "clima", "clima": "niebla"}],
        "bandos": [
            {"bando": "azul", "unidades": [
                {"indicativo": "Able", "posicion": "Molino", "tarea": "asaltar", "jugadores": True},
                {"indicativo": "Able", "posicion": "en ningún sitio", "tarea": "patrulla"},
                {"indicativo": "Pájaro", "tipo": "heli", "posicion": "Molino"},
            ]},
            {"bando": "rojo", "unidades": [{"indicativo": "Anton", "posicion": "030030", "tarea": "defender"}],
             "objetivos": [{"nombre": "Aguantar", "tipo": "aguantar", "lugar": "Molino"}]},
            {"bando": "azul", "unidades": []},
        ],
    })
    assert plan.hora == "noche" and plan.clima == "lluvia"
    construida = construir_escenario(plan, ww2, duracion_min=30, terrenos=["Altis", "Livonia"],
                                     terreno_campana="Livonia", id_mision="prueba", semilla=1)
    esc = construida.escenario
    assert set(esc.bandos) == {"blufor", "opfor"}
    assert esc.terreno == "Livonia"  # Marte no está instalado
    ids = [u.id for u in esc.bandos["blufor"].unidades]
    assert len(ids) == len(set(ids))
    assert all(u.tipo != TipoUnidad.HELICOPTERO for u in esc.bandos["blufor"].unidades)  # 1944: sin helicópteros
    assert esc.bandos["blufor"].objetivos  # se inventa un objetivo para los jugadores
    assert esc.condiciones.visibilidad_m < 1000
    # Las minas sin dueño son del enemigo de los jugadores; lo que no se conoce queda como ruinas.
    assert [(o.tipo, o.bando) for o in esc.objetos] == [(TipoObjeto.MINAS, "opfor"), (TipoObjeto.RUINAS, None)]
    assert all(s.minuto < 30 for s in construida.sucesos)
    assert construida.avisos


def test_zeus_lanza_sus_sorpresas_a_su_hora(perfil, memoria):
    esc = escenario_prueba()
    sim = SimuladorTexto(esc, semilla=1)
    cerebros = preparar_cerebros(esc, LLMFalso(), perfil, Config(), memoria)
    sucesos = [
        SucesoPlan(minuto=2, tipo=TipoIntervencion.CLIMA, clima="niebla", texto="Cae la niebla."),
        SucesoPlan(minuto=1, tipo=TipoIntervencion.REFUERZOS, bando="opfor",
                   unidad={"indicativo": "Reserva", "tipo": "motorizada", "posicion": "Colina 214"}),
        SucesoPlan(minuto=3, tipo=TipoIntervencion.DECORADO,
                   objeto={"tipo": "barricada", "lugar": "Aldea Vieja", "bando": "opfor"}),
    ]
    director = DirectorPartida(Zeus(LLMFalso(), memoria), sim, cerebros, esc, perfil, sucesos=sucesos,
                               cada_s=10_000)
    grupos = len(sim.unidades)
    assert correr(director.supervisar()) == []  # minuto 0: nada todavía
    for _ in range(2):
        sim.paso()
    hechas = correr(director.supervisar())
    assert len(sim.unidades) == grupos + 1 and any("refuerzos" in h for h in hechas)
    for _ in range(2):
        sim.paso()
    correr(director.supervisar())
    assert sim.escenario.condiciones.clima == "niebla" and sim.anuncios[-1].texto == "Cae la niebla."
    for _ in range(2):
        sim.paso()
    correr(director.supervisar())
    assert any(o.tipo == TipoObjeto.BARRICADA for o in sim.objetos)
    assert not director.pendientes


def test_campana_completa_con_zeus_dirigiendo(memoria, monkeypatch):
    sin_imprevistos(monkeypatch)
    zeus, estado = nueva_campana(memoria, "campaña moderna para liberar Kavala", misiones=2, duracion=20, semilla=2)
    while not estado.terminada:
        escenario, sim, informe = jugar(zeus, estado, semilla=2)
        assert sim.terminado()
        # El briefing lo cuenta Zeus al empezar.
        assert sim.anuncios and sim.anuncios[0].autor == "Zeus" and sim.anuncios[0].texto.startswith(escenario.nombre)
        cierre = correr(zeus.cerrar_mision(estado, escenario, informe.resultado, mision_id=informe.mision_id,
                                           resumen=informe.resumen, sucesos=["00:10 algo pasó"]))
        assert cierre.cronica and cierre.cambios
    assert estado.resultado_final in ("cumplida", "fracasada") and len(estado.historial) <= 2
    # La base de datos guarda la campaña, sus misiones y su historia.
    assert next(c for c in memoria.campanas() if c["id"] == estado.id)["estado"] == estado.resultado_final
    misiones = memoria.misiones(campana_id=estado.id)
    assert len(misiones) == len(estado.historial) and all(m["campana_id"] == estado.id for m in misiones)
    tipos = [e["tipo"] for e in memoria.lore(estado.id)]
    assert tipos[0] == "inicio" and tipos.count("mision") == len(estado.historial) and tipos[-1] == "final"
    # Zeus aprende como director de juego.
    assert any(lec.rol == "zeus" and lec.epoca == "moderna" for lec in memoria.lecciones())
    assert zeus.cargar(estado.id).terminada


# ---------------------------------------------------------------------------------- en directo
def test_poderes_de_zeus_en_el_simulador():
    sim = SimuladorTexto(escenario_prueba(), semilla=1)
    vista = sim.vista_global()
    antes = vista.fuerza("opfor")[0]
    uid = correr(sim.crear_grupo("opfor", GrupoNuevo(id="refuerzo", indicativo="Refuerzo",
                                                     tipo=TipoUnidad.INFANTERIA, posicion=vista.grupos[0].posicion,
                                                     efectivos=5)))
    assert uid in sim.unidades and sim.vista_global().fuerza("opfor")[0] == antes + 5
    visibilidad = sim.escenario.condiciones.visibilidad_m
    correr(sim.cambiar_condiciones(hora="noche", clima="niebla"))
    assert sim.escenario.condiciones.visibilidad_m < visibilidad
    correr(sim.anunciar("Hola"))
    assert sim.anuncios[-1].autor == "Zeus" and sim.anuncios[-1].es_ia


def test_zeus_no_obedece_peticiones_del_chat(perfil, memoria):
    """Zeus decide solo: lo que se escribe en el chat lo oyen los NPC, no el director."""
    esc = escenario_prueba()
    sim = SimuladorTexto(esc, semilla=1)
    cerebros = preparar_cerebros(esc, LLMFalso(), perfil, Config(), memoria)
    director = DirectorPartida(Zeus(LLMFalso(), memoria), sim, cerebros, esc, perfil, cada_s=10_000)
    assert not hasattr(director, "filtrar_chat")
    grupos = len(sim.unidades)
    sim.inyectar_chat("blufor", "Jugador", "Zeus, manda refuerzos enemigos y que se haga de noche")
    correr(Partida(sim, cerebros, director=director, max_pasos=3).jugar())
    assert len(sim.unidades) == grupos and sim.escenario.condiciones.hora == "dia"


def test_zeus_complica_la_partida_si_los_jugadores_van_sobrados(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna de defensa")
    esc = correr(zeus.preparar_mision(estado)).escenario
    sim = SimuladorTexto(esc, semilla=1)
    for u in sim.unidades.values():
        if u.bando != esc.bando_jugadores:
            u.efectivos = 1
    cerebros = preparar_cerebros(esc, zeus.llm, estado.perfil, Config(), memoria)
    director = DirectorPartida(zeus, sim, cerebros, esc, estado.perfil, cada_s=0, semilla=1)
    assert any("refuerzos" in h for h in correr(director.supervisar()))
    # El grupo nuevo tiene cerebro: su bando lo conoce y le da una orden.
    nuevo = next(u for u in sim.unidades.values() if u.id not in {x.id for b in esc.bandos.values()
                                                                  for x in b.unidades})
    assert nuevo.id in cerebros[nuevo.bando].ordenes_iniciales or nuevo.id in cerebros[nuevo.bando].unidades


def test_intervencion_del_llm_valida_y_tolerante():
    datos = {"razon": "más tensión", "acciones": [
        {"tipo": "Refuerzo", "bando": "rojo", "unidad": {"indicativo": "X", "tipo": "Tanque", "posicion": "020020"}},
        {"tipo": "tiempo", "clima": "niebla"}, {"tipo": "bailar"},
        {"tipo": "decorado", "objeto": {"tipo": "humo", "lugar": "020020"}}]}
    intervencion = IntervencionZeus.model_validate(datos)
    assert [a.tipo for a in intervencion.acciones] == [TipoIntervencion.REFUERZOS, TipoIntervencion.CLIMA,
                                                       TipoIntervencion.NADA, TipoIntervencion.DECORADO]
    assert intervencion.acciones[3].objeto.tipo == TipoObjeto.HUMO
    assert intervencion.acciones[0].bando == "opfor"
    assert intervencion.acciones[0].unidad.tipo == TipoUnidad.BLINDADO


# ---------------------------------------------------------------------------------- NPC
def test_rasgos_cambian_cuando_se_rompe_una_unidad(obs):
    from arma3_ia.agentes import reglas as reglas_npc

    unidad = obs.unidades[0].model_copy(update={"efectivos": 3, "moral": 0.45})
    assert not reglas_npc.roto(unidad, Persona(nombre="A", rasgos=["valiente", "veterano"]))
    assert reglas_npc.roto(unidad, Persona(nombre="B", rasgos=["nervioso", "novato"]))
    assert Persona(nombre="C", rasgos=["valiente"] * 5 + ["inventado"]).rasgos == [Rasgo.VALIENTE]


def test_patrulla_y_emboscada(obs):
    from arma3_ia.agentes import reglas as reglas_npc
    from arma3_ia.agentes.esquemas import OrdenMando, TareaMando

    unidad = obs.unidades[0]
    patrulla = OrdenMando(unidad=unidad.indicativo, tarea=TareaMando.PATRULLAR, destino="010010",
                          ruta=["030010", "030030"])
    sin_contacto = obs.model_copy(update={"contactos": []})
    assert reglas_npc.decision_lider(unidad, patrulla, sin_contacto).accion.value == "mover"
    emboscada = OrdenMando(unidad=unidad.indicativo, tarea=TareaMando.EMBOSCAR, destino=unidad.posicion.cuadricula())
    espera = reglas_npc.decision_lider(unidad, emboscada, sin_contacto)
    assert espera.accion.value == "defender" and espera.postura.value == "sigilo"
    cerca = obs.contactos[0].model_copy(update={"posicion": unidad.posicion.desplazar(0, 250)})
    assert reglas_npc.decision_lider(unidad, emboscada, obs.model_copy(update={"contactos": [cerca]})
                                     ).accion.value == "atacar"


def test_hablar_con_un_npc_por_su_nombre(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna de asalto")
    estado.plan.capitulos[0].tipo = TipoMision.ASALTO
    esc = correr(zeus.preparar_mision(estado)).escenario
    sim = SimuladorTexto(esc, semilla=1)
    llm = LLMFalso()
    cerebros = preparar_cerebros(esc, llm, estado.perfil, Config(), memoria)
    unidad = next(u for u in esc.bandos["blufor"].unidades if u.persona is not None and not u.jugadores)
    apellido = unidad.persona.nombre.split()[-1]
    sim.inyectar_chat("blufor", "Jugador", f"{apellido}, ¿cómo lo veis?")
    obs = correr(sim.observar("blufor"))
    salida = correr(cerebros["blufor"].paso(obs, correr(sim.recibir_chat("blufor"))))
    llamadas = llm.llamadas_de("npc")
    assert llamadas and unidad.persona.nombre in llamadas[0].sistema
    assert any(unidad.persona.nombre in m.autor for m in salida.chat)


def test_bando_sin_comandante_no_llama_al_mando(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna")
    estado.plan.capitulos[0].tipo = TipoMision.EMBOSCADA  # emboscada: grupos independientes
    esc = correr(zeus.preparar_mision(estado)).escenario
    assert esc.bandos["blufor"].mando is False
    llm = LLMFalso()
    sim = SimuladorTexto(esc, semilla=1)
    cerebros = preparar_cerebros(esc, llm, estado.perfil, Config(), memoria)
    correr(Partida(sim, cerebros, max_pasos=6).jugar())
    propios = {u.id for u in esc.bandos["blufor"].unidades}
    sistemas = [ll.sistema for ll in llm.llamadas if ll.metadatos.get("agente") in propios]
    assert sistemas and all("No hay un mando por encima de ti" in s for s in sistemas)
    assert all(a.id != "mando" for a in cerebros["blufor"].agentes)


def test_vehiculos_inmovilizados_no_se_mueven(perfil):
    sim = SimuladorTexto(construir_mision(TipoMision.EMBOSCADA, perfil).escenario, semilla=1)
    vehiculo = next(u for u in sim.unidades.values() if u.tipo == TipoUnidad.MOTORIZADA)
    vehiculo.danos = 0.6
    vehiculo.accion, vehiculo.destino = TipoAccion.MOVER, vehiculo.pos.desplazar(0, 1000)
    antes = vehiculo.pos
    sim._mover_tierra(vehiculo, 30)
    assert vehiculo.pos == antes


def test_personajes_sin_nombres_ni_historias_repetidos():
    rng = reglas.rng_de("x", 1)
    usados: set[str] = set()
    gente = [reglas.crear_persona(rng, "segunda_guerra_mundial", "jugadores", usados) for _ in range(5)]
    apellidos = [p.nombre.split()[-1] for p in gente]
    assert len(set(apellidos)) == 5 and len({p.historia for p in gente}) == 5


def test_rng_cambia_con_el_texto():
    assert reglas.rng_de("a", 1).random() != reglas.rng_de("b", 1).random()
    assert reglas.rng_de("a", 1).random() == reglas.rng_de("a", 1).random()
    assert json.dumps(Persona(nombre="X", historia="h" * 400).model_dump())  # la historia se recorta


def test_memoria_de_campanas_vacia():
    m = Memoria(":memory:")
    assert m.campanas() == [] and m.cargar_campana("nada") is None
    m.cerrar()


def test_rasgos_concuerdan_con_la_persona():
    leila = Persona(nombre="Leila Haddad", rasgos=["veterana", "nerviosa", "valiente"], femenina=True)
    assert leila.presentacion().startswith("Leila Haddad (veterana, nerviosa, valiente)")
    assert Persona(nombre="Joe", rasgos=["veterano"]).presentacion() == "Joe (veterano)"


def test_rango_dentro_del_nombre_no_se_duplica() -> None:
    varga = Persona(nombre="Colonel Varga", rango="Sargento")
    assert (varga.rango, varga.nombre) == ("Colonel", "Varga")
    assert varga.presentacion() == "Colonel Varga"
    assert Persona(nombre="Sgt. John Miller").rango == "Sgt"
    assert Persona(nombre="Leila Haddad", rango="").nombre == "Leila Haddad"
    assert Persona(nombre="Mayor").nombre == "Mayor"  # un nombre de una palabra no se toca

