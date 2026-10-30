#!/usr/bin/env bash
# Instalación del PC de la IA en Ubuntu 24.04.
# Pensado para portátiles híbridos (p. ej. RTX 3050 6 GB + Radeon 680M integrada): Ollama tiene que
# usar la NVIDIA, no la integrada.
# Uso:  bash scripts/instalar_ubuntu.sh [modelo]
#   Sin modelo: qwen3:4b con menos de 10 GB de VRAM, qwen3:8b con 10 GB o más.
# Se puede ejecutar varias veces: salta lo que ya esté hecho.
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

paso() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
aviso() { printf '\033[33m%s\033[0m\n' "$*"; }

paso "Paquetes del sistema"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl pciutils ubuntu-drivers-common

paso "Driver de NVIDIA"
if ! lspci | grep -qi 'nvidia'; then
    aviso "No veo ninguna GPU NVIDIA (lspci). La IA funcionará en la CPU y será muy lenta."
elif command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
    sudo ubuntu-drivers install
    echo "Driver instalado. REINICIA el equipo (sudo reboot) y vuelve a lanzar este script."
    exit 0
fi

# Portátil híbrido: con PRIME en modo "nvidia" la gráfica dedicada queda siempre encendida y no se
# duerme entre consultas (en modo "on-demand" puede tardar en despertar).
if command -v prime-select >/dev/null; then
    actual="$(prime-select query 2>/dev/null || true)"
    if [ "$actual" != "nvidia" ]; then
        sudo prime-select nvidia
        aviso "PRIME cambiado a 'nvidia' (antes: ${actual:-desconocido}). Reinicia cuando termine el script."
    fi
fi

VRAM_MB=0
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
fi
if [ -n "${1:-}" ]; then
    MODELO="$1"
elif [ "${VRAM_MB:-0}" -ge 10000 ]; then
    MODELO="qwen3:8b"
else
    MODELO="qwen3:4b"
fi
# Contexto y consultas simultáneas según lo que quepa en la VRAM.
if [ "${VRAM_MB:-0}" -lt 10000 ] && [[ "$MODELO" == *8b* ]]; then
    NUM_CTX=4096; PARALELO=1
    aviso "$MODELO en ${VRAM_MB} MB de VRAM va muy justo: contexto 4096 y una consulta a la vez."
else
    NUM_CTX=8192; PARALELO=2
fi
echo "VRAM: ${VRAM_MB} MB | modelo: $MODELO | contexto: $NUM_CTX | consultas a la vez: $PARALELO"

paso "Ollama"
if ! command -v ollama >/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
# Solo la NVIDIA (la integrada AMD queda fuera), atención rápida y caché KV a 8 bits para ahorrar VRAM.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/arma3-ia.conf >/dev/null <<CONF
[Service]
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="ROCR_VISIBLE_DEVICES=-1"
Environment="HIP_VISIBLE_DEVICES=-1"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=$PARALELO"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sudo systemctl restart ollama
sleep 3
if journalctl -u ollama --since "1 min ago" --no-pager 2>/dev/null | grep -qiE 'no compatible GPUs|cpu only'; then
    aviso "Ollama dice que no encuentra la GPU NVIDIA. Revisa 'nvidia-smi' y 'journalctl -u ollama'."
fi

paso "Modelo $MODELO"
ollama pull "$MODELO"

paso "Entorno de Python"
cd "$RAIZ"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
[ -f config/config.yaml ] || cp config/config.ejemplo.yaml config/config.yaml
sed -i "s/^  modelo: .*/  modelo: $MODELO/" config/config.yaml
sed -i "s/^  num_ctx: [0-9]*/  num_ctx: $NUM_CTX/" config/config.yaml
sed -i "s/^  max_concurrencia: [0-9]*/  max_concurrencia: $PARALELO/" config/config.yaml
mkdir -p datos

paso "Comprobación"
.venv/bin/pytest -q
.venv/bin/arma3-ia comprobar   # dice también si el modelo cabe entero en la GPU
ollama ps

cat <<FIN

Listo. En 'ollama ps' la columna PROCESSOR debe decir "100% GPU".
Prueba una campaña:
  source .venv/bin/activate
  arma3-ia campana nueva "una campaña en Normandía, 1944"
  arma3-ia campana jugar --interactivo
FIN
