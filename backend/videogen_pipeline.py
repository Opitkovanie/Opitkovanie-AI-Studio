"""Image (FLUX/MFLUX) + Video (LTX 2.3 MLX) generators for DubCut Studio.

Ports the standalone "Local LTX Studio" (Streamlit) engines into DubCut's native pattern:
each generation is a background job that spawns the same `uv run …` MLX subprocess the
original used, streams its stdout as SSE progress, and saves the output. The heavy models
(~45 GB) live once in the system VideoGenerator project — never bundled in the app. The
model is loaded by the one-shot subprocess and freed the moment it exits, so RAM is only
used while actually rendering.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

REQUIRED_LTX_FILES = [
    "audio_vae.safetensors", "connector.safetensors", "transformer-distilled.safetensors",
    "vae_decoder.safetensors", "vae_encoder.safetensors", "vocoder.safetensors",
    # The distilled pipeline also needs the model config and the spatial 2x upscaler
    # used by its stage-2 refine — without these the loader falls back to wrong latent
    # dimensions and the output decodes to a repeated-tile mosaic. Treat them as
    # required so an incomplete download is never reported as "ready".
    "embedded_config.json", "spatial_upscaler_x2_v1_1.safetensors",
]
DEFAULT_GEMMA = "mlx-community/gemma-3-12b-it-4bit"


# ---------------------------------------------------------------------------
# Engine location (system-installed VideoGenerator project)
# ---------------------------------------------------------------------------
def _app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def videogen_dir(config_app: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    candidates: List[Path] = []
    env = os.environ.get("DUBCUT_VIDEOGEN_DIR")
    if env:
        candidates.append(Path(env))
    if config_app:
        setting = str(config_app.get("videogen_dir") or "").strip()
        if setting:
            candidates.append(Path(setting))
    engines = os.environ.get("DUBCUT_ENGINES_DIR")
    if engines:
        candidates.append(Path(engines) / "VideoGenerator")
    candidates.append(_app_root() / "vendor" / "VideoGenerator")
    # NOTE: external-disk / sibling "VideoGenerator" copies are intentionally NOT
    # auto-detected — the app installs the engine into the system (engines dir).
    # A user who insists on an existing copy sets `app.videogen_dir` explicitly.
    for c in candidates:
        try:
            if c.exists() and (c / "vendor" / "ltx-2-mlx").exists():
                return c
        except Exception:
            continue
    return None


def _vendor_dir(root: Path) -> Path:
    return root / "vendor" / "ltx-2-mlx"


def uv_available() -> bool:
    import shutil
    return shutil.which("uv") is not None


def image_dir() -> Path:
    from config_store import module_dir  # type: ignore
    return module_dir("image")


def video_dir() -> Path:
    from config_store import module_dir  # type: ignore
    return module_dir("video")


def _kind_dir(kind: str) -> Path:
    return video_dir() if kind == "video" else image_dir()


def find_media(filename: str) -> Optional[Path]:
    """Locate a generated file by name across the image + video work folders (and the
    base-image inputs folder, so picked base images preview)."""
    safe = os.path.basename(filename)
    for d in (image_dir(), video_dir(), input_dir()):
        p = d / safe
        if p.exists():
            return p
    return None


def input_dir() -> Path:
    # Base images for image→image / image→video are per-clip temp INPUTS: keep them in
    # the work folder (under the Wideo category) so they're deleted together with the
    # clips — never left as orphans in the config root.
    from config_store import module_dir  # type: ignore
    d = module_dir("video") / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Option lists (faithful to the original runners)
# ---------------------------------------------------------------------------
def ltx_model_options(root: Path) -> Dict[str, str]:
    return {
        "LTX 2.3 Q4 - stabilny dla 24 GB": str(root / "models" / "ltx-2.3-mlx-q4"),
        "LTX 2.3 Q8 - eksperymentalna jakość": str(root / "models" / "ltx-2.3-mlx-q8"),
    }


def video_resolution_options() -> Dict[str, Tuple[int, int]]:
    return {
        "Fast preview 512 x 320": (512, 320), "Square 512 x 512": (512, 512),
        "Wide 640 x 384": (640, 384), "Wide 704 x 480": (704, 480),
        "SD safe 864 x 480": (864, 480), "HD safe 1280 x 736": (1280, 736),
        "Full HD safe 1920 x 1088": (1920, 1088), "Vertical 384 x 640": (384, 640),
        "Vertical 480 x 704": (480, 704), "Vertical HD safe 736 x 1280": (736, 1280),
        "Vertical Full HD safe 1088 x 1920": (1088, 1920),
    }


def duration_options() -> Dict[str, float]:
    return {
        "0.4 s / szybki test": 0.4, "1 s": 1.0, "2 s": 2.0, "4 s": 4.0, "6 s": 6.0,
        "8 s": 8.0, "10 s": 10.0, "12 s": 12.0, "16 s": 16.0, "20 s": 20.0,
    }


def fps_options() -> List[float]:
    return [12.0, 16.0, 18.0, 24.0, 30.0]


def frames_for_duration(duration_seconds: float, fps: float) -> int:
    target = max(9, round(duration_seconds * fps))
    k = max(1, round((target - 1) / 8))
    return 8 * k + 1


def image_model_options() -> Dict[str, str]:
    return {
        "FLUX.1 Schnell MFLUX Q4 - publiczny": "dhairyashil/FLUX.1-schnell-mflux-4bit",
        "FLUX.1 Dev MFLUX Q4 - publiczny, wolniej": "dhairyashil/FLUX.1-dev-mflux-4bit",
        "FLUX.1 Krea Dev MFLUX Q4 - publiczny": "filipstrand/FLUX.1-Krea-dev-mflux-4bit",
        "Z-Image Turbo MFLUX Q4 - szybki quality": "filipstrand/Z-Image-Turbo-mflux-4bit",
        "FLUX.2 Klein 4B Q4 - nowy szybki": "flux2-klein-4b",
    }


def image_resolution_options() -> Dict[str, Dict[str, Tuple[int, int]]]:
    return {
        "Square": {
            "512 x 512 - szybki test": (512, 512), "768 x 768 - dobry podgląd": (768, 768),
            "1024 x 1024 - standard": (1024, 1024), "1280 x 1280 - ciężkie": (1280, 1280),
            "1536 x 1536 - bardzo ciężkie": (1536, 1536),
        },
        "Wide": {
            "1216 x 832 - standard": (1216, 832), "1344 x 768 - cinematic": (1344, 768),
            "1536 x 864 - 16:9 ciężkie": (1536, 864), "1792 x 1024 - bardzo ciężkie": (1792, 1024),
            "2048 x 1152 - eksperymentalne": (2048, 1152),
        },
        "Vertical": {
            "832 x 1216 - standard": (832, 1216), "768 x 1344 - social": (768, 1344),
            "864 x 1536 - 9:16 ciężkie": (864, 1536), "1024 x 1792 - bardzo ciężkie": (1024, 1792),
            "1152 x 2048 - eksperymentalne": (1152, 2048),
        },
    }


def image_style_options() -> Dict[str, str]:
    return {
        "Bez stylu": "",
        "Cinematic photo": "cinematic photo, natural skin texture, realistic lighting, shallow depth of field, high detail",
        "Editorial fashion": "editorial fashion photography, polished styling, elegant pose, soft studio lighting, magazine quality",
        "Product shot": "premium product photography, clean composition, controlled studio lighting, sharp details, commercial finish",
        "Realistic portrait": "realistic portrait photography, expressive face, detailed eyes, natural light, 85mm lens look",
        "Concept art": "high-end concept art, dramatic composition, rich atmosphere, detailed environment, artstation quality",
        "Anime": "anime illustration, clean linework, expressive character design, vibrant colors, detailed background",
    }


def default_steps_for_model(model: str) -> int:
    low = model.lower()
    if "z-image" in low:
        return 9
    if "flux2" in low or "klein" in low:
        return 4
    return 4 if "schnell" in low else 20


def _image_command_name(model: str) -> str:
    low = model.lower()
    if "z-image" in low or "zimage" in low:
        return "mflux-generate-z-image-turbo"
    if "flux2" in low or "klein" in low:
        return "mflux-generate-flux2"
    return "mflux-generate"


def is_z_image_model(model: str) -> bool:
    low = model.lower()
    return "z-image" in low or "zimage" in low


def missing_ltx_files(model_dir: str) -> List[str]:
    p = Path(model_dir)
    return [name for name in REQUIRED_LTX_FILES if not (p / name).exists()]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def engine_status(config_app: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    root = videogen_dir(config_app)
    available = root is not None
    ltx_q4_ok = False
    mflux_ok = False
    if available:
        ltx_q4_ok = len(missing_ltx_files(str(root / "models" / "ltx-2.3-mlx-q4"))) == 0
        mflux_ok = (root / ".venv").exists() or (_vendor_dir(root) / ".venv").exists()
    return {
        "engine_available": available,
        "engine_dir": str(root) if root else "",
        "uv_available": uv_available(),
        "ltx_models_ok": ltx_q4_ok,
        "mflux_ok": mflux_ok,
    }


def _base_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["COPYFILE_DISABLE"] = "1"
    return env


# ---------------------------------------------------------------------------
# Subprocess streaming with cancellation
# ---------------------------------------------------------------------------
def _kill_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except Exception:
        try:
            process.send_signal(sig)
        except Exception:
            pass


def _run_streaming(command: List[str], cwd: Path, ctx: Any,
                   on_line, label: str) -> Tuple[int, List[str]]:
    """Run a command, forwarding stdout lines to `on_line(line)`; honours job cancel.

    A reader thread drains stdout into a queue so the main loop can poll cancellation
    every 0.4 s even while a render step blocks for tens of seconds with no output —
    otherwise the blocking readline made the "Przerwij" button look dead.
    """
    process = subprocess.Popen(
        command, cwd=str(cwd), env=_base_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        start_new_session=True,
    )
    q: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                q.put(raw)
        except Exception:
            pass
        finally:
            q.put(None)  # EOF sentinel

    threading.Thread(target=_reader, daemon=True, name=f"vg-{label}").start()

    lines: List[str] = []
    killed = False
    while True:
        if ctx is not None and ctx.cancelled and not killed:
            _kill_group(process, signal.SIGTERM)
            killed = True
        try:
            raw = q.get(timeout=0.4)
        except queue.Empty:
            continue
        if raw is None:
            break
        line = raw.rstrip()
        if line:
            lines.append(line)
            try:
                on_line(line)
            except Exception:
                pass

    if killed and process.poll() is None:
        time.sleep(0.3)
        if process.poll() is None:
            _kill_group(process, signal.SIGKILL)
    return process.wait(), lines


# ---------------------------------------------------------------------------
# Prompt helpers (enhance / translate via local Gemma) — run as jobs
# ---------------------------------------------------------------------------
def _clean_helper_output(output: str) -> str:
    noisy = ("warning:", "Fetching ", "Loading Gemma", "Original:")
    out: List[str] = []
    for raw in output.replace("\r", "\n").splitlines():
        line = raw.strip()
        if not line or line.startswith(noisy) or "it/s]" in line or "files:" in line:
            continue
        out.append(line)
    cleaned = "\n".join(out).strip()
    if "Enhanced:" in cleaned:
        cleaned = cleaned.split("Enhanced:", 1)[1].strip()
    return cleaned


def enhance_prompt(text: str, kind: str, config_app: Optional[Dict[str, Any]], ctx: Any) -> Dict[str, Any]:
    root = videogen_dir(config_app)
    if root is None:
        raise RuntimeError("Nie znaleziono silnika generatora (VideoGenerator).")
    vendor = _vendor_dir(root)
    ctx.progress(0.2, "Lokalna Gemma ulepsza prompt…")
    if kind == "image":
        command = ["uv", "run", "python", str(root / "scripts" / "enhance_image_prompt.py"),
                   "--prompt", text, "--gemma", DEFAULT_GEMMA, "--seed", "10"]
    else:
        command = ["uv", "run", "ltx-2-mlx", "enhance", "--prompt", text,
                   "--mode", "t2v", "--gemma", DEFAULT_GEMMA, "--seed", "10"]
    completed = subprocess.run(command, cwd=str(vendor), env=_base_env(),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
    out = _clean_helper_output(completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(out[-1500:] or "Prompt enhancer zakończył się błędem.")
    ctx.progress(1.0, "Gotowe.")
    return {"text": out.strip()}


def translate_prompt(text: str, config_app: Optional[Dict[str, Any]], ctx: Any) -> Dict[str, Any]:
    root = videogen_dir(config_app)
    if root is None:
        raise RuntimeError("Nie znaleziono silnika generatora (VideoGenerator).")
    vendor = _vendor_dir(root)
    ctx.progress(0.2, "Lokalna Gemma tłumaczy na angielski…")
    command = ["uv", "run", "python", "scripts/translate_prompt.py",
               "--prompt", text, "--gemma", DEFAULT_GEMMA, "--seed", "10"]
    completed = subprocess.run(command, cwd=str(vendor), env=_base_env(),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
    out = _clean_helper_output(completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(out[-1500:] or "Tłumaczenie zakończyło się błędem.")
    ctx.progress(1.0, "Gotowe.")
    return {"text": out.strip()}


# ---------------------------------------------------------------------------
# History (shared jsonl per kind, in DubCut data dir)
# ---------------------------------------------------------------------------
def _history_path(kind: str) -> Path:
    return _kind_dir(kind) / f"{kind}_history.jsonl"


def _append_history(kind: str, entry: Dict[str, Any]) -> None:
    with _history_path(kind).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_history(kind: str, limit: int = 60) -> List[Dict[str, Any]]:
    path = _history_path(kind)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        fname = data.get("file_name", "")
        if fname and (_kind_dir(kind) / fname).exists():
            data["url"] = f"/api/videogen/file?path={quote(fname)}"
            data["path"] = str((_kind_dir(kind) / fname).resolve())
            rows.append(data)
    return rows[-limit:][::-1]


def delete_history(kind: str, file_name: str) -> bool:
    safe = os.path.basename(file_name)
    path = _history_path(kind)
    rows: List[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("file_name") == safe:
                    continue
            except json.JSONDecodeError:
                continue
            rows.append(line)
        path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    target = (_kind_dir(kind) / safe)
    if target.is_file():
        target.unlink(missing_ok=True)
    for extra in (target.with_name(f"{target.stem}.metadata.json"), target.with_suffix(target.suffix + ".json")):
        if extra.is_file():
            extra.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------
def _resolve_generated_images(pattern: Path, seed: int, batch: int) -> List[Path]:
    if batch <= 1:
        return [pattern] if pattern.exists() else []
    paths: List[Path] = []
    for i in range(batch):
        s = seed + i
        expected = Path(str(pattern).format(seed=s))
        for cand in [expected, *sorted(expected.parent.glob(f"{expected.stem}_seed_{s}{expected.suffix}")),
                     *sorted(expected.parent.glob(f"{expected.stem}*{expected.suffix}"))]:
            if cand.exists() and cand not in paths:
                paths.append(cand)
                break
    return paths


def generate_image(settings: Dict[str, Any], config_app: Optional[Dict[str, Any]], ctx: Any) -> Dict[str, Any]:
    root = videogen_dir(config_app)
    if root is None:
        raise RuntimeError("Nie znaleziono silnika generatora obrazów (VideoGenerator).")

    model = str(settings.get("model") or "dhairyashil/FLUX.1-schnell-mflux-4bit")
    prompt = str(settings.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Prompt jest pusty.")
    style = str(settings.get("style_suffix") or "").strip()
    if style:
        prompt = f"{prompt}, {style}"
    width = int(settings.get("width", 1024))
    height = int(settings.get("height", 1024))
    steps = int(settings.get("steps", default_steps_for_model(model)))
    seed = int(settings.get("seed", 42))
    batch = max(1, min(4, int(settings.get("batch_count", 1))))
    guidance = settings.get("guidance")
    negative = str(settings.get("negative_prompt") or "").strip()
    low_ram = bool(settings.get("low_ram", True))
    cache_gb = int(settings.get("mlx_cache_limit_gb", 12))
    image_path = str(settings.get("image_path") or "").strip()
    image_strength = float(settings.get("image_strength", 0.6))

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_name = f"{ts}-image-{{seed}}.png" if batch > 1 else f"{ts}-image.png"
    out_path = image_dir() / out_name

    command = ["uv", "run", _image_command_name(model), "--model", model, "--prompt", prompt,
               "--height", str(height), "--width", str(width), "--steps", str(steps), "--seed",
               *[str(seed + i) for i in range(batch)], "--metadata", "--output", str(out_path)]
    if "flux2" in model.lower() or "klein" in model.lower():
        command.extend(["--quantize", "4"])
    if guidance is not None:
        command.extend(["--guidance", str(guidance)])
    if negative:
        command.extend(["--negative-prompt", negative])
    if low_ram:
        command.append("--low-ram")
    elif cache_gb:
        command.extend(["--mlx-cache-limit-gb", str(cache_gb)])
    if image_path and Path(image_path).exists():
        command.extend(["--image-path", image_path, "--image-strength", str(image_strength)])

    ctx.step("Uruchamianie generatora obrazu (MFLUX)…")
    ctx.progress(0.06, "Pierwsze użycie modelu może chwilę pobierać wagi…")

    # Monotonic progress: ignore the model-fetch / weight-load lines (their own
    # 100%/N/N counters used to spike the bar to ~95% before the real denoise steps
    # dragged it back down), and never let the bar move backwards.
    prog = {"v": 0.06}

    def on_line(line: str):
        low = line.lower()
        if any(x in low for x in ("fetch", "file", "download", "loading", "saving",
                                  "metadata", "safetensor", " vae", "tokenizer", "text encoder")):
            return
        m = re.search(r"(\d+)\s*/\s*(\d+)", line)
        pct = re.search(r"(\d+)\s*%", line)
        frac = None
        if m and int(m.group(2)) > 1:
            frac = int(m.group(1)) / int(m.group(2))
        elif pct:
            frac = int(pct.group(1)) / 100
        if frac is None:
            return
        target = 0.1 + max(0.0, min(1.0, frac)) * 0.85
        if target > prog["v"]:
            prog["v"] = target
            ctx.progress(target, "Generowanie obrazu…")

    code, lines = _run_streaming(command, root, ctx, on_line, "image")
    if ctx.cancelled:
        from jobs import st_shim  # type: ignore
        raise st_shim.StopException()
    if code != 0:
        raise RuntimeError(_image_error_hint(lines) or ("\n".join(lines))[-1500:] or "Render obrazu zakończył się błędem.")

    generated = [p for p in _resolve_generated_images(out_path, seed, batch) if p.exists()]
    if not generated:
        raise RuntimeError("Render zakończony, ale nie znaleziono pliku obrazu.")

    reuse_keys = ["model", "model_label", "prompt", "negative_prompt", "style_label", "format",
                  "resolution_label", "width", "height", "steps", "guidance_enabled", "guidance",
                  "low_ram", "mlx_cache_limit_gb", "batch_count", "image_to_image", "image_strength"]
    tracks = []
    for i, p in enumerate(generated):
        reuse = {k: settings.get(k) for k in reuse_keys}
        reuse.update({"seed": seed + i, "seed_random": False, "batch_count": 1})
        _append_history("image", {
            "file_name": p.name, "prompt": prompt, "model": model,
            "width": width, "height": height, "steps": steps, "seed": seed + i,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "settings": reuse,
        })
        tracks.append({"file_name": p.name, "url": f"/api/videogen/file?path={quote(p.name)}", "path": str(p.resolve())})

    ctx.progress(1.0, f"Gotowe — {len(tracks)} obraz(y).")
    return {"prompt": prompt, "items": tracks}


def _image_error_hint(lines: List[str]) -> Optional[str]:
    log = "\n".join(lines)
    if "GatedRepoError" in log or "401 Unauthorized" in log or "Cannot access gated repo" in log:
        return "Wybrany model jest gated na Hugging Face. Wybierz publiczny wariant MFLUX Q4."
    if "No space left on device" in log:
        return "Brakuje miejsca na dysku na pobranie lub zapis modelu/obrazu."
    if "metal::malloc" in log or "maximum allowed buffer size" in log or "Attempting to allocate" in log:
        return "Zabrakło pamięci dla tej rozdzielczości. Spróbuj mniejszy rozmiar albo włącz Low RAM."
    return None


# ---------------------------------------------------------------------------
# Video generation
# ---------------------------------------------------------------------------
def _build_video_prompt(prompt: str, audio_enabled: bool, sound: str, spoken: str) -> str:
    parts = [prompt.strip()]
    if audio_enabled and sound.strip():
        parts.append(f"Audio: {sound.strip()}")
    if audio_enabled and spoken.strip():
        parts.append('If speech is present, the person should say: '
                     f'"{spoken.strip()}". Keep the voice natural and synced to the scene.')
    if not audio_enabled:
        parts.append("Generate the scene visually. Audio will be removed after rendering.")
    return "\n\n".join(p for p in parts if p)


def _video_progress(line: str, audio_enabled: bool, started: float) -> Optional[Tuple[float, str]]:
    clean = line.replace("\r", "").strip()
    if not clean:
        return None
    frac, stage = None, "Praca"
    if "Enhancing prompt" in clean:
        frac, stage = 0.08, "Rozbudowa prompta"
    elif clean.startswith("Enhanced:"):
        frac, stage = 0.14, "Prompt gotowy"
    elif "Fetching" in clean and "files" in clean:
        m = re.search(r"(\d+)%", clean)
        frac, stage = 0.03 + (int(m.group(1)) / 100 if m else 0) * 0.12, "Sprawdzanie modeli"
    elif clean.startswith("Mode:"):
        frac, stage = 0.15, "Ładowanie pipeline"
    elif "Denoising" in clean:
        pm = re.search(r"(\d+)%", clean)
        cm = re.search(r"(\d+)/(\d+)", clean)
        d = (int(pm.group(1)) / 100) if pm else (int(cm.group(1)) / max(1, int(cm.group(2))) if cm else 0)
        frac, stage = 0.18 + d * 0.62, "Generowanie klatek"
    elif "Saved to:" in clean:
        frac, stage = (0.96 if audio_enabled else 0.90), "Zapisywanie pliku"
    elif "Removing audio" in clean:
        frac, stage = 0.97, "Usuwanie audio"
    if frac is None:
        return None
    return max(0.0, min(1.0, frac)), stage


def _ltx_pipeline_flag(model: str) -> str:
    """Pipeline mode required by `ltx-2-mlx generate` (v0.14+), inferred from the
    model's transformer checkpoint. Distilled models (q4) → --distilled; a dev
    transformer (q8) → --two-stage. Falls back to --distilled (the fastest path)."""
    try:
        mdir = Path(model)
        if (mdir / "transformer-distilled.safetensors").exists():
            return "--distilled"
        if (mdir / "transformer-dev.safetensors").exists():
            return "--two-stage"
    except Exception:
        pass
    return "--distilled"


def generate_video(settings: Dict[str, Any], config_app: Optional[Dict[str, Any]], ctx: Any) -> Dict[str, Any]:
    root = videogen_dir(config_app)
    if root is None:
        raise RuntimeError("Nie znaleziono silnika generatora wideo (VideoGenerator).")
    vendor = _vendor_dir(root)

    model = str(settings.get("model") or str(root / "models" / "ltx-2.3-mlx-q4"))
    prompt = str(settings.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Prompt jest pusty.")
    width = int(settings.get("width", 512))
    height = int(settings.get("height", 320))
    fps = float(settings.get("fps", 24))
    frames = int(settings.get("frames") or frames_for_duration(float(settings.get("duration", 4.0)), fps))
    steps = int(settings.get("steps", 8))
    seed = int(settings.get("seed", 42))
    audio_enabled = bool(settings.get("audio_enabled", True))
    sound = str(settings.get("sound_prompt") or "")
    spoken = str(settings.get("spoken_text") or "")
    image_path = str(settings.get("image_path") or "").strip()

    if width % 32 or height % 32:
        raise RuntimeError("Wymiary muszą być podzielne przez 32 — wybierz preset oznaczony „safe”.")
    if frames < 9 or (frames - 1) % 8:
        raise RuntimeError("Liczba klatek musi mieć postać 8k+1.")
    if missing_ltx_files(model):
        raise RuntimeError("Brakuje lokalnych plików modelu LTX. Pobierz model przed generowaniem.")

    ts = time.strftime("%Y%m%d-%H%M%S")
    mode = "i2v" if image_path else "t2v"
    final_path = video_dir() / f"{ts}-{mode}.mp4"
    render_path = final_path if audio_enabled else final_path.with_name(final_path.stem + "-audio-source.mp4")

    full_prompt = _build_video_prompt(prompt, audio_enabled, sound, spoken)
    # ltx-2-mlx (v0.14+) requires an explicit pipeline mode on `generate`. Pick it from
    # the model's transformer files: q4 ships only `transformer-distilled` → --distilled;
    # a `transformer-dev` checkpoint (e.g. q8) → --two-stage. Defaults to --distilled.
    pipeline_flag = _ltx_pipeline_flag(model)
    command = ["uv", "run", "ltx-2-mlx", "generate", "--prompt", full_prompt,
               "--output", str(render_path), "--model", model, "--gemma", DEFAULT_GEMMA,
               "--height", str(height), "--width", str(width), "--frames", str(frames),
               "--frame-rate", str(fps), "--steps", str(steps), "--seed", str(seed),
               pipeline_flag]
    if image_path and Path(image_path).exists():
        command.extend(["--image", image_path])

    started = time.time()
    ctx.step("Uruchamianie renderu LTX…")
    ctx.progress(0.02, "Pierwsze użycie modelu może chwilę potrwać…")

    # Monotonic — the denoise phase is the only one that should drive the bar; never
    # let model-fetch counters drag it backwards.
    prog = {"v": 0.02}

    def on_line(line: str):
        p = _video_progress(line, audio_enabled, started)
        if p and p[0] >= prog["v"]:
            prog["v"] = p[0]
            ctx.progress(p[0], p[1])

    code, lines = _run_streaming(command, vendor, ctx, on_line, "video")
    if ctx.cancelled:
        from jobs import st_shim  # type: ignore
        raise st_shim.StopException()

    if code == 0 and not audio_enabled and render_path.exists():
        ctx.progress(0.97, "Usuwanie ścieżki audio…")
        strip = subprocess.run(["ffmpeg", "-y", "-i", str(render_path), "-c:v", "copy", "-an", str(final_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if strip.returncode != 0:
            code = strip.returncode
        else:
            render_path.unlink(missing_ok=True)

    if code != 0:
        raise RuntimeError(("\n".join(lines))[-1500:] or "Render wideo zakończył się błędem.")
    if not final_path.exists():
        raise RuntimeError("Render zakończony, ale nie znaleziono pliku wideo.")

    reuse_keys = ["model_label", "mode", "resolution_label", "width", "height", "duration_label",
                  "duration", "fps", "steps", "prompt", "audio_enabled", "sound_prompt", "spoken_text"]
    video_reuse = {k: settings.get(k) for k in reuse_keys}
    video_reuse.update({"seed": seed, "seed_random": False})
    _append_history("video", {
        "file_name": final_path.name, "prompt": prompt, "mode": mode,
        "width": width, "height": height, "frames": frames, "fps": fps,
        "steps": steps, "seed": seed, "audio": audio_enabled,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "settings": video_reuse,
    })
    ctx.progress(1.0, "Gotowe.")
    return {"prompt": prompt, "items": [{"file_name": final_path.name,
                                          "url": f"/api/videogen/file?path={quote(final_path.name)}",
                                          "path": str(final_path.resolve())}]}
