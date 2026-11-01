"""Base de datos de la IA (SQLite, en el mismo PC): campañas, misiones, sucesos, lore y aprendizaje.

Un único fichero (por defecto ``BBDD/ia-arma3.db``) guarda:

- **campanas**: cada campaña con su estado completo (pasos, personajes, margen, resultado).
- **misiones**: cada misión jugada, de qué campaña y paso es, en qué terreno y cómo acabó.
- **eventos**: todo lo que pasó en cada misión (contactos, bajas, explosiones, radio...).
- **decisiones** y **conversaciones**: lo que decidió y dijo cada NPC (sirve para entrenar un LoRA).
- **lore**: la historia que va escribiendo Zeus (crónicas, imprevistos, caídos, final).
- **lecciones** y **habilidades**: lo aprendido, que se conserva aunque se borren campañas.

Borrar una campaña borra en cascada sus misiones, eventos, decisiones, conversaciones y lore.
Cada cambio pasa por el vigilante de espacio (``espacio.py``).
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
from .espacio import VigilanteEspacio

VERSION_ESQUEMA = 2

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campanas (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    encargo TEXT NOT NULL DEFAULT '',
    objetivo TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'en_curso',
    terreno TEXT NOT NULL DEFAULT '',
    datos TEXT NOT NULL,
    creada TEXT NOT NULL,
    actualizada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS misiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campana_id TEXT REFERENCES campanas(id) ON DELETE CASCADE,
    capitulo INTEGER,
    titulo TEXT NOT NULL DEFAULT '',
    escenario TEXT NOT NULL,
    terreno TEXT NOT NULL DEFAULT '',
    epoca TEXT,
    bando TEXT,
    inicio TEXT NOT NULL,
    fin TEXT,
    estado TEXT,
    puntuacion REAL,
    duracion_s REAL,
    resumen TEXT,
    resultado TEXT
);
CREATE INDEX IF NOT EXISTS idx_misiones_campana ON misiones(campana_id, capitulo);
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
CREATE INDEX IF NOT EXISTS idx_conversaciones_mision ON conversaciones(mision_id);
CREATE TABLE IF NOT EXISTS lore (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campana_id TEXT NOT NULL REFERENCES campanas(id) ON DELETE CASCADE,
    mision_id INTEGER,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    texto TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lore_campana ON lore(campana_id, id);
CREATE TABLE IF NOT EXISTS lecciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoca TEXT,
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
CREATE INDEX IF NOT EXISTS idx_lecciones_rol ON lecciones(rol, epoca, activa);
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
    """Algo que la IA ha aprendido. `epoca` es la era en la que vale; None = vale para cualquiera."""

    rol: str
    situacion: str
    leccion: str
    tipo: str = "error"
    etiquetas: list[str] = field(default_factory=list)
    epoca: str | None = None
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
        epoca=fila["epoca"],
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


class ErrorBaseDatos(RuntimeError):
    """La base de datos es de una versión antigua o no se puede abrir."""


class Memoria:
    """Acceso a la base de datos. Es segura para usar desde un único bucle asyncio.

    Con un `vigilante`, cada cambio comprueba el espacio; si el espacio es crítico, la memoria
    entra en modo ahorro: guarda las decisiones sin sus textos largos y no guarda la radio.
    """

    def __init__(self, ruta: str | Path = "BBDD/ia-arma3.db", vigilante: VigilanteEspacio | None = None) -> None:
        self.ruta = str(ruta)
        self.vigilante = vigilante
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.ruta, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._cerrojo = threading.RLock()
        version = self._version()
        if version is not None and version != VERSION_ESQUEMA:
            self._con.close()
            raise ErrorBaseDatos(
                f"La base de datos {self.ruta} es de la versión {version} y esta IA usa la {VERSION_ESQUEMA}. "
                "Bórrala (o muévela) y se creará una nueva."
            )
        with self._transaccion() as con:
            if self.ruta != ":memory:":
                con.execute("PRAGMA journal_mode = WAL")
            con.executescript(_ESQUEMA)
            con.execute(
                "INSERT OR IGNORE INTO meta (clave, valor) VALUES ('version_esquema', ?)",
                (str(VERSION_ESQUEMA),),
            )

    def _version(self) -> int | None:
        try:
            filas = self._con.execute("SELECT valor FROM meta WHERE clave = 'version_esquema'").fetchall()
        except sqlite3.OperationalError:
            return None
        return int(filas[0][0]) if filas else None

    @property
    def ahorro(self) -> bool:
        """Modo ahorro: el disco o el límite de la base de datos están al máximo."""
        return self.vigilante is not None and self.vigilante.critico

    @contextmanager
    def _transaccion(self) -> Iterator[sqlite3.Connection]:
        with self._cerrojo:
            try:
                yield self._con
                self._con.commit()
            except Exception:
                self._con.rollback()
                raise
        if self.vigilante is not None:
            self.vigilante.comprobar()

    def _consulta(self, sql: str, parametros: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._cerrojo:
            return self._con.execute(sql, tuple(parametros)).fetchall()

    def cerrar(self) -> None:
        with self._cerrojo:
            self._con.close()

    # ------------------------------------------------------------------ misiones
    def iniciar_mision(
        self,
        escenario: str,
        *,
        campana_id: str | None = None,
        capitulo: int | None = None,
        titulo: str = "",
        terreno: str = "",
        epoca: str | None = None,
        bando: str | None = None,
    ) -> int:
        with self._transaccion() as con:
            cur = con.execute(
                "INSERT INTO misiones (campana_id, capitulo, titulo, escenario, terreno, epoca, bando, inicio) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (campana_id, capitulo, titulo, escenario, terreno, epoca, bando, _ahora()),
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

    def misiones(self, campana_id: str | None = None, limite: int = 20) -> list[dict[str, Any]]:
        if campana_id:
            filas = self._consulta(
                "SELECT * FROM misiones WHERE campana_id = ? ORDER BY id DESC LIMIT ?", (campana_id, limite)
            )
        else:
            filas = self._consulta("SELECT * FROM misiones ORDER BY id DESC LIMIT ?", (limite,))
        return [dict(f) for f in filas]

    # ------------------------------------------------------------------ eventos y decisiones
    def registrar_eventos(self, mision_id: int, eventos: Iterable[Evento]) -> None:
        ahorro = self.ahorro
        filas = [
            (mision_id, e.tiempo, e.tipo, e.unidad, e.descripcion, json.dumps(e.datos, ensure_ascii=False, default=str))
            for e in eventos
            if not (ahorro and e.tipo == "radio")
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
        if self.ahorro:
            # Sin espacio: se guarda qué decidió, pero no los textos largos que lo llevaron a decidirlo.
            sistema, usuario = "", ""
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
                "INSERT INTO lecciones (epoca, rol, situacion, leccion, tipo, etiquetas, peso, "
                "veces_usada, exitos, confirmaciones, mision_origen, activa, creada, actualizada) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    leccion.epoca,
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
        epoca: str | None = None,
        *,
        incluir_generales: bool = True,
        solo_activas: bool = True,
    ) -> list[Leccion]:
        """Lecciones de un rol (más las del rol "todos") y de una era (más las generales)."""
        sql = "SELECT * FROM lecciones WHERE 1=1"
        parametros: list[Any] = []
        if rol is not None:
            sql += " AND rol IN (?, 'todos')"
            parametros.append(rol)
        if epoca is not None:
            sql += " AND (epoca = ?" + (" OR epoca IS NULL)" if incluir_generales else ")")
            parametros.append(epoca)
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

    # ------------------------------------------------------------------ campañas
    def guardar_campana(
        self,
        id_campana: str,
        *,
        nombre: str,
        datos: dict[str, Any],
        encargo: str = "",
        objetivo: str = "",
        estado: str = "en_curso",
        terreno: str = "",
    ) -> None:
        ahora = _ahora()
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO campanas (id, nombre, encargo, objetivo, estado, terreno, datos, creada, actualizada) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET nombre = excluded.nombre, "
                "encargo = excluded.encargo, objetivo = excluded.objetivo, estado = excluded.estado, "
                "terreno = excluded.terreno, datos = excluded.datos, actualizada = excluded.actualizada",
                (id_campana, nombre, encargo, objetivo, estado, terreno, json.dumps(datos, ensure_ascii=False),
                 ahora, ahora),
            )

    def cargar_campana(self, id_campana: str) -> dict[str, Any] | None:
        filas = self._consulta("SELECT datos FROM campanas WHERE id = ?", (id_campana,))
        return json.loads(filas[0]["datos"]) if filas else None

    def campanas(self) -> list[dict[str, Any]]:
        """Campañas guardadas, la última tocada primero (sin el estado completo)."""
        return [dict(f) for f in self._consulta(
            "SELECT id, nombre, encargo, objetivo, estado, terreno, creada, actualizada FROM campanas "
            "ORDER BY actualizada DESC, creada DESC, rowid DESC")]

    def borrar_campana(self, id_campana: str) -> dict[str, int]:
        """Borra la campaña y todo lo suyo (misiones, eventos, decisiones, chat, lore) y libera el espacio.

        Las lecciones y habilidades aprendidas se conservan. Devuelve cuántas filas se han borrado.
        """
        conteo = self.contar_campana(id_campana)
        with self._transaccion() as con:
            con.execute("DELETE FROM campanas WHERE id = ?", (id_campana,))
        with self._cerrojo:
            if self.ruta != ":memory:":
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._con.execute("VACUUM")
        if self.vigilante is not None:
            self.vigilante.comprobar()
        return conteo

    def contar_campana(self, id_campana: str) -> dict[str, int]:
        def contar(sql: str) -> int:
            return int(self._consulta(sql, (id_campana,))[0][0] or 0)

        en_misiones = "(SELECT id FROM misiones WHERE campana_id = ?)"
        return {
            "misiones": contar("SELECT COUNT(*) FROM misiones WHERE campana_id = ?"),
            "eventos": contar(f"SELECT COUNT(*) FROM eventos WHERE mision_id IN {en_misiones}"),
            "decisiones": contar(f"SELECT COUNT(*) FROM decisiones WHERE mision_id IN {en_misiones}"),
            "conversaciones": contar(f"SELECT COUNT(*) FROM conversaciones WHERE mision_id IN {en_misiones}"),
            "lore": contar("SELECT COUNT(*) FROM lore WHERE campana_id = ?"),
        }

    def uso_por_campana(self) -> dict[str, float]:
        """Megabytes (aproximados) que ocupa cada campaña en la base de datos."""
        uso: dict[str, float] = {}
        for c in self.campanas():
            cid = c["id"]
            en_misiones = "(SELECT id FROM misiones WHERE campana_id = ?)"
            consultas = (
                "SELECT SUM(LENGTH(datos)) FROM campanas WHERE id = ?",
                "SELECT SUM(LENGTH(COALESCE(resumen, '')) + LENGTH(COALESCE(resultado, ''))) FROM misiones "
                "WHERE campana_id = ?",
                f"SELECT SUM(LENGTH(descripcion) + LENGTH(COALESCE(datos, ''))) FROM eventos WHERE mision_id IN "
                f"{en_misiones}",
                f"SELECT SUM(LENGTH(sistema) + LENGTH(usuario) + LENGTH(respuesta)) FROM decisiones "
                f"WHERE mision_id IN {en_misiones}",
                f"SELECT SUM(LENGTH(texto)) FROM conversaciones WHERE mision_id IN {en_misiones}",
                "SELECT SUM(LENGTH(texto) + LENGTH(titulo)) FROM lore WHERE campana_id = ?",
            )
            uso[cid] = sum(float(self._consulta(sql, (cid,))[0][0] or 0) for sql in consultas) / (1024 * 1024)
        return uso

    # ------------------------------------------------------------------ lore
    def anadir_lore(self, campana_id: str, tipo: str, titulo: str, texto: str, mision_id: int | None = None) -> int:
        """Apunta un trozo de la historia de la campaña (inicio, misión, imprevisto, personaje, final)."""
        with self._transaccion() as con:
            cur = con.execute(
                "INSERT INTO lore (campana_id, mision_id, tipo, titulo, texto, creado) VALUES (?, ?, ?, ?, ?, ?)",
                (campana_id, mision_id, tipo, titulo, texto, _ahora()),
            )
            return int(cur.lastrowid or 0)

    def lore(self, campana_id: str) -> list[dict[str, Any]]:
        return [dict(f) for f in self._consulta("SELECT * FROM lore WHERE campana_id = ? ORDER BY id", (campana_id,))]

    # ------------------------------------------------------------------ informes
    def estadisticas(self) -> dict[str, Any]:
        def contar(sql: str) -> int:
            return int(self._consulta(sql)[0][0] or 0)

        return {
            "campanas": contar("SELECT COUNT(*) FROM campanas"),
            "misiones": contar("SELECT COUNT(*) FROM misiones WHERE fin IS NOT NULL"),
            "victorias": contar("SELECT COUNT(*) FROM misiones WHERE estado = 'victoria'"),
            "lecciones_activas": contar("SELECT COUNT(*) FROM lecciones WHERE activa = 1"),
            "lecciones_archivadas": contar("SELECT COUNT(*) FROM lecciones WHERE activa = 0"),
            "decisiones_llm": contar("SELECT COUNT(*) FROM decisiones WHERE origen = 'llm'"),
            "decisiones_reglas": contar("SELECT COUNT(*) FROM decisiones WHERE origen = 'reglas'"),
            "mensajes_chat": contar("SELECT COUNT(*) FROM conversaciones"),
            "lore": contar("SELECT COUNT(*) FROM lore"),
        }

    def exportar_dataset(
        self, ruta: str | Path, puntuacion_minima: float = 0.6, solo_llm: bool = True
    ) -> int:
        """Exporta en JSONL (formato "messages") las decisiones de las misiones que salieron bien.

        Es el material para entrenar más adelante un adaptador LoRA del modelo con la
        experiencia acumulada. Devuelve el número de ejemplos escritos.
        """
        sql = (
            "SELECT d.*, m.campana_id, m.epoca, m.puntuacion FROM decisiones d JOIN misiones m "
            "ON m.id = d.mision_id WHERE m.puntuacion >= ? AND d.sistema != ''"
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
                        "campana": fila["campana_id"],
                        "epoca": fila["epoca"],
                        "mision": fila["mision_id"],
                        "puntuacion": fila["puntuacion"],
                    },
                }
                f.write(json.dumps(ejemplo, ensure_ascii=False) + "\n")
        return len(filas)
