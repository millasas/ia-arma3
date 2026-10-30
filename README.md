# IA para ARMA 3

Una IA local que hace de **Zeus** en ARMA 3 y, a la vez, es **cada uno de los NPC**. Corre en un
PC dedicado con un modelo de lenguaje pequeño (7-8B) en Ollama.

Tú solo le pides una campaña:

```bash
arma3-ia campana nueva "una campaña de insurgencia por el control de un río, con una emboscada a un convoy"
arma3-ia campana jugar --interactivo
```

y la IA:

1. **Escribe la campaña**: historia, capítulos, personajes con nombre y carácter, y un antagonista.
2. **Prepara cada misión** como haría un Zeus: dónde está cada grupo, qué objetivos tiene cada
   bando, la hora y el clima. El enemigo también tiene su propio plan.
3. **Dirige la partida en directo**: manda refuerzos si los jugadores van sobrados, da un respiro
   si están al límite, hace caer la noche... y obedece al anfitrión por el chat
   (`Zeus, que llueva`, `Zeus, manda 2 tanques enemigos`).
4. **Es cada NPC**: cada grupo, aliado o enemigo, tiene un jefe con nombre, rasgos (veterano,
   nervioso, impulsivo...) e historia, y decide por su cuenta según su orden, lo que ve y su
   carácter. Puedes hablarle por su nombre (`Sargento Miller, ¿cómo lo veis?`) y te contesta
   como esa persona.
5. **Recuerda y aprende**: los personajes recuerdan lo que han vivido y pueden morir; la
   dificultad se ajusta según cómo te va; una derrota cambia la historia (aparece un capítulo
   nuevo); y cada rol (Zeus, mandos, NPC, pilotos) guarda lecciones que usa en las misiones
   siguientes.

Es **un solo modelo** que interpreta todos los papeles: cambia el prompt, la memoria y las
lecciones según a quién le toque decidir. Por eso todo lo que aprende se guarda en la misma base
de datos y se puede exportar para ajustar ese modelo (LoRA) más adelante.

Todavía no se conecta a ARMA 3: se prueba en un **simulador de texto** incluido. El puente con
el juego es el siguiente paso y ya tiene su interfaz y su protocolo definidos
([docs/protocolo-puente.md](docs/protocolo-puente.md)).

```
=== La lucha por Kalat (la_lucha_por_kalat) ===
 → 1. Emboscada en Kalat [emboscada]
[00:00] ⚡ Zeus: Emboscada en Kalat: Un convoy enemigo pasará cerca de Kalat. No debe llegar a su destino.
[03:00] Able detecta C1: motorizada (~4) en 025029, 250 m al NE
[03:00] Gruppe Anton tiene los vehículos inutilizados y ya no puede moverse
[10:00] ⚡ Zeus: refuerzos enemigos: Se oyen motores acercándose desde el noreste...
Lo que cambia en la campaña:
  · Sargento Walt Russo (veterano, prudente) ha caído en «Contraataque tras «Asalto a Cota 112»».
  · Ha sido duro: la próxima misión dará algo de respiro (dificultad 0.80).
  · La derrota cambia la historia: nuevo capítulo «Contraataque tras «Emboscada en Kalat»».
```

## Qué incluye

- **Zeus**: crea campañas y misiones a partir de un encargo en lenguaje normal, interviene
  durante la partida y obedece órdenes por el chat. Guarda cada campaña y la continúa donde se
  dejó.
- **NPC con personalidad**: rasgos que cambian cómo aguantan y cómo atacan, historia, recuerdos
  de misiones anteriores y conversación en personaje.
- **Agentes**: comandante (opcional por bando), líderes de escuadra, pilotos de helicóptero
  (transporte, escolta, apoyo aéreo) y enlace con los jugadores. Tareas de patrulla con ruta y
  emboscada. Los dos bandos los lleva la IA, cada uno con su doctrina. Deciden en paralelo y se hablan por una red de radio
  con indicativos y retardo según la época.
- **Nunca se congela**: si el modelo falla, responde mal o tarda demasiado, decide la doctrina
  por reglas y la partida sigue.
- **Memoria persistente** (SQLite): misiones, decisiones, eventos, conversaciones y lecciones.
- **Aprendizaje**: reflexión tras cada misión, lecciones con utilidad que se refuerzan o se
  olvidan, habilidad de aterrizaje que mejora practicando y exportación de datos para un LoRA.
- **Perfiles de campaña**: `moderno`, `ww2`, `insurgencia`, `contrainsurgencia`, `guerra_fria`.
- **Simulador de texto** con niebla de guerra, combate, helicópteros, objetivos y 4 escenarios.
- **Pruebas sin GPU**: 136 tests con un LLM falso y un servidor que imita la API de Ollama.

Detalles en [docs/arquitectura.md](docs/arquitectura.md).

## Instalación en el PC de la IA

Recomendado: **Ubuntu Server 24.04 LTS** instalado directamente (sin Proxmox ni máquinas
virtuales): la GPU queda entera para el modelo, sin configurar *passthrough*, y los drivers de
NVIDIA y Ollama se instalan en dos órdenes. Con 16 GB de RAM y una RTX 3060 de 12 GB, un modelo
8B cabe completo en la GPU.

### Opción rápida (script)

```bash
sudo apt-get install -y git
git clone https://github.com/millasas/ia-arma3.git
cd ia-arma3
bash scripts/instalar_ubuntu.sh          # instala driver, Ollama, qwen3:8b y el proyecto
```

Si el script instala el driver de NVIDIA te pedirá reiniciar: reinicia y vuelve a lanzarlo.

### Paso a paso

1. **Driver de NVIDIA**

   ```bash
   sudo ubuntu-drivers install
   sudo reboot
   nvidia-smi                 # debe mostrar la RTX 3060 con 12 GB
   ```

2. **Ollama y el modelo**

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull qwen3:8b
   ```

   Ajustes recomendados para 12 GB (`sudo systemctl edit ollama` y añade):

   ```ini
   [Service]
   Environment="OLLAMA_FLASH_ATTENTION=1"
   Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
   Environment="OLLAMA_NUM_PARALLEL=2"
   Environment="OLLAMA_KEEP_ALIVE=30m"
   ```

   Después `sudo systemctl restart ollama`.

3. **El proyecto** (Python 3.10 o superior)

   ```bash
   git clone https://github.com/millasas/ia-arma3.git && cd ia-arma3
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   cp config/config.ejemplo.yaml config/config.yaml
   pytest                       # todo debe pasar (no necesita GPU)
   arma3-ia comprobar           # comprueba Ollama, el modelo y la velocidad
   ```

### Qué modelo usar

| modelo        | VRAM aprox. | comentario |
|---------------|-------------|------------|
| `qwen3:8b`    | ~6 GB       | **por defecto**. Muy bueno con JSON y en español. `pensar: false` para tiempo real. |
| `llama3.1:8b` | ~5 GB       | alternativa sólida; pon `pensar: null` en la configuración. |
| `gemma3:12b`  | ~9 GB       | algo más listo y más lento; cabe en 12 GB con contexto 8192. |
| `qwen3:14b`   | ~10 GB      | el mayor que cabe; úsalo para `autoentrenar`, no en partidas en vivo. |

Para cambiarlo: `ollama pull <modelo>` y edita `llm.modelo` en `config/config.yaml` (o usa la
variable `ARMA3IA_LLM_MODELO`).

## Uso

### Campañas con Zeus (lo principal)

```bash
arma3-ia campana nueva "campaña de la segunda guerra mundial en Normandía"   # Zeus la escribe
arma3-ia campana jugar --interactivo        # juega el capítulo actual; tú escribes en el chat
arma3-ia campana jugar --todas              # juega todos los capítulos que quedan seguidos
arma3-ia campana estado                     # capítulos, personajes vivos y caídos, dificultad
arma3-ia campana lista                      # todas las campañas guardadas
```

En el chat interactivo:

- `Zeus, que se haga de noche` / `Zeus, manda refuerzos enemigos` / `Zeus, anuncia que llega
  la artillería`: órdenes al director de juego.
- `Miller, ¿cómo vais?` o `Able, ¿qué veis?`: habla directamente con ese NPC.
- Cualquier otra cosa la recibe el operador de radio de tu bando.

`--sin-intervenciones` desactiva las intervenciones automáticas de Zeus (solo obedece).
Con `--falso` todo funciona sin GPU, decidiendo por reglas.

### Misiones sueltas y otras órdenes

```bash
arma3-ia escenarios                          # lista de escenarios
arma3-ia perfiles                            # lista de campañas
arma3-ia simular asalto_aldea --mapa 10      # juega una misión y muestra el mapa cada 10 pasos
arma3-ia simular defensa_granja --perfil ww2 --interactivo   # tú escribes en el chat como jugador
arma3-ia simular rescate_helicoptero --falso # sin GPU: decide la doctrina por reglas
arma3-ia chat                                # habla con el operador de radio fuera de misión
arma3-ia autoentrenar --misiones 50 --vuelo 2000   # déjala aprendiendo sola
arma3-ia entrenar-vuelo --episodios 3000     # solo práctica de aterrizajes
arma3-ia lecciones                           # qué ha aprendido
arma3-ia memoria --resumen                   # historial de misiones
arma3-ia exportar-dataset datos/dataset.jsonl
```

En modo `--interactivo`, entre cada paso puedes escribir como si fueras un jugador
("Mando, necesitamos apoyo en la aldea"), pulsar Enter para seguir, `mapa` o `salir`.

### Configuración

Todo está en `config/config.yaml` (ver los comentarios de
[config/config.ejemplo.yaml](config/config.ejemplo.yaml)). Lo más habitual:

- `llm.url`: dónde está Ollama (por defecto en el mismo PC).
- `agentes.jugadores_al_mando`: si los jugadores pueden dar órdenes directas a las escuadras.
- `agentes.retransmitir_radio`: mostrar o no a los jugadores la radio entre agentes.
- `habilidades.explorar`: seguir probando variantes de maniobras durante las misiones.
- `perfil`: campaña por defecto.

Variables de entorno: `ARMA3IA_CONFIG`, `ARMA3IA_LLM_URL`, `ARMA3IA_LLM_MODELO`,
`ARMA3IA_LLM_PROVEEDOR` (`ollama` o `falso`) y `ARMA3IA_DB`.

### Crear una campaña o un escenario

- Campañas: copia un YAML de `src/arma3_ia/campanas/perfiles/` y cambia época, doctrina,
  restricciones, estilo de radio e indicativos. `arma3-ia simular X --perfil ruta/a/mi.yaml`.
- Escenarios: copia uno de `src/arma3_ia/simulador/escenarios/` y pásalo como ruta:
  `arma3-ia simular mis_escenarios/puente.yaml`.

## Cómo aprende (resumen)

1. Al terminar cada misión hace un **informe post-acción** por rol y guarda hasta 3 lecciones
   por rol ("EVITA: aterrizar con contactos a menos de 500 m…").
2. En las siguientes decisiones recupera las lecciones **relevantes para su rol y su campaña** y
   las añade al prompt.
3. Cada lección lleva la cuenta de cuántas veces se usó y en cuántas misiones salió bien. Las
   que no funcionan se archivan; las que se repiten se refuerzan.
4. Las maniobras finas (aterrizar) se aprenden **practicando**: de ~35 % de aterrizajes limpios
   a ~90 % tras unos cientos de intentos simulados.
5. Las decisiones buenas se pueden exportar para **ajustar el modelo** (LoRA) más adelante.

## Desarrollo

```bash
pytest                 # 136 tests, sin GPU (el de Ollama real se omite si no hay servidor)
pytest -m ollama       # prueba contra el Ollama de verdad
ruff check . && mypy   # estilo y tipos
```

## Hoja de ruta

- [x] Núcleo multiagente, memoria, reflexión, perfiles, simulador y pruebas.
- [x] Zeus: campañas, misiones generadas, dirección en directo y órdenes por chat.
- [x] NPC con personalidad, memoria de campaña y conversación en personaje.
- [x] Enemigo controlado también por la IA, con su propia doctrina.
- [ ] **Puente con ARMA 3**: extensión (`callExtension` + `ExtensionCallback`) y scripts SQF
      que envían observaciones y ejecutan órdenes por la red local hacia el PC de la IA.
- [ ] Voz: reconocimiento (Whisper) y síntesis para hablar por radio con los jugadores.
- [ ] Ajuste fino (LoRA) con las misiones exportadas.
