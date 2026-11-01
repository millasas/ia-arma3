"""Representación en texto del simulador: mapa ASCII y parte de situación."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..agentes import situacion as s
from ..modelo import EstadoObjetivo, EstadoUnidad, TipoObjeto

if TYPE_CHECKING:
    from .motor import SimuladorTexto

SIMBOLO_TERRENO = {"bosque": "^", "urbano": "#", "colina": "n", "setos": "%"}
SIMBOLO_OBJETO = {
    TipoObjeto.FORTIFICACION: "=", TipoObjeto.PUESTO_CONTROL: "=", TipoObjeto.TORRE_VIGILANCIA: "!",
    TipoObjeto.MINAS: "x", TipoObjeto.IED: "x", TipoObjeto.BARRICADA: ":", TipoObjeto.ALAMBRADA: ":",
    TipoObjeto.ESCOMBROS: ":", TipoObjeto.HUMO: '"',
}
LEYENDA = (
    "Leyenda: letra MAYÚSCULA = unidad propia, * = helicóptero propio, r = enemigo, "
    "? = enemigo sin identificar, O = objetivo pendiente, V = objetivo cumplido, X = fallido, "
    "+ = LZ, @ = base, ^ bosque, # pueblo, n colina, = fortificación, ! torre, x minas, : obstáculo, "
    '" humo, , otros restos'
)


def mapa_ascii(sim: SimuladorTexto, bando: str | None = None, ancho: int = 50, alto: int = 22) -> str:
    """Mapa del escenario. Con `bando` se ven solo sus contactos; sin él, todo (vista de árbitro)."""
    mapa = sim.escenario.mapa
    celdas = [["·"] * ancho for _ in range(alto)]

    def celda(x: float, y: float) -> tuple[int, int]:
        col = min(ancho - 1, max(0, int(x / mapa.ancho_m * ancho)))
        fila = min(alto - 1, max(0, alto - 1 - int(y / mapa.alto_m * alto)))
        return fila, col

    for r in sim.escenario.referencias:
        simbolo = SIMBOLO_TERRENO.get(r.tipo, "~")
        for fila in range(alto):
            for col in range(ancho):
                x = (col + 0.5) / ancho * mapa.ancho_m
                y = (alto - fila - 0.5) / alto * mapa.alto_m
                if (x - r.posicion.x) ** 2 + (y - r.posicion.y) ** 2 <= r.radio_m**2:
                    celdas[fila][col] = simbolo

    def poner(x: float, y: float, simbolo: str) -> None:
        fila, col = celda(x, y)
        celdas[fila][col] = simbolo

    principal = bando or sim.bando_principal
    objetos = sim.objetos_visibles(bando) if bando else [o for o in sim.objetos if o.activo]
    for obj in objetos:
        poner(obj.posicion.x, obj.posicion.y, SIMBOLO_OBJETO.get(obj.tipo, ","))
    for z in sim.escenario.zonas_aterrizaje:
        poner(z.posicion.x, z.posicion.y, "+")
    base = sim.base(principal)
    if base is not None:
        poner(base.x, base.y, "@")
    for o in sim.objetivos[principal]:
        poner(o.posicion.x, o.posicion.y,
              {EstadoObjetivo.PENDIENTE: "O", EstadoObjetivo.COMPLETADO: "V"}.get(o.estado, "X"))
    if bando is None:
        for u in sim.unidades.values():
            if u.operativa and u.bando != principal and u.estado != EstadoUnidad.EMBARCADA:
                poner(u.pos.x, u.pos.y, "r")
    else:
        for c in sim.contactos[bando].values():
            poner(c.pos.x, c.pos.y, "r" if c.identificado else "?")
    for u in sim.operativas(principal):
        if u.estado == EstadoUnidad.EMBARCADA:
            continue
        poner(u.pos.x, u.pos.y, "*" if u.es_aerea else u.indicativo[0].upper())

    borde = "+" + "-" * ancho + "+"
    filas = [borde] + ["|" + "".join(f) + "|" for f in celdas] + [borde]
    escala = f"Norte arriba. Mapa de {mapa.ancho_m / 1000:.1f} x {mapa.alto_m / 1000:.1f} km."
    return "\n".join(filas) + f"\n{LEYENDA}\n{escala}"


def parte_situacion(sim: SimuladorTexto, bando: str) -> str:
    obs = sim.observacion(bando, consumir_eventos=False)
    return s.unir(
        s.cabecera(obs),
        s.seccion("OBJETIVOS", s.describir_objetivos(obs), "ninguno"),
        s.seccion("UNIDADES", [s.describir_unidad(u, obs) for u in obs.unidades if u.operativa], "ninguna"),
        s.seccion("CONTACTOS", [s.describir_contacto(c) for c in obs.contactos], "ninguno"),
    )
