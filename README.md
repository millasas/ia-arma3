# IA para ARMA 3

Una IA local que hace de **Zeus** (el director de juego) en ARMA 3 y, a la vez, es **cada uno de
los NPC**. Corre en un PC dedicado con un modelo de lenguaje pequeño en Ollama.

**Tú solo pides la campaña**, como quieras, con las misiones y la duración que quieras:

```bash
arma3-ia campana nueva "campaña de 1944 para volar un puente en Normandía, 4 misiones de 60 minutos"
arma3-ia campana jugar
```

y Zeus hace **todo lo demás**, sin que tenga que pedírselo nadie:

1. **Inventa la campaña**: la época, los bandos, la doctrina, el estilo de radio, la historia,
   los personajes con nombre y carácter, un antagonista y **un solo objetivo final**.
2. **Lo divide en pasos**: exactamente las misiones que pediste, cada una con su objetivo y su
   problema (un inconveniente, algo que se tuerce). Algunos pasos son críticos.
3. **Elige el mapa y lo moldea**: escoge el terreno de ARMA 3 (de los que tienes instalados) y
   la zona, y coloca el decorado: fortificaciones, puestos de control, minas, IED, barricadas,
   alambradas, ruinas, restos de vehículos, humo...
4. **Crea cada misión**: dónde está cada grupo, quién lo manda, qué tiene que hacer cada bando,
   la hora, el clima y las sorpresas que pasarán a mitad de misión (refuerzos, niebla...).
5. **Dirige la partida en directo** por su cuenta: complica las cosas si vais sobrados o da un
   respiro si estáis al límite. No obedece peticiones del chat: decide él.
6. **Es cada NPC**: cada grupo, aliado o enemigo, tiene un jefe con nombre, rasgos e historia,
   y decide por su cuenta según su tarea, lo que ve y su carácter. Puedes hablarle por radio.
7. **Lleva la historia**: los personajes recuerdan y pueden morir, las derrotas gastan margen
   de error, entre misión y misión surgen imprevistos, y **la campaña puede fracasar**.

Todavía no se conecta a ARMA 3: se juega en un **simulador de texto** incluido. El puente con el
juego es el siguiente paso y ya tiene su interfaz y su protocolo
([docs/protocolo-puente.md](docs/protocolo-puente.md)).

Qué se ha hecho en esta versión y cómo está ordenado, explicado sin tecnicismos:
[INFORME.md](INFORME.md).

## Carpetas

```
ia-arma3/
  BBDD/          la base de datos (campañas, misiones, lo que pasó, lore y lo aprendido)
  CAMPAÑAS/      una carpeta por campaña: ficha, misiones e informes en texto
  config/        tu configuración (config.yaml) y el ejemplo comentado
  src/arma3_ia/  el programa
  tests/         las pruebas automáticas
  docs/          arquitectura y protocolo del futuro puente con ARMA 3
  scripts/       instalación en Ubuntu
```

## Instalación en el PC de la IA

Pensado para el equipo real del proyecto: un **portátil con RTX 3050 de 6 GB** (más una gráfica
integrada AMD Radeon 680M), Ryzen 7 y 16 GB de RAM. Con 6 GB el modelo por defecto es
**`qwen3:4b`**, que cabe entero en la GPU con margen.

Recomendado: **Ubuntu 24.04 LTS** instalado directamente (sin máquinas virtuales), así la GPU
queda entera para el modelo.

### Opción rápida (script)

```bash
sudo apt-get install -y git
git clone https://github.com/millasas/ia-arma3.git
cd ia-arma3
bash scripts/instalar_ubuntu.sh          # driver, PRIME, Ollama solo en la NVIDIA, modelo y proyecto
```

El script mira cuánta VRAM hay y elige el modelo (`qwen3:4b` con menos de 10 GB). Si instala el
driver de NVIDIA o cambia PRIME te pedirá reiniciar: reinicia y vuelve a lanzarlo.

### Paso a paso

1. **Driver de NVIDIA y gráfica dedicada** (portátil híbrido)

   ```bash
   sudo ubuntu-drivers install
   sudo prime-select nvidia   # la NVIDIA siempre encendida, no se duerme entre consultas
   sudo reboot
   nvidia-smi                 # debe mostrar la RTX 3050 Laptop con 6 GB (6144 MiB)
   ```

2. **Ollama solo en la NVIDIA** y el modelo

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull qwen3:4b
   ```

   Ajustes para 6 GB (`sudo systemctl edit ollama` y añade). Las tres primeras líneas hacen
   que Ollama use la NVIDIA e ignore la integrada AMD:

   ```ini
   [Service]
   Environment="CUDA_VISIBLE_DEVICES=0"
   Environment="ROCR_VISIBLE_DEVICES=-1"
   Environment="HIP_VISIBLE_DEVICES=-1"
   Environment="OLLAMA_FLASH_ATTENTION=1"
   Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
   Environment="OLLAMA_NUM_PARALLEL=2"
   Environment="OLLAMA_MAX_LOADED_MODELS=1"
   Environment="OLLAMA_KEEP_ALIVE=30m"
   ```

   Después `sudo systemctl restart ollama`. Comprueba con `ollama ps` (tras una consulta) que la
   columna PROCESSOR dice **100% GPU**.

3. **El proyecto** (Python 3.10 o superior)

   ```bash
   git clone https://github.com/millasas/ia-arma3.git && cd ia-arma3
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   cp config/config.ejemplo.yaml config/config.yaml
   pytest                       # todo debe pasar (no necesita GPU)
   arma3-ia comprobar           # Ollama, el modelo, la velocidad y si cabe entero en la GPU
   ```

Al ser un portátil: déjalo **enchufado** y con el modo de energía en rendimiento (en batería la
GPU baja mucho de velocidad), y con buena ventilación, porque la GPU va a trabajar seguido.

### Qué modelo usar

| modelo        | tamaño (Q4) | en 6 GB | comentario |
|---------------|-------------|---------|------------|
| `qwen3:4b`    | ~2,5 GB     | entero, contexto 8192, 2 consultas a la vez | **por defecto**. Buen JSON y buen español; `pensar: false` para tiempo real. |
| `qwen3:8b`    | ~5,2 GB     | muy justo: `num_ctx: 4096`, `max_concurrencia: 1` y `OLLAMA_NUM_PARALLEL=1` | más listo pero más lento; puede que alguna capa vaya a la CPU (lo dice `arma3-ia comprobar`). Mejor para preparar campañas que en partida. |
| `llama3.2:3b` | ~2 GB       | entero | el más rápido si la 4B no llega; algo peor en español. Pon `pensar: null`. |

Velocidad orientativa en la RTX 3050 de 6 GB: `qwen3:4b` unos 35-50 tokens/s, así que una
decisión (100-250 tokens) tarda 3-7 s. Zeus preparando una misión entera puede tardar 20-40 s,
pero eso se hace antes de empezar. Si una decisión tarda más que `timeout_decision_s`, decide la
doctrina por reglas y la partida no se para.

Para cambiarlo: `ollama pull <modelo>` y edita `llm.modelo` en `config/config.yaml` (o usa la
variable `ARMA3IA_LLM_MODELO`).

## Uso

### Campañas

```bash
arma3-ia campana nueva "lo que quieras"     # si no dices misiones y duración, te las pregunta
arma3-ia campana nueva "una guerrilla en la selva" --misiones 5 --duracion 90
arma3-ia campana jugar                      # Zeus prepara, dirige y cierra la siguiente misión
arma3-ia campana jugar --todas              # juega seguidas todas las que quedan
arma3-ia campana jugar --interactivo        # entre paso y paso hablas por radio con los NPC
arma3-ia campana estado                     # pasos, personajes vivos y caídos, margen de error
arma3-ia campana lista                      # campañas guardadas y lo que ocupan
arma3-ia campana borrar <id>                # borra una campaña (te pide confirmación)
```

- Después de **cada misión** se escribe un informe corto en
  `CAMPAÑAS/<campaña>/misiones/NN_<misión>/informe.txt`.
- Al **terminar la campaña** se escribe `informe_final.txt` y se te pregunta si quieres borrarla
  de la base de datos para ganar espacio (lo aprendido por la IA se conserva siempre).
- En modo `--interactivo` puedes escribir `Miller, ¿cómo vais?` (habla con ese NPC), cualquier
  otra cosa (la recibe el operador de radio de tu bando), Enter para seguir, `mapa` o `salir`.
- Con `--falso` todo funciona sin GPU, decidiendo por reglas.

### Otras órdenes

```bash
arma3-ia comprobar                           # Ollama, el modelo, la base de datos y el espacio
arma3-ia espacio                             # lo que ocupa la base de datos y cada campaña
arma3-ia lecciones [--rol zeus] [--epoca moderna]   # lo que ha aprendido la IA
arma3-ia entrenar-vuelo --episodios 3000     # práctica de aterrizajes de helicóptero
arma3-ia exportar-dataset                    # decisiones buenas en BBDD/dataset.jsonl (para LoRA)
```

### Espacio en disco

Cada vez que se guarda algo, la IA mide la base de datos, la carpeta `CAMPAÑAS` y el disco
libre. Si se acerca al límite (`bbdd.limite_mb`, 2 GB por defecto) o queda poco disco
(`bbdd.minimo_libre_mb`), avisa; si se pasa, entra en **modo ahorro** (deja de guardar los
textos largos de cada decisión y la radio) y recomienda borrar campañas terminadas.

### Configuración

Todo está en `config/config.yaml` (ver los comentarios de
[config/config.ejemplo.yaml](config/config.ejemplo.yaml)). Lo más habitual:

- `zeus.terrenos`: los terrenos de ARMA 3 que tienes instalados (Zeus solo usa esos).
- `llm.modelo` y `llm.url`: el modelo y dónde está Ollama.
- `bbdd.limite_mb` y `bbdd.minimo_libre_mb`: límites de espacio.
- `campanas.misiones_por_defecto` y `campanas.duracion_mision_min`: solo si no lo dices al pedir.
- `agentes.jugadores_al_mando`: si los jugadores pueden dar órdenes directas a los NPC.

Variables de entorno: `ARMA3IA_CONFIG`, `ARMA3IA_LLM_URL`, `ARMA3IA_LLM_MODELO`,
`ARMA3IA_LLM_PROVEEDOR` (`ollama` o `falso`), `ARMA3IA_DB`, `ARMA3IA_CAMPANAS` y `ARMA3IA_RAIZ`.

## Cómo aprende (resumen)

1. Al terminar cada misión cada rol (mando, jefes de grupo, pilotos, operador de radio) hace un
   **informe post-acción** y guarda hasta 3 lecciones; Zeus apunta si la misión estuvo
   equilibrada.
2. En las siguientes decisiones recupera las lecciones **de su rol y de la misma época**.
3. Las lecciones que no funcionan se archivan; las que se repiten se refuerzan.
4. Las maniobras finas (aterrizar) se aprenden **practicando**.
5. Las decisiones buenas se pueden exportar para **ajustar el modelo** (LoRA) más adelante.

Detalles en [docs/arquitectura.md](docs/arquitectura.md).

## Desarrollo

```bash
pytest                 # pruebas sin GPU (la de Ollama real se omite si no hay servidor)
pytest -m ollama       # prueba contra el Ollama de verdad
ruff check . && mypy   # estilo y tipos
```

## Hoja de ruta

- [x] NPC multiagente con memoria, reflexión y aprendizaje.
- [x] Zeus con control total: campañas por encargo, mapas, decorado, misiones, sorpresas,
      dirección en directo, imprevistos y final.
- [x] Base de datos en el PC de la IA con vigilante de espacio e informes en texto.
- [ ] **Puente con ARMA 3**: extensión (`callExtension` + `ExtensionCallback`) y scripts SQF
      que envían observaciones, colocan el decorado y ejecutan órdenes por la red local.
- [ ] Voz: reconocimiento (Whisper) y síntesis para hablar por radio con los jugadores.
- [ ] Ajuste fino (LoRA) con las misiones exportadas.
