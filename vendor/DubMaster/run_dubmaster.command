#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ -x "/opt/homebrew/bin/brew" ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x "/usr/local/bin/brew" ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

VENV_PY="$APP_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  clear
  echo "DubMaster nie jest jeszcze zainstalowany w tym folderze."
  echo ""
  echo "Najpierw kliknij:"
  echo "  install_dubmaster.command"
  echo ""
  read -r -p "Nacisnij Enter, aby zamknac..."
  exit 1
fi

exec "$VENV_PY" -m streamlit run "$APP_DIR/app.py"
