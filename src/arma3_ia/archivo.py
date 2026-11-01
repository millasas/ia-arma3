"""La carpeta CAMPAÑAS: una subcarpeta por campaña con su ficha, sus misiones y sus informes.

::

    CAMPAÑAS/
      <id de la campaña>/
        campana.txt              ficha: encargo, ambientación, objetivo, pasos, personajes, estado
        informe_final.txt        resumen de la campaña cuando termina
        misiones/
          01_<titulo>/
            mision.yaml          lo que preparó Zeus: terreno, decorado, grupos, objetivos, sorpresas
            informe.txt          informe corto de la misión

Son ficheros de texto pequeños para leerlos con cualquier editor. La base de datos (BBDD/)
guarda lo mismo y mucho más detalle.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .memoria import Leccion
from .modelo import ResultadoMision
from .simulador.escenario import Escenario, guardar_escenario
from .texto import normalizar, reloj
from .zeus import CierreMision, EstadoCampana, SucesoPlan

EVENTOS_CLAVE = ("objetivo_completado", "objetivo_fallido", "unidad_destruida", "refuerzos", "explosion",
                 "inmovilizado", "condiciones", "decorado", "aterrizaje", "embarque", "desembarco")
MAX_SUCESOS_INFORME = 10
ESTADO = {"victoria": "VICTORIA", "parcial": "RESULTADO PARCIAL", "derrota": "DERROTA"}


def _slug(texto: str, largo: int = 40) -> str:
    return normalizar(texto).replace(" ", "_")[:largo] or "mision"


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sucesos_clave(registro: list[Any], bando_jugadores: str | None = None) -> list[str]:
    """Los momentos importantes de la misión (para el informe y la crónica), sin repetir."""
    vistos: set[str] = set()
    lineas = []
    for e in registro:
        if e.tipo not in EVENTOS_CLAVE or e.descripcion in vistos:
            continue
        if e.tipo == "aterrizaje" and "ESTRELLA" not in e.descripcion:
            continue
        vistos.add(e.descripcion)
        lineas.append(f"{reloj(e.tiempo)} {e.descripcion}")
    return lineas


class ArchivoCampanas:
    def __init__(self, carpeta: str | Path, al_escribir: Any = None) -> None:
        self.carpeta = Path(carpeta)
        self.al_escribir = al_escribir

    def _escrito(self) -> None:
        if self.al_escribir is not None:
            self.al_escribir()

    def de(self, estado: EstadoCampana | str) -> Path:
        return self.carpeta / (estado if isinstance(estado, str) else estado.id)

    def carpeta_mision(self, estado: EstadoCampana, numero: int, titulo: str) -> Path:
        return self.de(estado) / "misiones" / f"{numero:02d}_{_slug(titulo)}"

    def _escribir(self, ruta: Path, texto: str) -> Path:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto.rstrip() + "\n", encoding="utf-8")
        self._escrito()
        return ruta

    # ------------------------------------------------------------------ ficha
    def ficha(self, estado: EstadoCampana) -> Path:
        p, plan = estado.perfil, estado.plan
        lineas = [
            f"CAMPAÑA: {plan.nombre}",
            f"Id: {estado.id} | creada {estado.creada.replace('T', ' ')[:16]} | estado: {estado.estado_texto}",
            "",
            f"ENCARGO: {estado.encargo}",
            f"{estado.misiones_total} misiones de {estado.duracion_mision_min} minutos.",
            "",
            f"AMBIENTACIÓN: {p.epoca}. {p.descripcion}",
            f"Terreno principal: {estado.terreno}",
            f"Jugadores: {p.bando_ia.nombre}. Enemigo: {p.enemigo.nombre}.",
            "",
            f"OBJETIVO FINAL: {plan.objetivo_final}",
            plan.premisa,
            "",
            "PASOS:",
        ]
        hechos = {r.capitulo: r for r in estado.historial}
        for i, c in enumerate(plan.capitulos):
            registro = hechos.get(i)
            marca = f"[{registro.estado}]" if registro else ("[siguiente]" if i == estado.capitulo and
                                                              not estado.terminada else "[pendiente]")
            lineas.append(f" {i + 1:2d}. {marca} {c.titulo}{' (CRÍTICO)' if c.critico else ''}")
            lineas.append(f"     {c.sinopsis}")
            if c.complicacion:
                lineas.append(f"     Problema: {c.complicacion}")
        lineas += ["", "PERSONAJES:"]
        lineas += [f" - {'(caído) ' if not pj.vivo else ''}{pj.persona.presentacion().split(' Recuerdas:')[0]}"
                   for pj in estado.personajes]
        if estado.antagonista:
            lineas.append(f" - Antagonista: {estado.antagonista.presentacion().split(' Recuerdas:')[0]}")
        lineas += ["", f"Margen de error: {estado.margen} de {estado.margen_inicial} | "
                       f"dificultad {estado.dificultad:.2f}"]
        if estado.terminada:
            lineas.append(f"FINAL: {estado.estado_texto}. {estado.motivo_final}")
        return self._escribir(self.de(estado) / "campana.txt", "\n".join(lineas))

    # ------------------------------------------------------------------ misiones
    def guardar_mision(self, estado: EstadoCampana, escenario: Escenario, sucesos: list[SucesoPlan]) -> Path:
        capitulo = estado.capitulo_actual
        titulo = capitulo.titulo if capitulo else escenario.nombre
        carpeta = self.carpeta_mision(estado, estado.capitulo + 1, titulo)
        extra = {"sucesos_preparados": [s.model_dump(mode="json", exclude_defaults=True) for s in sucesos]}
        ruta = guardar_escenario(escenario, carpeta / "mision.yaml", extra if sucesos else None)
        self._escrito()
        return ruta

    def informe_mision(
        self,
        estado: EstadoCampana,
        escenario: Escenario,
        resultado: ResultadoMision,
        cierre: CierreMision,
        *,
        sucesos: list[str],
        lecciones: list[Leccion] | None = None,
    ) -> Path:
        capitulo = estado.plan.capitulos[cierre.numero - 1]
        c = escenario.condiciones
        lineas = [
            f"INFORME DE MISIÓN {cierre.numero}/{estado.misiones_total}: {cierre.titulo}",
            f"Campaña: {estado.plan.nombre} | {_ahora()}",
            f"Terreno: {escenario.terreno}, {escenario.zona} | {c.hora}, {c.clima} | "
            f"duración {reloj(resultado.duracion_s)} de {reloj(escenario.duracion_max_s)}",
            f"Objetivo de la misión: {capitulo.objetivo or capitulo.sinopsis}",
            "",
            f"RESULTADO: {ESTADO[resultado.estado]} ({resultado.motivo}) | puntuación {resultado.puntuacion:.2f}",
            f"Objetivos {resultado.objetivos_completados}/{resultado.objetivos_totales} | bajas propias "
            f"{resultado.bajas_propias} de {resultado.efectivos_iniciales} | bajas enemigas {resultado.bajas_enemigas}",
            "",
            "QUÉ PASÓ:",
        ]
        lineas += [f" {s}" for s in sucesos[:MAX_SUCESOS_INFORME]] or [" (nada destacable)"]
        lineas += ["", "CRÓNICA:", cierre.cronica, "", "CONSECUENCIAS:"]
        lineas += [f" · {cambio}" for cambio in cierre.cambios] or [" · La historia sigue."]
        if lecciones:
            lineas += ["", "LO QUE HA APRENDIDO LA IA:"]
            lineas += [f" + ({leccion.rol}) {leccion.texto()}" for leccion in lecciones[:3]]
        carpeta = self.carpeta_mision(estado, cierre.numero, cierre.titulo)
        return self._escribir(carpeta / "informe.txt", "\n".join(lineas))

    # ------------------------------------------------------------------ final
    def informe_final(self, estado: EstadoCampana, lore: list[dict[str, Any]]) -> Path:
        h = estado.historial
        victorias = sum(r.estado == "victoria" for r in h)
        derrotas = sum(r.estado == "derrota" for r in h)
        caidos = [pj.persona for pj in estado.personajes if not pj.vivo]
        vivos = [pj.persona for pj in estado.personajes if pj.vivo]
        lineas = [
            f"INFORME FINAL DE CAMPAÑA: {estado.plan.nombre}",
            f"{_ahora()} | {estado.perfil.epoca} | terreno {estado.terreno}",
            "",
            f"ENCARGO: {estado.encargo}",
            f"OBJETIVO: {estado.plan.objetivo_final}",
            f"RESULTADO: {estado.estado_texto}. {estado.motivo_final}",
            "",
            f"Misiones jugadas: {len(h)} de {estado.misiones_total} ({victorias} victorias, {derrotas} derrotas, "
            f"{len(h) - victorias - derrotas} a medias) | tiempo total {reloj(sum(r.duracion_s for r in h))}",
            f"Bajas propias: {sum(r.bajas_propias for r in h)} | bajas enemigas: {sum(r.bajas_enemigas for r in h)}",
            "",
            "LA HISTORIA:",
        ]
        lineas += [f" - {e['titulo']}: {e['texto']}" for e in lore if e["tipo"] in ("inicio", "mision", "imprevisto",
                                                                                   "final")]
        lineas += ["", "PERSONAJES:"]
        lineas += [f" - Siguen vivos: {', '.join(p.nombre for p in vivos) or 'nadie'}",
                   f" - Caídos: {', '.join(p.nombre for p in caidos) or 'ninguno'}"]
        return self._escribir(self.de(estado) / "informe_final.txt", "\n".join(lineas))

    def borrar(self, id_campana: str) -> bool:
        carpeta = self.de(id_campana)
        if not carpeta.is_dir():
            return False
        shutil.rmtree(carpeta)
        self._escrito()
        return True
