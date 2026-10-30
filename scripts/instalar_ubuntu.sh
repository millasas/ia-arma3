#!/usr/bin/env bash
# Instalación del PC de la IA en Ubuntu Server 24.04 (RTX 3060 12 GB).
# Uso:  bash scripts/instalar_ubuntu.sh [modelo]      (por defecto qwen3:8b)
# Se puede ejecutar varias veces: salta lo que ya esté hecho.
set -euo pipefail

MODELO="${1:-qwen3:8b}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

paso() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

paso "Paquetes del sistema"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl ubuntu-drivers-common

paso "Driver de NVIDIA"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
else
    sudo ubuntu-drivers install
    echo "Driver instalado. REINICIA el equipo (sudo reboot) y vuelve a lanzar este script."
    exit 0
fi

paso "Ollama"
if ! command -v ollama >/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
# Ajustes para una sola GPU de 12 GB: atención rápida, caché KV a 8 bits y 2 consultas a la vez.
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/arma3-ia.conf >/dev/null <<'CONF'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sudo systemctl restart ollama
sleep 3

paso "Modelo $MODELO"
ollama pull "$MODELO"

paso "Entorno de Python"
cd "$RAIZ"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
[ -f config/config.yaml ] || cp config/config.ejemplo.yaml config/config.yaml
sed -i "s/^  modelo: .*/  modelo: $MODELO/" config/config.yaml
mkdir -p datos

paso "Comprobación"
.venv/bin/pytest -q
.venv/bin/arma3-ia comprobar

cat <<FIN

Listo. Prueba una misión:
  source .venv/bin/activate
  arma3-ia simular asalto_aldea --mapa 10
FIN
