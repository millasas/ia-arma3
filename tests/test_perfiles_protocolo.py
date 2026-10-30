import json

import pytest

from arma3_ia.campanas import cargar_perfil, perfiles_disponibles
from arma3_ia.geo import Posicion
from arma3_ia.modelo import Accion, MensajeChat, Observacion, TipoAccion, TipoUnidad
from arma3_ia.puente import (
    ErrorProtocolo,
    codificar_acciones,
    codificar_chat,
    codificar_observacion,
    decodificar,
    leer_acciones,
    leer_chat,
    leer_observacion,
)


def test_todos_los_perfiles_cargan():
    perfiles = perfiles_disponibles()
    assert {"moderno", "ww2", "insurgencia", "contrainsurgencia", "guerra_fria"} <= set(perfiles)
    for perfil in perfiles.values():
        assert perfil.doctrina, perfil.id
        assert perfil.indicativos.get("infanteria"), perfil.id
        assert perfil.bloque_contexto("comandante").startswith("CAMPAÑA:")


def test_tematizacion_por_campana():
    ww2 = cargar_perfil("ww2")
    assert not ww2.medios_aereos and ww2.retardo_radio_s > 0
    contexto = ww2.bloque_contexto("comandante")
    assert "1944" in contexto and "GPS" in contexto and "Tu carácter" in contexto
    assert "Doctrina" not in ww2.bloque_contexto("enlace")
    assert ww2.indicativo_para(TipoUnidad.INFANTERIA, 1) == "Baker"
    assert ww2.indicativo_para(TipoUnidad.HELICOPTERO, 0) is None


def test_perfil_desconocido_o_desde_fichero(tmp_path):
    with pytest.raises(KeyError, match="Disponibles"):
        cargar_perfil("marciano")
    fichero = tmp_path / "mio.yaml"
    fichero.write_text(
        "id: mio\nnombre: Mío\nepoca: '2000'\ndescripcion: d\nbando_ia: {nombre: A}\n"
        "enemigo: {nombre: B}\nestilo_comunicacion: seco\n",
        encoding="utf-8",
    )
    assert cargar_perfil(str(fichero)).id == "mio"
    assert "mio" in perfiles_disponibles(tmp_path)


def test_protocolo_ida_y_vuelta(obs):
    linea = codificar_observacion(obs)
    assert "\n" not in linea
    assert leer_observacion(decodificar(linea)) == obs

    acciones = [Accion(unidad="alfa", tipo=TipoAccion.MOVER, destino=Posicion(x=1, y=2), motivo="x")]
    assert leer_acciones(decodificar(codificar_acciones("blufor", acciones))) == acciones
    chat = [MensajeChat(autor="López", texto="¿Qué tal?")]
    assert leer_chat(decodificar(codificar_chat("blufor", chat))) == chat


def test_protocolo_acepta_posiciones_de_arma():
    linea = json.dumps({
        "v": 1, "tipo": "observacion", "bando": "blufor",
        "datos": {"tiempo": 10, "bando": "blufor", "unidades": [{
            "id": "grp1", "indicativo": "Alpha 1-1", "tipo": "infanteria",
            "posicion": [2650.5, 3720.2, 15.3], "efectivos": 6, "efectivos_max": 8,
        }]},
    })
    obs = leer_observacion(decodificar(linea))
    assert isinstance(obs, Observacion)
    assert obs.unidades[0].posicion == Posicion(x=2650.5, y=3720.2)


def test_protocolo_rechaza_mensajes_invalidos():
    with pytest.raises(ErrorProtocolo):
        decodificar("no es json")
    with pytest.raises(ErrorProtocolo, match="versión"):
        decodificar('{"v": 99, "tipo": "chat"}')
    with pytest.raises(ErrorProtocolo, match="se esperaba"):
        leer_acciones(decodificar('{"v": 1, "tipo": "chat", "datos": []}'))
