#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

if ! command -v uv >/dev/null 2>&1; then
    echo "Missing uv. Run Install.command first, or install uv manually:"
    echo "https://docs.astral.sh/uv/"
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "Missing ffmpeg/ffprobe. Run Install.command first, or install ffmpeg manually."
    exit 1
fi

cd "$ROOT"
find "$ROOT" -name '._*' -delete
rm -rf "$ROOT/.venv" "$ROOT/vendor/ltx-2-mlx/.venv"
uv python install "$PYTHON_VERSION"
UV_LINK_MODE=copy uv sync --python "$PYTHON_VERSION"
find "$ROOT/.venv" -name '._*' -delete

cd "$ROOT/vendor/ltx-2-mlx"
UV_LINK_MODE=copy uv sync --python "$PYTHON_VERSION"
find "$ROOT/vendor/ltx-2-mlx/.venv" -name '._*' -delete

echo
echo "Setup complete."
echo "Optional model download: uv run python scripts/download_models.py"
echo "Run: ./scripts/run.sh"
