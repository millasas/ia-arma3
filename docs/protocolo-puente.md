# Protocolo del puente ARMA 3 ↔ IA (v1)

La IA no sabe si juega en el simulador de texto o en ARMA 3: solo habla con la interfaz
`Mundo` (`src/arma3_ia/puente/interfaz.py`). El puente con ARMA 3 será otra implementación
de `Mundo` que intercambia **líneas JSON** con el juego. Este documento fija el formato para
que la parte de ARMA (SQF y la extensión) se pueda escribir sin tocar el núcleo.

## Arquitectura prevista

```
 PC de juego (servidor ARMA 3)                         PC de la IA (Ubuntu + RTX 3060)
┌──────────────────────────────────┐   LAN (TCP)   ┌──────────────────────────────────┐
│ Misión / mod SQF                 │               │ arma3-ia (Python)                │
│  · recoge grupos, contactos,     │               │  · PuenteArma3(Mundo)            │
│    objetivos y chat              │               │  · CerebroBando por bando        │
│  · ejecuta órdenes (waypoints,   │◄────────────► │  · Ollama (qwen3:8b) en la GPU   │
│    comportamiento, vehículos)    │  JSON líneas  │  · memoria SQLite                │
│ Extensión (DLL) callExtension    │               │                                  │
│  + ExtensionCallback             │               │                                  │
└──────────────────────────────────┘               └──────────────────────────────────┘
```

- **SQF** cada N segundos (p. ej. 10 s) construye la observación de cada bando de la IA y la
  envía con `"arma3ia" callExtension ["observacion", [_json]]`.
- La **extensión** (DLL/SO, en C++ o Rust) mantiene una conexión TCP con el PC de la IA y
  devuelve las respuestas de forma asíncrona con el evento `ExtensionCallback`, para no
  congelar el juego mientras el modelo piensa.
- El **PC de la IA** escucha en un puerto (p. ej. `0.0.0.0:7676`), traduce cada línea con
  `arma3_ia.puente.decodificar` y llama al cerebro.

Nada de esto existe aún: es el siguiente paso del proyecto. El formato de abajo ya está
implementado y probado (`tests/test_perfiles_protocolo.py`).

## Sobre

Cada línea es un objeto JSON en UTF-8 terminado en `\n`:

```json
{"v": 1, "tipo": "observacion", "bando": "blufor", "datos": { ... }}
```

| campo   | significado                                                                 |
|---------|-----------------------------------------------------------------------------|
| `v`     | versión del protocolo (1). Otra versión se rechaza con un error claro.       |
| `tipo`  | `hola`, `observacion`, `acciones`, `resultados`, `chat`, `fin`, `error`      |
| `bando` | `blufor`, `opfor`, `independent`… (el `side` de ARMA en minúsculas)          |
| `datos` | contenido según el tipo                                                      |

Flujo de cada ciclo:

1. ARMA → IA: `observacion` (y `chat` si los jugadores han escrito algo).
2. IA → ARMA: `acciones` y `chat` (respuestas del enlace y radio entre agentes).
3. ARMA → IA: `resultados` (qué órdenes se aceptaron y por qué no las otras).
4. Al terminar la misión, ARMA → IA: `fin` con el resultado; la IA reflexiona y aprende.

## Observación (ARMA → IA)

Las posiciones aceptan el formato nativo de ARMA `[x, y, z]` (la `z` se ignora) o `{"x", "y"}`.
Los campos omitidos toman su valor por defecto.

```json
{
  "v": 1, "tipo": "observacion", "bando": "blufor",
  "datos": {
    "tiempo": 930.0,
    "bando": "blufor",
    "mapa": {"nombre": "Altis", "ancho_m": 30720, "alto_m": 30720},
    "unidades": [
      {"id": "grp_alfa", "indicativo": "Alfa-1", "tipo": "infanteria",
       "posicion": [14520, 16280, 12.4], "efectivos": 7, "efectivos_max": 8,
       "municion": 0.8, "en_contacto": true},
      {"id": "heli_1", "indicativo": "Halcón-1", "tipo": "helicoptero",
       "posicion": [14100, 15900, 130], "efectivos": 4, "efectivos_max": 4,
       "en_vuelo": true, "altitud_m": 120, "combustible": 0.7, "capacidad": 1}
    ],
    "contactos": [
      {"id": "C1", "tipo": "infanteria", "posicion": [14900, 16600, 0],
       "efectivos_estimados": 6, "visto_hace_s": 20, "precision_m": 80}
    ],
    "objetivos": [
      {"id": "obj_kavala", "nombre": "Tomar Kavala", "tipo": "capturar",
       "posicion": [3600, 13000, 0], "radio_m": 300}
    ],
    "zonas_aterrizaje": [{"id": "lz1", "nombre": "LZ Roja", "posicion": [14000, 17000, 0]}],
    "base": [14600, 16700, 0],
    "eventos": [
      {"tiempo": 925, "tipo": "bajo_fuego", "descripcion": "Alfa-1 recibe fuego desde el NE",
       "unidad": "grp_alfa"}
    ]
  }
}
```

Reglas importantes para el lado de ARMA:

- **Niebla de guerra**: los `contactos` son solo lo que el bando conoce (`targets`,
  `knowsAbout`, `getHideFrom`), con una etiqueta estable (`C1`, `C2`…). Nunca enviar el
  nombre real del grupo enemigo ni sus posiciones exactas si no se conocen.
- `id` de las unidades propias: el `groupId` o una variable estable; `indicativo` es el
  nombre que verán los jugadores y el que usa la IA en la radio.
- Tipos de unidad: `infanteria`, `motorizada`, `blindado`, `helicoptero`, `desconocido`.
- Eventos útiles: `contacto`, `bajo_fuego`, `bajas`, `unidad_destruida`, `objetivo_completado`,
  `objetivo_fallido`, `embarque`, `desembarco`, `aterrizaje`, `danos_aeronave`, `municion_baja`,
  `accion_completada`, `accion_fallida`. Un evento `aterrizaje` con
  `datos: {"puntuacion": 0..1, "zona_caliente": bool, "parametros": {...}}` alimenta la
  habilidad de vuelo que aprende.

## Acciones (IA → ARMA)

```json
{"v": 1, "tipo": "acciones", "bando": "blufor", "datos": [
  {"unidad": "grp_alfa", "tipo": "atacar", "destino": {"x": 14900, "y": 16600}, "objetivo": "C1",
   "postura": "combate", "motivo": "Contacto al NE, asalto con cobertura del bosque"},
  {"unidad": "heli_1", "tipo": "aterrizar", "destino": {"x": 14000, "y": 17000},
   "parametros": {"velocidad_aproximacion": 12.0, "tasa_descenso": 1.8,
                  "altura_recogida": 22.0, "intensidad_recogida": 0.6},
   "motivo": "LZ fría para recoger a Bravo-1"}
]}
```

| `tipo`          | qué debe hacer ARMA (sugerencia)                                          |
|-----------------|---------------------------------------------------------------------------|
| `mover`         | waypoint `MOVE` en `destino`                                              |
| `atacar`        | waypoint `SAD`/`DESTROY` en `destino`, `setCombatMode "RED"`              |
| `defender`      | waypoint `HOLD`/`GUARD` en `destino` (o donde está)                        |
| `reconocer`     | `MOVE` + `setBehaviour "STEALTH"`, `setCombatMode "GREEN"`                |
| `retirarse`     | `MOVE` hacia `destino` con `setBehaviour "AWARE"` y velocidad máxima       |
| `esperar`       | borrar waypoints y mantener posición                                      |
| `embarcar`      | waypoint `GETIN` en el vehículo cuyo `id` está en `objetivo`              |
| `volar`         | waypoint de aeronave a `destino`, altura según `altitud` (`flyInHeight`)   |
| `aterrizar`     | `land "GET IN"` o waypoint `TR UNLOAD` en `destino`                        |
| `desembarcar`   | `leaveVehicle` / `action ["GetOut", …]` de los pasajeros                   |
| `apoyo_aereo`   | waypoint `SAD` sobre `destino`                                            |
| `regresar_base` | volver a `base` y aterrizar                                               |

`postura`: `sigilo`, `normal`, `combate`. `altitud`: `baja`, `media`, `alta`. `parametros` son
los valores aprendidos por la habilidad de vuelo: el puente puede usarlos para ajustar la
aproximación (velocidad con `limitSpeed`, altura de inicio de la recogida, etc.).

## Resultados (ARMA → IA)

```json
{"v": 1, "tipo": "resultados", "bando": "blufor", "datos": [
  {"accion": {"unidad": "heli_1", "tipo": "desembarcar"}, "aceptada": false,
   "motivo": "hay que aterrizar antes de desembarcar"}
]}
```

Una orden imposible **no** es un error: se rechaza con un motivo legible y el agente lo verá en
su siguiente decisión.

## Chat (en ambos sentidos)

```json
{"v": 1, "tipo": "chat", "bando": "blufor", "datos": [
  {"autor": "Sargento López", "texto": "Mando, ¿cuál es el plan para la aldea?", "canal": "lado",
   "tiempo": 60}
]}
```

De ARMA a la IA llegan los mensajes de los jugadores (evento `HandleChatMessage`). De la IA a
ARMA salen las respuestas del enlace (`es_ia: true`) y, si `retransmitir_radio` está activo, el
tráfico de radio entre agentes con `canal: "radio"` (mostrarlo con `sideChat` o `customChat`).

## Fin de misión (ARMA → IA)

```json
{"v": 1, "tipo": "fin", "bando": "blufor", "datos": {
  "estado": "victoria", "motivo": "objetivos cumplidos", "duracion_s": 2410,
  "objetivos_completados": 2, "objetivos_totales": 2, "efectivos_iniciales": 24,
  "bajas_propias": 5, "bajas_enemigas": 17, "puntuacion": 0.78
}}
```

`estado`: `victoria`, `parcial` o `derrota`. Con esto la IA escribe su informe post-acción,
actualiza la utilidad de las lecciones que usó y guarda lecciones nuevas.

## Poderes de Zeus (propuesta, aún no en el codificador)

El director de juego necesita cuatro operaciones más (`ControlZeus` en
`src/arma3_ia/puente/interfaz.py`, ya implementadas en el simulador). Propuesta de mensajes
para cuando se escriba el puente:

| IA → ARMA                    | datos                                                                 | en SQF |
|------------------------------|-----------------------------------------------------------------------|--------|
| `zeus_vista` (petición)      | —                                                                     | ARMA responde con todos los grupos de todos los bandos, objetivos y condiciones |
| `zeus_crear_grupo`           | `bando`, `id`, `indicativo`, `tipo`, `posicion`, `efectivos`, `armada` | `createGroup` + `createUnit`/`createVehicle`, devuelve el id |
| `zeus_condiciones`           | `hora` (`dia`, `amanecer`, `atardecer`, `noche`), `clima` (`despejado`, `nubes`, `lluvia`, `niebla`) | `skipTime`, `setOvercast`, `setRain`, `setFog` |
| `zeus_anuncio`               | `texto`                                                               | `systemChat` o `globalChat` a todos |

La vista global es como la observación pero sin niebla de guerra: Zeus lo ve todo. Los grupos
creados por Zeus pasan a ser NPC normales: el bando correspondiente les asigna persona y orden.
