"""Línea de órdenes: ``arma3-ia <orden>`` (o ``python -m arma3_ia <orden>``).

Las campañas solo se crean cuando tú las pides; todo lo demás lo decide Zeus.

- ``comprobar``                           Ollama, el modelo, la base de datos y el espacio.
- ``campana nueva "encargo"``             Zeus crea la campaña (pregunta misiones y duración si no las dices).
- ``campana jugar [id] [--todas]``        Zeus prepara, dirige y cierra la siguiente misión.
- ``campana lista`` / ``estado`` / ``borrar <id>``.
- ``espacio``                             lo que ocupa la base de datos y cada campaña.
- ``lecciones``                           lo que ha aprendido la IA.
- ``entrenar-vuelo`` / ``exportar-dataset``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import __version__
from .archivo import ArchivoCampanas, sucesos_clave
from .config import Config, cargar_config
from .habilidades import PARAMETROS_POR_DEFECTO, HabilidadAterrizaje, Habilidades, evaluar
from .llm import ClienteLLM, ClienteOllama, LLMFalso, MensajeLLM, crear_cliente, generar_estructurado
from .mando import SalidaCerebro
from .memoria import ErrorBaseDatos, EstadoEspacio, Memoria, Reflexion, VigilanteEspacio
from .partida import InformePartida, Partida, preparar_cerebros
from .simulador import Escenario, SimuladorTexto, mapa_ascii
from .texto import reloj
from .zeus import DirectorPartida, EstadoCampana, SucesoPlan, Zeus
from .zeus import reglas as reglas_zeus

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
COLORES = {"gris": "90", "rojo": "31", "verde": "32", "amarillo": "33", "azul": "34", "cian": "36", "negrita": "1"}
NIVEL_COLOR = {"ok": "verde", "aviso": "amarillo", "critico": "rojo"}


def c(texto: str, color: str) -> str:
    return f"\033[{COLORES[color]}m{texto}\033[0m" if COLOR else texto


def puede_preguntar() -> bool:
    return sys.stdin.isatty()


def preguntar_si(pregunta: str) -> bool:
    respuesta = input(c(f"{pregunta} [s/N]: ", "negrita")).strip().lower()
    return respuesta in ("s", "si", "sí", "y", "yes")


def preguntar_numero(pregunta: str, por_defecto: int, minimo: int, maximo: int) -> int:
    while True:
        texto = input(c(f"{pregunta} [{por_defecto}]: ", "negrita")).strip()
        if not texto:
            return por_defecto
        if texto.isdigit() and minimo <= int(texto) <= maximo:
            return int(texto)
        print(f"Escribe un número entre {minimo} y {maximo}.")


def _llm(config: Config, falso: bool) -> ClienteLLM:
    if falso:
        return LLMFalso()
    # Las respuestas de Zeus son largas: la conexión no puede cortarse antes que su propio límite.
    # Los NPC no se ven afectados porque tienen su límite aparte (agentes.timeout_decision_s).
    return crear_cliente(config.llm.model_copy(update={"timeout_s": max(config.llm.timeout_s,
                                                                         config.zeus.timeout_s)}))


def _avisar_espacio(estado: EstadoEspacio) -> None:
    print(c(estado.mensaje(), NIVEL_COLOR[estado.nivel]))


@dataclass
class Almacen:
    """La base de datos, su vigilante de espacio y la carpeta CAMPAÑAS, abiertos juntos."""

    memoria: Memoria
    vigilante: VigilanteEspacio
    archivo: ArchivoCampanas

    def cerrar(self) -> None:
        self.memoria.cerrar()


def abrir_almacen(config: Config) -> Almacen:
    vigilante = VigilanteEspacio(config.ruta_bbdd, carpeta_campanas=config.carpeta_campanas,
                                 limite_mb=config.bbdd.limite_mb, minimo_libre_mb=config.bbdd.minimo_libre_mb,
                                 al_cambiar=_avisar_espacio)
    memoria = Memoria(config.ruta_bbdd, vigilante=vigilante)
    archivo = ArchivoCampanas(config.carpeta_campanas, al_escribir=vigilante.carpetas_cambiadas)
    return Almacen(memoria, vigilante, archivo)


def crear_zeus(config: Config, llm: ClienteLLM, memoria: Memoria, semilla: int | None) -> Zeus:
    return Zeus(llm, memoria, terrenos=config.zeus.terrenos, timeout_s=config.zeus.timeout_s,
                max_tokens=config.zeus.max_tokens, reintentos=config.llm.reintentos, semilla=semilla)


# ---------------------------------------------------------------------------------- comprobar
async def orden_comprobar(args: argparse.Namespace, config: Config) -> int:
    print(c(f"arma3-ia {__version__}", "negrita"))
    print(f"Ollama: {config.llm.url} | modelo: {config.llm.modelo}")
    estado = 0
    cliente = ClienteOllama(config.llm.url, config.llm.modelo, pensar=config.llm.pensar,
                            num_ctx=config.llm.num_ctx, timeout_s=config.llm.timeout_s)
    try:
        informe = await cliente.comprobar()
        if not informe.conectado:
            print(c(f"✗ No hay conexión con Ollama: {informe.error}", "rojo"))
            print("  Instálalo con: curl -fsSL https://ollama.com/install.sh | sh")
            estado = 1
        elif not informe.modelo_disponible:
            print(c(f"✓ Ollama {informe.version} responde", "verde"))
            print(c(f"✗ El modelo {config.llm.modelo} no está descargado.", "rojo"))
            print(f"  Descárgalo con: ollama pull {config.llm.modelo}")
            print(f"  Modelos instalados: {', '.join(informe.modelos) or 'ninguno'}")
            estado = 1
        else:
            print(c(f"✓ Ollama {informe.version} responde", "verde"))
            print(c(f"✓ Modelo {config.llm.modelo} disponible", "verde"))
            if not args.rapido:
                await _probar_generacion(cliente)
    except Exception as error:
        print(c(f"✗ Error durante la comprobación: {error}", "rojo"))
        estado = 1
    finally:
        await cliente.cerrar()
    try:
        almacen = abrir_almacen(config)
        print(c(f"✓ Base de datos en {config.ruta_bbdd}: {almacen.memoria.estadisticas()}", "verde"))
        _avisar_espacio(almacen.vigilante.comprobar())
        almacen.cerrar()
    except Exception as error:
        print(c(f"✗ No se puede abrir la base de datos {config.ruta_bbdd}: {error}", "rojo"))
        estado = 1
    print(f"Terrenos que puede usar Zeus: {', '.join(config.zeus.terrenos)}")
    return estado


async def _probar_generacion(cliente: ClienteOllama) -> None:
    from .agentes import RespuestaEnlace

    inicio = time.monotonic()
    respuesta, bruta = await generar_estructurado(
        cliente,
        [MensajeLLM("system", "Eres un operador de radio militar. Responde en JSON."),
         MensajeLLM("user", "Confirma que recibes la transmisión en una frase.")],
        RespuestaEnlace,
    )
    segundos = time.monotonic() - inicio
    velocidad = bruta.tokens_salida / segundos if segundos else 0.0
    print(c(f"✓ Respuesta JSON válida en {segundos:.1f} s (~{velocidad:.0f} tokens/s): ", "verde")
          + respuesta.respuesta)
    if segundos > 20:
        print(c("  Aviso: va lento. ¿Está usando la GPU? Mira 'ollama ps' y 'nvidia-smi'.", "amarillo"))
    try:
        en_gpu = await cliente.en_gpu()
    except Exception:
        en_gpu = None
    if en_gpu is not None and en_gpu >= 0.99:
        print(c("✓ El modelo está entero en la GPU", "verde"))
    elif en_gpu is not None:
        print(c(f"  Aviso: solo el {en_gpu:.0%} del modelo cabe en la GPU; el resto va a la CPU y será "
                "más lento. Baja llm.num_ctx o usa un modelo más pequeño (qwen3:4b).", "amarillo"))


# ---------------------------------------------------------------------------------- misión
def _imprimir_paso(salidas: dict[str, SalidaCerebro], detalle: bool) -> None:
    for salida in salidas.values():
        for e in salida.eventos:
            color = "rojo" if e.tipo in ("bajas", "unidad_destruida", "objetivo_fallido", "explosion") else (
                "verde" if e.tipo in ("objetivo_completado", "enemigo_abatido", "contacto_destruido") else "gris")
            if detalle or color != "gris" or e.tipo in ("contacto", "aterrizaje", "embarque", "desembarco"):
                print(c(f"[{reloj(e.tiempo)}] {e.descripcion}", color))
        for m in salida.chat:
            if m.canal == "radio":
                print(c(f"[{reloj(m.tiempo)}] 📻 {m.autor} {m.texto}", "cian"))
            else:
                print(c(f"[{reloj(m.tiempo)}] 💬 {m.autor}: {m.texto}", "amarillo"))


def _imprimir_mision(escenario: Escenario) -> None:
    c_ = escenario.condiciones
    print(c(f"=== {escenario.nombre} ===", "negrita"))
    print(f"Terreno: {escenario.mapa.nombre} | {c_.hora}, {c_.clima} | {int(escenario.duracion_max_s // 60)} min")
    if escenario.briefing:
        print(escenario.briefing.strip())
    if escenario.objetos:
        print(c(f"Decorado de Zeus: {len(escenario.objetos)} elementos (" +
                ", ".join(sorted({o.tipo.value for o in escenario.objetos})) + ")", "gris"))
    for nombre, bando in escenario.bandos.items():
        cabecera = f"{bando.nombre or nombre} ({nombre})"
        if not bando.mando:
            cabecera += " — sin mando: cada grupo va a su aire"
        print(c(cabecera, "negrita"))
        if bando.comandante:
            print(f"   Al mando: {bando.comandante.presentacion()}")
        for u in bando.unidades:
            quien = " — jugadores" if u.jugadores else (f" — {u.persona.presentacion()}" if u.persona else "")
            tarea = f" [{u.orden.tarea.value}]" if u.orden else ""
            print(f"   {u.indicativo} ({u.tipo.value}, {u.efectivos}){tarea}{quien}")


def _imprimir_informe(informe: InformePartida) -> None:
    r = informe.resultado
    color = "verde" if r.estado == "victoria" else "amarillo" if r.estado == "parcial" else "rojo"
    print()
    print(c(f"=== MISIÓN TERMINADA: {r.estado.upper()} ({r.motivo}) ===", color))
    print(f"Puntuación {r.puntuacion:.2f} | objetivos {r.objetivos_completados}/{r.objetivos_totales} | "
          f"bajas propias {r.bajas_propias}/{r.efectivos_iniciales} | bajas enemigas {r.bajas_enemigas} | "
          f"duración {reloj(r.duracion_s)}")
    print(c("Informe post-acción:", "negrita"), informe.resumen)
    for leccion in informe.lecciones_nuevas:
        print(c(f"  + Nueva lección ({leccion.rol}): {leccion.texto()}", "verde"))
    for leccion in informe.lecciones_reforzadas:
        print(c(f"  ↑ Lección reforzada ({leccion.rol}): {leccion.texto()}", "azul"))


@dataclass
class OpcionesJuego:
    semilla: int | None = None
    detalle: bool = False
    interactivo: bool = False
    cada_mapa: int = 0
    max_pasos: int | None = None
    mostrar: bool = True


async def jugar_mision(
    config: Config,
    estado: EstadoCampana,
    escenario: Escenario,
    sucesos: list[SucesoPlan],
    zeus: Zeus,
    llm: ClienteLLM,
    memoria: Memoria,
    opciones: OpcionesJuego,
) -> tuple[InformePartida, SimuladorTexto]:
    """Juega una misión en el simulador con Zeus dirigiendo y los NPC decidiendo por su cuenta."""
    perfil = estado.perfil
    habilidades = Habilidades(memoria, explorar=config.habilidades.explorar, semilla=opciones.semilla)
    sim = SimuladorTexto(escenario, semilla=opciones.semilla)
    cerebros = preparar_cerebros(escenario, llm, perfil, config, memoria, habilidades)
    director = DirectorPartida(zeus, sim, cerebros, escenario, perfil, sucesos=sucesos,
                               cada_s=config.zeus.intervenir_cada_s, semilla=opciones.semilla)
    reflexion = (Reflexion(llm, memoria, perfil, umbral_exito=config.bbdd.umbral_exito)
                 if config.bbdd.reflexion else None)
    vistos = {"anuncios": 0}

    async def al_paso(paso: int, salidas: dict[str, SalidaCerebro]) -> None:
        if not opciones.mostrar:
            return
        _imprimir_paso(salidas, opciones.detalle)
        for anuncio in sim.anuncios[vistos["anuncios"]:]:
            print(c(f"[{reloj(anuncio.tiempo)}] ⚡ Zeus: {anuncio.texto}", "azul"))
        vistos["anuncios"] = len(sim.anuncios)
        if opciones.cada_mapa and paso % opciones.cada_mapa == 0:
            print(mapa_ascii(sim, sim.bando_principal))
        if opciones.interactivo and not sim.terminado():
            texto = (await asyncio.to_thread(
                input, c("Tú (Enter = seguir, 'mapa', 'salir' o habla por radio con un NPC): ", "negrita"))).strip()
            if texto.lower() == "salir":
                sim.finalizar("interrumpida por el jugador")
            elif texto.lower() == "mapa":
                print(mapa_ascii(sim, sim.bando_principal))
            elif texto:
                sim.inyectar_chat(sim.bando_principal, "Jugador", texto)

    if opciones.mostrar:
        _imprimir_mision(escenario)
        print(mapa_ascii(sim, sim.bando_principal))
    capitulo = estado.capitulo_actual
    partida = Partida(sim, cerebros, memoria=memoria, reflexion=reflexion, habilidades=habilidades,
                      escenario=escenario, campana_id=estado.id, capitulo=estado.capitulo,
                      titulo=capitulo.titulo if capitulo else escenario.nombre,
                      max_pasos=opciones.max_pasos, al_paso=al_paso, director=director)
    informe = await partida.jugar()
    if opciones.mostrar:
        print(mapa_ascii(sim))
        _imprimir_informe(informe)
    return informe, sim


# ---------------------------------------------------------------------------------- campañas
def _campana_elegida(zeus: Zeus, memoria: Memoria, id_campana: str | None, *, en_curso: bool = False) -> EstadoCampana:
    if id_campana:
        return zeus.cargar(id_campana)
    filas = memoria.campanas()
    if en_curso:
        filas = [f for f in filas if f["estado"] == "en_curso"]
    if not filas:
        raise KeyError("No hay campañas" + (" en curso" if en_curso else "") +
                       ". Pide una con: arma3-ia campana nueva \"...\"")
    return zeus.cargar(filas[0]["id"])


def imprimir_campana(estado: EstadoCampana) -> None:
    plan, p = estado.plan, estado.perfil
    print(c(f"=== {plan.nombre} ({estado.id}) ===", "negrita"))
    print(plan.premisa)
    print(c(f"OBJETIVO FINAL: {plan.objetivo_final}", "negrita"))
    print(f"{p.epoca} | {p.bando_ia.nombre} contra {p.enemigo.nombre} | terreno {estado.terreno} | "
          f"{estado.misiones_total} misiones de {estado.duracion_mision_min} min | margen de error "
          f"{estado.margen}/{estado.margen_inicial}")
    for i, cap in enumerate(plan.capitulos):
        hecho = next((r for r in estado.historial if r.capitulo == i), None)
        marca = (c("✓", "verde") if hecho and hecho.estado == "victoria" else
                 c("~", "amarillo") if hecho and hecho.estado == "parcial" else c("✗", "rojo") if hecho
                 else c("→", "amarillo") if i == estado.capitulo and not estado.terminada else " ")
        resultado = f" — {hecho.estado} ({hecho.puntuacion:.2f})" if hecho else ""
        critico = c(" [crítica]", "rojo") if cap.critico else ""
        print(f" {marca} {i + 1}. {cap.titulo} [{cap.tipo.value}]{critico}{resultado}")
        print(f"      {cap.sinopsis}")
        if cap.complicacion and not hecho:
            print(c(f"      Problema: {cap.complicacion}", "gris"))
    print(c("Personajes:", "negrita"))
    for pj in estado.personajes:
        print(f"   {'' if pj.vivo else '✝ '}{pj.persona.presentacion()}")
    if estado.antagonista:
        print(c("Antagonista: ", "negrita") + estado.antagonista.presentacion())
    if estado.terminada:
        color = "verde" if estado.resultado_final == "cumplida" else "rojo"
        print(c(f"CAMPAÑA TERMINADA: {estado.estado_texto}. {estado.motivo_final}", color))


def _pedir_tamano(args: argparse.Namespace, config: Config, encargo: str) -> tuple[int, int]:
    """Misiones y minutos por misión: de las opciones, del propio encargo o preguntando."""
    en_texto = reglas_zeus.interpretar_encargo(encargo)
    misiones = args.misiones or en_texto[0]
    duracion = args.duracion or en_texto[1]
    if misiones is None:
        if puede_preguntar():
            misiones = preguntar_numero("¿Cuántas misiones tendrá la campaña?", config.campanas.misiones_por_defecto,
                                        1, reglas_zeus.MAX_MISIONES)
        else:
            misiones = config.campanas.misiones_por_defecto
            print(f"(No has dicho cuántas misiones: uso {misiones}. Usa --misiones N para elegir.)")
    if duracion is None:
        if puede_preguntar():
            duracion = preguntar_numero("¿Cuántos minutos durará cada misión?", config.campanas.duracion_mision_min,
                                        reglas_zeus.DURACION_MIN, reglas_zeus.DURACION_MAX)
        else:
            duracion = config.campanas.duracion_mision_min
            print(f"(No has dicho cuánto dura cada misión: uso {duracion} min. Usa --duracion MIN para elegir.)")
    return misiones, duracion


async def _campana_nueva(args: argparse.Namespace, config: Config, zeus: Zeus, almacen: Almacen) -> None:
    encargo = " ".join(args.encargo).strip()
    misiones, duracion = _pedir_tamano(args, config, encargo)
    print(f"Zeus está creando la campaña ({misiones} misiones de {duracion} min)...")
    estado = await zeus.crear_campana(encargo, misiones=misiones, duracion_mision_min=duracion)
    ruta = almacen.archivo.ficha(estado)
    imprimir_campana(estado)
    print(f"\nFicha de la campaña: {ruta}")
    print(f"Para jugar la primera misión: arma3-ia campana jugar {estado.id}")


async def _campana_jugar(args: argparse.Namespace, config: Config, zeus: Zeus, llm: ClienteLLM,
                         almacen: Almacen) -> None:
    memoria, archivo = almacen.memoria, almacen.archivo
    estado = _campana_elegida(zeus, memoria, args.id, en_curso=not args.id)
    if estado.terminada:
        imprimir_campana(estado)
        return
    opciones = OpcionesJuego(semilla=args.semilla, detalle=args.detalle, interactivo=args.interactivo,
                             cada_mapa=args.mapa, max_pasos=args.max_pasos)
    while not estado.terminada:
        capitulo = estado.capitulo_actual
        assert capitulo is not None
        print(c(f"\nZeus prepara la misión {estado.capitulo + 1} de {estado.misiones_total}: {capitulo.titulo}...",
                "negrita"))
        mision = await zeus.preparar_mision(estado)
        for aviso in mision.avisos:
            print(c(f"  (Zeus corrige: {aviso})", "gris"))
        archivo.guardar_mision(estado, mision.escenario, mision.sucesos)
        informe, sim = await jugar_mision(config, estado, mision.escenario, mision.sucesos, zeus, llm, memoria,
                                          opciones)
        sucesos = sucesos_clave(sim.registro, mision.escenario.bando_jugadores)
        cierre = await zeus.cerrar_mision(estado, mision.escenario, informe.resultado, mision_id=informe.mision_id,
                                          resumen=informe.resumen, sucesos=sucesos)
        ruta = archivo.informe_mision(estado, mision.escenario, informe.resultado, cierre, sucesos=sucesos,
                                      lecciones=informe.lecciones_nuevas)
        archivo.ficha(estado)
        print(c("Crónica de Zeus:", "negrita"), cierre.cronica)
        print(c("Lo que cambia en la campaña:", "negrita"))
        for cambio in cierre.cambios or ["Nada especial: la historia sigue."]:
            print(f"  · {cambio}")
        print(f"Informe de la misión: {ruta}")
        if not args.todas:
            break
    _avisar_espacio(almacen.vigilante.comprobar())
    if estado.terminada:
        ruta = archivo.informe_final(estado, memoria.lore(estado.id))
        print()
        imprimir_campana(estado)
        print(f"Informe final de la campaña: {ruta}")
        ofrecer_borrado(estado.id, almacen)
    else:
        print(f"Siguiente misión: arma3-ia campana jugar {estado.id}")


def borrar_campana(id_campana: str, almacen: Almacen, *, carpeta: bool) -> None:
    antes = almacen.memoria.uso_por_campana().get(id_campana, 0.0)
    conteo = almacen.memoria.borrar_campana(id_campana)
    print(c(f"Campaña {id_campana} borrada de la base de datos (~{antes:.2f} MB): "
            + ", ".join(f"{n} {k}" for k, n in conteo.items()), "verde"))
    if carpeta and almacen.archivo.borrar(id_campana):
        print(c(f"Carpeta {almacen.archivo.de(id_campana)} borrada.", "verde"))
    elif not carpeta and almacen.archivo.de(id_campana).is_dir():
        print(f"Los informes siguen en {almacen.archivo.de(id_campana)}.")
    print("Lo que ha aprendido la IA (lecciones) se conserva.")
    _avisar_espacio(almacen.vigilante.comprobar())


def ofrecer_borrado(id_campana: str, almacen: Almacen) -> None:
    """Al acabar una campaña, pregunta si se borra de la base de datos para ganar espacio."""
    mb = almacen.memoria.uso_por_campana().get(id_campana, 0.0)
    if not puede_preguntar():
        print(f"La campaña ocupa ~{mb:.2f} MB en la base de datos. Para borrarla: arma3-ia campana borrar {id_campana}")
        return
    if not preguntar_si(f"La campaña ha terminado y ocupa ~{mb:.2f} MB. ¿Quieres eliminarla de la base de datos "
                        "para ganar espacio?"):
        print(f"Se conserva. Puedes borrarla cuando quieras con: arma3-ia campana borrar {id_campana}")
        return
    carpeta = preguntar_si(f"¿Borrar también sus informes de texto de {almacen.archivo.de(id_campana)}? "
                           "(ocupan muy poco)")
    borrar_campana(id_campana, almacen, carpeta=carpeta)


async def orden_campana(args: argparse.Namespace, config: Config) -> int:
    almacen = abrir_almacen(config)
    memoria = almacen.memoria
    llm = _llm(config, getattr(args, "falso", False))
    zeus = crear_zeus(config, llm, memoria, getattr(args, "semilla", None))
    try:
        if args.accion == "nueva":
            await _campana_nueva(args, config, zeus, almacen)
        elif args.accion == "jugar":
            await _campana_jugar(args, config, zeus, llm, almacen)
        elif args.accion == "estado":
            imprimir_campana(_campana_elegida(zeus, memoria, args.id))
        elif args.accion == "lista":
            filas = memoria.campanas()
            if not filas:
                print("No hay campañas. Pide una con: arma3-ia campana nueva \"...\"")
            uso = memoria.uso_por_campana()
            for fila in filas:
                estado = zeus.cargar(fila["id"])
                print(f"{fila['id']:32s} {estado.plan.nombre} — {estado.estado_texto} "
                      f"({uso.get(fila['id'], 0.0):.2f} MB)")
        elif args.accion == "borrar":
            elegida = {f["id"]: f for f in memoria.campanas()}.get(args.id)
            if elegida is None:
                raise KeyError(f"No existe la campaña '{args.id}'.")
            if not args.si:
                if not puede_preguntar():
                    raise KeyError("Para borrar sin preguntar añade --si.")
                if not preguntar_si(f"¿Seguro que quieres borrar «{elegida['nombre']}» ({elegida['estado']})?"):
                    print("No se ha borrado nada.")
                    return 0
            borrar_campana(args.id, almacen, carpeta=not args.conservar_informes)
    finally:
        await llm.cerrar()
        almacen.cerrar()
    return 0


# ---------------------------------------------------------------------------------- consultas
async def orden_espacio(args: argparse.Namespace, config: Config) -> int:
    almacen = abrir_almacen(config)
    try:
        estado = almacen.vigilante.comprobar()
        print(c(estado.mensaje(), NIVEL_COLOR[estado.nivel]))
        print(f"Base de datos: {config.ruta_bbdd} | campañas: {config.carpeta_campanas}")
        uso = almacen.memoria.uso_por_campana()
        for fila in almacen.memoria.campanas():
            terminada = "" if fila["estado"] == "en_curso" else c(" (terminada: se puede borrar)", "amarillo")
            print(f"  {fila['id']:32s} {uso.get(fila['id'], 0.0):8.2f} MB  {fila['nombre']}{terminada}")
    finally:
        almacen.cerrar()
    return 0 if estado.nivel != "critico" else 3


async def orden_lecciones(args: argparse.Namespace, config: Config) -> int:
    almacen = abrir_almacen(config)
    lecciones = almacen.memoria.lecciones(rol=args.rol, epoca=args.epoca, solo_activas=not args.todas)
    if not lecciones:
        print("Todavía no hay lecciones: se aprenden jugando misiones.")
    for leccion in sorted(lecciones, key=lambda l: (l.rol, -l.peso)):  # noqa: E741
        archivada = "" if leccion.activa else c(" [archivada]", "gris")
        print(f"#{leccion.id:<4d} [{leccion.rol:10s}] {leccion.epoca or 'general':22s} peso {leccion.peso:.2f} "
              f"usada {leccion.veces_usada} (éxito {leccion.utilidad:.0%}){archivada}")
        print(f"      {'⚠' if leccion.tipo == 'error' else '✓'} {leccion.texto()}")
    almacen.cerrar()
    return 0


async def orden_entrenar_vuelo(args: argparse.Namespace, config: Config) -> int:
    almacen = abrir_almacen(config)
    habilidad = HabilidadAterrizaje(almacen.memoria, random.Random(args.semilla))
    antes = {ctx: evaluar(habilidad.parametros(ctx == "caliente"), ctx == "caliente", 1000, random.Random(7))
             for ctx in ("fria", "caliente")}
    print(c(f"Practicando {args.episodios} aterrizajes simulados...", "negrita"))
    progreso = habilidad.entrenar(args.episodios)
    for p in progreso[:: max(1, len(progreso) // 10)]:
        print(f"  generación {p['generacion']:3d} LZ {p['contexto']:8s} puntuación media {p['puntuacion_media']:.2f}")
    for ctx in ("fria", "caliente"):
        parametros = habilidad.parametros(ctx == "caliente")
        despues = evaluar(parametros, ctx == "caliente", 1000, random.Random(7))
        print(c(f"LZ {ctx}: éxito {antes[ctx]['exito']:.0%} → {despues['exito']:.0%}, accidentes "
                f"{antes[ctx]['accidentes']:.0%} → {despues['accidentes']:.0%}", "verde"))
        print("   parámetros: " + ", ".join(f"{k}={v:.1f}" for k, v in parametros.items()))
    print(f"(novato: {', '.join(f'{k}={v}' for k, v in PARAMETROS_POR_DEFECTO.items())})")
    almacen.cerrar()
    return 0


async def orden_exportar(args: argparse.Namespace, config: Config) -> int:
    salida = config.ruta(args.salida)
    almacen = abrir_almacen(config)
    n = almacen.memoria.exportar_dataset(salida, puntuacion_minima=args.minimo, solo_llm=not args.incluir_reglas)
    almacen.cerrar()
    print(f"{n} ejemplos exportados a {salida}")
    return 0


# ---------------------------------------------------------------------------------- argparse
def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arma3-ia", description="IA local para ARMA 3: Zeus y NPC autónomos")
    parser.add_argument("--config", help="fichero de configuración YAML")
    parser.add_argument("--version", action="version", version=f"arma3-ia {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="registro detallado")
    sub = parser.add_subparsers(dest="orden", required=True)

    p = sub.add_parser("comprobar", help="comprueba Ollama, el modelo, la base de datos y el espacio")
    p.add_argument("--rapido", action="store_true", help="no hace la prueba de generación")
    p.set_defaults(funcion=orden_comprobar)

    p = sub.add_parser("campana", help="tus campañas: las pides tú, Zeus hace todo lo demás")
    acciones = p.add_subparsers(dest="accion", required=True)
    q = acciones.add_parser("nueva", help="pide una campaña nueva a Zeus")
    q.add_argument("encargo", nargs="+", help='p. ej. "campaña de 1944 para volar un puente, 5 misiones de 90 minutos"')
    q.add_argument("--misiones", type=int, help="número de misiones (si no, lo saca del encargo o pregunta)")
    q.add_argument("--duracion", type=int, metavar="MIN", help="minutos por misión (si no, del encargo o pregunta)")
    q.add_argument("--falso", action="store_true", help="usa el LLM falso (sin GPU ni Ollama)")
    q.add_argument("--semilla", type=int)
    q = acciones.add_parser("jugar", help="Zeus prepara, dirige y cierra la siguiente misión")
    q.add_argument("id", nargs="?", help="id de la campaña (por defecto la última en curso)")
    q.add_argument("--todas", action="store_true", help="juega todas las misiones que quedan seguidas")
    q.add_argument("--falso", action="store_true")
    q.add_argument("--semilla", type=int)
    q.add_argument("--interactivo", action="store_true", help="habla por radio con los NPC entre pasos")
    q.add_argument("--mapa", type=int, default=0, metavar="N", help="muestra el mapa cada N pasos")
    q.add_argument("--detalle", action="store_true", help="muestra todos los eventos")
    q.add_argument("--max-pasos", type=int, help="corta cada misión tras N pasos (para probar)")
    q = acciones.add_parser("estado", help="pasos, personajes e historia de una campaña")
    q.add_argument("id", nargs="?")
    acciones.add_parser("lista", help="campañas guardadas y lo que ocupan")
    q = acciones.add_parser("borrar", help="borra una campaña de la base de datos (y sus informes)")
    q.add_argument("id")
    q.add_argument("--si", action="store_true", help="no pregunta")
    q.add_argument("--conservar-informes", action="store_true", help="deja sus textos en CAMPAÑAS/")
    p.set_defaults(funcion=orden_campana)

    sub.add_parser("espacio", help="lo que ocupa la base de datos y cada campaña").set_defaults(funcion=orden_espacio)

    p = sub.add_parser("lecciones", help="lo que ha aprendido la IA")
    p.add_argument("--rol", choices=["zeus", "comandante", "lider", "piloto", "enlace"])
    p.add_argument("--epoca", help="segunda_guerra_mundial, guerra_fria, moderna o futura")
    p.add_argument("--todas", action="store_true", help="incluye las archivadas")
    p.set_defaults(funcion=orden_lecciones)

    p = sub.add_parser("entrenar-vuelo", help="practica aterrizajes de helicóptero simulados")
    p.add_argument("--episodios", type=int, default=3000)
    p.add_argument("--semilla", type=int)
    p.set_defaults(funcion=orden_entrenar_vuelo)

    p = sub.add_parser("exportar-dataset", help="exporta decisiones de misiones buenas en JSONL (para LoRA)")
    p.add_argument("salida", nargs="?", default="BBDD/dataset.jsonl")
    p.add_argument("--minimo", type=float, default=0.6, help="puntuación mínima de la misión")
    p.add_argument("--incluir-reglas", action="store_true", help="incluye decisiones tomadas por reglas")
    p.set_defaults(funcion=orden_exportar)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = crear_parser().parse_args(argv)
    config = cargar_config(args.config)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else getattr(logging, config.nivel_registro.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    funcion: Any = args.funcion
    try:
        return int(asyncio.run(funcion(args, config)))
    except KeyboardInterrupt:
        return 130
    except (KeyError, FileNotFoundError, ErrorBaseDatos) as error:
        print(c(f"Error: {error}", "rojo"), file=sys.stderr)
        return 1
