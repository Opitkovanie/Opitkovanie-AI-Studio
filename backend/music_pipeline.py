"""Music Generator engine for DubCut Studio.

Drives a system-installed ACE-Step 1.5 engine (the heavy 50+ GB checkpoints live once
on the machine, never inside the app/DMG). The engine is an HTTP API on port 8001 that
we launch *only when needed* (a generation is requested or the user explicitly loads the
model) and stop again to free RAM — keeping the rest of DubCut light.

Ported from the standalone Streamlit Music Generator, but reshaped to the native DubCut
pattern: generation runs as a background job that streams progress over SSE, instead of
the old 2-second polling loop.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

ACE_HOST = "127.0.0.1"
ACE_PORT = 8001
ACE_BASE_URL = f"http://{ACE_HOST}:{ACE_PORT}"

MODEL_OPTIONS = [
    "acestep-v15-turbo",
    "acestep-v15-sft",
    "acestep-v15-xl-turbo",
    "acestep-v15-xl-sft",
]
DEFAULT_MODEL = "acestep-v15-turbo"
# Models that misbehave on Apple Silicon (MPS OOM / metallic artefacts). Kept selectable
# but flagged in the UI so the working Turbo model is the obvious default.
PROBLEMATIC_MODELS = {
    "acestep-v15-xl-turbo": "Ten model potrafi wyczerpać pamięć MPS na Macu. Zalecany: acestep-v15-turbo.",
    "acestep-v15-xl-sft": "Ten model potrafi wyczerpać pamięć MPS na Macu. Zalecany: acestep-v15-turbo.",
    "acestep-v15-sft": "Ten model daje tutaj metaliczne wyniki. Zalecany: acestep-v15-turbo.",
}
FORMAT_OPTIONS = ["mp3", "wav", "flac"]
VARIANT_OPTIONS = [1, 2, 3, 4]

LANGUAGE_OPTIONS = [
    "unknown", "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "es", "fa", "fi", "fr", "he", "hi", "hr", "ht", "hu", "id", "is", "it",
    "ja", "ko", "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro",
    "ru", "sa", "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk",
    "ur", "vi", "yue", "zh",
]
LANGUAGE_LABELS = {
    "unknown": "Automatycznie", "ar": "Arabski", "az": "Azerbejdżański", "bg": "Bułgarski",
    "bn": "Bengalski", "ca": "Kataloński", "cs": "Czeski", "da": "Duński", "de": "Niemiecki",
    "el": "Grecki", "en": "Angielski", "es": "Hiszpański", "fa": "Perski", "fi": "Fiński",
    "fr": "Francuski", "he": "Hebrajski", "hi": "Hindi", "hr": "Chorwacki", "ht": "Haitański kreolski",
    "hu": "Węgierski", "id": "Indonezyjski", "is": "Islandzki", "it": "Włoski", "ja": "Japoński",
    "ko": "Koreański", "la": "Łacina", "lt": "Litewski", "ms": "Malajski", "ne": "Nepalski",
    "nl": "Niderlandzki", "no": "Norweski", "pa": "Pendżabski", "pl": "Polski", "pt": "Portugalski",
    "ro": "Rumuński", "ru": "Rosyjski", "sa": "Sanskryt", "sk": "Słowacki", "sr": "Serbski",
    "sv": "Szwedzki", "sw": "Suahili", "ta": "Tamilski", "te": "Telugu", "th": "Tajski",
    "tl": "Tagalski", "tr": "Turecki", "uk": "Ukraiński", "ur": "Urdu", "vi": "Wietnamski",
    "yue": "Kantoński", "zh": "Chiński",
}

BPM_OPTIONS = [
    "auto", "60", "70", "80", "90", "100", "110", "118", "120", "128",
    "135", "140", "150", "160", "180",
]
BPM_LABELS = {
    "auto": "Automatycznie", "60": "60 BPM – bardzo wolno", "70": "70 BPM – ballada",
    "80": "80 BPM – spokojnie", "90": "90 BPM – średnio wolno", "100": "100 BPM – pop wolniejszy",
    "110": "110 BPM – pop", "118": "118 BPM – pop/indie", "120": "120 BPM – standard pop/dance",
    "128": "128 BPM – dance/club", "135": "135 BPM – szybki dance", "140": "140 BPM – trap/dubstep",
    "150": "150 BPM – szybki pop/rock", "160": "160 BPM – punk/dnb", "180": "180 BPM – bardzo szybko",
}

_NOTES = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]
KEY_SCALE_OPTIONS = ["auto"] + [f"{note} {mode}" for note in _NOTES for mode in ("major", "minor")]
KEY_SCALE_LABELS = {"auto": "Automatycznie"}
for _ks in KEY_SCALE_OPTIONS:
    if _ks != "auto":
        _note, _mode = _ks.split(" ")
        KEY_SCALE_LABELS[_ks] = f"{_note} {'durowa' if _mode == 'major' else 'molowa'}"

TIME_SIGNATURE_OPTIONS = ["auto", "4/4", "3/4", "2/4", "6/8"]
TIME_SIGNATURE_LABELS = {
    "auto": "Automatycznie", "4/4": "4/4 – najczęstsze", "3/4": "3/4 – walc / ballada",
    "2/4": "2/4 – marsz / proste tempo", "6/8": "6/8 – folk / kołysanie",
}

VOCAL_TYPE_OPTIONS = ["auto", "male", "female", "duet"]
VOCAL_TYPE_LABELS = {"auto": "Automatycznie", "male": "Męski", "female": "Żeński", "duet": "Duet męski i żeński"}
VOCAL_PROMPT_HINTS = {
    "auto": "",
    "male": "male lead vocal, male singer, masculine voice, baritone vocal, no female vocals, not a female singer",
    "female": "female lead vocal, female singer, feminine voice, no male lead vocal",
    "duet": (
        "balanced male and female duet vocals, clearly audible male singer and clearly audible female singer, "
        "alternate male verse and female verse, both voices in choruses, call and response, "
        "do not make it female-only, do not use only one singer"
    ),
}


# ---------------------------------------------------------------------------
# Engine location
# ---------------------------------------------------------------------------
def _app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ace_dir(config_app: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Locate the system-installed ACE-Step engine.

    Search order: explicit env (set by Electron) → user setting → bundled vendor copy →
    a sibling "Music Generator" project next to DubCut Studio (the common local layout).
    """
    candidates: List[Path] = []
    env = os.environ.get("DUBCUT_ACE_DIR")
    if env:
        candidates.append(Path(env))
    if config_app:
        setting = str(config_app.get("ace_dir") or "").strip()
        if setting:
            candidates.append(Path(setting))
    engines = os.environ.get("DUBCUT_ENGINES_DIR")
    if engines:
        candidates.append(Path(engines) / "ACE-Step-1.5")
    candidates.append(_app_root() / "vendor" / "ACE-Step-1.5")
    # NOTE: external-disk / sibling "Music Generator" copies are intentionally NOT
    # auto-detected — the app installs the engine into the system (engines dir).
    # A user who insists on an existing copy sets `app.ace_dir` explicitly.
    for c in candidates:
        try:
            if c.exists() and (c / "pyproject.toml").exists():
                return c
        except Exception:
            continue
    return None


def _ace_venv() -> str:
    return os.environ.get("ACE_STEP_VENV", str(Path.home() / ".cache" / "music-generator" / "ace-step-venv"))


def uv_available() -> bool:
    import shutil
    return shutil.which("uv") is not None


def _pid_path() -> Path:
    from config_store import data_dir  # type: ignore
    return data_dir() / ".ace-step.pid"


def _log_path() -> Path:
    from config_store import module_dir  # type: ignore
    return module_dir("cache") / "ace-step.log"


# ---------------------------------------------------------------------------
# Engine process lifecycle (start / stop / health)
# ---------------------------------------------------------------------------
def find_ace_pids() -> List[int]:
    try:
        result = subprocess.run(["lsof", "-ti", f"tcp:{ACE_PORT}"], check=False, capture_output=True, text=True)
    except Exception:
        return []
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]


def ace_process_running() -> bool:
    return bool(find_ace_pids()) or _pid_path().exists()


def check_health(timeout: float = 2.0) -> Tuple[bool, Dict[str, Any]]:
    try:
        response = requests.get(f"{ACE_BASE_URL}/health", timeout=timeout)
        response.raise_for_status()
        return True, response.json()
    except Exception as exc:
        return False, {"error": str(exc)}


def engine_status(config_app: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Snapshot for the UI status pill."""
    directory = ace_dir(config_app)
    ok, health = check_health(timeout=2.0)
    inner = health.get("data", {}) if ok else {}
    loaded_model = inner.get("loaded_model") if ok else None
    initialized = bool(inner.get("models_initialized")) if ok else False
    running = ace_process_running()
    if ok and initialized:
        state = "ready"
    elif ok or running:
        state = "loading"
    else:
        state = "stopped"
    return {
        "state": state,                       # ready | loading | stopped
        "running": running,
        "loaded_model": loaded_model,
        "models_initialized": initialized,
        "engine_available": directory is not None,
        "engine_dir": str(directory) if directory else "",
        "uv_available": uv_available(),
        "error": "" if ok else str(health.get("error", "")),
    }


def start_ace(model: str, config_app: Optional[Dict[str, Any]] = None) -> int:
    directory = ace_dir(config_app)
    if directory is None:
        raise RuntimeError(
            "Nie znaleziono silnika ACE-Step. Zainstaluj go raz w systemie i wskaż folder "
            "w Ustawieniach (vendor/ACE-Step-1.5)."
        )
    if not uv_available():
        raise RuntimeError("Brakuje `uv` w systemie — jest wymagane do uruchomienia silnika ACE-Step.")

    env = os.environ.copy()
    env.update({
        "COPYFILE_DISABLE": "1",
        "UV_LINK_MODE": "copy",
        "UV_PROJECT_ENVIRONMENT": _ace_venv(),
        "ACESTEP_CACHE_DIR": env.get(
            "ACESTEP_CACHE_DIR", str(Path.home() / ".cache" / "music-generator" / "acestep-cache")
        ),
        "ACESTEP_CONFIG_PATH": model,
        "ACESTEP_NO_INIT": env.get("ACESTEP_NO_INIT", "false"),
        "PYTORCH_ENABLE_MPS_FALLBACK": env.get("PYTORCH_ENABLE_MPS_FALLBACK", "1"),
        "ACESTEP_QUEUE_WORKERS": env.get("ACESTEP_QUEUE_WORKERS", "1"),
    })

    log_file = _log_path().open("a", encoding="utf-8")
    process = subprocess.Popen(
        ["uv", "run", "acestep-api", "--host", ACE_HOST, "--port", str(ACE_PORT)],
        cwd=str(directory), env=env, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
    )
    _pid_path().write_text(str(process.pid), encoding="utf-8")
    return process.pid


def stop_ace() -> None:
    """Stop the engine and free its RAM/VRAM."""
    pids = set(find_ace_pids())
    if _pid_path().exists():
        try:
            pids.add(int(_pid_path().read_text(encoding="utf-8").strip()))
        except Exception:
            pass

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

    deadline = time.time() + 20
    while time.time() < deadline:
        if not find_ace_pids():
            break
        time.sleep(0.5)

    for pid in find_ace_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    _pid_path().unlink(missing_ok=True)


def ensure_loaded(model: str, config_app: Optional[Dict[str, Any]] = None,
                  ctx: Any = None, timeout_seconds: int = 900) -> str:
    """Make sure the engine is running with `model` loaded into memory.

    If a different model is loaded, restart so memory holds exactly one model. Reports
    progress through the job ctx while the (slow) first load happens.
    """
    def _log(msg: str, value: float):
        if ctx is not None:
            ctx.progress(value, msg)

    ok, health = check_health(timeout=3)
    inner = health.get("data", {}) if ok else {}
    if ok and bool(inner.get("models_initialized")) and str(inner.get("loaded_model")) == model:
        return model  # already hot with the right model

    # Wrong model or not initialised → (re)start clean.
    if ace_process_running() and (not ok or str(inner.get("loaded_model")) != model):
        _log("Zwalnianie poprzedniego modelu z pamięci…", 0.02)
        stop_ace()

    _log(f"Ładowanie modelu „{model}” do pamięci…", 0.04)
    start_ace(model, config_app)

    deadline = time.time() + timeout_seconds
    last = ""
    while time.time() < deadline:
        if ctx is not None:
            ctx.check_cancel()
        ok, health = check_health(timeout=5)
        if ok:
            data = health.get("data", {})
            loaded = str(data.get("loaded_model", model))
            if bool(data.get("models_initialized")) and loaded == model:
                _log(f"Model „{loaded}” gotowy w pamięci.", 0.1)
                return loaded
            last = f"ładuje: {loaded}"
        else:
            last = str(health.get("error", ""))
        _log("Ładowanie modelu do pamięci (pierwsze użycie może potrwać)…", 0.06)
        time.sleep(1.5)
    raise RuntimeError(f"Silnik ACE-Step nie załadował modelu w wyznaczonym czasie. {last}")


# ---------------------------------------------------------------------------
# Prompt / lyrics shaping & duration estimate
# ---------------------------------------------------------------------------
def model_step_bounds(model: str) -> Tuple[int, int]:
    return (4, 8) if "turbo" in model else (4, 64)


def model_variant_options(model: str) -> List[int]:
    return [1] if model.startswith("acestep-v15-xl") else VARIANT_OPTIONS


# Caption reinforcement for instrumental tracks. Without this the caption/metadata LM
# (thinking=True) tends to drift toward vocal arrangements; spelling it out keeps the
# arrangement vocal-free and pushes the model toward a fuller instrumental mix.
INSTRUMENTAL_PROMPT_HINT = (
    "instrumental, no vocals, no lyrics, no singing, purely instrumental arrangement, "
    "rich layered instrumentation"
)


def build_generation_prompt(prompt: str, vocal_type: str, instrumental: bool) -> str:
    parts = [prompt.strip()]
    if instrumental:
        parts.append(INSTRUMENTAL_PROMPT_HINT)
    else:
        hint = VOCAL_PROMPT_HINTS.get(vocal_type, "")
        if hint:
            parts.append(hint)
    return ", ".join(p for p in parts if p)


def build_generation_lyrics(lyrics: str, vocal_type: str, instrumental: bool) -> str:
    if instrumental:
        # The engine recognises instrumental purely from the lyrics field, and the model
        # was trained with the literal "[instrumental]" tag as the conditioning for
        # vocal-free tracks (the bundled CLI normalises empty lyrics to "[Instrumental]"
        # too). Sending an empty string instead is out-of-distribution and, with
        # thinking=True, lets the LM hallucinate lyrics that then get sung — the usual
        # cause of "instrumental" output that still has vocals/mumbling.
        return "[instrumental]"
    if vocal_type != "duet":
        return lyrics
    header = (
        "[Vocal arrangement: duet]\n"
        "[Male singer: lead the first verse with a warm masculine voice]\n"
        "[Female singer: answer in the second verse with a clear feminine voice]\n"
        "[Chorus: male and female sing together, both voices audible]\n"
    )
    return f"{header}\n{lyrics.strip()}"


def count_lyric_words(lyrics: str) -> int:
    clean = []
    for line in lyrics.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            continue
        clean.append(s)
    return len(re.findall(r"\b[\w'-]+\b", " ".join(clean), flags=re.UNICODE))


def estimate_duration(lyrics: str) -> Dict[str, int]:
    words = count_lyric_words(lyrics)
    if words == 0:
        return {"min": 60, "max": 90, "suggested": 75, "words": 0}
    min_s = round((words / 130) * 60)
    max_s = round((words / 95) * 60)
    mid = round((min_s + max_s) / 2)
    min_s = max(30, min_s + 16)
    max_s = max(min_s + 15, max_s + 24)
    mid = max(min_s, min(600, mid + 20))
    return {"min": min(600, min_s), "max": min(600, max_s), "suggested": min(600, max(10, mid)), "words": words}


# ---------------------------------------------------------------------------
# Output files & history
# ---------------------------------------------------------------------------
def output_dir() -> Path:
    from config_store import module_dir  # type: ignore
    return module_dir("music")


def _safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    cleaned = re.sub(r"[^\w .'-]+", "", normalized, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "Utwór"


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _existing_names() -> set:
    return {_normalized(p.name) for p in output_dir().iterdir() if not p.name.startswith("._")}


def _next_path(title: str, suffix: str) -> Path:
    stem = _safe_filename(title)
    existing = _existing_names()
    numbers = []
    pattern = re.compile(rf"{re.escape(stem)}(?: (\d+))?{re.escape(suffix)}")
    for name in existing:
        m = pattern.fullmatch(name)
        if m:
            numbers.append(int(m.group(1) or "1"))
    n = max(numbers, default=0) + 1
    while True:
        candidate = f"{stem} {n}{suffix}"
        if (
            _normalized(candidate) not in existing
            and _normalized(f"{candidate}.json") not in existing
            and not (output_dir() / candidate).exists()
        ):
            return output_dir() / candidate
        n += 1


def _ace_url(path: str) -> str:
    local = Path(path)
    if local.exists():
        return str(local)
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return f"{ACE_BASE_URL}{path}"
    return f"{ACE_BASE_URL}/v1/audio?path={quote(path, safe='')}"


def _audio_filename(path: str, index: int) -> str:
    candidate = path
    if "path=" in path:
        values = parse_qs(urlparse(path).query).get("path", [])
        if values:
            candidate = values[0]
    name = Path(unquote(candidate)).name
    return name if name and "." in name else f"audio-{index}.mp3"


def _fetch_bytes(path: str) -> bytes:
    if path and not path.startswith(("http://", "https://")):
        local = Path(path)
        if local.exists():
            return local.read_bytes()
    response = requests.get(_ace_url(path), timeout=(5, 120))
    response.raise_for_status()
    return response.content


def save_generated(source_paths: List[str], title: str, prompt: str, meta: Dict[str, Any]) -> List[str]:
    saved: List[str] = []
    for source in source_paths:
        filename = _audio_filename(str(source), len(saved) + 1)
        suffix = Path(filename).suffix.lower() or ".mp3"
        target = _next_path(title or "Utwór", suffix)
        target.write_bytes(_fetch_bytes(str(source)))
        sidecar = target.with_suffix(target.suffix + ".json")
        sidecar.write_text(
            json.dumps(
                {"title": title, "prompt": prompt, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "source_path": source, **meta},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        saved.append(str(target))
    return saved


def list_history() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for audio in output_dir().iterdir():
        if audio.name.startswith("._") or audio.name.endswith(".json"):
            continue
        if audio.suffix.lower() not in {".mp3", ".wav", ".flac", ".aac", ".opus"}:
            continue
        sidecar = audio.with_suffix(audio.suffix + ".json")
        metadata: Dict[str, Any] = {}
        if sidecar.exists():
            try:
                loaded = json.loads(sidecar.read_text(encoding="utf-8"))
                metadata = loaded if isinstance(loaded, dict) else {}
            except Exception:
                metadata = {}
        items.append({
            "file_name": audio.name,
            "title": metadata.get("title") or audio.stem,
            "prompt": metadata.get("prompt") or "",
            "settings": metadata.get("settings") or {},
            "created_at": metadata.get("created_at")
            or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(audio.stat().st_mtime)),
            "mtime": audio.stat().st_mtime,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# Generation (runs as a DubCut background job)
# ---------------------------------------------------------------------------
def _release_task(payload: Dict[str, Any]) -> str:
    response = requests.post(f"{ACE_BASE_URL}/release_task", json=payload, timeout=(5, 600))
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["data"]["task_id"]


def _query_task(task_id: str) -> Dict[str, Any]:
    try:
        response = requests.post(
            f"{ACE_BASE_URL}/query_result", json={"task_id_list": [task_id]}, timeout=(2, 6)
        )
        response.raise_for_status()
        item = response.json()["data"][0]
    except requests.RequestException as exc:
        return {"status": 0, "progress_text": "Silnik jest zajęty…", "result": [], "temporary_error": str(exc)}
    try:
        result = json.loads(item.get("result") or "[]")
    except json.JSONDecodeError:
        result = []
    return {
        "status": int(item.get("status", 0)),
        "progress_text": item.get("progress_text", ""),
        "result": result,
        "temporary_error": "",
    }


def _clean_progress(text: str, progress: float, stage: str) -> str:
    raw = (text or "").strip()
    lower = raw.lower()
    markers = ("generate_with_stop_condition", "<|im_start|>", "<|im_end|>", "instruction", "caption", "lyric")
    if any(m in lower for m in markers):
        return "Model analizuje tekst i przygotowuje strukturę utworu…"
    if "mlx cfg batch gen" in lower or "generating audio" in lower:
        return "Generowanie audio…"
    if "decod" in lower:
        return "Dekodowanie audio…"
    if "save" in lower:
        return "Zapisywanie pliku audio…"
    if raw and len(raw) <= 140:
        return raw
    if progress >= 1:
        return "Gotowe."
    if "phase 2" in stage.lower():
        return "Generowanie audio…"
    return "Pracuję lokalnie…"


def generate(settings: Dict[str, Any], config_app: Optional[Dict[str, Any]], ctx: Any) -> Dict[str, Any]:
    """Job target: load engine if needed, generate, save audio, optionally free memory."""
    model = str(settings.get("model") or DEFAULT_MODEL)
    if model not in MODEL_OPTIONS:
        model = DEFAULT_MODEL
    title = str(settings.get("title") or "Utwór")
    prompt = str(settings.get("prompt") or "")
    lyrics = str(settings.get("lyrics") or "")
    vocal_type = str(settings.get("vocal_type") or "auto")
    instrumental = bool(settings.get("instrumental"))
    auto_unload = bool(settings.get("auto_unload", True))

    gen_prompt = build_generation_prompt(prompt, vocal_type, instrumental)
    gen_lyrics = build_generation_lyrics(lyrics, vocal_type, instrumental)

    min_steps, max_steps = model_step_bounds(model)
    steps = int(settings.get("inference_steps", min_steps))
    steps = max(min_steps, min(max_steps, steps))
    variant = int(settings.get("variant_count", 2))
    if variant not in model_variant_options(model):
        variant = model_variant_options(model)[0]

    bpm_choice = str(settings.get("bpm_choice") or "auto")
    key_scale = str(settings.get("key_scale_choice") or "auto")
    time_sig = str(settings.get("time_signature_choice") or "auto")
    seed = str(settings.get("seed") or "").strip()
    audio_format = str(settings.get("audio_format") or "mp3")
    if audio_format not in FORMAT_OPTIONS:
        audio_format = "mp3"

    ctx.step("Przygotowanie silnika ACE-Step…")
    ensure_loaded(model, config_app, ctx)

    payload = {
        "prompt": gen_prompt,
        "lyrics": gen_lyrics,
        "vocal_language": str(settings.get("language") or "unknown"),
        "model": model,
        "audio_duration": float(settings.get("duration", 120)),
        "bpm": int(bpm_choice) if bpm_choice != "auto" else None,
        "key_scale": "" if key_scale == "auto" else key_scale,
        "time_signature": "" if time_sig == "auto" else time_sig,
        "inference_steps": steps,
        "guidance_scale": float(settings.get("guidance_scale", 7.0)),
        "use_random_seed": seed == "",
        "seed": int(seed) if seed else -1,
        "instrumental": instrumental,
        "thinking": bool(settings.get("thinking", True)),
        "sample_mode": False,
        "task_type": "text2music",
        "lm_backend": "mlx",
        "audio_format": audio_format,
        "use_tiled_decode": True,
        "batch_size": variant,
        "allow_lm_batch": False,
    }

    ctx.progress(0.12, "Wysyłanie zadania do silnika…")
    task_id = _release_task(payload)

    try:
        while True:
            ctx.check_cancel()
            status = _query_task(task_id)
            rows = status["result"]
            first = rows[0] if rows else {}
            raw_progress = float(first.get("progress", 0.0) or 0.0)
            stage = str(first.get("stage") or "start")
            # Map engine progress (0..1) into the 0.12..0.92 band — load + save bookend it.
            mapped = 0.12 + max(0.0, min(1.0, raw_progress)) * 0.8
            ctx.progress(mapped, _clean_progress(status.get("progress_text", ""), raw_progress, stage))

            if status["status"] == 1:
                break
            if status["status"] == 2:
                err = first.get("error") or status.get("progress_text") or "Nieznany błąd silnika."
                raise RuntimeError(f"Generowanie zakończyło się błędem: {err}")
            time.sleep(1.5)

        audio_paths = [row.get("file") for row in rows if row.get("file")]
        if not audio_paths:
            raise RuntimeError("Zadanie zakończone, ale silnik nie zwrócił pliku audio.")

        ctx.progress(0.94, "Zapisywanie utworów…")
        reuse_keys = ["title", "prompt", "lyrics", "model", "language", "duration", "audio_format",
                      "bpm_choice", "key_scale_choice", "time_signature_choice", "seed", "vocal_type",
                      "variant_count", "inference_steps", "guidance_scale", "instrumental", "thinking"]
        reuse = {k: settings.get(k) for k in reuse_keys}
        saved = save_generated(
            [str(p) for p in audio_paths], title, gen_prompt,
            {"model": model, "duration": payload["audio_duration"], "lyrics": lyrics, "settings": reuse},
        )
    finally:
        if auto_unload:
            # ctx.progress now raises on cancel — guard it so the engine still unloads
            # (the original cancellation still propagates after this finally block).
            try:
                ctx.progress(0.98, "Zwalnianie modelu z pamięci…")
            except Exception:
                pass
            stop_ace()

    tracks = [
        {"file_name": Path(p).name, "url": f"/api/music/audio?path={quote(Path(p).name)}"}
        for p in saved
    ]
    ctx.progress(1.0, f"Gotowe — {len(tracks)} utwór(y).")
    return {"title": title, "prompt": gen_prompt, "tracks": tracks, "unloaded": auto_unload}
