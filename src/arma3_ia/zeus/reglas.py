"""Zeus sin modelo: reglas para crear ambientación, campaña, misiones e intervenciones.

Solo se usan cuando el modelo no está disponible o falla (y con el LLM falso de las pruebas).
No son campañas preparadas: todo sale del encargo del anfitrión (época, número de misiones,
duración) y de una semilla, así que el resultado es más sencillo que el de la red pero
siempre jugable y siempre distinto para cada encargo.
"""

from __future__ import annotations

import random
import re
import zlib

from ..agentes.esquemas import TareaMando
from ..ambientacion import PerfilCampana
from ..geo import Posicion
from ..modelo import Persona, Rasgo, ResultadoMision, TipoObjetivo, TipoObjeto, TipoUnidad, VistaGlobal
from ..texto import normalizar
from .esquemas import (
    AccionZeus,
    AmbientacionPlan,
    BandoAmbiente,
    BandoPlan,
    CapituloPlan,
    IntervencionZeus,
    LugarPlan,
    ObjetivoPlan,
    ObjetoPlan,
    PlanCampana,
    PlanMision,
    SucesoPlan,
    TipoIntervencion,
    TipoMision,
    UnidadPlan,
)
from .mapas import terreno_por_reglas

NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
           "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20}
_NUM = rf"(\d+(?:[.,]\d+)?|{'|'.join(NUMEROS)}|media)"
MAX_MISIONES = 30
DURACION_MIN, DURACION_MAX = 10, 600

CLAVES_ERA = (
    (("1944", "1943", "1942", "1941", "1940", "segunda guerra", "ww2", "normandia", "nazi", "wehrmacht"),
     "segunda_guerra_mundial"),
    (("guerra fria", "sovietic", "pacto de varsovia", "1985", "1980", "1970", "1960", "urss", "vietnam"),
     "guerra_fria"),
    (("futur", "ciencia ficcion", "2050", "2060", "2100"), "futura"),
)
CLAVES_IRREGULAR = ("insurgen", "yihad", "terroris", "talib", "guerrilla", "milicia", "rebelde", "cartel",
                    "narco", "celula", "resistencia", "partisano")
CLAVES_JUGADORES_IRREGULARES = ("somos la resistencia", "somos la guerrilla", "como guerrilla", "como insurgentes",
                                "jugamos como milicia", "liberar nuestro", "contra la ocupacion", "contra el invasor")
CLAVES_MISION = (
    (("emboscada", "convoy", "emboscar"), TipoMision.EMBOSCADA),
    (("rescat", "extraer", "extraccion", "evacuar", "derribado", "rehen"), TipoMision.RESCATE),
    (("defender", "defensa", "resistir", "aguantar", "asedio"), TipoMision.DEFENSA),
    (("reconoc", "explorar", "infiltr", "espiar", "patrulla"), TipoMision.RECONOCIMIENTO),
    (("destruir", "sabot", "volar", "demoler", "artiller"), TipoMision.DESTRUCCION),
    (("asalt", "tomar", "atacar", "capturar", "conquistar", "liberar", "ofensiva"), TipoMision.ASALTO),
)
# Pasos típicos para llegar a un gran objetivo: primero saber, luego preparar, por último golpear.
ARCO = (TipoMision.RECONOCIMIENTO, TipoMision.EMBOSCADA, TipoMision.RESCATE, TipoMision.DESTRUCCION,
        TipoMision.DEFENSA, TipoMision.ASALTO)
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
OBJETIVOS = {
    TipoMision.ASALTO: "Tomar {lugar} y mantenerlo",
    TipoMision.DEFENSA: "Que {lugar} siga en nuestras manos al acabar la misión",
    TipoMision.EMBOSCADA: "Destruir el convoy cerca de {lugar}",
    TipoMision.RESCATE: "Traer de vuelta al grupo aislado en {lugar}",
    TipoMision.RECONOCIMIENTO: "Averiguar qué hay en {lugar} sin ser descubiertos",
    TipoMision.DESTRUCCION: "Destruir la guarnición de {lugar}",
}
COMPLICACIONES = {
    TipoMision.ASALTO: ["El pueblo está fortificado y los accesos minados.", "Hay civiles atrapados en el objetivo.",
                        "El enemigo espera refuerzos motorizados en cualquier momento."],
    TipoMision.DEFENSA: ["La munición escasea y el apoyo llegará tarde.", "El enemigo atacará por dos flancos.",
                         "Las comunicaciones fallan: cada grupo tendrá que arreglárselas."],
    TipoMision.EMBOSCADA: ["El convoy lleva escolta y puede cambiar de ruta.", "La zona está patrullada.",
                           "Solo hay una oportunidad antes de que se dé la alarma."],
    TipoMision.RESCATE: ["Los aislados tienen heridos y no pueden moverse rápido.",
                         "El enemigo ya los está rodeando.", "La zona de extracción puede estar vigilada."],
    TipoMision.RECONOCIMIENTO: ["Si os detectan, el enemigo cambiará sus planes.",
                                "Hay torres de vigilancia en las alturas.",
                                "La niebla puede jugar a favor o en contra."],
    TipoMision.DESTRUCCION: ["El objetivo está protegido por un campo de minas.",
                             "Hay que entrar y salir antes de que llegue la reacción enemiga.",
                             "La información sobre el objetivo puede estar desfasada."],
}
IMPREVISTOS = [
    ("Un informante desaparece y nadie sabe qué ha contado.", False),
    ("Llega un temporal: el tiempo empeora durante días.", False),
    ("El enemigo recibe refuerzos inesperados.", False),
    ("Los suministros llegan con retraso: menos munición para el siguiente paso.", False),
    ("Un aliado local cambia de bando.", False),
    ("Una orden desde arriba obliga a cambiar los planes.", False),
    ("La información de inteligencia era falsa: el objetivo no estaba donde se creía.", True),
    ("Un convoy propio cae en una emboscada y se pierden hombres y material.", True),
    ("El enemigo descubre el plan y traslada lo que buscábamos.", True),
]
LUGARES_TERRENO = {
    "Altis": ["Kavala", "Pyrgos", "Athira", "Sofia", "Zaros", "Neochori", "Agios Dionysios", "Paros", "Charkia"],
    "Stratis": ["Agia Marina", "Girna", "Kamino", "Air Station Mike-26", "Camp Maxwell", "Camp Tempest"],
    "Malden": ["Le Port", "La Trinité", "Arudy", "Goisse", "Chapoi", "Saint Louis", "Larche"],
    "Tanoa": ["Georgetown", "Lijnhaven", "Katkoula", "Tuvanaka", "Balavu", "Moddergat", "La Rochelle"],
    "Livonia": ["Lukow", "Topolin", "Sitnik", "Gliniska", "Nadbor", "Bielawa", "Radacz"],
    "Chernarus": ["Chernogorsk", "Elektrozavodsk", "Zelenogorsk", "Stary Sobor", "Berezino", "Gorka"],
    "Takistan": ["Rasman", "Loy Manara", "Feruz Abad", "Garmsar", "Chak Chak", "Nur"],
}
LUGARES_ERA = {
    "segunda_guerra_mundial": ["Saint-Aubin", "La Croix", "Le Mesnil", "Bois du Loup", "Granja Lefèvre", "Cota 112"],
    "guerra_fria": ["Grünbach", "Hohenfeld", "Paso de Fulda", "Waldheim", "Cota 410", "Molino Viejo"],
    "moderna": ["Villanueva", "Aldea Vieja", "Colina 214", "Bosque Negro", "Cruce del Molino", "Valdecierva"],
    "futura": ["Nodo 7", "Estación Kepler", "Ciudad Baja", "Cota 9", "Planta Delta", "Arcología Sur"],
}
NOMBRES = {
    ("segunda_guerra_mundial", "jugadores"): (["Joe", "Frank", "Tom", "Eddie", "Walt", "Harry"],
                                              ["Miller", "Kowalski", "O'Brien", "Russo", "Hayes", "Murphy"],
                                              ["Sargento", "Cabo", "Teniente"]),
    ("segunda_guerra_mundial", "enemigo"): (["Hans", "Karl", "Otto", "Fritz", "Ernst"],
                                            ["Becker", "Krause", "Wolf", "Hartmann", "Vogel"],
                                            ["Feldwebel", "Unteroffizier", "Leutnant"]),
    ("guerra_fria", "enemigo"): (["Iván", "Dmitri", "Serguéi", "Yuri", "Oleg"],
                                 ["Volkov", "Petrov", "Sokolov", "Orlov", "Kuznetsov"], ["Sargento", "Teniente"]),
    ("irregular", "enemigo"): (["Omar", "Yusuf", "Tariq", "Samir", "Jalil"],
                               ["Haddad", "Nasser", "Karimi", "Rahman"], ["", "Jefe de célula"]),
    ("irregular", "jugadores"): (["Omar", "Leila", "Tariq", "Samira", "Jalil"],
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
ERA_INFO: dict[str, dict] = {
    "segunda_guerra_mundial": {
        "epoca": "Europa, 1944", "jugadores": ("Ejército aliado", "Infantería con algún blindado de apoyo."),
        "enemigo": ("Wehrmacht", "Infantería veterana con ametralladoras y posiciones preparadas."),
        "aereos": False, "retardo": 30, "mando": "Sunray", "mando_enemigo": "Kompanie",
        "indicativos": ["Able", "Baker", "Charlie", "Dog", "Easy"],
        "indicativos_enemigos": ["Gruppe Anton", "Gruppe Berta", "Gruppe Caesar", "Gruppe Dora"],
        "radio": "Radio de campaña de 1944: mensajes breves, se tarda en contactar y se habla con claves.",
        "vocabulario": ["Roger", "Over", "Wilco"],
        "restricciones": ["No hay GPS, visión nocturna ni helicópteros; la radio es lenta."],
    },
    "guerra_fria": {
        "epoca": "Europa central, años 80", "jugadores": ("Fuerzas de la OTAN", "Infantería mecanizada."),
        "enemigo": ("Pacto de Varsovia", "Masas de blindados e infantería motorizada."),
        "aereos": True, "retardo": 10, "mando": "Guardián", "mando_enemigo": "Tormenta",
        "indicativos": ["Lobo-1", "Lobo-2", "Oso-1", "Oso-2", "Halcón-1"],
        "indicativos_enemigos": ["Volga-1", "Volga-2", "Ural-1", "Ural-2"],
        "radio": "Radio militar analógica: frases breves, códigos y cuadrículas.",
        "vocabulario": ["cambio", "recibido", "negativo"], "restricciones": ["No hay GPS en las unidades pequeñas."],
    },
    "moderna": {
        "epoca": "Actualidad", "jugadores": ("Fuerza de Tarea Aliada", "Infantería ligera apoyada por aviación."),
        "enemigo": ("Fuerzas hostiles", "Fuerza convencional con infantería, vehículos y posiciones preparadas."),
        "aereos": True, "retardo": 0, "mando": "Mando", "mando_enemigo": "Fortaleza",
        "indicativos": ["Alfa-1", "Bravo-1", "Charlie-1", "Delta-1", "Eco-1"],
        "indicativos_enemigos": ["Rojo-1", "Rojo-2", "Rojo-3", "Ariete-1"],
        "radio": "Procedimiento de radio militar en español: frases breves con indicativos y cuadrículas.",
        "vocabulario": ["cambio", "recibido", "afirmativo", "en posición"], "restricciones": [],
    },
    "futura": {
        "epoca": "Año 2060", "jugadores": ("Coalición", "Infantería con drones y exoesqueletos ligeros."),
        "enemigo": ("Directorio", "Fuerza automatizada con blindados y sensores."),
        "aereos": True, "retardo": 0, "mando": "Faro", "mando_enemigo": "Núcleo",
        "indicativos": ["Vector-1", "Vector-2", "Prisma-1", "Prisma-2"],
        "indicativos_enemigos": ["Unidad-7", "Unidad-9", "Unidad-12"],
        "radio": "Enlace de datos cifrado: mensajes muy cortos y técnicos.",
        "vocabulario": ["confirmado", "enlace estable"], "restricciones": [],
    },
}


def rng_de(texto: str, semilla: int | None = None) -> random.Random:
    """Aleatoriedad repetible: el mismo texto (y la misma semilla) dan siempre el mismo resultado."""
    return random.Random(zlib.crc32(f"{semilla}|{texto}".encode()))


def detectar_era(texto: str) -> str:
    norm = normalizar(texto)
    for claves, era in CLAVES_ERA:
        if any(c in norm for c in claves):
            return era
    return "moderna"


def detectar_misiones(texto: str) -> list[TipoMision]:
    norm = normalizar(texto)
    encontradas: list[tuple[int, TipoMision]] = []
    for claves, tipo in CLAVES_MISION:
        posiciones = [norm.find(c) for c in claves if c in norm]
        if posiciones:
            encontradas.append((min(posiciones), tipo))
    return [t for _, t in sorted(encontradas)]


def _numero(texto: str) -> float:
    if texto == "media":
        return 0.5
    return float(texto.replace(",", ".")) if texto[0].isdigit() else float(NUMEROS[texto])


def numero_antes(texto: str, palabras: tuple[str, ...]) -> int | None:
    """'tres escuadras' -> 3; '4 helicopteros' -> 4."""
    norm = normalizar(texto)
    for palabra in palabras:
        m = re.search(rf"(\d+|{'|'.join(NUMEROS)})\s+{palabra}", norm)
        if m:
            return int(_numero(m.group(1)))
    return None


def interpretar_encargo(texto: str) -> tuple[int | None, int | None]:
    """Número de misiones y minutos por misión que pide el anfitrión en su encargo, si los dice.

    '5 misiones de 90 minutos' -> (5, 90); 'campaña de 6 horas con 4 misiones' -> (4, 90);
    'tres misiones de hora y media cada una' -> (3, 90).
    """
    norm = normalizar(texto).replace("hora y media", "1.5 horas").replace("una hora", "1 hora")
    misiones = numero_antes(norm, ("misiones", "mision", "capitulos", "operaciones", "partes"))
    minutos: float | None = None
    m = re.search(rf"{_NUM}\s*(horas|hora|h|minutos|minuto|min)\b", norm)
    if m:
        valor = _numero(m.group(1))
        minutos = valor * 60 if m.group(2).startswith("h") else valor
        resto = norm[m.end():m.end() + 40]
        antes = norm[max(0, m.start() - 30):m.start()]
        # La duración va con la palabra más cercana: "misiones de 90 minutos" o "campaña de 6 horas".
        cerca_mision = max(antes.rfind("mision"), antes.rfind("capitulo"), antes.rfind("partes"))
        cerca_campana = antes.rfind("campana")
        por_mision = any(p in resto for p in ("cada una", "cada mision", "por mision", "por cada")) or (
            cerca_mision > cerca_campana)
        total = cerca_campana > cerca_mision or "en total" in resto or "total" in antes
        if misiones and total and not por_mision:
            minutos = minutos / misiones
    duracion = None if minutos is None else round(max(DURACION_MIN, min(DURACION_MAX, minutos)))
    if misiones is not None:
        misiones = max(1, min(MAX_MISIONES, misiones))
    return misiones, duracion


_PALABRAS_NUM = "|".join(NUMEROS)
_TAMANO = re.compile(
    rf",?\s*(?:con\s+|en\s+)?(?:\d+|{_PALABRAS_NUM})\s+(?:misi[oó]n(?:es)?|cap[ií]tulos?|operaciones|partes)"
    rf"(?:\s+de\s+(?:\d+(?:[.,]\d+)?|{_PALABRAS_NUM}|media)\s*(?:horas?|h|minutos?|min)\b(?:\s+y\s+media)?)?"
    rf"(?:\s+cada\s+una)?|,?\s*(?:de|en)\s+(?:\d+(?:[.,]\d+)?|{_PALABRAS_NUM})\s*(?:horas?|minutos?|min)\b",
    re.IGNORECASE,
)
_PROPOSITO = re.compile(r"\b(?:para|cuyo objetivo es|con el objetivo de|objetivo:?)\s+(.+)", re.IGNORECASE)


def proposito_encargo(encargo: str) -> str:
    """Lo que hay que conseguir según el encargo: 'campaña de 1944 para volar un puente' -> 'volar un puente'."""
    texto = _TAMANO.sub("", encargo).strip()
    m = _PROPOSITO.search(texto)
    if m is None:
        m = re.search(r"\bsobre\s+(.+)", texto, re.IGNORECASE)
    if m is None:
        return ""
    proposito = re.split(r"[.;:\n]|,\s*(?:y\s+)?(?:que|donde|con|en\s+la\s+que)\b", m.group(1))[0]
    return proposito.strip(" ,.")


def _irregulares(encargo: str) -> tuple[bool, bool]:
    """(jugadores irregulares, enemigo irregular) según el encargo."""
    norm = normalizar(encargo)
    if any(c in norm for c in CLAVES_JUGADORES_IRREGULARES):
        return True, False
    return False, any(c in norm for c in CLAVES_IRREGULAR)


def crear_persona(rng: random.Random, era: str, lado: str, usados: set[str] | None = None,
                  irregular: bool = False) -> Persona:
    nombres, apellidos, rangos = NOMBRES.get(("irregular" if irregular else era, lado), NOMBRES_GENERICOS)
    """Un personaje con nombre, rango, carácter e historia, sin repetir nombres, apellidos ni historias.

    `usados` guarda lo que ya tienen otros personajes de la campaña (se actualiza).
    """
    usados = usados if usados is not None else set()
    partes = {parte for u in usados for parte in u.split()}

    def libre(nombre: str) -> bool:
        primero, *_, ultimo = nombre.split()
        return nombre not in usados and primero not in partes and ultimo not in partes

    nombre = f"{rng.choice(nombres)} {rng.choice(apellidos)}"
    for _ in range(30):
        if libre(nombre):
            break
        nombre = f"{rng.choice(nombres)} {rng.choice(apellidos)}"
    historias = [h for h in HISTORIAS if h not in usados] or HISTORIAS
    historia = rng.choice(historias)
    usados.update((nombre, historia))
    rasgos = rng.sample(list(Rasgo), k=2)
    if Rasgo.VETERANO in rasgos and Rasgo.NOVATO in rasgos:
        rasgos.remove(Rasgo.NOVATO)
    return Persona(nombre=nombre, rango=rng.choice(rangos), rasgos=rasgos, historia=historia,
                   femenina=nombre.split()[0] in NOMBRES_FEMENINOS)


# ---------------------------------------------------------------------------------- ambientación
def ambientacion_por_reglas(encargo: str, terrenos: list[str]) -> AmbientacionPlan:
    era = detectar_era(encargo)
    info = ERA_INFO[era]
    jugadores_irr, enemigo_irr = _irregulares(encargo)
    jugadores, enemigo = info["jugadores"], info["enemigo"]
    if enemigo_irr:
        enemigo = ("Insurgentes", "Células pequeñas e irregulares: emboscadas, IED y golpes rápidos.")
    if jugadores_irr:
        jugadores = ("Resistencia", "Combatientes irregulares que conocen el terreno.")
    doctrina_enemiga = (["Golpear y desaparecer.", "Usar IED y emboscadas en las rutas.", "Evitar el choque frontal."]
                        if enemigo_irr else ["Defender en profundidad.", "Contraatacar con la reserva.",
                                             "Patrullar los accesos."])
    return AmbientacionPlan(
        era=era,
        epoca=info["epoca"],
        descripcion=encargo.strip().rstrip(".")[:300] + ".",
        terreno=terreno_por_reglas(encargo, terrenos),
        jugadores=BandoAmbiente(nombre=jugadores[0], descripcion=jugadores[1]),
        enemigo=BandoAmbiente(nombre=enemigo[0], descripcion=enemigo[1]),
        doctrina_enemiga=doctrina_enemiga,
        doctrina=["Reconocer antes de actuar.", "Fijar y flanquear.", "No dejar grupos aislados."],
        restricciones=info["restricciones"],
        estilo_radio=info["radio"],
        medios_aereos=info["aereos"] and not jugadores_irr,
        retardo_radio_s=info["retardo"],
        indicativo_mando=info["mando"],
        indicativos=info["indicativos"],
        indicativo_mando_enemigo=info["mando_enemigo"],
        indicativos_enemigos=["Célula-1", "Célula-2", "Célula-3", "Célula-4"] if enemigo_irr
        else info["indicativos_enemigos"],
        vocabulario=info["vocabulario"],
    )


def es_irregular(perfil: PerfilCampana, lado: str) -> bool:
    texto = normalizar((perfil.enemigo if lado == "enemigo" else perfil.bando_ia).nombre + " "
                       + (perfil.enemigo if lado == "enemigo" else perfil.bando_ia).descripcion)
    return any(c in texto for c in (*CLAVES_IRREGULAR, "irregular"))


# ---------------------------------------------------------------------------------- campaña
def capitulo_por_reglas(tipo: TipoMision, lugar: str, rng: random.Random, critico: bool = False) -> CapituloPlan:
    return CapituloPlan(titulo=TITULOS[tipo].format(lugar=lugar), sinopsis=SINOPSIS[tipo].format(lugar=lugar),
                        tipo=tipo, objetivo=OBJETIVOS[tipo].format(lugar=lugar),
                        complicacion=rng.choice(COMPLICACIONES[tipo]), critico=critico)


def lugares_de(terreno: str, era: str) -> list[str]:
    return list(LUGARES_TERRENO.get(terreno) or LUGARES_ERA.get(era, LUGARES_ERA["moderna"]))


def campana_por_reglas(encargo: str, perfil: PerfilCampana, terreno: str, misiones: int,
                       semilla: int | None = None) -> PlanCampana:
    rng = rng_de(encargo, semilla)
    pedidas = detectar_misiones(encargo)
    lugares = lugares_de(terreno, perfil.id)
    rng.shuffle(lugares)
    final = pedidas[0] if pedidas else TipoMision.ASALTO
    objetivo_lugar = lugares[0]
    # Antes del golpe final hay que preparar el terreno: los pasos del arco, en orden.
    tipos = [t for t in ARCO if t != final][: max(0, misiones - 1)]
    while len(tipos) < misiones - 1:
        tipos.append(rng.choice(ARCO))
    tipos.append(final)
    capitulos = [capitulo_por_reglas(t, lugares[(i + 1) % len(lugares)] if i < misiones - 1 else objetivo_lugar, rng)
                 for i, t in enumerate(tipos)]
    if misiones >= 4:
        capitulos[misiones // 2].critico = True
    capitulos[-1].critico = True
    usados: set[str] = set()
    jugadores_irr = es_irregular(perfil, "jugadores")
    personajes = [crear_persona(rng, perfil.id, "jugadores", usados, jugadores_irr) for _ in range(3)]
    antagonista = crear_persona(rng, perfil.id, "enemigo", usados, es_irregular(perfil, "enemigo"))
    antagonista.rango = antagonista.rango or "Comandante"
    proposito = proposito_encargo(encargo)
    if proposito:
        objetivo = f"{proposito[:1].upper()}{proposito[1:]} (golpe final en {objetivo_lugar})."
    else:
        objetivo = SINOPSIS[final].format(lugar=objetivo_lugar)
    premisa = (f"{perfil.epoca}. {perfil.bando_ia.nombre} contra {perfil.enemigo.nombre}. El mando tiene un "
               f"único objetivo y no admite excusas; llegar hasta él exigirá {misiones} paso(s), y cada uno "
               "puede torcerse.")
    return PlanCampana(
        nombre=f"Operación {objetivo_lugar}",
        premisa=premisa,
        objetivo_final=objetivo,
        capitulos=capitulos,
        personajes=personajes,
        antagonista=antagonista,
    )


def ajustar_capitulos(plan: PlanCampana, misiones: int, terreno: str, era: str, semilla: int | None = None) -> None:
    """Deja exactamente `misiones` pasos (el modelo a veces escribe de más o de menos) y la última crítica."""
    rng = rng_de(plan.nombre, semilla)
    del plan.capitulos[misiones:]
    lugares = lugares_de(terreno, era)
    while len(plan.capitulos) < misiones:
        tipo = ARCO[len(plan.capitulos) % len(ARCO)]
        plan.capitulos.insert(max(0, len(plan.capitulos) - 1), capitulo_por_reglas(tipo, rng.choice(lugares), rng))
    for capitulo in plan.capitulos:
        if not capitulo.complicacion:
            capitulo.complicacion = rng.choice(COMPLICACIONES[capitulo.tipo])
        if not capitulo.objetivo:
            capitulo.objetivo = capitulo.sinopsis
    plan.capitulos[-1].critico = True


# ---------------------------------------------------------------------------------- misión
def mision_por_reglas(
    perfil: PerfilCampana,
    capitulo: CapituloPlan,
    *,
    nombre_campana: str,
    terreno: str,
    personajes: list[Persona],
    antagonista: Persona | None,
    dificultad: float = 1.0,
    duracion_min: int = 60,
    semilla: int | None = None,
) -> PlanMision:
    rng = rng_de(capitulo.titulo + nombre_campana, semilla)
    texto = f"{capitulo.titulo} {capitulo.sinopsis} {capitulo.complicacion}"
    norm = normalizar(texto)
    lugares_mapa = lugares_de(terreno, perfil.id)
    lugar_obj = next((lg for lg in lugares_mapa if normalizar(lg) in norm), lugares_mapa[0])
    otros = [lg for lg in lugares_mapa if lg != lugar_obj]
    rng.shuffle(otros)
    propios_ind = perfil.indicativos.get("infanteria", [])
    enemigos_ind = perfil.enemigo.indicativos.get("infanteria", [])
    jugador_irr, enemigo_irr = es_irregular(perfil, "jugadores"), es_irregular(perfil, "enemigo")

    obj = Posicion(x=rng.uniform(2300, 2900), y=rng.uniform(2400, 3000))
    # La distancia de partida depende de lo que dura la misión: a pie se avanzan unos 80 m por
    # minuto, y tiene que quedar tiempo para combatir (y para volver, en un rescate).
    aproximacion = max(1000.0, min(2600.0, 35.0 * duracion_min + 300.0))
    if capitulo.tipo == TipoMision.RESCATE:
        aproximacion *= 0.7
    salida = obj.desplazar(225 + rng.uniform(-20, 20), aproximacion).limitar(3800, 3800)
    salida = Posicion(x=max(200.0, salida.x), y=max(200.0, salida.y))
    medio = Posicion(x=(obj.x + salida.x) / 2 + rng.uniform(-300, 300), y=(obj.y + salida.y) / 2)
    lugares = [
        LugarPlan(nombre=lugar_obj, tipo="urbano", posicion=obj.cuadricula(), radio_m=250),
        LugarPlan(nombre=otros[0], tipo="bosque", posicion=medio.cuadricula(), radio_m=350),
        LugarPlan(nombre=otros[1], tipo="colina", posicion=obj.desplazar(rng.uniform(250, 340), 800).cuadricula()),
    ]
    if perfil.medios_aereos:
        lugares.append(LugarPlan(nombre="LZ Norte", tipo="lz", posicion=obj.desplazar(0, 900).cuadricula()))

    def ind_propio(i: int) -> str:
        return propios_ind[i] if i < len(propios_ind) else f"Grupo-{i + 1}"

    def ind_enemigo(i: int) -> str:
        return enemigos_ind[i] if i < len(enemigos_ind) else f"Hostil-{i + 1}"

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
    decorado: list[ObjetoPlan] = []

    def lider_enemigo() -> Persona:
        return crear_persona(rng, perfil.id, "enemigo", usados, enemigo_irr)

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
        decorado += [
            ObjetoPlan(tipo=TipoObjeto.FORTIFICACION, lugar=obj.desplazar(45, 120).cuadricula(), bando="blufor",
                       radio_m=80, descripcion="Sacos terreros y trincheras mirando al noreste"),
            ObjetoPlan(tipo=TipoObjeto.ALAMBRADA, lugar=obj.desplazar(45, 400).cuadricula(), bando="blufor",
                       radio_m=120, descripcion="Alambrada en el acceso"),
        ]
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
        decorado.append(ObjetoPlan(tipo=TipoObjeto.RESTOS_VEHICULOS, lugar=emboscada.desplazar(270, 200).cuadricula(),
                                   descripcion="Coches quemados en la cuneta"))
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
        if perfil.medios_aereos:
            propias.append(UnidadPlan(indicativo="Halcón-1", tipo=TipoUnidad.HELICOPTERO, efectivos=4,
                                      posicion=salida.cuadricula(), tarea=TareaMando.ESPERAR))
        decorado.append(ObjetoPlan(tipo=TipoObjeto.ESCOMBROS, lugar=obj.cuadricula(), radio_m=150,
                                   descripcion="Casas derruidas donde se refugian los aislados"))
    elif tipo == TipoMision.RECONOCIMIENTO:
        # Un reconocimiento empieza más cerca: a pie y en sigilo se avanza despacio.
        cerca = obj.desplazar(225, min(1300.0, aproximacion))
        propias[0] = propias[0].model_copy(update={"tarea": TareaMando.RECONOCER, "destino": lugar_obj,
                                                   "posicion": cerca.cuadricula()})
        for i in range(n_enemigos):
            enemigas.append(UnidadPlan(indicativo=ind_enemigo(i), efectivos=fuerza,
                                       posicion=obj.desplazar(rng.uniform(0, 360), 300).cuadricula(),
                                       tarea=TareaMando.PATRULLAR if i % 2 else TareaMando.DEFENDER,
                                       destino=lugar_obj, lider=lider_enemigo()))
        objetivos_j.append(ObjetivoPlan(nombre=f"Reconocer {lugar_obj}", tipo=TipoObjetivo.RECONOCER,
                                        lugar=lugar_obj))
        decorado.append(ObjetoPlan(tipo=TipoObjeto.TORRE_VIGILANCIA, lugar=obj.desplazar(200, 250).cuadricula(),
                                   bando="opfor", radio_m=50, descripcion="Torre de vigilancia sobre el camino"))
    else:  # asalto y destrucción
        # Atacar una posición preparada exige superioridad: tres grupos propios (y apoyo aéreo si
        # la época lo tiene) contra una guarnición más pequeña.
        propias[0] = propias[0].model_copy(update={"tarea": TareaMando.ATACAR, "destino": lugar_obj})
        jefes: list[Persona | None] = [*lideres[:3]]
        jefes += [None] * (3 - len(jefes))
        for i, jefe in enumerate(jefes):
            propias.append(UnidadPlan(indicativo=ind_propio(i), efectivos=7,
                                      posicion=salida.desplazar(rng.uniform(0, 90), 250).cuadricula(),
                                      tarea=TareaMando.RECONOCER if i == 0 else TareaMando.ATACAR,
                                      destino=lugar_obj, lider=jefe))
        if perfil.medios_aereos:
            propias.append(UnidadPlan(indicativo=perfil.indicativos.get("helicoptero", ["Búho-1"])[-1],
                                      tipo=TipoUnidad.HELICOPTERO, efectivos=2, armada=True,
                                      posicion=salida.cuadricula(), tarea=TareaMando.ESPERAR))
        guardia = []
        for i in range(max(2, n_enemigos - 1)):
            indicativo = ind_enemigo(i)
            guardia.append(indicativo)
            # Quien defiende un pueblo tiene ventaja: la guarnición es más pequeña que el atacante.
            enemigas.append(UnidadPlan(indicativo=indicativo, efectivos=max(3, fuerza - 1),
                                       posicion=obj.desplazar(rng.uniform(0, 360), 120 * i).cuadricula(),
                                       tarea=TareaMando.DEFENDER if i < 2 else TareaMando.PATRULLAR,
                                       destino=lugar_obj, lider=lider_enemigo()))
        decorado += [
            ObjetoPlan(tipo=TipoObjeto.FORTIFICACION, lugar=obj.cuadricula(), bando="opfor", radio_m=100,
                       descripcion="Sacos terreros y nidos de ametralladora en las casas"),
            ObjetoPlan(tipo=TipoObjeto.PUESTO_CONTROL, lugar=obj.desplazar(225, 450).cuadricula(), bando="opfor",
                       descripcion="Puesto de control en la entrada del pueblo"),
        ]
        if tipo == TipoMision.DESTRUCCION:
            objetivos_j.append(ObjetivoPlan(nombre=f"Destruir la guarnición de {lugar_obj}",
                                            tipo=TipoObjetivo.DESTRUIR, lugar=lugar_obj, unidades=guardia[:2]))
            decorado.append(ObjetoPlan(tipo=TipoObjeto.DEPOSITO, lugar=obj.desplazar(90, 80).cuadricula(),
                                       bando="opfor", descripcion="Depósito de munición"))
        else:
            objetivos_j.append(ObjetivoPlan(nombre=f"Tomar {lugar_obj}", tipo=TipoObjetivo.CAPTURAR, lugar=lugar_obj))
    if dificultad >= 1.2 and tipo != TipoMision.EMBOSCADA:
        enemigas.append(UnidadPlan(indicativo=ind_enemigo(len(enemigas)), tipo=TipoUnidad.MOTORIZADA, efectivos=4,
                                   posicion=obj.desplazar(45, 1500).cuadricula(), tarea=TareaMando.ESPERAR,
                                   lider=lider_enemigo()))
    if enemigo_irr or "mina" in norm or "ied" in norm:
        trampa = TipoObjeto.IED if enemigo_irr else TipoObjeto.MINAS
        decorado.append(ObjetoPlan(tipo=trampa, lugar=medio.desplazar(45, 300).cuadricula(), bando="opfor",
                                   radio_m=80 if trampa == TipoObjeto.MINAS else 40,
                                   descripcion="En el camino más corto hacia el objetivo"))

    sucesos = sucesos_por_reglas(capitulo, obj, duracion_min, rng)
    hora = rng.choices(["dia", "amanecer", "atardecer", "noche"], weights=[5, 2, 2, 1])[0]
    clima = rng.choices(["despejado", "nubes", "lluvia", "niebla"], weights=[5, 3, 1, 1])[0]
    for palabra in ("noche", "amanecer", "atardecer"):
        if palabra in norm:
            hora = palabra
    return PlanMision(
        nombre=capitulo.titulo,
        briefing=f"{capitulo.sinopsis} {capitulo.complicacion}".strip(),
        terreno=terreno,
        zona=f"alrededores de {lugar_obj}",
        hora=hora,
        clima=clima,
        tamano_km=4,
        lugares=lugares,
        decorado=decorado,
        sucesos=sucesos,
        bandos=[
            # El mando aliado es un NPC más (el cuartel general con el que hablan los jugadores). En
            # golpes de mano pequeños (emboscada, reconocimiento) cada grupo va por su cuenta.
            BandoPlan(bando="blufor", nombre=perfil.bando_ia.nombre,
                      mando=tipo not in (TipoMision.EMBOSCADA, TipoMision.RECONOCIMIENTO) and not jugador_irr,
                      unidades=propias, objetivos=objetivos_j, base=salida.cuadricula()),
            # Una guerrilla actúa por células, sin mando central.
            BandoPlan(bando="opfor", nombre=perfil.enemigo.nombre, mando=not enemigo_irr,
                      comandante=antagonista, unidades=enemigas),
        ],
        bando_jugadores="blufor",
    )


def sucesos_por_reglas(capitulo: CapituloPlan, objetivo: Posicion, duracion_min: int,
                       rng: random.Random) -> list[SucesoPlan]:
    """Lo que Zeus deja preparado para complicar la misión a mitad de camino."""
    sucesos: list[SucesoPlan] = []
    norm = normalizar(f"{capitulo.complicacion} {capitulo.sinopsis}")
    if any(p in norm for p in ("refuerz", "reaccion", "motoriz")) or capitulo.tipo == TipoMision.ASALTO:
        punto = objetivo.desplazar(45, 1200)
        sucesos.append(SucesoPlan(
            minuto=max(5, int(duracion_min * 0.45)), tipo=TipoIntervencion.REFUERZOS, bando="opfor",
            texto="Llegan refuerzos enemigos.",
            unidad=UnidadPlan(indicativo="Reacción", tipo=TipoUnidad.MOTORIZADA, efectivos=4,
                              posicion=punto.cuadricula(), tarea=TareaMando.ATACAR, destino=objetivo.cuadricula())))
    if "niebla" in norm or (capitulo.tipo == TipoMision.RECONOCIMIENTO and rng.random() < 0.5):
        sucesos.append(SucesoPlan(minuto=max(5, int(duracion_min * 0.3)), tipo=TipoIntervencion.CLIMA,
                                  clima="niebla"))
    if any(p in norm for p in ("comunicac", "radio")):
        sucesos.append(SucesoPlan(minuto=max(5, int(duracion_min * 0.25)), tipo=TipoIntervencion.MENSAJE,
                                  texto="Interferencias en la radio: las comunicaciones van y vienen."))
    return sucesos[:4]


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


# ---------------------------------------------------------------------------------- después
def imprevisto_por_reglas(rng: random.Random) -> tuple[str, bool]:
    """Un imprevisto entre misiones: (texto, grave). Los graves gastan margen de error."""
    return rng.choice(IMPREVISTOS)


def cronica_por_reglas(titulo: str, resultado: ResultadoMision, cambios: list[str]) -> str:
    estado = {"victoria": "salió bien", "parcial": "salió a medias", "derrota": "salió mal"}[resultado.estado]
    texto = (f"La misión «{titulo}» {estado}: {resultado.motivo}. Hubo {resultado.bajas_propias} bajas propias "
             f"y {resultado.bajas_enemigas} enemigas.")
    caidos = [c for c in cambios if "ha caído" in c]
    if caidos:
        texto += " " + " ".join(caidos)
    return texto
