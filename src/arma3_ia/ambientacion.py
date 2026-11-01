"""Ambientación de una campaña: la "temática" con la que piensan y hablan Zeus y los NPC.

No hay ambientaciones fijas: Zeus inventa una para cada campaña a partir de lo que pide el
anfitrión (época, bandos, doctrina, restricciones técnicas, estilo de radio, indicativos...)
y se guarda dentro de la campaña. El campo ``id`` es la **era** (segunda guerra mundial,
guerra fría, moderna o futura): sirve para que lo aprendido en una campaña se aproveche en
otras de la misma era.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .texto import normalizar, recortar

ERAS = ("segunda_guerra_mundial", "guerra_fria", "moderna", "futura")
_CLAVES_ERA = (
    (("194", "193", "segunda", "ww2", "wwii", "nazi", "wehrmacht"), "segunda_guerra_mundial"),
    (("fria", "196", "197", "198", "vietnam", "sovietic", "urss"), "guerra_fria"),
    (("futur", "ciencia_ficcion", "205", "206", "210"), "futura"),
)


def normalizar_era(valor: Any) -> str:
    """'Segunda Guerra Mundial', 'WW2' o '1944' -> 'segunda_guerra_mundial'."""
    norm = normalizar(str(valor or "")).replace(" ", "_")
    if norm in ERAS:
        return norm
    for claves, era in _CLAVES_ERA:
        if any(c in norm for c in claves):
            return era
    return "moderna"


class InfoBando(BaseModel):
    nombre: str
    descripcion: str = ""
    # Solo se usan cuando la IA juega este bando como enemigo (ver PerfilCampana.del_enemigo).
    doctrina: list[str] = Field(default_factory=list)
    indicativo_mando: str = "Mando"
    indicativos: dict[str, list[str]] = Field(default_factory=dict)


class PerfilCampana(BaseModel):
    id: str = Field(default="moderna", description="Era: " + ", ".join(ERAS))
    nombre: str
    epoca: str
    descripcion: str
    bando_ia: InfoBando
    enemigo: InfoBando
    estilo_comunicacion: str
    doctrina: list[str] = Field(default_factory=list)
    restricciones: list[str] = Field(default_factory=list)
    reglas_enfrentamiento: list[str] = Field(default_factory=list)
    personalidad_mando: str = ""
    medios_aereos: bool = True
    # Segundos que tardan los mensajes de radio en llegar (comunicaciones de la época).
    retardo_radio_s: float = 0.0
    indicativo_mando: str = "Mando"
    indicativo_enlace: str = "Radio"
    indicativos: dict[str, list[str]] = Field(default_factory=dict)
    vocabulario: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _era(cls, valor: Any) -> str:
        return normalizar_era(valor)

    @field_validator("descripcion", "estilo_comunicacion", "personalidad_mando", mode="before")
    @classmethod
    def _recortar(cls, valor: Any) -> Any:
        return recortar(valor, 500) if isinstance(valor, str) else valor

    @field_validator("retardo_radio_s", mode="before")
    @classmethod
    def _retardo(cls, valor: Any) -> float:
        try:
            return max(0.0, min(120.0, float(valor)))
        except (TypeError, ValueError):
            return 0.0

    def del_enemigo(self) -> PerfilCampana:
        """La misma ambientación vista desde el otro bando, para que la IA juegue también al enemigo."""
        return self.model_copy(
            update={
                "bando_ia": InfoBando(nombre=self.enemigo.nombre, descripcion=self.enemigo.descripcion),
                "enemigo": InfoBando(nombre=self.bando_ia.nombre, descripcion=self.bando_ia.descripcion),
                "doctrina": list(self.enemigo.doctrina),
                "reglas_enfrentamiento": [],
                "personalidad_mando": "",
                "indicativo_mando": self.enemigo.indicativo_mando,
                "indicativo_enlace": f"Radio {self.enemigo.nombre}",
                "indicativos": dict(self.enemigo.indicativos),
            }
        )

    def bloque_contexto(self, rol: str) -> str:
        """Texto de ambientación que se añade a las instrucciones de cada agente."""
        lineas = [
            f"CAMPAÑA: {self.nombre} ({self.epoca}). {self.descripcion.strip()}",
            f"Tu bando: {self.bando_ia.nombre}. {self.bando_ia.descripcion}".strip(),
            f"Enemigo: {self.enemigo.nombre}. {self.enemigo.descripcion}".strip(),
            f"Estilo de comunicación: {self.estilo_comunicacion.strip()}",
        ]
        if self.vocabulario:
            lineas.append("Expresiones típicas: " + ", ".join(self.vocabulario) + ".")
        if self.doctrina and rol != "enlace":
            lineas.append("Doctrina:")
            lineas += [f"- {d}" for d in self.doctrina]
        if self.restricciones:
            lineas.append("Restricciones: " + " ".join(self.restricciones))
        if self.reglas_enfrentamiento and rol != "enlace":
            lineas.append("Reglas de enfrentamiento: " + " ".join(self.reglas_enfrentamiento))
        if self.personalidad_mando and rol == "comandante":
            lineas.append(f"Tu carácter: {self.personalidad_mando}")
        return "\n".join(lineas)
