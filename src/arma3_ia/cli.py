"""Línea de órdenes: ``arma3-ia <orden>`` (o ``python -m arma3_ia <orden>``).

Órdenes principales:

- ``comprobar``          comprueba Ollama, el modelo y la base de datos.
- ``simular``            juega una misión en el simulador de texto.
- ``autoentrenar``       encadena misiones para que la IA aprenda sola (déjala trabajando).
- ``entrenar-vuelo``     practica aterrizajes de helicóptero miles de veces.
- ``chat``               habla con el operador de radio de la IA fuera de una misión.
- ``lecciones``          muestra lo que ha aprendido.
- ``perfiles`` / ``escenarios`` / ``memoria`` / ``exportar-dataset``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from collections.abc import Sequence
from typing import Any

from . import __version__
from .campanas import PerfilCampana, cargar_perfil, perfiles_disponibles
from .config import Config, cargar_config
from .habilidades import PARAMETROS_POR_DEFECTO, HabilidadAterrizaje, Habilidades, evaluar
from .llm import ClienteLLM, ClienteOllama, LLMFalso, MensajeLLM, crear_cliente, generar_estructurado
from .mando import CerebroBando, SalidaCerebro
from .memoria import Memoria, Reflexion
from .modelo import MensajeChat, Observacion
from .partida import InformePartida, Partida, preparar_cerebros
from .simulador import Escenario, SimuladorTexto, cargar_escenario, escenarios_disponibles, mapa_ascii
from .texto import recortar, reloj
from .zeus import DirectorPartida, EstadoCampana, Zeus

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
COLORES = {"gris": "90", "rojo": "31", "verde": "32", "amarillo": "33", "azul": "34", "cian": "36", "negrita": "1"}


def c(texto: str, color: str) -> str:
    return f"\033[{COLORES[color]}m{texto}\033[0m" if COLOR else texto


def _llm(config: Config, falso: bool) -> ClienteLLM:
    return LLMFalso() if falso else crear_cliente(config.llm)


def _memoria(config: Config, sin_memoria: bool) -> Memoria:
    return Memoria(":memory:" if sin_memoria else config.memoria.ruta_db)


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
            return 1
        print(c(f"✓ Ollama {informe.version} responde", "verde"))
        if not informe.modelo_disponible:
            print(c(f"✗ El modelo {config.llm.modelo} no está descargado.", "rojo"))
            print(f"  Descárgalo con: ollama pull {config.llm.modelo}")
            print(f"  Modelos instalados: {', '.join(informe.modelos) or 'ninguno'}")
            return 1
        print(c(f"✓ Modelo {config.llm.modelo} disponible", "verde"))
        if not args.rapido:
            from .agentes import RespuestaEnlace

            inicio = time.monotonic()
            respuesta, bruta = await generar_estructurado(
                cliente,
                [
                    MensajeLLM("system", "Eres un operador de radio militar. Responde en JSON."),
                    MensajeLLM("user", "Confirma que recibes la transmisión en una frase."),
                ],
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
    except Exception as error:
        print(c(f"✗ Error durante la comprobación: {error}", "rojo"))
        estado = 1
    finally:
        await cliente.cerrar()
    try:
        memoria = Memoria(config.memoria.ruta_db)
        print(c(f"✓ Memoria en {config.memoria.ruta_db}: {memoria.estadisticas()}", "verde"))
        memoria.cerrar()
    except Exception as error:
        print(c(f"✗ No se puede abrir la memoria {config.memoria.ruta_db}: {error}", "rojo"))
        estado = 1
    return estado


# ---------------------------------------------------------------------------------- simular
def _imprimir_paso(sim: SimuladorTexto, salidas: dict[str, SalidaCerebro], detalle: bool) -> None:
    for salida in salidas.values():
        for e in salida.eventos:
            color = "rojo" if e.tipo in ("bajas", "unidad_destruida", "objetivo_fallido") else (
                "verde" if e.tipo in ("objetivo_completado", "enemigo_abatido", "contacto_destruido") else "gris")
            if detalle or color != "gris" or e.tipo in ("contacto", "aterrizaje", "embarque", "desembarco"):
                print(c(f"[{reloj(e.tiempo)}] {e.descripcion}", color))
        for m in salida.chat:
            if m.canal == "radio":
                print(c(f"[{reloj(m.tiempo)}] 📻 {m.autor} {m.texto}", "cian"))
            else:
                print(c(f"[{reloj(m.tiempo)}] 💬 {m.autor}: {m.texto}", "amarillo"))


def _imprimir_fuerzas(escenario: Escenario) -> None:
    for nombre, bando in escenario.bandos.items():
        if not any(u.persona or u.orden for u in bando.unidades):
            continue
        cabecera = f"{bando.nombre or nombre} ({nombre})"
        cabecera += "" if bando.mando else " — sin mando: cada grupo va a su aire"
        print(c(cabecera, "negrita"))
        if bando.comandante:
            print(f"   Al mando: {bando.comandante.presentacion()}")
        for u in bando.unidades:
            quien = f" — {u.persona.presentacion()}" if u.persona else (" — jugadores" if u.jugadores else "")
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
    print(f"Decisiones: {informe.decisiones} ({informe.estadisticas})")
    print(c("Informe post-acción:", "negrita"), informe.resumen)
    for leccion in informe.lecciones_nuevas:
        print(c(f"  + Nueva lección ({leccion.rol}): {leccion.texto()}", "verde"))
    for leccion in informe.lecciones_reforzadas:
        print(c(f"  ↑ Lección reforzada ({leccion.rol}): {leccion.texto()}", "azul"))
    if informe.lecciones_archivadas:
        print(c(f"  - Lecciones olvidadas por inútiles: {informe.lecciones_archivadas}", "gris"))


async def _jugar_mision(
    config: Config,
    escenario_id: str | Escenario,
    perfil: PerfilCampana | None,
    llm: ClienteLLM,
    memoria: Memoria,
    *,
    semilla: int | None,
    explorar: bool,
    reflexionar: bool,
    mostrar: bool = True,
    detalle: bool = False,
    interactivo: bool = False,
    cada_mapa: int = 0,
    max_pasos: int | None = None,
    zeus: Zeus | None = None,
    zeus_automatico: bool = True,
) -> InformePartida:
    escenario = escenario_id if isinstance(escenario_id, Escenario) else cargar_escenario(escenario_id)
    perfil = perfil or cargar_perfil(escenario.perfil_recomendado)
    habilidades = Habilidades(memoria, explorar=explorar, semilla=semilla)
    sim = SimuladorTexto(escenario, perfil, semilla=semilla)
    cerebros = preparar_cerebros(escenario, llm, perfil, config, memoria, habilidades)
    director = (
        DirectorPartida(zeus, sim, cerebros, escenario, perfil, automatico=zeus_automatico, semilla=semilla)
        if zeus is not None else None
    )
    vistos = {"anuncios": 0}
    reflexion = (
        Reflexion(llm, memoria, perfil, umbral_exito=config.memoria.umbral_exito)
        if reflexionar and config.memoria.reflexion
        else None
    )

    async def al_paso(paso: int, salidas: dict[str, SalidaCerebro]) -> None:
        if not mostrar:
            return
        _imprimir_paso(sim, salidas, detalle)
        for anuncio in sim.anuncios[vistos["anuncios"]:]:
            print(c(f"[{reloj(anuncio.tiempo)}] ⚡ Zeus: {anuncio.texto}", "azul"))
        vistos["anuncios"] = len(sim.anuncios)
        if cada_mapa and paso % cada_mapa == 0:
            print(mapa_ascii(sim, sim.bando_principal))
        if interactivo and not sim.terminado():
            texto = await asyncio.to_thread(
                input, c("Tú (Enter = seguir, 'mapa', 'salir', 'Zeus, ...' o habla con un NPC): ", "negrita"))
            texto = texto.strip()
            if texto.lower() == "salir":
                sim.finalizar("interrumpida por el jugador")
            elif texto.lower() == "mapa":
                print(mapa_ascii(sim, sim.bando_principal))
            elif texto:
                sim.inyectar_chat(sim.bando_principal, "Jugador", texto)

    if mostrar:
        print(c(f"=== {escenario.nombre} | campaña: {perfil.nombre} | modelo: {llm.nombre_modelo} ===", "negrita"))
        print(escenario.descripcion.strip())
        _imprimir_fuerzas(escenario)
        print(mapa_ascii(sim, sim.bando_principal))
    partida = Partida(sim, cerebros, memoria=memoria, reflexion=reflexion, habilidades=habilidades,
                      campana=perfil.id, escenario=escenario.id, max_pasos=max_pasos, al_paso=al_paso,
                      director=director)
    informe = await partida.jugar()
    if mostrar:
        print(mapa_ascii(sim))
        _imprimir_informe(informe)
    return informe


async def orden_simular(args: argparse.Namespace, config: Config) -> int:
    perfil = cargar_perfil(args.perfil) if args.perfil else None
    llm = _llm(config, args.falso)
    memoria = _memoria(config, args.sin_memoria)
    try:
        informe = await _jugar_mision(
            config, args.escenario, perfil, llm, memoria,
            semilla=args.semilla, explorar=config.habilidades.explorar, reflexionar=not args.sin_reflexion,
            detalle=args.detalle, interactivo=args.interactivo, cada_mapa=args.mapa, max_pasos=args.max_pasos,
        )
    finally:
        await llm.cerrar()
        memoria.cerrar()
    return 0 if informe.resultado.estado != "derrota" else 2


# ---------------------------------------------------------------------------------- autoentrenar
async def orden_autoentrenar(args: argparse.Namespace, config: Config) -> int:
    escenarios = args.escenarios or list(escenarios_disponibles())
    perfil = cargar_perfil(args.perfil) if args.perfil else None
    llm = _llm(config, args.falso)
    memoria = _memoria(config, args.sin_memoria)
    rng = random.Random(args.semilla)
    puntuaciones: list[float] = []
    print(c(f"Autoentrenamiento: {args.misiones} misiones en {', '.join(escenarios)}", "negrita"))
    if args.vuelo:
        HabilidadAterrizaje(memoria, random.Random(args.semilla)).entrenar(args.vuelo)
        print(f"Práctica de vuelo previa: {args.vuelo} aterrizajes simulados.")
    try:
        for n in range(1, args.misiones + 1):
            escenario = escenarios[(n - 1) % len(escenarios)]
            inicio = time.monotonic()
            informe = await _jugar_mision(config, escenario, perfil, llm, memoria,
                                          semilla=rng.randrange(1_000_000), explorar=True,
                                          reflexionar=True, mostrar=False)
            r = informe.resultado
            puntuaciones.append(r.puntuacion)
            color = "verde" if r.estado == "victoria" else "amarillo" if r.estado == "parcial" else "rojo"
            print(c(f"#{n:03d} {escenario:22s} {r.estado:9s} punt {r.puntuacion:.2f} bajas "
                    f"{r.bajas_propias:2d}/{r.efectivos_iniciales:<2d} lecciones +{len(informe.lecciones_nuevas)} "
                    f"↑{len(informe.lecciones_reforzadas)} ({time.monotonic() - inicio:.0f} s)", color))
    except KeyboardInterrupt:
        print("\nInterrumpido: lo aprendido hasta ahora ya está guardado.")
    finally:
        await llm.cerrar()
        estadisticas = memoria.estadisticas()
        memoria.cerrar()
    if len(puntuaciones) >= 4:
        mitad = len(puntuaciones) // 2
        antes = sum(puntuaciones[:mitad]) / mitad
        despues = sum(puntuaciones[mitad:]) / (len(puntuaciones) - mitad)
        print(c(f"Puntuación media: primera mitad {antes:.2f} → segunda mitad {despues:.2f}", "negrita"))
    print(f"Memoria: {estadisticas}")
    return 0


# ---------------------------------------------------------------------------------- vuelo
async def orden_entrenar_vuelo(args: argparse.Namespace, config: Config) -> int:
    memoria = _memoria(config, args.sin_memoria)
    habilidad = HabilidadAterrizaje(memoria, random.Random(args.semilla))
    antes = {
        ctx: evaluar(habilidad.parametros(ctx == "caliente"), ctx == "caliente", 1000, random.Random(7))
        for ctx in ("fria", "caliente")
    }
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
    memoria.cerrar()
    return 0


# ---------------------------------------------------------------------------------- chat
async def orden_chat(args: argparse.Namespace, config: Config) -> int:
    perfil = cargar_perfil(args.perfil or config.perfil)
    llm = _llm(config, args.falso)
    cerebro = CerebroBando("blufor", llm, perfil, config)
    obs = Observacion(tiempo=0.0, bando="blufor")
    print(c(f"Hablas con {perfil.indicativo_enlace} ({perfil.nombre}). Escribe 'salir' para terminar.", "negrita"))
    try:
        while True:
            texto = (await asyncio.to_thread(input, c("Tú: ", "negrita"))).strip()
            if texto.lower() in ("salir", "exit", "quit"):
                break
            if not texto:
                continue
            respuestas, _ = await cerebro.enlace.atender([MensajeChat(autor=args.nombre, texto=texto)], obs)
            for r in respuestas:
                print(c(f"{r.autor}: {r.texto}", "amarillo"))
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        await llm.cerrar()
    return 0


# ---------------------------------------------------------------------------------- campañas (Zeus)
def _campana_actual(zeus: Zeus, id_campana: str | None) -> EstadoCampana:
    if id_campana:
        return zeus.cargar(id_campana)
    assert zeus.memoria is not None
    campanas = zeus.memoria.campanas()
    if not campanas:
        raise KeyError("Todavía no hay campañas. Crea una con: arma3-ia campana nueva \"...\"")
    return zeus.cargar(campanas[0]["id"])


def _imprimir_campana(estado: EstadoCampana) -> None:
    plan = estado.plan
    print(c(f"=== {plan.nombre} ({estado.id}) ===", "negrita"))
    print(plan.premisa)
    print(f"Época: {plan.perfil} | {plan.nombre_jugadores} contra {plan.nombre_enemigo} | "
          f"dificultad {estado.dificultad:.2f}")
    for i, cap in enumerate(plan.capitulos):
        hecho = next((r for r in estado.historial if r.capitulo == i), None)
        marca = (c("✓", "verde") if hecho and hecho.estado == "victoria" else c("✗", "rojo") if hecho
                 else c("→", "amarillo") if i == estado.capitulo else " ")
        resultado = f" — {hecho.estado} ({hecho.puntuacion:.2f})" if hecho else ""
        print(f" {marca} {i + 1}. {cap.titulo} [{cap.tipo.value}]{resultado}")
        print(f"      {cap.sinopsis}")
    print(c("Personajes:", "negrita"))
    for p in estado.personajes:
        print(f"   {'' if p.vivo else '✝ '}{p.persona.presentacion()}")
    if estado.antagonista:
        print(c("Antagonista: ", "negrita") + estado.antagonista.presentacion())
    if estado.terminada:
        print(c("Campaña terminada.", "verde"))


async def orden_campana(args: argparse.Namespace, config: Config) -> int:
    llm = _llm(config, getattr(args, "falso", False))
    memoria = Memoria(config.memoria.ruta_db)
    zeus = Zeus(llm, memoria, semilla=getattr(args, "semilla", None))
    try:
        if args.accion == "nueva":
            print("Zeus está imaginando la campaña...")
            estado = await zeus.crear_campana(" ".join(args.encargo))
            _imprimir_campana(estado)
            print(f"\nPara jugar el primer capítulo: arma3-ia campana jugar {estado.id}")
        elif args.accion == "lista":
            for fila in memoria.campanas():
                estado = zeus.cargar(fila["id"])
                total = len(estado.plan.capitulos)
                print(f"{fila['id']:32s} {estado.plan.nombre} — capítulo {min(estado.capitulo + 1, total)}/{total}"
                      f"{' (terminada)' if estado.terminada else ''}")
        elif args.accion == "estado":
            _imprimir_campana(_campana_actual(zeus, args.id))
        elif args.accion == "jugar":
            estado = _campana_actual(zeus, args.id)
            while not estado.terminada:
                capitulo = estado.capitulo_actual
                assert capitulo is not None
                print(c(f"\nZeus prepara el capítulo {estado.capitulo + 1}: {capitulo.titulo}...", "negrita"))
                mision = await zeus.preparar_mision(estado)
                for aviso in mision.avisos:
                    print(c(f"  (Zeus corrige: {aviso})", "gris"))
                informe = await _jugar_mision(
                    config, mision.escenario, zeus.perfil(estado), llm, memoria,
                    semilla=args.semilla, explorar=config.habilidades.explorar, reflexionar=True,
                    detalle=args.detalle, interactivo=args.interactivo, cada_mapa=args.mapa,
                    max_pasos=args.max_pasos, zeus=zeus, zeus_automatico=not args.sin_intervenciones,
                )
                cambios = await zeus.cerrar_mision(estado, mision.escenario, informe.resultado,
                                                   mision_id=informe.mision_id, resumen=informe.resumen)
                print(c("Lo que cambia en la campaña:", "negrita"))
                for cambio in cambios or ["Nada especial: la historia sigue."]:
                    print(f"  · {cambio}")
                if not args.todas:
                    break
            if estado.terminada:
                _imprimir_campana(estado)
    finally:
        await llm.cerrar()
        memoria.cerrar()
    return 0


# ---------------------------------------------------------------------------------- consultas
async def orden_lecciones(args: argparse.Namespace, config: Config) -> int:
    memoria = Memoria(config.memoria.ruta_db)
    lecciones = memoria.lecciones(rol=args.rol, campana=args.campana, solo_activas=not args.todas)
    if not lecciones:
        print("Todavía no hay lecciones. Juega o autoentrena alguna misión.")
    for leccion in sorted(lecciones, key=lambda l: (l.rol, -l.peso)):  # noqa: E741
        estado = "" if leccion.activa else c(" [archivada]", "gris")
        print(f"#{leccion.id:<4d} [{leccion.rol:10s}] {leccion.campana or 'general':18s} peso {leccion.peso:.2f} "
              f"usada {leccion.veces_usada} (éxito {leccion.utilidad:.0%}){estado}")
        print(f"      {'⚠' if leccion.tipo == 'error' else '✓'} {leccion.texto()}")
    memoria.cerrar()
    return 0


async def orden_perfiles(args: argparse.Namespace, config: Config) -> int:
    for perfil in perfiles_disponibles().values():
        aereo = "con aviación" if perfil.medios_aereos else "sin aviación"
        print(f"{c(perfil.id, 'negrita'):20s} {perfil.nombre} — {perfil.epoca} ({aereo})")
        print(f"    {recortar(perfil.descripcion, 110)}")
    return 0


async def orden_escenarios(args: argparse.Namespace, config: Config) -> int:
    for escenario in escenarios_disponibles().values():
        print(f"{c(escenario.id, 'negrita'):24s} {escenario.nombre} (perfil {escenario.perfil_recomendado}, "
              f"{int(escenario.duracion_max_s // 60)} min)")
        print(f"    {recortar(escenario.descripcion, 110)}")
    return 0


async def orden_memoria(args: argparse.Namespace, config: Config) -> int:
    memoria = Memoria(config.memoria.ruta_db)
    print(c(f"Memoria {config.memoria.ruta_db}", "negrita"), json.dumps(memoria.estadisticas(), ensure_ascii=False))
    for m in memoria.misiones(limite=args.limite):
        print(f"#{m['id']:<4d} {m['inicio']} {m['escenario']:22s} {m['campana']:18s} {m['estado'] or 'en curso':9s} "
              f"punt {m['puntuacion'] if m['puntuacion'] is not None else '-'}")
        if m["resumen"] and args.resumen:
            print(f"      {m['resumen']}")
    memoria.cerrar()
    return 0


async def orden_exportar(args: argparse.Namespace, config: Config) -> int:
    memoria = Memoria(config.memoria.ruta_db)
    n = memoria.exportar_dataset(args.salida, puntuacion_minima=args.minimo, solo_llm=not args.incluir_reglas)
    memoria.cerrar()
    print(f"{n} ejemplos exportados a {args.salida}")
    return 0


# ---------------------------------------------------------------------------------- argparse
def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arma3-ia", description="IA local multiagente para ARMA 3")
    parser.add_argument("--config", help="fichero de configuración YAML")
    parser.add_argument("--version", action="version", version=f"arma3-ia {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="registro detallado")
    sub = parser.add_subparsers(dest="orden", required=True)

    p = sub.add_parser("comprobar", help="comprueba Ollama, el modelo y la memoria")
    p.add_argument("--rapido", action="store_true", help="no hace la prueba de generación")
    p.set_defaults(funcion=orden_comprobar)

    def comunes(p: argparse.ArgumentParser) -> None:
        p.add_argument("--perfil", help="perfil de campaña (por defecto el del escenario)")
        p.add_argument("--falso", action="store_true", help="usa el LLM falso (sin GPU ni Ollama)")
        p.add_argument("--sin-memoria", action="store_true", help="no guarda nada en disco")
        p.add_argument("--semilla", type=int, help="semilla para repetir exactamente la partida")

    p = sub.add_parser("simular", help="juega una misión en el simulador de texto")
    p.add_argument("escenario", nargs="?", default="asalto_aldea", help="id del escenario o ruta a un YAML")
    comunes(p)
    p.add_argument("--interactivo", action="store_true", help="escribe en el chat como un jugador entre pasos")
    p.add_argument("--mapa", type=int, default=0, metavar="N", help="muestra el mapa cada N pasos")
    p.add_argument("--detalle", action="store_true", help="muestra todos los eventos")
    p.add_argument("--sin-reflexion", action="store_true", help="no analiza la misión al terminar")
    p.add_argument("--max-pasos", type=int, help="detiene la misión tras N pasos")
    p.set_defaults(funcion=orden_simular)

    p = sub.add_parser("campana", help="Zeus: crea y dirige campañas completas")
    acciones = p.add_subparsers(dest="accion", required=True)
    q = acciones.add_parser("nueva", help="crea una campaña a partir de un encargo")
    q.add_argument("encargo", nargs="+", help='p. ej. "una campaña de 1944 sobre la defensa de un puente"')
    q.add_argument("--falso", action="store_true", help="usa el LLM falso (sin GPU ni Ollama)")
    q.add_argument("--semilla", type=int)
    q = acciones.add_parser("jugar", help="juega el siguiente capítulo (Zeus lo prepara y lo dirige)")
    q.add_argument("id", nargs="?", help="id de la campaña (por defecto la última)")
    q.add_argument("--todas", action="store_true", help="juega todos los capítulos que quedan")
    q.add_argument("--falso", action="store_true")
    q.add_argument("--semilla", type=int)
    q.add_argument("--interactivo", action="store_true",
                   help="escribe en el chat entre pasos: 'Zeus, que llueva' o 'Baker, ¿cómo vais?'")
    q.add_argument("--mapa", type=int, default=0, metavar="N")
    q.add_argument("--detalle", action="store_true")
    q.add_argument("--max-pasos", type=int)
    q.add_argument("--sin-intervenciones", action="store_true", help="Zeus solo actúa cuando se lo pides")
    q = acciones.add_parser("estado", help="capítulos, personajes e historial de una campaña")
    q.add_argument("id", nargs="?")
    acciones.add_parser("lista", help="campañas guardadas")
    p.set_defaults(funcion=orden_campana)

    p = sub.add_parser("autoentrenar", help="encadena misiones para que la IA aprenda sola")
    p.add_argument("--misiones", type=int, default=10)
    p.add_argument("--escenarios", nargs="*", help="escenarios a usar (por defecto todos)")
    p.add_argument("--vuelo", type=int, default=0, metavar="N", help="practica N aterrizajes antes de empezar")
    comunes(p)
    p.set_defaults(funcion=orden_autoentrenar)

    p = sub.add_parser("entrenar-vuelo", help="practica aterrizajes de helicóptero simulados")
    p.add_argument("--episodios", type=int, default=3000)
    p.add_argument("--semilla", type=int)
    p.add_argument("--sin-memoria", action="store_true")
    p.set_defaults(funcion=orden_entrenar_vuelo)

    p = sub.add_parser("chat", help="habla con el operador de radio de la IA")
    p.add_argument("--perfil")
    p.add_argument("--nombre", default="Jugador", help="tu nombre en el chat")
    p.add_argument("--falso", action="store_true")
    p.set_defaults(funcion=orden_chat)

    p = sub.add_parser("lecciones", help="muestra lo que la IA ha aprendido")
    p.add_argument("--rol", choices=["comandante", "lider", "piloto", "enlace"])
    p.add_argument("--campana")
    p.add_argument("--todas", action="store_true", help="incluye las archivadas")
    p.set_defaults(funcion=orden_lecciones)

    sub.add_parser("perfiles", help="lista los perfiles de campaña").set_defaults(funcion=orden_perfiles)
    sub.add_parser("escenarios", help="lista los escenarios del simulador").set_defaults(funcion=orden_escenarios)

    p = sub.add_parser("memoria", help="estadísticas y últimas misiones")
    p.add_argument("--limite", type=int, default=15)
    p.add_argument("--resumen", action="store_true", help="muestra el informe de cada misión")
    p.set_defaults(funcion=orden_memoria)

    p = sub.add_parser("exportar-dataset", help="exporta decisiones exitosas en JSONL (para LoRA)")
    p.add_argument("salida", nargs="?", default="datos/dataset.jsonl")
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
    except (KeyError, FileNotFoundError) as error:
        print(c(f"Error: {error}", "rojo"), file=sys.stderr)
        return 1
