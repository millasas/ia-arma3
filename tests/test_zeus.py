"""Zeus como director de juego: campañas, misiones generadas, intervenciones en directo y NPC con personalidad."""

import asyncio
import json

import pytest

from arma3_ia.campanas import cargar_perfil
from arma3_ia.config import Config
from arma3_ia.llm import LLMFalso
from arma3_ia.memoria import Memoria
from arma3_ia.modelo import (
    GrupoNuevo,
    MensajeChat,
    Persona,
    Rasgo,
    ResultadoMision,
    TipoAccion,
    TipoUnidad,
)
from arma3_ia.partida import Partida, preparar_cerebros
from arma3_ia.simulador import SimuladorTexto, cargar_escenario
from arma3_ia.simulador.escenario import guardar_escenario
from arma3_ia.zeus import (
    DirectorPartida,
    EstadoCampana,
    IntervencionZeus,
    PlanMision,
    TipoIntervencion,
    TipoMision,
    Zeus,
    construir_escenario,
)
from arma3_ia.zeus.reglas import detectar_misiones, detectar_perfil, orden_por_reglas


def correr(coro):
    return asyncio.run(coro)


def nueva_campana(memoria, encargo, *, llm=None, semilla=1):
    zeus = Zeus(llm or LLMFalso(), memoria, semilla=semilla)
    return zeus, correr(zeus.crear_campana(encargo))


def jugar(zeus, estado, *, semilla=1, max_pasos=None):
    """Prepara, juega y cierra la misión actual de la campaña con Zeus dirigiendo."""
    mision = correr(zeus.preparar_mision(estado))
    escenario, perfil = mision.escenario, zeus.perfil(estado)
    sim = SimuladorTexto(escenario, perfil, semilla=semilla)
    cerebros = preparar_cerebros(escenario, zeus.llm, perfil, Config(), zeus.memoria)
    director = DirectorPartida(zeus, sim, cerebros, escenario, perfil, cada_s=120, semilla=semilla)
    informe = correr(Partida(sim, cerebros, memoria=zeus.memoria, director=director, campana=perfil.id,
                             escenario=escenario.id, max_pasos=max_pasos).jugar())
    return escenario, sim, director, informe


# ---------------------------------------------------------------------------------- campaña
def test_detecta_epoca_y_misiones_del_encargo():
    assert detectar_perfil("una campaña en Normandía, segunda guerra mundial") == "ww2"
    assert detectar_perfil("guerrilla contra el ejército en las montañas") == "insurgencia"
    tipos = detectar_misiones("primero una emboscada a un convoy y luego rescatar a los pilotos")
    assert tipos[:2] == [TipoMision.EMBOSCADA, TipoMision.RESCATE]


def test_crear_campana_por_reglas(memoria):
    zeus, estado = nueva_campana(memoria, "campaña de la segunda guerra mundial: emboscada a un convoy y "
                                          "después defender un pueblo")
    plan = estado.plan
    assert plan.perfil == "ww2"
    assert 3 <= len(plan.capitulos) <= 6
    assert [c.tipo for c in plan.capitulos[:2]] == [TipoMision.EMBOSCADA, TipoMision.DEFENSA]
    assert len(estado.vivos()) >= 2 and plan.antagonista is not None
    # Queda guardada y se puede recuperar.
    assert zeus.cargar(estado.id).plan.nombre == plan.nombre
    assert estado.id in [c["id"] for c in memoria.campanas()]


def test_campana_desde_el_llm(memoria):
    plan = {
        "nombre": "Operación Tormenta", "premisa": "Recuperar el valle.", "perfil": "Guerra moderna",
        "nombre_jugadores": "Fuerza Azul", "nombre_enemigo": "Brigada Roja",
        "capitulos": [{"titulo": "Primer golpe", "sinopsis": "Tomar el cruce.", "tipo": "Asaltar"},
                      {"titulo": "Aguantar", "sinopsis": "Resistir el contraataque.", "tipo": "defensa"},
                      {"titulo": "Ojos", "sinopsis": "Ver qué hay detrás.", "tipo": "explorar"}],
        "personajes": [{"nombre": "Ana Ruiz", "rango": "Sargento", "rasgos": ["Veterana", "valiente"]}],
    }
    llm = LLMFalso(respuestas=[plan])
    _, estado = nueva_campana(memoria, "lo que quieras", llm=llm)
    assert estado.plan.nombre == "Operación Tormenta" and estado.plan.perfil == "moderno"
    assert [c.tipo for c in estado.plan.capitulos] == [TipoMision.ASALTO, TipoMision.DEFENSA,
                                                       TipoMision.RECONOCIMIENTO]
    ana = estado.vivos()[0]
    assert ana.nombre == "Ana Ruiz" and ana.rasgos == [Rasgo.VETERANO, Rasgo.VALIENTE]
    assert len(estado.vivos()) >= 2  # Zeus completa el reparto si el modelo se queda corto
    assert llm.llamadas_de("zeus")


@pytest.mark.parametrize("tipo", list(TipoMision))
def test_cada_tipo_de_mision_da_un_escenario_jugable(memoria, tipo):
    zeus, estado = nueva_campana(memoria, f"campaña moderna de {tipo.value}")
    estado.plan.capitulos[0].tipo = tipo
    mision = correr(zeus.preparar_mision(estado))
    esc = mision.escenario
    assert esc.bando_jugadores == "blufor" and esc.briefing
    assert set(esc.bandos_ia) == set(esc.bandos)  # Zeus controla a todos los bandos
    jugadores, enemigo = esc.bandos["blufor"], esc.bandos["opfor"]
    assert jugadores.objetivos and enemigo.objetivos  # el enemigo también tiene su propio plan
    assert all(u.orden is not None for b in esc.bandos.values() for u in b.unidades)
    assert any(u.persona is not None for b in esc.bandos.values() for u in b.unidades)
    *_, informe = jugar(zeus, estado, max_pasos=40)
    assert informe.pasos > 0


def test_campana_completa_con_zeus_dirigiendo(memoria):
    zeus, estado = nueva_campana(memoria, "insurgencia: emboscada y luego asalto", semilla=2)
    capitulos = len(estado.plan.capitulos)
    escenario, sim, _, informe = jugar(zeus, estado, semilla=2)
    resultado = informe.resultado
    assert sim.terminado()
    # El briefing lo cuenta Zeus y no lo trata el enlace como si fuera un jugador.
    assert sim.anuncios and sim.anuncios[0].autor == "Zeus" and sim.anuncios[0].texto.startswith(escenario.nombre)
    assert not any("Zeus, aquí" in m.texto for m in sim.chat_log)
    cambios = correr(zeus.cerrar_mision(estado, escenario, resultado, mision_id=informe.mision_id))
    assert cambios
    assert estado.historial[0].estado == resultado.estado
    if resultado.estado == "derrota":
        assert len(estado.plan.capitulos) == capitulos + 1 and estado.dificultad < 1.0
    # Zeus aprende como director de juego.
    assert any(lec.rol == "zeus" for lec in memoria.lecciones())
    # La campaña sigue donde se dejó.
    assert zeus.cargar(estado.id).capitulo == 1


def test_registrar_muertes_recuerdos_reclutas_y_dificultad(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna")
    estado.plan.capitulos[0].tipo = TipoMision.ASALTO
    mision = correr(zeus.preparar_mision(estado))
    esc = mision.escenario
    con_persona = [u for u in esc.bandos["blufor"].unidades if u.persona is not None]
    caido, superviviente = con_persona[0], (con_persona[1] if len(con_persona) > 1 else None)
    metricas = {caido.id: {"destruida": True, "efectivos_iniciales": 6, "efectivos_finales": 0}}
    if superviviente:
        metricas[superviviente.id] = {"efectivos_iniciales": 6, "efectivos_finales": 4}
    resultado = ResultadoMision(estado="victoria", motivo="", duracion_s=900, objetivos_completados=1,
                                objetivos_totales=1, efectivos_iniciales=12, bajas_propias=8, bajas_enemigas=10,
                                puntuacion=0.9, metricas={"unidades": metricas})
    vivos_antes = len(estado.vivos())
    cambios = estado.registrar(esc, resultado, semilla=1)
    assert any("ha caído" in c for c in cambios)
    assert len(estado.vivos()) >= 2 and len(estado.vivos()) >= vivos_antes - 1
    assert estado.dificultad > 1.0
    if superviviente:
        persona = next(p.persona for p in estado.personajes if p.persona.nombre == superviviente.persona.nombre)
        assert "perdiste a 2" in persona.recuerdos[-1]
        assert "Recuerdas" in persona.presentacion()
    copia = EstadoCampana.model_validate_json(estado.model_dump_json())
    assert copia == estado


# ---------------------------------------------------------------------------------- construcción
def test_construir_repara_planes_malos():
    perfil = cargar_perfil("ww2")
    plan = PlanMision.model_validate({
        "nombre": "Prueba", "briefing": "Hola", "hora": "de noche", "clima": "tormenta", "duracion_min": "500",
        "lugares": [{"nombre": "Molino", "tipo": "aldea", "posicion": "020020"}],
        "bandos": [
            {"bando": "azul", "unidades": [
                {"indicativo": "Able", "posicion": "Molino", "tarea": "asaltar"},
                {"indicativo": "Able", "posicion": "en ningún sitio", "tarea": "patrulla"},
                {"indicativo": "Pájaro", "tipo": "heli", "posicion": "Molino"},
            ]},
            {"bando": "rojo", "unidades": [{"indicativo": "Anton", "posicion": "030030", "tarea": "defender"}],
             "objetivos": [{"nombre": "Aguantar", "tipo": "aguantar", "lugar": "Molino"}]},
            {"bando": "azul", "unidades": []},
        ],
    })
    assert plan.hora == "noche" and plan.clima == "lluvia" and plan.duracion_min == 180
    construida = construir_escenario(plan, perfil, id_mision="prueba", semilla=1)
    esc = construida.escenario
    assert set(esc.bandos) == {"blufor", "opfor"}
    ids = [u.id for u in esc.bandos["blufor"].unidades]
    assert len(ids) == len(set(ids))
    assert all(u.tipo != TipoUnidad.HELICOPTERO for u in esc.bandos["blufor"].unidades)  # ww2: sin helicópteros
    assert esc.bandos["blufor"].objetivos  # se inventa un objetivo para los jugadores
    assert esc.condiciones.visibilidad_m < 1000
    assert construida.avisos


def test_escenario_generado_se_guarda_y_se_carga(memoria, tmp_path):
    zeus, estado = nueva_campana(memoria, "guerra fría, reconocimiento")
    esc = correr(zeus.preparar_mision(estado)).escenario
    ruta = tmp_path / "mision.yaml"
    guardar_escenario(esc, ruta)
    copia = cargar_escenario(ruta)
    assert copia.bandos.keys() == esc.bandos.keys()
    assert [u.persona for u in copia.bandos["blufor"].unidades] == [u.persona for u in esc.bandos["blufor"].unidades]


# ---------------------------------------------------------------------------------- en directo
def test_ordenes_de_zeus_por_reglas():
    sim = SimuladorTexto(cargar_escenario("asalto_aldea"), cargar_perfil("moderno"), semilla=1)
    vista = sim.vista_global()
    lluvia = orden_por_reglas("que llueva y se haga de noche", vista)
    assert {a.tipo for a in lluvia.acciones} == {TipoIntervencion.CLIMA, TipoIntervencion.HORA}
    refuerzos = orden_por_reglas("manda 2 tanques enemigos", vista)
    accion = refuerzos.acciones[0]
    assert accion.tipo == TipoIntervencion.REFUERZOS and accion.bando != vista.bando_jugadores
    assert accion.unidad is not None and accion.unidad.tipo == TipoUnidad.BLINDADO
    aviso = orden_por_reglas("anuncia que llega la artillería", vista)
    assert aviso.acciones[0].tipo == TipoIntervencion.MENSAJE


def test_poderes_de_zeus_en_el_simulador():
    sim = SimuladorTexto(cargar_escenario("asalto_aldea"), cargar_perfil("moderno"), semilla=1)
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


def test_el_anfitrion_habla_con_zeus_por_el_chat(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna de defensa")
    esc = correr(zeus.preparar_mision(estado)).escenario
    perfil = zeus.perfil(estado)
    sim = SimuladorTexto(esc, perfil, semilla=1)
    cerebros = preparar_cerebros(esc, zeus.llm, perfil, Config(), memoria)
    director = DirectorPartida(zeus, sim, cerebros, esc, perfil, semilla=1)
    grupos = len(sim.unidades)
    mensajes = [MensajeChat(autor="Jugador", texto="Zeus, manda refuerzos enemigos"),
                MensajeChat(autor="Jugador", texto="Alfa, ¿cómo vais?")]
    resto = correr(director.filtrar_chat(mensajes))
    assert [m.texto for m in resto] == ["Alfa, ¿cómo vais?"]
    assert len(sim.unidades) == grupos + 1
    assert sim.anuncios[-1].autor == "Zeus" and "refuerzos" in sim.anuncios[-1].texto
    # El grupo nuevo tiene cerebro: el bando enemigo lo conoce y le da una orden.
    nuevo = next(u for u in sim.unidades.values() if u.id not in {x.id for b in esc.bandos.values()
                                                                  for x in b.unidades})
    assert nuevo.id in cerebros[nuevo.bando].ordenes_iniciales or nuevo.id in cerebros[nuevo.bando].unidades


def test_zeus_complica_la_partida_si_los_jugadores_van_sobrados(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna de defensa")
    esc = correr(zeus.preparar_mision(estado)).escenario
    perfil = zeus.perfil(estado)
    sim = SimuladorTexto(esc, perfil, semilla=1)
    for u in sim.unidades.values():
        if u.bando != esc.bando_jugadores:
            u.efectivos = 1
    cerebros = preparar_cerebros(esc, zeus.llm, perfil, Config(), memoria)
    director = DirectorPartida(zeus, sim, cerebros, esc, perfil, cada_s=0, semilla=1)
    hechas = correr(director.supervisar())
    assert any("refuerzos" in h for h in hechas)


def test_intervencion_del_llm_valida_y_tolerante():
    datos = {"razon": "más tensión", "acciones": [
        {"tipo": "Refuerzo", "bando": "rojo", "unidad": {"indicativo": "X", "tipo": "Tanque", "posicion": "020020"}},
        {"tipo": "tiempo", "clima": "niebla"}, {"tipo": "bailar"}]}
    intervencion = IntervencionZeus.model_validate(datos)
    assert [a.tipo for a in intervencion.acciones] == [TipoIntervencion.REFUERZOS, TipoIntervencion.CLIMA,
                                                       TipoIntervencion.NADA]
    assert intervencion.acciones[0].bando == "opfor"
    assert intervencion.acciones[0].unidad.tipo == TipoUnidad.BLINDADO


# ---------------------------------------------------------------------------------- NPC
def test_rasgos_cambian_cuando_se_rompe_una_unidad(obs):
    from arma3_ia.agentes import reglas

    unidad = obs.unidades[0].model_copy(update={"efectivos": 3, "moral": 0.45})
    valiente = Persona(nombre="A", rasgos=["valiente", "veterano"])
    nervioso = Persona(nombre="B", rasgos=["nervioso", "novato"])
    assert not reglas.roto(unidad, valiente)
    assert reglas.roto(unidad, nervioso)
    assert Persona(nombre="C", rasgos=["valiente"] * 5 + ["inventado"]).rasgos == [Rasgo.VALIENTE]


def test_patrulla_y_emboscada(obs):
    from arma3_ia.agentes import reglas
    from arma3_ia.agentes.esquemas import OrdenMando, TareaMando

    unidad = obs.unidades[0]
    patrulla = OrdenMando(unidad=unidad.indicativo, tarea=TareaMando.PATRULLAR, destino="010010",
                          ruta=["030010", "030030"])
    sin_contacto = obs.model_copy(update={"contactos": []})
    decision = reglas.decision_lider(unidad, patrulla, sin_contacto)
    assert decision.accion.value == "mover"
    emboscada = OrdenMando(unidad=unidad.indicativo, tarea=TareaMando.EMBOSCAR, destino=unidad.posicion.cuadricula())
    espera = reglas.decision_lider(unidad, emboscada, sin_contacto)
    assert espera.accion.value == "defender" and espera.postura.value == "sigilo"
    cerca = obs.contactos[0].model_copy(update={"posicion": unidad.posicion.desplazar(0, 250)})
    fuego = reglas.decision_lider(unidad, emboscada, obs.model_copy(update={"contactos": [cerca]}))
    assert fuego.accion.value == "atacar"


def test_hablar_con_un_npc_por_su_nombre(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna de asalto")
    esc = correr(zeus.preparar_mision(estado)).escenario
    perfil = zeus.perfil(estado)
    sim = SimuladorTexto(esc, perfil, semilla=1)
    llm = LLMFalso()
    cerebros = preparar_cerebros(esc, llm, perfil, Config(), memoria)
    unidad = next(u for u in esc.bandos["blufor"].unidades if u.persona is not None and not u.jugadores)
    apellido = unidad.persona.nombre.split()[-1]
    sim.inyectar_chat("blufor", "Jugador", f"{apellido}, ¿cómo lo veis?")
    cerebro = cerebros["blufor"]
    obs = correr(sim.observar("blufor"))
    salida = correr(cerebro.paso(obs, correr(sim.recibir_chat("blufor"))))
    llamadas = llm.llamadas_de("npc")
    assert llamadas and unidad.persona.nombre in llamadas[0].sistema
    assert any(unidad.persona.nombre in m.autor for m in salida.chat)


def test_bando_sin_comandante_no_llama_al_mando(memoria):
    zeus, estado = nueva_campana(memoria, "campaña moderna")
    estado.plan.capitulos[0].tipo = TipoMision.EMBOSCADA  # emboscada: grupos independientes
    esc = correr(zeus.preparar_mision(estado)).escenario
    assert esc.bandos["blufor"].mando is False
    perfil = zeus.perfil(estado)
    llm = LLMFalso()
    sim = SimuladorTexto(esc, perfil, semilla=1)
    cerebros = preparar_cerebros(esc, llm, perfil, Config(), memoria)
    correr(Partida(sim, cerebros, max_pasos=6).jugar())
    propios = {u.id for u in esc.bandos["blufor"].unidades}
    sistemas = [ll.sistema for ll in llm.llamadas if ll.metadatos.get("agente") in propios]
    assert sistemas and all("No hay un mando por encima de ti" in s for s in sistemas)
    assert all(a.id != "mando" for a in cerebros["blufor"].agentes)


def test_el_enemigo_usa_su_propia_doctrina():
    perfil = cargar_perfil("ww2")
    enemigo = perfil.del_enemigo()
    assert enemigo.bando_ia.nombre == perfil.enemigo.nombre
    assert enemigo.enemigo.nombre == perfil.bando_ia.nombre
    assert enemigo.doctrina == perfil.enemigo.doctrina and enemigo.doctrina != perfil.doctrina
    assert enemigo.indicativo_mando == perfil.enemigo.indicativo_mando != perfil.indicativo_mando


def test_vehiculos_inmovilizados_no_se_mueven():
    sim = SimuladorTexto(cargar_escenario("emboscada_convoy"), cargar_perfil("moderno"), semilla=1)
    vehiculo = next(u for u in sim.unidades.values() if u.tipo == TipoUnidad.MOTORIZADA)
    vehiculo.danos = 0.6
    vehiculo.accion, vehiculo.destino = TipoAccion.MOVER, vehiculo.pos.desplazar(0, 1000)
    antes = vehiculo.pos
    sim._mover_tierra(vehiculo, 30)
    assert vehiculo.pos == antes


def test_rng_cambia_con_el_texto():
    from arma3_ia.zeus.reglas import rng_de

    assert rng_de("a", 1).random() != rng_de("b", 1).random()
    assert rng_de("a", 1).random() == rng_de("a", 1).random()
    assert json.dumps(Persona(nombre="X", historia="h" * 400).model_dump())  # la historia se recorta


def test_memoria_de_campanas_vacia():
    m = Memoria(":memory:")
    assert m.campanas() == [] and m.cargar_campana("nada") is None
    m.cerrar()


def test_rasgos_concuerdan_con_la_persona():
    leila = Persona(nombre="Leila Haddad", rasgos=["veterana", "nerviosa", "valiente"], femenina=True)
    assert leila.presentacion().startswith("Leila Haddad (veterana, nerviosa, valiente)")
    assert Persona(nombre="Joe", rasgos=["veterano"]).presentacion() == "Joe (veterano)"
