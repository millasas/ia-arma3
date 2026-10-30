import pytest

from arma3_ia.geo import Posicion, describir_relativa, desde_cuadricula, direccion_cardinal, parsear_posicion
from arma3_ia.texto import normalizar, recortar, reloj, similitud, tokens


def test_normalizar_quita_tildes_y_signos():
    assert normalizar("Halcón-1") == "halcon 1"
    assert normalizar("  ¡Águila, ÁGUILA!  ") == "aguila aguila"


def test_tokens_reducen_a_la_raiz_y_quitan_palabras_vacias():
    assert tokens("Los aterrizajes en la LZ caliente") == tokens("aterrizaje lz calientes")
    assert "los" not in tokens("los aterrizajes")


def test_similitud():
    assert similitud("no aterrizar en zona caliente", "no aterrizar en zonas calientes") == 1.0
    assert similitud("emboscada en el bosque", "aterrizaje nocturno") == 0.0


def test_reloj_y_recortar():
    assert reloj(75) == "01:15"
    assert reloj(3725) == "1:02:05"
    assert recortar("una frase larguísima", 10) == "una frase…"


@pytest.mark.parametrize(
    ("texto", "x", "y"),
    [("045123", 4550, 12350), ("045 123", 4550, 12350), ("0451", 4500, 51500), ("04501230", 4505, 12305)],
)
def test_desde_cuadricula(texto, x, y):
    pos = desde_cuadricula(texto)
    assert pos is not None
    assert (pos.x, pos.y) == (x, y)


def test_cuadricula_ida_y_vuelta():
    p = Posicion(x=2650, y=3720)
    assert p.cuadricula() == "026037"
    assert p.cuadricula(8) == "02650372"
    vuelta = desde_cuadricula(p.cuadricula())
    assert vuelta is not None and vuelta.distancia(p) < 71


def test_posicion_acepta_formato_arma():
    assert Posicion.model_validate([1500.5, 2000, 12.3]) == Posicion(x=1500.5, y=2000)
    assert Posicion.model_validate("015020").x == 1550


def test_rumbo_y_direccion():
    origen = Posicion(x=0, y=0)
    assert origen.rumbo(Posicion(x=0, y=100)) == pytest.approx(0)
    assert origen.rumbo(Posicion(x=100, y=0)) == pytest.approx(90)
    assert direccion_cardinal(45) == "NE"
    assert direccion_cardinal(350) == "N"
    assert direccion_cardinal(225) == "SO"
    assert describir_relativa(origen, Posicion(x=300, y=300)) == "420 m al NE"


def test_hacia_no_se_pasa():
    a, b = Posicion(x=0, y=0), Posicion(x=100, y=0)
    assert a.hacia(b, 40) == Posicion(x=40, y=0)
    assert a.hacia(b, 500) == b


def test_parsear_posicion_variantes():
    lugares = {"LZ Norte": Posicion(x=2700, y=3500), "obj_aldea": Posicion(x=2600, y=2700)}
    limites = (4000.0, 4000.0)
    assert parsear_posicion("LZ Norte", lugares, limites) == lugares["LZ Norte"]
    assert parsear_posicion("lz norte", lugares, limites) == lugares["LZ Norte"]
    assert parsear_posicion("la LZ Norte, rápido", lugares, limites) == lugares["LZ Norte"]
    assert parsear_posicion("OBJ_ALDEA", lugares, limites) == lugares["obj_aldea"]
    assert parsear_posicion("026027", lugares, limites) == Posicion(x=2650, y=2750)
    assert parsear_posicion("026-027", lugares, limites) == Posicion(x=2650, y=2750)
    assert parsear_posicion("x=1200, y=3400", lugares, limites) == Posicion(x=1200, y=3400)
    assert parsear_posicion([100, 200], lugares, limites) == Posicion(x=100, y=200)
    assert parsear_posicion("ninguno", lugares, limites) is None
    assert parsear_posicion("Marte", lugares, limites) is None


def test_parsear_prefiere_la_interpretacion_dentro_del_mapa():
    # "2500 3000" como cuadrícula de 8 cifras caería fuera de un mapa de 4 km: son metros.
    assert parsear_posicion("2500, 3000", {}, (4000.0, 4000.0)) == Posicion(x=2500, y=3000)
