"""Bucle de una partida completa: mundo <-> cerebros, registro y reflexión final."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .habilidades import Habilidades
from .mando import CerebroBando, SalidaCerebro
from .memoria import Leccion, Memoria, Reflexion
from .memoria.reflexion import resumen_plantilla
from .modelo import ResultadoMision
from .puente import Mundo

log = logging.getLogger(__name__)

AlPaso = Callable[[int, dict[str, SalidaCerebro]], "Awaitable[Any] | Any"]


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
        campana: str = "?",
        escenario: str = "?",
        max_pasos: int | None = None,
        al_paso: AlPaso | None = None,
    ) -> None:
        self.mundo = mundo
        self.cerebros = cerebros
        self.memoria = memoria
        self.reflexion = reflexion
        self.habilidades = habilidades
        self.campana = campana
        self.escenario = escenario
        self.max_pasos = max_pasos
        self.al_paso = al_paso

    async def jugar(self) -> InformePartida:
        mision_id = None
        if self.memoria is not None:
            mision_id = self.memoria.iniciar_mision(self.campana, self.escenario, ",".join(self.cerebros))
        for cerebro in self.cerebros.values():
            cerebro.iniciar_mision(mision_id)

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
