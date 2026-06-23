#!/usr/bin/env bash
# DubCut Studio — backend environment installer (macOS / Linux).
# Creates a single Python venv and installs the native backend + engine dependencies.
# Invoked by the app (env: DUBCUT_VENV, DUBCUT_BACKEND_DIR) or runnable standalone.
set -u

BACKEND_DIR="${DUBCUT_BACKEND_DIR:-$(cd "$(dirname "$0")" && pwd)}"
VENV="${DUBCUT_VENV:-$BACKEND_DIR/.venv}"

echo "==> DubCut Studio installer"
echo "    backend: $BACKEND_DIR"
echo "    venv:    $VENV"
TARGET="${DUBCUT_INSTALL_TARGET:-all}"
case "$TARGET" in
  common|shorts|dubmaster|all|music|videogen) ;;
  *) echo "!! Nieznany profil instalacji: $TARGET" >&2; exit 1 ;;
esac
echo "    profil:  $TARGET"

# ---------------------------------------------------------------------------
# Preflight: system tools the app cannot install for the user (Python, ffmpeg,
# git). Instead of failing late with a cryptic error, detect everything that is
# missing up front and print ONE copy-paste command that installs the lot. The
# app shows this block verbatim in the install panel.
#
# What each target truly needs:
#   common/shorts/dubmaster/all → Python 3 (backend) + ffmpeg (whisper/yt-dlp/dub)
#   music/videogen              → git (clone engines) + ffmpeg (video audio mux)
# uv is bootstrapped by the app itself (curl), so it's not listed here.
# ---------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
# A real, runnable python3 (the /usr/bin stub without Command Line Tools exits
# non-zero), not just something on PATH.
python_ok() {
  for c in python3.11 python3.12 python3.10 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' >/dev/null 2>&1 && return 0
  done
  return 1
}

preflight_deps() {
  local need_py=0 need_ff=0 need_git=0 missing=""
  case "$TARGET" in
    common|shorts|dubmaster|all) python_ok || { need_py=1; missing="$missing Python3.11+"; } ;;
  esac
  case "$TARGET" in
    music|videogen) have git || { need_git=1; missing="$missing git"; } ;;
  esac
  have ffmpeg || { need_ff=1; missing="$missing ffmpeg"; }

  # ffmpeg alone is a runtime tool — warn but don't block the install. Python/git
  # are hard blockers for their targets.
  if [ "$need_py" -eq 0 ] && [ "$need_git" -eq 0 ] && [ "$need_ff" -eq 0 ]; then
    return 0
  fi

  # Brew packages to suggest, scoped to what's actually missing.
  local brew_pkgs=""
  [ "$need_py" -eq 1 ] && brew_pkgs="$brew_pkgs python@3.11"
  [ "$need_ff" -eq 1 ] && brew_pkgs="$brew_pkgs ffmpeg"
  [ "$need_git" -eq 1 ] && brew_pkgs="$brew_pkgs git"
  brew_pkgs="${brew_pkgs# }"   # trim leading space (no external `sed` dependency)

  echo "" >&2
  echo "==================================================================" >&2
  echo " Brakuje narzędzi systemowych, których aplikacja nie może" >&2
  echo " zainstalować za Ciebie:$missing" >&2
  echo "" >&2
  echo " Skopiuj i wklej w Terminalu (jednorazowo):" >&2
  echo "" >&2
  if have brew; then
    echo "    brew install $brew_pkgs" >&2
  else
    echo "    # 1) Homebrew (jeśli go nie masz):" >&2
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    echo "" >&2
    echo "    # 2) Wymagane narzędzia:" >&2
    echo "    brew install $brew_pkgs" >&2
  fi
  echo "" >&2
  echo " Potem wróć tutaj i kliknij ponownie „Zainstaluj”." >&2
  echo "==================================================================" >&2
  echo "" >&2

  # Block only when a HARD dependency for this target is missing.
  if [ "$need_py" -eq 1 ] || [ "$need_git" -eq 1 ]; then
    exit 2
  fi
}
preflight_deps

# ---------------------------------------------------------------------------
# Standalone AI engines (Muzyka / Obraz·Wideo).
#
# Unlike the pip modules, these are self-contained projects run via `uv`. The app
# installs them automatically into a managed engines dir (DUBCUT_ENGINES_DIR) and
# their model weights cache system-side via Hugging Face — exactly like Whisper /
# Qwen TTS. The user never points at a folder by hand.
# ---------------------------------------------------------------------------
ENGINES_DIR="${DUBCUT_ENGINES_DIR:-$HOME/.dubcut/engines}"

ensure_uv() {
  command -v uv >/dev/null 2>&1 && return 0
  echo "==> Instaluję uv (menedżer środowisk silników)…"
  if command -v brew >/dev/null 2>&1; then
    brew install uv && return 0
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh || return 1
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1
}

ensure_git() {
  command -v git >/dev/null 2>&1 && return 0
  echo "!! git nie został znaleziony. Zainstaluj git (np. 'xcode-select --install')." >&2
  return 1
}

# Engines are PINNED to the exact commits the backend's CLI calls match. Cloning
# bare HEAD is fragile: upstream changed `ltx-2-mlx generate --fps` → `--frame-rate`,
# which silently breaks video. We clone, then hard-checkout the pinned commit, and
# re-pin idempotently on every reinstall (so an already-installed drift is corrected).
ACE_PIN="dce621408bee8c31b4fcf4811682eb9359e1bc94"   # ACE-Step v0.1.8
LTX_PIN="59a42d7f0eafd397c5c421868877c1b6d5324910"   # ltx-2-mlx v0.14.11 — `generate` uses --frame-rate

clone_pinned() {
  # clone_pinned <dir> <url> <pin-sha>
  local dir="$1" url="$2" pin="$3"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$pin" ]; then
    echo "   (już na przypiętej wersji ${pin:0:10})"
    return 0
  fi
  rm -rf "$dir"
  # Full clone (code only, a few MB) so an arbitrary historical commit can be checked out.
  git clone "$url" "$dir" || return 1
  git -C "$dir" checkout --force "$pin" || return 1
}

install_music_engine() {
  ensure_git || exit 1
  ensure_uv  || { echo "!! uv jest wymagane dla silnika muzyki." >&2; exit 1; }
  mkdir -p "$ENGINES_DIR"
  local dir="$ENGINES_DIR/ACE-Step-1.5"
  echo "==> Pobieram silnik muzyki ACE-Step (przypięty ${ACE_PIN:0:10})…"
  clone_pinned "$dir" "https://github.com/ace-step/ACE-Step-1.5.git" "$ACE_PIN" \
    || { echo "!! Instalacja ACE-Step nie powiodła się." >&2; exit 1; }
  echo "==> Konfiguruję środowisko ACE-Step (uv sync)…"
  ( cd "$dir" && UV_LINK_MODE=copy uv sync ) || echo "   (uv sync zgłosił problem — sprawdź logi powyżej)"
  echo "==> Gotowe. Silnik muzyki w: $dir"
  echo "    Modele (~50 GB) pobiorą się z Hugging Face przy pierwszym generowaniu."
}

install_videogen_engine() {
  ensure_git || exit 1
  ensure_uv  || { echo "!! uv jest wymagane dla silnika obrazu/wideo." >&2; exit 1; }
  mkdir -p "$ENGINES_DIR"
  local dir="$ENGINES_DIR/VideoGenerator"
  mkdir -p "$dir"
  # Lay down the bundled Streamlit/MLX wrapper (scripts + pyproject) shipped with the app.
  if [ -d "$BACKEND_DIR/engine_templates/videogen" ]; then
    echo "==> Rozpakowuję wrapper LTX…"
    cp -R "$BACKEND_DIR/engine_templates/videogen/." "$dir/"
    find "$dir" -name '._*' -delete 2>/dev/null || true
  fi
  local ltx="$dir/vendor/ltx-2-mlx"
  mkdir -p "$dir/vendor"
  echo "==> Pobieram ltx-2-mlx (przypięty ${LTX_PIN:0:10})…"
  clone_pinned "$ltx" "https://github.com/dgrauet/ltx-2-mlx.git" "$LTX_PIN" \
    || { echo "!! Instalacja ltx-2-mlx nie powiodła się." >&2; exit 1; }
  echo "==> Konfiguruję środowiska LTX (uv sync)…"
  ( cd "$dir"  && UV_LINK_MODE=copy uv sync ) || echo "   (uv sync wrappera zgłosił problem)"
  ( cd "$ltx"  && UV_LINK_MODE=copy uv sync ) || echo "   (uv sync ltx-2-mlx zgłosił problem)"
  echo "==> Pobieram modele LTX z Hugging Face (~45 GB, może chwilę potrwać)…"
  ( cd "$dir" && uv run python scripts/download_models.py ) || echo "   (pobieranie modeli LTX zgłosiło problem — można powtórzyć)"
  echo "==> Gotowe. Silnik obrazu/wideo w: $dir"
}

case "$TARGET" in
  music)    install_music_engine;    exit 0 ;;
  videogen) install_videogen_engine; exit 0 ;;
esac

# --- locate a suitable python -------------------------------------------------
# Prefer the interpreter that ALREADY has the most engine deps installed, so the
# venv (built with --system-site-packages below) can reuse them instead of
# re-downloading gigabytes. Falls back to the first 3.10+ found on a fresh machine.
PROBE='import importlib.util as u
mods=["yt_dlp","numpy","torch","cv2","transformers","faster_whisper","PIL","soundfile","demucs","ultralytics","fastapi","google.genai"]
print(sum(1 for m in mods if u.find_spec(m)))'
PY=""
PY_FALLBACK=""
PY_BEST_SCORE=-1
for cand in python3.11 python3.12 python3.10 python3; do
  c="$(command -v "$cand" 2>/dev/null)" || continue
  [ -z "$c" ] && continue
  [ -z "$PY_FALLBACK" ] && PY_FALLBACK="$c"
  score="$("$c" -c "$PROBE" 2>/dev/null || echo -1)"
  case "$score" in (*[!0-9]*) score=-1 ;; esac
  if [ "$score" -gt "$PY_BEST_SCORE" ]; then
    PY_BEST_SCORE="$score"
    PY="$c"
  fi
done
[ -z "$PY" ] && PY="$PY_FALLBACK"
if [ -z "$PY" ]; then
  echo "!! Nie znaleziono Pythona 3. Zainstaluj Python 3.11+ i spróbuj ponownie." >&2
  exit 1
fi
echo "==> Python: $PY ($("$PY" --version 2>&1))"
echo "    Wykryto $PY_BEST_SCORE z 12 kluczowych pakietów już w systemie — zostaną użyte ponownie."

# --- ffmpeg check (warn only) --------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "!! Uwaga: ffmpeg nie został znaleziony w PATH."
  if command -v brew >/dev/null 2>&1; then
    echo "   Próba instalacji przez Homebrew…"
    brew install ffmpeg || echo "   (pomiń — zainstaluj ffmpeg ręcznie)"
  else
    echo "   Zainstaluj ffmpeg ręcznie (np. 'brew install ffmpeg' lub menedżer pakietów)."
  fi
fi

# --- choose install target: system interpreter (default from app) or venv ------
NO_VENV="${DUBCUT_NO_VENV:-}"
if [ -n "$NO_VENV" ]; then
  # Install missing packages straight into the system interpreter the app detected,
  # so already-present modules are reused and only the gaps are downloaded.
  VPY="${DUBCUT_PYTHON:-$PY}"
  echo "==> Tryb systemowy: doinstalowuję brakujące pakiety do $VPY (bez venv)."
else
  # --system-site-packages: the venv inherits everything already installed in the
  # chosen interpreter, so pip skips satisfied requirements and only installs gaps.
  # An older venv created WITHOUT this flag is recreated so the reuse actually works.
  if [ -x "$VENV/bin/python" ] && ! grep -q "include-system-site-packages = true" "$VENV/pyvenv.cfg" 2>/dev/null; then
    echo "==> Istniejący venv nie widzi pakietów systemowych — tworzę go ponownie…"
    rm -rf "$VENV"
  fi
  if [ ! -x "$VENV/bin/python" ]; then
    echo "==> Tworzę środowisko venv (z dostępem do pakietów systemowych)…"
    "$PY" -m venv --system-site-packages "$VENV" || { echo "!! Nie udało się utworzyć venv" >&2; exit 1; }
  fi
  VPY="$VENV/bin/python"
fi

# Robust pip install: works in a venv, and for system interpreters falls back to
# --user, then --break-system-packages (Homebrew/Debian externally-managed envs).
pipi() {
  "$VPY" -m pip install "$@" && return 0
  if [ -n "$NO_VENV" ]; then
    echo "   (ponawiam z --user)"
    "$VPY" -m pip install --user "$@" && return 0
    echo "   (ponawiam z --break-system-packages)"
    "$VPY" -m pip install --break-system-packages "$@" && return 0
  fi
  return 1
}

if [ -z "$NO_VENV" ]; then
  echo "==> Aktualizuję pip…"
  "$VPY" -m pip install --upgrade pip wheel setuptools >/dev/null 2>&1 || true
fi

# --- lightweight API layer first (so the backend can boot immediately) ---------
echo "==> Instaluję warstwę API (FastAPI / uvicorn)…"
pipi -r "$BACKEND_DIR/requirements-backend.txt" || {
  echo "!! Instalacja warstwy API nie powiodła się" >&2; exit 1;
}

echo "==> Instaluję moduły wspólne (yt-dlp, Gemini, Whisper)…"
# No --upgrade here: requirements already satisfied by system packages are reused
# as-is (avoids re-downloading and avoids ABI drift, e.g. numpy vs system torch).
pipi -r "$BACKEND_DIR/requirements-common.txt" || {
  echo "!! Instalacja modułów wspólnych nie powiodła się" >&2; exit 1;
}
# yt-dlp is the one exception — always pull the newest release (YouTube breaks on old builds).
echo "==> Aktualizuję yt-dlp do najnowszej wersji…"
pipi --upgrade yt-dlp || echo "   (pominięto aktualizację yt-dlp)"

# "common" profile installs only the shared base (no heavy engine stack).
if [ "$TARGET" = "common" ]; then
  echo "==> Gotowe. Moduły wspólne (podstawa) zainstalowane."
  exit 0
fi

REQ_FILE="$BACKEND_DIR/requirements.txt"
LABEL="pełny stos silników (Shorty + DubMaster)"
if [ "$TARGET" = "shorts" ]; then
  REQ_FILE="$BACKEND_DIR/requirements-shorts.txt"
  LABEL="silniki dla Shortów / AI ViralCutter"
elif [ "$TARGET" = "dubmaster" ]; then
  REQ_FILE="$BACKEND_DIR/requirements-dubmaster.txt"
  LABEL="silniki dla DubMastera (Demucs + Qwen TTS)"
fi

# --- selected engine stack (heavy ML deps) -------------------------------------
echo "==> Instaluję: $LABEL"
echo "    To może chwilę potrwać przy pierwszym uruchomieniu."
pipi -r "$REQ_FILE" || {
  echo "!! Część zależności ML mogła się nie zainstalować. Backend nadal wystartuje;" >&2
  echo "   funkcje wymagające modeli zgłoszą braki." >&2
}

echo "==> Gotowe. Środowisko DubCut Studio jest zainstalowane."
