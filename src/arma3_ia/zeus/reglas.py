"""Zeus sin modelo: plantillas y reglas para crear campañas, misiones e intervenciones.

Se usa cuando el modelo no está disponible o falla (y con el LLM falso de las pruebas). El
resultado es más sencillo que el de la red, pero siempre jugable.
"""

from __future__ import annotations

import random
import re
import zlib

from ..agentes.esquemas import TareaMando
from ..geo import Posicion
from ..modelo import Persona, Rasgo, TipoObjetivo, TipoUnidad, VistaGlobal
from ..texto import normalizar
from .esquemas import (
    AccionZeus,
    BandoPlan,
    CapituloPlan,
    IntervencionZeus,
    LugarPlan,
    ObjetivoPlan,
    PlanCampana,
    PlanMision,
    TipoIntervencion,
    TipoMision,
    UnidadPlan,
)

NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
           "ocho": 8, "nueve": 9, "diez": 10}
CLAVES_PERFIL = (
    (("1944", "1943", "segunda guerra", "ww2", "normandia", "nazi", "aleman", "wehrmacht"), "ww2"),
    (("guerra fria", "sovietic", "pacto de varsovia", "1985", "1980", "urss"), "guerra_fria"),
    (("contrainsurgencia", "coin", "cazar insurgentes", "talib"), "contrainsurgencia"),
    (("insurgen", "guerrilla", "milicia", "resistencia", "ocupacion", "rebelde"), "insurgencia"),
)
CLAVES_MISION = (
    (("emboscada", "convoy", "emboscar"), TipoMision.EMBOSCADA),
    (("rescat", "extraer", "extraccion", "evacuar", "derribado", "rehen"), TipoMision.RESCATE),
    (("defender", "defensa", "resistir", "aguantar", "asedio"), TipoMision.DEFENSA),
    (("reconoc", "explorar", "infiltr", "espiar", "patrulla"), TipoMision.RECONOCIMIENTO),
    (("destruir", "sabot", "volar", "demoler", "artiller"), TipoMision.DESTRUCCION),
    (("asalt", "tomar", "atacar", "capturar", "conquistar", "liberar", "ofensiva"), TipoMision.ASALTO),
)
ARCO_POR_DEFECTO = (TipoMision.RECONOCIMIENTO, TipoMision.ASALTO, TipoMision.DEFENSA, TipoMision.EMBOSCADA,
                    TipoMision.RESCATE, TipoMision.DESTRUCCION)
TITULOS = {
    TipoMision.ASALTO: "Asalto a {lugar}",
    TipoMision.DEFENSA: "Resistir en {lugar}",
    TipoMision.EMBOSCADA: "Emboscada en {lugar}",
    TipoMision.RESCATE: "Sacarlos de {lugar}",
    TipoMision.RECONOCIMIENTO: "Ojos sobre {lugar}",
    TipoMision.DESTRUCCION: "Golpe en {lugar}",
}
SINOPSIS = {
    TipoMision.ASALTO: "Hay que expulsar al enemigo de {lugar} y hacerse con el control.",
    TipoMision.DEFENSA: "El enemigo viene a por {lugar}. Hay que aguantar hasta que lleguen refuerzos.",
    TipoMision.EMBOSCADA: "Un convoy enemigo pasará cerca de {lugar}. No debe llegar a su destino.",
    TipoMision.RESCATE: "Un grupo propio ha quedado aislado en {lugar}. Hay que traerlo de vuelta.",
    TipoMision.RECONOCIMIENTO: "Nadie sabe qué hay en {lugar}. Hay que mirar sin ser visto.",
    TipoMision.DESTRUCCION: "En {lugar} hay algo que el enemigo necesita. Hay que destruirlo.",
}
LUGARES = {
    "ww2": ["Saint-Aubin", "La Croix", "Le Mesnil", "Bois du Loup", "Granja Lefèvre", "Cota 112", "Puente de Orne"],
    "guerra_fria": ["Grünbach", "Hohenfeld", "Paso de Fulda", "Waldheim", "Cota 410", "Molino Viejo"],
    "insurgencia": ["Qasr Ahmar", "Wadi Sur", "Pozo de Nur", "Kalat", "Paso de las Cabras", "Aldea Blanca"],
    "contrainsurgencia": ["Sarab", "Qal'at Nur", "Barranco Seco", "Cerro Pelado", "Mercado de Dahan"],
    "moderno": ["Villanueva", "Aldea Vieja", "Colina 214", "Bosque Negro", "Cruce del Molino", "Valdecierva"],
}
NOMBRES = {
    ("ww2", "jugadores"): (["Joe", "Frank", "Tom", "Eddie", "Walt", "Harry"],
                           ["Miller", "Kowalski", "O'Brien", "Russo", "Hayes", "Murphy"],
                           ["Sargento", "Cabo", "Teniente"]),
    ("ww2", "enemigo"): (["Hans", "Karl", "Otto", "Fritz", "Ernst"],
                         ["Becker", "Krause", "Wolf", "Hartmann", "Vogel"], ["Feldwebel", "Unteroffizier", "Leutnant"]),
    ("guerra_fria", "enemigo"): (["Iván", "Dmitri", "Serguéi", "Yuri", "Oleg"],
                                 ["Volkov", "Petrov", "Sokolov", "Orlov", "Kuznetsov"], ["Sargento", "Teniente"]),
    ("contrainsurgencia", "enemigo"): (["Omar", "Yusuf", "Tariq", "Samir", "Jalil"],
                                       ["Haddad", "Nasser", "Karimi", "Rahman"], ["", "Jefe de célula"]),
    ("insurgencia", "jugadores"): (["Omar", "Leila", "Tariq", "Samira", "Jalil"],
                                   ["Haddad", "Nasser", "Karimi", "Rahman"], ["", "Jefe de célula"]),
}
NOMBRES_GENERICOS = (["Javier", "Lucía", "Andrés", "Marta", "Pablo", "Elena", "Diego", "Sara"],
                     ["Ruiz", "Ortega", "Navarro", "Vidal", "Castillo", "Serrano", "Molina", "Iglesias"],
                     ["Sargento", "Cabo", "Teniente", "Sargento primero"])
NOMBRES_FEMENINOS = {"Leila", "Samira", "Lucía", "Marta", "Elena", "Sara"}
HISTORIAS = [
    "Lleva tres despliegues a sus espaldas y no se fía de nadie que no haya visto combatir.",
    "Es su primera misión al mando y quiere demostrar que vale.",
    "Perdió a un amigo en la última operación y no piensa repetirlo.",
    "Conoce la zona porque creció cerca; habla poco y observa mucho.",
    "Tiene fama de cabezota, pero su gente le seguiría a cualquier parte.",
    "Antes formaba a reclutas; lo hace todo según el manual.",
]


def rng_de(texto: str, semilla: int | None = None) -> random.Random:
    """Aleatoriedad repetible: el mismo texto (y la misma semilla) dan siempre el mismo resultado."""
    return random.Random(zlib.crc32(f"{semilla}|{texto}".encode()))


def detectar_perfil(texto: str, disponibles: list[str] | None = None) -> str:
    norm = normalizar(texto)
    for claves, perfil in CLAVES_PERFIL:
        if any(c in norm for c in claves) and (disponibles is None or perfil in disponibles):
            return perfil
    return "moderno"


def detectar_misiones(texto: str) -> list[TipoMision]:
    norm = normalizar(texto)
    encontradas: list[tuple[int, TipoMision]] = []
    for claves, tipo in CLAVES_MISION:
        posiciones = [norm.find(c) for c in claves if c in norm]
        if posiciones:
            encontradas.append((min(posiciones), tipo))
    return [t for _, t in sorted(encontradas)]


def numero_antes(texto: str, palabras: tuple[str, ...]) -> int | None:
    """'tres escuadras' -> 3; '4 helicopteros' -> 4."""
    norm = normalizar(texto)
    for palabra in palabras:
        m = re.search(rf"(\d+|{'|'.join(NUMEROS)})\s+{palabra}", norm)
        if m:
            valor = m.group(1)
            return int(valor) if valor.isdigit() else NUMEROS[valor]
    return None


def crear_persona(rng: random.Random, perfil: str, lado: str, usados: set[str] | None = None) -> Persona:
    nombres, apellidos, rangos = NOMBRES.get((perfil, lado), NOMBRES_GENERICOS)
    usados = usados if usados is not None else set()
    primeros = {u.split()[0] for u in usados if u}
    for _ in range(20):
        nombre = f"{rng.choice(nombres)} {rng.choice(apellidos)}"
        if nombre not in usados and nombre.split()[0] not in primeros:
            break
    usados.add(nombre)
    rasgos = rng.sample(list(Rasgo), k=2)
    if Rasgo.VETERANO in rasgos and Rasgo.NOVATO in rasgos:
        rasgos.remove(Rasgo.NOVATO)
    return Persona(nombre=nombre, rango=rng.choice(rangos), rasgos=rasgos, historia=rng.choice(HISTORIAS),
                   femenina=nombre.split()[0] in NOMBRES_FEMENINOS)


# ---------------------------------------------------------------------------------- campaña
def campana_por_reglas(encargo: str, perfiles: dict[str, tuple[str, str]], semilla: int | None = None) -> PlanCampana:
    """`perfiles`: id -> (nombre del bando de los jugadores, nombre del enemigo)."""
    rng = rng_de(encargo, semilla)
    perfil = detectar_perfil(encargo, list(perfiles))
    jugadores, enemigo = perfiles.get(perfil, ("Fuerzas propias", "Fuerzas hostiles"))
    pedidas = detectar_misiones(encargo)
    n = numero_antes(encargo, ("misiones", "capitulos", "operaciones", "partes")) or max(4, len(pedidas))
    n = max(3, min(6, n))
    tipos = list(pedidas)
    for tipo in ARCO_POR_DEFECTO:
        if len(tipos) >= n:
            break
        if tipo not in tipos:
            tipos.append(tipo)
    while len(tipos) < n:
        tipos.append(rng.choice(ARCO_POR_DEFECTO))
    lugares = list(LUGARES.get(perfil, LUGARES["moderno"]))
    rng.shuffle(lugares)
    capitulos = [
        CapituloPlan(titulo=TITULOS[t].format(lugar=lugares[i % len(lugares)]),
                     sinopsis=SINOPSIS[t].format(lugar=lugares[i % len(lugares)]), tipo=t)
        for i, t in enumerate(tipos[:n])
    ]
    usados: set[str] = set()
    personajes = [crear_persona(rng, perfil, "jugadores", usados) for _ in range(3)]
    antagonista = crear_persona(rng, perfil, "enemigo", usados)
    antagonista.rango = antagonista.rango or "Comandante"
    encargo_limpio = encargo.strip().rstrip(".")
    return PlanCampana(
        nombre=f"Operación {lugares[0]}" if perfil != "insurgencia" else f"La lucha por {lugares[0]}",
        premisa=f"{encargo_limpio[:1].upper()}{encargo_limpio[1:]}. {jugadores} contra {enemigo}.",
        perfil=perfil,
        nombre_jugadores=jugadores,
        nombre_enemigo=enemigo,
        capitulos=capitulos,
        personajes=personajes,
        antagonista=antagonista,
    )


# ---------------------------------------------------------------------------------- misión
def mision_por_reglas(
    campana: PlanCampana,
    capitulo: CapituloPlan,
    *,
    indicativos_propios: list[str],
    indicativos_enemigos: list[str],
    personajes: list[Persona],
    antagonista: Persona | None,
    medios_aereos: bool,
    dificultad: float = 1.0,
    semilla: int | None = None,
) -> PlanMision:
    rng = rng_de(capitulo.titulo + campana.nombre, semilla)
    texto = f"{capitulo.titulo} {capitulo.sinopsis}"
    lugares_campana = list(LUGARES.get(campana.perfil, LUGARES["moderno"]))
    lugar_obj = next((lg for lg in lugares_campana if normalizar(lg) in normalizar(texto)), lugares_campana[0])
    otros = [lg for lg in lugares_campana if lg != lugar_obj]
    rng.shuffle(otros)

    obj = Posicion(x=rng.uniform(2300, 2900), y=rng.uniform(2400, 3000))
    salida = Posicion(x=rng.uniform(500, 900), y=rng.uniform(500, 900))
    medio = Posicion(x=(obj.x + salida.x) / 2 + rng.uniform(-300, 300), y=(obj.y + salida.y) / 2)
    lugares = [
        LugarPlan(nombre=lugar_obj, tipo="urbano", posicion=obj.cuadricula(), radio_m=250),
        LugarPlan(nombre=otros[0], tipo="bosque", posicion=medio.cuadricula(), radio_m=350),
        LugarPlan(nombre=otros[1], tipo="colina", posicion=obj.desplazar(rng.uniform(250, 340), 800).cuadricula()),
    ]
    if medios_aereos:
        lugares.append(LugarPlan(nombre="LZ Norte", tipo="lz", posicion=obj.desplazar(0, 900).cuadricula()))

    def ind_propio(i: int) -> str:
        return indicativos_propios[i] if i < len(indicativos_propios) else f"Grupo-{i + 1}"

    def ind_enemigo(i: int) -> str:
        return indicativos_enemigos[i] if i < len(indicativos_enemigos) else f"Hostil-{i + 1}"

    lideres = list(personajes)
    propias: list[UnidadPlan] = [
        UnidadPlan(indicativo="Jugadores", efectivos=6, posicion=salida.cuadricula(), tarea=TareaMando.ESPERAR,
                   jugadores=True),
    ]
    n_enemigos = max(2, round(3 * dificultad))
    fuerza = max(3, round(5 * dificultad))
    enemigas: list[UnidadPlan] = []
    objetivos_j: list[ObjetivoPlan] = []
    usados: set[str] = {p.nombre for p in personajes}

    def lider_enemigo() -> Persona:
        return crear_persona(rng, campana.perfil, "enemigo", usados)

    tipo = capitulo.tipo
    if tipo == TipoMision.DEFENSA:
        propias[0] = propias[0].model_copy(update={"posicion": lugar_obj, "tarea": TareaMando.DEFENDER,
                                                   "destino": lugar_obj})
        for i, lider in enumerate(lideres[:2]):
            propias.append(UnidadPlan(indicativo=ind_propio(i), efectivos=7, posicion=lugar_obj,
                                      tarea=TareaMando.DEFENDER, destino=lugar_obj, lider=lider))
        origen = obj.desplazar(45, 1300)
        for i in range(n_enemigos + 1):
            enemigas.append(UnidadPlan(indicativo=ind_enemigo(i), efectivos=fuerza + 1,
                                       posicion=origen.desplazar(rng.uniform(0, 360), 250).cuadricula(),
                                       tarea=TareaMando.ATACAR, destino=lugar_obj, lider=lider_enemigo()))
        objetivos_j.append(ObjetivoPlan(nombre=f"Defender {lugar_obj}", tipo=TipoObjetivo.DEFENDER, lugar=lugar_obj))
    elif tipo == TipoMision.EMBOSCADA:
        emboscada = obj.desplazar(270, 500)
        # Los emboscados empiezan ya en sus puestos, a pocos metros de la carretera: la tensión
        # está en esperar al convoy sin ser vistos, no en llegar a tiempo.
        puesto = emboscada.desplazar(180, 100)
        propias[0] = propias[0].model_copy(update={"posicion": puesto.cuadricula(), "tarea": TareaMando.EMBOSCAR,
                                                   "destino": puesto.cuadricula()})
        for i, lider in enumerate(lideres[:1]):
            propias.append(UnidadPlan(indicativo=ind_propio(i), efectivos=7,
                                      posicion=puesto.desplazar(90, 300).cuadricula(),
                                      tarea=TareaMando.EMBOSCAR, destino=puesto.desplazar(90, 300).cuadricula(),
                                      lider=lider))
        inicio = emboscada.desplazar(90, 2000).limitar(3800, 3800)
        fin = emboscada.desplazar(270, 1500).limitar(3800, 3800)
        convoy = []
        for i in range(max(2, n_enemigos - 1)):
            indicativo = ind_enemigo(i)
            convoy.append(indicativo)
            enemigas.append(UnidadPlan(indicativo=indicativo, tipo=TipoUnidad.MOTORIZADA, efectivos=4,
                                       posicion=inicio.desplazar(90, 80 * i).cuadricula(), tarea=TareaMando.MOVER,
                                       destino=fin.cuadricula(), lider=lider_enemigo()))
        objetivos_j.append(ObjetivoPlan(nombre="Destruir el convoy", tipo=TipoObjetivo.DESTRUIR,
                                        lugar=emboscada.cuadricula(), unidades=convoy))
    elif tipo == TipoMision.RESCATE:
        aislado = ind_propio(0)
        jefe = lideres[0] if lideres else None
        propias.append(UnidadPlan(indicativo=aislado, efectivos=4, posicion=lugar_obj, tarea=TareaMando.DEFENDER,
                                  destino=lugar_obj, lider=jefe))
        for i in range(n_enemigos):
            enemigas.append(UnidadPlan(indicativo=ind_enemigo(i), efectivos=fuerza,
                                       posicion=obj.desplazar(rng.uniform(0, 360), 600).cuadricula(),
                                       tarea=TareaMando.ATACAR if i == 0 else TareaMando.PATRULLAR,
                                       destino=lugar_obj, lider=lider_enemigo()))
        objetivos_j.append(ObjetivoPlan(nombre=f"Traer a {aislado} a casa", tipo=TipoObjetivo.EXTRAER,
                                        lugar=salida.cuadricula(), unidades=[aislado]))
        if medios_aereos:
            propias.append(UnidadPlan(indicativo="Halcón-1", tipo=TipoUnidad.HELICOPTERO, efectivos=4,
                                      posicion=salida.cuadricula(), tarea=TareaMando.ESPERAR))
    elif tipo == TipoMision.RECONOCIMIENTO:
        # Un reconocimiento empieza más cerca: a pie y en sigilo se avanza despacio.
        cerca = obj.desplazar(225, 1300)
        propias[0] = propias[0].model_copy(update={"tarea": TareaMando.RECONOCER, "destino": lugar_obj,
                                                   "posicion": cerca.cuadricula()})
        for i in range(n_enemigos):
            enemigas.append(UnidadPlan(indicativo=ind_enemigo(i), efectivos=fuerza,
                                       posicion=obj.desplazar(rng.uniform(0, 360), 300).cuadricula(),
                                       tarea=TareaMando.PATRULLAR if i % 2 else TareaMando.DEFENDER,
                                       destino=lugar_obj, lider=lider_enemigo()))
        objetivos_j.append(ObjetivoPlan(nombre=f"Reconocer {lugar_obj}", tipo=TipoObjetivo.RECONOCER,
                                        lugar=lugar_obj))
    else:  # asalto y destrucción
        propias[0] = propias[0].model_copy(update={"tarea": TareaMando.ATACAR, "destino": lugar_obj})
        for i, lider in enumerate(lideres[:2]):
            propias.append(UnidadPlan(indicativo=ind_propio(i), efectivos=7,
                                      posicion=salida.desplazar(rng.uniform(0, 90), 250).cuadricula(),
                                      tarea=TareaMando.RECONOCER if i == 0 else TareaMando.ATACAR,
                                      destino=lugar_obj, lider=lider))
        guardia = []
        for i in range(n_enemigos):
            indicativo = ind_enemigo(i)
            guardia.append(indicativo)
            # Quien defiende un pueblo tiene ventaja: la guarnición es más pequeña que el atacante.
            enemigas.append(UnidadPlan(indicativo=indicativo, efectivos=max(3, fuerza - 1),
                                       posicion=obj.desplazar(rng.uniform(0, 360), 120 * i).cuadricula(),
                                       tarea=TareaMando.DEFENDER if i < 2 else TareaMando.PATRULLAR,
                                       destino=lugar_obj, lider=lider_enemigo()))
        if tipo == TipoMision.DESTRUCCION:
            objetivos_j.append(ObjetivoPlan(nombre=f"Destruir la guarnición de {lugar_obj}",
                                            tipo=TipoObjetivo.DESTRUIR, lugar=lugar_obj, unidades=guardia[:2]))
        else:
            objetivos_j.append(ObjetivoPlan(nombre=f"Tomar {lugar_obj}", tipo=TipoObjetivo.CAPTURAR, lugar=lugar_obj))
    if dificultad >= 1.2 and tipo != TipoMision.EMBOSCADA:
        enemigas.append(UnidadPlan(indicativo=ind_enemigo(len(enemigas)), tipo=TipoUnidad.MOTORIZADA, efectivos=4,
                                   posicion=obj.desplazar(45, 1500).cuadricula(), tarea=TareaMando.ESPERAR,
                                   lider=lider_enemigo()))

    # Una guerrilla (el enemigo en contrainsurgencia) actúa por células, sin mando central.
    mando_enemigo = campana.perfil != "contrainsurgencia"
    hora = rng.choices(["dia", "amanecer", "atardecer", "noche"], weights=[5, 2, 2, 1])[0]
    clima = rng.choices(["despejado", "nubes", "lluvia", "niebla"], weights=[5, 3, 1, 1])[0]
    for palabra in ("noche", "amanecer", "atardecer"):
        if palabra in normalizar(texto):
            hora = palabra
    return PlanMision(
        nombre=capitulo.titulo,
        briefing=capitulo.sinopsis,
        hora=hora,
        clima=clima,
        duracion_min=60,
        tamano_km=4,
        lugares=lugares,
        bandos=[
            # El mando aliado es un NPC más (el cuartel general con el que hablan los jugadores). En
            # golpes de mano pequeños (emboscada, reconocimiento) cada grupo va por su cuenta.
            BandoPlan(bando="blufor", nombre=campana.nombre_jugadores,
                      mando=tipo not in (TipoMision.EMBOSCADA, TipoMision.RECONOCIMIENTO), unidades=propias,
                      objetivos=objetivos_j, base=salida.cuadricula()),
            BandoPlan(bando="opfor", nombre=campana.nombre_enemigo, mando=mando_enemigo,
                      comandante=antagonista, unidades=enemigas),
        ],
        bando_jugadores="blufor",
    )


# ---------------------------------------------------------------------------------- en directo
def intervencion_por_reglas(vista: VistaGlobal, historial: list[str]) -> IntervencionZeus:
    """Zeus como director de juego: mantiene la partida tensa pero ganable."""
    jugadores = vista.bando_jugadores or "blufor"
    enemigos = [b for b in vista.bandos() if b != jugadores]
    if not enemigos or vista.duracion_max_s is None:
        return IntervencionZeus(razon="Nada que hacer.")
    enemigo = enemigos[0]
    progreso = vista.tiempo / vista.duracion_max_s
    propios, propios_max = vista.fuerza(jugadores)
    hostiles, hostiles_max = vista.fuerza(enemigo)
    objetivo = next((o for o in vista.objetivos.get(jugadores, []) if o.estado.value == "pendiente"), None)
    hechas = " ".join(historial)

    if (hostiles_max and hostiles / hostiles_max < 0.35 and progreso < 0.6 and objetivo is not None
            and hechas.count("refuerzos enemigos") < 2):
        punto = objetivo.posicion.desplazar(45, 900)
        return IntervencionZeus(razon="refuerzos enemigos: los jugadores van sobrados", acciones=[
            AccionZeus(tipo=TipoIntervencion.REFUERZOS, bando=enemigo, texto="Llegan refuerzos enemigos.",
                       unidad=UnidadPlan(indicativo="Refuerzo", tipo=TipoUnidad.MOTORIZADA, efectivos=5,
                                         posicion=punto.cuadricula(), tarea=TareaMando.ATACAR,
                                         destino=objetivo.posicion.cuadricula())),
            AccionZeus(tipo=TipoIntervencion.MENSAJE, texto="Se oyen motores acercándose desde el noreste..."),
        ])
    if propios_max and propios / propios_max < 0.4 and "refuerzos aliados" not in hechas:
        base = next((g.posicion for g in vista.grupos if g.bando == jugadores), None)
        if base is not None:
            return IntervencionZeus(razon="refuerzos aliados: los jugadores están al límite", acciones=[
                AccionZeus(tipo=TipoIntervencion.REFUERZOS, bando=jugadores, texto="Llega un grupo de apoyo.",
                           unidad=UnidadPlan(indicativo="Apoyo", efectivos=6, posicion=base.cuadricula(),
                                             tarea=TareaMando.ATACAR if objetivo else TareaMando.DEFENDER,
                                             destino=objetivo.posicion.cuadricula() if objetivo else None)),
                AccionZeus(tipo=TipoIntervencion.MENSAJE, texto="Aguantad, os mandamos apoyo."),
            ])
    if progreso >= 0.5 and vista.condiciones.hora == "atardecer" and "cae la noche" not in hechas:
        return IntervencionZeus(razon="cae la noche", acciones=[
            AccionZeus(tipo=TipoIntervencion.HORA, hora="noche"),
            AccionZeus(tipo=TipoIntervencion.MENSAJE, texto="Se hace de noche. La visibilidad cae."),
        ])
    return IntervencionZeus(razon="La partida va bien: no intervengo.")


def orden_por_reglas(texto: str, vista: VistaGlobal) -> IntervencionZeus:
    """Entiende órdenes sencillas que el jugador da a Zeus por el chat."""
    norm = normalizar(texto)
    acciones: list[AccionZeus] = []
    for palabra in ("noche", "amanecer", "atardecer", "dia"):
        if re.search(rf"\b{palabra}\b", norm):
            acciones.append(AccionZeus(tipo=TipoIntervencion.HORA, hora=palabra))
            break
    for palabra, clima in (("lluvia", "lluvia"), ("llueva", "lluvia"), ("niebla", "niebla"),
                           ("despej", "despejado"), ("nubla", "nubes")):
        if palabra in norm:
            acciones.append(AccionZeus(tipo=TipoIntervencion.CLIMA, clima=clima))
            break
    if any(p in norm for p in ("refuerz", "manda", "envia", "aparezc", "spawn", "crea")):
        jugadores = vista.bando_jugadores or "blufor"
        aliados = any(p in norm for p in ("aliad", "amig", "nuestr", "apoyo para nosotros"))
        bando = jugadores if aliados else next((b for b in vista.bandos() if b != jugadores), "opfor")
        tipo = TipoUnidad.INFANTERIA
        for palabra, t in (("blindad", TipoUnidad.BLINDADO), ("tanque", TipoUnidad.BLINDADO),
                           ("vehicul", TipoUnidad.MOTORIZADA), ("tecnica", TipoUnidad.MOTORIZADA),
                           ("helicoptero", TipoUnidad.HELICOPTERO)):
            if palabra in norm:
                tipo = t
        cantidad = numero_antes(texto, ("grupos", "escuadras", "blindados", "tanques", "vehiculos", "tecnicas",
                                        "helicopteros", "equipos")) or 1
        objetivo = next((o for o in vista.objetivos.get(vista.bando_jugadores or "blufor", [])), None)
        destino = objetivo.posicion if objetivo else None
        lugar = None
        for g in vista.grupos:
            if normalizar(g.indicativo) in norm:
                lugar = g.posicion
        centro = lugar or destino or Posicion(x=2000, y=2000)
        for i in range(min(cantidad, 3)):
            pos = centro.desplazar(45 + 30 * i, 900)
            acciones.append(AccionZeus(
                tipo=TipoIntervencion.REFUERZOS, bando=bando,
                unidad=UnidadPlan(indicativo="Refuerzo", tipo=tipo, efectivos=4 if tipo != TipoUnidad.INFANTERIA else 6,
                                  posicion=pos.cuadricula(), tarea=TareaMando.ATACAR,
                                  destino=(destino or centro).cuadricula())))
    m = re.search(r"(?:anuncia|di|diles|avisa)(?:\s+a\s+los\s+jugadores)?\s*(?:que)?\s*[:,]?\s*(.+)", texto,
                  re.IGNORECASE)
    if m:
        acciones.append(AccionZeus(tipo=TipoIntervencion.MENSAJE, texto=m.group(1).strip()))
    if not acciones:
        return IntervencionZeus(razon="No he entendido la orden.", acciones=[
            AccionZeus(tipo=TipoIntervencion.NADA, texto="No te he entendido. Prueba con: noche, lluvia, "
                       "manda refuerzos enemigos, anuncia que...")])
    return IntervencionZeus(razon=f"Orden del director: {texto}", acciones=acciones[:3])
