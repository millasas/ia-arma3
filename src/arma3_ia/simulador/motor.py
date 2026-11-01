"""Simulador táctico de texto: un "ARMA 3 de bolsillo" para probar la IA sin el juego.

Modela lo justo para que las decisiones importen: movimiento por tipo y postura, detección
con niebla de guerra (alcance, terreno, postura, visibilidad), combate con cobertura,
sorpresa y moral, munición, helicópteros con combustible, embarque y desembarque,
aterrizajes (con la habilidad aprendida), el decorado que coloca Zeus (fortificaciones,
minas, barricadas, humo...) y objetivos de misión. Todos los bandos los lleva la IA.

Es determinista con una semilla, así que los tests pueden reproducir una partida exacta.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from ..geo import Posicion, describir_relativa
from ..habilidades.aterrizaje import CondicionesAterrizaje, simular_aterrizaje
from ..modelo import (
    ACCIONES_AIRE,
    ACCIONES_TIERRA,
    COBERTURA_OBJETO,
    NOMBRE_TIPO,
    PELIGROS,
    Accion,
    Altitud,
    Contacto,
    EstadoObjetivo,
    EstadoUnidad,
    Evento,
    GrupoGlobal,
    GrupoNuevo,
    MensajeChat,
    Objetivo,
    ObjetoMapa,
    Observacion,
    Postura,
    ResultadoAccion,
    ResultadoMision,
    TipoAccion,
    TipoObjetivo,
    TipoObjeto,
    TipoUnidad,
    UnidadPropia,
    VistaGlobal,
)
from ..puente.interfaz import ControlZeus, Mundo
from .escenario import Escenario, UnidadEscenario

# Velocidades en m/s
VELOCIDAD = {
    TipoUnidad.INFANTERIA: 1.4,
    TipoUnidad.MOTORIZADA: 9.0,
    TipoUnidad.BLINDADO: 6.0,
    TipoUnidad.HELICOPTERO: 55.0,
}
FACTOR_POSTURA = {Postura.SIGILO: 0.55, Postura.NORMAL: 1.0, Postura.COMBATE: 1.35}
ALCANCE_ARMAS = {
    TipoUnidad.INFANTERIA: 400.0,
    TipoUnidad.MOTORIZADA: 600.0,
    TipoUnidad.BLINDADO: 1200.0,
    TipoUnidad.HELICOPTERO: 1000.0,
}
ALCANCE_DETECCION = {
    TipoUnidad.INFANTERIA: 500.0,
    TipoUnidad.MOTORIZADA: 450.0,
    TipoUnidad.BLINDADO: 400.0,
    TipoUnidad.HELICOPTERO: 1500.0,
}
PROB_IMPACTO = {
    TipoUnidad.INFANTERIA: 0.08,
    TipoUnidad.MOTORIZADA: 0.10,
    TipoUnidad.BLINDADO: 0.20,
    TipoUnidad.HELICOPTERO: 0.18,
}
# Cada impacto daña los vehículos de tierra; a partir de INMOVILIZADO ya no se mueven (ruedas, motor).
DANO_VEHICULO = {TipoUnidad.MOTORIZADA: 0.25, TipoUnidad.BLINDADO: 0.05}
INMOVILIZADO = 0.5
COBERTURA_MAXIMA = 0.7
COBERTURA_TERRENO = {"urbano": 0.5, "bosque": 0.4, "colina": 0.25, "setos": 0.35}
OCULTACION_TERRENO = {"urbano": 0.6, "bosque": 0.5, "colina": 0.85, "setos": 0.6}
DETECTABILIDAD_POSTURA = {Postura.SIGILO: 0.5, Postura.NORMAL: 1.0, Postura.COMBATE: 1.25}
ALTURA_VUELO = {Altitud.BAJA: 60.0, Altitud.MEDIA: 150.0, Altitud.ALTA: 400.0}
CONSUMO_COMBUSTIBLE_S = 0.01 / 30.0
OLVIDO_CONTACTO_S = 300.0
RADIO_ZONA_CALIENTE = 500.0
DISTANCIA_EMBARQUE = 80.0
VISIBILIDAD_HORA = {"dia": 1500.0, "amanecer": 1000.0, "atardecer": 1000.0, "noche": 500.0}
FACTOR_CLIMA = {"despejado": 1.0, "nubes": 0.9, "lluvia": 0.7, "niebla": 0.4}
# Decorado: cuánto frena cada obstáculo (vehículos, infantería) y cuánto se ve dentro del humo.
FRENO_OBJETO = {TipoObjeto.BARRICADA: (0.3, 1.0), TipoObjeto.ESCOMBROS: (0.4, 0.9), TipoObjeto.ALAMBRADA: (1.0, 0.5)}
FACTOR_HUMO = 0.6
FACTOR_TORRE = 1.3
PROB_MINA = 0.3
VISIBLE_ENEMIGO_M = 600.0


@dataclass
class UnidadSim:
    id: str
    indicativo: str
    bando: str
    tipo: TipoUnidad
    pos: Posicion
    efectivos: int
    efectivos_max: int
    municion: float = 1.0
    moral: float = 1.0
    estado: EstadoUnidad = EstadoUnidad.ACTIVA
    postura: Postura = Postura.NORMAL
    accion: TipoAccion | None = None
    destino: Posicion | None = None
    objetivo: str | None = None
    parametros: dict[str, float] = field(default_factory=dict)
    altitud: Altitud = Altitud.MEDIA
    en_vuelo: bool = False
    combustible: float = 1.0
    danos: float = 0.0
    armada: bool = False
    capacidad: int = 0
    pasajeros: list[str] = field(default_factory=list)
    embarcada_en: str | None = None
    intentos_aterrizaje: int = 0
    # Estado de combate
    bajo_fuego: bool = False
    bajo_fuego_previo: bool = False
    en_contacto: bool = False
    disparando: bool = False
    avisos: set[str] = field(default_factory=set)
    stats: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))

    @property
    def es_aerea(self) -> bool:
        return self.tipo.es_aerea

    @property
    def operativa(self) -> bool:
        return self.estado != EstadoUnidad.DESTRUIDA

    @property
    def en_suelo(self) -> bool:
        """Presente físicamente sobre el terreno (no embarcada ni volando)."""
        return self.operativa and self.estado != EstadoUnidad.EMBARCADA and not self.en_vuelo


@dataclass
class ContactoSim:
    etiqueta: str
    tipo: TipoUnidad
    pos: Posicion
    ultimo_t: float
    efectivos: int | None
    precision: float
    en_vuelo: bool
    identificado: bool


class SimuladorTexto(Mundo, ControlZeus):
    def __init__(self, escenario: Escenario, semilla: int | None = None) -> None:
        self.escenario = escenario
        self.rng = random.Random(semilla)
        self.tiempo = 0.0
        self.unidades: dict[str, UnidadSim] = {}
        self.objetivos: dict[str, list[Objetivo]] = {}
        self.contactos: dict[str, dict[str, ContactoSim]] = {}
        self._etiquetas: dict[str, dict[str, str]] = {}
        self.eventos: dict[str, list[Evento]] = {}
        self.registro: list[Evento] = []
        self.chat_entrante: dict[str, list[MensajeChat]] = {}
        self.chat_log: list[MensajeChat] = []
        self._control: defaultdict[str, float] = defaultdict(float)
        self._resultado: ResultadoMision | None = None
        self.bandos = list(escenario.bandos)
        if not self.bandos:
            raise ValueError("El escenario necesita al menos un bando")
        jugadores = escenario.bando_jugadores
        self.bando_principal = jugadores if jugadores in self.bandos else self.bandos[0]
        self.anuncios: list[MensajeChat] = []
        self.objetos: list[ObjetoMapa] = [o.model_copy() for o in escenario.objetos]

        for nombre, bando in escenario.bandos.items():
            self.objetivos[nombre] = [o.model_copy(deep=True) for o in bando.objetivos]
            self.contactos[nombre] = {}
            self._etiquetas[nombre] = {}
            self.eventos[nombre] = []
            self.chat_entrante[nombre] = []
            for u in bando.unidades:
                self.unidades[u.id] = self._crear_unidad(u, nombre, u.indicativo)
        self.efectivos_iniciales = {
            b: sum(u.efectivos for u in self._de(b) if not u.es_aerea) for b in self.bandos
        }

    def _crear_unidad(self, u: UnidadEscenario, bando: str, indicativo: str) -> UnidadSim:
        return UnidadSim(
            id=u.id,
            indicativo=indicativo,
            bando=bando,
            tipo=u.tipo,
            pos=u.posicion,
            efectivos=u.efectivos,
            efectivos_max=u.efectivos,
            municion=u.municion,
            postura=u.postura,
            armada=u.armada,
            capacidad=u.capacidad,
            combustible=u.combustible,
        )

    # ------------------------------------------------------------------ consultas
    def _de(self, bando: str) -> list[UnidadSim]:
        return [u for u in self.unidades.values() if u.bando == bando]

    def operativas(self, bando: str) -> list[UnidadSim]:
        return [u for u in self._de(bando) if u.operativa]

    def enemigas(self, bando: str) -> list[UnidadSim]:
        return [u for u in self.unidades.values() if u.bando != bando and u.operativa]

    def base(self, bando: str) -> Posicion | None:
        return self.escenario.bandos[bando].base

    def terreno(self, pos: Posicion) -> str:
        """Tipo de terreno en un punto (la referencia más pequeña que lo contiene)."""
        dentro = [r for r in self.escenario.referencias if r.posicion.distancia(pos) <= r.radio_m]
        return min(dentro, key=lambda r: r.radio_m).tipo if dentro else "abierto"

    def objetos_en(self, pos: Posicion, *tipos: TipoObjeto) -> list[ObjetoMapa]:
        """Objetos del decorado activos que cubren un punto (de los tipos indicados, o todos)."""
        return [o for o in self.objetos if o.activo and (not tipos or o.tipo in tipos)
                and o.posicion.distancia(pos) <= o.radio_m]

    def objetos_visibles(self, bando: str) -> list[ObjetoMapa]:
        """Lo que un bando sabe del decorado: lo suyo, lo neutral que no es una trampa, y lo enemigo cercano."""
        propias = [u.pos for u in self.operativas(bando) if u.estado != EstadoUnidad.EMBARCADA]
        visibles = []
        for o in self.objetos:
            if not o.activo:
                continue
            if o.bando == bando:
                visibles.append(o)
            elif o.tipo in PELIGROS:
                continue  # las trampas ajenas no se ven hasta que se pisan
            elif o.bando is None or any(p.distancia(o.posicion) <= VISIBLE_ENEMIGO_M for p in propias):
                visibles.append(o)
        return visibles

    def _evento(self, bando: str, tipo: str, descripcion: str, unidad: str | None = None,
                **datos: object) -> None:
        evento = Evento(tiempo=self.tiempo, tipo=tipo, descripcion=descripcion, unidad=unidad,
                        datos=dict(datos))
        self.registro.append(evento)
        self.eventos[bando].append(evento)

    def etiqueta(self, observador: str, objetivo_id: str) -> str:
        etiquetas = self._etiquetas[observador]
        if objetivo_id not in etiquetas:
            etiquetas[objetivo_id] = f"C{len(etiquetas) + 1}"
        return etiquetas[objetivo_id]

    def _resolver_unidad(self, bando: str, texto: str | None) -> UnidadSim | None:
        if not texto:
            return None
        if texto in self.unidades and self.unidades[texto].bando == bando:
            return self.unidades[texto]
        bajo = texto.lower()
        return next(
            (u for u in self._de(bando) if u.indicativo.lower() == bajo or u.id.lower() == bajo), None
        )

    def _contacto_por_etiqueta(self, bando: str, etiqueta: str | None) -> ContactoSim | None:
        if not etiqueta:
            return None
        return next(
            (c for c in self.contactos[bando].values() if c.etiqueta.lower() == etiqueta.lower()), None
        )

    # ------------------------------------------------------------------ interfaz Mundo
    async def observar(self, bando: str) -> Observacion:
        return self.observacion(bando)

    def observacion(self, bando: str, consumir_eventos: bool = True) -> Observacion:
        unidades = [self._a_unidad_propia(u) for u in self._de(bando)]
        contactos = [
            Contacto(
                id=c.etiqueta,
                tipo=c.tipo if c.identificado else TipoUnidad.DESCONOCIDO,
                posicion=c.pos,
                efectivos_estimados=c.efectivos,
                visto_hace_s=self.tiempo - c.ultimo_t,
                precision_m=round(c.precision),
                en_vuelo=c.en_vuelo,
            )
            for c in self.contactos[bando].values()
        ]
        objetivos = [
            o.model_copy(update={"unidades": []}) if o.tipo == TipoObjetivo.DESTRUIR else o.model_copy()
            for o in self.objetivos[bando]
        ]
        eventos = list(self.eventos[bando])
        if consumir_eventos:
            self.eventos[bando] = []
        return Observacion(
            tiempo=self.tiempo,
            bando=bando,
            mapa=self.escenario.mapa,
            unidades=unidades,
            contactos=contactos,
            objetivos=objetivos,
            zonas_aterrizaje=self.escenario.zonas_aterrizaje,
            referencias=self.escenario.referencias,
            objetos=self.objetos_visibles(bando),
            base=self.base(bando),
            condiciones=self.escenario.condiciones,
            eventos=eventos,
        )

    def _a_unidad_propia(self, u: UnidadSim) -> UnidadPropia:
        return UnidadPropia(
            id=u.id,
            indicativo=u.indicativo,
            tipo=u.tipo,
            posicion=u.pos,
            efectivos=u.efectivos,
            efectivos_max=u.efectivos_max,
            municion=round(u.municion, 2),
            moral=round(u.moral, 2),
            estado=u.estado,
            postura=u.postura,
            accion_actual=u.accion,
            destino_actual=u.destino,
            en_contacto=u.en_contacto,
            bajo_fuego=u.bajo_fuego,
            en_vuelo=u.en_vuelo,
            altitud_m=ALTURA_VUELO[u.altitud] if u.en_vuelo else 0.0,
            combustible=round(u.combustible, 2),
            danos=round(u.danos, 2),
            armada=u.armada,
            capacidad=u.capacidad,
            pasajeros=list(u.pasajeros),
            embarcada_en=u.embarcada_en,
        )

    async def ejecutar(self, bando: str, acciones: list[Accion]) -> list[ResultadoAccion]:
        return [self.aplicar(bando, a) for a in acciones]

    async def recibir_chat(self, bando: str) -> list[MensajeChat]:
        pendientes, self.chat_entrante[bando] = self.chat_entrante.get(bando, []), []
        self.chat_log.extend(pendientes)
        return pendientes

    async def enviar_chat(self, bando: str, mensajes: list[MensajeChat]) -> None:
        self.chat_log.extend(mensajes)

    def inyectar_chat(self, bando: str, autor: str, texto: str) -> None:
        """Simula que un jugador escribe en el chat (modo interactivo)."""
        self.chat_entrante.setdefault(bando, []).append(
            MensajeChat(autor=autor, texto=texto, tiempo=self.tiempo)
        )

    async def avanzar(self) -> None:
        self.paso()

    def terminado(self) -> bool:
        return self._resultado is not None

    def resultado(self) -> ResultadoMision | None:
        return self._resultado

    def finalizar(self, motivo: str) -> ResultadoMision:
        if self._resultado is None:
            completados = self._objetivos_completados(self.bando_principal)
            estado = "parcial" if completados else "derrota"
            self._resultado = self._calcular_resultado(estado, motivo)
        return self._resultado

    # ------------------------------------------------------------------ poderes de Zeus
    def vista_global(self) -> VistaGlobal:
        return VistaGlobal(
            tiempo=self.tiempo,
            duracion_max_s=self.escenario.duracion_max_s,
            bando_jugadores=self.bando_principal,
            grupos=[
                GrupoGlobal(id=u.id, indicativo=u.indicativo, bando=u.bando, tipo=u.tipo, posicion=u.pos,
                            efectivos=u.efectivos, efectivos_max=u.efectivos_max, estado=u.estado,
                            accion=u.accion, en_combate=u.en_contacto or u.bajo_fuego)
                for u in self.unidades.values()
            ],
            objetivos={b: [o.model_copy() for o in objs] for b, objs in self.objetivos.items()},
            condiciones=self.escenario.condiciones.model_copy(),
            mapa=self.escenario.mapa,
            objetos=[o.model_copy() for o in self.objetos if o.activo],
        )

    async def crear_grupo(self, bando: str, grupo: GrupoNuevo) -> str:
        if bando not in self.escenario.bandos:
            raise ValueError(f"bando desconocido: {bando}")
        uid = grupo.id
        n = 2
        while uid in self.unidades:
            uid, n = f"{grupo.id}_{n}", n + 1
        mapa = self.escenario.mapa
        datos = UnidadEscenario(id=uid, indicativo=grupo.indicativo, tipo=grupo.tipo,
                                posicion=grupo.posicion.limitar(mapa.ancho_m, mapa.alto_m),
                                efectivos=grupo.efectivos, armada=grupo.armada, capacidad=grupo.capacidad)
        self.unidades[uid] = self._crear_unidad(datos, bando, grupo.indicativo)
        if not grupo.tipo.es_aerea:
            # Los refuerzos no cuentan como bajas evitadas: se suman a los efectivos iniciales.
            self.efectivos_iniciales[bando] = self.efectivos_iniciales.get(bando, 0) + grupo.efectivos
        self._evento(bando, "refuerzos", f"Llegan refuerzos: {grupo.indicativo} en {datos.posicion.cuadricula()}",
                     uid)
        return uid

    async def cambiar_condiciones(self, hora: str | None = None, clima: str | None = None) -> None:
        c = self.escenario.condiciones
        nuevas = c.model_copy(update={k: v for k, v in (("hora", hora), ("clima", clima)) if v})
        nuevas.visibilidad_m = VISIBILIDAD_HORA.get(nuevas.hora, 1500.0) * FACTOR_CLIMA.get(nuevas.clima, 1.0)
        self.escenario.condiciones = nuevas
        for bando in self.bandos:
            self._evento(bando, "condiciones", f"Cambian las condiciones: {nuevas.hora}, {nuevas.clima}")

    async def colocar_objeto(self, objeto: ObjetoMapa) -> None:
        mapa = self.escenario.mapa
        objeto = objeto.model_copy(update={"posicion": objeto.posicion.limitar(mapa.ancho_m, mapa.alto_m)})
        self.objetos.append(objeto)
        if objeto.bando in self.bandos:
            self._evento(objeto.bando, "decorado", f"Zeus coloca {objeto.tipo.value} en {objeto.posicion.cuadricula()}")

    async def anunciar(self, texto: str) -> None:
        mensaje = MensajeChat(autor="Zeus", texto=texto, canal="global", tiempo=self.tiempo, es_ia=True)
        self.anuncios.append(mensaje)
        self.chat_log.append(mensaje)

    # ------------------------------------------------------------------ órdenes
    def aplicar(self, bando: str, a: Accion) -> ResultadoAccion:
        u = self._resolver_unidad(bando, a.unidad)

        def rechazo(motivo: str) -> ResultadoAccion:
            return ResultadoAccion(accion=a, aceptada=False, motivo=motivo)

        if u is None:
            return rechazo("unidad desconocida")
        if not u.operativa:
            return rechazo("unidad destruida")
        if u.estado == EstadoUnidad.EMBARCADA:
            return rechazo("la unidad va embarcada; espera al desembarco")
        if u.es_aerea and a.tipo not in ACCIONES_AIRE:
            return rechazo(f"'{a.tipo.value}' no es una acción de aeronave")
        if not u.es_aerea and a.tipo not in ACCIONES_TIERRA:
            return rechazo(f"'{a.tipo.value}' no es una acción de tierra")

        mapa = self.escenario.mapa
        destino = a.destino.limitar(mapa.ancho_m, mapa.alto_m) if a.destino else None
        contacto = self._contacto_por_etiqueta(bando, a.objetivo)
        if destino is None and contacto is not None and a.tipo in (TipoAccion.ATACAR, TipoAccion.APOYO_AEREO):
            destino = contacto.pos
        objetivo = a.objetivo

        if a.tipo in (TipoAccion.MOVER, TipoAccion.RECONOCER, TipoAccion.RETIRARSE, TipoAccion.ATACAR,
                      TipoAccion.VOLAR, TipoAccion.ATERRIZAR, TipoAccion.APOYO_AEREO) and destino is None:
            return rechazo("falta el destino")
        if a.tipo == TipoAccion.DEFENDER:
            destino = destino or u.pos
        elif a.tipo == TipoAccion.ESPERAR:
            destino = None
        elif a.tipo == TipoAccion.EMBARCAR:
            transporte = self._resolver_unidad(bando, a.objetivo)
            if transporte is None or not transporte.es_aerea or not transporte.operativa:
                return rechazo("no hay helicóptero válido para embarcar")
            if transporte.capacidad <= len(transporte.pasajeros):
                return rechazo(f"{transporte.indicativo} va lleno")
            objetivo, destino = transporte.id, None
        elif a.tipo == TipoAccion.DESEMBARCAR:
            if u.en_vuelo:
                return rechazo("hay que aterrizar antes de desembarcar")
            if not u.pasajeros:
                return rechazo("no lleva pasajeros")
            self._desembarcar(u)
            u.accion, u.destino = None, None
            return ResultadoAccion(accion=a, aceptada=True)
        elif a.tipo == TipoAccion.APOYO_AEREO and not u.armada:
            return rechazo("la aeronave no va armada")
        elif a.tipo == TipoAccion.REGRESAR_BASE:
            destino = self.base(bando)
            if destino is None:
                return rechazo("el bando no tiene base")

        u.accion, u.destino, u.objetivo = a.tipo, destino, objetivo
        u.intentos_aterrizaje = 0
        if a.parametros:
            u.parametros = dict(a.parametros)
        if a.altitud is not None:
            u.altitud = a.altitud
        if a.postura is not None:
            u.postura = a.postura
        elif a.tipo == TipoAccion.RECONOCER:
            u.postura = Postura.SIGILO
        elif a.tipo in (TipoAccion.ATACAR, TipoAccion.RETIRARSE):
            u.postura = Postura.COMBATE
        return ResultadoAccion(accion=a, aceptada=True)

    # ------------------------------------------------------------------ simulación
    def paso(self) -> None:
        if self._resultado is not None:
            return
        dt = self.escenario.paso_s
        self.tiempo += dt
        self._mover(dt)
        self._peligros()
        self._detectar()
        self._combatir()
        self._actualizar_estado(dt)
        self._evaluar_objetivos(dt)
        self._comprobar_fin()

    def _mover(self, dt: float) -> None:
        for u in list(self.unidades.values()):
            if not u.operativa or u.estado == EstadoUnidad.EMBARCADA:
                continue
            if u.es_aerea:
                self._mover_aeronave(u, dt)
            else:
                self._mover_tierra(u, dt)
        for h in self.unidades.values():
            for pid in h.pasajeros:
                self.unidades[pid].pos = h.pos

    def _mover_tierra(self, u: UnidadSim, dt: float) -> None:
        if u.accion is None or u.accion == TipoAccion.ESPERAR:
            return
        if u.accion == TipoAccion.EMBARCAR:
            self._intentar_embarque(u, dt)
            return
        if u.destino is None:
            return
        if u.tipo in DANO_VEHICULO and u.danos >= INMOVILIZADO:
            return
        distancia = u.pos.distancia(u.destino)
        if u.accion == TipoAccion.DEFENDER and distancia < 30:
            return
        velocidad = VELOCIDAD[u.tipo] * FACTOR_POSTURA[u.postura]
        if u.bajo_fuego:
            # Un vehículo tiroteado frena, maniobra y desembarca a medias: no cruza la emboscada a toda velocidad.
            velocidad *= 0.5 if u.tipo == TipoUnidad.INFANTERIA else 0.15
        if u.tipo != TipoUnidad.INFANTERIA and self.terreno(u.pos) in ("bosque", "urbano"):
            velocidad *= 0.5
        for o in self.objetos_en(u.pos, *FRENO_OBJETO):
            velocidad *= FRENO_OBJETO[o.tipo][0 if u.tipo != TipoUnidad.INFANTERIA else 1]
        avance = velocidad * dt
        u.stats["distancia_m"] += min(avance, distancia)
        u.pos = u.pos.hacia(u.destino, avance)
        if avance < distancia:
            return
        lugar = u.destino.cuadricula()
        if u.accion == TipoAccion.DEFENDER:
            self._evento(u.bando, "accion_completada", f"{u.indicativo} en posición defensiva en {lugar}", u.id)
        elif u.accion == TipoAccion.ATACAR:
            enemigos_cerca = [e for e in self.enemigas(u.bando) if e.en_suelo and e.pos.distancia(u.pos) < 300]
            if not enemigos_cerca:
                self._evento(u.bando, "accion_completada", f"{u.indicativo} alcanza el objetivo en {lugar}", u.id)
                u.accion, u.destino = TipoAccion.DEFENDER, u.pos
        else:
            verbo = {TipoAccion.RECONOCER: "termina el reconocimiento en", TipoAccion.RETIRARSE: "se ha replegado a"}
            self._evento(u.bando, "accion_completada",
                         f"{u.indicativo} {verbo.get(u.accion, 'llega a')} {lugar}", u.id)
            u.accion, u.destino = None, None

    def _intentar_embarque(self, u: UnidadSim, dt: float) -> None:
        h = self.unidades.get(u.objetivo or "")
        if h is None or not h.operativa:
            self._evento(u.bando, "accion_fallida", f"{u.indicativo}: el transporte ya no existe", u.id)
            u.accion = None
            return
        if h.en_vuelo:
            return
        distancia = u.pos.distancia(h.pos)
        if distancia > DISTANCIA_EMBARQUE:
            u.pos = u.pos.hacia(h.pos, VELOCIDAD[u.tipo] * FACTOR_POSTURA[Postura.COMBATE] * dt)
            if u.pos.distancia(h.pos) > DISTANCIA_EMBARQUE:
                return
        if len(h.pasajeros) >= h.capacidad:
            self._evento(u.bando, "accion_fallida", f"{u.indicativo}: {h.indicativo} va lleno", u.id)
            u.accion = None
            return
        h.pasajeros.append(u.id)
        u.estado, u.embarcada_en, u.accion, u.destino = EstadoUnidad.EMBARCADA, h.id, None, None
        u.pos = h.pos
        self._evento(u.bando, "embarque", f"{u.indicativo} embarca en {h.indicativo}", h.id, pasajero=u.id)

    def _desembarcar(self, h: UnidadSim) -> None:
        for pid in h.pasajeros:
            p = self.unidades[pid]
            p.estado, p.embarcada_en, p.pos = EstadoUnidad.ACTIVA, None, h.pos
            p.accion, p.destino = None, None
            self._evento(p.bando, "desembarco", f"{p.indicativo} desembarca en {h.pos.cuadricula()}", p.id,
                         aeronave=h.id)
            self._evento(h.bando, "desembarco", f"{h.indicativo} ha desembarcado a {p.indicativo}", h.id,
                         pasajero=p.id)
        h.pasajeros = []

    def _mover_aeronave(self, u: UnidadSim, dt: float) -> None:
        en_marcha = u.accion in (TipoAccion.VOLAR, TipoAccion.ATERRIZAR, TipoAccion.APOYO_AEREO,
                                 TipoAccion.REGRESAR_BASE)
        if not u.en_vuelo:
            if not en_marcha or u.destino is None or u.pos.distancia(u.destino) < 100:
                if en_marcha and u.accion != TipoAccion.APOYO_AEREO:
                    self._evento(u.bando, "accion_completada", f"{u.indicativo} ya está en el destino", u.id)
                    u.accion, u.destino = None, None
                return
            u.en_vuelo = True
            self._evento(u.bando, "despegue", f"{u.indicativo} despega desde {u.pos.cuadricula()}", u.id)
        u.combustible = max(0.0, u.combustible - CONSUMO_COMBUSTIBLE_S * dt)
        if u.combustible <= 0.0:
            self._evento(u.bando, "danos_aeronave", f"{u.indicativo} se queda sin combustible", u.id)
            self._aterrizar(u, forzoso=True)
            return
        if u.destino is None or u.accion == TipoAccion.ESPERAR:
            return
        distancia = u.pos.distancia(u.destino)
        if u.accion == TipoAccion.APOYO_AEREO and distancia <= 600:
            return
        avance = VELOCIDAD[u.tipo] * dt
        u.stats["distancia_m"] += min(avance, distancia)
        u.pos = u.pos.hacia(u.destino, avance)
        if avance < distancia:
            return
        if u.accion == TipoAccion.VOLAR:
            self._evento(u.bando, "accion_completada", f"{u.indicativo} sobrevuela {u.destino.cuadricula()}", u.id)
            u.accion = TipoAccion.ESPERAR
        elif u.accion in (TipoAccion.ATERRIZAR, TipoAccion.REGRESAR_BASE):
            self._aterrizar(u, en_base=u.accion == TipoAccion.REGRESAR_BASE)

    def _aterrizar(self, u: UnidadSim, en_base: bool = False, forzoso: bool = False) -> None:
        caliente = any(
            e.en_suelo and not e.es_aerea and e.pos.distancia(u.pos) < RADIO_ZONA_CALIENTE
            for e in self.enemigas(u.bando)
        )
        condiciones = CondicionesAterrizaje(
            viento_ms=self.escenario.condiciones.viento_ms * (2.0 if forzoso else 1.0),
            zona_caliente=caliente,
            noche=self.escenario.condiciones.hora == "noche",
            en_base=en_base,
        )
        resultado = simular_aterrizaje(u.parametros, condiciones, self.rng)
        lugar = u.pos.cuadricula()
        datos = {
            "resultado": resultado.resultado,
            "puntuacion": resultado.puntuacion,
            "zona_caliente": caliente,
            "en_base": en_base,
            "parametros": {**u.parametros} if u.parametros else {},
        }
        u.stats[f"aterrizajes_{resultado.resultado}"] += 1
        if resultado.resultado == "abortado" and not forzoso:
            u.intentos_aterrizaje += 1
            self._evento(u.bando, "aterrizaje", f"{u.indicativo} aborta la aproximación en {lugar}", u.id, **datos)
            if u.intentos_aterrizaje >= 3:
                self._evento(u.bando, "accion_fallida", f"{u.indicativo} no consigue aterrizar en {lugar}", u.id)
                u.accion = TipoAccion.ESPERAR
            return
        u.danos = min(1.0, u.danos + resultado.danos)
        if resultado.resultado == "accidente" or u.danos >= 1.0:
            self._evento(u.bando, "aterrizaje", f"{u.indicativo} SE ESTRELLA al aterrizar en {lugar}", u.id, **datos)
            for pid in list(u.pasajeros):
                p = self.unidades[pid]
                perdidas = self.rng.randint(max(1, p.efectivos // 3), p.efectivos)
                self._aplicar_bajas(p, perdidas, causante=None, sorpresa=False)
            self._desembarcar(u)
            self._destruir(u, "se estrella")
            return
        u.en_vuelo, u.intentos_aterrizaje = False, 0
        u.accion, u.destino = None, None
        if resultado.resultado == "duro":
            self._evento(u.bando, "aterrizaje", f"{u.indicativo} aterriza con un golpe duro en {lugar} "
                         f"(daños {u.danos:.0%})", u.id, **datos)
            for pid in u.pasajeros:
                if self.rng.random() < 0.3:
                    self._aplicar_bajas(self.unidades[pid], 1, causante=None, sorpresa=False)
        else:
            self._evento(u.bando, "aterrizaje", f"{u.indicativo} aterriza en {lugar}", u.id, **datos)

    def _peligros(self) -> None:
        """Minas e IED: hieren a quien no es de su bando cuando pasa por encima."""
        for o in self.objetos:
            if not o.activo or o.tipo not in PELIGROS:
                continue
            for u in list(self.unidades.values()):
                if not u.en_suelo or u.es_aerea or u.bando == o.bando or u.pos.distancia(o.posicion) > o.radio_m:
                    continue
                if u.accion in (None, TipoAccion.ESPERAR, TipoAccion.DEFENDER) and o.tipo == TipoObjeto.MINAS:
                    continue  # quien está quieto no pisa minas nuevas
                if o.tipo == TipoObjeto.MINAS and self.rng.random() >= PROB_MINA:
                    continue
                que = "un campo de minas" if o.tipo == TipoObjeto.MINAS else "un artefacto explosivo (IED)"
                self._evento(u.bando, "explosion", f"{u.indicativo} pisa {que} en {o.posicion.cuadricula()}", u.id,
                             objeto=o.id)
                if u.tipo in DANO_VEHICULO:
                    antes = u.danos
                    u.danos = min(1.0, u.danos + (0.6 if o.tipo == TipoObjeto.IED else 0.35))
                    if antes < INMOVILIZADO <= u.danos:
                        self._evento(u.bando, "inmovilizado", f"{u.indicativo} queda inmovilizado por la explosión",
                                     u.id)
                bajas = self.rng.randint(1, 3) if o.tipo == TipoObjeto.IED else 1
                self._aplicar_bajas(u, bajas, causante=None, sorpresa=True)
                if o.tipo == TipoObjeto.IED:
                    o.activo = False
                    break

    def _detectar(self) -> None:
        vis = self.escenario.condiciones.visibilidad_m
        factor_vis = min(1.2, max(0.25, vis / 1500.0))
        for bando in self.bandos:
            observadores = [u for u in self.operativas(bando) if u.estado != EstadoUnidad.EMBARCADA]
            conocidos = self.contactos[bando]
            for e in self.enemigas(bando):
                if e.estado == EstadoUnidad.EMBARCADA:
                    continue
                mejor_p, mejor_obs, mejor_d = 0.0, None, 0.0
                for o in observadores:
                    d = o.pos.distancia(e.pos)
                    alcance = ALCANCE_DETECCION[o.tipo] * factor_vis
                    if o.es_aerea and not o.en_vuelo:
                        alcance = 300.0
                    if o.accion == TipoAccion.RECONOCER:
                        alcance *= 1.4
                    if not o.es_aerea:
                        if any(t.bando in (None, o.bando) for t in self.objetos_en(o.pos, TipoObjeto.TORRE_VIGILANCIA)):
                            alcance *= FACTOR_TORRE
                        if self.objetos_en(o.pos, TipoObjeto.HUMO):
                            alcance *= FACTOR_HUMO
                    if e.es_aerea and e.en_vuelo:
                        alcance = max(alcance, 2500.0 * factor_vis)
                    if d > alcance:
                        continue
                    p = (1 - d / alcance) ** 0.5
                    if not (e.es_aerea and e.en_vuelo):
                        p *= OCULTACION_TERRENO.get(self.terreno(e.pos), 1.0)
                        p *= DETECTABILIDAD_POSTURA[e.postura]
                        if self.objetos_en(e.pos, TipoObjeto.HUMO):
                            p *= FACTOR_HUMO
                    if e.disparando:
                        p *= 1.6
                    if d < 120:
                        p = max(p, 0.9)
                    if p > mejor_p:
                        mejor_p, mejor_obs, mejor_d = min(p, 0.95), o, d
                if mejor_obs is None or self.rng.random() >= mejor_p:
                    continue
                error = 10 + 0.04 * mejor_d
                estimada = Posicion(x=e.pos.x + self.rng.gauss(0, error), y=e.pos.y + self.rng.gauss(0, error))
                previo = conocidos.get(e.id)
                nuevo = previo is None or self.tiempo - previo.ultimo_t > OLVIDO_CONTACTO_S / 2
                identificado = mejor_d < 800 or (previo is not None and previo.identificado)
                efectivos = None
                if mejor_d < 600:
                    efectivos = max(1, round(e.efectivos * self.rng.uniform(0.75, 1.25)))
                elif previo is not None:
                    efectivos = previo.efectivos
                conocidos[e.id] = ContactoSim(
                    etiqueta=self.etiqueta(bando, e.id),
                    tipo=e.tipo,
                    pos=estimada,
                    ultimo_t=self.tiempo,
                    efectivos=efectivos,
                    precision=error,
                    en_vuelo=e.en_vuelo,
                    identificado=identificado,
                )
                if nuevo:
                    tipo = NOMBRE_TIPO[e.tipo] if identificado else "tipo desconocido"
                    cantidad = f" (~{efectivos})" if efectivos else ""
                    self._evento(
                        bando, "contacto",
                        f"{mejor_obs.indicativo} detecta {conocidos[e.id].etiqueta}: {tipo}{cantidad} en "
                        f"{estimada.cuadricula()}, {describir_relativa(mejor_obs.pos, estimada)}",
                        mejor_obs.id, contacto=conocidos[e.id].etiqueta,
                    )
            for objetivo_id in [i for i, c in conocidos.items() if self.tiempo - c.ultimo_t > OLVIDO_CONTACTO_S]:
                del conocidos[objetivo_id]

    def _cobertura(self, u: UnidadSim) -> float:
        cobertura = COBERTURA_TERRENO.get(self.terreno(u.pos), 0.0)
        if u.accion == TipoAccion.DEFENDER and (u.destino is None or u.pos.distancia(u.destino) < 30):
            cobertura += 0.2
        if u.postura == Postura.SIGILO:
            cobertura += 0.1
        extra = [COBERTURA_OBJETO[o.tipo] for o in self.objetos_en(u.pos, *COBERTURA_OBJETO)
                 if o.bando in (None, u.bando)]
        return min(cobertura + max(extra, default=0.0), COBERTURA_MAXIMA)

    def _combatir(self) -> None:
        for u in self.unidades.values():
            u.bajo_fuego_previo, u.bajo_fuego, u.en_contacto, u.disparando = u.bajo_fuego, False, False, False
        disparos: list[tuple[UnidadSim, UnidadSim, int, bool]] = []
        for a in self.unidades.values():
            if not a.operativa or a.estado == EstadoUnidad.EMBARCADA or a.municion <= 0.02:
                continue
            if a.es_aerea and not (a.armada and a.en_vuelo and a.accion == TipoAccion.APOYO_AEREO):
                continue
            sigilo = a.postura == Postura.SIGILO and a.accion not in (TipoAccion.ATACAR, TipoAccion.DEFENDER)
            if sigilo and not a.bajo_fuego_previo:
                continue
            conocidos = self.contactos[a.bando]
            alcance = ALCANCE_ARMAS[a.tipo]
            candidatos = []
            for b in self.enemigas(a.bando):
                c = conocidos.get(b.id)
                if c is None or c.ultimo_t < self.tiempo or b.estado == EstadoUnidad.EMBARCADA:
                    continue
                d = a.pos.distancia(b.pos)
                if a.es_aerea:
                    if a.destino is None or b.pos.distancia(a.destino) > 500 or d > alcance:
                        continue
                elif d > alcance:
                    continue
                candidatos.append((d, b))
            if not candidatos:
                continue
            preferido = next(
                (b for _, b in candidatos if conocidos[b.id].etiqueta == (a.objetivo or "")), None
            )
            if preferido is None or (preferido.tipo in DANO_VEHICULO and preferido.danos >= INMOVILIZADO):
                # Un vehículo ya inutilizado no se va a escapar: se dispara antes al que aún se mueve.
                movil = next((b for _, b in sorted(candidatos, key=lambda x: x[0])
                              if b.tipo in DANO_VEHICULO and b.danos < INMOVILIZADO), None)
                preferido = movil or preferido
            d, b = (preferido.pos.distancia(a.pos), preferido) if preferido else min(candidatos, key=lambda x: x[0])
            sorpresa = a.id not in self.contactos[b.bando]
            p = PROB_IMPACTO[a.tipo] * max(0.15, 1 - d / (alcance * 1.15))
            p *= (1 - self._cobertura(b)) if not b.es_aerea else 0.35 * (0.4 if b.altitud == Altitud.ALTA else 1.0)
            p *= 0.5 + 0.5 * a.moral
            p *= 0.4 if a.municion < 0.15 else 1.0
            if a.es_aerea and a.altitud == Altitud.ALTA:
                p *= 0.6
            if b.tipo == TipoUnidad.BLINDADO and a.tipo == TipoUnidad.INFANTERIA:
                p *= 0.15
            if sorpresa:
                p *= 2.0
            tiradores = 6 if a.es_aerea else a.efectivos
            impactos = sum(self.rng.random() < min(p, 0.9) for _ in range(tiradores))
            disparos.append((a, b, impactos, sorpresa))

        for a, b, impactos, sorpresa in disparos:
            a.disparando = a.en_contacto = True
            a.municion = max(0.0, a.municion - (0.02 if a.es_aerea else 0.04))
            if not b.operativa:
                continue
            b.bajo_fuego = b.en_contacto = True
            if b.es_aerea:
                if impactos:
                    b.danos = min(1.0, b.danos + 0.06 * impactos)
                    self._evento(b.bando, "danos_aeronave",
                                 f"{b.indicativo} recibe {impactos} impacto(s) (daños {b.danos:.0%})", b.id)
                    if b.danos >= 1.0:
                        if b.en_vuelo:
                            for pid in list(b.pasajeros):
                                self._destruir(self.unidades[pid], "derribado con su transporte")
                            b.pasajeros = []
                        else:
                            self._desembarcar(b)
                        self._destruir(b, "derribado")
                continue
            if impactos and b.tipo in DANO_VEHICULO:
                antes = b.danos
                # La primera ráfaga de una emboscada va a por los motores y las ruedas.
                b.danos = min(1.0, b.danos + DANO_VEHICULO[b.tipo] * impactos * (2 if sorpresa else 1))
                if antes < INMOVILIZADO <= b.danos:
                    self._evento(b.bando, "inmovilizado",
                                 f"{b.indicativo} tiene los vehículos inutilizados y ya no puede moverse", b.id)
            self._aplicar_bajas(b, impactos, causante=a, sorpresa=sorpresa)

        for u in self.unidades.values():
            if u.bajo_fuego and not u.bajo_fuego_previo and u.operativa:
                self._evento(u.bando, "bajo_fuego", f"{u.indicativo} está bajo fuego enemigo", u.id)

    def _aplicar_bajas(self, b: UnidadSim, bajas: int, causante: UnidadSim | None, sorpresa: bool) -> None:
        bajas = min(bajas, b.efectivos)
        if bajas <= 0 or not b.operativa:
            return
        b.efectivos -= bajas
        b.stats["bajas_sufridas"] += bajas
        b.moral = max(0.0, b.moral - 0.6 * bajas / b.efectivos_max - 0.03)
        origen = ""
        if causante is not None:
            causante.stats["bajas_causadas"] += bajas
            etiqueta = self.contactos[b.bando].get(causante.id)
            origen = f" por fuego de {etiqueta.etiqueta}" if etiqueta else " por fuego desde una posición oculta"
            objetivo = self.contactos[causante.bando].get(b.id)
            if objetivo is not None:
                self._evento(causante.bando, "enemigo_abatido",
                             f"{causante.indicativo} causa {bajas} baja(s) a {objetivo.etiqueta}",
                             causante.id, contacto=objetivo.etiqueta, bajas=bajas)
        self._evento(b.bando, "bajas",
                     f"{b.indicativo} sufre {bajas} baja(s){origen} (quedan {b.efectivos}/{b.efectivos_max})",
                     b.id, bajas=bajas, sorpresa=sorpresa)
        if b.efectivos <= 0:
            self._destruir(b, "aniquilada")

    def _destruir(self, u: UnidadSim, motivo: str) -> None:
        if u.estado == EstadoUnidad.DESTRUIDA:
            return
        u.estado, u.efectivos, u.accion, u.destino = EstadoUnidad.DESTRUIDA, 0, None, None
        self._evento(u.bando, "unidad_destruida", f"{u.indicativo} destruida ({motivo})", u.id)
        for bando in self.bandos:
            if bando != u.bando and u.id in self.contactos[bando]:
                etiqueta = self.contactos[bando].pop(u.id).etiqueta
                self._evento(bando, "contacto_destruido", f"{etiqueta} ha sido destruido", None,
                             contacto=etiqueta)

    def _actualizar_estado(self, dt: float) -> None:
        for u in self.unidades.values():
            if not u.operativa:
                continue
            if not u.bajo_fuego:
                u.moral = min(1.0, u.moral + 0.02)
            base = self.base(u.bando)
            if base is not None and u.pos.distancia(base) < 300 and not u.en_contacto and not u.en_vuelo:
                u.municion = min(1.0, u.municion + 0.1)
                if u.es_aerea:
                    u.combustible = min(1.0, u.combustible + 0.1)
            if u.municion < 0.25 and "municion" not in u.avisos:
                u.avisos.add("municion")
                self._evento(u.bando, "municion_baja", f"{u.indicativo} se queda sin munición", u.id)
            if u.moral < 0.3 and "moral" not in u.avisos:
                u.avisos.add("moral")
                self._evento(u.bando, "moral_baja", f"{u.indicativo} tiene la moral por los suelos", u.id)
            if u.municion > 0.5:
                u.avisos.discard("municion")
            if u.moral > 0.5:
                u.avisos.discard("moral")

    # ------------------------------------------------------------------ objetivos y final
    def _evaluar_objetivos(self, dt: float) -> None:
        for bando, objetivos in self.objetivos.items():
            propias = [u for u in self.operativas(bando) if u.en_suelo and not u.es_aerea]
            enemigas = [u for u in self.enemigas(bando) if u.en_suelo and not u.es_aerea]
            for o in objetivos:
                if o.estado != EstadoObjetivo.PENDIENTE:
                    continue
                amigos = [u for u in propias if u.pos.distancia(o.posicion) <= o.radio_m]
                hostiles = [u for u in enemigas if u.pos.distancia(o.posicion) <= o.radio_m]
                nuevo: EstadoObjetivo | None = None
                if o.tipo == TipoObjetivo.CAPTURAR:
                    self._control[o.id] = self._control[o.id] + dt if amigos and not hostiles else 0
                    if self._control[o.id] >= self.escenario.tiempo_control_s:
                        nuevo = EstadoObjetivo.COMPLETADO
                elif o.tipo == TipoObjetivo.DESTRUIR:
                    if o.unidades:
                        if all(not self.unidades[i].operativa for i in o.unidades if i in self.unidades):
                            nuevo = EstadoObjetivo.COMPLETADO
                    elif not hostiles and any(u.pos.distancia(o.posicion) <= o.radio_m + 300 for u in propias):
                        nuevo = EstadoObjetivo.COMPLETADO
                elif o.tipo == TipoObjetivo.DEFENDER:
                    self._control[o.id] = self._control[o.id] + dt if hostiles and not amigos else 0
                    if self._control[o.id] >= self.escenario.tiempo_control_s:
                        nuevo = EstadoObjetivo.FALLIDO
                    elif o.hasta_s is not None and self.tiempo >= o.hasta_s:
                        nuevo = EstadoObjetivo.COMPLETADO
                elif o.tipo == TipoObjetivo.RECONOCER:
                    observadores = [u for u in self.operativas(bando) if u.estado != EstadoUnidad.EMBARCADA]
                    if any(u.pos.distancia(o.posicion) <= o.radio_m for u in observadores):
                        nuevo = EstadoObjetivo.COMPLETADO
                elif o.tipo == TipoObjetivo.EXTRAER:
                    ids = o.unidades or [u.id for u in self._de(bando) if not u.es_aerea]
                    vivas = [self.unidades[i] for i in ids if i in self.unidades and self.unidades[i].operativa]
                    if not vivas:
                        nuevo = EstadoObjetivo.FALLIDO
                    elif all(u.en_suelo and u.pos.distancia(o.posicion) <= o.radio_m for u in vivas):
                        nuevo = EstadoObjetivo.COMPLETADO
                if nuevo is not None:
                    o.estado = nuevo
                    if nuevo == EstadoObjetivo.COMPLETADO:
                        self._evento(bando, "objetivo_completado", f"Objetivo cumplido: {o.nombre}", None,
                                     objetivo=o.id)
                    else:
                        self._evento(bando, "objetivo_fallido", f"Objetivo FALLIDO: {o.nombre}", None,
                                     objetivo=o.id)

    def _objetivos_completados(self, bando: str) -> int:
        return sum(o.estado == EstadoObjetivo.COMPLETADO for o in self.objetivos[bando])

    def _comprobar_fin(self) -> None:
        bando = self.bando_principal
        objetivos = self.objetivos[bando]
        obligatorios = [o for o in objetivos if o.obligatorio]
        if obligatorios and all(o.estado == EstadoObjetivo.COMPLETADO for o in obligatorios):
            self._resultado = self._calcular_resultado("victoria", "objetivos cumplidos")
        elif any(o.estado == EstadoObjetivo.FALLIDO for o in obligatorios):
            self._resultado = self._calcular_resultado("derrota", "un objetivo obligatorio ha fallado")
        elif any(
            rivales and all(o.estado == EstadoObjetivo.COMPLETADO for o in rivales)
            for b in self.bandos if b != bando
            for rivales in [[o for o in self.objetivos[b] if o.obligatorio]]
        ):
            self._resultado = self._calcular_resultado("derrota", "el enemigo ha cumplido su objetivo")
        elif not any(not u.es_aerea for u in self.operativas(bando)):
            self._resultado = self._calcular_resultado("derrota", "todas las unidades de tierra destruidas")
        elif self.tiempo >= self.escenario.duracion_max_s:
            estado = "parcial" if self._objetivos_completados(bando) else "derrota"
            self._resultado = self._calcular_resultado(estado, "se agotó el tiempo")

    def _calcular_resultado(self, estado: str, motivo: str) -> ResultadoMision:
        bando = self.bando_principal
        objetivos = self.objetivos[bando]
        pesos = {o.id: 1.0 if o.obligatorio else 0.5 for o in objetivos}
        total = sum(pesos.values()) or 1.0
        fraccion = sum(pesos[o.id] for o in objetivos if o.estado == EstadoObjetivo.COMPLETADO) / total
        iniciales = self.efectivos_iniciales[bando]
        actuales = sum(u.efectivos for u in self._de(bando) if not u.es_aerea)
        bajas = iniciales - actuales
        enemigos_ini = sum(self.efectivos_iniciales[b] for b in self.bandos if b != bando)
        enemigos_act = sum(u.efectivos for b in self.bandos if b != bando for u in self._de(b) if not u.es_aerea)
        rapidez = 1 - self.tiempo / self.escenario.duracion_max_s if estado == "victoria" else 0.0
        puntuacion = 0.6 * fraccion + 0.25 * (1 - bajas / max(1, iniciales)) + 0.15 * max(0.0, rapidez)
        unidades = {}
        for u in self._de(bando):
            m: dict[str, object] = {
                "indicativo": u.indicativo,
                "tipo": u.tipo.value,
                "aerea": u.es_aerea,
                "efectivos_iniciales": u.efectivos_max,
                "efectivos_finales": u.efectivos,
                "destruida": not u.operativa,
            }
            m.update({k: round(v) for k, v in sorted(u.stats.items())})
            if u.es_aerea:
                m["danos"] = round(u.danos, 2)
            unidades[u.id] = m
        return ResultadoMision(
            estado=estado,  # type: ignore[arg-type]
            motivo=motivo,
            duracion_s=self.tiempo,
            objetivos_completados=self._objetivos_completados(bando),
            objetivos_totales=len(objetivos),
            efectivos_iniciales=iniciales,
            bajas_propias=bajas,
            bajas_enemigas=enemigos_ini - enemigos_act,
            puntuacion=round(puntuacion, 3),
            metricas={"unidades": unidades, "contactos_detectados": len(self._etiquetas[bando])},
        )
