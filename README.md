# IA para ARMA 3

IA local y multiagente para ARMA 3 que corre en un PC dedicado con un modelo de lenguaje
pequeño (7-8B) en Ollama. Un **comandante** planifica la misión, los **líderes de escuadra** y
los **pilotos** deciden cada uno por su cuenta y se coordinan por radio, y un **enlace** habla
con los jugadores por el chat. Después de cada misión la IA escribe un informe post-acción y
**aprende lecciones** que usa en las siguientes, y adapta su forma de luchar y de hablar a la
**campaña** (moderna, 1944, insurgencia…).

Esta primera fase es solo **el núcleo de la IA**. Todavía no se conecta a ARMA 3: se prueba en
un **simulador de texto** incluido. El puente con el juego es el siguiente paso y ya tiene su
interfaz y su protocolo definidos ([docs/protocolo-puente.md](docs/protocolo-puente.md)).

```
[00:00] 📻 Centinela [a Ángel-1] Ángel-1, aquí Centinela: transporte a Patrulla-2 hasta 006006.
[00:00] 📻 Centinela [a Patrulla-2] Patrulla-2, aquí Centinela: embarque en Ángel-1. Ángel-1 os llevará a 006006.
[00:00] 📻 Ángel-1 [a Centinela] En camino para recoger a Patrulla-2.
[00:30] Patrulla-2 detecta C1: infantería (~5) en 035034, 350 m al SE
[00:30] 💬 Centinela-Radio: Teniente Vega, aquí Centinela-Radio. Recibido, lo transmito al mando.
...
=== MISIÓN TERMINADA: VICTORIA (objetivos cumplidos) ===
  + Nueva lección (piloto): Aterrizaje en una LZ con enemigos cerca: No aterrizar con contactos
    a menos de 500 m: elegir una LZ alternativa más alejada o pedir apoyo para limpiar la zona antes.
```

## Qué incluye

- **Agentes**: comandante, líderes de escuadra, pilotos de helicóptero (transporte, escolta,
  apoyo aéreo) y enlace con los jugadores. Deciden en paralelo y se hablan por una red de radio
  con indicativos y retardo según la época.
- **Nunca se congela**: si el modelo falla, responde mal o tarda demasiado, decide la doctrina
  por reglas y la partida sigue.
- **Memoria persistente** (SQLite): misiones, decisiones, eventos, conversaciones y lecciones.
- **Aprendizaje**: reflexión tras cada misión, lecciones con utilidad que se refuerzan o se
  olvidan, habilidad de aterrizaje que mejora practicando y exportación de datos para un LoRA.
- **Perfiles de campaña**: `moderno`, `ww2`, `insurgencia`, `contrainsurgencia`, `guerra_fria`.
- **Simulador de texto** con niebla de guerra, combate, helicópteros, objetivos y 4 escenarios.
- **Pruebas sin GPU**: 108 tests con un LLM falso y un servidor que imita la API de Ollama.

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
pytest                 # 108 tests, sin GPU (el de Ollama real se omite si no hay servidor)
pytest -m ollama       # prueba contra el Ollama de verdad
ruff check . && mypy   # estilo y tipos
```

## Hoja de ruta

- [x] Núcleo multiagente, memoria, reflexión, perfiles, simulador y pruebas.
- [ ] **Puente con ARMA 3**: extensión (`callExtension` + `ExtensionCallback`) y scripts SQF
      que envían observaciones y ejecutan órdenes por la red local hacia el PC de la IA.
- [ ] Enemigo controlado también por la IA (IA contra IA para autoentrenarse).
- [ ] Voz: reconocimiento (Whisper) y síntesis para hablar por radio con los jugadores.
- [ ] Ajuste fino (LoRA) con las misiones exportadas.
