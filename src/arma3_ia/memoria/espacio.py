"""Vigilante del espacio: comprueba, cada vez que se guarda algo, que la base de datos no llena el disco.

Mide el tamaño de la base de datos (con sus ficheros auxiliares), el de la carpeta de
campañas y el espacio libre del disco. Hay tres niveles:

- ``ok``: todo bien.
- ``aviso``: la base de datos pasa del 80 % de su límite o queda poco disco libre.
- ``critico``: se ha llegado al límite o casi no queda disco. La memoria entra en modo ahorro
  (deja de guardar los textos largos de cada decisión) y se recomienda borrar campañas
  terminadas.

Cada comprobación es muy barata (dos ``stat`` y un ``statvfs``), así que se hace en cada cambio;
solo se avisa cuando el nivel cambia.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Nivel = Literal["ok", "aviso", "critico"]
MB = 1024 * 1024
UMBRAL_AVISO = 0.8


@dataclass(frozen=True)
class EstadoEspacio:
    nivel: Nivel
    bbdd_mb: float
    carpetas_mb: float
    limite_mb: float
    libre_mb: float
    minimo_libre_mb: float

    def mensaje(self) -> str:
        texto = (f"Base de datos {self.bbdd_mb:.1f} MB de {self.limite_mb:.0f} MB "
                 f"({self.bbdd_mb / self.limite_mb:.0%}), campañas {self.carpetas_mb:.2f} MB, "
                 f"disco libre {self.libre_mb / 1024:.1f} GB")
        if self.nivel == "critico":
            return ("ESPACIO CRÍTICO: " + texto + ". La IA deja de guardar los textos largos de las decisiones. "
                    "Borra campañas terminadas con: arma3-ia campana borrar <id>")
        if self.nivel == "aviso":
            return "Aviso de espacio: " + texto + ". Conviene borrar alguna campaña terminada."
        return "Espacio correcto: " + texto + "."


def tamano_fichero_mb(ruta: str | Path) -> float:
    """Tamaño de una base SQLite contando sus ficheros -wal y -shm."""
    total = 0
    for sufijo in ("", "-wal", "-shm", "-journal"):
        try:
            total += os.stat(f"{ruta}{sufijo}").st_size
        except OSError:
            continue
    return total / MB


def tamano_carpeta_mb(ruta: str | Path) -> float:
    total = 0
    for raiz, _, ficheros in os.walk(ruta):
        for nombre in ficheros:
            try:
                total += os.stat(os.path.join(raiz, nombre)).st_size
            except OSError:
                continue
    return total / MB


class VigilanteEspacio:
    def __init__(
        self,
        ruta_bbdd: str | Path,
        *,
        carpeta_campanas: str | Path | None = None,
        limite_mb: float = 2048.0,
        minimo_libre_mb: float = 2048.0,
        al_cambiar: Callable[[EstadoEspacio], None] | None = None,
    ) -> None:
        self.ruta_bbdd = Path(ruta_bbdd)
        self.carpeta_campanas = Path(carpeta_campanas) if carpeta_campanas else None
        self.limite_mb = max(1.0, limite_mb)
        self.minimo_libre_mb = max(0.0, minimo_libre_mb)
        self.al_cambiar = al_cambiar
        self.comprobaciones = 0
        self._carpetas_mb: float | None = None
        self.ultimo: EstadoEspacio | None = None

    def carpetas_cambiadas(self) -> None:
        """Avisa de que se han escrito ficheros en la carpeta de campañas (se vuelve a medir)."""
        self._carpetas_mb = None
        self.comprobar()

    def _libre_mb(self) -> float:
        carpeta = self.ruta_bbdd.parent if self.ruta_bbdd.parent.exists() else Path(".")
        return shutil.disk_usage(carpeta).free / MB

    def comprobar(self) -> EstadoEspacio:
        self.comprobaciones += 1
        if self._carpetas_mb is None:
            existe = self.carpeta_campanas is not None and self.carpeta_campanas.is_dir()
            self._carpetas_mb = tamano_carpeta_mb(self.carpeta_campanas) if existe else 0.0  # type: ignore[arg-type]
        bbdd = tamano_fichero_mb(self.ruta_bbdd)
        libre = self._libre_mb()
        ocupado = bbdd + self._carpetas_mb
        nivel: Nivel = "ok"
        if ocupado >= self.limite_mb or libre < self.minimo_libre_mb:
            nivel = "critico"
        elif ocupado >= self.limite_mb * UMBRAL_AVISO or libre < self.minimo_libre_mb * 2:
            nivel = "aviso"
        estado = EstadoEspacio(nivel, round(bbdd, 2), round(self._carpetas_mb, 2), self.limite_mb, round(libre, 1),
                               self.minimo_libre_mb)
        anterior, self.ultimo = self.ultimo, estado
        # Solo se avisa cuando cambia el nivel (y no al arrancar si todo está bien).
        cambia = nivel != "ok" if anterior is None else anterior.nivel != nivel
        if self.al_cambiar is not None and cambia:
            self.al_cambiar(estado)
        return estado

    @property
    def critico(self) -> bool:
        return self.ultimo is not None and self.ultimo.nivel == "critico"
