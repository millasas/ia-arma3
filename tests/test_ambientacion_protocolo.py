import json

import pytest

from arma3_ia.ambientacion import ERAS, PerfilCampana, normalizar_era
from arma3_ia.geo import Posicion
from arma3_ia.modelo import Accion, MensajeChat, Observacion, TipoAccion
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


def test_la_ambientacion_tematiza_a_los_npc(perfil):
    ww2 = perfil.model_copy(update={"id": "segunda_guerra_mundial", "epoca": "Europa, 1944",
                                    "restricciones": ["Sin GPS."], "medios_aereos": False})
    contexto = ww2.bloque_contexto("comandante")
    assert contexto.startswith("CAMPAÑA:") and "1944" in contexto and "GPS" in contexto
    assert "Tu carácter" in contexto and "Doctrina" in contexto
    assert "Doctrina" not in ww2.bloque_contexto("enlace")
    enemigo = ww2.del_enemigo()
    assert enemigo.bando_ia.nombre == ww2.enemigo.nombre and enemigo.indicativo_mando == "Fortaleza"
    assert enemigo.id == ww2.id  # lo aprendido se comparte dentro de la misma época


@pytest.mark.parametrize(("texto", "era"), [("WW2", "segunda_guerra_mundial"), ("Guerra Fría", "guerra_fria"),
                                            ("moderno", "moderna"), ("ciencia ficción", "futura"),
                                            ("cualquier cosa", "moderna")])
def test_las_eras_se_normalizan(texto, era):
    assert normalizar_era(texto) == era
    assert era in ERAS


def test_la_ambientacion_valida_lo_que_inventa_el_modelo():
    perfil = PerfilCampana.model_validate({
        "id": "Segunda Guerra Mundial", "nombre": "x", "epoca": "1944", "descripcion": "d",
        "bando_ia": {"nombre": "A"}, "enemigo": {"nombre": "B"}, "estilo_comunicacion": "seco",
        "retardo_radio_s": "mucho",
    })
    assert perfil.id == "segunda_guerra_mundial" and perfil.retardo_radio_s >= 0


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
