"""Estado de una campaña dirigida por Zeus: pasos, personajes, historial, margen de error y final.

Una campaña tiene **un solo objetivo final** y el número de misiones (pasos) que pidió el
anfitrión. Cada paso tiene su propio problema y puede salir mal: una derrota gasta margen de
error, perder un paso crítico o quedarse sin margen hace imposible el objetivo, y entre misión
y misión pueden surgir imprevistos (algunos graves). El objetivo final se cumple solo si se
gana la última misión.

Se guarda en la base de datos después de cada cambio, así que la campaña continúa donde se
dejó aunque se apague el PC.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..ambientacion import PerfilCampana
from ..modelo import Persona, ResultadoMision
from ..simulador.escenario import Escenario
from ..texto import normalizar, reloj
from .esquemas import CapituloPlan, PlanCampana
from .reglas import crear_persona, imprevisto_por_reglas

DIFICULTAD_MIN, DIFICULTAD_MAX = 0.6, 1.8
MAX_RECUERDOS = 8
PROB_IMPREVISTO = 0.35


def margen_inicial(misiones: int) -> int:
    """Cuántas derrotas puede encajar la campaña sin que el objetivo se pierda."""
    return max(0, misiones // 3)


class Personaje(BaseModel):
    persona: Persona
    vivo: bool = True
    misiones: int = 0


class RegistroMision(BaseModel):
    capitulo: int
    titulo: str
    estado: str
    puntuacion: float
    resumen: str = ""
    bajas_propias: int = 0
    bajas_enemigas: int = 0
    duracion_s: float = 0.0
    mision_id: int | None = None
    terreno: str = ""


class Imprevisto(BaseModel):
    texto: str
    grave: bool = False


@dataclass
class Registro:
    """Lo que ha cambiado en la campaña al cerrar una misión."""

    cambios: list[str] = field(default_factory=list)
    caidos: list[Persona] = field(default_factory=list)
    imprevisto: Imprevisto | None = None


class EstadoCampana(BaseModel):
    id: str
    encargo: str
    perfil: PerfilCampana
    terreno: str
    plan: PlanCampana
    duracion_mision_min: int = 60
    capitulo: int = 0
    dificultad: float = 1.0
    margen: int = 0
    margen_inicial: int = 0
    personajes: list[Personaje] = Field(default_factory=list)
    antagonista: Persona | None = None
    historial: list[RegistroMision] = Field(default_factory=list)
    terminada: bool = False
    resultado_final: Literal["cumplida", "fracasada"] | None = None
    motivo_final: str = ""
    creada: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def nueva(cls, encargo: str, perfil: PerfilCampana, terreno: str, plan: PlanCampana,
              duracion_mision_min: int) -> EstadoCampana:
        margen = margen_inicial(len(plan.capitulos))
        return cls(
            id=normalizar(plan.nombre).replace(" ", "_")[:40] or "campana",
            encargo=encargo,
            perfil=perfil,
            terreno=terreno,
            plan=plan,
            duracion_mision_min=duracion_mision_min,
            margen=margen,
            margen_inicial=margen,
            personajes=[Personaje(persona=p) for p in plan.personajes],
            antagonista=plan.antagonista,
        )

    @property
    def misiones_total(self) -> int:
        return len(self.plan.capitulos)

    @property
    def capitulo_actual(self) -> CapituloPlan | None:
        if self.terminada or self.capitulo >= len(self.plan.capitulos):
            return None
        return self.plan.capitulos[self.capitulo]

    @property
    def estado_texto(self) -> str:
        if self.resultado_final == "cumplida":
            return "OBJETIVO CUMPLIDO"
        if self.resultado_final == "fracasada":
            return "OBJETIVO NO CUMPLIDO"
        return f"en curso (misión {self.capitulo + 1} de {self.misiones_total})"

    def vivos(self) -> list[Persona]:
        return [p.persona for p in self.personajes if p.vivo]

    def resumen(self, lore: list[str] | None = None) -> str:
        """Lo que Zeus necesita saber para preparar la siguiente misión."""
        p = self.perfil
        lineas = [f"CAMPAÑA: {self.plan.nombre}. {self.plan.premisa}",
                  f"OBJETIVO FINAL: {self.plan.objetivo_final}",
                  f"Época: {p.epoca}. Terreno principal: {self.terreno}.",
                  f"Jugadores: {p.bando_ia.nombre}. Enemigo: {p.enemigo.nombre}."]
        if self.antagonista:
            lineas.append(f"Antagonista: {self.antagonista.presentacion()}")
        lineas.append(f"PASOS ({self.misiones_total} misiones de {self.duracion_mision_min} min):")
        for i, c in enumerate(self.plan.capitulos):
            marca = "✓" if i < self.capitulo else ("→" if i == self.capitulo else " ")
            critico = " [CRÍTICO]" if c.critico else ""
            lineas.append(f" {marca} {i + 1}. {c.titulo} ({c.tipo.value}){critico}: {c.sinopsis}"
                          + (f" Problema: {c.complicacion}" if c.complicacion else ""))
        if lore:
            lineas.append("LO QUE HA PASADO (crónica):")
            lineas += [f"- {t}" for t in lore[-6:]]
        elif self.historial:
            lineas.append("LO QUE HA PASADO:")
            lineas += [f"- {r.titulo}: {r.estado} (bajas {r.bajas_propias}). {r.resumen}" for r in self.historial[-5:]]
        if self.personajes:
            lineas.append("PERSONAJES ALIADOS:")
            lineas += [f"- {p.persona.presentacion()}" + ("" if p.vivo else " [MUERTO]") for p in self.personajes]
        lineas.append(f"MARGEN DE ERROR: quedan {self.margen} derrota(s) que se pueden encajar.")
        lineas.append(f"DIFICULTAD ACTUAL: {self.dificultad:.2f} (1 = normal; sube si los jugadores ganan con "
                      "facilidad y baja si pierden).")
        return "\n".join(lineas)

    def registrar(
        self,
        escenario: Escenario,
        resultado: ResultadoMision,
        *,
        mision_id: int | None = None,
        resumen: str = "",
        semilla: int | None = None,
    ) -> Registro:
        """Apunta el resultado de la misión y hace avanzar (o hundirse) la campaña."""
        capitulo = self.capitulo_actual
        titulo = capitulo.titulo if capitulo else escenario.nombre
        registro = Registro()
        cambios = registro.cambios
        self.historial.append(RegistroMision(
            capitulo=self.capitulo, titulo=titulo, estado=resultado.estado, puntuacion=resultado.puntuacion,
            resumen=resumen, bajas_propias=resultado.bajas_propias, bajas_enemigas=resultado.bajas_enemigas,
            duracion_s=resultado.duracion_s, mision_id=mision_id, terreno=escenario.terreno,
        ))
        rng = random.Random(f"{semilla}|{self.id}|{self.capitulo}")

        # Personajes: quién estuvo, quién cayó y qué recuerdan.
        metricas = resultado.metricas.get("unidades", {})
        por_nombre = {
            u.persona.nombre: u.id
            for b in escenario.bandos.values() for u in b.unidades if u.persona is not None
        }
        for personaje in self.personajes:
            uid = por_nombre.get(personaje.persona.nombre)
            if not personaje.vivo or uid is None:
                continue
            personaje.misiones += 1
            datos = metricas.get(uid, {})
            if datos.get("destruida"):
                personaje.vivo = False
                registro.caidos.append(personaje.persona)
                cambios.append(f"{personaje.persona.presentacion().split('.')[0]} ha caído en «{titulo}».")
                continue
            perdidos = int(datos.get("efectivos_iniciales", 0)) - int(datos.get("efectivos_finales", 0))
            recuerdo = f"En «{titulo}» ({resultado.estado}, {reloj(resultado.duracion_s)})"
            recuerdo += f" perdiste a {perdidos} de los tuyos" if perdidos > 0 else " no perdiste a nadie"
            personaje.persona.recuerdos = [*personaje.persona.recuerdos, recuerdo][-MAX_RECUERDOS:]

        usados = {p.persona.nombre for p in self.personajes}
        while len(self.vivos()) < 2:
            nuevo = crear_persona(rng, self.perfil.id, "jugadores", usados)
            nuevo.historia = f"Se incorpora a la campaña después de «{titulo}». {nuevo.historia}"
            self.personajes.append(Personaje(persona=nuevo))
            cambios.append(f"Se incorpora {nuevo.presentacion().split('.')[0]}.")

        # Dificultad: Zeus quiere partidas tensas pero ganables.
        anterior = self.dificultad
        if resultado.estado == "victoria" and resultado.puntuacion >= 0.8:
            self.dificultad += 0.15
        elif resultado.estado == "derrota":
            self.dificultad -= 0.2
        elif resultado.estado == "parcial":
            self.dificultad -= 0.05
        self.dificultad = round(max(DIFICULTAD_MIN, min(DIFICULTAD_MAX, self.dificultad)), 2)
        if self.dificultad > anterior:
            cambios.append(f"Ha sido fácil: la próxima misión será más dura (dificultad {self.dificultad:.2f}).")
        elif self.dificultad < anterior:
            cambios.append(f"Ha sido duro: la próxima misión dará algo de respiro (dificultad {self.dificultad:.2f}).")

        # ¿Sigue siendo posible el objetivo final?
        ultima = self.capitulo >= self.misiones_total - 1
        if ultima:
            if resultado.estado == "victoria":
                self._terminar("cumplida", f"Se ganó la misión final «{titulo}».")
            else:
                self._terminar("fracasada", f"La misión final «{titulo}» terminó en {resultado.estado}.")
        elif resultado.estado == "derrota":
            if capitulo is not None and capitulo.critico:
                self._terminar("fracasada", f"Se perdió «{titulo}», un paso crítico: ya no hay forma de llegar.")
            else:
                self.margen -= 1
                if self.margen < 0:
                    self._terminar("fracasada", "Demasiadas derrotas: ya no quedan fuerzas ni tiempo para el objetivo.")
                else:
                    cambios.append(f"La derrota cuesta caro: queda margen para {self.margen} derrota(s) más.")
        if self.terminada:
            cambios.append(f"Fin de la campaña: {self.estado_texto}. {self.motivo_final}")
            self.capitulo += 1
            return registro

        # Imprevistos: el plan nunca sale como se espera.
        if rng.random() < PROB_IMPREVISTO:
            imprevisto = imprevisto_por_reglas(rng)
            registro.imprevisto = Imprevisto(texto=imprevisto[0], grave=imprevisto[1])
            siguiente = self.plan.capitulos[self.capitulo + 1]
            siguiente.complicacion = (f"{siguiente.complicacion} Además: {imprevisto[0]}".strip()
                                      if imprevisto[0] not in siguiente.complicacion else siguiente.complicacion)
            cambios.append(f"Imprevisto{' GRAVE' if imprevisto[1] else ''}: {imprevisto[0]}")
            if imprevisto[1]:
                # Un golpe de mala suerte gasta margen. Sin margen, si además la misión no salió
                # bien, la campaña se hunde; si salió bien, el siguiente paso pasa a ser a vida o muerte.
                if self.margen > 0:
                    self.margen -= 1
                    cambios.append(f"Se pierde margen de error: quedan {self.margen} derrota(s) que encajar.")
                elif resultado.estado != "victoria":
                    self._terminar("fracasada", f"Un error y un imprevisto lo han echado todo a perder: "
                                                f"{imprevisto[0]}")
                    cambios.append(f"Fin de la campaña: {self.estado_texto}. {self.motivo_final}")
                elif not siguiente.critico:
                    siguiente.critico = True
                    cambios.append(f"Ya no queda margen: «{siguiente.titulo}» pasa a ser crítica.")
        self.capitulo += 1
        return registro

    def _terminar(self, resultado: Literal["cumplida", "fracasada"], motivo: str) -> None:
        self.terminada = True
        self.resultado_final = resultado
        self.motivo_final = motivo
