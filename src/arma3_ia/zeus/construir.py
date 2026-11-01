"""De lo que imagina Zeus (PlanMision) a una misión jugable (Escenario).

El plan viene de un modelo pequeño, así que aquí se valida y se repara todo: lugares que no
existen, coordenadas fuera del mapa, terrenos que no están instalados, helicópteros en 1944,
bandos sin objetivos, nombres repetidos... Cada arreglo queda anotado en ``avisos``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..agentes.esquemas import OrdenMando, TareaMando
from ..ambientacion import PerfilCampana
from ..geo import Posicion, parsear_posicion
from ..modelo import (
    Condiciones,
    Mapa,
    Objetivo,
    ObjetoMapa,
    Referencia,
    TipoObjetivo,
    TipoUnidad,
    ZonaAterrizaje,
)
from ..simulador.escenario import BandoEscenario, Escenario, UnidadEscenario
from ..simulador.motor import FACTOR_CLIMA, VISIBILIDAD_HORA
from ..texto import normalizar
from .esquemas import BandoPlan, ObjetivoPlan, ObjetoPlan, PlanMision, SucesoPlan, UnidadPlan
from .mapas import elegir_terreno

OBJETIVO_CONTRARIO = {
    TipoObjetivo.CAPTURAR: TipoObjetivo.DEFENDER,
    TipoObjetivo.DEFENDER: TipoObjetivo.CAPTURAR,
    TipoObjetivo.RECONOCER: TipoObjetivo.DEFENDER,
    TipoObjetivo.DESTRUIR: TipoObjetivo.DEFENDER,
    TipoObjetivo.EXTRAER: TipoObjetivo.DESTRUIR,
}


@dataclass
class MisionConstruida:
    escenario: Escenario
    avisos: list[str] = field(default_factory=list)
    # Lo que Zeus deja preparado para que pase durante la misión (refuerzos, niebla...).
    sucesos: list[SucesoPlan] = field(default_factory=list)


def _slug(texto: str) -> str:
    return normalizar(texto).replace(" ", "_")[:40] or "x"


def construir_escenario(
    plan: PlanMision,
    perfil: PerfilCampana,
    *,
    duracion_min: int = 60,
    terrenos: list[str] | None = None,
    terreno_campana: str | None = None,
    id_mision: str | None = None,
    semilla: int | None = None,
) -> MisionConstruida:
    rng = random.Random(semilla)
    avisos: list[str] = []
    terrenos = terrenos or []
    terreno = elegir_terreno(plan.terreno or terreno_campana, terrenos, terreno_campana)
    if plan.terreno and terrenos and terreno != plan.terreno and plan.terreno not in terrenos:
        avisos.append(f"El terreno '{plan.terreno}' no está instalado: uso {terreno}.")
    lado = plan.tamano_km * 1000.0
    limites = (lado, lado)
    centro = Posicion(x=lado / 2, y=lado / 2)

    # Lugares
    referencias: list[Referencia] = []
    lzs: list[ZonaAterrizaje] = []
    lugares: dict[str, Posicion] = {}
    for lugar in plan.lugares:
        pos = parsear_posicion(lugar.posicion, lugares, limites)
        if pos is None:
            pos = centro.desplazar(rng.uniform(0, 360), rng.uniform(300, lado * 0.35))
            avisos.append(f"El lugar '{lugar.nombre}' no tenía una posición válida: lo coloco en {pos.cuadricula()}.")
        pos = pos.limitar(lado, lado)
        lugares[lugar.nombre] = pos
        if lugar.tipo == "lz":
            lzs.append(ZonaAterrizaje(id=_slug(lugar.nombre), nombre=lugar.nombre, posicion=pos))
        else:
            referencias.append(Referencia(nombre=lugar.nombre, tipo=lugar.tipo, posicion=pos, radio_m=lugar.radio_m))

    def situar(texto: str | None, cerca: Posicion, nombre: str) -> Posicion:
        pos = parsear_posicion(texto, lugares, limites) if texto else None
        if pos is None:
            avisos.append(f"No reconozco la posición '{texto}' de {nombre}: la sitúo cerca.")
            pos = cerca.desplazar(rng.uniform(0, 360), rng.uniform(150, 500))
        return pos.limitar(lado, lado)

    bandos_plan = _bandos_validos(plan, avisos)
    jugadores = plan.bando_jugadores if any(b.bando == plan.bando_jugadores for b in bandos_plan) else (
        bandos_plan[0].bando)
    ids_por_indicativo: dict[str, str] = {}
    usados: set[str] = set()

    # Unidades
    unidades_por_bando: dict[str, list[UnidadEscenario]] = {}
    for i, bp in enumerate(bandos_plan):
        esquina = Posicion(x=lado * (0.15 if i == 0 else 0.85), y=lado * (0.15 if i == 0 else 0.85))
        lista: list[UnidadEscenario] = []
        ocupados: dict[str, int] = {}
        for up in bp.unidades:
            if up.tipo == TipoUnidad.HELICOPTERO and not perfil.medios_aereos:
                avisos.append(f"{up.indicativo}: en esta campaña no hay helicópteros; lo quito.")
                continue
            if up.tipo == TipoUnidad.DESCONOCIDO:
                up = up.model_copy(update={"tipo": TipoUnidad.INFANTERIA})
            base_id = _slug(up.indicativo)
            uid, n = base_id, 2
            while uid in usados:
                uid, n = f"{base_id}_{n}", n + 1
            usados.add(uid)
            ids_por_indicativo[normalizar(up.indicativo)] = uid
            pos = situar(up.posicion, esquina, up.indicativo)
            # Separa un poco a los grupos que empiezan en el mismo sitio.
            clave = pos.cuadricula()
            if ocupados.get(clave):
                pos = pos.desplazar(rng.uniform(0, 360), 60 * ocupados[clave]).limitar(lado, lado)
            ocupados[clave] = ocupados.get(clave, 0) + 1
            lista.append(
                UnidadEscenario(
                    id=uid,
                    indicativo=up.indicativo,
                    tipo=up.tipo,
                    posicion=pos,
                    efectivos=up.efectivos,
                    armada=up.armada and up.tipo == TipoUnidad.HELICOPTERO,
                    capacidad=1 if up.tipo == TipoUnidad.HELICOPTERO and not up.armada else 0,
                    persona=up.lider,
                    orden=_orden(up, pos),
                    jugadores=up.jugadores,
                )
            )
        unidades_por_bando[bp.bando] = lista

    # Objetivos
    objetivos_por_bando: dict[str, list[Objetivo]] = {}
    for bp in bandos_plan:
        objetivos = []
        for j, op in enumerate(bp.objetivos):
            objetivos.append(_objetivo(op, j, bp.bando, situar(op.lugar, centro, op.nombre), ids_por_indicativo,
                                       duracion_min * 60, avisos))
        objetivos_por_bando[bp.bando] = objetivos
    if not objetivos_por_bando.get(jugadores):
        pos = next(iter(lugares.values()), centro)
        objetivos_por_bando[jugadores] = [
            Objetivo(id="obj_principal", nombre="Tomar la zona", tipo=TipoObjetivo.CAPTURAR, posicion=pos, radio_m=250)
        ]
        avisos.append("Los jugadores no tenían objetivo: les doy tomar la zona principal.")
    # Destino de los grupos que tienen que ir a algún sitio lejano (p. ej. un convoy).
    destinos = {
        u.id: pos
        for lista in unidades_por_bando.values() for u in lista
        if u.orden is not None and (pos := parsear_posicion(u.orden.destino, lugares, limites)) is not None
        and pos.distancia(u.posicion) > 600
    }
    for bp in bandos_plan:
        if bp.bando != jugadores and not objetivos_por_bando.get(bp.bando):
            objetivos_por_bando[bp.bando] = _espejo(objetivos_por_bando[jugadores], destinos)

    bandos: dict[str, BandoEscenario] = {}
    for bp in bandos_plan:
        unidades = unidades_por_bando[bp.bando]
        base = situar(bp.base, unidades[0].posicion, f"la base de {bp.bando}") if bp.base else None
        if base is None and bp.bando == jugadores and unidades:
            base = unidades[0].posicion
        bandos[bp.bando] = BandoEscenario(
            nombre=bp.nombre,
            mando=bp.mando,
            comandante=bp.comandante,
            base=base,
            unidades=unidades,
            objetivos=objetivos_por_bando.get(bp.bando, []),
        )

    # Decorado: lo que Zeus coloca o cambia en el terreno.
    bandos_validos = set(bandos)
    objetos: list[ObjetoMapa] = []
    for k, deco in enumerate(plan.decorado):
        objetos.append(_objeto(deco, k, situar(deco.lugar, centro, deco.tipo.value), bandos_validos, jugadores,
                               avisos))

    hora, clima = plan.hora, plan.clima
    condiciones = Condiciones(hora=hora, clima=clima,
                              visibilidad_m=VISIBILIDAD_HORA.get(hora, 1500.0) * FACTOR_CLIMA.get(clima, 1.0),
                              viento_ms=round(rng.uniform(1, 7), 1))
    zona = plan.zona or (referencias[0].nombre if referencias else "zona de operaciones")
    escenario = Escenario(
        id=id_mision or _slug(plan.nombre),
        nombre=plan.nombre,
        briefing=plan.briefing,
        terreno=terreno,
        zona=zona,
        mapa=Mapa(nombre=f"{terreno}: {zona} ({plan.tamano_km} km)", ancho_m=lado, alto_m=lado),
        duracion_max_s=duracion_min * 60.0,
        condiciones=condiciones,
        referencias=referencias,
        zonas_aterrizaje=lzs,
        objetos=objetos,
        bandos=bandos,
        bando_jugadores=jugadores,
    )
    sucesos = sorted((sp for sp in plan.sucesos if sp.minuto < duracion_min), key=lambda sp: sp.minuto)
    return MisionConstruida(escenario, avisos, sucesos)


def _bandos_validos(plan: PlanMision, avisos: list[str]) -> list[BandoPlan]:
    vistos: dict[str, BandoPlan] = {}
    for bp in plan.bandos:
        if bp.bando not in ("blufor", "opfor", "independent"):
            libre = next(b for b in ("blufor", "opfor", "independent") if b not in vistos)
            avisos.append(f"Bando '{bp.bando}' desconocido: lo convierto en {libre}.")
            bp = bp.model_copy(update={"bando": libre})
        if bp.bando in vistos:
            avisos.append(f"Bando {bp.bando} repetido: junto sus unidades.")
            previo = vistos[bp.bando]
            vistos[bp.bando] = previo.model_copy(update={"unidades": previo.unidades + bp.unidades,
                                                         "objetivos": previo.objetivos + bp.objetivos})
            continue
        vistos[bp.bando] = bp
    validos = [bp for bp in vistos.values() if bp.unidades]
    if len(validos) < 2:
        raise ValueError("La misión necesita al menos dos bandos con unidades.")
    return validos


def _orden(up: UnidadPlan, pos: Posicion) -> OrdenMando:
    tarea = up.tarea
    ruta = list(up.ruta)
    if tarea == TareaMando.PATRULLAR and not ruta:
        ruta = [pos.desplazar(a, 400).cuadricula() for a in (0, 120, 240)]
    if tarea == TareaMando.EMBOSCAR and up.tipo.es_aerea:
        tarea = TareaMando.PATRULLAR
    return OrdenMando(unidad=up.indicativo, tarea=tarea, destino=up.destino or pos.cuadricula(), ruta=ruta)


def _objetivo(op: ObjetivoPlan, indice: int, bando: str, pos: Posicion, ids: dict[str, str], duracion_s: float,
              avisos: list[str]) -> Objetivo:
    unidades = []
    for nombre in op.unidades:
        uid = ids.get(normalizar(nombre))
        if uid is None:
            avisos.append(f"El objetivo '{op.nombre}' menciona '{nombre}', que no existe.")
        else:
            unidades.append(uid)
    return Objetivo(
        id=f"obj_{bando}_{indice + 1}",
        nombre=op.nombre,
        tipo=op.tipo,
        posicion=pos,
        radio_m=250,
        prioridad=indice + 1,
        obligatorio=op.obligatorio,
        descripcion=op.descripcion,
        unidades=unidades,
        hasta_s=duracion_s * 0.8 if op.tipo == TipoObjetivo.DEFENDER else None,
    )


def _objeto(op: ObjetoPlan, indice: int, pos: Posicion, bandos: set[str], jugadores: str,
            avisos: list[str]) -> ObjetoMapa:
    bando = op.bando
    if bando is not None and bando not in bandos:
        avisos.append(f"El decorado '{op.tipo.value}' es de un bando que no existe ({bando}): lo dejo neutral.")
        bando = None
    if op.tipo.value in ("minas", "ied") and bando is None:
        # Una trampa siempre la pone alguien: si no lo dice, es del enemigo de los jugadores.
        bando = next((b for b in sorted(bandos) if b != jugadores), None)
    return ObjetoMapa(id=f"obj{indice + 1}_{op.tipo.value}", tipo=op.tipo, posicion=pos, radio_m=op.radio_m,
                      descripcion=op.descripcion, bando=bando)


def _espejo(objetivos: list[Objetivo], destinos: dict[str, Posicion]) -> list[Objetivo]:
    """Objetivos del enemigo: lo contrario de lo que buscan los jugadores."""
    espejo = []
    for o in objetivos:
        tipo = OBJETIVO_CONTRARIO[o.tipo]
        destino = next((destinos[u] for u in o.unidades if u in destinos), None)
        if o.tipo == TipoObjetivo.DESTRUIR and destino is not None:
            # Lo que los jugadores quieren destruir (un convoy) tiene que llegar a su destino.
            espejo.append(o.model_copy(update={"id": f"{o.id}_contra", "nombre": "Llegar al destino",
                                               "tipo": TipoObjetivo.EXTRAER, "posicion": destino}))
            continue
        espejo.append(o.model_copy(update={
            "id": f"{o.id}_contra",
            "nombre": f"Impedir que el enemigo logre: {o.nombre[:1].lower()}{o.nombre[1:]}",
            "tipo": tipo,
            # El enemigo que defiende gana si aguanta hasta el final (se agota el tiempo).
            "hasta_s": None,
            "unidades": o.unidades if tipo == TipoObjetivo.DESTRUIR else [],
        }))
    return espejo
