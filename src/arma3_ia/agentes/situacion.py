"""Convierte una Observación en texto compacto y claro para el modelo (partes de situación)."""

from __future__ import annotations

from collections.abc import Iterable

from ..comunicacion import MensajeRadio
from ..geo import Posicion, describir_relativa
from ..modelo import (
    NOMBRE_TIPO,
    Contacto,
    EstadoObjetivo,
    EstadoUnidad,
    Evento,
    Observacion,
    UnidadPropia,
)
from ..texto import recortar, reloj

RADIO_LZ_CALIENTE = 500.0
ALTITUDES = {"baja": "a baja altura", "media": "a media altura", "alta": "a gran altura"}


def porcentaje(valor: float) -> str:
    return f"{round(valor * 100)}%"


def nivel(valor: float) -> str:
    return "alta" if valor >= 0.7 else "media" if valor >= 0.4 else "baja"


def cabecera(obs: Observacion) -> str:
    c = obs.condiciones
    return (
        f"HORA DE MISIÓN {reloj(obs.tiempo)}. Condiciones: {c.hora}, {c.clima}, "
        f"visibilidad {int(c.visibilidad_m)} m, viento {c.viento_ms:.0f} m/s."
    )


def describir_unidad(u: UnidadPropia, obs: Observacion | None = None) -> str:
    accion = u.accion_actual.value if u.accion_actual else "ninguna"
    destino = f" hacia {u.destino_actual.cuadricula()}" if u.destino_actual else ""
    if u.es_aerea:
        tipo = "helicóptero armado" if u.armada else "helicóptero de transporte"
        situacion = "en vuelo" if u.en_vuelo else "en tierra"
        pasajeros = ""
        if u.pasajeros:
            nombres = [
                (obs.unidad(p).indicativo if obs and obs.unidad(p) else p)  # type: ignore[union-attr]
                for p in u.pasajeros
            ]
            pasajeros = f"; lleva a {', '.join(nombres)}"
        base = ""
        if obs and obs.base and u.posicion.distancia(obs.base) < 300 and not u.en_vuelo:
            base = " (en la base)"
        return (
            f"{u.indicativo} ({tipo}, capacidad {u.capacidad} escuadra(s)) {situacion} en "
            f"{u.posicion.cuadricula()}{base}; combustible {porcentaje(u.combustible)}, daños "
            f"{porcentaje(u.danos)}{pasajeros} | acción: {accion}{destino}"
        )
    estado = ""
    if u.estado == EstadoUnidad.EMBARCADA:
        transporte = obs.unidad(u.embarcada_en) if obs and u.embarcada_en else None
        estado = f" [EMBARCADA en {transporte.indicativo if transporte else u.embarcada_en}]"
    alertas = (" | EN CONTACTO" if u.en_contacto else "") + (" | BAJO FUEGO" if u.bajo_fuego else "")
    return (
        f"{u.indicativo} ({NOMBRE_TIPO[u.tipo]}, {u.efectivos}/{u.efectivos_max} efectivos, "
        f"munición {porcentaje(u.municion)}, moral {nivel(u.moral)}) en {u.posicion.cuadricula()}"
        f"{estado}, postura {u.postura.value} | acción: {accion}{destino}{alertas}"
    )


def describir_contacto(c: Contacto, desde: Posicion | None = None) -> str:
    tipo = NOMBRE_TIPO[c.tipo] + (" en vuelo" if c.en_vuelo else "")
    efectivos = f" ~{c.efectivos_estimados}" if c.efectivos_estimados else ""
    relativa = f", {describir_relativa(desde, c.posicion)}" if desde else ""
    return (
        f"{c.id}: {tipo}{efectivos} en {c.posicion.cuadricula()} "
        f"(visto hace {int(c.visto_hace_s)} s{relativa})"
    )


def describir_objetivos(obs: Observacion) -> list[str]:
    lineas = []
    for o in sorted(obs.objetivos, key=lambda o: o.prioridad):
        extra = f", aguantar hasta {reloj(o.hasta_s)}" if o.hasta_s else ""
        opcional = "" if o.obligatorio else " (opcional)"
        lineas.append(
            f"[{o.estado.value}] {o.id} \"{o.nombre}\" ({o.tipo.value}{opcional}) en "
            f"{o.posicion.cuadricula()}, radio {int(o.radio_m)} m{extra}"
            + (f". {o.descripcion}" if o.descripcion else "")
        )
    return lineas


def contactos_cerca(obs: Observacion, punto: Posicion, radio: float) -> list[Contacto]:
    return [c for c in obs.contactos if c.posicion.distancia(punto) <= radio]


def describir_lzs(obs: Observacion) -> list[str]:
    lineas = []
    for z in obs.zonas_aterrizaje:
        cerca = contactos_cerca(obs, z.posicion, RADIO_LZ_CALIENTE)
        estado = (
            f"CALIENTE: {len(cerca)} contacto(s) a menos de {int(RADIO_LZ_CALIENTE)} m"
            if cerca
            else "fría (sin contactos conocidos cerca)"
        )
        lineas.append(f"{z.nombre} ({z.id}) en {z.posicion.cuadricula()}: {estado}")
    return lineas


def describir_referencias(obs: Observacion, cerca_de: Posicion | None = None, maximo: int = 8) -> list[str]:
    referencias = list(obs.referencias)
    if cerca_de is not None:
        referencias.sort(key=lambda r: r.posicion.distancia(cerca_de))
    lineas = []
    for r in referencias[:maximo]:
        relativa = f", {describir_relativa(cerca_de, r.posicion)}" if cerca_de else ""
        lineas.append(f"{r.nombre} ({r.tipo}) en {r.posicion.cuadricula()}{relativa}")
    return lineas


def describir_eventos(eventos: Iterable[Evento], maximo: int = 8) -> list[str]:
    lista = [e for e in eventos if e.tipo != "radio"]
    return [f"[{reloj(e.tiempo)}] {e.descripcion}" for e in lista[-maximo:]]


def describir_mensajes(mensajes: Iterable[MensajeRadio], maximo: int = 8) -> list[str]:
    lista = list(mensajes)[-maximo:]
    return [f"{m.de_indicativo} ({m.tipo.value}): {recortar(m.texto, 300)}" for m in lista]


def seccion(titulo: str, lineas: list[str], vacio: str | None = None) -> str:
    if not lineas:
        return f"{titulo}: {vacio}" if vacio else ""
    return f"{titulo}:\n" + "\n".join(f"- {linea}" for linea in lineas)


def unir(*bloques: str) -> str:
    return "\n".join(b for b in bloques if b)


def objetivos_pendientes(obs: Observacion) -> list:
    return [o for o in obs.objetivos if o.estado == EstadoObjetivo.PENDIENTE]
