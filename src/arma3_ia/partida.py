"""Bucle de una partida completa: mundo <-> cerebros, registro y reflexión final."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .ambientacion import PerfilCampana
from .config import Config
from .habilidades import Habilidades
from .llm import ClienteLLM
from .mando import CerebroBando, SalidaCerebro
from .memoria import Leccion, Memoria, Reflexion
from .memoria.reflexion import resumen_plantilla
from .modelo import ResultadoMision
from .puente import Mundo

if TYPE_CHECKING:
    from .simulador.escenario import Escenario

log = logging.getLogger(__name__)

AlPaso = Callable[[int, dict[str, SalidaCerebro]], "Awaitable[Any] | Any"]


class Director(Protocol):
    """Zeus durante la partida (ver zeus.DirectorPartida): actúa por su cuenta, no por encargo."""

    async def iniciar(self) -> None: ...

    async def supervisar(self) -> list[str]: ...


def preparar_cerebros(
    escenario: Escenario,
    llm: ClienteLLM,
    perfil: PerfilCampana,
    config: Config | None = None,
    memoria: Memoria | None = None,
    habilidades: Habilidades | None = None,
) -> dict[str, CerebroBando]:
    """Un cerebro por bando con los NPC (personas y tareas) que puso Zeus.

    El bando de los jugadores usa la ambientación tal cual y aprende (memoria); el bando
    contrario la usa vista desde el enemigo.
    """
    nombres = list(escenario.bandos)
    principal = escenario.bando_jugadores if escenario.bando_jugadores in nombres else nombres[0]
    cerebros = {}
    for nombre, bando in escenario.bandos.items():
        es_principal = nombre == principal
        cerebros[nombre] = CerebroBando(
            nombre, llm, perfil if es_principal else perfil.del_enemigo(), config,
            memoria if es_principal else None, habilidades,
            con_mando=bando.mando,
            personas={u.id: u.persona for u in bando.unidades if u.persona is not None},
            ordenes={u.id: u.orden for u in bando.unidades if u.orden is not None},
            persona_mando=bando.comandante,
        )
    return cerebros


@dataclass
class InformePartida:
    resultado: ResultadoMision
    mision_id: int | None
    pasos: int
    decisiones: int
    resumen: str
    lecciones_nuevas: list[Leccion] = field(default_factory=list)
    lecciones_reforzadas: list[Leccion] = field(default_factory=list)
    lecciones_archivadas: list[int] = field(default_factory=list)
    estadisticas: dict[str, int] = field(default_factory=dict)


class Partida:
    def __init__(
        self,
        mundo: Mundo,
        cerebros: dict[str, CerebroBando],
        *,
        memoria: Memoria | None = None,
        reflexion: Reflexion | None = None,
        habilidades: Habilidades | None = None,
        escenario: Escenario | None = None,
        campana_id: str | None = None,
        capitulo: int | None = None,
        titulo: str = "",
        max_pasos: int | None = None,
        al_paso: AlPaso | None = None,
        director: Director | None = None,
    ) -> None:
        self.mundo = mundo
        self.cerebros = cerebros
        self.memoria = memoria
        self.reflexion = reflexion
        self.habilidades = habilidades
        self.escenario = escenario
        self.campana_id = campana_id
        self.capitulo = capitulo
        self.titulo = titulo
        self.max_pasos = max_pasos
        self.al_paso = al_paso
        self.director = director

    async def jugar(self) -> InformePartida:
        mision_id = None
        if self.memoria is not None:
            e = self.escenario
            epocas = {c.perfil.id for c in self.cerebros.values()}
            mision_id = self.memoria.iniciar_mision(
                e.id if e else "?", campana_id=self.campana_id, capitulo=self.capitulo,
                titulo=self.titulo or (e.nombre if e else ""), terreno=e.terreno if e else "",
                epoca=epocas.pop() if len(epocas) == 1 else None,
                bando=(e.bando_jugadores if e else None) or ",".join(self.cerebros),
            )
        for cerebro in self.cerebros.values():
            cerebro.iniciar_mision(mision_id)
        if self.director is not None:
            await self.director.iniciar()

        pasos = decisiones = 0
        while not self.mundo.terminado():
            if self.max_pasos is not None and pasos >= self.max_pasos:
                break
            salidas: dict[str, SalidaCerebro] = {}
            for bando, cerebro in self.cerebros.items():
                obs = await self.mundo.observar(bando)
                chat = await self.mundo.recibir_chat(bando)
                salida = await cerebro.paso(obs, chat)
                resultados = await self.mundo.ejecutar(bando, salida.acciones)
                for r in resultados:
                    if not r.aceptada:
                        log.info("Acción rechazada (%s %s): %s", r.accion.unidad, r.accion.tipo.value, r.motivo)
                if salida.chat:
                    await self.mundo.enviar_chat(bando, salida.chat)
                if self.memoria is not None:
                    self.memoria.registrar_chat(mision_id, [*chat, *(m for m in salida.chat if m.canal != "radio")])
                decisiones += salida.decisiones
                salidas[bando] = salida
            await self.mundo.avanzar()
            pasos += 1
            if self.director is not None and not self.mundo.terminado():
                await self.director.supervisar()
            if self.al_paso is not None:
                retorno = self.al_paso(pasos, salidas)
                if inspect.isawaitable(retorno):
                    await retorno

        resultado = self.mundo.resultado() or self.mundo.finalizar("partida detenida")
        informe = InformePartida(
            resultado=resultado,
            mision_id=mision_id,
            pasos=pasos,
            decisiones=decisiones,
            resumen=resumen_plantilla(resultado),
        )
        for cerebro in self.cerebros.values():
            for clave, valor in cerebro.estadisticas().items():
                informe.estadisticas[clave] = informe.estadisticas.get(clave, 0) + valor

        if self.memoria is not None and mision_id is not None:
            self.memoria.cerrar_mision(mision_id, resultado, informe.resumen)
            if self.reflexion is not None:
                usadas: set[int] = set()
                for cerebro in self.cerebros.values():
                    usadas |= cerebro.lecciones_usadas()
                reflexion = await self.reflexion.analizar(mision_id, resultado, usadas)
                informe.resumen = reflexion.resumen
                informe.lecciones_nuevas = reflexion.nuevas
                informe.lecciones_reforzadas = reflexion.reforzadas
                informe.lecciones_archivadas = reflexion.archivadas
        if self.habilidades is not None:
            self.habilidades.guardar()
        return informe
