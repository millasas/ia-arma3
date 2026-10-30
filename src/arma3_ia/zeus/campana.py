"""Estado de una campaña dirigida por Zeus: capítulos, personajes, historial y dificultad.

Se guarda en la memoria (tabla ``campanas``) después de cada misión, así que la campaña
continúa donde se dejó aunque se apague el PC. Los personajes recuerdan lo que han vivido y
pueden morir; Zeus ajusta la dificultad según cómo les va a los jugadores.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from ..modelo import Persona, ResultadoMision
from ..simulador.escenario import Escenario
from ..texto import normalizar, reloj
from .esquemas import CapituloPlan, PlanCampana, TipoMision
from .reglas import crear_persona

DIFICULTAD_MIN, DIFICULTAD_MAX = 0.6, 1.8
MAX_RECUERDOS = 8


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


class EstadoCampana(BaseModel):
    id: str
    plan: PlanCampana
    capitulo: int = 0
    dificultad: float = 1.0
    personajes: list[Personaje] = Field(default_factory=list)
    antagonista: Persona | None = None
    historial: list[RegistroMision] = Field(default_factory=list)
    terminada: bool = False

    @classmethod
    def nueva(cls, plan: PlanCampana) -> EstadoCampana:
        return cls(
            id=normalizar(plan.nombre).replace(" ", "_")[:40] or "campana",
            plan=plan,
            personajes=[Personaje(persona=p) for p in plan.personajes],
            antagonista=plan.antagonista,
        )

    @property
    def capitulo_actual(self) -> CapituloPlan | None:
        if self.terminada or self.capitulo >= len(self.plan.capitulos):
            return None
        return self.plan.capitulos[self.capitulo]

    def vivos(self) -> list[Persona]:
        return [p.persona for p in self.personajes if p.vivo]

    def resumen(self) -> str:
        """Lo que Zeus necesita saber para preparar la siguiente misión."""
        lineas = [f"CAMPAÑA: {self.plan.nombre}. {self.plan.premisa}",
                  f"Jugadores: {self.plan.nombre_jugadores}. Enemigo: {self.plan.nombre_enemigo}."]
        if self.antagonista:
            lineas.append(f"Antagonista: {self.antagonista.presentacion()}")
        lineas.append("CAPÍTULOS:")
        for i, c in enumerate(self.plan.capitulos):
            marca = "✓" if i < self.capitulo else ("→" if i == self.capitulo else " ")
            lineas.append(f" {marca} {i + 1}. {c.titulo} ({c.tipo.value}): {c.sinopsis}")
        if self.historial:
            lineas.append("LO QUE HA PASADO:")
            lineas += [f"- {r.titulo}: {r.estado} (puntuación {r.puntuacion:.2f}, bajas {r.bajas_propias}). "
                       f"{r.resumen}" for r in self.historial[-5:]]
        if self.personajes:
            lineas.append("PERSONAJES ALIADOS:")
            lineas += [f"- {p.persona.presentacion()}" + ("" if p.vivo else " [MUERTO]") for p in self.personajes]
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
    ) -> list[str]:
        """Apunta el resultado de la misión y hace avanzar la historia. Devuelve lo que ha cambiado."""
        capitulo = self.capitulo_actual
        titulo = capitulo.titulo if capitulo else escenario.nombre
        cambios: list[str] = []
        self.historial.append(RegistroMision(
            capitulo=self.capitulo, titulo=titulo, estado=resultado.estado, puntuacion=resultado.puntuacion,
            resumen=resumen, bajas_propias=resultado.bajas_propias, bajas_enemigas=resultado.bajas_enemigas,
            duracion_s=resultado.duracion_s, mision_id=mision_id,
        ))

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
                cambios.append(f"{personaje.persona.presentacion().split('.')[0]} ha caído en «{titulo}».")
                continue
            perdidos = int(datos.get("efectivos_iniciales", 0)) - int(datos.get("efectivos_finales", 0))
            recuerdo = f"En «{titulo}» ({resultado.estado}, {reloj(resultado.duracion_s)})"
            recuerdo += f" perdiste a {perdidos} de los tuyos" if perdidos > 0 else " no perdiste a nadie"
            personaje.persona.recuerdos = [*personaje.persona.recuerdos, recuerdo][-MAX_RECUERDOS:]

        rng = random.Random(semilla if semilla is not None else len(self.historial))
        usados = {p.persona.nombre for p in self.personajes}
        while len(self.vivos()) < 2:
            nuevo = crear_persona(rng, self.plan.perfil, "jugadores", usados)
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

        # La historia sigue: una derrota abre un capítulo nuevo en vez de repetir el mismo.
        if resultado.estado == "derrota" and capitulo is not None:
            insertado = CapituloPlan(
                titulo=f"Contraataque tras «{titulo}»",
                sinopsis=f"Después del fracaso en «{titulo}» el enemigo contraataca. Hay que reagruparse y aguantar.",
                tipo=TipoMision.DEFENSA,
            )
            self.plan.capitulos.insert(self.capitulo + 1, insertado)
            cambios.append(f"La derrota cambia la historia: nuevo capítulo «{insertado.titulo}».")
        self.capitulo += 1
        if self.capitulo >= len(self.plan.capitulos):
            self.terminada = True
            cambios.append("Fin de la campaña.")
        return cambios
