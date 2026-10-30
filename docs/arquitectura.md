# Arquitectura del núcleo

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
| `comunicacion.py`      | Red de radio entre agentes: indicativos, alias ("HQ", "alfa 1"), difusión a "todos" y retardo configurable por campaña (30 s en 1944). |
| `mando.py`             | Cerebro de un bando: en cada paso sincroniza agentes, atiende el chat, decide el mando y luego las unidades **en paralelo**. |
| `memoria/`             | SQLite con misiones, eventos, decisiones, conversaciones, lecciones y habilidades. Recuperación de lecciones relevantes y reflexión después de cada misión. |
| `habilidades/`         | Habilidades numéricas que se aprenden por práctica sin LLM (hoy: aterrizaje de helicóptero con un optimizador CEM). |
| `campanas/`            | Perfiles de campaña en YAML: época, doctrina, restricciones, tono de radio, indicativos. |
| `simulador/`           | Mundo de texto determinista para probar todo sin ARMA: movimiento, detección con niebla de guerra, combate, helicópteros, objetivos y enemigo por guion. |
| `puente/`              | Interfaz `Mundo` y protocolo JSON para el futuro puente con ARMA 3 (ver `protocolo-puente.md`). |
| `partida.py`           | Bucle mundo ↔ cerebros, registro en memoria y reflexión final. |

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
   ese rol y campaña (palabras en común con la situación, ponderadas por rareza, utilidad y
   peso). Las lecciones de otra campaña no se mezclan; las generales valen para todas.
4. **Habilidades por práctica.** Lo que es control fino (aproximación, tasa de descenso,
   recogida) no se aprende con palabras sino con números: un optimizador CEM ajusta los
   parámetros de aterrizaje para LZ fría y caliente a partir de aterrizajes simulados
   (`entrenar-vuelo`) y de los aterrizajes reales de cada misión (`explorar: true`).
5. **Ajuste fino futuro.** `exportar-dataset` saca en JSONL (formato *messages*) las decisiones
   del modelo en misiones con buena puntuación, listo para entrenar un LoRA del modelo base.

El modelo de vuelo del simulador es un modelo de juguete, no el de ARMA 3. Cuando exista el
puente, los eventos `aterrizaje` del juego real alimentarán la misma habilidad.

## Rendimiento previsto (RTX 3060 12 GB)

Un modelo 8B en Q4 ocupa unos 5-6 GB de VRAM y genera del orden de 40-60 tokens/s. Una decisión
táctica son 100-250 tokens de salida, así que cada consulta tarda 2-6 s; con 2 consultas en
paralelo, una ronda completa de 4-6 agentes cabe holgadamente en un ciclo de 10-30 s de juego.
Con `pensar: true` (qwen3) la calidad sube algo pero cada consulta tarda varias veces más.
