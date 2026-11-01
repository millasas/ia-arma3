"""Reflexión post-misión ("informe post-acción"): de los errores y aciertos salen lecciones.

Tras cada misión:

1. Las lecciones que se usaron suman un uso, y un éxito si la misión salió bien; las que
   casi nunca funcionan se archivan.
2. Para cada rol (comandante, líderes, pilotos) el modelo analiza sus decisiones y lo que
   pasó, y propone lecciones concretas. Si el modelo falla, se usan reglas.
3. Las lecciones nuevas se guardan; si ya existía una casi igual, se refuerza en su lugar.

Así la IA mejora de una misión a la siguiente sin reentrenar el modelo: las lecciones se
inyectan en las instrucciones de los agentes cuando la situación se parece.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..ambientacion import PerfilCampana
from ..llm import ClienteLLM, ErrorLLM, MensajeLLM, generar_estructurado
from ..modelo import ResultadoMision
from ..texto import recortar, reloj, similitud
from .almacen import Leccion, Memoria

log = logging.getLogger(__name__)

ROLES_REFLEXION = ("comandante", "lider", "piloto")
NOMBRE_ROL = {"comandante": "comandante", "lider": "líder de escuadra", "piloto": "piloto de helicóptero"}
UMBRAL_DUPLICADO = 0.5

EJEMPLO_REFLEXION = (
    '{"resumen": "Breve valoración de la misión desde tu rol.", "lecciones": ['
    '{"situacion": "Cuándo aplica (p. ej. aterrizar cerca de enemigos)", '
    '"leccion": "Qué hacer la próxima vez, concreto y accionable", '
    '"tipo": "error", "alcance": "general", "etiquetas": ["aterrizaje", "lz"]}]}'
)


class LeccionPropuesta(BaseModel):
    situacion: str
    leccion: str
    tipo: Literal["error", "acierto"] = "error"
    alcance: Literal["epoca", "general"] = "epoca"
    etiquetas: list[str] = Field(default_factory=list, json_schema_extra={"maxItems": 5})

    @field_validator("alcance", mode="before")
    @classmethod
    def _alcance(cls, valor: Any) -> str:
        return "general" if str(valor).strip().lower() == "general" else "epoca"

    @field_validator("situacion", "leccion")
    @classmethod
    def _recortar(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("no puede estar vacío")
        return recortar(valor, 300)

    @field_validator("etiquetas")
    @classmethod
    def _limitar(cls, valor: list[str]) -> list[str]:
        return [recortar(e.strip().lower(), 30) for e in valor if e.strip()][:5]


class ReflexionRol(BaseModel):
    resumen: str = ""
    lecciones: list[LeccionPropuesta] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("lecciones")
    @classmethod
    def _maximo(cls, valor: list[LeccionPropuesta]) -> list[LeccionPropuesta]:
        return valor[:3]


@dataclass
class InformeReflexion:
    mision_id: int
    exito: bool
    resumen: str = ""
    nuevas: list[Leccion] = field(default_factory=list)
    reforzadas: list[Leccion] = field(default_factory=list)
    archivadas: list[int] = field(default_factory=list)
    origen: dict[str, str] = field(default_factory=dict)


def resumen_plantilla(resultado: ResultadoMision) -> str:
    return (
        f"Misión terminada en {resultado.estado} ({resultado.motivo}): "
        f"{resultado.objetivos_completados}/{resultado.objetivos_totales} objetivos, "
        f"{resultado.bajas_propias} bajas propias y {resultado.bajas_enemigas} enemigas "
        f"en {reloj(resultado.duracion_s)}. Puntuación {resultado.puntuacion:.2f}."
    )


def _metricas_rol(rol: str, resultado: ResultadoMision) -> list[dict[str, Any]]:
    unidades = resultado.metricas.get("unidades", {})
    aereas = rol == "piloto"
    return [m for m in unidades.values() if bool(m.get("aerea")) == aereas]


def reflexion_por_reglas(
    rol: str, resultado: ResultadoMision, eventos: list[dict[str, Any]]
) -> ReflexionRol:
    """Lecciones deducidas de las métricas cuando el modelo no está disponible."""
    lecciones: list[LeccionPropuesta] = []
    ratio_bajas = resultado.bajas_propias / max(1, resultado.efectivos_iniciales)
    if rol == "comandante":
        if resultado.estado != "victoria" and "tiempo" in resultado.motivo.lower():
            lecciones.append(
                LeccionPropuesta(
                    situacion="Misión con límite de tiempo y varios objetivos",
                    leccion="Concentrar las fuerzas en el objetivo principal desde el inicio y no "
                    "dejar unidades esperando; el tiempo se agotó sin completar la misión.",
                    etiquetas=["tiempo", "objetivo", "concentracion"],
                    alcance="general",
                )
            )
        if ratio_bajas >= 0.35:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Asalto a posiciones enemigas defendidas",
                    leccion=f"Reconocer y fijar al enemigo con apoyo (aéreo o de otra escuadra) antes "
                    f"de asaltar; se perdió el {ratio_bajas:.0%} de los efectivos.",
                    etiquetas=["asalto", "bajas", "reconocimiento", "apoyo"],
                    alcance="general",
                )
            )
        if resultado.estado == "victoria" and ratio_bajas < 0.2:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Ataque coordinado a un objetivo",
                    leccion="Reconocer primero y atacar después con varias unidades a la vez funcionó "
                    "con pocas bajas; repetir el esquema.",
                    tipo="acierto",
                    etiquetas=["asalto", "coordinacion", "reconocimiento"],
                )
            )
    elif rol == "lider":
        sorpresas = [e for e in eventos if e["tipo"] == "bajas" and e["datos"].get("sorpresa")]
        if sorpresas:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Avance cerca de bosques, pueblos o zonas sin reconocer",
                    leccion="Avanzar en postura sigilo y reconocer antes; la escuadra cayó en una "
                    "emboscada y sufrió bajas antes de poder responder.",
                    etiquetas=["emboscada", "sigilo", "reconocimiento", "bosque"],
                    alcance="general",
                )
            )
        diezmadas = [
            m for m in _metricas_rol(rol, resultado)
            if m.get("efectivos_iniciales") and m["efectivos_finales"] / m["efectivos_iniciales"] < 0.5
        ]
        if diezmadas:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Escuadra bajo fuego intenso con muchas bajas",
                    leccion="Romper el contacto a tiempo, buscar cobertura y pedir apoyo antes de "
                    "quedar por debajo de la mitad de efectivos.",
                    etiquetas=["bajas", "cobertura", "retirada", "apoyo"],
                    alcance="general",
                )
            )
    elif rol == "piloto":
        aterrizajes = [e for e in eventos if e["tipo"] == "aterrizaje"]
        calientes_mal = [
            e
            for e in aterrizajes
            if e["datos"].get("zona_caliente") and e["datos"].get("resultado") != "exito"
        ]
        accidentes = [e for e in aterrizajes if e["datos"].get("resultado") == "accidente"]
        if calientes_mal:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Aterrizaje en una LZ con enemigos cerca",
                    leccion="No aterrizar con contactos a menos de 500 m: elegir una LZ alternativa "
                    "más alejada o pedir apoyo para limpiar la zona antes.",
                    etiquetas=["aterrizaje", "lz", "zona caliente"],
                    alcance="general",
                )
            )
        if accidentes:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Aproximación final y toma de tierra",
                    leccion="Reducir velocidad y tasa de descenso antes de la recogida; una "
                    "aproximación brusca terminó en accidente.",
                    etiquetas=["aterrizaje", "accidente", "aproximacion"],
                    alcance="general",
                )
            )
        if aterrizajes and not calientes_mal and not accidentes:
            lecciones.append(
                LeccionPropuesta(
                    situacion="Transporte de tropas en helicóptero",
                    leccion="Aterrizar en LZ frías y lejos del enemigo permitió desembarcos sin "
                    "bajas; mantener ese criterio.",
                    tipo="acierto",
                    etiquetas=["aterrizaje", "transporte", "lz"],
                )
            )
    return ReflexionRol(resumen=resumen_plantilla(resultado), lecciones=lecciones)


def _resumen_decision(d: dict[str, Any]) -> str:
    try:
        respuesta = json.loads(d["respuesta"])
    except (TypeError, ValueError):
        return recortar(str(d["respuesta"]), 160)
    if "ordenes" in respuesta:
        ordenes = ", ".join(
            f"{o.get('unidad')}:{o.get('tarea')}" + (f"→{o.get('destino')}" if o.get("destino") else "")
            for o in respuesta.get("ordenes", [])
        )
        return recortar(f"{respuesta.get('intencion', '')} | órdenes: {ordenes or 'ninguna'}", 220)
    accion = respuesta.get("accion", "?")
    destino = respuesta.get("destino")
    texto = f"{accion}" + (f" → {destino}" if destino else "")
    return recortar(f"{texto} — {respuesta.get('evaluacion', '')}", 200)


class Reflexion:
    def __init__(
        self,
        llm: ClienteLLM,
        memoria: Memoria,
        perfil: PerfilCampana,
        *,
        umbral_exito: float = 0.6,
        timeout_s: float = 180.0,
    ) -> None:
        self.llm = llm
        self.memoria = memoria
        self.perfil = perfil
        self.umbral_exito = umbral_exito
        self.timeout_s = timeout_s

    def _contexto(
        self,
        rol: str,
        resultado: ResultadoMision,
        decisiones: list[dict[str, Any]],
        eventos: list[dict[str, Any]],
    ) -> str:
        lineas = [
            f"RESULTADO: {resultado.estado.upper()} — {resultado.motivo}.",
            f"Duración {reloj(resultado.duracion_s)}; objetivos {resultado.objetivos_completados}/"
            f"{resultado.objetivos_totales}; bajas propias {resultado.bajas_propias}/"
            f"{resultado.efectivos_iniciales}; bajas enemigas {resultado.bajas_enemigas}; "
            f"puntuación {resultado.puntuacion:.2f}.",
        ]
        metricas = _metricas_rol(rol, resultado)
        if metricas:
            lineas.append("MÉTRICAS DE TUS UNIDADES:")
            for m in metricas:
                datos = ", ".join(f"{k}={v}" for k, v in m.items() if k not in ("aerea", "indicativo"))
                lineas.append(f"- {m.get('indicativo', '?')}: {datos}")
        propias = [d for d in decisiones if d["rol"] == rol][-15:]
        if propias:
            lineas.append("DECISIONES TOMADAS (las últimas):")
            lineas += [
                f"- [{reloj(d['tiempo'] or 0)}] {d['agente']}: {_resumen_decision(d)}" for d in propias
            ]
        importantes = [e for e in eventos if e["tipo"] not in ("radio", "contacto")][-25:]
        if importantes:
            lineas.append("EVENTOS CLAVE:")
            lineas += [f"- [{reloj(e['tiempo'])}] {e['descripcion']}" for e in importantes]
        return "\n".join(lineas)

    def _mensajes(self, rol: str, contexto: str, existentes: list[Leccion]) -> list[MensajeLLM]:
        sistema = (
            f"Eres el oficial de análisis de {self.perfil.bando_ia.nombre}. Revisas una misión "
            f"terminada desde el punto de vista del {NOMBRE_ROL.get(rol, rol)} para que la IA "
            "aprenda de sus errores y repita sus aciertos.\n"
            f"{self.perfil.bloque_contexto(rol)}\n\n"
            "Extrae como máximo 3 lecciones CONCRETAS y ACCIONABLES que expliquen por qué pasó "
            "lo que pasó. Cada lección dice en qué situación aplica y qué hacer. Usa "
            '"alcance": "general" si vale para cualquier época y "epoca" si solo vale en esta '
            "época. No repitas lecciones que ya se conocen. Si no hay nada nuevo que aprender, "
            "devuelve la lista vacía.\n"
            f"Responde SOLO con JSON con este formato:\n{EJEMPLO_REFLEXION}"
        )
        usuario = contexto
        if existentes:
            usuario += "\n\nLECCIONES YA CONOCIDAS (no las repitas):\n" + "\n".join(
                f"- {leccion.texto()}" for leccion in existentes[:10]
            )
        return [MensajeLLM("system", sistema), MensajeLLM("user", usuario)]

    def guardar(self, propuesta: LeccionPropuesta, rol: str, mision_id: int) -> tuple[Leccion, bool]:
        """Guarda la lección o refuerza una casi idéntica. Devuelve (lección, es_nueva)."""
        epoca = None if propuesta.alcance == "general" else self.perfil.id
        for existente in self.memoria.lecciones(rol=rol, epoca=self.perfil.id, solo_activas=False):
            if similitud(existente.leccion, propuesta.leccion) >= UMBRAL_DUPLICADO and existente.id:
                self.memoria.reforzar_leccion(existente.id)
                return self.memoria.leccion(existente.id) or existente, False
        nueva = Leccion(
            rol=rol,
            situacion=propuesta.situacion,
            leccion=propuesta.leccion,
            tipo=propuesta.tipo,
            etiquetas=propuesta.etiquetas,
            epoca=epoca,
            mision_origen=mision_id,
        )
        self.memoria.anadir_leccion(nueva)
        return nueva, True

    async def analizar(
        self,
        mision_id: int,
        resultado: ResultadoMision,
        lecciones_usadas: Iterable[int] = (),
    ) -> InformeReflexion:
        exito = resultado.exito or resultado.puntuacion >= self.umbral_exito
        informe = InformeReflexion(mision_id=mision_id, exito=exito)
        self.memoria.registrar_uso_lecciones(lecciones_usadas, exito)
        informe.archivadas = self.memoria.revisar_lecciones()

        decisiones = self.memoria.decisiones(mision_id)
        eventos = self.memoria.eventos(mision_id)
        presentes = Counter(d["rol"] for d in decisiones)
        roles = [r for r in ROLES_REFLEXION if presentes[r]] or ["comandante"]

        for rol in roles:
            contexto = self._contexto(rol, resultado, decisiones, eventos)
            existentes = self.memoria.lecciones(rol=rol, epoca=self.perfil.id)

            def fallback(rol: str = rol) -> ReflexionRol:
                return reflexion_por_reglas(rol, resultado, eventos)

            try:
                reflexion, _ = await asyncio.wait_for(
                    generar_estructurado(
                        self.llm,
                        self._mensajes(rol, contexto, existentes),
                        ReflexionRol,
                        temperatura=0.3,
                        metadatos={"rol": "reflexion", "agente": rol, "fallback": fallback},
                    ),
                    timeout=self.timeout_s,
                )
                informe.origen[rol] = "llm"
            except (ErrorLLM, asyncio.TimeoutError) as error:
                log.warning("Reflexión de %s sin modelo (%s); se usan reglas.", rol, error)
                reflexion = fallback()
                informe.origen[rol] = "reglas"
            for propuesta in reflexion.lecciones:
                leccion, es_nueva = self.guardar(propuesta, rol, mision_id)
                (informe.nuevas if es_nueva else informe.reforzadas).append(leccion)
            if rol == "comandante" and reflexion.resumen.strip():
                informe.resumen = reflexion.resumen.strip()

        informe.resumen = informe.resumen or resumen_plantilla(resultado)
        self.memoria.actualizar_resumen(mision_id, informe.resumen)
        return informe
