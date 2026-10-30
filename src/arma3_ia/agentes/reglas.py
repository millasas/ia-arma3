"""Doctrina por reglas: decisiones sin modelo de lenguaje.

Se usan cuando el modelo falla o tarda demasiado (la IA nunca se queda congelada), como
"reflejos" inmediatos entre decisiones y como base del LLM falso en las pruebas.
"""

from __future__ import annotations

from ..campanas import PerfilCampana
from ..geo import Posicion, describir_relativa, parsear_posicion
from ..modelo import (
    Accion,
    Contacto,
    EstadoUnidad,
    MensajeChat,
    Objetivo,
    Observacion,
    Persona,
    Postura,
    Rasgo,
    TipoAccion,
    TipoObjetivo,
    UnidadPropia,
)
from ..texto import normalizar, recortar
from .esquemas import (
    AccionAire,
    AccionTierra,
    DecisionComandante,
    DecisionLider,
    DecisionPiloto,
    MensajeSaliente,
    OrdenMando,
    RespuestaEnlace,
    Solicitud,
    TareaMando,
    TipoSolicitud,
)
from .situacion import RADIO_LZ_CALIENTE, contactos_cerca, objetivos_pendientes

PROPORCION_CRITICA = 0.35
MORAL_CRITICA = 0.25
LLEGADA_M = 120.0


def _valor(persona: Persona | None) -> float:
    return persona.valor if persona is not None else 0.0


def roto(unidad: UnidadPropia, persona: Persona | None = None) -> bool:
    """¿Ha llegado la unidad a su límite? Los valientes aguantan más, los nerviosos menos."""
    v = _valor(persona)
    return (unidad.proporcion_efectivos < PROPORCION_CRITICA - 0.3 * v
            or unidad.moral < MORAL_CRITICA - 0.3 * v)


def siguiente_punto(unidad: UnidadPropia, puntos: list[Posicion]) -> tuple[Posicion, bool]:
    """Punto de patrulla al que dirigirse y si ya iba hacia él (para no repetir la orden)."""
    destino = unidad.destino_actual
    if destino is not None and unidad.accion_actual is not None:
        for p in puntos:
            if p.distancia(destino) < 30 and unidad.posicion.distancia(p) > LLEGADA_M:
                return p, True
    cercano = min(range(len(puntos)), key=lambda i: puntos[i].distancia(unidad.posicion))
    if puntos[cercano].distancia(unidad.posicion) <= LLEGADA_M:
        return puntos[(cercano + 1) % len(puntos)], False
    return puntos[cercano], False


def _mas_cercano(contactos: list[Contacto], punto: Posicion) -> Contacto | None:
    return min(contactos, key=lambda c: c.posicion.distancia(punto), default=None)


def _mayor(contactos: list[Contacto]) -> Contacto | None:
    return max(contactos, key=lambda c: c.efectivos_estimados or 1, default=None)


def punto_seguro(obs: Observacion, unidad: UnidadPropia) -> Posicion:
    """Base propia o, si no hay, 600 m en dirección contraria al enemigo más cercano."""
    if obs.base is not None:
        return obs.base
    amenaza = _mas_cercano(obs.contactos, unidad.posicion)
    if amenaza is None:
        return unidad.posicion
    rumbo = amenaza.posicion.rumbo(unidad.posicion)
    destino = unidad.posicion.desplazar(rumbo, 600)
    limites = obs.limites()
    return destino.limitar(*limites) if limites else destino


def _lz_para(obs: Observacion, objetivo: Objetivo) -> Posicion | None:
    """LZ fría más cercana al objetivo entre 300 y 1500 m."""
    candidatas = []
    for z in obs.zonas_aterrizaje:
        d = z.posicion.distancia(objetivo.posicion)
        if 300 <= d <= 1500 and not contactos_cerca(obs, z.posicion, RADIO_LZ_CALIENTE):
            candidatas.append((d, z.posicion))
    return min(candidatas, key=lambda par: par[0])[1] if candidatas else None


# ---------------------------------------------------------------------------------- comandante
def decision_comandante(obs: Observacion, perfil: PerfilCampana | None = None) -> DecisionComandante:
    pendientes = sorted(objetivos_pendientes(obs), key=lambda o: o.prioridad)
    tierra = [u for u in obs.operativas(aereas=False) if u.estado != EstadoUnidad.EMBARCADA]
    aire = obs.operativas(aereas=True)
    ordenes: list[OrdenMando] = []

    if not pendientes:
        for u in tierra:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.DEFENDER,
                                      destino=u.posicion.cuadricula(), detalles="Consolidad la posición."))
        for h in aire:
            ordenes.append(OrdenMando(unidad=h.indicativo, tarea=TareaMando.REGRESAR_BASE))
        return DecisionComandante(evaluacion="No quedan objetivos pendientes.",
                                  intencion="Consolidar y replegar los medios aéreos.", ordenes=ordenes)

    obj = pendientes[0]
    destino_obj = obj.posicion.cuadricula()
    zona = obj.posicion
    if obj.tipo == TipoObjetivo.EXTRAER:
        # En una extracción el peligro está donde se encuentran las unidades a rescatar.
        a_rescatar = [u for u in tierra if not obj.unidades or u.id in obj.unidades]
        lejos = [u for u in a_rescatar if u.posicion.distancia(obj.posicion) > obj.radio_m]
        if lejos:
            zona = lejos[0].posicion
    amenazas = contactos_cerca(obs, zona, obj.radio_m + 500)
    principal = _mayor(amenazas)
    transportadas: set[str] = set()

    # Medios aéreos
    for h in aire:
        if h.danos > 0.6 or h.combustible < 0.25:
            ordenes.append(OrdenMando(unidad=h.indicativo, tarea=TareaMando.REGRESAR_BASE,
                                      detalles="Daños o combustible críticos."))
            continue
        if h.armada and principal is not None:
            ordenes.append(OrdenMando(unidad=h.indicativo, tarea=TareaMando.APOYO_AEREO,
                                      destino=principal.posicion.cuadricula(), objetivo=principal.id,
                                      detalles="Batid la posición antes del asalto."))
            continue
        if h.capacidad > 0 and not h.pasajeros and obj.tipo != TipoObjetivo.DEFENDER:
            lejanas = [
                u for u in tierra
                if u.posicion.distancia(obj.posicion) > 1500 and u.id not in transportadas
                and (obj.tipo != TipoObjetivo.EXTRAER or not obj.unidades or u.id in obj.unidades)
            ]
            # Para una extracción el helicóptero lleva a la unidad al propio punto de extracción.
            lz = obj.posicion if obj.tipo == TipoObjetivo.EXTRAER else _lz_para(obs, obj)
            if lejanas and lz is not None:
                carga = min(lejanas, key=lambda u: u.posicion.distancia(h.posicion))
                transportadas.add(carga.id)
                ordenes.append(OrdenMando(unidad=h.indicativo, tarea=TareaMando.TRANSPORTE,
                                          destino=lz.cuadricula(), objetivo=carga.indicativo))

    # Unidades de tierra
    tierra_libres = sorted(
        (u for u in tierra if u.id not in transportadas),
        key=lambda u: u.posicion.distancia(obj.posicion),
    )
    for i, u in enumerate(tierra_libres):
        if u.proporcion_efectivos < PROPORCION_CRITICA:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.RETIRARSE,
                                      destino=punto_seguro(obs, u).cuadricula(),
                                      detalles="Demasiadas bajas: replegaos."))
            continue
        if obj.tipo == TipoObjetivo.DEFENDER:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.DEFENDER,
                                      destino=destino_obj, detalles=f"Objetivo: {obj.nombre}."))
        elif obj.tipo == TipoObjetivo.EXTRAER:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.MOVER,
                                      destino=destino_obj, detalles=f"Objetivo: {obj.nombre}."))
        elif obj.tipo == TipoObjetivo.RECONOCER:
            tarea = TareaMando.RECONOCER if i == 0 else TareaMando.DEFENDER
            destino = destino_obj if i == 0 else u.posicion.cuadricula()
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=tarea, destino=destino))
        elif amenazas:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.ATACAR, destino=destino_obj,
                                      objetivo=principal.id if principal else None,
                                      detalles=f"Asaltad {obj.nombre} en coordinación."))
        elif i == 0:
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.RECONOCER, destino=destino_obj,
                                      detalles=f"Reconoced {obj.nombre}."))
        else:
            apoyo = obj.posicion.hacia(u.posicion, obj.radio_m + 300)
            ordenes.append(OrdenMando(unidad=u.indicativo, tarea=TareaMando.MOVER,
                                      destino=apoyo.cuadricula(),
                                      detalles=f"Posición de apoyo junto a {obj.nombre}."))

    evaluacion = (
        f"{len(obs.contactos)} contacto(s) conocidos, {len(amenazas)} cerca del objetivo; "
        f"{len(tierra) + len(aire)} unidades operativas."
    )
    intencion = f"Prioridad: {obj.nombre} ({obj.tipo.value})." + (
        " Batir al enemigo y asaltar." if amenazas else " Reconocer y ocupar."
    )
    return DecisionComandante(evaluacion=evaluacion, intencion=intencion, ordenes=ordenes)


# ---------------------------------------------------------------------------------- líder
def decision_lider(
    unidad: UnidadPropia, orden: OrdenMando | None, obs: Observacion, persona: Persona | None = None
) -> DecisionLider:
    if unidad.estado == EstadoUnidad.EMBARCADA:
        return DecisionLider(evaluacion="Embarcados, esperando el desembarco.", accion=AccionTierra.CONTINUAR)
    cerca = sorted(
        contactos_cerca(obs, unidad.posicion, 600), key=lambda c: c.posicion.distancia(unidad.posicion)
    )
    cercano = cerca[0] if cerca else None
    informe = None
    if cercano is not None and (unidad.bajo_fuego or unidad.en_contacto):
        informe = (
            f"Contacto: {len(cerca)} grupo(s); el más cercano {cercano.id} "
            f"{describir_relativa(unidad.posicion, cercano.posicion)}."
        )

    if roto(unidad, persona):
        return DecisionLider(
            evaluacion="Bajas graves o moral rota: hay que romper el contacto.",
            accion=AccionTierra.RETIRARSE,
            destino=punto_seguro(obs, unidad).cuadricula(),
            postura=Postura.COMBATE,
            informe=f"Nos replegamos con {unidad.efectivos}/{unidad.efectivos_max} efectivos.",
        )

    solicitud = None
    if unidad.bajo_fuego and cercano is not None and unidad.proporcion_efectivos < 0.7:
        solicitud = Solicitud(tipo=TipoSolicitud.APOYO_AEREO, destino=cercano.posicion.cuadricula(),
                              detalles="Fuego intenso, necesitamos apoyo.")

    agresivo = persona is not None and persona.tiene(Rasgo.AGRESIVO, Rasgo.IMPULSIVO, Rasgo.VALIENTE)
    if orden is None:
        if cercano is not None and agresivo:
            return DecisionLider(evaluacion="Sin órdenes, pero no vamos a esperar a que vengan.",
                                 accion=AccionTierra.ATACAR, objetivo=cercano.id,
                                 destino=cercano.posicion.cuadricula(), postura=Postura.COMBATE,
                                 informe=informe, solicitud=solicitud)
        if cercano is not None:
            return DecisionLider(evaluacion="Sin órdenes y con enemigo cerca: defender.",
                                 accion=AccionTierra.DEFENDER, destino=unidad.posicion.cuadricula(),
                                 informe=informe, solicitud=solicitud)
        return DecisionLider(evaluacion="Sin órdenes: a la espera.", accion=AccionTierra.ESPERAR)

    destino = orden.destino
    tarea = orden.tarea
    lugares, limites = obs.lugares(), obs.limites()
    if tarea == TareaMando.PATRULLAR:
        if cercano is not None and cercano.posicion.distancia(unidad.posicion) < (500 if agresivo else 350):
            return DecisionLider(evaluacion="Contacto durante la patrulla: lo enfrentamos.",
                                 accion=AccionTierra.ATACAR, objetivo=cercano.id,
                                 destino=cercano.posicion.cuadricula(), postura=Postura.COMBATE,
                                 informe=informe, solicitud=solicitud)
        textos = orden.ruta or ([destino] if destino else [])
        puntos = [p for p in (parsear_posicion(x, lugares, limites) for x in textos) if p is not None]
        if not puntos:
            return DecisionLider(evaluacion="Patrulla sin ruta: vigilamos la zona.", accion=AccionTierra.DEFENDER,
                                 destino=unidad.posicion.cuadricula(), informe=informe)
        punto, en_marcha = siguiente_punto(unidad, puntos)
        if en_marcha:
            return DecisionLider(evaluacion="Seguimos la ruta de patrulla.", accion=AccionTierra.CONTINUAR,
                                 informe=informe)
        return DecisionLider(evaluacion="Siguiente tramo de la patrulla.", accion=AccionTierra.MOVER,
                             destino=punto.cuadricula(), postura=Postura.NORMAL, informe=informe)
    if tarea == TareaMando.EMBOSCAR:
        puesto = parsear_posicion(destino, lugares, limites) if destino else None
        puesto = puesto or unidad.posicion
        alcance = 500 if agresivo else 400  # se abre fuego en cuanto entra en alcance de las armas
        if cercano is not None and cercano.posicion.distancia(unidad.posicion) < alcance:
            return DecisionLider(evaluacion="El enemigo ha entrado en la zona de muerte: ¡fuego!",
                                 accion=AccionTierra.ATACAR, objetivo=cercano.id,
                                 destino=cercano.posicion.cuadricula(), postura=Postura.COMBATE,
                                 informe=informe)
        if unidad.posicion.distancia(puesto) > LLEGADA_M:
            return DecisionLider(evaluacion="Vamos a la posición de emboscada sin ser vistos.",
                                 accion=AccionTierra.MOVER, destino=puesto.cuadricula(), postura=Postura.SIGILO)
        return DecisionLider(evaluacion="Emboscada preparada: en silencio y a la espera.",
                             accion=AccionTierra.DEFENDER, destino=puesto.cuadricula(), postura=Postura.SIGILO)
    if tarea == TareaMando.ATACAR:
        objetivo = orden.objetivo
        if cercano is not None and cercano.posicion.distancia(unidad.posicion) < 400:
            objetivo, destino = cercano.id, cercano.posicion.cuadricula()
        return DecisionLider(evaluacion="Ejecutamos el asalto.", accion=AccionTierra.ATACAR,
                             destino=destino, objetivo=objetivo, postura=Postura.COMBATE,
                             informe=informe, solicitud=solicitud)
    if tarea == TareaMando.RECONOCER:
        return DecisionLider(evaluacion="Reconocimiento discreto.", accion=AccionTierra.RECONOCER,
                             destino=destino, postura=Postura.SIGILO, informe=informe)
    if tarea == TareaMando.DEFENDER:
        return DecisionLider(evaluacion="Ocupamos posición defensiva.", accion=AccionTierra.DEFENDER,
                             destino=destino or unidad.posicion.cuadricula(), informe=informe,
                             solicitud=solicitud)
    if tarea == TareaMando.RETIRARSE:
        return DecisionLider(evaluacion="Repliegue ordenado.", accion=AccionTierra.RETIRARSE,
                             destino=destino or punto_seguro(obs, unidad).cuadricula(),
                             postura=Postura.COMBATE)
    if tarea == TareaMando.EMBARCAR:
        return DecisionLider(evaluacion="Vamos al helicóptero.", accion=AccionTierra.EMBARCAR,
                             objetivo=orden.objetivo)
    if tarea == TareaMando.MOVER:
        postura = Postura.COMBATE if cercano is not None else Postura.NORMAL
        return DecisionLider(evaluacion="En movimiento.", accion=AccionTierra.MOVER, destino=destino,
                             postura=postura, informe=informe, solicitud=solicitud)
    return DecisionLider(evaluacion="Orden no aplicable a infantería: a la espera.",
                         accion=AccionTierra.ESPERAR, informe=informe)


# ---------------------------------------------------------------------------------- piloto
def decision_piloto(
    unidad: UnidadPropia, orden: OrdenMando | None, obs: Observacion
) -> DecisionPiloto:
    if unidad.danos > 0.6 or unidad.combustible < 0.2:
        return DecisionPiloto(evaluacion="Daños o combustible críticos.", accion=AccionAire.REGRESAR_BASE,
                              informe="Regreso a la base: aparato tocado o sin combustible.")
    if orden is None:
        if unidad.en_vuelo and unidad.accion_actual is None:
            return DecisionPiloto(evaluacion="Sin misión: regreso.", accion=AccionAire.REGRESAR_BASE)
        return DecisionPiloto(evaluacion="Sin misión: a la espera.", accion=AccionAire.ESPERAR)

    lugares = obs.lugares()
    limites = obs.limites()
    tarea = orden.tarea
    if tarea == TareaMando.TRANSPORTE:
        carga = obs.buscar_unidad(orden.objetivo)
        destino = parsear_posicion(orden.destino, lugares, limites)
        if carga is None or not carga.operativa or destino is None:
            return DecisionPiloto(evaluacion="Transporte imposible.", accion=AccionAire.REGRESAR_BASE,
                                  informe="No puedo completar el transporte: regreso a la base.")
        if carga.id in unidad.pasajeros:
            if not unidad.en_vuelo and unidad.posicion.distancia(destino) < 200:
                return DecisionPiloto(evaluacion="En el destino: desembarco.", accion=AccionAire.DESEMBARCAR,
                                      informe=f"{carga.indicativo} desembarcando en {destino.cuadricula()}.")
            return DecisionPiloto(evaluacion="Tropas a bordo, rumbo al destino.", accion=AccionAire.ATERRIZAR,
                                  destino=orden.destino, altitud=None,
                                  informe=f"{carga.indicativo} a bordo. En ruta.")
        if carga.estado == EstadoUnidad.EMBARCADA:
            return DecisionPiloto(evaluacion="La unidad va en otro aparato.", accion=AccionAire.REGRESAR_BASE)
        if carga.posicion.distancia(destino) < 300:
            return DecisionPiloto(evaluacion="Transporte completado.", accion=AccionAire.REGRESAR_BASE,
                                  informe="Transporte completado, regreso a la base.")
        if not unidad.en_vuelo and unidad.posicion.distancia(carga.posicion) < 300:
            return DecisionPiloto(evaluacion="Esperando a que embarquen.", accion=AccionAire.ESPERAR)
        return DecisionPiloto(evaluacion="Voy a recoger a la unidad.", accion=AccionAire.ATERRIZAR,
                              destino=carga.indicativo, informe=f"En camino para recoger a {carga.indicativo}.")
    if tarea in (TareaMando.APOYO_AEREO, TareaMando.ATACAR):
        if not unidad.armada:
            return DecisionPiloto(evaluacion="No voy armado.", accion=AccionAire.ESPERAR,
                                  informe="Negativo: aparato de transporte sin armamento.")
        contacto = obs.contacto(orden.objetivo)
        blanco = contacto.posicion if contacto else parsear_posicion(orden.destino, lugares, limites)
        if blanco is None:
            return DecisionPiloto(evaluacion="Sin blanco claro.", accion=AccionAire.ESPERAR)
        if not contactos_cerca(obs, blanco, 600) and unidad.posicion.distancia(blanco) < 900:
            return DecisionPiloto(evaluacion="Zona despejada.", accion=AccionAire.REGRESAR_BASE,
                                  informe="Zona batida, sin enemigos a la vista. Regreso.")
        return DecisionPiloto(evaluacion="Apoyo aéreo sobre el blanco.", accion=AccionAire.APOYO_AEREO,
                              destino=blanco.cuadricula(), objetivo=contacto.id if contacto else None,
                              informe=None)
    if tarea == TareaMando.PATRULLAR:
        textos = orden.ruta or ([orden.destino] if orden.destino else [])
        puntos = [p for p in (parsear_posicion(x, lugares, limites) for x in textos) if p is not None]
        if puntos:
            punto, en_marcha = siguiente_punto(unidad, puntos)
            if en_marcha:
                return DecisionPiloto(evaluacion="Sigo la ruta de patrulla.", accion=AccionAire.CONTINUAR)
            return DecisionPiloto(evaluacion="Patrulla aérea.", accion=AccionAire.VOLAR, destino=punto.cuadricula())
    if tarea in (TareaMando.MOVER, TareaMando.RECONOCER, TareaMando.PATRULLAR):
        return DecisionPiloto(evaluacion="Vuelo al punto indicado.", accion=AccionAire.VOLAR,
                              destino=orden.destino)
    if tarea in (TareaMando.REGRESAR_BASE, TareaMando.RETIRARSE):
        return DecisionPiloto(evaluacion="Regreso a la base.", accion=AccionAire.REGRESAR_BASE)
    return DecisionPiloto(evaluacion="A la espera.", accion=AccionAire.ESPERAR)


# ---------------------------------------------------------------------------------- enlace
PALABRAS_SITUACION = ("situacion", "estado", "como va", "informe", "sitrep", "que pasa", "como vais")


def respuesta_enlace(mensaje: MensajeChat, obs: Observacion, indicativo: str) -> RespuestaEnlace:
    texto = normalizar(mensaje.texto)
    if any(p in texto for p in PALABRAS_SITUACION):
        pendientes = len(objetivos_pendientes(obs))
        unidades = ", ".join(
            f"{u.indicativo} {u.efectivos}/{u.efectivos_max}" for u in obs.unidades if u.operativa
        )
        return RespuestaEnlace(
            respuesta=f"{mensaje.autor}, aquí {indicativo}: {pendientes} objetivo(s) pendiente(s), "
            f"{len(obs.contactos)} contacto(s) conocidos. Unidades: {unidades or 'ninguna'}."
        )
    return RespuestaEnlace(
        respuesta=f"{mensaje.autor}, aquí {indicativo}. Recibido, lo transmito al mando.",
        reenviar=[MensajeSaliente(para="mando", texto=recortar(mensaje.texto, 300))],
    )


def respuesta_npc(
    mensaje: MensajeChat,
    unidad: UnidadPropia,
    obs: Observacion,
    indicativo: str,
    orden: OrdenMando | None,
    persona: Persona | None = None,
) -> RespuestaEnlace:
    """Lo que contesta un jefe de grupo sin modelo: su estado, su tarea y lo que ve, con algo de carácter."""
    cerca = sorted(contactos_cerca(obs, unidad.posicion, 800), key=lambda c: c.posicion.distancia(unidad.posicion))
    tarea = "sin órdenes" if orden is None else f"con la tarea de {orden.tarea.value.replace('_', ' ')}"
    partes = [f"{mensaje.autor}, aquí {indicativo}."]
    if persona is not None and unidad.bajo_fuego and persona.tiene(Rasgo.NERVIOSO, Rasgo.NOVATO):
        partes.append("¡Nos están dando duro!")
    elif persona is not None and persona.tiene(Rasgo.VALIENTE, Rasgo.AGRESIVO, Rasgo.VETERANO):
        partes.append("Todo bajo control.")
    partes.append(f"Somos {unidad.efectivos} de {unidad.efectivos_max}, {tarea}.")
    if cerca:
        partes.append(f"Enemigo a {describir_relativa(unidad.posicion, cerca[0].posicion)}.")
    reenviar = []
    norm = normalizar(mensaje.texto)
    if any(p in norm for p in ("apoyo", "ayuda", "refuerz", "evacu", "municion")):
        reenviar.append(MensajeSaliente(para="mando", texto=recortar(mensaje.texto, 300)))
        partes.append("Lo paso al mando.")
    return RespuestaEnlace(respuesta=" ".join(partes), reenviar=reenviar)


# ---------------------------------------------------------------------------------- reflejos
def reflejo(unidad: UnidadPropia, obs: Observacion, persona: Persona | None = None) -> Accion | None:
    """Reacción inmediata sin pensar (entre decisiones). None si no hace falta."""
    if not unidad.operativa or unidad.estado == EstadoUnidad.EMBARCADA:
        return None
    if unidad.es_aerea:
        critico = unidad.danos > 0.7 or (unidad.en_vuelo and unidad.combustible < 0.15)
        if critico and unidad.accion_actual != TipoAccion.REGRESAR_BASE:
            return Accion(unidad=unidad.id, tipo=TipoAccion.REGRESAR_BASE,
                          motivo="Reflejo: aparato en estado crítico.")
        return None
    if (
        unidad.bajo_fuego
        and unidad.proporcion_efectivos < 0.4 - 0.3 * _valor(persona)
        and unidad.accion_actual != TipoAccion.RETIRARSE
    ):
        return Accion(unidad=unidad.id, tipo=TipoAccion.RETIRARSE, destino=punto_seguro(obs, unidad),
                      postura=Postura.COMBATE, motivo="Reflejo: romper el contacto.")
    return None
