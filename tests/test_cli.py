import pytest

from arma3_ia.cli import main


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    """Cada prueba usa su propia base de datos y ningún fichero de configuración del usuario."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARMA3IA_CONFIG", raising=False)
    monkeypatch.setenv("ARMA3IA_DB", str(tmp_path / "memoria.db"))
    return tmp_path


def test_listados(capsys):
    assert main(["perfiles"]) == 0
    salida = capsys.readouterr().out
    assert "ww2" in salida and "insurgencia" in salida
    assert main(["escenarios"]) == 0
    assert "rescate_helicoptero" in capsys.readouterr().out


def test_simular_con_llm_falso_guarda_en_memoria(capsys):
    assert main(["simular", "rescate_helicoptero", "--falso", "--semilla", "1"]) == 0
    salida = capsys.readouterr().out
    assert "MISIÓN TERMINADA: VICTORIA" in salida and "Informe post-acción" in salida
    assert main(["memoria", "--resumen"]) == 0
    assert "rescate_helicoptero" in capsys.readouterr().out
    assert main(["lecciones"]) == 0
    capsys.readouterr()
    assert main(["exportar-dataset", "dataset.jsonl", "--minimo", "0"]) == 0
    assert "ejemplos exportados" in capsys.readouterr().out


def test_simular_escenario_inexistente(capsys):
    assert main(["simular", "no_existe", "--falso"]) == 1
    assert "Disponibles" in capsys.readouterr().err


def test_autoentrenar_y_entrenar_vuelo(capsys):
    assert main(["entrenar-vuelo", "--episodios", "160", "--semilla", "1"]) == 0
    assert "LZ caliente" in capsys.readouterr().out
    assert main(["autoentrenar", "--falso", "--misiones", "2", "--escenarios", "defensa_granja",
                 "--semilla", "3"]) == 0
    salida = capsys.readouterr().out
    assert "#001" in salida and "#002" in salida


def test_comprobar_contra_servidor_ollama_simulado(servidor_ollama, monkeypatch, capsys):
    monkeypatch.setenv("ARMA3IA_LLM_URL", servidor_ollama.url)
    assert main(["comprobar"]) == 0
    salida = capsys.readouterr().out
    assert "Ollama 0.12.0 responde" in salida and "Respuesta JSON válida" in salida
    monkeypatch.setenv("ARMA3IA_LLM_MODELO", "llama3.1:8b")
    assert main(["comprobar", "--rapido"]) == 1
    assert "ollama pull llama3.1:8b" in capsys.readouterr().out


def test_comprobar_sin_ollama(monkeypatch, capsys):
    monkeypatch.setenv("ARMA3IA_LLM_URL", "http://127.0.0.1:9")
    assert main(["comprobar"]) == 1
    assert "No hay conexión" in capsys.readouterr().out


def test_config_desde_fichero(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("llm:\n  url: http://127.0.0.1:9\n  modelo: gemma3:12b\n",
                                          encoding="utf-8")
    assert main(["comprobar", "--rapido"]) == 1
    assert "gemma3:12b" in capsys.readouterr().out


def test_simular_con_ollama_simulado(servidor_ollama, monkeypatch, capsys):
    """Camino real HTTP completo: misión corta con un servidor que habla la API de Ollama."""
    monkeypatch.setenv("ARMA3IA_LLM_URL", servidor_ollama.url)
    assert main(["simular", "asalto_aldea", "--max-pasos", "3", "--sin-reflexion"]) in (0, 2)
    assert len(servidor_ollama.peticiones) >= 4
    assert any("Mando" in p["messages"][0]["content"] for p in servidor_ollama.peticiones)
    assert all(p["format"]["type"] == "object" for p in servidor_ollama.peticiones)


def test_config_de_ejemplo_es_valida():
    from pathlib import Path

    from arma3_ia.config import cargar_config

    raiz = Path(__file__).resolve().parents[1]
    config = cargar_config(raiz / "config" / "config.ejemplo.yaml")
    assert config.llm.modelo == "qwen3:8b" and config.memoria.reflexion


def test_campana_de_zeus_desde_la_consola(capsys):
    assert main(["campana", "nueva", "campaña", "de", "insurgencia", "con", "una", "emboscada",
                 "--falso", "--semilla", "2"]) == 0
    salida = capsys.readouterr().out
    assert "Emboscada" in salida
    assert main(["campana", "lista"]) == 0
    assert "capítulo 1/" in capsys.readouterr().out
    assert main(["campana", "jugar", "--falso", "--semilla", "2"]) == 0
    salida = capsys.readouterr().out
    assert "Zeus: " in salida and "MISIÓN TERMINADA" in salida
    assert "Zeus, aquí" not in salida
    assert main(["campana", "estado"]) == 0
    assert "→ 2." in capsys.readouterr().out
