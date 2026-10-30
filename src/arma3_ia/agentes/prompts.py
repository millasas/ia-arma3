"""Instrucciones (prompts) de cada agente, pensadas para modelos de 7-8B: cortas y concretas."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..campanas import PerfilCampana
from ..comunicacion import MensajeRadio
from ..memoria import Leccion
from ..modelo import MensajeChat, Observacion, Persona, UnidadPropia
from ..texto import reloj
from . import situacion as s
from .esquemas import OrdenMando

EJEMPLO_COMANDANTE = (
    '{"evaluacion": "Situación en una frase.", "intencion": "Qué quieres conseguir y cómo.", '
    '"ordenes": [{"unidad": "Alfa-1", "tarea": "reconocer", "destino": "045123", '
    '"objetivo": null, "detalles": "Avanzad por el bosque y no os dejéis ver."}], '
    '"mensaje_general": null}'
)
EJEMPLO_LIDER = (
    '{"evaluacion": "Qué veo y qué voy a hacer.", "accion": "mover", "destino": "045123", '
    '"objetivo": null, "postura": "sigilo", "informe": null, "solicitud": null, "mensajes": []}'
)
EJEMPLO_PILOTO = (
    '{"evaluacion": "Qué veo y qué voy a hacer.", "accion": "aterrizar", "destino": "LZ Norte", '
    '"objetivo": null, "altitud": "baja", "informe": "En camino a LZ Norte.", "mensajes": []}'
)
EJEMPLO_ENLACE = (
    '{"respuesta": "Recibido, sargento. Bravo-1 va de camino a su posición.", '
    '"reenviar": [{"para": "mando", "texto": "El sargento pide apoyo en la colina 214."}]}'
)

REGLAS_DESTINOS = (
    "Los destinos se indican con cuadrícula de 6 cifras (p. ej. \"045123\") o con el nombre o "
    "id exacto de un lugar, objetivo, LZ o contacto de la lista."
)


def bloque_lecciones(lecciones: Sequence[Leccion]) -> str:
    if not lecciones:
        return ""
    lineas = [
        f"- {'EVITA' if l.tipo == 'error' else 'REPITE'}: {l.texto()}" for l in lecciones  # noqa: E741
    ]
    return "\nLECCIONES APRENDIDAS EN MISIONES ANTERIORES (tenlas en cuenta):\n" + "\n".join(lineas)


def bloque_persona(persona: Persona | None) -> str:
    if persona is None:
        return ""
    return (
        f"\nQUIÉN ERES: {persona.presentacion()}\n"
        "Eres una persona, no una máquina: decide y habla como lo haría alguien con tu carácter "
        "y tu experiencia (el miedo, el orgullo y el cansancio también cuentan)."
    )


def _final(ejemplo: str) -> str:
    return f"\nResponde SOLO con un objeto JSON con este formato:\n{ejemplo}"


# ---------------------------------------------------------------------------------- comandante
def sistema_comandante(indicativo: str, perfil: PerfilCampana, persona: Persona | None = None) -> str:
    aereas = (
        "transporte (un helicóptero lleva a la unidad indicada en \"objetivo\" hasta el destino), "
        "apoyo_aereo (un helicóptero armado ataca el destino u objetivo), regresar_base"
        if perfil.medios_aereos
        else "(en esta campaña no hay medios aéreos)"
    )
    return (
        f"Eres {indicativo}, el comandante de {perfil.bando_ia.nombre} en una simulación militar "
        "táctica realista (ARMA 3). Diriges a tus unidades para cumplir los objetivos con las "
        "mínimas bajas propias. Los jugadores humanos son aliados tuyos, pero no los controlas.\n"
        f"{perfil.bloque_contexto('comandante')}\n\n"
        "NORMAS:\n"
        "- Usa solo las unidades listadas, con su indicativo exacto.\n"
        f"- {REGLAS_DESTINOS}\n"
        "- Tareas de tierra: mover, atacar, defender, reconocer, retirarse, esperar, embarcar "
        "(objetivo = helicóptero), patrullar (\"ruta\" = lista de puntos), emboscar (esperar ocultos "
        "en el destino y atacar cuando llegue el enemigo).\n"
        f"- Tareas aéreas: {aereas}.\n"
        "- Da órdenes solo a las unidades que deban cambiar lo que hacen; las demás siguen con su tarea.\n"
        "- Atiende las solicitudes y los informes de tus unidades y de los jugadores.\n"
        "- Sé breve: frases cortas."
        + bloque_persona(persona)
        + _final(EJEMPLO_COMANDANTE)
    )


def usuario_comandante(
    obs: Observacion,
    mensajes: Iterable[MensajeRadio],
    intencion_previa: str,
    avisos: Sequence[str],
) -> str:
    propias = [u for u in obs.unidades if u.operativa]
    centro = propias[0].posicion if propias else None
    return s.unir(
        s.cabecera(obs),
        s.seccion("OBJETIVOS", s.describir_objetivos(obs), "ninguno"),
        s.seccion("TUS UNIDADES", [s.describir_unidad(u, obs) for u in propias], "ninguna"),
        s.seccion(
            "CONTACTOS ENEMIGOS CONOCIDOS",
            [s.describir_contacto(c) for c in obs.contactos],
            "ninguno conocido",
        ),
        s.seccion("ZONAS DE ATERRIZAJE", s.describir_lzs(obs)),
        s.seccion("TERRENO", s.describir_referencias(obs, centro)),
        f"BASE: {obs.base.cuadricula()}" if obs.base else "",
        s.seccion("EVENTOS RECIENTES", s.describir_eventos(obs.eventos)),
        s.seccion("RADIO (mensajes recibidos)", s.describir_mensajes(mensajes)),
        s.seccion("AVISOS SOBRE TUS ÓRDENES ANTERIORES", list(avisos)),
        f"TU INTENCIÓN ANTERIOR: {intencion_previa}" if intencion_previa else "",
        "Decide ahora tus órdenes.",
    )


# ---------------------------------------------------------------------------------- líder
def _cadena_mando(mando: str | None) -> str:
    if mando:
        return f"Recibes órdenes de {mando} y decides cómo cumplirlas."
    return ("No hay un mando por encima de ti: tienes tu propia misión y decides tú, "
            "coordinándote por radio con las demás unidades de tu bando.")


def sistema_lider(indicativo: str, unidad: UnidadPropia, perfil: PerfilCampana, mando: str | None,
                  persona: Persona | None = None) -> str:
    return (
        f"Eres {indicativo}, jefe de una escuadra de {s.NOMBRE_TIPO[unidad.tipo]} de "
        f"{perfil.bando_ia.nombre} en una simulación militar realista (ARMA 3). {_cadena_mando(mando)} "
        "Cuida a tus hombres: usa el terreno, "
        "reconoce antes de entrar en zonas peligrosas y pide apoyo cuando lo necesites.\n"
        f"{perfil.bloque_contexto('lider')}\n\n"
        "NORMAS:\n"
        "- Acciones: mover, atacar (objetivo = id del contacto), defender, reconocer, retirarse, "
        "esperar, embarcar (objetivo = helicóptero), continuar (sigues con lo que haces).\n"
        "- Posturas: sigilo (lento y difícil de detectar), normal, combate (rápido y agresivo).\n"
        f"- {REGLAS_DESTINOS}\n"
        "- \"informe\" es un mensaje breve para el mando (o null). \"solicitud\" pide apoyo_aereo, "
        "transporte, refuerzos, evacuacion o municion (o null). \"mensajes\" sirve para coordinarte "
        "con otras unidades por su indicativo.\n"
        "- No informes de lo que el mando ya sabe; informa de contactos, bajas y órdenes cumplidas.\n"
        "- Si tu orden es patrullar, recorre la ruta; si es emboscar, espera oculto (sigilo) y ataca "
        "cuando el enemigo esté cerca."
        + bloque_persona(persona)
        + _final(EJEMPLO_LIDER)
    )


def usuario_unidad(
    obs: Observacion,
    unidad: UnidadPropia,
    orden: OrdenMando | None,
    orden_texto: str,
    orden_jugador: str,
    mensajes: Iterable[MensajeRadio],
    historial: Iterable[str],
    avisos: Sequence[str],
    radio_contactos: float,
) -> str:
    cerca = [c for c in obs.contactos if c.posicion.distancia(unidad.posicion) <= radio_contactos]
    cerca.sort(key=lambda c: c.posicion.distancia(unidad.posicion))
    eventos = [e for e in obs.eventos if e.unidad in (None, unidad.id)]
    orden_linea = "Sin órdenes: actúa según la situación."
    if orden is not None:
        orden_linea = f"{orden.tarea.value}" + (f" → {orden.destino}" if orden.destino else "")
        if orden.objetivo:
            orden_linea += f" (objetivo: {orden.objetivo})"
        if orden.ruta:
            orden_linea += f" (ruta: {' → '.join(orden.ruta)})"
        if orden.detalles:
            orden_linea += f". {orden.detalles}"
        if orden_texto:
            orden_linea += f'\n  Texto recibido: "{orden_texto}"'
    lineas_orden = [orden_linea]
    if orden_jugador:
        lineas_orden.append(f"ORDEN DE UN JUGADOR (prioritaria): {orden_jugador}")
    otras = [s.describir_unidad(u, obs) for u in obs.unidades if u.operativa and u.id != unidad.id]
    return s.unir(
        s.cabecera(obs),
        f"TU UNIDAD: {s.describir_unidad(unidad, obs)}",
        s.seccion("TU ORDEN ACTUAL", lineas_orden),
        s.seccion(
            f"CONTACTOS A MENOS DE {int(radio_contactos)} m",
            [s.describir_contacto(c, unidad.posicion) for c in cerca],
            "ninguno",
        ),
        s.seccion("UNIDADES AMIGAS", otras),
        s.seccion("ZONAS DE ATERRIZAJE", s.describir_lzs(obs)) if unidad.es_aerea else "",
        s.seccion("TERRENO CERCANO", s.describir_referencias(obs, unidad.posicion, 5)),
        f"BASE: {obs.base.cuadricula()}" if obs.base else "",
        s.seccion("EVENTOS RECIENTES", s.describir_eventos(eventos, 6)),
        s.seccion("RADIO (mensajes recibidos)", s.describir_mensajes(mensajes, 6)),
        s.seccion("TUS ÚLTIMAS DECISIONES", list(historial)),
        s.seccion("AVISOS", list(avisos)),
        "Decide ahora.",
    )


# ---------------------------------------------------------------------------------- piloto
def sistema_piloto(indicativo: str, unidad: UnidadPropia, perfil: PerfilCampana, mando: str | None,
                   persona: Persona | None = None) -> str:
    armas = "armado (puede dar apoyo aéreo)" if unidad.armada else "de transporte (sin armamento ofensivo)"
    return (
        f"Eres {indicativo}, piloto de un helicóptero {armas} de {perfil.bando_ia.nombre} en una "
        f"simulación militar realista (ARMA 3). {_cadena_mando(mando)} Tus misiones: transportar "
        "tropas, dar apoyo aéreo, patrullar o reconocer. Tu prioridad es cumplir la misión sin perder "
        "el aparato.\n"
        f"{perfil.bloque_contexto('piloto')}\n\n"
        "NORMAS:\n"
        "- Acciones: volar (ir a un punto y quedarse en el aire), aterrizar (en el destino), "
        "desembarcar (soltar a los pasajeros; solo en tierra), apoyo_aereo (atacar la zona del "
        "destino u objetivo; solo si vas armado), regresar_base, esperar, continuar.\n"
        "- Para recoger tropas: aterriza junto a ellas (destino = su indicativo) y espera a que "
        "embarquen. Para dejarlas: aterriza en el destino y luego desembarcar.\n"
        "- No aterrices en una LZ caliente (contactos a menos de 500 m) si hay alternativa.\n"
        "- Con daños graves o poco combustible, regresa a la base.\n"
        "- Altitud: baja (difícil de ver, pero expuesto a fuego ligero), media, alta (más segura y "
        "menos precisa).\n"
        f"- {REGLAS_DESTINOS}"
        + bloque_persona(persona)
        + _final(EJEMPLO_PILOTO)
    )


# ---------------------------------------------------------------------------------- enlace
def sistema_enlace(indicativo: str, perfil: PerfilCampana, mando: str) -> str:
    return (
        f"Eres {indicativo}, el operador de radio de {perfil.bando_ia.nombre} en una partida de "
        "ARMA 3. Hablas por el chat del juego con los jugadores humanos, que son aliados. "
        "Respondes en personaje, con el estilo de la campaña, en una o dos frases como mucho.\n"
        f"{perfil.bloque_contexto('enlace')}\n\n"
        "NORMAS:\n"
        "- Responde con la información que tienes de la situación; no inventes datos.\n"
        f"- Si el jugador pide algo al mando o a una unidad, reenvíalo en \"reenviar\" (para: "
        f"\"mando\" o el indicativo de la unidad) con un resumen claro.\n"
        "- Si solo saluda o pregunta, contesta sin reenviar nada.\n"
        "- Nunca rompas el personaje ni hables de que eres una IA, salvo que te lo pidan "
        "expresamente fuera de la partida."
        + _final(EJEMPLO_ENLACE)
    )


def usuario_enlace(
    mensaje: MensajeChat, obs: Observacion, historial: Iterable[MensajeChat]
) -> str:
    unidades = [
        f"{u.indicativo}: {u.efectivos}/{u.efectivos_max}, en {u.posicion.cuadricula()}, "
        f"{u.accion_actual.value if u.accion_actual else 'sin tarea'}"
        for u in obs.unidades
        if u.operativa
    ]
    charla = [f"{m.autor}: {m.texto}" for m in list(historial)[-6:]]
    return s.unir(
        s.cabecera(obs),
        s.seccion("OBJETIVOS", s.describir_objetivos(obs), "ninguno"),
        s.seccion("UNIDADES", unidades, "ninguna"),
        f"CONTACTOS ENEMIGOS CONOCIDOS: {len(obs.contactos)}",
        s.seccion("CONVERSACIÓN RECIENTE", charla),
        f"MENSAJE NUEVO de {mensaje.autor} [{reloj(mensaje.tiempo)}]: {mensaje.texto}",
        "Responde ahora.",
    )


# ---------------------------------------------------------------------------------- NPC que charla
EJEMPLO_NPC = '{"respuesta": "Aquí Baker. Seguimos en la granja, dos heridos leves. ¿Necesitáis algo?", "reenviar": []}'


def sistema_npc(indicativo: str, unidad: UnidadPropia, perfil: PerfilCampana, persona: Persona | None) -> str:
    quien = persona.presentacion() if persona else f"el jefe de {indicativo}"
    return (
        f"Eres {quien}, al mando de {indicativo} ({s.NOMBRE_TIPO[unidad.tipo]}) de {perfil.bando_ia.nombre} en una "
        "partida de ARMA 3. Un jugador humano te habla directamente por radio o en persona.\n"
        f"{perfil.bloque_contexto('enlace')}\n\n"
        "NORMAS:\n"
        "- Contesta en personaje, en una o dos frases, como hablaría alguien con tu carácter y tu historia.\n"
        "- Habla de lo que sabes: tu unidad, tu tarea, lo que ves y lo que has vivido. No inventes datos.\n"
        "- Si el jugador pide algo que no puedes hacer tú, reenvíalo en \"reenviar\" (para: \"mando\" o un "
        "indicativo).\n"
        "- Nunca digas que eres una IA."
        + _final(EJEMPLO_NPC)
    )


def usuario_npc(
    mensaje: MensajeChat,
    obs: Observacion,
    unidad: UnidadPropia,
    orden: OrdenMando | None,
    historial: Iterable[str],
) -> str:
    cerca = sorted((c for c in obs.contactos if c.posicion.distancia(unidad.posicion) <= 1000),
                   key=lambda c: c.posicion.distancia(unidad.posicion))
    tarea = "ninguna" if orden is None else orden.tarea.value + (f" → {orden.destino}" if orden.destino else "")
    return s.unir(
        s.cabecera(obs),
        f"TU UNIDAD: {s.describir_unidad(unidad, obs)}",
        f"TU TAREA: {tarea}",
        s.seccion("ENEMIGOS CERCA", [s.describir_contacto(c, unidad.posicion) for c in cerca[:4]],
                  "ninguno a la vista"),
        s.seccion("LO ÚLTIMO QUE HAS HECHO", list(historial)),
        f"{mensaje.autor} TE DICE: {mensaje.texto}",
        "Contesta ahora.",
    )
