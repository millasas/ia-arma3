"""Base común de todos los agentes: consulta al modelo con lecciones, validación y plan B."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel

from ..ambientacion import PerfilCampana
from ..comunicacion import MensajeRadio, TipoMensaje
from ..config import ConfigAgentes, ConfigLLM
from ..llm import ClienteLLM, ErrorLLM, MensajeLLM, generar_estructurado
from ..memoria import Leccion, Memoria, Recuperador
from ..modelo import Accion
from . import prompts

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class ContextoAgentes:
    """Dependencias compartidas por todos los agentes de un bando."""

    llm: ClienteLLM
    perfil: PerfilCampana
    config: ConfigAgentes
    bando: str
    memoria: Memoria | None = None
    recuperador: Recuperador | None = None
    mision_id: int | None = None
    reintentos_llm: int = 1

    @classmethod
    def crear(
        cls,
        llm: ClienteLLM,
        perfil: PerfilCampana,
        bando: str,
        config: ConfigAgentes | None = None,
        memoria: Memoria | None = None,
        config_llm: ConfigLLM | None = None,
    ) -> ContextoAgentes:
        return cls(
            llm=llm,
            perfil=perfil,
            config=config or ConfigAgentes(),
            bando=bando,
            memoria=memoria,
            recuperador=Recuperador(memoria) if memoria is not None else None,
            reintentos_llm=config_llm.reintentos if config_llm else 1,
        )


@dataclass
class Saliente:
    """Mensaje de radio que un agente quiere transmitir."""

    para: str
    texto: str
    tipo: TipoMensaje = TipoMensaje.GENERAL
    datos: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultadoAgente:
    acciones: list[Accion] = field(default_factory=list)
    mensajes: list[Saliente] = field(default_factory=list)


class Agente:
    rol: ClassVar[str] = "agente"

    def __init__(self, ctx: ContextoAgentes, agente_id: str, indicativo: str) -> None:
        self.ctx = ctx
        self.id = agente_id
        self.indicativo = indicativo
        self.ultima_decision_t: float | None = None
        self.bandeja: list[MensajeRadio] = []
        self.recientes: deque[MensajeRadio] = deque(maxlen=6)
        self.historial: deque[str] = deque(maxlen=4)
        self.avisos: list[str] = []
        self.lecciones_usadas: set[int] = set()
        self.estadisticas: Counter[str] = Counter()

    @property
    def usar_llm(self) -> bool:
        return True

    def recibir(self, mensajes: list[MensajeRadio]) -> None:
        self.bandeja.extend(mensajes)

    def tomar_bandeja(self) -> list[MensajeRadio]:
        mensajes, self.bandeja = self.bandeja, []
        self.recientes.extend(mensajes)
        return mensajes

    def _lecciones(self, consulta: str, rol: str | None = None) -> list[Leccion]:
        if self.ctx.recuperador is None:
            return []
        return self.ctx.recuperador.buscar(
            rol or self.rol, self.ctx.perfil.id, consulta, k=self.ctx.config.lecciones_por_decision
        )

    async def consultar(
        self,
        *,
        tiempo: float,
        sistema: str,
        usuario: str,
        esquema: type[T],
        fallback: Callable[[], T],
        consulta_lecciones: str,
        rol: str | None = None,
    ) -> T:
        """Pide una decisión al modelo; si falla, usa la doctrina por reglas (`fallback`).

        `rol` cambia el papel de esta consulta (p. ej. "npc" cuando un jefe de grupo charla).
        """
        rol = rol or self.rol
        lecciones = self._lecciones(consulta_lecciones, rol)
        sistema_completo = sistema + prompts.bloque_lecciones(lecciones)
        ids = {leccion.id for leccion in lecciones if leccion.id is not None}
        self.lecciones_usadas |= ids
        inicio = time.monotonic()
        origen = "llm"
        if not self.usar_llm:
            decision, origen = fallback(), "reglas"
        else:
            try:
                decision, _ = await asyncio.wait_for(
                    generar_estructurado(
                        self.ctx.llm,
                        [MensajeLLM("system", sistema_completo), MensajeLLM("user", usuario)],
                        esquema,
                        reintentos=self.ctx.reintentos_llm,
                        metadatos={"rol": rol, "agente": self.id, "fallback": fallback},
                    ),
                    timeout=self.ctx.config.timeout_decision_s,
                )
            except (ErrorLLM, asyncio.TimeoutError) as error:
                log.warning("%s: el modelo falló (%s); decido por reglas.", self.indicativo, error)
                decision, origen = fallback(), "reglas"
                self.estadisticas["errores_llm"] += 1
        self.estadisticas[origen] += 1
        self.ultima_decision_t = tiempo
        if self.ctx.memoria is not None:
            self.ctx.memoria.registrar_decision(
                mision_id=self.ctx.mision_id,
                tiempo=tiempo,
                agente=self.indicativo,
                rol=rol,
                sistema=sistema_completo,
                usuario=usuario,
                respuesta=decision.model_dump_json(),
                origen=origen,
                lecciones=ids,
                duracion_s=time.monotonic() - inicio,
            )
        return decision
