"""Zeus: la IA como director de juego, con control total.

El anfitrión solo hace el encargo ("una campaña de 5 misiones de 90 minutos sobre..."). A partir
de ahí Zeus lo decide todo: inventa la ambientación, elige el terreno de ARMA 3, escribe el
objetivo final y los pasos para llegar, prepara cada misión (lugares, decorado, grupos de NPC,
objetivos y sorpresas programadas), la dirige en directo y, al terminar, escribe la crónica y
deja que la historia cambie según lo que pasó.

Zeus usa la misma red (el modelo de Ollama) que los NPC, con otras instrucciones. Si el modelo
falla, sigue funcionando con las reglas de ``zeus/reglas.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from ..agentes.esquemas import OrdenMando, TareaMando
from ..ambientacion import ERAS, PerfilCampana
from ..geo import Posicion, parsear_posicion
from ..llm import ClienteLLM, ErrorLLM, MensajeLLM, generar_estructurado
from ..memoria import Leccion, Memoria, Recuperador
from ..modelo import GrupoNuevo, ObjetoMapa, Persona, ResultadoMision, TipoObjeto, VistaGlobal
from ..puente.interfaz import ControlZeus
from ..simulador.escenario import Escenario
from ..texto import normalizar, recortar, reloj
from . import reglas
from .campana import EstadoCampana, Imprevisto
from .construir import MisionConstruida, construir_escenario
from .esquemas import (
    AccionZeus,
    AmbientacionPlan,
    DesenlaceZeus,
    IntervencionZeus,
    ObjetoPlan,
    PlanCampana,
    PlanMision,
    SucesoPlan,
    TipoIntervencion,
    UnidadPlan,
)
from .mapas import TERRENOS_BASE, describir_terrenos, elegir_terreno, terreno_por_reglas

if TYPE_CHECKING:
    from ..mando import CerebroBando

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

TIPOS_DECORADO = ", ".join(t.value for t in TipoObjeto)
EJEMPLO_AMBIENTACION = (
    '{"era": "moderna", "epoca": "Mediterráneo, 2035", "descripcion": "...", "terreno": "Altis", '
    '"jugadores": {"nombre": "Fuerza de Tarea Aliada", "descripcion": "..."}, '
    '"enemigo": {"nombre": "Milicia del Frente", "descripcion": "..."}, "doctrina_enemiga": ["..."], '
    '"doctrina": ["..."], "restricciones": [], "estilo_radio": "...", "medios_aereos": true, '
    '"retardo_radio_s": 0, "indicativo_mando": "Mando", "indicativos": ["Alfa-1", "Bravo-1"], '
    '"indicativo_mando_enemigo": "Fortaleza", "indicativos_enemigos": ["Rojo-1", "Rojo-2"], '
    '"vocabulario": ["cambio", "recibido"]}'
)
EJEMPLO_CAMPANA = (
    '{"nombre": "Operación Agua Negra", "premisa": "...", "objetivo_final": "Capturar al coronel Varga", '
    '"capitulos": [{"titulo": "Ojos en el pozo", "sinopsis": "...", "tipo": "reconocimiento", '
    '"objetivo": "Saber cuántos hombres guardan el pozo", "complicacion": "Hay torres de vigilancia", '
    '"critico": false}], "personajes": [{"nombre": "Leila Haddad", "rango": "", "rasgos": ["valiente"], '
    '"historia": "..."}], "antagonista": {"nombre": "Varga", "rango": "Coronel", "rasgos": ["agresivo"], '
    '"historia": "..."}}'
)
EJEMPLO_MISION = (
    '{"nombre": "...", "briefing": "...", "terreno": "Altis", "zona": "alrededores de Kavala", '
    '"hora": "atardecer", "clima": "despejado", "tamano_km": 4, '
    '"lugares": [{"nombre": "Pozo de Nur", "tipo": "urbano", "posicion": "026027"}], '
    '"decorado": [{"tipo": "fortificacion", "lugar": "Pozo de Nur", "bando": "opfor", "radio_m": 80, '
    '"descripcion": "Sacos terreros en la plaza"}, {"tipo": "minas", "lugar": "020022", "bando": "opfor"}], '
    '"bandos": [{"bando": "blufor", "nombre": "...", "mando": true, "base": "007007", "unidades": '
    '[{"indicativo": "Jugadores", "tipo": "infanteria", "efectivos": 6, "posicion": "007007", '
    '"tarea": "atacar", "destino": "Pozo de Nur", "jugadores": true}, {"indicativo": "Lobo", "tipo": '
    '"infanteria", "efectivos": 7, "posicion": "008006", "tarea": "reconocer", "destino": "Pozo de Nur", '
    '"lider": {"nombre": "Leila Haddad", "rango": "", "rasgos": ["valiente"], "historia": "..."}}], '
    '"objetivos": [{"nombre": "Tomar el pozo", "tipo": "capturar", "lugar": "Pozo de Nur"}]}, '
    '{"bando": "opfor", "nombre": "...", "mando": true, "unidades": [{"indicativo": "Sierra-1", "tipo": '
    '"infanteria", "efectivos": 6, "posicion": "Pozo de Nur", "tarea": "defender", "destino": "Pozo de Nur", '
    '"lider": {"nombre": "...", "rango": "Sargento", "rasgos": ["veterano"], "historia": "..."}}], '
    '"objetivos": []}], "bando_jugadores": "blufor", "sucesos": [{"minuto": 25, "tipo": "refuerzos", '
    '"bando": "opfor", "unidad": {"indicativo": "Reacción", "tipo": "motorizada", "efectivos": 4, '
    '"posicion": "035035", "tarea": "atacar", "destino": "Pozo de Nur"}}]}'
)
EJEMPLO_INTERVENCION = (
    '{"razon": "Los jugadores avanzan sin oposición.", "acciones": [{"tipo": "refuerzos", "bando": "opfor", '
    '"texto": "", "unidad": {"indicativo": "Refuerzo", "tipo": "motorizada", "efectivos": 4, "posicion": '
    '"031030", "tarea": "atacar", "destino": "026027"}}, {"tipo": "mensaje", "texto": "Se oyen motores al '
    'norte..."}]}'
)
EJEMPLO_DESENLACE = (
    '{"cronica": "Al caer la tarde, Lobo entró en el pueblo...", "sinopsis_siguiente": "...", '
    '"complicacion_siguiente": "..."}'
)


@dataclass
class CierreMision:
    """Lo que deja una misión en la campaña."""

    numero: int
    titulo: str
    cambios: list[str] = field(default_factory=list)
    cronica: str = ""
    imprevisto: Imprevisto | None = None
    caidos: list[Persona] = field(default_factory=list)


class Zeus:
    def __init__(
        self,
        llm: ClienteLLM,
        memoria: Memoria | None = None,
        *,
        terrenos: list[str] | None = None,
        timeout_s: float = 400.0,
        max_tokens: int = 3000,
        reintentos: int = 1,
        semilla: int | None = None,
    ) -> None:
        self.llm = llm
        self.memoria = memoria
        self.terrenos = list(terrenos or TERRENOS_BASE)
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.reintentos = reintentos
        self.semilla = semilla
        self.estadisticas: dict[str, int] = {"llm": 0, "reglas": 0}

    # ------------------------------------------------------------------ utilidades
    async def _pedir(self, sistema: str, usuario: str, esquema: type[T], fallback: Callable[[], T],
                     max_tokens: int | None = None, temperatura: float = 0.8) -> T:
        try:
            resultado, _ = await asyncio.wait_for(
                generar_estructurado(
                    self.llm, [MensajeLLM("system", sistema), MensajeLLM("user", usuario)], esquema,
                    reintentos=self.reintentos, temperatura=temperatura,
                    max_tokens=min(max_tokens or self.max_tokens, self.max_tokens),
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

    def _lecciones(self, era: str | None, consulta: str) -> str:
        if self.memoria is None:
            return ""
        lecciones = Recuperador(self.memoria).buscar("zeus", era, consulta, k=4)
        if not lecciones:
            return ""
        return "\nLO QUE HAS APRENDIDO DIRIGIENDO OTRAS PARTIDAS:\n" + "\n".join(
            f"- {leccion.texto()}" for leccion in lecciones)

    def _lore(self, estado: EstadoCampana) -> list[str]:
        if self.memoria is None:
            return []
        return [f"{e['titulo']}: {e['texto']}" for e in self.memoria.lore(estado.id) if e["tipo"] != "inicio"]

    # ------------------------------------------------------------------ campaña
    async def crear_campana(self, encargo: str, *, misiones: int, duracion_mision_min: int) -> EstadoCampana:
        """Crea la campaña que pide el anfitrión con el número de misiones y la duración que ha dicho."""
        misiones = max(1, min(reglas.MAX_MISIONES, misiones))
        duracion_mision_min = max(reglas.DURACION_MIN, min(reglas.DURACION_MAX, duracion_mision_min))
        sistema = (
            "Eres Zeus, el director de juego de una comunidad de ARMA 3 con mods realistas. Vas a crear la "
            "AMBIENTACIÓN de una campaña a partir del encargo del anfitrión: época, bandos, doctrina, "
            "restricciones técnicas de la época, estilo de radio e indicativos. Todo debe ser creíble.\n"
            f"Eras posibles: {', '.join(ERAS)}.\n"
            "Terrenos instalados (elige tú el que mejor encaje con la historia): "
            f"{describir_terrenos(self.terrenos)}.\n"
            "- medios_aereos: false si en esa época o para ese bando no hay helicópteros.\n"
            "- retardo_radio_s: segundos que tarda un mensaje de radio (unos 30 en 1944, 0 hoy).\n"
            "- Los indicativos, con el estilo de la época y del bando.\n"
            "Escribe en español. Responde SOLO con JSON con este formato:\n"
            f"{EJEMPLO_AMBIENTACION}"
        )
        ambiente = await self._pedir(sistema, f"ENCARGO DEL ANFITRIÓN: {encargo}", AmbientacionPlan,
                                     lambda: reglas.ambientacion_por_reglas(encargo, self.terrenos),
                                     max_tokens=1200)
        terreno = elegir_terreno(ambiente.terreno, self.terrenos, terreno_por_reglas(encargo, self.terrenos))
        perfil = ambiente.a_perfil(nombre="")

        sistema = (
            "Eres Zeus, el director de juego. Ahora escribes el ARCO de la campaña que te han encargado.\n"
            f"{perfil.bloque_contexto('comandante')}\n"
            f"Terreno principal: {terreno}.\n\n"
            "NORMAS:\n"
            f"- La campaña tiene UN SOLO objetivo final y EXACTAMENTE {misiones} misión(es) para llegar a él.\n"
            f"- Cada misión dura {duracion_mision_min} minutos.\n"
            "- Cada misión es un paso hacia el objetivo final, con su propio objetivo y una complicación "
            "(un problema, un inconveniente, algo que se tuerce) que la hace difícil.\n"
            "- Marca critico=true en los pasos que, si se pierden, hacen imposible el objetivo final "
            "(la última misión siempre lo es). La campaña puede fracasar: no lo pongas fácil.\n"
            "- Tipos de misión: asalto, defensa, emboscada, rescate, reconocimiento, destruccion.\n"
            "- 2-4 personajes aliados con nombre, carácter e historia, y un antagonista.\n"
            "- Rasgos posibles: veterano, novato, valiente, prudente, agresivo, disciplinado, nervioso, impulsivo.\n"
            "Escribe en español."
            f"{self._lecciones(perfil.id, encargo)}\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_CAMPANA}"
        )
        plan = await self._pedir(
            sistema, f"ENCARGO DEL ANFITRIÓN: {encargo}", PlanCampana,
            lambda: reglas.campana_por_reglas(encargo, perfil, terreno, misiones, self.semilla),
            max_tokens=700 + 160 * misiones,
        )
        reglas.ajustar_capitulos(plan, misiones, terreno, perfil.id, self.semilla)
        if len(plan.personajes) < 2:
            rng = reglas.rng_de(encargo, self.semilla)
            usados = {p.nombre for p in plan.personajes}
            plan.personajes += [reglas.crear_persona(rng, perfil.id, "jugadores", usados)
                                for _ in range(2 - len(plan.personajes))]
        perfil.nombre = plan.nombre
        estado = EstadoCampana.nueva(encargo, perfil, terreno, plan, duracion_mision_min)
        estado.id = self._id_libre(estado.id)
        self.guardar(estado)
        if self.memoria is not None:
            self.memoria.anadir_lore(estado.id, "inicio", plan.nombre,
                                     f"{plan.premisa} Objetivo: {plan.objetivo_final}")
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
            self.memoria.guardar_campana(
                estado.id, nombre=estado.plan.nombre, encargo=estado.encargo, objetivo=estado.plan.objetivo_final,
                estado=estado.resultado_final or "en_curso", terreno=estado.terreno,
                datos=estado.model_dump(mode="json"),
            )

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
        perfil = estado.perfil
        duracion = estado.duracion_mision_min
        id_mision = f"{estado.id}_m{estado.capitulo + 1}"

        def fallback() -> PlanMision:
            return reglas.mision_por_reglas(
                perfil, capitulo, nombre_campana=estado.plan.nombre, terreno=estado.terreno,
                personajes=estado.vivos(), antagonista=estado.antagonista, dificultad=estado.dificultad,
                duracion_min=duracion, semilla=self.semilla,
            )

        propios = perfil.indicativos.get("infanteria", [])
        hostiles = perfil.enemigo.indicativos.get("infanteria", [])
        sistema = (
            "Eres Zeus, el director de juego, y tienes el control total de la partida. Preparas tú solo la "
            "siguiente misión de la campaña: eliges el terreno y la zona, moldeas el mapa con el decorado "
            "(fortificaciones, minas, barricadas, ruinas...), colocas los grupos de NPC con su jefe (nombre, "
            "carácter, historia) y su tarea inicial, fijas los objetivos y dejas preparadas sorpresas que "
            "ocurrirán a mitad de misión. Los NPC actuarán por su cuenta según su tarea y su carácter.\n"
            f"{perfil.bloque_contexto('comandante')}\n\n"
            "NORMAS:\n"
            f"- Terrenos instalados: {', '.join(self.terrenos)}. Normalmente usa el de la campaña "
            f"({estado.terreno}); cambia solo si la historia se traslada.\n"
            f"- La misión dura {duracion} minutos. Los sucesos ocurren en un minuto entre 1 y {duracion - 1}.\n"
            "- Zona de operaciones cuadrada de 'tamano_km' km; posiciones con cuadrícula de 6 cifras (000000 "
            "es la esquina suroeste; 040040 la noreste en 4 km) o con el nombre de un lugar.\n"
            "- Tipos de lugar: urbano, bosque, colina, campo, lz. Tipos de grupo: infanteria, motorizada, "
            f"blindado{', helicoptero' if perfil.medios_aereos else ''}.\n"
            f"- Decorado (tipo): {TIPOS_DECORADO}. Pon el bando que lo coloca.\n"
            "- Tareas: mover, atacar, defender, reconocer, patrullar (con 'ruta'), emboscar, esperar.\n"
            "- Objetivos: capturar, destruir (con 'unidades' = indicativos enemigos), defender, extraer "
            "(con 'unidades' = indicativos propios a rescatar), reconocer.\n"
            "- Sucesos (tipo): refuerzos (con 'unidad'), clima, hora, mensaje, decorado (con 'objeto').\n"
            "- Un grupo con \"jugadores\": true es el de los humanos (en el bando de los jugadores).\n"
            "- La complicación del paso tiene que notarse en la misión (decorado, enemigos o sucesos).\n"
            "- Usa a los personajes aliados vivos como jefes de grupos aliados. Ajusta el número de "
            "enemigos a la dificultad.\n"
            f"- Indicativos aliados sugeridos: {', '.join(propios) or 'libres'}; enemigos: "
            f"{', '.join(hostiles) or 'libres'}."
            f"{self._lecciones(perfil.id, capitulo.tipo.value + ' ' + capitulo.sinopsis)}\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_MISION}"
        )
        usuario = (f"{estado.resumen(self._lore(estado))}\n\nPREPARA LA MISIÓN {estado.capitulo + 1} DE "
                   f"{estado.misiones_total}: {capitulo.titulo} ({capitulo.tipo.value}). {capitulo.sinopsis}\n"
                   f"Objetivo de esta misión: {capitulo.objetivo}\nComplicación: {capitulo.complicacion}")
        plan = await self._pedir(sistema, usuario, PlanMision, fallback)
        opciones = {"duracion_min": duracion, "terrenos": self.terrenos, "terreno_campana": estado.terreno,
                    "id_mision": id_mision, "semilla": self.semilla}
        try:
            mision = construir_escenario(plan, perfil, **opciones)  # type: ignore[arg-type]
        except ValueError as error:
            log.warning("El plan de Zeus no es jugable (%s): uso las reglas.", error)
            mision = construir_escenario(fallback(), perfil, **opciones)  # type: ignore[arg-type]
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
        sucesos: list[str] | None = None,
    ) -> CierreMision:
        """Apunta el resultado, escribe la crónica, aprende como director de juego y guarda la campaña."""
        capitulo = estado.capitulo_actual
        numero = estado.capitulo + 1
        titulo = capitulo.titulo if capitulo else escenario.nombre
        dificultad = estado.dificultad
        registro = estado.registrar(escenario, resultado, mision_id=mision_id, resumen=resumen, semilla=self.semilla)
        cierre = CierreMision(numero=numero, titulo=titulo, cambios=registro.cambios, imprevisto=registro.imprevisto,
                              caidos=registro.caidos)

        siguiente = estado.capitulo_actual
        sistema = (
            "Eres Zeus, el director de juego. Acaba de terminar una misión de la campaña. Escribe la CRÓNICA: "
            "lo que ha pasado contado como una historia, en 3-5 frases, con los nombres de los personajes. "
            + ("La campaña ha TERMINADO: cuenta también cómo acaba la historia. "
               if estado.terminada else
               "Después reescribe la sinopsis y la complicación de la siguiente misión para que la historia "
               "siga lo que ha pasado (y el imprevisto, si lo hay). ")
            + "Escribe en español. Responde SOLO con JSON con este formato:\n" + EJEMPLO_DESENLACE
        )
        usuario = "\n".join([
            estado.resumen(self._lore(estado)),
            f"MISIÓN TERMINADA: {titulo} — {resultado.estado.upper()} ({resultado.motivo}), duración "
            f"{reloj(resultado.duracion_s)}, bajas propias {resultado.bajas_propias}, "
            f"enemigas {resultado.bajas_enemigas}.",
            "LO MÁS IMPORTANTE QUE PASÓ:", *[f"- {s}" for s in (sucesos or [])[:12]],
            "CAMBIOS EN LA CAMPAÑA:", *[f"- {c}" for c in registro.cambios],
            f"SIGUIENTE MISIÓN: {siguiente.titulo}. {siguiente.sinopsis} Problema: {siguiente.complicacion}"
            if siguiente else f"FINAL DE LA CAMPAÑA: {estado.estado_texto}. {estado.motivo_final}",
        ])
        desenlace = await self._pedir(
            sistema, usuario, DesenlaceZeus,
            lambda: DesenlaceZeus(cronica=reglas.cronica_por_reglas(titulo, resultado, registro.cambios)),
            max_tokens=700, temperatura=0.7,
        )
        cierre.cronica = desenlace.cronica.strip() or reglas.cronica_por_reglas(titulo, resultado, registro.cambios)
        if siguiente is not None:
            if desenlace.sinopsis_siguiente.strip():
                siguiente.sinopsis = desenlace.sinopsis_siguiente.strip()
            if desenlace.complicacion_siguiente.strip() and registro.imprevisto is None:
                siguiente.complicacion = desenlace.complicacion_siguiente.strip()

        if self.memoria is not None:
            self.memoria.anadir_lore(estado.id, "mision", f"Misión {numero}: {titulo}", cierre.cronica, mision_id)
            for persona in registro.caidos:
                self.memoria.anadir_lore(estado.id, "personaje", f"Cae {persona.nombre}",
                                         f"{persona.presentacion().split('.')[0]} murió en «{titulo}».", mision_id)
            if registro.imprevisto is not None:
                self.memoria.anadir_lore(estado.id, "imprevisto", "Imprevisto grave" if registro.imprevisto.grave
                                         else "Imprevisto", registro.imprevisto.texto, mision_id)
            if estado.terminada:
                self.memoria.anadir_lore(estado.id, "final", estado.estado_texto, estado.motivo_final, mision_id)
            if capitulo is not None:
                self._aprender(estado, escenario, resultado, dificultad, capitulo.tipo.value, mision_id)
        self.guardar(estado)
        return cierre

    def _aprender(self, estado: EstadoCampana, escenario: Escenario, resultado: ResultadoMision, dificultad: float,
                  tipo: str, mision_id: int | None) -> None:
        assert self.memoria is not None
        enemigos = sum(len(b.unidades) for n, b in escenario.bandos.items() if n != escenario.bando_jugadores)
        situacion = f"Misión de {tipo} con {enemigos} grupos enemigos (dificultad {dificultad:.2f})"
        if resultado.estado == "victoria" and resultado.puntuacion >= 0.8:
            texto, clase = "Fue demasiado fácil para los jugadores: añadir enemigos o complicaciones.", "error"
        elif resultado.estado == "derrota":
            texto, clase = "Fue demasiado dura: menos enemigos o más apoyo para los jugadores.", "error"
        else:
            texto, clase = "El equilibrio fue bueno: repetir una proporción parecida.", "acierto"
        self.memoria.anadir_leccion(Leccion(rol="zeus", situacion=situacion, leccion=texto, tipo=clase,
                                            etiquetas=[tipo], epoca=estado.perfil.id, mision_origen=mision_id))

    # ------------------------------------------------------------------ en directo
    def _sistema_directo(self, perfil: PerfilCampana) -> str:
        return (
            "Eres Zeus, el director de juego de una partida de ARMA 3 en curso. Lo ves todo y decides tú solo. "
            "Tu trabajo es que la partida sea emocionante y justa: si los jugadores van sobrados, complica las "
            "cosas; si están al límite, dales un respiro. No intervengas sin motivo.\n"
            f"{perfil.bloque_contexto('enlace')}\n\n"
            "Acciones posibles (máximo 3): refuerzos (un grupo nuevo en 'unidad' para un bando), hora (dia, "
            "amanecer, atardecer, noche), clima (despejado, nubes, lluvia, niebla), mensaje (texto a todos los "
            "jugadores), orden (nueva tarea para el grupo NPC 'destinatario'), decorado (colocar un 'objeto' "
            f"en el mapa: {TIPOS_DECORADO}), nada.\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_INTERVENCION}"
        )

    async def intervenir(self, vista: VistaGlobal, perfil: PerfilCampana, historial: list[str]) -> IntervencionZeus:
        usuario = describir_vista(vista) + "\n\nTUS INTERVENCIONES ANTERIORES:\n" + (
            "\n".join(f"- {h}" for h in historial[-6:]) or "- ninguna") + "\n¿Intervienes ahora?"
        return await self._pedir(self._sistema_directo(perfil), usuario, IntervencionZeus,
                                 lambda: reglas.intervencion_por_reglas(vista, historial), max_tokens=700)


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
    if vista.objetos:
        lineas.append("DECORADO: " + "; ".join(f"{o.tipo.value} en {o.posicion.cuadricula()}"
                                               for o in vista.objetos[:10]))
    return "\n".join(lineas)


class DirectorPartida:
    """Zeus durante una misión: lanza las sorpresas que dejó preparadas e interviene cuando hace falta."""

    def __init__(
        self,
        zeus: Zeus,
        mundo: ControlZeus,
        cerebros: dict[str, CerebroBando],
        escenario: Escenario,
        perfil: PerfilCampana,
        *,
        sucesos: list[SucesoPlan] | None = None,
        cada_s: float = 300.0,
        semilla: int | None = None,
    ) -> None:
        self.zeus = zeus
        self.mundo = mundo
        self.cerebros = cerebros
        self.escenario = escenario
        self.perfil = perfil
        self.pendientes = sorted(sucesos or [], key=lambda s: s.minuto)
        self.cada_s = cada_s
        self.semilla = semilla
        self.historial: list[str] = []
        self._ultima = 0.0
        self._refuerzos = 0
        self._objetos = 0

    async def iniciar(self) -> None:
        """Zeus abre la misión contando el briefing a todos."""
        if self.escenario.briefing:
            await self.mundo.anunciar(f"{self.escenario.nombre}: {self.escenario.briefing}")

    async def supervisar(self) -> list[str]:
        vista = self.mundo.vista_global()
        hechas: list[str] = []
        while self.pendientes and self.pendientes[0].minuto * 60 <= vista.tiempo:
            suceso = self.pendientes.pop(0)
            hechas += await self.aplicar(IntervencionZeus(razon=f"sorpresa preparada (minuto {suceso.minuto})",
                                                          acciones=[suceso]))
            if suceso.texto and suceso.tipo != TipoIntervencion.MENSAJE:
                await self.mundo.anunciar(suceso.texto)
        if vista.tiempo - self._ultima >= self.cada_s:
            self._ultima = vista.tiempo
            intervencion = await self.zeus.intervenir(vista, self.perfil, self.historial)
            hechas += await self.aplicar(intervencion)
        return hechas

    async def aplicar(self, intervencion: IntervencionZeus) -> list[str]:
        hechas: list[str] = []
        vista = self.mundo.vista_global()
        for accion in intervencion.acciones:
            try:
                hecha = await self._aplicar_accion(accion, vista)
            except (ValueError, KeyError) as error:
                log.warning("Zeus no pudo aplicar %s: %s", accion.tipo.value, error)
                continue
            if hecha:
                hechas.append(hecha)
        if hechas:
            hechas_texto = f"[{reloj(vista.tiempo)}] {intervencion.razon or 'intervención'}: {', '.join(hechas)}"
            self.historial.append(hechas_texto)
            log.info("Zeus: %s", hechas_texto)
        return hechas

    async def _aplicar_accion(self, accion: AccionZeus, vista: VistaGlobal) -> str | None:
        if accion.tipo == TipoIntervencion.REFUERZOS and accion.unidad is not None:
            return await self._refuerzo(accion.bando, accion.unidad, vista)
        if accion.tipo == TipoIntervencion.HORA and accion.hora:
            await self.mundo.cambiar_condiciones(hora=accion.hora)
            return f"hora: {accion.hora}"
        if accion.tipo == TipoIntervencion.CLIMA and accion.clima:
            await self.mundo.cambiar_condiciones(clima=accion.clima)
            return f"clima: {accion.clima}"
        if accion.tipo == TipoIntervencion.MENSAJE and accion.texto:
            await self.mundo.anunciar(accion.texto)
            return f"mensaje: {recortar(accion.texto, 60)}"
        if accion.tipo == TipoIntervencion.ORDEN and accion.destinatario and accion.tarea:
            return self._ordenar(accion.destinatario, accion.tarea, accion.destino)
        if accion.tipo == TipoIntervencion.DECORADO and accion.objeto is not None:
            return await self._decorado(accion.objeto)
        return None

    def _lugares(self) -> dict[str, Posicion]:
        lugares = {r.nombre: r.posicion for r in self.escenario.referencias}
        lugares.update({z.nombre: z.posicion for z in self.escenario.zonas_aterrizaje})
        return lugares

    def _posicion(self, texto: str | None) -> Posicion:
        mapa = self.escenario.mapa
        pos = parsear_posicion(texto, self._lugares(), (mapa.ancho_m, mapa.alto_m))
        if pos is None:
            raise ValueError(f"posición desconocida: {texto}")
        return pos

    async def _refuerzo(self, bando: str | None, unidad: UnidadPlan, vista: VistaGlobal) -> str:
        bando = bando if bando in self.cerebros else next(
            (b for b in self.cerebros if b != vista.bando_jugadores), vista.bando_jugadores or "opfor")
        pos = self._posicion(unidad.posicion)
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

    async def _decorado(self, objeto: ObjetoPlan) -> str:
        pos = self._posicion(objeto.lugar)
        self._objetos += 1
        bando = objeto.bando if objeto.bando in self.escenario.bandos else None
        await self.mundo.colocar_objeto(ObjetoMapa(
            id=f"zeus_obj{self._objetos}_{objeto.tipo.value}", tipo=objeto.tipo, posicion=pos, radio_m=objeto.radio_m,
            descripcion=objeto.descripcion, bando=bando))
        return f"decorado: {objeto.tipo.value} en {pos.cuadricula()}"

    def _ordenar(self, destinatario: str, tarea: TareaMando, destino: str | None) -> str:
        for cerebro in self.cerebros.values():
            for agente in cerebro.unidades.values():
                if normalizar(agente.indicativo) == normalizar(destinatario):
                    agente.asignar_orden(OrdenMando(unidad=agente.indicativo, tarea=tarea, destino=destino),
                                         texto=f"Orden de Zeus: {tarea.value} {destino or ''}".strip())
                    return f"orden a {agente.indicativo}: {tarea.value}"
        raise KeyError(destinatario)
