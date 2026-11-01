# Arquitectura

## Una sola IA para todos los papeles

```
  tu encargo ("campaña de 1944 para volar un puente, 4 misiones de 60 minutos")
            │
            ▼
   Zeus.crear_campana ── ambientación (época, bandos, doctrina, radio) + terreno elegido
            │            └─ arco: UN objetivo final, N pasos con objetivo, problema y si son críticos
            ▼
   EstadoCampana (pasos, personajes, margen de error, dificultad) ──► BBDD/ia-arma3.db + CAMPAÑAS/<id>/campana.txt
            │
   Zeus.preparar_mision ── PlanMision (terreno, zona, lugares, decorado, grupos, objetivos, sorpresas)
            │                 └─ construir_escenario lo valida y repara ─► Escenario + sucesos
            ▼                                                            └─► CAMPAÑAS/<id>/misiones/NN/mision.yaml
   DirectorPartida ── sorpresas a su hora + intervenciones propias (refuerzos, hora, clima, decorado)
            │                                         │
            ▼                                         ▼
   Mundo (simulador o ARMA 3)  ◄──────────►  CerebroBando por bando (los dos los lleva la IA)
                                              · comandante opcional · jefes con persona · pilotos · radio
            │
   Zeus.cerrar_mision ── crónica, caídos y recuerdos, dificultad, margen, imprevistos, final
            └─► lore y lecciones en la BBDD, informe.txt (y al final informe_final.txt)
```

El mismo modelo hace de Zeus, de comandante, de cada NPC y de operador de radio. Lo que cambia
es el **rol** (prompt y esquema de respuesta), la **persona** (nombre, rango, rasgos, historia
y recuerdos) y las **lecciones** que se recuperan de la memoria para ese rol y esa época.

## Zeus (`zeus/`)

- **No hay campañas, ambientaciones ni misiones fijas.** Todo sale del encargo. Si el modelo
  falla o tarda, Zeus usa sus reglas (`zeus/reglas.py`) para lo que falte, así que siempre hay
  campaña y misión.
- `reglas.interpretar_encargo` saca del texto cuántas misiones y cuánto dura cada una
  ("5 misiones de 90 minutos", "campaña de 6 horas con 4 misiones"); si no lo dices, la orden
  `campana nueva` lo pregunta.
- `crear_campana` hace dos consultas: la **ambientación** (`AmbientacionPlan` → `PerfilCampana`,
  `ambientacion.py`) con el terreno, y el **arco** (`PlanCampana`). `ajustar_capitulos` deja
  exactamente las misiones pedidas y la última crítica.
- `mapas.py` tiene el catálogo de terrenos (vanilla, DLC y mods conocidos). Zeus solo elige
  entre `zeus.terrenos` de la configuración.
- `preparar_mision` pide un `PlanMision` y `construir_escenario` lo valida: lugares que no
  existen, unidades repetidas, helicópteros en épocas sin ellos, bandos que faltan, terrenos no
  instalados, decorado desconocido, sorpresas fuera de tiempo. El enemigo recibe objetivos
  espejo. La distancia de partida depende de la duración de la misión.
- `campana.py` (`EstadoCampana.registrar`) lleva la cuenta: perder un paso crítico o la última
  misión, o quedarse sin margen de error (`misiones // 3` derrotas), hace fracasar la campaña;
  ganar la última la cumple. Tras cada misión hay un 35 % de **imprevisto**: complica el paso
  siguiente y, si es grave, gasta margen o vuelve crítico el siguiente paso.
- `DirectorPartida` lanza las sorpresas en su minuto y cada `intervenir_cada_s` se plantea
  intervenir por su cuenta. **No atiende peticiones del chat**: el chat es para los NPC.

## Decorado

`ObjetoMapa` (`modelo.py`) es cada cosa que Zeus coloca. En el simulador tiene efectos reales:

| tipo | efecto |
|------|--------|
| fortificación, puesto de control, escombros, ruinas | dan cobertura a su bando |
| minas, IED | hieren a quien los cruza (el IED explota una vez); no se ven hasta que se pisan |
| barricada, alambrada, escombros | frenan a vehículos o a infantería |
| torre de vigilancia | su bando ve más lejos |
| humo | tapa la vista |
| campamento, depósito, restos de vehículos, cráteres | ambientación (y referencias para los NPC) |

En ARMA 3 cada tipo será una composición colocada con `createVehicle`/`createMine`.

## Datos (`memoria/`, `archivo.py`)

- `memoria/almacen.py`: SQLite en `BBDD/ia-arma3.db` (esquema v2). Tablas `campanas`,
  `misiones` (con `campana_id`), `eventos`, `decisiones`, `conversaciones`, `lore`, `lecciones`
  y `habilidades`. Borrar una campaña borra en cascada sus misiones, sucesos, decisiones, chat y
  lore, y compacta el fichero (`VACUUM`); las lecciones se conservan.
- `memoria/espacio.py`: `VigilanteEspacio` se ejecuta tras **cada transacción** (y cada vez que
  se escribe en `CAMPAÑAS/`). Niveles `ok`, `aviso` (80 % del límite o poco disco) y `critico`
  (modo ahorro: decisiones sin los textos largos, sin radio).
- `archivo.py`: ficha de campaña, `mision.yaml`, `informe.txt` por misión e
  `informe_final.txt`.

## Los NPC

Cada grupo tiene un jefe con persona: los rasgos mueven los umbrales de ruptura (un valiente
aguanta más bajas, un nervioso se retira antes) y la agresividad (un agresivo ataca contactos
cercanos aunque no tenga orden). Tareas `patrullar` (con ruta) y `emboscar` (esperar en sigilo y
abrir fuego cuando el enemigo entra en alcance). Ven el decorado que conocen (lo suyo, lo neutral
y lo enemigo cercano, nunca las minas ajenas).

```
                       ┌───────────────────────── CerebroBando (mando.py) ─────────────────────────┐
 chat de jugadores ──► │ Enlace ──radio──► Comandante ──órdenes por radio──► Líderes / Pilotos     │ ──► acciones
                       │   ▲                    │                                  │                │
 observación ────────► │   └── respuestas ◄─────┴──── informes / solicitudes ◄─────┘                │ ──► chat / radio
                       │                                                                            │
                       │  Agente.consultar(): lecciones + prompt ─► LLM (Ollama) ─► JSON validado   │
                       │                                   └── si falla o tarda ─► doctrina (reglas) │
                       └────────────────────────────────────────────────────────────────────────────┘
                                   │ decisiones, eventos, chat                   ▲ lecciones, habilidades
                                   ▼                                             │
                         Memoria SQLite (memoria/almacen.py) ◄── Reflexión post-misión (memoria/reflexion.py)
```

## Piezas

| módulo                 | qué hace |
|------------------------|----------|
| `llm/`                 | Cliente de Ollama (`/api/chat` con *structured outputs*), LLM falso para pruebas, extracción tolerante de JSON y reintento con corrección cuando el modelo responde mal. |
| `agentes/`             | Comandante, líder de escuadra, piloto y enlace. Cada uno tiene su prompt (`prompts.py`), su esquema de respuesta (`esquemas.py`) y su doctrina por reglas (`reglas.py`). |
| `comunicacion.py`      | Red de radio entre agentes: indicativos, alias ("HQ", "alfa 1"), difusión a "todos" y retardo configurable por campaña (unos 30 s en 1944). |
| `mando.py`             | Cerebro de un bando: en cada paso sincroniza agentes, atiende el chat, decide el mando y luego las unidades **en paralelo**. |
| `memoria/`             | Base de datos, vigilante del espacio, recuperación de lecciones y reflexión después de cada misión. |
| `habilidades/`         | Habilidades numéricas que se aprenden por práctica sin LLM (hoy: aterrizaje de helicóptero con un optimizador CEM). |
| `ambientacion.py`      | La ambientación que inventa Zeus: época, bandos, doctrina, restricciones, tono de radio, indicativos. |
| `simulador/`           | Mundo de texto determinista para probar todo sin ARMA: movimiento, detección con niebla de guerra, combate, helicópteros, objetivos y decorado. Los dos bandos los llevan NPC. |
| `puente/`              | Interfaz `Mundo` y protocolo JSON para el futuro puente con ARMA 3 (ver `protocolo-puente.md`). |
| `zeus/`                | Director de juego: campañas, terrenos, planes de misión, decorado, construcción de escenarios, intervenciones en directo y estado de la campaña. |
| `archivo.py`           | Fichas e informes de texto en `CAMPAÑAS/`. |
| `partida.py`           | Bucle mundo ↔ cerebros (con Zeus opcional), registro en memoria y reflexión final. |

## Decisiones de diseño

**Cada agente decide con el LLM, pero nunca depende de él.** Todas las consultas pasan por
`Agente.consultar()`: inyecta lecciones, pide JSON con un esquema, valida con pydantic y, si el
modelo falla, devuelve JSON inválido dos veces o tarda más de `timeout_decision_s`, decide la
doctrina por reglas. En el juego la IA nunca se queda congelada.

**El modelo responde con un esquema, no con texto libre.** Ollama recibe el JSON Schema de la
respuesta (`format`), con los `$ref` resueltos. Los validadores aceptan las variantes típicas
de un modelo pequeño ("Atacar", "RTB", "apoyo aéreo", destinos como lista) y recortan textos
largos, para que un 7-8B sea fiable.

**Solo la información que el bando conoce.** La observación solo contiene contactos detectados
con etiquetas `C1`, `C2`… y precisión, nunca la identidad real del enemigo. El objetivo "destruir"
no revela qué unidades hay que destruir.

**Paralelismo controlado.** Líderes y pilotos deciden a la vez con `asyncio.gather`; un semáforo
en el cliente (`max_concurrencia`, igual que `OLLAMA_NUM_PARALLEL`) evita saturar la GPU. No
todos deciden en cada paso: solo quien tiene motivo (orden nueva, evento propio, mensaje o
intervalo cumplido). Los demás aplican reflejos por reglas (replegarse con muchas bajas, etc.).

**Radio con disciplina.** Las órdenes repetidas en menos de 5 minutos y los informes idénticos
en menos de 2 se descartan. Una orden imposible ("transporte" a una escuadra, apoyo aéreo sin
armamento) no se transmite: vuelve al comandante como aviso en su siguiente decisión.

## Cómo aprende

1. **Lecciones (memoria episódica).** Al terminar una misión, `Reflexion` revisa por rol las
   decisiones, eventos y métricas, y pide al modelo un informe con hasta 3 lecciones por rol.
   Sin modelo usa reglas (p. ej. "aterrizaste con enemigos a menos de 500 m y te estrellaste").
   Una lección casi igual a otra ya guardada la **refuerza** en vez de duplicarse.
2. **Utilidad.** Cada lección que se inyectó en un prompt se marca como usada; si la misión
   supera `umbral_exito`, cuenta como éxito. La utilidad es `(éxitos+1)/(usos+2)`; con 5 usos o
   más y utilidad por debajo de 0,25 se archiva (la IA olvida lo que no funciona).
3. **Recuperación.** Antes de cada decisión se buscan las `k` lecciones más relevantes para
   ese rol y época (palabras en común con la situación, ponderadas por rareza, utilidad y
   peso). Las lecciones de otra época no se mezclan (lo aprendido en 1944 no se usa en 2035); las generales valen para todas.
4. **Habilidades por práctica.** Lo que es control fino (aproximación, tasa de descenso,
   recogida) no se aprende con palabras sino con números: un optimizador CEM ajusta los
   parámetros de aterrizaje para LZ fría y caliente a partir de aterrizajes simulados
   (`entrenar-vuelo`) y de los aterrizajes reales de cada misión (`explorar: true`).
5. **Ajuste fino futuro.** `exportar-dataset` saca en JSONL (formato *messages*) las decisiones
   del modelo en misiones con buena puntuación, listo para entrenar un LoRA del modelo base.

El modelo de vuelo del simulador es un modelo de juguete, no el de ARMA 3. Cuando exista el
puente, los eventos `aterrizaje` del juego real alimentarán la misma habilidad.

## Rendimiento previsto (RTX 3050 de portátil, 6 GB)

`qwen3:4b` en Q4 ocupa unos 2,5 GB; con la caché KV a 8 bits y contexto 8192 caben 2 consultas
a la vez y el total queda por debajo de 5 GB. Esa GPU tiene poco ancho de banda de memoria, así
que genera del orden de 35-50 tokens/s: cada decisión táctica (100-250 tokens) tarda 3-7 s y una
ronda de 4-6 agentes cabe en un ciclo de 15-30 s de juego. Los agentes no deciden todos a la vez
(intervalos de 25-120 s y solo cuando pasa algo), y si una consulta se pasa del tiempo límite
decide la doctrina por reglas.

`qwen3:8b` (~5,2 GB) solo cabe con contexto 4096 y una consulta a la vez, y aun así puede que
alguna capa vaya a la CPU; `arma3-ia comprobar` avisa del porcentaje que queda fuera de la GPU.
En un portátil híbrido Ollama tiene que usar la NVIDIA (`CUDA_VISIBLE_DEVICES=0` y la AMD
desactivada con `ROCR_VISIBLE_DEVICES=-1`); el script de instalación lo deja así.
