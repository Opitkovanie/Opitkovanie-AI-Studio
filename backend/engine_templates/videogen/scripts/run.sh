#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UV_LINK_MODE=copy uv run streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8501
