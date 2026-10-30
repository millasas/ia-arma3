"""Perfiles de campaña: la "temática" con la que piensa y habla la IA.

Cada perfil es un YAML con la época, los bandos, la doctrina táctica, las restricciones
técnicas (sin GPS en 1944, sin aviación en una guerrilla...), el estilo de radio y los
indicativos. Se pueden crear perfiles nuevos copiando uno de ``campanas/perfiles``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..modelo import TipoUnidad

DIRECTORIO_PERFILES = Path(__file__).parent / "perfiles"


class InfoBando(BaseModel):
    nombre: str
    descripcion: str = ""
    # Solo se usan cuando la IA juega también este bando (ver PerfilCampana.del_enemigo).
    doctrina: list[str] = Field(default_factory=list)
    indicativo_mando: str = "Mando"
    indicativos: dict[str, list[str]] = Field(default_factory=dict)


class PerfilCampana(BaseModel):
    id: str
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

    def del_enemigo(self) -> PerfilCampana:
        """El mismo perfil visto desde el otro bando, para que la IA juegue también al enemigo."""
        return self.model_copy(
            update={
                "id": self.id,
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

    def indicativo_para(self, tipo: TipoUnidad, indice: int) -> str | None:
        """Indicativo temático para la unidad nº `indice` de ese tipo (None si no hay)."""
        lista = self.indicativos.get(tipo.value, [])
        return lista[indice] if indice < len(lista) else None

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


def cargar_perfil_fichero(ruta: str | Path) -> PerfilCampana:
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}
    return PerfilCampana.model_validate(datos)


def perfiles_disponibles(directorio_extra: str | Path | None = None) -> dict[str, PerfilCampana]:
    """Perfiles incluidos en el paquete más los de `directorio_extra` (que tienen prioridad)."""
    perfiles: dict[str, PerfilCampana] = {}
    directorios = [DIRECTORIO_PERFILES]
    if directorio_extra and Path(directorio_extra).is_dir():
        directorios.append(Path(directorio_extra))
    for directorio in directorios:
        for ruta in sorted(directorio.glob("*.yaml")):
            perfil = cargar_perfil_fichero(ruta)
            perfiles[perfil.id] = perfil
    return perfiles


def cargar_perfil(id_o_ruta: str, directorio_extra: str | Path | None = None) -> PerfilCampana:
    """Carga un perfil por id ("ww2") o por ruta a un fichero YAML."""
    ruta = Path(id_o_ruta)
    if ruta.suffix in (".yaml", ".yml") and ruta.is_file():
        return cargar_perfil_fichero(ruta)
    perfiles = perfiles_disponibles(directorio_extra)
    if id_o_ruta not in perfiles:
        disponibles = ", ".join(sorted(perfiles))
        raise KeyError(f"No existe el perfil '{id_o_ruta}'. Disponibles: {disponibles}")
    return perfiles[id_o_ruta]
