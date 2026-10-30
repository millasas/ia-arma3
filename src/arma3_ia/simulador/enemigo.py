"""Comportamientos por guion para los bandos que no controla la IA (el "enemigo" del simulador).

- defender: mantiene su posición inicial.
- patrulla: recorre su ruta y ataca lo que detecta a menos de 600 m.
- emboscada: espera oculta y abre fuego por sorpresa cuando el enemigo entra en su radio.
- qrf: fuerza de reacción rápida; acude a donde salte la alarma.
- asalto: avanza hacia el último punto de su ruta atacando lo que encuentre.

Todas rompen el contacto y se retiran si pierden demasiados hombres o la moral.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..geo import Posicion
from ..modelo import Postura, TipoAccion

if TYPE_CHECKING:
    from .motor import SimuladorTexto, UnidadSim


def _alejarse(sim: SimuladorTexto, u: UnidadSim, amenaza: Posicion) -> Posicion:
    destino = u.pos.desplazar(amenaza.rumbo(u.pos), 600)
    return destino.limitar(sim.escenario.mapa.ancho_m, sim.escenario.mapa.alto_m)


def _ordenar(u: UnidadSim, accion: TipoAccion, destino: Posicion | None, postura: Postura) -> None:
    u.accion, u.destino, u.postura = accion, destino, postura


def planificar(sim: SimuladorTexto, bando: str) -> None:
    conocidos = sim.contactos[bando]
    for u in sim.operativas(bando):
        if u.es_aerea or not u.en_suelo:
            continue
        visibles = [
            (sim.unidades[i], c)
            for i, c in conocidos.items()
            if sim.tiempo - c.ultimo_t <= sim.escenario.paso_s and sim.unidades[i].en_suelo
        ]
        cercano = min(visibles, key=lambda par: par[1].pos.distancia(u.pos), default=None)
        distancia = cercano[1].pos.distancia(u.pos) if cercano else float("inf")
        if u.bajo_fuego_previo or (cercano and distancia < 600):
            sim.alarma[bando] = u.pos

        proporcion = u.efectivos / u.efectivos_max
        if u.moral < 0.25 or proporcion < 0.35:
            if u.accion != TipoAccion.RETIRARSE:
                amenaza = cercano[1].pos if cercano else u.pos
                destino = _alejarse(sim, u, amenaza) if cercano else (u.posicion_inicial or u.pos)
                _ordenar(u, TipoAccion.RETIRARSE, destino, Postura.COMBATE)
            continue

        if u.comportamiento == "emboscada":
            if not u.activada:
                _ordenar(u, TipoAccion.DEFENDER, u.pos, Postura.SIGILO)
                if cercano and distancia <= u.radio_activacion:
                    u.activada = True
                    u.postura = Postura.COMBATE
                    sim.alarma[bando] = u.pos
                continue
            if u.ticks_combate > 6 or proporcion < 0.6:
                if u.accion != TipoAccion.RETIRARSE:
                    amenaza = cercano[1].pos if cercano else u.pos
                    _ordenar(u, TipoAccion.RETIRARSE, _alejarse(sim, u, amenaza), Postura.COMBATE)
            else:
                _ordenar(u, TipoAccion.DEFENDER, u.pos, Postura.COMBATE)
        elif u.comportamiento == "patrulla":
            if cercano and distancia < 600:
                _ordenar(u, TipoAccion.ATACAR, cercano[1].pos, Postura.COMBATE)
            elif u.ruta and (u.accion is None or u.destino is None or u.pos.distancia(u.destino) < 20):
                u.indice_ruta = (u.indice_ruta + 1) % len(u.ruta)
                _ordenar(u, TipoAccion.MOVER, u.ruta[u.indice_ruta], Postura.NORMAL)
        elif u.comportamiento == "qrf":
            alarma = sim.alarma.get(bando)
            if cercano and distancia < 800:
                _ordenar(u, TipoAccion.ATACAR, cercano[1].pos, Postura.COMBATE)
            elif alarma is not None and not u.activada:
                u.activada = True
                _ordenar(u, TipoAccion.ATACAR, alarma, Postura.COMBATE)
            elif u.activada and (u.accion is None or u.destino is None):
                _ordenar(u, TipoAccion.DEFENDER, u.pos, Postura.COMBATE)
        elif u.comportamiento == "asalto":
            if cercano and distancia < 500:
                _ordenar(u, TipoAccion.ATACAR, cercano[1].pos, Postura.COMBATE)
            elif u.ruta:
                _ordenar(u, TipoAccion.ATACAR, u.ruta[-1], Postura.NORMAL)
        else:
            inicio = u.posicion_inicial or u.pos
            _ordenar(u, TipoAccion.DEFENDER, inicio, Postura.NORMAL)
