"""Zeus: la IA como director de juego.

Con un encargo ("crea una campaña de insurgencia sobre el control del agua...") inventa la
campaña, prepara cada misión, la dirige en directo (refuerzos, hora, clima, mensajes, órdenes)
y, al terminar, deja que la historia y la dificultad cambien según lo que pasó.

Zeus usa la misma red (el modelo de Ollama) que los NPC, con otras instrucciones. Si el modelo
falla, sigue funcionando con las reglas de ``zeus/reglas.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from ..agentes.esquemas import OrdenMando, TareaMando
from ..campanas import PerfilCampana, cargar_perfil, perfiles_disponibles
from ..geo import parsear_posicion
from ..llm import ClienteLLM, ErrorLLM, MensajeLLM, generar_estructurado
from ..memoria import Leccion, Memoria, Recuperador
from ..modelo import GrupoNuevo, MensajeChat, ResultadoMision, VistaGlobal
from ..puente.interfaz import ControlZeus
from ..simulador.escenario import Escenario
from ..texto import normalizar, recortar, reloj
from . import reglas
from .campana import EstadoCampana
from .construir import MisionConstruida, construir_escenario
from .esquemas import IntervencionZeus, PlanCampana, PlanMision, TipoIntervencion, UnidadPlan

if TYPE_CHECKING:
    from ..mando import CerebroBando

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
PREFIJOS_ZEUS = ("zeus", "director", "dios")

EJEMPLO_CAMPANA = (
    '{"nombre": "Operación Agua Negra", "premisa": "...", "perfil": "insurgencia", '
    '"nombre_jugadores": "Milicia del Valle", "nombre_enemigo": "Fuerzas de ocupación", '
    '"capitulos": [{"titulo": "Ojos en el pozo", "sinopsis": "...", "tipo": "reconocimiento"}], '
    '"personajes": [{"nombre": "Leila Haddad", "rango": "", "rasgos": ["valiente", "impulsivo"], '
    '"historia": "..."}], "antagonista": {"nombre": "...", "rango": "Coronel", "rasgos": ["agresivo"], '
    '"historia": "..."}}'
)
EJEMPLO_MISION = (
    '{"nombre": "...", "briefing": "...", "hora": "atardecer", "clima": "despejado", "duracion_min": 60, '
    '"tamano_km": 4, "lugares": [{"nombre": "Pozo de Nur", "tipo": "urbano", "posicion": "026027"}], '
    '"bandos": [{"bando": "blufor", "nombre": "...", "mando": true, "base": "007007", "unidades": '
    '[{"indicativo": "Jugadores", "tipo": "infanteria", "efectivos": 6, "posicion": "007007", '
    '"tarea": "atacar", "destino": "Pozo de Nur", "jugadores": true}, {"indicativo": "Lobo", "tipo": '
    '"infanteria", "efectivos": 7, "posicion": "008006", "tarea": "reconocer", "destino": "Pozo de Nur", '
    '"lider": {"nombre": "Leila Haddad", "rango": "", "rasgos": ["valiente"], "historia": "..."}}], '
    '"objetivos": [{"nombre": "Tomar el pozo", "tipo": "capturar", "lugar": "Pozo de Nur"}]}, '
    '{"bando": "opfor", "nombre": "...", "mando": true, "unidades": [{"indicativo": "Sierra-1", "tipo": '
    '"infanteria", "efectivos": 6, "posicion": "Pozo de Nur", "tarea": "defender", "destino": "Pozo de Nur", '
    '"lider": {"nombre": "...", "rango": "Sargento", "rasgos": ["veterano"], "historia": "..."}}], '
    '"objetivos": []}], "bando_jugadores": "blufor"}'
)
EJEMPLO_INTERVENCION = (
    '{"razon": "Los jugadores avanzan sin oposición.", "acciones": [{"tipo": "refuerzos", "bando": "opfor", '
    '"texto": "", "unidad": {"indicativo": "Refuerzo", "tipo": "motorizada", "efectivos": 4, "posicion": '
    '"031030", "tarea": "atacar", "destino": "026027"}}, {"tipo": "mensaje", "texto": "Se oyen motores al '
    'norte..."}]}'
)


class Zeus:
    def __init__(
        self,
        llm: ClienteLLM,
        memoria: Memoria | None = None,
        *,
        timeout_s: float = 300.0,
        reintentos: int = 1,
        semilla: int | None = None,
    ) -> None:
        self.llm = llm
        self.memoria = memoria
        self.timeout_s = timeout_s
        self.reintentos = reintentos
        self.semilla = semilla
        self.perfiles = perfiles_disponibles()
        self.estadisticas: dict[str, int] = {"llm": 0, "reglas": 0}

    # ------------------------------------------------------------------ utilidades
    async def _pedir(self, sistema: str, usuario: str, esquema: type[T], fallback: Callable[[], T]) -> T:
        try:
            resultado, _ = await asyncio.wait_for(
                generar_estructurado(
                    self.llm, [MensajeLLM("system", sistema), MensajeLLM("user", usuario)], esquema,
                    reintentos=self.reintentos, temperatura=0.8,
                    metadatos={"rol": "zeus", "agente": "zeus", "fallback": fallback},
                ),
                timeout=self.timeout_s,
            )
            self.estadisticas["llm"] += 1
            return resultado
        except (ErrorLLM, asyncio.TimeoutError) as error:
            log.warning("Zeus sin modelo (%s): uso reglas.", error)
            self.estadisticas["reglas"] += 1
            return fallback()

    def _lecciones(self, perfil: str, consulta: str) -> str:
        if self.memoria is None:
            return ""
        lecciones = Recuperador(self.memoria).buscar("zeus", perfil, consulta, k=4)
        if not lecciones:
            return ""
        return "\nLO QUE HAS APRENDIDO DIRIGIENDO OTRAS PARTIDAS:\n" + "\n".join(
            f"- {leccion.texto()}" for leccion in lecciones)

    def perfil(self, estado: EstadoCampana) -> PerfilCampana:
        return self.perfiles.get(estado.plan.perfil) or cargar_perfil("moderno")

    # ------------------------------------------------------------------ campaña
    async def crear_campana(self, encargo: str) -> EstadoCampana:
        catalogo = {pid: (p.bando_ia.nombre, p.enemigo.nombre) for pid, p in self.perfiles.items()}
        lista = "; ".join(f"{pid} = {p.nombre} ({p.epoca})" for pid, p in self.perfiles.items())
        sistema = (
            "Eres Zeus, el director de juego de una comunidad de ARMA 3. A partir de un encargo creas una "
            "campaña militar con historia: premisa, capítulos (cada uno será una misión) y personajes aliados "
            "con nombre, carácter e historia, más un antagonista. Piensa en partidas divertidas, tensas y "
            "creíbles para la época.\n"
            f"Perfiles de época disponibles: {lista}.\n"
            "Tipos de capítulo: asalto, defensa, emboscada, rescate, reconocimiento, destruccion.\n"
            "Rasgos posibles: veterano, novato, valiente, prudente, agresivo, disciplinado, nervioso, impulsivo.\n"
            "Entre 3 y 6 capítulos; 2-4 personajes. Escribe en español."
            f"{self._lecciones('', encargo)}\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_CAMPANA}"
        )
        plan = await self._pedir(sistema, f"ENCARGO: {encargo}", PlanCampana,
                                 lambda: reglas.campana_por_reglas(encargo, catalogo, self.semilla))
        if plan.perfil not in self.perfiles:
            plan.perfil = reglas.detectar_perfil(encargo, list(self.perfiles))
        if len(plan.personajes) < 2:
            rng = reglas.rng_de(encargo, self.semilla)
            usados = {p.nombre for p in plan.personajes}
            plan.personajes += [reglas.crear_persona(rng, plan.perfil, "jugadores", usados)
                                for _ in range(2 - len(plan.personajes))]
        estado = EstadoCampana.nueva(plan)
        estado.id = self._id_libre(estado.id)
        self.guardar(estado)
        return estado

    def _id_libre(self, base: str) -> str:
        if self.memoria is None:
            return base
        ids = {c["id"] for c in self.memoria.campanas()}
        candidato, n = base, 2
        while candidato in ids:
            candidato, n = f"{base}_{n}", n + 1
        return candidato

    def guardar(self, estado: EstadoCampana) -> None:
        if self.memoria is not None:
            self.memoria.guardar_campana(estado.id, estado.plan.nombre, estado.model_dump(mode="json"))

    def cargar(self, id_campana: str) -> EstadoCampana:
        datos = self.memoria.cargar_campana(id_campana) if self.memoria else None
        if datos is None:
            raise KeyError(f"No existe la campaña '{id_campana}'.")
        return EstadoCampana.model_validate(datos)

    # ------------------------------------------------------------------ misión
    async def preparar_mision(self, estado: EstadoCampana) -> MisionConstruida:
        capitulo = estado.capitulo_actual
        if capitulo is None:
            raise ValueError("La campaña ya ha terminado.")
        perfil = self.perfil(estado)
        enemigo = perfil.del_enemigo()
        propios = perfil.indicativos.get("infanteria", [])
        hostiles = enemigo.indicativos.get("infanteria", []) + enemigo.indicativos.get("motorizada", [])

        def fallback() -> PlanMision:
            return reglas.mision_por_reglas(
                estado.plan, capitulo, indicativos_propios=propios, indicativos_enemigos=hostiles,
                personajes=estado.vivos(), antagonista=estado.antagonista, medios_aereos=perfil.medios_aereos,
                dificultad=estado.dificultad, semilla=self.semilla,
            )

        sistema = (
            "Eres Zeus, el director de juego. Preparas la siguiente misión de una campaña de ARMA 3: el mapa "
            "(lugares con nombre), los bandos, sus grupos de NPC con su jefe (nombre, carácter, historia) y su "
            "tarea inicial, y los objetivos. Los NPC actuarán por su cuenta según su tarea y su carácter.\n"
            f"{perfil.bloque_contexto('comandante')}\n\n"
            "NORMAS:\n"
            "- Mapa cuadrado de 'tamano_km' km; posiciones con cuadrícula de 6 cifras (000000 a la esquina "
            "suroeste; 040040 es la esquina noreste en un mapa de 4 km) o con el nombre de un lugar.\n"
            "- Tipos de lugar: urbano, bosque, colina, campo, lz. Tipos de grupo: infanteria, motorizada, "
            f"blindado{', helicoptero' if perfil.medios_aereos else ''}.\n"
            "- Tareas: mover, atacar, defender, reconocer, patrullar (con 'ruta'), emboscar, esperar.\n"
            "- Objetivos: capturar, destruir (con 'unidades' = indicativos enemigos), defender, extraer "
            "(con 'unidades' = indicativos propios a rescatar), reconocer.\n"
            "- Un grupo con \"jugadores\": true es el de los humanos (en el bando de los jugadores).\n"
            "- Usa a los personajes aliados vivos como jefes de grupos aliados. Ajusta el número de "
            "enemigos a la dificultad.\n"
            f"- Indicativos aliados sugeridos: {', '.join(propios) or 'libres'}; enemigos: "
            f"{', '.join(hostiles) or 'libres'}."
            f"{self._lecciones(perfil.id, capitulo.tipo.value + ' ' + capitulo.sinopsis)}\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_MISION}"
        )
        usuario = f"{estado.resumen()}\n\nPREPARA EL CAPÍTULO {estado.capitulo + 1}: {capitulo.titulo} " \
                  f"({capitulo.tipo.value}). {capitulo.sinopsis}"
        plan = await self._pedir(sistema, usuario, PlanMision, fallback)
        try:
            mision = construir_escenario(plan, perfil, id_mision=f"{estado.id}_c{estado.capitulo + 1}",
                                         semilla=self.semilla)
        except ValueError as error:
            log.warning("El plan de Zeus no es jugable (%s): uso la plantilla.", error)
            mision = construir_escenario(fallback(), perfil, id_mision=f"{estado.id}_c{estado.capitulo + 1}",
                                         semilla=self.semilla)
            mision.avisos.insert(0, f"Plan del modelo descartado: {error}")
        return mision

    async def cerrar_mision(
        self,
        estado: EstadoCampana,
        escenario: Escenario,
        resultado: ResultadoMision,
        *,
        mision_id: int | None = None,
        resumen: str = "",
    ) -> list[str]:
        """Apunta el resultado, aprende como director de juego y guarda la campaña."""
        capitulo = estado.capitulo_actual
        dificultad = estado.dificultad
        cambios = estado.registrar(escenario, resultado, mision_id=mision_id, resumen=resumen, semilla=self.semilla)
        if self.memoria is not None and capitulo is not None:
            enemigos = sum(len(b.unidades) for n, b in escenario.bandos.items() if n != escenario.bando_jugadores)
            situacion = f"Misión de {capitulo.tipo.value} con {enemigos} grupos enemigos (dificultad {dificultad:.2f})"
            if resultado.estado == "victoria" and resultado.puntuacion >= 0.8:
                texto, tipo = "Fue demasiado fácil para los jugadores: añadir enemigos o complicaciones.", "error"
            elif resultado.estado == "derrota":
                texto, tipo = "Fue demasiado dura: menos enemigos o más apoyo para los jugadores.", "error"
            else:
                texto, tipo = "El equilibrio fue bueno: repetir una proporción parecida.", "acierto"
            self.memoria.anadir_leccion(Leccion(rol="zeus", situacion=situacion, leccion=texto, tipo=tipo,
                                                etiquetas=[capitulo.tipo.value], campana=estado.plan.perfil,
                                                mision_origen=mision_id))
        self.guardar(estado)
        return cambios

    # ------------------------------------------------------------------ en directo
    def _sistema_directo(self, perfil: PerfilCampana) -> str:
        return (
            "Eres Zeus, el director de juego de una partida de ARMA 3 en curso. Lo ves todo. Tu trabajo es que "
            "la partida sea emocionante y justa: si los jugadores van sobrados, complica las cosas; si están al "
            "límite, dales un respiro. No intervengas sin motivo.\n"
            f"{perfil.bloque_contexto('enlace')}\n\n"
            "Acciones posibles (máximo 3): refuerzos (un grupo nuevo en 'unidad' para un bando), hora (dia, "
            "amanecer, atardecer, noche), clima (despejado, nubes, lluvia, niebla), mensaje (texto a todos los "
            "jugadores), orden (nueva tarea para el grupo NPC 'destinatario'), nada.\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_INTERVENCION}"
        )

    async def intervenir(self, vista: VistaGlobal, perfil: PerfilCampana, historial: list[str]) -> IntervencionZeus:
        usuario = describir_vista(vista) + "\n\nTUS INTERVENCIONES ANTERIORES:\n" + (
            "\n".join(f"- {h}" for h in historial[-6:]) or "- ninguna") + "\n¿Intervienes ahora?"
        return await self._pedir(self._sistema_directo(perfil), usuario, IntervencionZeus,
                                 lambda: reglas.intervencion_por_reglas(vista, historial))

    async def obedecer(self, orden: str, vista: VistaGlobal, perfil: PerfilCampana) -> IntervencionZeus:
        """El jugador (o el anfitrión) le pide algo directamente a Zeus por el chat."""
        usuario = describir_vista(vista) + f"\n\nEL ANFITRIÓN TE PIDE: {orden}\nHazlo con las acciones posibles."
        return await self._pedir(self._sistema_directo(perfil), usuario, IntervencionZeus,
                                 lambda: reglas.orden_por_reglas(orden, vista))


def describir_vista(vista: VistaGlobal) -> str:
    lineas = [f"TIEMPO {reloj(vista.tiempo)}" + (f" de {reloj(vista.duracion_max_s)}" if vista.duracion_max_s else ""),
              f"CONDICIONES: {vista.condiciones.hora}, {vista.condiciones.clima}",
              f"BANDO DE LOS JUGADORES: {vista.bando_jugadores}"]
    for bando in vista.bandos():
        actuales, maximos = vista.fuerza(bando)
        lineas.append(f"{bando.upper()}: {actuales}/{maximos} efectivos")
        for g in vista.grupos:
            if g.bando == bando and g.estado.value != "destruida":
                lineas.append(f"  - {g.indicativo} ({g.tipo.value}) {g.efectivos}/{g.efectivos_max} en "
                              f"{g.posicion.cuadricula()}, {g.accion.value if g.accion else 'sin tarea'}"
                              + (", EN COMBATE" if g.en_combate else ""))
        for o in vista.objetivos.get(bando, []):
            lineas.append(f"  objetivo: {o.nombre} ({o.tipo.value}) en {o.posicion.cuadricula()}: {o.estado.value}")
    return "\n".join(lineas)


class DirectorPartida:
    """Zeus durante una misión: vigila, interviene cada cierto tiempo y obedece al anfitrión."""

    def __init__(
        self,
        zeus: Zeus,
        mundo: ControlZeus,
        cerebros: dict[str, CerebroBando],
        escenario: Escenario,
        perfil: PerfilCampana,
        *,
        cada_s: float = 300.0,
        automatico: bool = True,
        semilla: int | None = None,
    ) -> None:
        self.zeus = zeus
        self.mundo = mundo
        self.cerebros = cerebros
        self.escenario = escenario
        self.perfil = perfil
        self.cada_s = cada_s
        self.automatico = automatico
        self.semilla = semilla
        self.historial: list[str] = []
        self._ultima = 0.0
        self._refuerzos = 0

    async def iniciar(self) -> None:
        """Zeus abre la misión contando el briefing a todos."""
        if self.escenario.briefing:
            await self.mundo.anunciar(f"{self.escenario.nombre}: {self.escenario.briefing}")

    async def filtrar_chat(self, mensajes: Iterable[MensajeChat]) -> list[MensajeChat]:
        """Los mensajes que empiezan por 'Zeus' son órdenes para el director; el resto sigue su camino."""
        resto = []
        for m in mensajes:
            norm = normalizar(m.texto)
            prefijo = next((p for p in PREFIJOS_ZEUS if norm.startswith(p)), None)
            if prefijo is None or m.es_ia:
                resto.append(m)
                continue
            orden = m.texto.split(maxsplit=1)[1].lstrip(" ,:") if " " in m.texto.strip() else ""
            intervencion = await self.zeus.obedecer(orden, self.mundo.vista_global(), self.perfil)
            hechas = await self.aplicar(intervencion)
            await self.mundo.anunciar(f"{'; '.join(hechas) if hechas else intervencion.razon}")
        return resto

    async def supervisar(self) -> list[str]:
        vista = self.mundo.vista_global()
        if not self.automatico or vista.tiempo - self._ultima < self.cada_s:
            return []
        self._ultima = vista.tiempo
        intervencion = await self.zeus.intervenir(vista, self.perfil, self.historial)
        return await self.aplicar(intervencion)

    async def aplicar(self, intervencion: IntervencionZeus) -> list[str]:
        hechas: list[str] = []
        vista = self.mundo.vista_global()
        for accion in intervencion.acciones:
            try:
                if accion.tipo == TipoIntervencion.REFUERZOS and accion.unidad is not None:
                    hechas.append(await self._refuerzo(accion.bando, accion.unidad, vista))
                elif accion.tipo == TipoIntervencion.HORA and accion.hora:
                    await self.mundo.cambiar_condiciones(hora=accion.hora)
                    hechas.append(f"hora: {accion.hora}")
                elif accion.tipo == TipoIntervencion.CLIMA and accion.clima:
                    await self.mundo.cambiar_condiciones(clima=accion.clima)
                    hechas.append(f"clima: {accion.clima}")
                elif accion.tipo == TipoIntervencion.MENSAJE and accion.texto:
                    await self.mundo.anunciar(accion.texto)
                    hechas.append(f"mensaje: {recortar(accion.texto, 60)}")
                elif accion.tipo == TipoIntervencion.ORDEN and accion.destinatario and accion.tarea:
                    hechas.append(self._ordenar(accion.destinatario, accion.tarea, accion.destino))
            except (ValueError, KeyError) as error:
                log.warning("Zeus no pudo aplicar %s: %s", accion.tipo.value, error)
        if hechas:
            hechas_texto = f"[{reloj(vista.tiempo)}] {intervencion.razon or 'intervención'}: {', '.join(hechas)}"
            self.historial.append(hechas_texto)
            log.info("Zeus: %s", hechas_texto)
        return hechas

    async def _refuerzo(self, bando: str | None, unidad: UnidadPlan, vista: VistaGlobal) -> str:
        bando = bando if bando in self.cerebros else next(
            (b for b in self.cerebros if b != vista.bando_jugadores), vista.bando_jugadores or "opfor")
        lugares = {r.nombre: r.posicion for r in self.escenario.referencias}
        lugares.update({z.nombre: z.posicion for z in self.escenario.zonas_aterrizaje})
        limites = (self.escenario.mapa.ancho_m, self.escenario.mapa.alto_m)
        pos = parsear_posicion(unidad.posicion, lugares, limites)
        if pos is None:
            raise ValueError(f"posición desconocida: {unidad.posicion}")
        self._refuerzos += 1
        indicativo = unidad.indicativo if unidad.indicativo not in {g.indicativo for g in vista.grupos} else (
            f"{unidad.indicativo}-{self._refuerzos}")
        uid = await self.mundo.crear_grupo(bando, GrupoNuevo(
            id=f"zeus_{normalizar(indicativo).replace(' ', '_')}", indicativo=indicativo, tipo=unidad.tipo,
            posicion=pos, efectivos=unidad.efectivos, armada=unidad.armada,
            capacidad=1 if unidad.tipo.es_aerea and not unidad.armada else 0,
        ))
        cerebro = self.cerebros[bando]
        rng = reglas.rng_de(indicativo, self.semilla)
        lado = "jugadores" if bando == vista.bando_jugadores else "enemigo"
        cerebro.personas[uid] = unidad.lider or reglas.crear_persona(rng, self.perfil.id, lado)
        cerebro.ordenes_iniciales[uid] = OrdenMando(unidad=indicativo, tarea=unidad.tarea,
                                                    destino=unidad.destino or pos.cuadricula(), ruta=unidad.ruta)
        texto = "enemigos" if lado == "enemigo" else "aliados"
        return f"refuerzos {texto} {indicativo} ({unidad.tipo.value}, {unidad.efectivos}) en {pos.cuadricula()}"

    def _ordenar(self, destinatario: str, tarea: TareaMando, destino: str | None) -> str:
        for cerebro in self.cerebros.values():
            for agente in cerebro.unidades.values():
                if normalizar(agente.indicativo) == normalizar(destinatario):
                    agente.asignar_orden(OrdenMando(unidad=agente.indicativo, tarea=tarea, destino=destino),
                                         texto=f"Orden de Zeus: {tarea.value} {destino or ''}".strip())
                    return f"orden a {agente.indicativo}: {tarea.value}"
        raise KeyError(destinatario)
