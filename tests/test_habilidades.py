import random

from arma3_ia.habilidades import (
    PARAMETROS_ATERRIZAJE,
    PARAMETROS_POR_DEFECTO,
    CondicionesAterrizaje,
    HabilidadAterrizaje,
    Habilidades,
    OptimizadorCEM,
    Parametro,
    evaluar,
    simular_aterrizaje,
)


def test_cem_encuentra_el_optimo_de_una_funcion_sencilla():
    parametros = [Parametro("x", 0, 10), Parametro("y", -5, 5)]
    cem = OptimizadorCEM(parametros, {"x": 1, "y": 4}, rng=random.Random(0))
    for _ in range(60 * cem.poblacion):
        valores = cem.proponer()
        cem.informar(valores, -((valores["x"] - 7) ** 2) - (valores["y"] + 2) ** 2)
    final = cem.actuales()
    assert abs(final["x"] - 7) < 0.5 and abs(final["y"] + 2) < 0.5
    assert cem.generacion == 60 and cem.historial[-1] > cem.historial[0]


def test_cem_guarda_y_restaura_estado():
    parametros = [Parametro("x", 0, 1)]
    a = OptimizadorCEM(parametros, rng=random.Random(1))
    for _ in range(a.poblacion * 3):
        a.informar(a.proponer(), random.random())
    b = OptimizadorCEM(parametros)
    b.restaurar(a.estado())
    assert b.actuales() == a.actuales() and b.generacion == 3
    b.restaurar({"media": [0.1, 0.2]})  # estado incompatible: se ignora
    assert b.actuales() == a.actuales()


def test_simulacion_de_aterrizaje_es_fisicamente_razonable():
    tranquilo = CondicionesAterrizaje(viento_ms=0, zona_caliente=False, noche=False)
    rng = random.Random(0)
    suave = {"velocidad_aproximacion": 10, "tasa_descenso": 1.5, "altura_recogida": 20, "intensidad_recogida": 0.6}
    brusco = {"velocidad_aproximacion": 40, "tasa_descenso": 8, "altura_recogida": 3, "intensidad_recogida": 0.1}
    assert all(simular_aterrizaje(suave, tranquilo, rng).exito for _ in range(50))
    assert sum(simular_aterrizaje(brusco, tranquilo, rng).resultado == "accidente" for _ in range(50)) > 25


def test_la_practica_mejora_el_aterrizaje(memoria):
    novato = {
        ctx: evaluar(PARAMETROS_POR_DEFECTO, ctx, 400, random.Random(5))["exito"] for ctx in (False, True)
    }
    habilidad = HabilidadAterrizaje(memoria, random.Random(3))
    progreso = habilidad.entrenar(640)
    assert progreso and {p["contexto"] for p in progreso} == {"fria", "caliente"}
    for caliente in (False, True):
        entrenado = evaluar(habilidad.parametros(caliente), caliente, 400, random.Random(5))["exito"]
        assert entrenado > novato[caliente] + 0.3, (caliente, novato[caliente], entrenado)
    # Lo aprendido persiste en la memoria y lo recupera otra instancia.
    otra = HabilidadAterrizaje(memoria)
    assert otra.parametros(True) == habilidad.parametros(True)
    assert otra.parametros(False) != PARAMETROS_POR_DEFECTO


def test_habilidades_aprende_de_aterrizajes_reales(memoria):
    habilidades = Habilidades(memoria, explorar=True, semilla=1)
    nombres = {p.nombre for p in PARAMETROS_ATERRIZAJE}
    propuesta = habilidades.parametros_aterrizaje(zona_caliente=False)
    assert set(propuesta) == nombres
    optimizador = habilidades.aterrizaje.optimizadores["fria"]
    habilidades.informar_aterrizaje({"parametros": propuesta, "zona_caliente": False, "puntuacion": 0.9})
    habilidades.informar_aterrizaje({"parametros": {}, "puntuacion": 0.1})  # incompleto: se ignora
    habilidades.informar_aterrizaje({"resultado": "exito"})
    assert optimizador.evaluaciones == 1 and optimizador.mejor is not None
    habilidades.guardar()
    assert memoria.cargar_habilidad(HabilidadAterrizaje.NOMBRE)["fria"]["evaluaciones"] == 1
    sin_explorar = Habilidades(memoria)
    assert sin_explorar.parametros_aterrizaje(False) == sin_explorar.parametros_aterrizaje(False)
