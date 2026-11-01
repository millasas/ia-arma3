import pytest

from arma3_ia import cli
from arma3_ia.cli import main


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    """Cada prueba usa su propia carpeta de proyecto y ningún fichero de configuración del usuario."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARMA3IA_CONFIG", raising=False)
    monkeypatch.delenv("ARMA3IA_DB", raising=False)
    monkeypatch.delenv("ARMA3IA_CAMPANAS", raising=False)
    monkeypatch.setenv("ARMA3IA_RAIZ", str(tmp_path))
    monkeypatch.setattr(cli, "puede_preguntar", lambda: False)
    return tmp_path


def respuestas(monkeypatch, *textos):
    cola = list(textos)
    monkeypatch.setattr(cli, "puede_preguntar", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _="": cola.pop(0))
    return cola


def test_campana_entera_desde_la_consola(entorno, monkeypatch, capsys):
    assert main(["campana", "nueva", "campaña de 1944 para volar un puente, 2 misiones de 20 minutos",
                 "--falso", "--semilla", "2"]) == 0
    salida = capsys.readouterr().out
    assert "2 misiones de 20 min" in salida and "Volar un puente" in salida
    campanas = entorno / "CAMPAÑAS"
    (carpeta,) = list(campanas.iterdir())
    assert (carpeta / "campana.txt").read_text(encoding="utf-8").startswith("CAMPAÑA:")
    assert (entorno / "BBDD" / "ia-arma3.db").is_file()

    assert main(["campana", "lista"]) == 0
    assert "en curso (misión 1 de 2)" in capsys.readouterr().out
    # Una misión: Zeus la prepara, la dirige y escribe el informe.
    assert main(["campana", "jugar", "--falso", "--semilla", "2"]) == 0
    salida = capsys.readouterr().out
    assert "Zeus: " in salida and "MISIÓN TERMINADA" in salida and "Crónica de Zeus" in salida
    (mision,) = list((carpeta / "misiones").iterdir())
    assert mision.name.startswith("01_") and (mision / "mision.yaml").is_file()
    informe = (mision / "informe.txt").read_text(encoding="utf-8")
    assert informe.startswith("INFORME DE MISIÓN 1/2") and "RESULTADO:" in informe and "CRÓNICA:" in informe
    assert len(informe) < 4000  # un informe corto

    # Al terminar la campaña pregunta si se borra: aquí se contesta que sí, pero se guardan los textos.
    respuestas(monkeypatch, "s", "n")
    assert main(["campana", "jugar", "--todas", "--falso", "--semilla", "2"]) == 0
    salida = capsys.readouterr().out
    assert "CAMPAÑA TERMINADA" in salida and "borrada de la base de datos" in salida
    assert (carpeta / "informe_final.txt").read_text(encoding="utf-8").startswith("INFORME FINAL DE CAMPAÑA")
    assert main(["campana", "lista"]) == 0
    assert "No hay campañas" in capsys.readouterr().out
    assert carpeta.is_dir()


def test_si_no_se_dice_el_tamano_se_pregunta(monkeypatch, capsys):
    respuestas(monkeypatch, "3", "")
    assert main(["campana", "nueva", "una campaña en la selva", "--falso"]) == 0
    assert "3 misiones de 60 min" in capsys.readouterr().out


def test_sin_terminal_usa_los_valores_por_defecto(capsys):
    assert main(["campana", "nueva", "algo", "--falso", "--duracion", "25"]) == 0
    salida = capsys.readouterr().out
    assert "4 misiones de 25 min" in salida and "--misiones N" in salida


def test_no_borra_sin_preguntar_y_al_final_explica_como_borrar(entorno, monkeypatch, capsys):
    assert main(["campana", "nueva", "campaña corta, 1 misión de 15 minutos", "--falso", "--semilla", "1"]) == 0
    capsys.readouterr()
    assert main(["campana", "jugar", "--falso", "--semilla", "1"]) == 0
    salida = capsys.readouterr().out
    assert "Para borrarla: arma3-ia campana borrar" in salida  # sin terminal no pregunta
    (carpeta,) = list((entorno / "CAMPAÑAS").iterdir())
    assert main(["campana", "jugar", carpeta.name, "--falso"]) == 0  # una campaña terminada no se rejuega
    assert "CAMPAÑA TERMINADA" in capsys.readouterr().out
    assert main(["campana", "borrar", carpeta.name]) == 1
    assert "--si" in capsys.readouterr().err
    respuestas(monkeypatch, "n")
    assert main(["campana", "borrar", carpeta.name]) == 0
    assert "No se ha borrado nada" in capsys.readouterr().out
    assert main(["campana", "borrar", carpeta.name, "--si"]) == 0
    assert not carpeta.exists()
    assert main(["campana", "borrar", "no_existe", "--si"]) == 1


def test_espacio_lecciones_y_dataset(capsys):
    assert main(["campana", "nueva", "campaña moderna, 1 misión de 15 minutos", "--falso"]) == 0
    assert main(["campana", "jugar", "--falso"]) == 0
    capsys.readouterr()
    assert main(["espacio"]) == 0
    salida = capsys.readouterr().out
    assert "Espacio correcto" in salida and "terminada: se puede borrar" in salida
    assert main(["lecciones", "--rol", "zeus"]) == 0
    assert "[zeus" in capsys.readouterr().out
    assert main(["exportar-dataset", "--minimo", "0"]) == 0
    assert "ejemplos exportados" in capsys.readouterr().out


def test_jugar_sin_campanas(capsys):
    assert main(["campana", "jugar", "--falso"]) == 1
    assert "campana nueva" in capsys.readouterr().err


def test_entrenar_vuelo(capsys):
    assert main(["entrenar-vuelo", "--episodios", "160", "--semilla", "1"]) == 0
    assert "LZ caliente" in capsys.readouterr().out


def test_comprobar_contra_servidor_ollama_simulado(servidor_ollama, monkeypatch, capsys):
    monkeypatch.setenv("ARMA3IA_LLM_URL", servidor_ollama.url)
    assert main(["comprobar"]) == 0
    salida = capsys.readouterr().out
    assert "Ollama 0.12.0 responde" in salida and "Respuesta JSON válida" in salida
    assert "solo el 75% del modelo cabe en la GPU" in salida
    assert "Base de datos en" in salida and "Espacio correcto" in salida
    monkeypatch.setenv("ARMA3IA_LLM_MODELO", "llama3.1:8b")
    assert main(["comprobar", "--rapido"]) == 1
    assert "ollama pull llama3.1:8b" in capsys.readouterr().out


def test_comprobar_sin_ollama(monkeypatch, capsys):
    monkeypatch.setenv("ARMA3IA_LLM_URL", "http://127.0.0.1:9")
    assert main(["comprobar"]) == 1
    assert "No hay conexión" in capsys.readouterr().out


def test_config_desde_fichero(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("llm:\n  url: http://127.0.0.1:9\n  modelo: gemma3:12b\n"
                                          "zeus:\n  terrenos: [Chernarus]\n", encoding="utf-8")
    assert main(["comprobar", "--rapido"]) == 1
    salida = capsys.readouterr().out
    assert "gemma3:12b" in salida and "Chernarus" in salida


def test_campana_con_ollama_simulado(servidor_ollama, monkeypatch, capsys):
    """Camino HTTP completo: el servidor contesta lo mínimo y Zeus repara lo que falta con sus reglas."""
    monkeypatch.setenv("ARMA3IA_LLM_URL", servidor_ollama.url)
    assert main(["campana", "nueva", "campaña moderna, 1 misión de 10 minutos"]) == 0
    assert main(["campana", "jugar", "--max-pasos", "3"]) == 0
    assert len(servidor_ollama.peticiones) >= 4
    assert any("Zeus" in p["messages"][0]["content"] for p in servidor_ollama.peticiones)
    assert all(p["format"]["type"] == "object" for p in servidor_ollama.peticiones)


def test_config_de_ejemplo_es_valida(tmp_path):
    from pathlib import Path

    from arma3_ia.config import cargar_config

    raiz = Path(__file__).resolve().parents[1]
    config = cargar_config(raiz / "config" / "config.ejemplo.yaml")
    assert config.llm.modelo == "qwen3:4b" and config.bbdd.reflexion
    assert config.ruta_bbdd == tmp_path / "BBDD" / "ia-arma3.db"
    assert config.carpeta_campanas == tmp_path / "CAMPAÑAS"
    assert "Altis" in config.zeus.terrenos
