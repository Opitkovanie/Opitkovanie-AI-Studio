"""OmniVoice (k2-fsa/OmniVoice) — system-installed voice engine for DubCut.

OmniVoice is an alternative to Qwen3-TTS that delivers studio-grade quality, true
voice cloning and a far wider language set (including Polish, which Qwen cannot
voice). Like the ACE-Step (music) and VideoGenerator engines, it lives once in the
system — in its own uv/venv under ~/.cache/omnivoice-tts — rather than being bundled
in the app. The `dubbing_engine._run_omnivoice_tts` worker spawns this venv's
interpreter; this module owns the venv's location, readiness probe and one-click
install so a fresh Mac can set everything up from Settings.

The model weights themselves download to the shared Hugging Face cache
(~/.cache/huggingface) on first use, exactly like every other model the app uses.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

MODEL_ID = "k2-fsa/OmniVoice"
# Packages the engine venv needs. `omnivoice` (PyPI) pulls torch/transformers/etc.;
# soundfile is used by the worker to write the WAV segments.
_PIP_PACKAGES = ["omnivoice", "soundfile", "num2words"]


def engine_root() -> Path:
    override = os.environ.get("DUBCUT_OMNIVOICE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "omnivoice-tts"


def venv_dir() -> Path:
    return engine_root() / "venv"


def python_path() -> Path:
    override = os.environ.get("DUBCUT_OMNIVOICE_PYTHON")
    if override:
        return Path(override)
    return venv_dir() / "bin" / "python"


def is_ready() -> bool:
    """True when the engine venv exists AND the `omnivoice` package imports there."""
    py = python_path()
    if not py.exists():
        return False
    try:
        res = subprocess.run(
            [str(py), "-c", "import omnivoice, soundfile, torch"],
            capture_output=True, timeout=60,
        )
        return res.returncode == 0
    except Exception:
        return False


def uv_available() -> bool:
    return shutil.which("uv") is not None


def model_cached() -> bool:
    """True when the OmniVoice weights are already in the shared HF cache."""
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    folder = "models--" + MODEL_ID.replace("/", "--")
    p = hub / folder / "snapshots"
    try:
        return p.exists() and any(p.iterdir())
    except Exception:
        return False


def engine_status(config_app: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "ready": is_ready(),
        "venv": str(venv_dir()),
        "python": str(python_path()),
        "uv_available": uv_available(),
        "model_cached": model_cached(),
        "model_id": MODEL_ID,
    }


def _stream(cmd: list[str], ctx, *, env: Optional[Dict[str, str]] = None) -> int:
    """Run a command, forwarding each output line to the job log. Returns rc."""
    ctx.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env or os.environ.copy(),
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            ctx.log(line)
            # Also echo to stdout so the Settings "logs" panel (which tails the backend
            # process output) shows install/download progress live.
            print("[omnivoice] " + line, flush=True)
    return proc.wait()


def install(ctx, *, prefetch_model: bool = True) -> Dict[str, Any]:
    """Create the engine venv, install the `omnivoice` package, and (optionally)
    pre-download the model so the first dub doesn't stall. Designed to be run as a
    background job (`ctx` is a jobs.JobContext)."""
    root = engine_root()
    root.mkdir(parents=True, exist_ok=True)
    venv = venv_dir()
    py = python_path()

    env = os.environ.copy()
    env.update({"COPYFILE_DISABLE": "1", "UV_LINK_MODE": "copy", "PYTHONUNBUFFERED": "1"})

    use_uv = uv_available()
    ctx.step("Tworzenie środowiska silnika OmniVoice…")
    if use_uv:
        rc = _stream(["uv", "venv", "--python", "3.11", str(venv)], ctx, env=env)
        if rc != 0 and not py.exists():
            raise RuntimeError("Nie udało się utworzyć środowiska (uv venv).")
    else:
        # Fallback for machines without uv: plain venv + pip. Slower, but works on a
        # bare Mac that only has the system Python from the one-time setup step.
        base_py = shutil.which("python3.11") or shutil.which("python3") or sys.executable
        rc = _stream([base_py, "-m", "venv", str(venv)], ctx, env=env)
        if rc != 0 and not py.exists():
            raise RuntimeError("Nie udało się utworzyć środowiska (python -m venv).")
        _stream([str(py), "-m", "pip", "install", "-U", "pip"], ctx, env=env)

    ctx.progress(0.2, "Instalacja pakietu OmniVoice (torch + transformers)…")
    if use_uv:
        rc = _stream(["uv", "pip", "install", "--python", str(py), *_PIP_PACKAGES], ctx, env=env)
    else:
        rc = _stream([str(py), "-m", "pip", "install", *_PIP_PACKAGES], ctx, env=env)
    if rc != 0:
        raise RuntimeError("Instalacja pakietu OmniVoice nie powiodła się — sprawdź log.")

    ctx.progress(0.6, "Weryfikacja instalacji…")
    if not is_ready():
        raise RuntimeError("Pakiet OmniVoice zainstalowany, ale import się nie powiódł.")

    if prefetch_model:
        ctx.step("Pobieranie modelu OmniVoice + ASR do klonowania (~4 GB, jednorazowo)…")
        # load_asr=True also fetches the Whisper model used to transcribe a voice
        # sample during cloning, so the first clone doesn't stall on a download.
        prefetch = (
            "import torch;from omnivoice import OmniVoice;"
            "OmniVoice.from_pretrained('%s', device_map='cpu', dtype=torch.float32, load_asr=True);"
            "print('MODEL_OK')" % MODEL_ID
        )
        rc = _stream([str(py), "-c", prefetch], ctx, env=env)
        if rc != 0:
            ctx.log("Pobranie modelu nie powiodło się — model dociągnie się przy pierwszym użyciu.",
                    "warning")

    ctx.progress(1.0, "OmniVoice gotowy.")
    return engine_status()
