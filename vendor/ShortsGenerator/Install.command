#!/bin/bash

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

clear
echo "===================================================="
echo "  Instalator AI ViralCutter by Opitkovanie"
echo "===================================================="
echo ""
echo "Ten instalator przygotuje:"
echo "  - Homebrew, jesli nie jest jeszcze zainstalowany"
echo "  - Python 3.11"
echo "  - FFmpeg / FFprobe"
echo "  - lokalne srodowisko Python .venv"
echo "  - biblioteki wymagane przez aplikacje app.py"
echo ""

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Ten plik instalacyjny jest przygotowany dla macOS."
  echo "Nacisnij dowolny klawisz, aby zamknac."
  read -n 1 -s
  exit 1
fi

ensure_brew_in_path() {
  if [[ -x "/opt/homebrew/bin/brew" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x "/usr/local/bin/brew" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

ensure_brew_in_path

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew nie jest zainstalowany. Rozpoczynam instalacje..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ensure_brew_in_path

  if [[ -x "/opt/homebrew/bin/brew" ]] && ! grep -q 'opt/homebrew/bin/brew shellenv' "$HOME/.zprofile" 2>/dev/null; then
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
  elif [[ -x "/usr/local/bin/brew" ]] && ! grep -q 'usr/local/bin/brew shellenv' "$HOME/.zprofile" 2>/dev/null; then
    echo 'eval "$(/usr/local/bin/brew shellenv)"' >> "$HOME/.zprofile"
  fi
else
  echo "Homebrew: OK"
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Nie udalo sie dodac Homebrew do sciezki. Zamknij terminal, otworz ponownie i uruchom instalator jeszcze raz."
  read -n 1 -s
  exit 1
fi

echo ""
echo "Instaluje / aktualizuje Python 3.11 i FFmpeg..."
brew install python@3.11 ffmpeg

if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.11)"
elif [[ -x "$(brew --prefix python@3.11)/bin/python3.11" ]]; then
  PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
else
  echo "Nie znaleziono python3.11 po instalacji."
  read -n 1 -s
  exit 1
fi

echo ""
echo "Python: $($PYTHON_BIN --version)"
echo "FFmpeg: $(ffmpeg -version | head -n 1)"

echo ""
echo "Tworze lokalne srodowisko aplikacji..."
"$PYTHON_BIN" -m venv .venv
source ".venv/bin/activate"

echo ""
echo "Aktualizuje instalator pakietow Python..."
python -m pip install --upgrade pip setuptools wheel

echo ""
echo "Instaluje biblioteki wymagane przez app.py..."
python -m pip install -r requirements.txt

if [[ "$(uname -m)" == "arm64" ]]; then
  echo ""
  echo "Wykryto Apple Silicon. Instaluje szybki silnik Whisper MLX..."
  python -m pip install mlx-whisper
else
  echo ""
  echo "Nie wykryto Apple Silicon. Aplikacja uzyje faster-whisper zamiast mlx-whisper."
fi

echo ""
echo "Przygotowuje foldery robocze..."
mkdir -p workspace/downloads workspace/sessions workspace/favorites models

echo ""
echo "Sprawdzam podstawowe importy aplikacji..."
python - <<'PY'
import streamlit
import yt_dlp
from google import genai
from PIL import ImageFont
import cv2
from ultralytics import YOLO
from faster_whisper import WhisperModel
print("Importy Python: OK")
PY

echo ""
echo "===================================================="
echo "Instalacja zakonczona pomyslnie."
echo "Teraz uruchom aplikacje plikiem start.command."
echo "===================================================="
echo ""
echo "Nacisnij dowolny klawisz, aby zamknac."
read -n 1 -s
