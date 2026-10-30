"""Memoria persistente en SQLite: misiones, eventos, decisiones, chat, lecciones y habilidades.

Un único fichero (por defecto ``datos/memoria.db``) guarda todo lo que la IA vive.
Las decisiones con su contexto completo quedan registradas y se pueden exportar como
conjunto de datos para un futuro ajuste fino (LoRA) del modelo.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..modelo import Evento, MensajeChat, ResultadoMision

VERSION_ESQUEMA = 1

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS misiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campana TEXT NOT NULL,
    escenario TEXT NOT NULL,
    bando TEXT,
    inicio TEXT NOT NULL,
    fin TEXT,
    estado TEXT,
    puntuacion REAL,
    duracion_s REAL,
    resumen TEXT,
    resultado TEXT
);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mision_id INTEGER NOT NULL REFERENCES misiones(id) ON DELETE CASCADE,
    tiempo REAL NOT NULL,
    tipo TEXT NOT NULL,
    unidad TEXT,
    descripcion TEXT NOT NULL,
    datos TEXT
);
CREATE INDEX IF NOT EXISTS idx_eventos_mision ON eventos(mision_id, tiempo);
CREATE TABLE IF NOT EXISTS decisiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mision_id INTEGER REFERENCES misiones(id) ON DELETE CASCADE,
    tiempo REAL,
    agente TEXT NOT NULL,
    rol TEXT NOT NULL,
    sistema TEXT NOT NULL,
    usuario TEXT NOT NULL,
    respuesta TEXT NOT NULL,
    origen TEXT NOT NULL,
    lecciones TEXT,
    duracion_s REAL
);
CREATE INDEX IF NOT EXISTS idx_decisiones_mision ON decisiones(mision_id, tiempo);
CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mision_id INTEGER REFERENCES misiones(id) ON DELETE CASCADE,
    tiempo REAL,
    autor TEXT NOT NULL,
    texto TEXT NOT NULL,
    es_ia INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lecciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campana TEXT,
    rol TEXT NOT NULL,
    situacion TEXT NOT NULL,
    leccion TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'error',
    etiquetas TEXT NOT NULL DEFAULT '[]',
    peso REAL NOT NULL DEFAULT 1.0,
    veces_usada INTEGER NOT NULL DEFAULT 0,
    exitos INTEGER NOT NULL DEFAULT 0,
    confirmaciones INTEGER NOT NULL DEFAULT 1,
    mision_origen INTEGER,
    activa INTEGER NOT NULL DEFAULT 1,
    creada TEXT NOT NULL,
    actualizada TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lecciones_rol ON lecciones(rol, campana, activa);
CREATE TABLE IF NOT EXISTS habilidades (
    nombre TEXT PRIMARY KEY,
    estado TEXT NOT NULL,
    actualizada TEXT NOT NULL
);
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Leccion:
    """Algo que la IA ha aprendido. `campana=None` significa que vale para cualquier campaña."""

    rol: str
    situacion: str
    leccion: str
    tipo: str = "error"
    etiquetas: list[str] = field(default_factory=list)
    campana: str | None = None
    peso: float = 1.0
    veces_usada: int = 0
    exitos: int = 0
    confirmaciones: int = 1
    mision_origen: int | None = None
    activa: bool = True
    id: int | None = None

    @property
    def utilidad(self) -> float:
        """Probabilidad estimada de que aplicar esta lección acabe bien (suavizado de Laplace)."""
        return (self.exitos + 1) / (self.veces_usada + 2)

    def texto(self) -> str:
        return f"{self.situacion.rstrip('.')}: {self.leccion}"


def _leccion_de_fila(fila: sqlite3.Row) -> Leccion:
    return Leccion(
        id=fila["id"],
        campana=fila["campana"],
        rol=fila["rol"],
        situacion=fila["situacion"],
        leccion=fila["leccion"],
        tipo=fila["tipo"],
        etiquetas=json.loads(fila["etiquetas"] or "[]"),
        peso=fila["peso"],
        veces_usada=fila["veces_usada"],
        exitos=fila["exitos"],
        confirmaciones=fila["confirmaciones"],
        mision_origen=fila["mision_origen"],
        activa=bool(fila["activa"]),
    )


class Memoria:
    """Acceso a la base de datos. Es segura para usar desde un único bucle asyncio."""

    def __init__(self, ruta: str | Path = "datos/memoria.db") -> None:
        self.ruta = str(ruta)
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.ruta, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._cerrojo = threading.RLock()
        with self._transaccion() as con:
            con.execute("PRAGMA foreign_keys = ON")
            if self.ruta != ":memory:":
                con.execute("PRAGMA journal_mode = WAL")
            con.executescript(_ESQUEMA)
            con.execute(
                "INSERT OR IGNORE INTO meta (clave, valor) VALUES ('version_esquema', ?)",
                (str(VERSION_ESQUEMA),),
            )

    @contextmanager
    def _transaccion(self) -> Iterator[sqlite3.Connection]:
        with self._cerrojo:
            try:
                yield self._con
                self._con.commit()
            except Exception:
                self._con.rollback()
                raise

    def _consulta(self, sql: str, parametros: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._cerrojo:
            return self._con.execute(sql, tuple(parametros)).fetchall()

    def cerrar(self) -> None:
        with self._cerrojo:
            self._con.close()

    # ------------------------------------------------------------------ misiones
    def iniciar_mision(self, campana: str, escenario: str, bando: str | None = None) -> int:
        with self._transaccion() as con:
            cur = con.execute(
                "INSERT INTO misiones (campana, escenario, bando, inicio) VALUES (?, ?, ?, ?)",
                (campana, escenario, bando, _ahora()),
            )
            return int(cur.lastrowid or 0)

    def cerrar_mision(self, mision_id: int, resultado: ResultadoMision, resumen: str = "") -> None:
        with self._transaccion() as con:
            con.execute(
                "UPDATE misiones SET fin = ?, estado = ?, puntuacion = ?, duracion_s = ?, "
                "resumen = ?, resultado = ? WHERE id = ?",
                (
                    _ahora(),
                    resultado.estado,
                    resultado.puntuacion,
                    resultado.duracion_s,
                    resumen,
                    resultado.model_dump_json(),
                    mision_id,
                ),
            )

    def actualizar_resumen(self, mision_id: int, resumen: str) -> None:
        with self._transaccion() as con:
            con.execute("UPDATE misiones SET resumen = ? WHERE id = ?", (resumen, mision_id))

    def mision(self, mision_id: int) -> dict[str, Any] | None:
        filas = self._consulta("SELECT * FROM misiones WHERE id = ?", (mision_id,))
        return dict(filas[0]) if filas else None

    def misiones(self, campana: str | None = None, limite: int = 20) -> list[dict[str, Any]]:
        if campana:
            filas = self._consulta(
                "SELECT * FROM misiones WHERE campana = ? ORDER BY id DESC LIMIT ?", (campana, limite)
            )
        else:
            filas = self._consulta("SELECT * FROM misiones ORDER BY id DESC LIMIT ?", (limite,))
        return [dict(f) for f in filas]

    # ------------------------------------------------------------------ eventos y decisiones
    def registrar_eventos(self, mision_id: int, eventos: Iterable[Evento]) -> None:
        filas = [
            (mision_id, e.tiempo, e.tipo, e.unidad, e.descripcion, json.dumps(e.datos, ensure_ascii=False, default=str))
            for e in eventos
        ]
        if not filas:
            return
        with self._transaccion() as con:
            con.executemany(
                "INSERT INTO eventos (mision_id, tiempo, tipo, unidad, descripcion, datos) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                filas,
            )

    def eventos(self, mision_id: int, tipos: Iterable[str] | None = None) -> list[dict[str, Any]]:
        filas = self._consulta(
            "SELECT * FROM eventos WHERE mision_id = ? ORDER BY tiempo, id", (mision_id,)
        )
        tipos_set = set(tipos) if tipos is not None else None
        resultado = []
        for f in filas:
            if tipos_set is not None and f["tipo"] not in tipos_set:
                continue
            d = dict(f)
            d["datos"] = json.loads(d["datos"] or "{}")
            resultado.append(d)
        return resultado

    def registrar_decision(
        self,
        *,
        mision_id: int | None,
        tiempo: float,
        agente: str,
        rol: str,
        sistema: str,
        usuario: str,
        respuesta: str,
        origen: str,
        lecciones: Iterable[int] = (),
        duracion_s: float = 0.0,
    ) -> int:
        with self._transaccion() as con:
            cur = con.execute(
                "INSERT INTO decisiones (mision_id, tiempo, agente, rol, sistema, usuario, respuesta, "
                "origen, lecciones, duracion_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mision_id,
                    tiempo,
                    agente,
                    rol,
                    sistema,
                    usuario,
                    respuesta,
                    origen,
                    json.dumps(sorted(lecciones)),
                    duracion_s,
                ),
            )
            return int(cur.lastrowid or 0)

    def decisiones(
        self, mision_id: int | None = None, rol: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisiones WHERE 1=1"
        parametros: list[Any] = []
        if mision_id is not None:
            sql += " AND mision_id = ?"
            parametros.append(mision_id)
        if rol is not None:
            sql += " AND rol = ?"
            parametros.append(rol)
        filas = self._consulta(sql + " ORDER BY tiempo, id", parametros)
        resultado = []
        for f in filas:
            d = dict(f)
            d["lecciones"] = json.loads(d["lecciones"] or "[]")
            resultado.append(d)
        return resultado

    def registrar_chat(self, mision_id: int | None, mensajes: Iterable[MensajeChat]) -> None:
        filas = [(mision_id, m.tiempo, m.autor, m.texto, int(m.es_ia)) for m in mensajes]
        if not filas:
            return
        with self._transaccion() as con:
            con.executemany(
                "INSERT INTO conversaciones (mision_id, tiempo, autor, texto, es_ia) VALUES (?, ?, ?, ?, ?)",
                filas,
            )

    def conversaciones(self, mision_id: int) -> list[dict[str, Any]]:
        return [
            dict(f)
            for f in self._consulta(
                "SELECT * FROM conversaciones WHERE mision_id = ? ORDER BY tiempo, id", (mision_id,)
            )
        ]

    # ------------------------------------------------------------------ lecciones
    def anadir_leccion(self, leccion: Leccion) -> int:
        ahora = _ahora()
        with self._transaccion() as con:
            cur = con.execute(
                "INSERT INTO lecciones (campana, rol, situacion, leccion, tipo, etiquetas, peso, "
                "veces_usada, exitos, confirmaciones, mision_origen, activa, creada, actualizada) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    leccion.campana,
                    leccion.rol,
                    leccion.situacion,
                    leccion.leccion,
                    leccion.tipo,
                    json.dumps(leccion.etiquetas, ensure_ascii=False),
                    leccion.peso,
                    leccion.veces_usada,
                    leccion.exitos,
                    leccion.confirmaciones,
                    leccion.mision_origen,
                    int(leccion.activa),
                    ahora,
                    ahora,
                ),
            )
            leccion.id = int(cur.lastrowid or 0)
            return leccion.id

    def leccion(self, leccion_id: int) -> Leccion | None:
        filas = self._consulta("SELECT * FROM lecciones WHERE id = ?", (leccion_id,))
        return _leccion_de_fila(filas[0]) if filas else None

    def lecciones(
        self,
        rol: str | None = None,
        campana: str | None = None,
        *,
        incluir_generales: bool = True,
        solo_activas: bool = True,
    ) -> list[Leccion]:
        """Lecciones de un rol (más las del rol "todos") y de una campaña (más las generales)."""
        sql = "SELECT * FROM lecciones WHERE 1=1"
        parametros: list[Any] = []
        if rol is not None:
            sql += " AND rol IN (?, 'todos')"
            parametros.append(rol)
        if campana is not None:
            sql += " AND (campana = ?" + (" OR campana IS NULL)" if incluir_generales else ")")
            parametros.append(campana)
        if solo_activas:
            sql += " AND activa = 1"
        return [_leccion_de_fila(f) for f in self._consulta(sql + " ORDER BY id", parametros)]

    def reforzar_leccion(self, leccion_id: int, incremento: float = 0.25, peso_maximo: float = 3.0) -> None:
        """La misma lección se ha vuelto a aprender: gana peso."""
        with self._transaccion() as con:
            con.execute(
                "UPDATE lecciones SET confirmaciones = confirmaciones + 1, "
                "peso = MIN(peso + ?, ?), activa = 1, actualizada = ? WHERE id = ?",
                (incremento, peso_maximo, _ahora(), leccion_id),
            )

    def registrar_uso_lecciones(self, ids: Iterable[int], exito: bool) -> None:
        """Las lecciones usadas en una misión suman un uso y, si salió bien, un éxito."""
        filas = [(int(exito), _ahora(), i) for i in set(ids)]
        if not filas:
            return
        with self._transaccion() as con:
            con.executemany(
                "UPDATE lecciones SET veces_usada = veces_usada + 1, exitos = exitos + ?, "
                "actualizada = ? WHERE id = ?",
                filas,
            )

    def archivar_leccion(self, leccion_id: int) -> None:
        with self._transaccion() as con:
            con.execute(
                "UPDATE lecciones SET activa = 0, actualizada = ? WHERE id = ?", (_ahora(), leccion_id)
            )

    def revisar_lecciones(self, min_usos: int = 5, utilidad_minima: float = 0.25) -> list[int]:
        """Archiva ("olvida") las lecciones que se han usado bastante y casi nunca funcionan."""
        archivadas = [
            leccion.id
            for leccion in self.lecciones()
            if leccion.id is not None
            and leccion.veces_usada >= min_usos
            and leccion.utilidad < utilidad_minima
        ]
        for leccion_id in archivadas:
            if leccion_id is not None:
                self.archivar_leccion(leccion_id)
        return [i for i in archivadas if i is not None]

    # ------------------------------------------------------------------ habilidades
    def cargar_habilidad(self, nombre: str) -> dict[str, Any] | None:
        filas = self._consulta("SELECT estado FROM habilidades WHERE nombre = ?", (nombre,))
        return json.loads(filas[0]["estado"]) if filas else None

    def guardar_habilidad(self, nombre: str, estado: dict[str, Any]) -> None:
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO habilidades (nombre, estado, actualizada) VALUES (?, ?, ?) "
                "ON CONFLICT(nombre) DO UPDATE SET estado = excluded.estado, "
                "actualizada = excluded.actualizada",
                (nombre, json.dumps(estado), _ahora()),
            )

    # ------------------------------------------------------------------ informes
    def estadisticas(self) -> dict[str, Any]:
        def contar(sql: str) -> int:
            return int(self._consulta(sql)[0][0] or 0)

        return {
            "misiones": contar("SELECT COUNT(*) FROM misiones WHERE fin IS NOT NULL"),
            "victorias": contar("SELECT COUNT(*) FROM misiones WHERE estado = 'victoria'"),
            "lecciones_activas": contar("SELECT COUNT(*) FROM lecciones WHERE activa = 1"),
            "lecciones_archivadas": contar("SELECT COUNT(*) FROM lecciones WHERE activa = 0"),
            "decisiones_llm": contar("SELECT COUNT(*) FROM decisiones WHERE origen = 'llm'"),
            "decisiones_reglas": contar("SELECT COUNT(*) FROM decisiones WHERE origen = 'reglas'"),
            "mensajes_chat": contar("SELECT COUNT(*) FROM conversaciones"),
        }

    def exportar_dataset(
        self, ruta: str | Path, puntuacion_minima: float = 0.6, solo_llm: bool = True
    ) -> int:
        """Exporta en JSONL (formato "messages") las decisiones de las misiones que salieron bien.

        Es el material para entrenar más adelante un adaptador LoRA del modelo con la
        experiencia acumulada. Devuelve el número de ejemplos escritos.
        """
        sql = (
            "SELECT d.*, m.campana, m.puntuacion FROM decisiones d JOIN misiones m ON m.id = d.mision_id "
            "WHERE m.puntuacion >= ?"
        )
        if solo_llm:
            sql += " AND d.origen = 'llm'"
        filas = self._consulta(sql + " ORDER BY d.id", (puntuacion_minima,))
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            for fila in filas:
                ejemplo = {
                    "messages": [
                        {"role": "system", "content": fila["sistema"]},
                        {"role": "user", "content": fila["usuario"]},
                        {"role": "assistant", "content": fila["respuesta"]},
                    ],
                    "metadata": {
                        "rol": fila["rol"],
                        "campana": fila["campana"],
                        "mision": fila["mision_id"],
                        "puntuacion": fila["puntuacion"],
                    },
                }
                f.write(json.dumps(ejemplo, ensure_ascii=False) + "\n")
        return len(filas)
