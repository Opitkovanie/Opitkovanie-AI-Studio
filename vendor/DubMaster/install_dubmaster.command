#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

pause() {
  echo ""
  read -r -p "Nacisnij Enter, aby zamknac..."
}

step() {
  echo ""
  echo "[$1/$2] $3"
}

clear
echo "============================================================"
echo "  DubMaster - instalacja dla macOS Apple Silicon"
echo "============================================================"
echo ""
echo "Folder aplikacji:"
echo "  $APP_DIR"
echo ""

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Blad: ten instalator jest przygotowany dla macOS."
  pause
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Blad: DubMaster wymaga Maca z Apple Silicon (M1/M2/M3/M4)."
  pause
  exit 1
fi

step 1 9 "Sprawdzam Xcode Command Line Tools..."
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Instaluje Xcode Command Line Tools. Potwierdz okno systemowe, jesli sie pojawi."
  xcode-select --install || true
  echo ""
  echo "Po zakonczeniu instalacji Xcode Command Line Tools uruchom ten plik ponownie."
  pause
  exit 0
fi

step 2 9 "Sprawdzam Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
  echo "Instaluje Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if [[ -x "/opt/homebrew/bin/brew" ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x "/usr/local/bin/brew" ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Blad: Homebrew nie jest dostepny po instalacji."
  echo "Zamknij Terminal, otworz ponownie i uruchom instalator jeszcze raz."
  pause
  exit 1
fi

step 3 9 "Instaluje Python 3.11 i ffmpeg..."
brew install python@3.11 ffmpeg

PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3.11 || true)"
fi

if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then
  echo "Blad: python3.11 nie jest dostepny."
  pause
  exit 1
fi

echo "Uzywam Pythona:"
"$PYTHON_BIN" --version

step 4 9 "Tworze lokalne srodowisko aplikacji (.venv)..."
if [[ -d "$APP_DIR/.venv" && ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "Istniejace .venv wyglada na przeniesione lub uszkodzone. Tworze je od nowa."
  rm -rf "$APP_DIR/.venv"
fi

if [[ ! -d "$APP_DIR/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

VENV_PY="$APP_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Blad: nie udalo sie utworzyc lokalnego srodowiska .venv."
  pause
  exit 1
fi

step 5 9 "Aktualizuje pip..."
"$VENV_PY" -m pip install --upgrade pip setuptools wheel

step 6 9 "Instaluje biblioteki Pythona..."
REQ_FILE="$APP_DIR/requirements-dubmaster.txt"
if [[ ! -f "$REQ_FILE" ]]; then
  echo "Blad: nie znaleziono requirements-dubmaster.txt."
  pause
  exit 1
fi

TMP_REQ="$(mktemp)"
trap 'rm -f "$TMP_REQ"' EXIT
grep -Ev '^[[:space:]]*(#|$|qwen_tts([<>=!~ ].*)?$)' "$REQ_FILE" > "$TMP_REQ"
"$VENV_PY" -m pip install --upgrade -r "$TMP_REQ"
rm -f "$TMP_REQ"
trap - EXIT

step 7 9 "Instaluje Qwen3-TTS..."
if ! "$VENV_PY" -m pip install --upgrade qwen_tts; then
  echo "qwen_tts nie byl dostepny z PyPI, probuje instalacji z GitHub..."
  "$VENV_PY" -m pip install --upgrade "git+https://github.com/QwenLM/Qwen3-TTS.git"
fi

step 8 9 "Instaluje opcjonalne nagrywanie z mikrofonu..."
if ! "$VENV_PY" -m pip install --upgrade streamlit-mic-recorder audio-recorder-streamlit; then
  echo "Uwaga: opcjonalne komponenty mikrofonu nie zainstalowaly sie. Reszta aplikacji moze dzialac."
fi

step 9 9 "Sprawdzam instalacje..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Blad: ffmpeg nie jest dostepny."
  pause
  exit 1
fi

"$VENV_PY" - <<'PY'
import importlib

mods = [
    "streamlit",
    "yt_dlp",
    "certifi",
    "requests",
    "numpy",
    "soundfile",
    "torch",
    "torchaudio",
    "torchcodec",
    "demucs",
    "mlx_whisper",
    "transformers",
    "qwen_tts",
]

missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append((name, str(exc)))

if missing:
    print("Brakuje modulow:")
    for name, err in missing:
        print(f" - {name}: {err}")
    raise SystemExit(1)

print("OK - podstawowe moduly sa dostepne.")
PY

chmod +x "$APP_DIR/run_dubmaster.command" 2>/dev/null || true
chmod +x "$APP_DIR/install_dubmaster.command" 2>/dev/null || true

echo ""
echo "============================================================"
echo "  Instalacja zakonczona."
echo "============================================================"
echo ""
echo "Aby uruchomic aplikacje, kliknij:"
echo "  run_dubmaster.command"
echo ""
echo "Aplikacja jest przenosna: moze lezec w dowolnym folderze."
echo "Srodowisko Python jest lokalnie w:"
echo "  $APP_DIR/.venv"
echo ""
echo "Pierwsze uzycie pobierze modele AI do cache uzytkownika."
pause
