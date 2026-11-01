"""Terrenos de ARMA 3 entre los que Zeus elige dónde transcurre cada campaña y cada misión.

Solo puede elegir los que el anfitrión tiene instalados (``zeus.terrenos`` en la configuración).
El catálogo describe cada terreno para que Zeus sepa cuál encaja con la historia que pide el
anfitrión; los que vienen de un DLC o de un mod lo indican.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..texto import normalizar


@dataclass(frozen=True)
class Terreno:
    nombre: str
    clase: str  # nombre interno del mundo en ARMA 3 (worldName)
    tamano_km: float
    paisaje: str
    origen: str = "ARMA 3"

    def describir(self) -> str:
        return f"{self.nombre} ({self.tamano_km:g} km, {self.paisaje}; {self.origen})"


CATALOGO: dict[str, Terreno] = {
    t.nombre: t
    for t in (
        Terreno("Altis", "Altis", 30, "isla mediterránea grande: pueblos, olivares, colinas secas, aeropuertos"),
        Terreno("Stratis", "Stratis", 8, "isla mediterránea pequeña y montañosa con una base aérea"),
        Terreno("Malden", "Malden", 12, "isla mediterránea con pueblos, puerto y montes", "ARMA 3 (Malden 2035)"),
        Terreno("Tanoa", "Tanoa", 15, "archipiélago tropical: selva, plantaciones, pueblos costeros", "DLC Apex"),
        Terreno("Livonia", "Enoch", 16, "Europa del Este: bosques densos, granjas, minas y aldeas", "DLC Contact"),
        Terreno("Sefrou-Ramal", "SefrouRamal", 20, "desierto del norte de África: oasis, ciudades de adobe",
                "CDLC Western Sahara"),
        Terreno("Cam Lao Nam", "Cam_Lao_Nam", 20, "selva de Vietnam y Laos: ríos, arrozales, montañas",
                "CDLC S.O.G. Prairie Fire"),
        Terreno("Normandía", "SPE_Normandy", 10, "bocage normando de 1944: setos, granjas, pueblos de piedra",
                "CDLC Spearhead 1944"),
        Terreno("Weferlingen", "gm_weferlingen_summer", 20, "frontera entre las dos Alemanias en los años 80",
                "CDLC Global Mobilization"),
        Terreno("Chernarus", "Chernarus", 15, "antigua república soviética: bosques, aldeas, ciudad costera",
                "mod CUP Terrains"),
        Terreno("Takistan", "Takistan", 13, "Asia central: desierto montañoso, aldeas de adobe, pasos",
                "mod CUP Terrains"),
        Terreno("Zargabad", "Zargabad", 8, "ciudad desértica de Asia central rodeada de colinas", "mod CUP Terrains"),
    )
}
TERRENOS_BASE = ["Altis", "Stratis", "Malden", "Tanoa", "Livonia"]


def describir_terrenos(disponibles: list[str]) -> str:
    return "; ".join(terreno(n).describir() for n in disponibles)


def terreno(nombre: str) -> Terreno:
    """El terreno del catálogo, o uno genérico si el anfitrión ha añadido un mapa que no está en él."""
    return CATALOGO.get(nombre) or Terreno(nombre, nombre, 10, "terreno añadido por el anfitrión", "mod")


def elegir_terreno(texto: str | None, disponibles: list[str], por_defecto: str | None = None) -> str:
    """Interpreta el terreno que escribe el modelo ('altis', 'isla de Altis') entre los instalados."""
    if not disponibles:
        return texto or por_defecto or "Altis"
    norm = normalizar(texto or "")
    for nombre in disponibles:
        claves = {normalizar(nombre), normalizar(terreno(nombre).clase)}
        if norm and any(c and (c == norm or c in norm) for c in claves):
            return nombre
    return por_defecto if por_defecto in disponibles else disponibles[0]


PISTAS_PAISAJE = (
    (("selva", "jungla", "vietnam", "tropical", "pacifico", "isla tropical"), ("Cam Lao Nam", "Tanoa")),
    (("desierto", "sahara", "afric", "arena", "oasis", "sahel"), ("Sefrou-Ramal", "Takistan", "Zargabad")),
    (("afganist", "oriente medio", "irak", "siria", "talib", "yihad", "montan"), ("Takistan", "Zargabad")),
    (("normandia", "1944", "desembarco", "segunda guerra"), ("Normandía", "Livonia")),
    (("guerra fria", "sovietic", "pacto de varsovia", "alemania", "otan"), ("Weferlingen", "Chernarus", "Livonia")),
    (("bosque", "nieve", "europa del este", "rusia", "ucrania", "balcan"), ("Livonia", "Chernarus")),
    (("isla", "mediterr", "grecia", "costa", "anfibio"), ("Altis", "Malden", "Stratis")),
)


def terreno_por_reglas(encargo: str, disponibles: list[str]) -> str:
    """Sin modelo: el terreno instalado que mejor encaja con las palabras del encargo."""
    norm = normalizar(encargo)
    explicito = next((n for n in disponibles if normalizar(n) in norm), None)
    if explicito:
        return explicito
    for pistas, candidatos in PISTAS_PAISAJE:
        if any(p in norm for p in pistas):
            elegido = next((c for c in candidatos if c in disponibles), None)
            if elegido:
                return elegido
    return disponibles[0] if disponibles else "Altis"
