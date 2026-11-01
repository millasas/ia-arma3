"""El cerebro de un bando: coordina a todos los agentes en cada paso de la partida.

Orden de cada paso:

1. Sincroniza agentes con las unidades (crea líderes y pilotos, da de baja a los caídos).
2. El enlace atiende el chat de los jugadores y reenvía peticiones por radio.
3. El comandante decide si hace falta (al inicio, cada cierto tiempo o ante eventos
   importantes) y transmite órdenes.
4. Líderes y pilotos que tengan motivo deciden en paralelo (el semáforo del cliente LLM
   limita cuántas consultas van a la GPU a la vez).
5. Las unidades que no deciden pueden reaccionar por reflejo.
6. Los aterrizajes reciben los parámetros aprendidos por la habilidad de vuelo.
7. El tráfico de radio se guarda y, si se quiere, se muestra a los jugadores.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from .agentes import (
    AgenteUnidad,
    Comandante,
    ContextoAgentes,
    Enlace,
    LiderEscuadra,
    OrdenMando,
    Piloto,
    ResultadoAgente,
    Saliente,
    reglas,
)
from .agentes.situacion import RADIO_LZ_CALIENTE, contactos_cerca
from .ambientacion import PerfilCampana
from .comunicacion import MensajeRadio, RedRadio, TipoMensaje
from .config import Config
from .habilidades import Habilidades
from .llm import ClienteLLM
from .memoria import Memoria
from .modelo import Accion, Evento, MensajeChat, Observacion, Persona, TipoAccion
from .texto import normalizar

log = logging.getLogger(__name__)

ALIAS_MANDO = ("mando", "comandante", "cuartel general", "hq", "jefe", "base", "actual")
ALIAS_ENLACE = ("enlace", "radio", "operador")


@dataclass
class SalidaCerebro:
    acciones: list[Accion] = field(default_factory=list)
    chat: list[MensajeChat] = field(default_factory=list)
    radio: list[MensajeRadio] = field(default_factory=list)
    eventos: list[Evento] = field(default_factory=list)
    decisiones: int = 0


class CerebroBando:
    def __init__(
        self,
        bando: str,
        llm: ClienteLLM,
        perfil: PerfilCampana,
        config: Config | None = None,
        memoria: Memoria | None = None,
        habilidades: Habilidades | None = None,
        *,
        con_mando: bool = True,
        personas: dict[str, Persona] | None = None,
        ordenes: dict[str, OrdenMando] | None = None,
        persona_mando: Persona | None = None,
    ) -> None:
        self.bando = bando
        self.config = config or Config()
        self.perfil = perfil
        self.memoria = memoria
        self.habilidades = habilidades
        # Sin mando, cada NPC actúa por su cuenta con la tarea que le dio Zeus.
        self.con_mando = con_mando
        self.personas = dict(personas or {})
        self.ordenes_iniciales = dict(ordenes or {})
        self.radio = RedRadio(retardo_s=perfil.retardo_radio_s)
        self.ctx = ContextoAgentes.crear(
            llm, perfil, bando, self.config.agentes, memoria, self.config.llm
        )
        self.comandante = Comandante(self.ctx, "mando", perfil.indicativo_mando, persona_mando)
        self.enlace = Enlace(self.ctx, "enlace", perfil.indicativo_enlace)
        if con_mando:
            self.radio.registrar("mando", perfil.indicativo_mando, ALIAS_MANDO)
        self.radio.registrar("enlace", perfil.indicativo_enlace, ALIAS_ENLACE)
        self.unidades: dict[str, AgenteUnidad] = {}
        self.caidos: dict[str, AgenteUnidad] = {}

    @property
    def agentes(self) -> list:
        mando = [self.comandante] if self.con_mando else []
        return [*mando, self.enlace, *self.unidades.values(), *self.caidos.values()]

    def iniciar_mision(self, mision_id: int | None) -> None:
        self.ctx.mision_id = mision_id

    def lecciones_usadas(self) -> set[int]:
        usadas: set[int] = set()
        for agente in self.agentes:
            usadas |= agente.lecciones_usadas
        return usadas

    def estadisticas(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for agente in self.agentes:
            for clave, valor in agente.estadisticas.items():
                total[clave] = total.get(clave, 0) + valor
        return total

    def _sincronizar(self, obs: Observacion) -> None:
        presentes = {u.id for u in obs.unidades}
        for u in obs.unidades:
            if u.id in self.unidades:
                if not u.operativa:
                    self.radio.dar_de_baja(u.id)
                    self.caidos[u.id] = self.unidades.pop(u.id)
                continue
            if not u.operativa or u.id in self.caidos:
                continue
            clase = Piloto if u.es_aerea else LiderEscuadra
            agente = clase(self.ctx, u.id, u.indicativo, self.personas.get(u.id),
                           "mando" if self.con_mando else None)
            orden = self.ordenes_iniciales.get(u.id)
            if orden is not None:
                agente.asignar_orden(orden)
            self.unidades[u.id] = agente
            self.radio.registrar(u.id, u.indicativo)
        for uid in [i for i in self.unidades if i not in presentes]:
            self.radio.dar_de_baja(uid)
            self.caidos[uid] = self.unidades.pop(uid)

    def destinatario(self, texto: str) -> AgenteUnidad | None:
        """El NPC al que se dirige un mensaje ("Baker, ¿cómo vais?", "Sargento Miller, ...")."""
        inicio = f" {normalizar(texto)[:60]} "
        mejor: tuple[int, AgenteUnidad] | None = None
        for agente in self.unidades.values():
            for nombre in agente.nombres():
                if f" {nombre} " in inicio and (mejor is None or len(nombre) > mejor[0]):
                    mejor = (len(nombre), agente)
        return mejor[1] if mejor else None

    def _transmitir(self, de: str, salientes: Iterable[Saliente], tiempo: float) -> None:
        for s in salientes:
            para = s.para
            if not self.con_mando and normalizar(para) in ALIAS_MANDO:
                para = "todos"
            entregados = self.radio.enviar(de, para, s.texto, s.tipo, tiempo, s.datos)
            if not entregados and s.tipo != TipoMensaje.GENERAL:
                log.debug("Mensaje de %s a '%s' sin destinatario: %s", de, s.para, s.texto)

    async def paso(self, obs: Observacion, chat: Iterable[MensajeChat] = ()) -> SalidaCerebro:
        t = obs.tiempo
        self._sincronizar(obs)
        salida = SalidaCerebro(eventos=list(obs.eventos))
        if self.memoria is not None and self.ctx.mision_id is not None:
            self.memoria.registrar_eventos(self.ctx.mision_id, obs.eventos)
        if self.habilidades is not None:
            for e in obs.eventos:
                if e.tipo == "aterrizaje":
                    self.habilidades.informar_aterrizaje(e.datos)

        # 1. Jugadores: si nombran a un NPC le contesta él; si no, el operador de radio.
        jugadores = [m for m in chat if not m.es_ia]
        al_enlace = []
        por_id = {u.id: u for u in obs.unidades}
        for mensaje in jugadores:
            agente = self.destinatario(mensaje.texto)
            unidad = por_id.get(agente.id) if agente is not None else None
            if agente is None or unidad is None:
                al_enlace.append(mensaje)
                continue
            respuesta, salientes = await agente.charlar(mensaje, obs, unidad)
            salida.chat.append(respuesta)
            salida.decisiones += 1
            self._transmitir(agente.id, salientes, t)
        if al_enlace:
            respuestas, salientes = await self.enlace.atender(al_enlace, obs)
            salida.chat.extend(respuestas)
            salida.decisiones += len(respuestas)
            self._transmitir("enlace", salientes, t)

        # 2. Mando
        self.comandante.recibir(self.radio.recibir("mando", t))
        self.radio.recibir("enlace", t)
        if self.con_mando and self.comandante.necesita_decidir(obs):
            decision = await self.comandante.decidir(obs)
            self._transmitir("mando", self.comandante.mensajes_de_ordenes(decision, obs), t)
            salida.decisiones += 1

        # 3. Unidades (en paralelo)
        por_id = {u.id: u for u in obs.unidades}
        pendientes = []
        for uid, agente in self.unidades.items():
            agente.recibir(self.radio.recibir(uid, t))
            unidad = por_id.get(uid)
            if unidad is not None and agente.necesita_decidir(obs, unidad):
                pendientes.append((uid, agente.decidir_y_actuar(obs, unidad)))
        resultados: list[ResultadoAgente] = list(await asyncio.gather(*(c for _, c in pendientes)))
        decididas = set()
        for (uid, _), resultado in zip(pendientes, resultados):
            decididas.add(uid)
            salida.acciones.extend(resultado.acciones)
            self._transmitir(uid, resultado.mensajes, t)
        salida.decisiones += len(resultados)

        # 4. Reflejos de las unidades que no han decidido
        for uid in self.unidades:
            unidad = por_id.get(uid)
            if uid not in decididas and unidad is not None:
                accion = reglas.reflejo(unidad, obs, self.personas.get(uid))
                if accion is not None:
                    salida.acciones.append(accion)

        # 5. Habilidad de vuelo aprendida
        if self.habilidades is not None:
            salida.acciones = [self._con_parametros(a, obs) for a in salida.acciones]

        # 6. Radio
        salida.radio = self.radio.tomar_nuevos()
        if self.memoria is not None and self.ctx.mision_id is not None:
            self.memoria.registrar_eventos(
                self.ctx.mision_id,
                [
                    Evento(tiempo=t, tipo="radio", unidad=m.de,
                           descripcion=f"{m.de_indicativo} → {m.para_indicativo}: {m.texto}",
                           datos={"tipo": m.tipo.value})
                    for m in salida.radio
                ],
            )
        if self.config.agentes.retransmitir_radio:
            salida.chat.extend(
                MensajeChat(autor=m.de_indicativo, texto=f"[a {m.para_indicativo}] {m.texto}",
                            canal="radio", tiempo=t, es_ia=True)
                for m in salida.radio
            )
        return salida

    def _con_parametros(self, accion: Accion, obs: Observacion) -> Accion:
        if accion.tipo not in (TipoAccion.ATERRIZAR, TipoAccion.REGRESAR_BASE) or accion.parametros:
            return accion
        assert self.habilidades is not None
        punto = accion.destino or obs.base
        caliente = bool(punto and contactos_cerca(obs, punto, RADIO_LZ_CALIENTE))
        return accion.model_copy(update={"parametros": self.habilidades.parametros_aterrizaje(caliente)})
