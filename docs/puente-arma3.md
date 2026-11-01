# El puente entre la IA y ARMA 3: qué hace falta y cómo se hace

Este documento explica, paso a paso, cómo conectar la IA (que corre en el portátil con Ubuntu)
con ARMA 3 (que corre en tu PC de juego mientras juegas). Todavía no hay código del puente:
esto es el plan detallado para construirlo. Se apoya en lo que ya existe en el repositorio:

- la interfaz `Mundo` + `ControlZeus` (`src/arma3_ia/puente/interfaz.py`), que es todo lo que
  la IA necesita de un juego;
- el protocolo de líneas JSON v1 (`docs/protocolo-puente.md`, codificador en
  `src/arma3_ia/puente/protocolo.py`), con los mensajes `zeus_*` propuestos;
- el `mision.yaml` que Zeus escribe en `CAMPAÑAS/<campaña>/misiones/NN_*/` para cada misión.

La idea clave: **la IA no sabe si juega en el simulador de texto o en ARMA 3**. El puente es
otra implementación de `Mundo` que, en vez de simular, pregunta y ordena al juego por la red.
El cerebro (Zeus, comandantes, líderes, pilotos, enlace), la memoria y el aprendizaje no se
tocan.

---

## 1. La idea en una imagen

```
        PC DE JUEGO (Windows)                                PORTÁTIL DE LA IA (Ubuntu)
┌─────────────────────────────────────────┐            ┌──────────────────────────────────┐
│ ARMA 3 (tú hospedas la partida)         │            │ arma3-ia servir                  │
│                                         │            │                                  │
│  Mod @arma3ia (solo en el servidor)     │            │  PuenteArma3  (= Mundo + Zeus)   │
│   · SQF: cada 10 s junta lo que ve cada │            │   · servidor TCP 0.0.0.0:7676    │
│     bando y lo manda                    │   red de   │   · traduce JSON ⇄ objetos       │
│   · SQF: ejecuta órdenes (waypoints,    │   casa     │                                  │
│     crear grupos, clima, decorado...)   │   (TCP)    │  Zeus + NPC + memoria (SQLite)   │
│                                         │◄──────────►│                                  │
│  arma3ia_x64.dll (extensión)            │  líneas    │  Ollama qwen3:4b (GPU, local)    │
│   · abre la conexión TCP                │  JSON      │   · solo escucha en localhost    │
│   · hilo propio: nunca congela el juego │            │                                  │
└─────────────────────────────────────────┘            └──────────────────────────────────┘
       ▲  tus amigos se conectan a ARMA como siempre (puertos 2302-2306 UDP)
```

Tres piezas nuevas:

| Pieza | Dónde corre | Lenguaje | Qué hace |
|---|---|---|---|
| **PuenteArma3** | portátil de la IA | Python (en este repo) | Servidor TCP. Implementa `Mundo` y `ControlZeus` hablando el protocolo JSON. |
| **Extensión `arma3ia_x64.dll`** | PC de juego, dentro de ARMA | Rust (recomendado) o C++ | El "cable": ARMA no sabe abrir conexiones de red, la extensión sí. |
| **Mod `@arma3ia`** | PC de juego, solo el servidor | SQF (el lenguaje de ARMA) | Los "ojos y manos" de la IA dentro del juego. |

Y opcionalmente una cuarta: **misiones "lienzo"**, una misión vacía por mapa (Altis, Livonia,
Takistan...) donde Zeus monta su misión.

---

## 2. Qué se necesita (requisitos)

### 2.1 En el PC de juego (Windows)

- **ARMA 3** comprado, y para compilar el mod las **Arma 3 Tools** (gratis en Steam) o, mejor,
  **HEMTT** (herramienta de línea de comandos que empaqueta mods en `.pbo`).
- **Hospedar la partida**: hay dos formas.
  - *Hospedar desde el juego* (Multijugador → Hospedar). Lo más sencillo para empezar: tu
    propio ARMA es el servidor. El mod y la DLL se cargan en tu juego.
  - *Servidor dedicado* (`arma3server_x64.exe`) en el mismo PC, y tú entras como un jugador más.
    Consume más RAM (unos 2-4 GB extra), pero separa los scripts de la IA de tu juego y permite
    cargar el mod solo en el servidor con `-serverMod=@arma3ia`. **Recomendado cuando ya
    funcione**, para partidas largas.
- **BattlEye**: el antitrampas bloquea DLL que no estén en su lista blanca. Para una partida
  privada entre amigos lo práctico es **desactivarlo**: en el lanzador (pestaña de parámetros,
  sin BattlEye) si hospedas desde el juego, o `BattlEye = 0;` en `server.cfg` si usas el
  dedicado. Tus amigos pueden entrar igual.
- **Visual C++ Redistributable x64** (si la DLL se hace en C++; con Rust normalmente no hace falta).

### 2.2 Mods de ARMA

| Mod | ¿Hace falta? | Por qué |
|---|---|---|
| **CBA_A3** | Muy recomendable | Funciones de ayuda: bucles por tiempo (`CBA_fnc_addPerFrameHandler`), mapas de datos (hashes), eventos entre máquinas y **JSON** (`CBA_fnc_encodeJSON` / `CBA_fnc_parseJSON`). Casi todos los mods realistas ya lo piden. |
| **ACE3** | Opcional (realismo) | Funciona, pero el puente debe contar como "baja" a los inconscientes de ACE (`ace_medical` cambia qué significa estar herido). |
| **LAMBS Danger / VCOM AI** | **Cuidado** | Son otras "IA mejores" que toman el control de los grupos. Pelearían con la nuestra. O no se cargan, o el puente las desactiva en los grupos de la IA (`group setVariable ["lambs_danger_disableGroupAI", true]`). Se pueden dejar para los grupos que la IA no controle. |
| **TFAR / ACRE2** | Más adelante (voz) | Para que los NPC hablen por radio con voz. No hace falta para el primer puente: el chat de texto basta. |
| **Mods de época y facciones** (CUP, RHS, IFA3 / Spearhead 1944 para WW2...) | Según campaña | Zeus pide "infantería alemana de 1944"; alguien tiene que saber qué soldados (*classnames*) existen. Ver la sección 5.4. |
| **Mapas extra** (CUP Terrains: Takistan, Chernarus...) | Según campaña | Zeus solo elige entre los de `zeus.terrenos` en la configuración: tienen que estar instalados en tu PC **y en los de tus amigos**. |
| **ZEN (Zeus Enhanced)** | No | Nuestra "Zeus" no usa la interfaz de Zeus del juego, usa scripts. ZEN solo sirve si un humano quiere mirar o corregir. |

### 2.3 En el portátil de la IA (Ubuntu)

Ya está casi todo instalado (`scripts/instalar_ubuntu.sh`). Lo que falta:

- **IP fija en la red de casa** para el portátil (reserva DHCP en el router, por ejemplo
  `192.168.1.50`). Si cambia, ARMA no lo encontrará.
- **Abrir el puerto del puente solo a la red local**:
  `sudo ufw allow from 192.168.1.0/24 to any port 7676 proto tcp`.
- **Ollama sigue escuchando solo en `localhost`**: el juego nunca habla con Ollama, habla con
  `arma3-ia`. No hay que exponerlo.
- **Que el portátil no se duerma** con la tapa cerrada ni se apague el wifi (mejor por cable).

### 2.4 La red

- Los dos PC en la misma red (cable mejor que wifi: menos cortes).
- Comprobación previa sin juego: en Ubuntu `nc -l 7676`, y en Windows (PowerShell)
  `Test-NetConnection 192.168.1.50 -Port 7676`. Si sale `TcpTestSucceeded : True`, la red está lista.
- Si tus amigos juegan desde sus casas, **no cambia nada del puente**: ellos se conectan a tu
  ARMA por internet (puertos 2302-2306 UDP en tu router), y el puente sigue siendo solo entre
  tus dos PC.

---

## 3. Cómo se comunica (el protocolo, en sencillo)

Por el cable viajan **líneas de texto JSON**, una por mensaje, ya definidas en
`docs/protocolo-puente.md`:

```json
{"v": 1, "tipo": "observacion", "bando": "opfor", "datos": { ... }}
```

### 3.1 Quién empieza

**ARMA llama a la IA** (la IA es el servidor TCP, ARMA el cliente). Así el portátil puede
estar siempre encendido esperando, y cuando arrancas la misión la extensión se conecta sola y
manda `hola` (versión del protocolo, mapa, nombre de la misión y una contraseña compartida
sencilla para que nadie más de la red se cuele).

### 3.2 El ciclo de una misión

```
 Al empezar
   ARMA → IA   hola            (mapa = worldName, misión, versión)
   ARMA → IA   zeus_terreno    (pueblos, colinas, carreteras reales del mapa)   ← nuevo
   IA  → ARMA  zeus_preparar   (el mision.yaml de Zeus: grupos, objetivos, decorado, hora, clima)
   ARMA → IA   resultados      (qué se pudo colocar y qué no)

 Cada ~10 segundos, para cada bando que lleva la IA
   ARMA → IA   observacion     (lo que ESE bando sabe: niebla de guerra)
   ARMA → IA   chat            (si los jugadores escribieron algo)
   IA  → ARMA  acciones        (órdenes a cada grupo)
   IA  → ARMA  chat            (respuestas de los NPC, radio entre agentes)
   ARMA → IA   resultados      (órdenes aceptadas o rechazadas con motivo)

 Cuando Zeus decide intervenir (cada ~5 min, o cuando ve algo)
   IA  → ARMA  zeus_vista      ARMA responde con TODO (sin niebla de guerra)
   IA  → ARMA  zeus_crear_grupo / zeus_decorado / zeus_condiciones / zeus_anuncio

 Al terminar
   ARMA → IA   fin             (victoria/parcial/derrota, bajas, duración)
   → la IA escribe el informe en CAMPAÑAS/, reflexiona y guarda lecciones
```

### 3.3 Por qué hace falta una extensión (y cómo no congelar el juego)

ARMA solo puede salir de sí mismo con `callExtension`, que llama a una función de una DLL.
Dos problemas:

1. **`callExtension` para el juego hasta que la DLL contesta.** Si la DLL esperase a que el
   modelo piense (2-20 segundos), el juego se quedaría congelado. Solución: la DLL **solo
   encola** el mensaje y vuelve al instante; un hilo propio de la DLL lo manda por TCP, y
   cuando llega la respuesta la entrega al juego con el evento **`ExtensionCallback`**:

   ```sqf
   addMissionEventHandler ["ExtensionCallback", {
       params ["_nombre", "_funcion", "_datos"];
       if (_nombre != "arma3ia") exitWith {};
       [_funcion, _datos] call arma3ia_fnc_recibir;   // acciones, chat, zeus_*...
   }];
   "arma3ia" callExtension ["enviar", [_lineaJson]];   // vuelve al momento
   ```

2. **Tamaño limitado.** Lo que entra y sale por `callExtension`/`ExtensionCallback` tiene un
   límite de unos pocos KB por llamada (del orden de 10 KB). Una observación grande o el
   `mision.yaml` completo no caben de una vez: la DLL los **trocea** y el SQF los junta
   (`parte 1/4`, `2/4`...). Se resuelve una vez en la DLL y ya no se piensa más.

### 3.4 JSON dentro de ARMA

SQF no tiene JSON propio. Dos opciones:

- Con **CBA**: `CBA_fnc_encodeJSON` y `CBA_fnc_parseJSON`. Lo más sencillo.
- Sin CBA: SQF manda arrays con `str` (`[["grp_1","Alfa-1",[1450,1628,0]],...]`), la DLL los
  convierte en JSON; y al revés, la DLL convierte el JSON en array de SQF y el juego lo lee con
  `parseSimpleArray`, que es muy rápido. **Esta es la que recomiendo**: el trabajo pesado queda
  en la DLL y el SQF se queda simple y rápido.

---

## 4. La extensión (DLL)

**Recomendación: Rust con la librería `arma-rs`**. Es la forma más cómoda hoy de hacer
extensiones de ARMA 3: da hecho el registro de funciones, la conversión de tipos y los
*callbacks*, y el compilador evita los fallos de memoria típicos de C++ que tumban el juego.

Funciones que expone (lo mínimo):

| Llamada desde SQF | Qué hace |
|---|---|
| `"arma3ia" callExtension ["conectar", ["192.168.1.50", 7676, "clave"]]` | Abre el hilo de red y se conecta (reintenta solo si se corta). |
| `"arma3ia" callExtension ["enviar", [_datos]]` | Encola un mensaje (lo trocea si es grande). |
| `"arma3ia" callExtension ["estado", []]` | `conectado` / `reconectando` / `sin_ia`. |
| `"arma3ia" callExtension ["cerrar", []]` | Cierra al terminar la misión. |
| *callback* `["arma3ia", "acciones", "..."]` | Lo que manda la IA llega solo por `ExtensionCallback`. |

Detalles que importan:

- Se compila para **Windows 64 bits** (`arma3ia_x64.dll`) y se pone en la raíz del mod
  (`@arma3ia/arma3ia_x64.dll`). Se puede compilar desde el propio Ubuntu con
  `cargo build --target x86_64-pc-windows-gnu` o en Windows con el compilador normal.
- Guarda un **registro** (`arma3ia.log`) junto al servidor: es lo primero que se mira cuando
  algo no va.
- Si la IA no responde, **el juego sigue**: los grupos se quedan con su última orden y la IA
  del propio ARMA se encarga de disparar y cubrirse. Al volver la conexión, se retoma.

**Alternativa sin compilar nada: Pythia.** Es un mod que mete Python dentro de ARMA; con él se
puede escribir el "cable" en Python con `socket`. Es más rápido de probar, pero añade una
dependencia más, va más justo de mantenimiento y es menos robusto en partidas de horas. Sirve
como prototipo de una tarde; para quedarse, la DLL propia.

**Lo que NO recomiendo:** `extDB3` (base de datos como buzón: obliga a montar MySQL y a
preguntar constantemente) ni intercambiar ficheros (lento y frágil).

---

## 5. El mod `@arma3ia` (SQF)

Un mod de servidor con funciones registradas en `CfgFunctions`. Estructura propuesta:

```
@arma3ia/
├── arma3ia_x64.dll
└── addons/arma3ia.pbo        (se genera con HEMTT desde arma/addons/arma3ia/)
      config.cpp               CfgPatches + CfgFunctions
      functions/
        fn_iniciar.sqf          conecta, manda hola y zeus_terreno, arranca el bucle
        fn_bucle.sqf            cada 10 s: observar cada bando IA y enviar
        fn_observar.sqf         construye la observación de un bando (con niebla de guerra)
        fn_vistaGlobal.sqf      todo el mundo, para Zeus
        fn_recibir.sqf          reparte lo que llega de la IA
        fn_ejecutar.sqf         traduce acciones → waypoints/comportamiento
        fn_zeusPreparar.sqf     monta la misión del mision.yaml
        fn_zeusCrearGrupo.sqf   createGroup + createUnit/createVehicle
        fn_zeusDecorado.sqf     composiciones: fortificación, minas, IED, barricada, torre, humo
        fn_zeusCondiciones.sqf  skipTime, setOvercast, setRain, setFog
        fn_chat.sqf             chat de jugadores → IA, y respuestas de NPC → juego
        fn_eventos.sqf          Killed, Hit, objetivos... → lista de eventos
        fn_fin.sqf              resultado de la misión
        fn_catalogo.sqf         escanea facciones y vehículos instalados (ver 5.4)
```

Si usas el **servidor dedicado**, se carga con `-serverMod=@arma3ia` y **tus amigos no
tienen que instalar nada** de esto. Si hospedas desde tu juego, lo cargas tú con `-mod=` y
tampoco hace falta en los demás.

### 5.1 Ojos: construir la observación

Para cada bando que lleva la IA (`blufor`, `opfor`, `independent`):

- **Unidades propias**: `allGroups select {side _x == _lado}`, con id estable
  (`groupId` o una variable puesta al crearlas), posición del líder, efectivos vivos
  (`{alive _x} count units _g`), munición aproximada, si están en combate
  (`behaviour leader _g == "COMBAT"`), y para helicópteros altura, combustible y daño.
- **Contactos (niebla de guerra)**: solo lo que ese bando conoce, con
  `_lider targets [true, 1500]`, `knowsAbout` y `getHideFrom` (posición *estimada*, no la
  real). Se les da una etiqueta estable `C1`, `C2`... **Nunca** se manda la posición exacta de
  un enemigo que no se ha visto: si no, la IA "hace trampas" y se nota.
- **Eventos** desde la última observación: con manejadores `Killed`, `Hit`, `FiredNear`,
  `GetIn`/`GetOut`, aterrizajes, objetivos completados.

### 5.2 Manos: ejecutar órdenes

La tabla de `docs/protocolo-puente.md` ya dice qué hacer con cada acción: `mover` → waypoint
`MOVE`; `atacar` → `SAD` + `setCombatMode "RED"`; `reconocer` → `STEALTH` + `GREEN`;
`embarcar` → `GETIN`; `aterrizar` → `land "GET IN"`; etc.

Reparto de trabajo muy importante: **nuestra IA decide "qué" y "a dónde"; la IA del propio
ARMA decide "cómo" disparar, cubrirse y moverse metro a metro**. Un modelo de lenguaje no
puede apuntar 30 veces por segundo, pero sí decidir como un sargento: "flanquead por el bosque
del norte y esperad mi señal". Por eso el ciclo de 10 segundos está bien.

Antes de aceptar una orden, el SQF comprueba que es posible (el grupo existe, el destino no es
agua, un helicóptero no puede desembarcar en vuelo...) y si no, la rechaza con un motivo en
`resultados`. Ya está previsto así en el protocolo.

### 5.3 Zeus: montar y cambiar el mundo

"Zeus" aquí **no es el módulo de Zeus del juego** sino el servidor haciendo lo que haría un
Zeus humano, por script:

- **`zeus_preparar`**: recibe el `mision.yaml` (terreno, zona, lugares, grupos con su jefe y
  tarea, objetivos, decorado, hora y clima). El SQF comprueba `worldName` contra `terreno`
  (si no coincide, avisa y no arranca), crea los grupos, pone el decorado, crea las tareas de
  los jugadores (`BIS_fnc_taskCreate`) y ajusta hora y clima.
- **Posiciones válidas**: Zeus escribe coordenadas aproximadas; el SQF las corrige al suelo
  real con `findEmptyPosition`, `isFlatEmpty` y `surfaceIsWater`, para que nada aparezca dentro
  de una casa o en el mar.
- **Opcional**: crear un módulo de Zeus para ti (`createVehicle "ModuleCurator_F"` +
  `addCuratorEditableObjects`) y así puedes **ver** desde Zeus todo lo que la IA coloca, sin
  tocar nada.

### 5.4 El catálogo de facciones (el punto más delicado)

Zeus piensa en "pelotón de infantería alemana de 1944" o "pick-up con ametralladora de
insurgentes". ARMA necesita nombres exactos de clase, como `LIB_GER_rifleman` o
`O_G_Offroad_01_armed_F`, que **dependen de los mods instalados**.

Solución: al arrancar, `fn_catalogo.sqf` recorre `CfgGroups`, `CfgVehicles` y `CfgFactionClasses`
de lo que haya instalado y manda a la IA un **catálogo**: facciones, su bando, su época
aproximada y qué grupos y vehículos tienen, clasificados en los tipos que ya usa la IA
(`infanteria`, `motorizada`, `blindado`, `helicoptero`). La IA lo guarda en `BBDD/` y Zeus
elige solo de ahí. Así la misma IA sirve para vanilla, para RHS, para WW2... sin tocar código.

### 5.5 Mapas y misiones "lienzo"

Una misión de ARMA va siempre ligada a un mapa. Por eso se hace **una misión vacía por mapa**
(`arma3ia_lienzo.Altis`, `arma3ia_lienzo.Livonia`, ...), con solo los huecos para jugadores y
la línea que arranca el mod. Al empezar una misión de la campaña, Zeus dice en el informe qué
mapa ha elegido y tú cargas ese lienzo. (Más adelante se puede automatizar el cambio de mapa
con los comandos de administrador del servidor dedicado.)

El mensaje nuevo **`zeus_terreno`** (ARMA → IA) manda los lugares reales del mapa con
`nearestLocations` (ciudades, pueblos, colinas, aeródromos) para que Zeus coloque su historia
en sitios que existen, y no en coordenadas inventadas.

### 5.6 Chat

- **Jugadores → NPC**: evento `HandleChatMessage`. Si hospedas desde tu juego salta en tu
  máquina; con servidor dedicado conviene un pequeño script en los clientes que reenvíe el
  mensaje al servidor (`remoteExec`). Recuerda: **Zeus no obedece el chat**; los mensajes van a
  los NPC, que contestan en personaje.
- **NPC → jugadores**: `sideChat`/`groupChat` desde el líder del grupo (sale con su nombre) o
  `customChat` en un canal propio "Radio IA" para el tráfico entre agentes.

---

## 6. La parte de Python (en este repo)

Lo que hay que añadir en `src/arma3_ia/puente/`:

- **`servidor.py`**: servidor TCP con `asyncio` en `0.0.0.0:7676`; lee líneas, comprueba la
  versión y la contraseña del `hola`, y reparte por tipo con `arma3_ia.puente.decodificar`.
- **`arma3.py`**: `class PuenteArma3(Mundo, ControlZeus)`:
  - `observar(bando)` espera la siguiente `observacion` de ese bando (con tiempo máximo).
  - `ejecutar(bando, acciones)` manda `acciones` y espera `resultados`.
  - `avanzar()` en ARMA no simula nada: espera a que llegue la siguiente ronda.
  - `vista_global()` hoy es síncrona en la interfaz; en ARMA conviene que el SQF mande la vista
    completa cada pocos minutos y el puente devuelva la última recibida (o hacer la interfaz
    `async`; es un cambio pequeño).
  - `crear_grupo`, `cambiar_condiciones`, `colocar_objeto`, `anunciar` → mensajes `zeus_*`.
- **Codificador de los `zeus_*`** en `protocolo.py` (hoy son solo una propuesta en la
  documentación) y de los nuevos `zeus_terreno` y `catalogo`.
- **CLI**: `arma3-ia servir` (espera a ARMA y juega la campaña activa) y
  `arma3-ia puente probar` (un ARMA falso en Python que habla el protocolo, para probar todo
  sin abrir el juego, igual que ya se hace con el LLM falso en los tests).
- **Configuración** nueva en `config.yaml`:

  ```yaml
  puente:
    escuchar: 0.0.0.0
    puerto: 7676
    clave: "cambia-esto"
    ciclo_s: 10            # cada cuánto observa y decide cada bando
    espera_max_s: 30       # si ARMA no contesta, se considera desconectado
  ```

---

## 7. Pasos, en orden

Cada paso se puede probar por separado antes de seguir:

1. **Red**: IP fija al portátil, puerto 7676 abierto a la red de casa, prueba con `nc` y
   `Test-NetConnection` (sección 2.4).
2. **Puente en Python + ARMA falso**: `servidor.py`, `PuenteArma3` y un cliente falso que
   repite una misión del simulador por TCP. Tests sin GPU ni juego. *Aquí todavía no se abre
   ARMA.*
3. **DLL mínima**: `conectar`, `enviar`, `estado` y el *callback*. Prueba desde la consola de
   depuración de ARMA en el editor: `"arma3ia" callExtension ["enviar", ["hola"]]` y ver la
   línea llegar al portátil.
4. **Mod con solo ojos**: misión lienzo en Altis con unos grupos puestos a mano; el SQF manda
   `observacion` cada 10 s y la IA solo *mira* y lo escribe en su registro. Sirve para afinar
   la niebla de guerra y los eventos.
5. **Manos**: la IA manda `acciones` a un grupo enemigo; comprobar que se mueve, ataca y se
   retira con sentido. Luego varios grupos y los dos bandos.
6. **Chat**: hablar con un NPC por su nombre y que conteste.
7. **Catálogo y terreno**: `fn_catalogo.sqf` y `zeus_terreno`, para que Zeus sepa qué tiene.
8. **Zeus completo**: `zeus_preparar` con un `mision.yaml` real de `CAMPAÑAS/`, y después las
   intervenciones en directo (refuerzos, clima, decorado, anuncios).
9. **Fin y aprendizaje**: mensaje `fin`, informe en `CAMPAÑAS/`, lecciones guardadas. Jugar
   una misión entera.
10. **Partida larga con amigos**: servidor dedicado, una campaña de varias misiones, mirar
    rendimiento y cortes. Si el servidor va lento con muchos NPC, añadir un *headless client*
    (un ARMA sin pantalla que se reparte la carga de la IA del juego).
11. **Después**: voz (TFAR/ACRE + reconocimiento y síntesis de voz en el portátil) y pilotaje
    de helicópteros más fino.

---

## 8. Dificultades conocidas y cómo se resuelven

| Problema | Por qué pasa | Qué hacemos |
|---|---|---|
| El juego se congela | `callExtension` espera a la DLL | La DLL encola y contesta al momento; respuestas por `ExtensionCallback`. |
| Mensajes cortados | Límite de tamaño por llamada | Troceado en la DLL. |
| BattlEye bloquea la DLL | No está en su lista blanca | BattlEye desactivado en partidas privadas. |
| La IA tarda en decidir | `qwen3:4b` en una RTX 3050 tarda unos segundos por decisión, y hay varios grupos | Ciclo de ~10 s, decisiones en paralelo (ya está hecho), reglas de doctrina si tarda demasiado (ya está hecho), y la IA de ARMA cubre lo inmediato. |
| Choque con LAMBS / VCOM | Dos "cerebros" para el mismo grupo | No cargarlos, o desactivarlos en los grupos de la IA. |
| Órdenes que "no hacen nada" | **Localidad** en ARMA: algunos comandos solo funcionan en la máquina dueña del grupo (sobre todo con *headless client*) | Ejecutar las órdenes con `remoteExec` en `groupOwner` del grupo. |
| Soldados que no existen | Classnames distintos según los mods | Catálogo de facciones (5.4). |
| Cosas que aparecen en el mar o dentro de edificios | Zeus no ve el mapa | `zeus_terreno` + corrección de posiciones en SQF. |
| La IA "hace trampas" | Mandar posiciones reales del enemigo | Contactos solo con `targets`/`getHideFrom`. |
| Se corta la red a mitad | Wifi, portátil que se duerme | Cable, reconexión automática en la DLL, la partida sigue sin la IA mientras tanto. |
| Contar bajas con ACE | Inconscientes ≠ muertos | Leer el estado de ACE en `fn_observar`. |
| Rendimiento del servidor | Muchos NPC y scripts | Bucle cada 10 s (no cada fotograma), servidor dedicado, *headless client*. |

---

## 9. En resumen

- Hace falta: **CBA**, una **DLL propia en Rust** (o Pythia para probar), un **mod de servidor
  en SQF**, una **misión lienzo por mapa**, y en Python un **`PuenteArma3`** que hable el
  protocolo que ya existe.
- Se comunican por **TCP en tu red de casa**, con **líneas JSON**: ARMA cuenta lo que ve cada
  bando, la IA contesta con órdenes y Zeus monta y cambia el mundo con los mensajes `zeus_*`.
- Se construye **de dentro hacia fuera**: primero Python con un ARMA falso, luego la DLL,
  luego ojos, manos, chat y por último Zeus completo. Cada paso se prueba solo.
- Lo más delicado no es la red, sino **el catálogo de facciones**, **las posiciones en el mapa**
  y **la latencia del modelo**; las tres tienen solución prevista arriba.
