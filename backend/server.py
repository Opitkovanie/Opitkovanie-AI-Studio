"""DubCut Studio — native backend (FastAPI). No Streamlit.

Reuses the DubMaster / ShortsGenerator engine modules (download, whisper, gemini, ffmpeg,
demucs, qwen-tts) through a streamlit shim, exposing them as a clean HTTP + SSE API that the
Electron/React desktop UI drives.

Heavy ML imports are deferred into the job bodies, so the server boots instantly even before
the model dependencies are installed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time as _time
from contextlib import asynccontextmanager
from urllib.parse import quote
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent
APP_ROOT = BACKEND_DIR.parent

# --- Make the shim and vendor engine modules importable -------------------------------
sys.path.insert(0, str(BACKEND_DIR / "shims"))  # provides `import streamlit`


def _runtime_dir(env_key: str, vendor_name: str) -> Path:
    """Prefer the writable runtime copy (set by Electron), else bundled vendor."""
    env = os.environ.get(env_key)
    if env and Path(env).exists():
        return Path(env)
    return APP_ROOT / "vendor" / vendor_name


SHORTS_DIR = _runtime_dir("DUBCUT_SHORTS_DIR", "ShortsGenerator")
DUB_DIR = _runtime_dir("DUBCUT_DUB_DIR", "DubMaster")

# The ShortsGenerator engine modules use flat sibling imports (e.g. `from downloader import ...`),
# so their directory must be importable. DubMaster is monolithic (Streamlit app.py) and is not
# imported, so we don't put it on the path to avoid name collisions.
if str(SHORTS_DIR) not in sys.path:
    sys.path.append(str(SHORTS_DIR))

import config_store  # noqa: E402
import music_pipeline  # noqa: E402
import videogen_pipeline  # noqa: E402
import omnivoice_engine  # noqa: E402
from jobs import manager  # noqa: E402

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
    from pydantic import BaseModel
except Exception as exc:  # pragma: no cover
    print(f"[dubcut-backend] FastAPI not installed: {exc}", file=sys.stderr)
    raise

@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    # Replaces the deprecated @app.on_event("startup") hooks. Both run synchronously
    # on boot; the functions are defined below and resolved from module globals at call time.
    _sweep_stale_config_root()
    _log_environment_health()
    yield


app = FastAPI(
    title="DubCut Studio Backend",
    version=os.environ.get("DUBCUT_VERSION", "5.0.68"),
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sweep_stale_config_root() -> None:
    """Older versions left per-clip temp (subtitle previews, picked base images) in the
    CONFIG root — those survive even when the user clears/deletes the work folder, so they
    pile up as garbage. All per-clip temp now lives under the work root, so wipe the old
    config-root leftovers on boot."""
    try:
        root = config_store.data_dir()
        for name in ("previews", "videogen_input"):
            stale = root / name
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)
    except Exception:
        pass


def _log_environment_health() -> None:
    """Print the environment check on boot so a missing tool / unplugged work disk /
    low space shows up in the log immediately, not only when a render fails later."""
    try:
        sys_health = _system_health()
        for w in sys_health.get("warnings", []):
            print(f"[dubcut-backend] ⚠ {w}", file=sys.stderr)
        if sys_health.get("ok"):
            print(
                f"[dubcut-backend] środowisko OK — FFmpeg, dysk roboczy "
                f"({sys_health.get('disk_free_gb', '?')} GB wolne).",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[dubcut-backend] health-check przy starcie nieudany: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Filesystem junk filter — the work disk is often exFAT/HFS, where macOS scatters
# AppleDouble sidecars ("._name") and ".DS_Store". These are NOT real media/data
# files; listing them produced phantom entries + occasional broken reads. One
# helper, used everywhere we enumerate a directory, so the rule stays consistent.
# ---------------------------------------------------------------------------
def _is_junk_name(name: str) -> bool:
    return name.startswith("._") or name == ".DS_Store"


def _force_rmtree(path) -> bool:
    """Remove a directory tree robustly on macOS + exFAT (the user's external work
    disk), where shutil.rmtree / rm -rf / dot_clean ALL fail to delete folders that
    contain non-ASCII filenames (e.g. „…Prądu…") plus their AppleDouble `._` sidecars:
    `unlink` reports "No such file or directory" on the `._` shadow (a NFC/NFD
    normalization mismatch), so the directory never empties and rmdir fails with
    ENOTEMPTY. A half-deleted version folder then 'resurrects' as a ghost entry.

    Verified-working strategy: RENAME each entry to a plain-ASCII temp name first
    (this rebinds the directory entry away from the un-unlinkable Unicode name), then
    remove it; recurse into subdirs; retry a few passes. Returns True iff gone after.

    Going forward `_safe_title` is ASCII-only so new renders avoid this entirely; this
    helper still cleans up folders created by older builds."""
    p = Path(path)

    def _purge(target: str) -> None:
        for _ in range(8):
            try:
                entries = list(os.scandir(target))
            except FileNotFoundError:
                return
            except Exception:
                entries = []
            if not entries:
                break
            for i, e in enumerate(entries):
                src = e.path
                tmp = os.path.join(target, f"._del_{i}_{int(_time.time() * 1000) % 100000}")
                try:
                    os.rename(src, tmp)
                    src = tmp
                except Exception:
                    pass  # fall back to the original name
                try:
                    if os.path.isdir(src) and not os.path.islink(src):
                        _purge(src)
                        try:
                            os.rmdir(src)
                        except Exception:
                            pass
                    else:
                        try:
                            os.remove(src)
                        except FileNotFoundError:
                            pass
                        except PermissionError:
                            try:
                                os.chmod(src, 0o777); os.remove(src)
                            except Exception:
                                pass
                except Exception:
                    pass
            _time.sleep(0.03)
        try:
            os.rmdir(target)
        except Exception:
            pass

    for _ in range(3):
        if not p.exists():
            return True
        _purge(str(p))
    # Last resort — best effort.
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    return not p.exists()


def _path_within(child: Path, parent: Path) -> bool:
    """True iff `child` is `parent` or a descendant of it. Uses relative_to (not a raw
    string prefix) so it never matches a sibling whose name merely starts with the
    parent's path, e.g. `/work-old` vs `/work` (the classic path-prefix bug)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _real_files(directory: Path, pattern: str = "*"):
    """Yield real files in `directory` matching `pattern`, skipping AppleDouble/
    .DS_Store junk. Safe on a missing/unmounted directory (yields nothing)."""
    try:
        for p in directory.glob(pattern):
            if _is_junk_name(p.name):
                continue
            yield p
    except Exception:
        return


# ---------------------------------------------------------------------------
# Static metadata (presets, fonts, languages) read straight from vendor config
# ---------------------------------------------------------------------------
def _load_shorts_meta() -> Dict[str, Any]:
    meta: Dict[str, Any] = {"presets": {}, "animations": [], "languages": [], "fonts": [], "logos": []}
    cfg_py = SHORTS_DIR / "config.py"
    if cfg_py.exists():
        # Import the vendor config module by path (it makedirs/chdir on import — harmless,
        # but we save & restore cwd to avoid surprising the rest of the server).
        import importlib.util as iu
        prev_cwd = os.getcwd()
        try:
            spec = iu.spec_from_file_location("_shorts_config", str(cfg_py))
            mod = iu.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            meta["presets"] = getattr(mod, "SUBTITLE_PRESETS", {})
            meta["animations"] = getattr(mod, "ANIMATION_TYPES", [])
            meta["languages"] = getattr(mod, "AVAILABLE_LANGS", [])
        except Exception:
            pass
        finally:
            try:
                os.chdir(prev_cwd)
            except Exception:
                pass
    # Faithful to ShortsGenerator.subtitle_engine.get_available_fonts()
    fonts = ["Arial", "Impact", "Consolas"]
    fonts_dir = SHORTS_DIR / "fonts"
    if fonts_dir.exists():
        for p in _real_files(fonts_dir):
            if p.suffix.lower() in (".ttf", ".otf"):
                fonts.append(p.name)
    meta["fonts"] = sorted(set(fonts), key=str.lower)
    logos: List[Dict[str, str]] = []
    for folder, prefix in ((SHORTS_DIR / "workspace", "workspace"), (SHORTS_DIR / "logo", "logo")):
        if not folder.exists():
            continue
        for p in _real_files(folder):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
                logos.append({"name": p.name, "path": f"{prefix}/{p.name}"})
    meta["logos"] = sorted({x["path"]: x for x in logos}.values(), key=lambda x: x["name"].lower())
    return meta


_SHORTS_META_CACHE: Optional[Dict[str, Any]] = None


def shorts_meta() -> Dict[str, Any]:
    global _SHORTS_META_CACHE
    if _SHORTS_META_CACHE is None:
        _SHORTS_META_CACHE = _load_shorts_meta()
    return _SHORTS_META_CACHE


# --- Faithful to DubMaster app.py constants ---
DUB_SOURCE_LANGUAGES = [
    "Automatyczne wykrywanie", "Polski", "Angielski", "Niemiecki", "Francuski",
    "Hiszpański", "Włoski", "Portugalski", "Holenderski", "Rosyjski", "Ukraiński",
    "Czeski", "Słowacki", "Szwedzki", "Norweski", "Duński", "Fiński", "Grecki",
    "Rumuński", "Węgierski", "Bułgarski", "Chorwacki", "Serbski", "Turecki",
    "Arabski", "Hebrajski", "Hindi", "Wietnamski", "Tajski", "Indonezyjski",
    "Japoński", "Koreański", "Chiński",
]
# Dubbing targets = exactly the 10 languages Qwen3-TTS speaks. No Polish (Qwen has
# no Polish voice) and no Arabic (not supported). Keep this in sync with
# dubbing_engine.QWEN_LANGS — these are the ONLY languages we can voice.
DUB_TARGET_LANGUAGES = [
    "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Włoski", "Portugalski",
    "Rosyjski", "Chiński", "Japoński", "Koreański",
]
# Subtitle/metadata translation targets — far wider than dubbing, because NLLB-200
# (and Gemini) translate ~200 languages even where we can't dub them. B2B partners
# need Dutch, Portuguese, Nordic, etc. Every entry here MUST exist in
# local_translate._LANG so NLLB/Argos get a FLORES code.
TRANSLATE_TARGET_LANGUAGES = [
    "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Włoski", "Portugalski",
    "Holenderski", "Polski", "Rosyjski", "Ukraiński", "Czeski", "Słowacki",
    "Szwedzki", "Norweski", "Duński", "Fiński", "Grecki", "Rumuński", "Węgierski",
    "Bułgarski", "Chorwacki", "Serbski", "Turecki", "Arabski", "Hebrajski",
    "Hindi", "Wietnamski", "Tajski", "Indonezyjski", "Japoński", "Koreański",
    "Chiński",
]
# OmniVoice can VOICE far more languages than Qwen (incl. Polish), so when the user
# selects the OmniVoice engine the dubbing target list widens to the full translatable
# set. Kept in sync with dubbing_engine.OMNIVOICE_LANGS.
OMNIVOICE_DUB_LANGUAGES = list(TRANSLATE_TARGET_LANGUAGES)
# Selectable voice engines (global app setting `tts_engine`). Used by every module.
TTS_ENGINES = [
    {"id": "qwen", "label": "Qwen TTS (10 języków, głosy presetowe)"},
    {"id": "omnivoice", "label": "OmniVoice (jakość studyjna, polski, klonowanie głosu)"},
]
# Most-common languages first, long tail after — drives the order of the dubbing /
# Text→Audio language picker so users see useful targets at the top.
POPULAR_LANG_ORDER = [
    "Angielski", "Polski", "Hiszpański", "Niemiecki", "Francuski", "Włoski",
    "Portugalski", "Rosyjski", "Ukraiński", "Holenderski", "Chiński", "Japoński",
    "Koreański", "Arabski", "Hindi", "Turecki", "Wietnamski", "Indonezyjski",
    "Czeski", "Szwedzki", "Grecki", "Rumuński", "Węgierski", "Duński", "Norweski",
    "Fiński", "Słowacki", "Bułgarski", "Chorwacki", "Serbski", "Hebrajski", "Tajski",
]


def _tts_voiceable_languages(tts_engine: str) -> List[str]:
    """Languages the ACTIVE voice engine can speak."""
    return OMNIVOICE_DUB_LANGUAGES if tts_engine in ("omnivoice", "omni") else DUB_TARGET_LANGUAGES


def _dub_target_languages(app_cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Dubbing/Text→Audio targets = languages BOTH the active TTS engine can voice AND
    the translation model can produce, ordered most-popular first. Recomputed from the
    current settings so switching the voice/translation engine changes the list live."""
    app_cfg = app_cfg or config_store.load().get("app", {})
    tts_engine = str(app_cfg.get("tts_engine", "qwen")).lower()
    tts = set(_tts_voiceable_languages(tts_engine))
    # All exposed translation engines (NLLB/Argos/Gemini) cover this set; it's the
    # universe of languages the app can translate into.
    translatable = set(TRANSLATE_TARGET_LANGUAGES)
    both = tts & translatable
    ordered = [l for l in POPULAR_LANG_ORDER if l in both]
    ordered += [l for l in TRANSLATE_TARGET_LANGUAGES if l in both and l not in ordered]
    return ordered
QWEN_SPEAKERS = [
    "Ryan", "Aiden", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee",
]
TTS_MODELS = ["0.6B (Szybki)", "1.7B (Wysoka jakość)"]
TRANSLATION_MODELS = [
    "Gemini 2.5 Flash (Lokalizacja 2-Etapowa)",
    "Brak (Tylko transkrypcja)",
]
# Local-first translation engines for Shorts (subtitle + dub localisation).
# id matches app.translation_engine; label is shown in Settings.
TRANSLATION_ENGINES = [
    {"id": "nllb", "label": "NLLB-200 (lokalny, najlepsza jakość)"},
    {"id": "argos", "label": "Argos (lokalny, lekki CPU)"},
    {"id": "gemini", "label": "Gemini 2.5 Flash (chmura, wymaga klucza)"},
]
DUB_MIX_MODES = [
    "Czysty dubbing (usuń oryginalny głos)",
    "Lektor (oryginał + głos AI)",
    "Lektor z duckingiem (oryginał ścisza się pod AI)",
]
DUB_VOICEOVER_ENGINES = [
    "Stabilny lektor systemowy (bez halucynacji)",
    "Qwen TTS (eksperymentalny, naturalniejszy)",
]
DUB_VOICE_SOURCES = [
    "Głos z oryginalnego filmu",
    "Sklonowany głos (własna próbka)",
    "Głos presetowy (Qwen)",
]
DUB_VOICE_STORE_MODES = ["Próbki własne", "Biblioteka głosów"]
DUB_OUTPUT_RES = ["Auto (jak oryginał)", "480p", "720p", "1080p", "4K (2160p)"]
DUB_YT_QUALITIES = ["1080p", "1440p", "2160p (4K)", "720p", "Najlepsza"]
DUB_CLONE_MODES = [
    "Strict Voice Clone (stabilniejsza barwa głosu)",
    "Expressive Clone (więcej ekspresji)",
]
# --- Faithful to ShortsGenerator constants ---
SHORTS_YT_QUALITIES = ["Najlepsza dostępna", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p"]
SHORTS_EXPORT_RES = ["Zgodna ze źródłem", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p"]
SHORTS_PROXY_RES = ["1080p", "720p"]
SHORTS_CODECS = ["H.264 (Większa kompatybilność)", "H.265 / HEVC"]
SHORTS_ASPECT = ["9:16 (Pionowy - Short/TikTok)", "16:9 (Oryginalny - YouTube)"]
SHORTS_SUB_MODES = ["highlight", "highlight_box", "word_by_word", "build_up", "fade"]
SHORTS_FT_STRATEGIES = [
    "Główny mówca (Skupia na największej twarzy)",
    "Utrzymuj cel (Śledzi jedną wybraną twarz)",
]
SHORTS_FT_TRACKERS = [
    "Auto (sam dobiera)",
    "ByteTrack (1 osoba, najszybszy)",
    "BoT-SORT (wiele osób / zasłanianie)",
]
SHORTS_WHISPER_LANGS = [
    "Auto-detekcja", "Polski", "Angielski", "Niemiecki", "Francuski", "Hiszpański",
    "Włoski", "Portugalski", "Holenderski", "Rosyjski", "Ukraiński", "Czeski",
    "Słowacki", "Szwedzki", "Norweski", "Duński", "Fiński", "Grecki", "Rumuński",
    "Węgierski", "Bułgarski", "Chorwacki", "Serbski", "Turecki", "Arabski",
    "Hebrajski", "Hindi", "Wietnamski", "Tajski", "Indonezyjski", "Japoński",
    "Koreański", "Chiński",
]
SHORTS_PROMPT_MODES = [
    "Precyzyjna (Domyślna - bardziej restrykcyjna)",
    "Kreatywna (Więcej swobody AI)",
    "Trailer / Zapowiedź",
    "Własny prompt",
]


# ---------------------------------------------------------------------------
# Health & config
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "shorts_dir": str(SHORTS_DIR),
        "dub_dir": str(DUB_DIR),
        "data_dir": str(config_store.data_dir()),
        "deps": _deps_status(),
        "system": _system_health(),
    }


_LOW_DISK_GB = 5.0  # warn under this much free space on the work volume


def _system_health() -> Dict[str, Any]:
    """Startup-grade environment check surfaced in the UI: core tools, free space on
    the WORK volume (data lives there — often an external disk reached via symlinks),
    and whether that disk is actually mounted. A missing external disk is the #1 cause
    of 'my projects vanished' — better a clear warning than silent empty lists."""
    warnings: List[str] = []
    info: Dict[str, Any] = {
        "ffmpeg": _ffmpeg_ok(),
        "python": True,
        "python_version": platform.python_version(),
    }
    if not info["ffmpeg"]:
        warnings.append("Brak FFmpeg — renderowanie i konwersja audio/wideo nie zadziałają.")

    # Resolve the real on-disk location of the Shorts data (symlinks → work disk).
    try:
        sessions = (SHORTS_DIR / "workspace" / "sessions")
        real = sessions.resolve()
        info["data_path"] = str(real)
        # On macOS an external/removable volume lives under /Volumes/<name>.
        on_external = str(real).startswith("/Volumes/")
        info["external_disk"] = on_external
        link = SHORTS_DIR / "workspace" / "sessions"
        # A symlink whose target is gone = unplugged work disk.
        dangling = link.is_symlink() and not link.exists()
        info["data_disk_mounted"] = (not dangling) and real.exists()
        if on_external:
            # /Volumes/<name>/... → the mount root is the first two path parts.
            parts = real.parts
            info["disk_name"] = parts[2] if len(parts) > 2 else str(real)
        if not info["data_disk_mounted"]:
            warnings.append(
                "Dysk roboczy z danymi nie jest podłączony — projekty, pobrane filmy i wersje "
                "nie będą widoczne, dopóki go nie podłączysz. (Dane NIE są utracone.)"
            )
    except Exception as e:  # noqa: BLE001
        info["data_path"] = ""
        warnings.append(f"Nie można sprawdzić dysku roboczego: {e}")

    # Free space on the volume that actually holds the data.
    try:
        check_path = Path(info.get("data_path") or "") if info.get("data_disk_mounted") else config_store.data_dir()
        if not check_path.exists():
            check_path = config_store.data_dir()
        usage = shutil.disk_usage(str(check_path))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        info["disk_free_gb"] = round(free_gb, 1)
        info["disk_total_gb"] = round(total_gb, 1)
        info["disk_free_pct"] = round(100.0 * usage.free / max(1, usage.total), 1)
        info["low_disk"] = free_gb < _LOW_DISK_GB
        if info["low_disk"]:
            warnings.append(
                f"Mało miejsca na dysku roboczym: wolne {free_gb:.1f} GB. Renderowanie wideo może się nie udać."
            )
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Nie można odczytać wolnego miejsca: {e}")

    info["warnings"] = warnings
    info["ok"] = len(warnings) == 0
    return info


def _deps_status() -> Dict[str, bool]:
    import importlib.util as iu

    def has(mod: str) -> bool:
        try:
            return iu.find_spec(mod) is not None
        except Exception:
            return False

    return {
        # The backend itself runs on Python, so if these deps are being reported it's present.
        "python": True,
        "torch": has("torch"),
        "torchaudio": has("torchaudio"),
        "torchcodec": has("torchcodec"),
        "whisper": has("faster_whisper") or has("mlx_whisper"),
        "gemini": has("google.genai") or has("google.generativeai"),
        "yt_dlp": has("yt_dlp"),
        "opencv": has("cv2"),
        "ultralytics": has("ultralytics"),
        "pillow": has("PIL"),
        "demucs": has("demucs"),
        "transformers": has("transformers"),
        "accelerate": has("accelerate"),
        "sentencepiece": has("sentencepiece"),
        "soundfile": has("soundfile"),
        "qwen_tts": has("qwen_tts"),
        # OmniVoice runs in its own system-installed venv (not the app's Python), so
        # probe that venv rather than the in-process import table.
        "omnivoice": omnivoice_engine.is_ready(),
        "ffmpeg": _ffmpeg_ok(),
        # Local translation engines (replace the paid Gemini dependency).
        "nllb": has("torch") and has("transformers"),
        "argos": has("argostranslate"),
        # Music Generator (ACE-Step) — engine is installed once in the system, not bundled.
        "uv": music_pipeline.uv_available(),
        "ace_step": music_pipeline.ace_dir(config_store.load().get("app")) is not None,
        # Image (FLUX/MFLUX) + Video (LTX) generators — system-installed VideoGenerator.
        "videogen": videogen_pipeline.videogen_dir(config_store.load().get("app")) is not None,
    }


def _norm_lang(name: str) -> str:
    """Accent/case-insensitive language-name key, so 'Angielski' == 'angielski'."""
    s = (name or "").strip().lower()
    return s.translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _translation_engine(full: Dict[str, Any]) -> str:
    """The user's preferred shorts translation engine (nllb | argos | gemini)."""
    return str((full.get("app", {}) or {}).get("translation_engine", "nllb")).lower()


# ISO 639-1 -> Polish display name, for labelling the original (untranslated) short.
_ISO_TO_PL = {
    "en": "Angielski", "pl": "Polski", "de": "Niemiecki", "fr": "Francuski",
    "es": "Hiszpański", "it": "Włoski", "pt": "Portugalski", "ru": "Rosyjski",
    "ar": "Arabski", "ja": "Japoński", "ko": "Koreański", "zh": "Chiński",
}


def _source_language_label(transcript: str, whisper_lang: str) -> str:
    """Best-effort source-language display name for a short.

    Prefers the explicit Whisper language the user picked; otherwise auto-detects
    from the transcript so the UI shows e.g. "Polski" instead of "Brak (Oryginał)".
    """
    wl = (whisper_lang or "").strip()
    if wl and wl not in ("Auto-detekcja", "Automatyczne wykrywanie"):
        return wl
    try:
        import local_translate  # type: ignore
        iso, _ = local_translate._detect_source(transcript or "")
        return _ISO_TO_PL.get(iso, "Polski")
    except Exception:
        return "Polski"


def _ffmpeg_ok() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


def _gpu_percent() -> Optional[int]:
    """Best-effort Apple Silicon GPU utilisation (no sudo) via IOAccelerator stats."""
    try:
        out = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=2,
        ).stdout
        for pattern in (r'"Device Utilization %"=(\d+)', r'"GPU Activity\(%\)"=(\d+)'):
            m = re.search(pattern, out)
            if m:
                return max(0, min(100, int(m.group(1))))
    except Exception:
        pass
    return None


@app.get("/api/system/stats")
def system_stats() -> Dict[str, Any]:
    """Live CPU / RAM / GPU usage for the top-bar meters."""
    stats: Dict[str, Any] = {"cpu": None, "ram": None, "ram_used_gb": None, "ram_total_gb": None, "gpu": None}
    try:
        import psutil  # type: ignore
        stats["cpu"] = round(psutil.cpu_percent(interval=None))
        vm = psutil.virtual_memory()
        stats["ram"] = round(vm.percent)
        stats["ram_used_gb"] = round(vm.used / 1e9, 1)
        stats["ram_total_gb"] = round(vm.total / 1e9, 1)
    except Exception:
        pass
    stats["gpu"] = _gpu_percent()
    return stats


def _dir_stats(path: Path, exts: Optional[set] = None) -> Dict[str, int]:
    total = 0
    count = 0
    if path.exists():
        for p in path.rglob("*"):
            if not p.is_file() or _is_junk_name(p.name):
                continue
            if exts is not None and p.suffix.lower() not in exts:
                continue
            try:
                total += p.stat().st_size
            except Exception:
                continue
            if exts is None and p.suffix.lower() in {".json", ".jsonl"}:
                continue  # sidecars don't count toward the visible asset tally
            count += 1
    return {"bytes": total, "count": count}


def _storage_categories() -> List[Dict[str, Any]]:
    """One entry per module work-folder under the work root. Each is a self-contained,
    deletable folder — clearing it is just emptying that folder."""
    _ensure_shorts_links()  # make sure Shorts data is in the work folder before we measure
    root = config_store.work_root()
    cats: List[Dict[str, Any]] = []
    for key, name in config_store.WORK_CATEGORIES:
        d = root / name
        cats.append({"key": key, "label": name, "path": str(d.resolve()), "clearable": True, **_dir_stats(d)})
    return cats


@app.get("/api/storage/usage")
def storage_usage() -> Dict[str, Any]:
    cats = _storage_categories()
    return {
        "data_dir": str(config_store.work_root().resolve()),
        "config_dir": str(config_store.data_dir().resolve()),
        "categories": cats,
        "total_bytes": sum(c["bytes"] for c in cats),
    }


class StorageCleanupRequest(BaseModel):
    target: str                       # category key from WORK_CATEGORIES


@app.post("/api/storage/cleanup")
def storage_cleanup(req: StorageCleanupRequest) -> Dict[str, Any]:
    names = dict(config_store.WORK_CATEGORIES)
    if req.target not in names:
        raise HTTPException(400, "Nieznana kategoria.")
    folder = (config_store.work_root() / names[req.target]).resolve()
    work_root = config_store.work_root().resolve()
    if not _path_within(folder, work_root):
        raise HTTPException(400, "Niedozwolona ścieżka.")
    freed = _du_bytes(folder) if folder.exists() else 0
    # Empty the category folder (the app recreates it on demand) — for Shorts this clears
    # the symlink targets; _sessions_dir() re-creates them on next use.
    if folder.exists():
        for child in folder.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except Exception:
                continue
    folder.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "freed_bytes": freed, "removed": 0}


def _du_bytes(path: Path) -> int:
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=30).stdout
        return int(out.split()[0]) * 1024
    except Exception:
        return 0


# Friendly labels + module/required hints for the models cached under the Hugging
# Face hub. Matched (case-insensitive) as substrings against the repo id derived
# from each `models--owner--name` folder. First match wins; unknown repos still
# show with their raw id so nothing is hidden.
_HF_MODEL_RULES = [
    ("qwen3-tts",        "Qwen TTS (Tekst→Audio)",                 True),
    ("omnivoice",        "OmniVoice (głosy Tekst→Audio)",          False),
    ("faster-whisper",   "Whisper (transkrypcja — dubbing/napisy)", True),
    ("whisper",          "Whisper MLX (transkrypcja — dubbing/napisy)", True),
    ("nllb",             "NLLB (tłumaczenie — dubbing)",           True),
    ("wav2vec2",         "wav2vec2 (dopasowanie napisów)",         False),
    ("flux",             "FLUX (Obraz)",                           False),
    ("gemma",            "Gemma (ulepszanie promptów — obraz/wideo)", False),
    ("ace-step",         "ACE-Step (muzyka)",                      True),
]


def _hf_repo_from_dir(name: str) -> str:
    """`models--Qwen--Qwen3-TTS-12Hz-1.7B-Base` → `Qwen/Qwen3-TTS-12Hz-1.7B-Base`."""
    rest = name[len("models--"):] if name.startswith("models--") else name
    owner, _, model = rest.partition("--")
    return f"{owner}/{model}" if model else owner


def _hf_hub_entries(home: Path) -> List[Dict[str, Any]]:
    hub = home / ".cache" / "huggingface" / "hub"
    if not hub.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for child in sorted(hub.iterdir()):
        if not child.is_dir() or not child.name.startswith("models--"):
            continue
        repo = _hf_repo_from_dir(child.name)
        low = repo.lower()
        label, required = None, False
        for needle, friendly, req in _HF_MODEL_RULES:
            if needle in low:
                label, required = friendly, req
                break
        if label is None:
            label = repo  # unknown model — surface it raw rather than hiding it
        entries.append({
            "key": f"hf:{child.name}",
            "label": label,
            "sublabel": repo,
            "path": str(child.resolve()),
            "deletable": True,
            "required": required,
        })
    return entries


def _model_entries() -> List[Dict[str, Any]]:
    app_cfg = config_store.load().get("app")
    home = Path.home()
    ace = music_pipeline.ace_dir(app_cfg)
    vg = videogen_pipeline.videogen_dir(app_cfg)
    # Everything is deletable — every model re-downloads on next use, so the user can
    # free any of it. "wymagany" is shown as info only (it'll be re-fetched when needed).
    raw: List[Dict[str, Any]] = []
    if ace:
        raw.append({"key": "ace", "label": "ACE-Step (muzyka) — checkpointy", "path": str((ace / "checkpoints").resolve()), "deletable": True, "required": True})
    if vg:
        raw.append({"key": "ltx_q4", "label": "LTX 2.3 Q4 (wideo) — zalecany", "path": str((vg / "models" / "ltx-2.3-mlx-q4").resolve()), "deletable": True, "required": True})
        raw.append({"key": "ltx_q8", "label": "LTX 2.3 Q8 (wideo) — opcjonalny", "path": str((vg / "models" / "ltx-2.3-mlx-q8").resolve()), "deletable": True})
    # Itemise the Hugging Face hub cache so every model the modules use — Qwen TTS,
    # Whisper, NLLB, OmniVoice, FLUX, Gemma, … — is listed individually instead of as
    # one opaque blob.
    raw.extend(_hf_hub_entries(home))
    # Speaker separation / alignment weights live in the torch hub cache (demucs +
    # wav2vec2), used by dubbing.
    raw.append({"key": "torch_cache", "label": "Demucs / wav2vec2 (separacja głosu — dubbing)", "path": str((home / ".cache" / "torch").resolve()), "deletable": True, "required": True})
    raw.append({"key": "acestep_cache", "label": "ACE-Step cache (do pobrania ponownie)", "path": str((home / ".cache" / "music-generator").resolve()), "deletable": True})
    out: List[Dict[str, Any]] = []
    for e in raw:
        p = Path(e["path"])
        if not p.exists():
            continue
        e["bytes"] = _du_bytes(p)
        out.append(e)
    return out


@app.get("/api/models/list")
def models_list() -> Dict[str, Any]:
    entries = _model_entries()
    return {"models": entries, "total_bytes": sum(e["bytes"] for e in entries)}


class ModelDeleteRequest(BaseModel):
    path: str


@app.post("/api/models/delete")
def models_delete(req: ModelDeleteRequest) -> Dict[str, Any]:
    allowed = {e["path"] for e in _model_entries() if e.get("deletable")}
    target = str(Path(req.path).resolve())
    if target not in allowed:
        raise HTTPException(400, "Tej pozycji nie można usunąć z poziomu aplikacji.")
    p = Path(target)
    if not p.exists():
        return {"ok": True, "freed_bytes": 0}
    freed = _du_bytes(p)
    shutil.rmtree(p, ignore_errors=True)
    return {"ok": True, "freed_bytes": freed}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return config_store.load()


class ConfigPatch(BaseModel):
    dub: Optional[Dict[str, Any]] = None
    shorts: Optional[Dict[str, Any]] = None
    music: Optional[Dict[str, Any]] = None
    image: Optional[Dict[str, Any]] = None
    video: Optional[Dict[str, Any]] = None
    app: Optional[Dict[str, Any]] = None


@app.post("/api/config")
def post_config(patch: ConfigPatch) -> Dict[str, Any]:
    return config_store.save({k: v for k, v in patch.model_dump().items() if v is not None})


class ShortsPreviewRequest(BaseModel):
    settings: Dict[str, Any]


@app.post("/api/shorts/preview")
def shorts_preview(req: ShortsPreviewRequest) -> Dict[str, str]:
    """Render a small loop preview with the real ShortsGenerator ASS subtitle engine."""
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")
    settings = req.settings or {}
    preview_identity = {"renderer": "shorts-preview-v2", "settings": settings}
    key = hashlib.sha1(json.dumps(preview_identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    out_dir = config_store.module_dir("cache") / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = out_dir / f"shorts-preview-{key}.mp4"
    if out_mp4.exists():
        return {"url": f"/api/shorts/preview/{out_mp4.name}"}

    prev_cwd = os.getcwd()
    ass_path = out_dir / f"shorts-preview-{key}.ass"
    try:
        os.chdir(str(SHORTS_DIR))
        from logo_handler import build_ffmpeg_filters  # type: ignore
        from subtitle_engine import generate_viral_ass_subtitles  # type: ignore
        from utils import get_ffmpeg_path  # type: ignore

        aspect_ratio = "16:9" if "16:9" in str(settings.get("aspect_ratio", "")) else "9:16"
        res = _preview_resolution(settings.get("export_resolution"), aspect_ratio)
        width = int(res.split("x", 1)[0])
        # 10 words so the full range of the "słowa w bloku" setting (max 10) can be previewed.
        words_text = ["TWÓJ", "VIRALOWY", "TESTOWY", "TEKST", "POKAZUJE",
                      "DOKŁADNIE", "WYGLĄD", "NAPISÓW", "W", "PODGLĄDZIE"]
        if not settings.get("sub_upper", True):
            words_text = [w.capitalize() for w in words_text]
        total = 5.6
        step = total / len(words_text)
        global_words = [
            {"word": word, "start": i * step, "end": (i + 1) * step}
            for i, word in enumerate(words_text)
        ]
        segments = [{"start_time": 0.0, "end_time": total, "text": " ".join(words_text)}]

        filters: List[str] = []
        current_v = "[0:v]"
        if settings.get("enable_subtitles", True):
            _build_short_ass(settings, segments, global_words, str(ass_path), aspect_ratio)
            font_dir = str(SHORTS_DIR / "fonts").replace("\\", "/").replace(":", "\\:")
            filters.append(f"{current_v}ass='{_ffmpeg_escape(str(ass_path))}':fontsdir='{font_dir}'[with_subs]")
            current_v = "[with_subs]"

        logo_path = _resolve_logo_path(settings.get("logo_path"))
        logo_idx = -1
        cmd_inputs = [
            get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0x111119:s={res}:d={total}:r=30",
        ]
        if settings.get("enable_logo") and logo_path and logo_path.exists():
            cmd_inputs.extend(["-loop", "1", "-framerate", "30", "-i", str(logo_path)])
            logo_idx = 1

        logo_settings = _preview_logo_settings(settings, logo_path)
        extra_filters, current_v = build_ffmpeg_filters(current_v, logo_settings, logo_idx, width)
        if extra_filters:
            filters.append(extra_filters)

        cmd = cmd_inputs
        if filters:
            cmd.extend(["-filter_complex", "; ".join(filters), "-map", current_v])
        else:
            cmd.extend(["-map", "0:v"])
        cmd.extend([
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out_mp4),
        ])
        subprocess.run(cmd, check=True, timeout=20)
        return {"url": f"/api/shorts/preview/{out_mp4.name}"}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Podgląd renderował się zbyt długo")
    except Exception as exc:
        raise HTTPException(500, f"Nie udało się wyrenderować podglądu: {exc}")
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass


@app.get("/api/shorts/preview/{filename}")
def get_shorts_preview(filename: str):
    safe = os.path.basename(filename)
    path = config_store.module_dir("cache") / "previews" / safe
    if not path.exists() or path.suffix.lower() != ".mp4":
        raise HTTPException(404, "Brak podglądu")
    return FileResponse(str(path), media_type="video/mp4", headers={"Cache-Control": "max-age=86400"})


def _build_short_ass(settings: Dict[str, Any], segments: List[Dict[str, Any]],
                     words: List[Dict[str, Any]], ass_path: str, aspect_ratio: str):
    """Generate the ASS subtitle file from our sub_* settings — single source of truth
    shared by the live preview and the full render so they look identical."""
    from subtitle_engine import generate_viral_ass_subtitles  # type: ignore
    return generate_viral_ass_subtitles(
        segments,
        words,
        ass_path,
        preset_name=settings.get("sub_preset", "MrBeast Clean Hook"),
        custom_font=settings.get("custom_font"),
        aspect_ratio=aspect_ratio,
        override_bcolor=settings.get("sub_bcolor"),
        override_hcolor=settings.get("sub_hcolor"),
        override_size=_num(settings.get("sub_size")),
        override_margin=_num(settings.get("sub_margin")),
        auto_scale=bool(settings.get("sub_autoscale")),
        override_hsize=_num(settings.get("sub_hsize")),
        override_out_color=settings.get("sub_out_color"),
        override_out_thick=_num(settings.get("sub_out_thick")),
        override_shad_color=settings.get("sub_shad_color"),
        override_shad_size=_num(settings.get("sub_shad_size")),
        override_bold=bool(settings.get("sub_bold")),
        override_italic=bool(settings.get("sub_italic")),
        override_upper=bool(settings.get("sub_upper")),
        override_words=int(_num(settings.get("sub_words"), 3)),
        override_mode=settings.get("sub_mode"),
        override_punct=bool(settings.get("sub_punct")),
        override_animation=_animation_code(settings.get("sub_animation")),
        override_bg_padding=_num(settings.get("sub_bg_pad")),
    )


def _num(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _animation_code(value: Any) -> str:
    mapping = {
        "Brak": "none",
        "Wyskakiwanie (Spring Pop)": "spring",
        "Płynne Karaoke": "karaoke",
        "Trzęsienie (Jiggle)": "jiggle",
        "Wyłanianie (Blur Reveal)": "blur_reveal",
        "Nalot (Zoom In)": "zoom_in",
        "Pulsowanie (Color Pulse)": "color_pulse",
        "Wjazd 3D (Slide Up)": "slide_up",
    }
    return mapping.get(str(value), str(value or "none"))


def _preview_resolution(resolution: Any, aspect_ratio: str) -> str:
    text = str(resolution or "1080p")
    short = 2160 if "4K" in text or "2160" in text else (
        1440 if "2K" in text or "1440" in text else (
            720 if "720" in text else (
                480 if "480" in text else 1080
            )
        )
    )
    return f"{short}x{short * 16 // 9}" if aspect_ratio == "9:16" else f"{short * 16 // 9}x{short}"


def _ffmpeg_escape(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _resolve_logo_path(value: Any) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    safe = raw.replace("\\", "/").lstrip("/")
    if safe.startswith("logo/"):
        return SHORTS_DIR / safe
    if safe.startswith("workspace/"):
        return SHORTS_DIR / safe
    return SHORTS_DIR / "logo" / os.path.basename(safe)


def _preview_logo_settings(settings: Dict[str, Any], logo_path: Optional[Path]) -> Dict[str, Any]:
    out = dict(settings)
    if logo_path:
        out["logo_path"] = str(logo_path)
    out["logo_scale"] = _num(settings.get("logo_scale"), 45)
    out["logo_x"] = _num(settings.get("logo_x"), 2)
    out["logo_y"] = _num(settings.get("logo_y"), 4)
    out["logo_opacity"] = _num(settings.get("logo_opacity"), 100)
    out["wm_size"] = _num(settings.get("wm_size"), 65)
    out["wm_opacity"] = _num(settings.get("wm_opacity"), 100)
    out["wm_x"] = _num(settings.get("wm_x"), 50)
    out["wm_y"] = _num(settings.get("wm_y"), 10)
    out["wm_out_thick"] = _num(settings.get("wm_out_thick"), 0)
    out["wm_shad_size"] = _num(settings.get("wm_shad_size"), 0)
    return out


@app.get("/api/fonts/{filename}")
def get_font(filename: str):
    """Serve a font file so the UI can render real font previews."""
    # Guard against path traversal — only a bare filename from the fonts dir.
    safe = os.path.basename(filename)
    path = SHORTS_DIR / "fonts" / safe
    if not path.exists() or path.suffix.lower() not in (".ttf", ".otf"):
        raise HTTPException(404, "Brak czcionki")
    media = "font/ttf" if path.suffix.lower() == ".ttf" else "font/otf"
    return FileResponse(str(path), media_type=media, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/logos/{filename}")
def get_logo(filename: str):
    """Serve a known logo image from ShortsGenerator's logo/workspace folders."""
    safe = os.path.basename(filename)
    for folder in (SHORTS_DIR / "logo", SHORTS_DIR / "workspace"):
        path = folder / safe
        if path.exists() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            return FileResponse(str(path), headers={"Cache-Control": "max-age=86400"})
    raise HTTPException(404, "Brak logo")


@app.get("/api/meta")
def get_meta() -> Dict[str, Any]:
    sm = shorts_meta()
    app_cfg = config_store.load().get("app", {})
    tts_engine = str(app_cfg.get("tts_engine", "qwen")).lower()
    # Dub/Text→Audio targets adapt to the chosen voice + translation engines.
    dub_langs = _dub_target_languages(app_cfg)
    return {
        "shorts": {
            "presets": list(sm["presets"].keys()),
            "preset_data": sm["presets"],
            "animations": sm["animations"] or ["Brak"],
            "fonts": sm["fonts"],
            "logos": sm["logos"],
            "languages": sm["languages"] or SHORTS_WHISPER_LANGS,
            "aspect_ratios": SHORTS_ASPECT,
            "export_resolutions": SHORTS_EXPORT_RES,
            "proxy_resolutions": SHORTS_PROXY_RES,
            "yt_qualities": SHORTS_YT_QUALITIES,
            "codecs": SHORTS_CODECS,
            "sub_modes": SHORTS_SUB_MODES,
            "ft_strategies": SHORTS_FT_STRATEGIES,
            "ft_trackers": SHORTS_FT_TRACKERS,
            "prompt_modes": SHORTS_PROMPT_MODES,
        },
        "dub": {
            "source_languages": DUB_SOURCE_LANGUAGES,
            "target_languages": dub_langs,
            "languages": dub_langs,
            "dub_target_languages": dub_langs,
            "translate_target_languages": TRANSLATE_TARGET_LANGUAGES,
            "tts_engines": TTS_ENGINES,
            "tts_engine": tts_engine,
            "speakers": QWEN_SPEAKERS,
            "tts_models": TTS_MODELS,
            "translation_models": TRANSLATION_MODELS,
            "translation_engines": TRANSLATION_ENGINES,
            "voice_sources": DUB_VOICE_SOURCES,
            "voice_store_modes": DUB_VOICE_STORE_MODES,
            "mix_modes": DUB_MIX_MODES,
            "voiceover_engines": DUB_VOICEOVER_ENGINES,
            "clone_modes": DUB_CLONE_MODES,
            "output_resolutions": DUB_OUTPUT_RES,
            "yt_qualities": DUB_YT_QUALITIES,
        },
        "music": {
            "models": music_pipeline.MODEL_OPTIONS,
            "default_model": music_pipeline.DEFAULT_MODEL,
            "problematic_models": music_pipeline.PROBLEMATIC_MODELS,
            "formats": music_pipeline.FORMAT_OPTIONS,
            "variants": music_pipeline.VARIANT_OPTIONS,
            "languages": music_pipeline.LANGUAGE_OPTIONS,
            "language_labels": music_pipeline.LANGUAGE_LABELS,
            "bpm_options": music_pipeline.BPM_OPTIONS,
            "bpm_labels": music_pipeline.BPM_LABELS,
            "key_scale_options": music_pipeline.KEY_SCALE_OPTIONS,
            "key_scale_labels": music_pipeline.KEY_SCALE_LABELS,
            "time_signature_options": music_pipeline.TIME_SIGNATURE_OPTIONS,
            "time_signature_labels": music_pipeline.TIME_SIGNATURE_LABELS,
            "vocal_types": music_pipeline.VOCAL_TYPE_OPTIONS,
            "vocal_type_labels": music_pipeline.VOCAL_TYPE_LABELS,
        },
        "image": {
            "models": videogen_pipeline.image_model_options(),
            "resolutions": videogen_pipeline.image_resolution_options(),
            "styles": videogen_pipeline.image_style_options(),
        },
        "video": {
            "models": videogen_pipeline.ltx_model_options(
                videogen_pipeline.videogen_dir(config_store.load().get("app")) or Path("/")
            ),
            "resolutions": videogen_pipeline.video_resolution_options(),
            "durations": videogen_pipeline.duration_options(),
            "fps": videogen_pipeline.fps_options(),
        },
        "voices": _list_voice_samples(),
    }


def _voice_labels_path() -> Path:
    return config_store.data_dir() / "voice_labels.json"


def _load_voice_labels() -> Dict[str, str]:
    try:
        return json.loads(_voice_labels_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_voice_labels(data: Dict[str, str]) -> None:
    try:
        _voice_labels_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _list_voice_samples() -> List[Dict[str, Any]]:
    overrides = _load_voice_labels()

    def clean_label(name: str) -> str:
        return re.sub(r"[_-]+", " ", name).strip() or name

    def add(path: Path, label: str, sample_id: str):
        if path.exists() and path.is_file() and path.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".mp4", ".mov", ".mkv"}:
            resolved = str(path.resolve())
            # A user-set custom name (rename) always wins over the derived label.
            label = overrides.get(resolved, label)
            out.append({"id": sample_id, "label": label, "path": resolved})

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    shorts_vs = SHORTS_DIR / "workspace" / "voice_samples"
    shorts_vs.mkdir(parents=True, exist_ok=True)

    # Copy the named voice library from the old DubMaster into the Shorts workspace
    # so the new ViralCutter can use the same samples as the original generator.
    voices_json = DUB_DIR / "voice_samples" / "voices.json"
    labels: Dict[str, str] = {}
    if voices_json.exists():
        try:
            for item in json.loads(voices_json.read_text(encoding="utf-8")).get("voices", []):
                sid = str(item.get("id") or "").strip()
                labels[sid] = str(item.get("name") or sid).strip()
                src_rel = str(item.get("path") or "")
                src = (DUB_DIR / src_rel).resolve()
                if sid and src.exists():
                    target = shorts_vs / f"{labels[sid]} - {sid}{src.suffix.lower() or '.wav'}"
                    if not target.exists():
                        shutil.copy2(src, target)
        except Exception:
            pass

    for p in sorted(shorts_vs.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        add(p, clean_label(p.stem), f"shorts:{p.name}")

    dub_vs = DUB_DIR / "voice_samples"
    if dub_vs.exists():
        for d in sorted(dub_vs.iterdir(), key=lambda x: x.name.lower()):
            if not d.is_dir():
                continue
            wav = d / "voice.wav"
            key = str(wav.resolve())
            if key in seen:
                continue
            seen.add(key)
            add(wav, labels.get(d.name, d.name), f"dub:{d.name}")
    return out


@app.get("/api/shorts/voice-samples")
def shorts_voice_samples() -> List[Dict[str, Any]]:
    return _list_voice_samples()


class VoiceSampleRenameRequest(BaseModel):
    path: str
    label: str


class VoiceSamplePathRequest(BaseModel):
    path: str


def _voice_sample_roots() -> List[Path]:
    return [
        (SHORTS_DIR / "workspace" / "voice_samples").resolve(),
        (DUB_DIR / "voice_samples").resolve(),
    ]


def _validate_voice_path(path: str) -> Path:
    """Resolve + sandbox a voice-sample path to the known sample folders."""
    p = Path(path).resolve()
    if not any(str(p).startswith(str(root) + os.sep) for root in _voice_sample_roots()):
        raise HTTPException(400, "Ścieżka próbki spoza dozwolonych katalogów.")
    return p


def _remove_dub_library_entry(sid: str) -> None:
    """Drop a voice from the DubMaster voices.json + its folder so it doesn't get
    re-copied into the Shorts workspace on the next listing (no resurrection)."""
    if not sid:
        return
    voices_json = DUB_DIR / "voice_samples" / "voices.json"
    if voices_json.exists():
        try:
            data = json.loads(voices_json.read_text(encoding="utf-8"))
            before = data.get("voices", [])
            data["voices"] = [v for v in before if str(v.get("id") or "").strip() != sid]
            if len(data["voices"]) != len(before):
                voices_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    dub_dir = (DUB_DIR / "voice_samples" / sid)
    if dub_dir.is_dir():
        shutil.rmtree(dub_dir, ignore_errors=True)


@app.post("/api/shorts/voice-samples/rename")
def rename_voice_sample(req: VoiceSampleRenameRequest) -> Dict[str, Any]:
    p = _validate_voice_path(req.path)
    label = (req.label or "").strip()
    if not label:
        raise HTTPException(400, "Nazwa nie może być pusta.")
    overrides = _load_voice_labels()
    overrides[str(p)] = label
    _save_voice_labels(overrides)
    return {"ok": True, "path": str(p), "label": label}


@app.post("/api/shorts/voice-samples/delete")
def delete_voice_sample(req: VoiceSamplePathRequest) -> Dict[str, Any]:
    p = _validate_voice_path(req.path)
    shorts_vs = (SHORTS_DIR / "workspace" / "voice_samples").resolve()
    dub_vs = (DUB_DIR / "voice_samples").resolve()

    # Case 1: a DubMaster library sample (…/voice_samples/<id>/voice.wav).
    if str(p).startswith(str(dub_vs) + os.sep):
        sid = p.parent.name
        _remove_dub_library_entry(sid)
        # also drop any copy that landed in the Shorts workspace
        for f in shorts_vs.glob(f"* - {sid}.*"):
            try:
                f.unlink()
            except Exception:
                pass
    else:
        # Case 2: a Shorts-workspace file. If it's a copy of a dub-library voice
        # ("<name> - <id>.wav"), purge the source too so it can't be re-copied.
        stem = p.stem
        if " - " in stem:
            sid = stem.rsplit(" - ", 1)[-1].strip()
            if re.fullmatch(r"[0-9a-fA-F]{6,}", sid):
                _remove_dub_library_entry(sid)
        try:
            p.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Nie udało się usunąć pliku: {e}")

    overrides = _load_voice_labels()
    if str(p) in overrides:
        overrides.pop(str(p), None)
        _save_voice_labels(overrides)
    return {"ok": True, "path": str(p)}


_VOICE_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv"}


@app.get("/api/shorts/voice-samples/audio")
def voice_sample_audio(path: str):
    """Stream a saved voice sample so the UI can PREVIEW it (play button). Sandboxed
    to the known sample folders. Shared by Shorts and DubMaster (same library)."""
    p = _validate_voice_path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Brak pliku próbki głosu")
    media = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    }.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(str(p), media_type=media, headers={"Cache-Control": "no-cache"})


class VoiceSampleAddRequest(BaseModel):
    path: str             # absolute path to any audio/video file (from the native picker)
    label: Optional[str] = None


@app.post("/api/shorts/voice-samples/add")
def add_voice_sample(req: VoiceSampleAddRequest) -> Dict[str, Any]:
    """Copy an arbitrary audio/video file into the SHARED voice-sample library so it
    becomes reusable in BOTH Shorts and DubMaster (both list `_list_voice_samples`).
    The source can be anywhere on disk (picked via the native dialog); we copy it into
    the Shorts workspace library and remember an optional custom label."""
    src = Path(req.path).expanduser()
    if not src.exists() or not src.is_file():
        raise HTTPException(400, "Wybrany plik nie istnieje.")
    if src.suffix.lower() not in _VOICE_EXTS:
        raise HTTPException(400, "Nieobsługiwany format próbki głosu.")
    lib = (SHORTS_DIR / "workspace" / "voice_samples")
    lib.mkdir(parents=True, exist_ok=True)
    label = (req.label or src.stem).strip() or src.stem
    # Clean, collision-safe filename inside the library.
    safe_stem = re.sub(r"[^\w\- ]+", "", label).strip() or "glos"
    target = lib / f"{safe_stem}{src.suffix.lower()}"
    n = 2
    while target.exists():
        target = lib / f"{safe_stem} {n}{src.suffix.lower()}"
        n += 1
    try:
        shutil.copy2(src, target)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Nie udało się dodać próbki: {e}")
    if req.label:
        overrides = _load_voice_labels()
        overrides[str(target.resolve())] = label
        _save_voice_labels(overrides)
    return {"ok": True, "path": str(target.resolve()), "label": label, "voices": _list_voice_samples()}


# ---------------------------------------------------------------------------
# Pipelines (jobs) — reuse vendor engines
# ---------------------------------------------------------------------------
class ShortsRequest(BaseModel):
    input_method: str = "Lokalny plik"   # "Lokalny plik" | "Link z YouTube"
    source: str                          # local path or YouTube URL
    settings: Optional[Dict[str, Any]] = None
    force_transcribe: bool = False       # re-run Whisper from scratch, ignore cached transcript


def _purge_transcription_forced_rerun(video_id: str, video_file: str, dl_dir: Path, ctx) -> None:
    """Remove every transcript-derived state for a source before a forced rerun.

    Merely bypassing ``*_transcript.txt`` left old session data in History, which
    could reopen stale word timings after Whisper had produced a clean result.
    The original downloaded/local video itself is deliberately preserved.
    """
    removed_cache = 0
    for pattern in (f"{video_id}*_transcript.txt", f"{video_id}*_words.json"):
        for path in dl_dir.glob(pattern):
            try:
                path.unlink()
                removed_cache += 1
            except OSError:
                pass
    try:
        source_key = str(Path(video_file).resolve())
    except OSError:
        source_key = os.path.abspath(video_file)
    removed_projects = 0
    for project in list(_sessions_dir().iterdir()):
        data_file = project / "data.json"
        if not data_file.exists():
            continue
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
            paths = [str(data.get("video_file") or ""), str(data.get("original_source_path") or "")]
            normalized = []
            for item in paths:
                if item:
                    try:
                        normalized.append(str(Path(item).resolve()))
                    except OSError:
                        normalized.append(os.path.abspath(item))
            same_source = source_key in normalized
            # Legacy sessions may have only a copied/proxy path; their project id
            # still includes the deterministic source id.
            same_source = same_source or project.name.endswith(f"_{video_id}") or f"_{video_id}_" in project.name
            if not same_source:
                continue
            shutil.rmtree(project, ignore_errors=True)
            removed_projects += 1
        except Exception:
            continue
    ctx.log(f"Wyczyszczono poprzednią transkrypcję: {removed_cache} plik(ów) cache i {removed_projects} projekt(ów).", "info")


@app.post("/api/shorts/analyze")
def shorts_analyze(req: ShortsRequest) -> Dict[str, Any]:
    full = config_store.load()
    s = {**full["shorts"], **(req.settings or {})}
    if not s.get("api_key"):
        s["api_key"] = full["app"].get("gemini_api_key", "")
    if not (s.get("whisper_glossary") or "").strip():
        s["whisper_glossary"] = full["app"].get("glossary", "")

    # Fail fast: AI moment-selection needs Gemini, so reject before the slow
    # download + Whisper steps instead of erroring out minutes later.
    if not (s.get("api_key") or "").strip():
        raise HTTPException(
            400,
            "Brak klucza API Gemini. Wpisz go w Ustawieniach → Klucze API — "
            "bez niego AI nie wybierze viralowych momentów.",
        )

    def target(ctx) -> Any:
        _ensure_shorts_links()
        os.chdir(str(SHORTS_DIR))
        ctx.step("Inicjalizacja silnika ViralCutter…")
        # Lazy import — only now do heavy deps load. Mirrors pipeline.process_video_pipeline.
        from downloader import (  # type: ignore
            download_video, get_video_title, get_yt_id, download_yt_subtitles,
        )
        from ai_processor import (  # type: ignore
            load_whisper, transcribe_video, analyze_with_gemini,
            optimize_segments, initialize_short_words, parse_vtt_to_transcript,
            apply_whisper_glossary_to_transcript, apply_whisper_glossary_to_words,
        )
        from config import LANG_MAP  # type: ignore

        api_key = (s.get("api_key") or "").strip()
        glossary = (s.get("whisper_glossary") or "").strip()
        is_yt = req.input_method == "Link z YouTube"
        lang_code = LANG_MAP.get(s.get("whisper_lang", "Auto-detekcja"))

        # Validate the key BEFORE the slow download/transcription so a bad key fails
        # in a second with a clear message — not as a misleading "corrupted format"
        # error after Whisper + 3 wasted Gemini retries.
        ctx.progress(0.02, "Sprawdzanie klucza API Gemini…")
        _ensure_gemini_key_valid(api_key)

        quality = s.get("yt_quality", "1080p")
        video_id = get_yt_id(req.source) if is_yt else os.path.splitext(os.path.basename(req.source))[0]

        dl_dir = SHORTS_DIR / "workspace" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)

        # Per-video meta sidecar: lets a re-run reuse the cached title (no network
        # call) and powers the "downloaded videos" quick-pick list.
        meta_file = dl_dir / f"{video_id}_meta.json"
        cached_meta: Dict[str, Any] = {}
        if meta_file.exists():
            try:
                cached_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                cached_meta = {}

        already = is_yt and bool(_cached_video_for(dl_dir, video_id))
        ctx.progress(0.05, "Wczytywanie pobranego wideo…" if already else "Pobieranie / przygotowanie wideo…")
        # download_video() returns the cached file instantly when it already exists,
        # so an already-downloaded video is never fetched again.
        video_file = download_video(req.source, quality) if is_yt else req.source
        if is_yt:
            title = cached_meta.get("title") or get_video_title(req.source) or video_id
        else:
            title = os.path.basename(req.source)

        # Remember this download so it appears in the quick-pick list and can be
        # re-run later with zero network calls (download + transcript both cached).
        if is_yt:
            try:
                meta_file.write_text(json.dumps({
                    "video_id": video_id, "title": title, "url": req.source,
                    "quality": quality, "file": os.path.abspath(video_file),
                    "updated": _time.time(),
                }, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        # Transcription cache (per video + language) — so a retry (e.g. after fixing
        # the key) skips Whisper entirely instead of re-downloading + re-transcribing.
        lang_suffix = f"_{lang_code}" if lang_code else ""
        transcript_file = dl_dir / f"{video_id}{lang_suffix}_transcript.txt"
        words_file = dl_dir / f"{video_id}{lang_suffix}_words.json"

        if req.force_transcribe:
            _purge_transcription_forced_rerun(video_id, str(video_file), dl_dir, ctx)

        if not req.force_transcribe and transcript_file.exists() and words_file.exists():
            ctx.progress(0.25, "Wczytano transkrypcję z pamięci podręcznej…")
            transcript = transcript_file.read_text(encoding="utf-8")
            words = json.loads(words_file.read_text(encoding="utf-8"))
        else:
            if req.force_transcribe:
                ctx.log("Wymuszono ponowną transkrypcję — pomijam pamięć podręczną.", "info")
            transcript, words = "", []
            if is_yt and s.get("use_yt_subs") and not req.force_transcribe:
                ctx.progress(0.15, "Pobieranie gotowych napisów z YouTube…")
                vtt = download_yt_subtitles(req.source, video_id, lang_code)
                if vtt:
                    transcript, words = parse_vtt_to_transcript(vtt)
            if not transcript.strip() or not words:
                ctx.progress(0.25, "Transkrypcja (Whisper)…")
                try:
                    transcript, words = transcribe_video(video_file, load_whisper(), lang_code=lang_code)
                except Exception as werr:  # noqa: BLE001
                    ctx.log(f"Główny silnik Whisper zawiódł ({werr}); próbuję faster-whisper…", "warning")
                    transcript, words = _faster_whisper_fallback(video_file, lang_code)
            if transcript.strip():
                transcript_file.write_text(transcript, encoding="utf-8")
                words_file.write_text(json.dumps(words), encoding="utf-8")

        # Whisper glossary post-processing — fix proper-name spellings in transcript/words.
        if glossary:
            transcript = apply_whisper_glossary_to_transcript(transcript, glossary)
            words = apply_whisper_glossary_to_words(words, glossary)

        ctx.progress(0.6, "AI wybiera viralowe momenty…")
        shorts = analyze_with_gemini(
            transcript, api_key, int(s.get("shorts_count", 10)),
            int(s.get("duration_min", 45)), int(s.get("duration_max", 90)),
            s.get("prompt_mode", "Precyzyjna (Domyślna - bardziej restrykcyjna)"),
            s.get("custom_prompt_text", ""), glossary,
        )

        # Apply the glossary to Gemini's own text fields too (titles/hooks/hashtags/segments).
        if glossary:
            for short in shorts:
                for field in ("title", "hook_text", "hashtags", "yt_tags"):
                    if short.get(field):
                        short[field] = apply_whisper_glossary_to_transcript(short[field], glossary)
                for seg in short.get("segments", []):
                    if seg.get("text"):
                        seg["text"] = apply_whisper_glossary_to_transcript(seg["text"], glossary)

        src_label = _source_language_label(transcript, s.get("whisper_lang"))
        for short in shorts:
            short["segments"] = optimize_segments(short.get("segments", []), words)
            initialize_short_words(short, words)
            short.setdefault("source_language", src_label)

        # Persist the project so it shows up in history and can be reloaded later
        # (faithful to the original ShortsGenerator workspace/sessions layout).
        from datetime import datetime
        project_id = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{video_id}"
        session_dir = SHORTS_DIR / "workspace" / "sessions" / project_id
        session_dir.mkdir(parents=True, exist_ok=True)
        abs_video = os.path.abspath(video_file)
        session = {
            "project_id": project_id,
            "display_name": f"{title} ({len(shorts)} shortów)",
            "ai_outputs": shorts,
            "video_file": abs_video,
            "original_source_path": abs_video,
            "global_words": words,
            "render_settings": s,
            "created": _time.time(),
        }
        (session_dir / "data.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")

        ctx.progress(1.0, f"Gotowe — {len(shorts)} propozycji.")
        return {"title": title, "video_file": abs_video, "shorts": shorts, "project_id": project_id}

    job = manager.start("shorts.analyze", target)
    return {"job_id": job.id}


@app.post("/api/shorts/prepare-manual")
def shorts_prepare_manual(req: ShortsRequest) -> Dict[str, Any]:
    """Download/load + transcribe a source (YouTube or local file) WITHOUT the paid Gemini
    moment-selection, then create an EMPTY project the user fills by hand in the scene
    editor (custom shorts). Local files use a lighter proxy working copy when enabled."""
    full = config_store.load()
    s = {**full["shorts"], **(req.settings or {})}
    if not (s.get("whisper_glossary") or "").strip():
        s["whisper_glossary"] = full["app"].get("glossary", "")

    def target(ctx) -> Any:
        _ensure_shorts_links()
        os.chdir(str(SHORTS_DIR))
        ctx.step("Inicjalizacja silnika…")
        from downloader import (  # type: ignore
            download_video, get_video_title, get_yt_id, download_yt_subtitles,
        )
        from ai_processor import (  # type: ignore
            load_whisper, transcribe_video, parse_vtt_to_transcript,
            apply_whisper_glossary_to_transcript, apply_whisper_glossary_to_words,
        )
        from config import LANG_MAP  # type: ignore
        try:
            from video_engine import create_proxy  # type: ignore
        except Exception:
            create_proxy = None

        glossary = (s.get("whisper_glossary") or "").strip()
        is_yt = req.input_method == "Link z YouTube"
        lang_code = LANG_MAP.get(s.get("whisper_lang", "Auto-detekcja"))
        quality = s.get("yt_quality", "1080p")
        video_id = get_yt_id(req.source) if is_yt else os.path.splitext(os.path.basename(req.source))[0]

        dl_dir = SHORTS_DIR / "workspace" / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        meta_file = dl_dir / f"{video_id}_meta.json"
        cached_meta: Dict[str, Any] = {}
        if meta_file.exists():
            try:
                cached_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                cached_meta = {}

        already = is_yt and bool(_cached_video_for(dl_dir, video_id))
        ctx.progress(0.05, "Wczytywanie wideo…" if already else "Pobieranie / przygotowanie wideo…")
        video_file = download_video(req.source, quality) if is_yt else req.source
        title = (cached_meta.get("title") or get_video_title(req.source) or video_id) if is_yt else os.path.basename(req.source)
        if is_yt:
            try:
                meta_file.write_text(json.dumps({
                    "video_id": video_id, "title": title, "url": req.source,
                    "quality": quality, "file": os.path.abspath(video_file), "updated": _time.time(),
                }, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        # Local-file proxy (lighter working copy) when the user enabled it.
        if (not is_yt) and s.get("use_proxy") and create_proxy:
            try:
                ctx.progress(0.12, "Tworzenie proxy (kopii roboczej)…")
                proxy = create_proxy(video_file, proxy_res=s.get("proxy_res", "1080p"), proxy_bitrate=int(s.get("proxy_bitrate", 15)))
                if proxy and os.path.exists(proxy):
                    video_file = proxy
            except Exception as pe:
                ctx.log(f"Proxy nieudane ({pe}); używam oryginału.", "warning")

        lang_suffix = f"_{lang_code}" if lang_code else ""
        transcript_file = dl_dir / f"{video_id}{lang_suffix}_transcript.txt"
        words_file = dl_dir / f"{video_id}{lang_suffix}_words.json"
        if req.force_transcribe:
            _purge_transcription_forced_rerun(video_id, str(video_file), dl_dir, ctx)
        if not req.force_transcribe and transcript_file.exists() and words_file.exists():
            ctx.progress(0.3, "Wczytano transkrypcję z pamięci podręcznej…")
            transcript = transcript_file.read_text(encoding="utf-8")
            words = json.loads(words_file.read_text(encoding="utf-8"))
        else:
            if req.force_transcribe:
                ctx.log("Wymuszono ponowną transkrypcję — pomijam pamięć podręczną.", "info")
            transcript, words = "", []
            if is_yt and s.get("use_yt_subs") and not req.force_transcribe:
                ctx.progress(0.2, "Pobieranie gotowych napisów z YouTube…")
                vtt = download_yt_subtitles(req.source, video_id, lang_code)
                if vtt:
                    transcript, words = parse_vtt_to_transcript(vtt)
            if not transcript.strip() or not words:
                ctx.progress(0.35, "Transkrypcja (Whisper)…")
                try:
                    transcript, words = transcribe_video(video_file, load_whisper(), lang_code=lang_code)
                except Exception as werr:  # noqa: BLE001
                    ctx.log(f"Główny silnik Whisper zawiódł ({werr}); próbuję faster-whisper…", "warning")
                    transcript, words = _faster_whisper_fallback(video_file, lang_code)
            if transcript.strip():
                transcript_file.write_text(transcript, encoding="utf-8")
                words_file.write_text(json.dumps(words), encoding="utf-8")
        if glossary:
            transcript = apply_whisper_glossary_to_transcript(transcript, glossary)
            words = apply_whisper_glossary_to_words(words, glossary)

        src_label = _source_language_label(transcript, s.get("whisper_lang"))
        from datetime import datetime
        project_id = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{video_id}_manual"
        session_dir = SHORTS_DIR / "workspace" / "sessions" / project_id
        session_dir.mkdir(parents=True, exist_ok=True)
        abs_video = os.path.abspath(video_file)
        session = {
            "project_id": project_id,
            "display_name": f"{title} (własne shorty)",
            "ai_outputs": [],
            "video_file": abs_video,
            "original_source_path": abs_video,
            "global_words": words,
            "source_language": src_label,
            "render_settings": s,
            "created": _time.time(),
            "manual": True,
        }
        (session_dir / "data.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        ctx.progress(1.0, "Gotowe — projekt gotowy do montażu własnego shorta.")
        return {"title": title, "video_file": abs_video, "project_id": project_id, "global_words": words}

    job = manager.start("shorts.prepare-manual", target)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Shorts project history (workspace/sessions) — list / load / delete
# ---------------------------------------------------------------------------
_SHORTS_LINKS_FOR: Optional[str] = None


def _ensure_shorts_links() -> None:
    """Relocate Shorts' bulky temp data (sessions/downloads/favorites) into the user's
    work folder (work_dir/Shorts) via symlinks, so it's split per-module and deletable
    like every other category. The vendor engine writes to relative `workspace/<sub>`
    paths, so a symlink transparently redirects it. Imported voice samples stay put
    (persistent, system-side). Existing real folders are migrated once, then linked.
    Re-runs when the work folder changes (cheap no-op otherwise)."""
    global _SHORTS_LINKS_FOR
    ws = SHORTS_DIR / "workspace"
    try:
        shorts_root = config_store.module_dir("shorts")
        key = str(shorts_root.resolve())
        if _SHORTS_LINKS_FOR == key:
            return
        ws.mkdir(parents=True, exist_ok=True)
        for sub in ("sessions", "downloads", "favorites"):
            target = shorts_root / sub
            target.mkdir(parents=True, exist_ok=True)
            link = ws / sub
            if link.is_symlink():
                if os.path.realpath(link) != str(target.resolve()):
                    link.unlink()
                    link.symlink_to(target)
            elif link.exists():
                for item in link.iterdir():
                    dest = target / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))
                shutil.rmtree(link, ignore_errors=True)
                link.symlink_to(target)
            else:
                link.symlink_to(target)
        _SHORTS_LINKS_FOR = key
    except Exception:
        # If the work disk is unavailable, fall back to the real folders (no crash).
        pass


def _sessions_dir() -> Path:
    _ensure_shorts_links()
    d = SHORTS_DIR / "workspace" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/shorts/projects")
def shorts_projects() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in sorted(_sessions_dir().iterdir(), reverse=True) if _sessions_dir().exists() else []:
        data_file = d / "data.json"
        if not (d.is_dir() and data_file.exists()):
            continue
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": d.name,
            "display_name": data.get("display_name", d.name),
            "shorts_count": len(data.get("ai_outputs", [])),
            "created": data.get("created", 0),
            "video_file": data.get("video_file", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Downloaded videos (workspace/downloads) — quick-pick to re-run shorts without
# re-downloading or re-running Whisper.
# ---------------------------------------------------------------------------
_DL_VIDEO_EXTS = (".mp4", ".mkv", ".webm")


def _cached_video_for(dl_dir: Path, video_id: str) -> Optional[Path]:
    """The downloaded video file for a given id, regardless of quality suffix."""
    if not dl_dir.exists() or not video_id:
        return None
    for p in dl_dir.iterdir():
        if p.suffix.lower() in _DL_VIDEO_EXTS and (p.stem == video_id or p.stem.startswith(f"{video_id}_")):
            return p
    return None


@app.get("/api/shorts/downloads")
def shorts_downloads() -> List[Dict[str, Any]]:
    """Videos already on disk — each can re-run shorts with no network calls
    (download + transcript are both cached). Titles come from the meta sidecars
    written at download time; legacy downloads fall back to the video id."""
    _ensure_shorts_links()
    dl_dir = SHORTS_DIR / "workspace" / "downloads"
    if not dl_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()

    # Map video_id → real title from the projects made off each download, so the
    # quick-pick buttons read the film's title instead of the bare YouTube id.
    project_titles: Dict[str, str] = {}
    try:
        for proj in sorted(_sessions_dir().iterdir()) if _sessions_dir().exists() else []:
            df = proj / "data.json"
            if not df.is_file():
                continue
            try:
                pdata = json.loads(df.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = str(pdata.get("display_name") or "").strip()
            vf = os.path.basename(str(pdata.get("video_file") or ""))
            stem = os.path.splitext(vf)[0]
            pvid = stem.rsplit("_", 1)[0] if "_" in stem else stem
            if name and pvid:
                project_titles[pvid] = name  # later (sorted) projects win → freshest title
    except Exception:
        pass

    def _entry(vid: str, vpath: Path, title: str, url: str, quality: str) -> Dict[str, Any]:
        # Prefer a real title; fall back to a project title; only then the bare id.
        display = (title or "").strip()
        if not display or display == vid:
            display = project_titles.get(vid, "") or vid
        return {
            "video_id": vid,
            "title": display,
            "url": url,
            "quality": quality,
            "has_transcript": bool(list(dl_dir.glob(f"{vid}*_transcript.txt"))),
            "size": vpath.stat().st_size,
            "mtime": vpath.stat().st_mtime,
        }

    # Meta sidecars first (they carry the real title + original url).
    for meta in sorted(dl_dir.glob("*_meta.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if _is_junk_name(meta.name):
            continue  # macOS AppleDouble junk, not a real download
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = data.get("video_id") or meta.stem[:-5]
        if vid in seen:
            continue
        f = data.get("file")
        vpath = Path(f) if f and os.path.exists(f) else _cached_video_for(dl_dir, vid)
        if not vpath or not vpath.exists():
            continue
        seen.add(vid)
        out.append(_entry(vid, vpath, data.get("title", ""), data.get("url") or f"https://youtu.be/{vid}", data.get("quality", "")))

    # Legacy downloads without a sidecar.
    for p in sorted(dl_dir.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if _is_junk_name(p.name) or p.suffix.lower() not in _DL_VIDEO_EXTS:
            continue
        vid = p.stem.rsplit("_", 1)[0] if "_" in p.stem else p.stem
        if vid in seen:
            continue
        seen.add(vid)
        quality = p.stem.rsplit("_", 1)[1] if "_" in p.stem else ""
        url = f"https://youtu.be/{vid}" if len(vid) == 11 else ""
        out.append(_entry(vid, p, vid, url, quality))

    out.sort(key=lambda e: e.get("mtime", 0), reverse=True)
    return out


class ShortsDownloadDeleteRequest(BaseModel):
    video_id: str


def _download_id_users(vid: str) -> List[str]:
    """Display names of projects whose source film is the download `vid`.

    A single downloaded video (workspace/downloads/<vid>_*.mp4) plus its cached
    transcript/words/meta is SHARED by every project generated from it. Deleting it
    would break those projects, so callers check this first and refuse when non-empty.
    Matches by the video-id stem so `xl9ZbpQ1Esw` covers `xl9ZbpQ1Esw_1080p.mp4`.
    """
    users: List[str] = []
    if not vid:
        return users
    for proj in sorted(_sessions_dir().iterdir()) if _sessions_dir().exists() else []:
        data_file = proj / "data.json"
        if not (proj.is_dir() and data_file.exists()):
            continue
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("video_file", "original_source_path"):
            stem = Path(os.path.basename(str(data.get(key) or ""))).stem
            if stem == vid or stem.startswith(f"{vid}_") or stem.startswith(f"{vid}."):
                users.append(str(data.get("display_name") or proj.name))
                break
    return users


@app.post("/api/shorts/downloads/delete")
def shorts_downloads_delete(req: ShortsDownloadDeleteRequest) -> Dict[str, Any]:
    """Remove a downloaded video and its cached transcript/words/subs/meta.

    Refuses when the film is still referenced by one or more projects — a shared
    source file must not be deleted out from under a project that needs it. The user
    must delete those projects from History first to free the file.
    """
    _ensure_shorts_links()
    dl_dir = SHORTS_DIR / "workspace" / "downloads"
    vid = os.path.basename((req.video_id or "").strip())
    users = _download_id_users(vid)
    if users:
        return {"ok": False, "removed": 0, "in_use": True, "projects": users}
    removed = 0
    if dl_dir.exists() and vid:
        for p in list(dl_dir.iterdir()):
            if p.stem == vid or p.stem.startswith(f"{vid}_") or p.stem.startswith(f"{vid}."):
                try:
                    p.unlink()
                    removed += 1
                except Exception:
                    pass
    return {"ok": True, "removed": removed, "in_use": False, "projects": []}


@app.get("/api/shorts/projects/{project_id}")
def shorts_project(project_id: str) -> Dict[str, Any]:
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    if _restore_polluted_base_shorts(data):
        # One-time cleanup persisted: un-translate base shorts that an OLD build mutated
        # in place. In the current model base shorts ALWAYS stay in the film's original
        # language; translations live as separate versions. Restoring fixes the recurring
        # "my subtitles became English without asking" on legacy projects.
        try:
            data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as _e:
            print(f"[dubcut-backend] zapis przywróconych oryginałów nieudany: {_e}", file=sys.stderr)
    return data


def _restore_polluted_base_shorts(data: Dict[str, Any]) -> bool:
    """Revert any base short that the deprecated in-place `/api/shorts/translate` left
    translated (it stored `original_*` backups before overwriting). Returns True if
    anything changed. Base shorts must read in the film's ORIGINAL language now —
    translating is opt-in and produces a separate version, never mutates the base."""
    changed = False
    for short in (data.get("ai_outputs") or []):
        if not isinstance(short, dict) or "original_segments" not in short:
            continue
        short["segments"] = short.pop("original_segments")
        if "original_words" in short:
            short["words"] = short.pop("original_words")
        for key in ("title", "hook_text", "hashtags", "yt_tags"):
            ok = f"original_{key}"
            if ok in short:
                short[key] = short.pop(ok)
        short["language"] = short.get("source_language") or data.get("source_language") or "Polski"
        short.pop("translation_engine", None)
        short.pop("_translation_repair", None)
        # The previously rendered mp4 is in the wrong language — force a fresh render.
        short["force_re_render"] = True
        changed = True
    return changed


_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi")


def _resolve_project_source(safe: str, data: Dict[str, Any]) -> Optional[Path]:
    """Find the project's source film even when data.json's stored path went stale
    (file moved into the project folder by the data-hygiene pass, or volume renamed).
    Tries the recorded path, the original source path, an exact-name match inside the
    session/downloads dirs, then the largest video file in the session folder."""
    for c in (data.get("video_file", ""), data.get("original_source_path", "")):
        if c:
            p = Path(c)
            if p.exists() and p.is_file():
                return p
    want = os.path.basename(str(data.get("video_file") or data.get("original_source_path") or "")).lower()
    sess = _sessions_dir() / safe
    search_dirs = [sess, SHORTS_DIR / "workspace" / "downloads"]
    if want:
        for d in search_dirs:
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if f.is_file() and f.name.lower() == want:
                    return f
    if sess.exists():
        vids = sorted(
            (f for f in sess.rglob("*") if f.is_file() and f.suffix.lower() in _VIDEO_EXTS),
            key=lambda f: f.stat().st_size, reverse=True,
        )
        if vids:
            return vids[0]
    return None


@app.get("/api/shorts/projects/{project_id}/source")
def shorts_project_source(project_id: str):
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    source = _resolve_project_source(safe, data)
    if not source:
        raise HTTPException(404, "Brak pliku źródłowego")
    return FileResponse(str(source), media_type="video/mp4", filename=source.name, headers={"Cache-Control": "no-cache"})


def _pcm_to_peaks(raw: bytes, buckets: int) -> List[float]:
    """Downsample mono float32 PCM into `buckets` max-amplitude values, normalised 0..1."""
    n = len(raw) // 4
    if n == 0:
        return [0.0] * buckets
    try:
        import numpy as np  # type: ignore
        a = np.abs(np.frombuffer(raw, dtype=np.float32, count=n))
        edges = (np.arange(buckets + 1) * n // buckets).astype(int)
        out = np.zeros(buckets, dtype=np.float32)
        for b in range(buckets):
            s, e = edges[b], edges[b + 1]
            if e > s:
                out[b] = a[s:e].max()
        mx = float(out.max()) or 1.0
        return (out / mx).tolist()
    except Exception:
        import array
        a = array.array("f")
        a.frombytes(raw[: n * 4])
        out = []
        for b in range(buckets):
            s = b * n // buckets
            e = (b + 1) * n // buckets
            m = 0.0
            for i in range(s, e):
                v = a[i] if a[i] >= 0 else -a[i]
                if v > m:
                    m = v
            out.append(m)
        mx = max(out) or 1.0
        return [v / mx for v in out]


@app.get("/api/shorts/projects/{project_id}/peaks")
def shorts_project_peaks(project_id: str, start: float = 0.0, end: float = 0.0, buckets: int = 800) -> Dict[str, Any]:
    """Real audio waveform peaks for the [start,end] window of the source film, so the
    scene/subtitle editors can show the ACTUAL sound (not a fake bar pattern)."""
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    source = _resolve_project_source(safe, data)
    if not source:
        raise HTTPException(404, "Brak pliku źródłowego")
    start = max(0.0, float(start))
    end = float(end)
    if end <= start:
        end = start + 1.0
    dur = min(600.0, end - start)
    buckets = max(50, min(4000, int(buckets)))
    sr = 8000
    try:
        from utils import get_ffmpeg_path  # type: ignore
        prev = os.getcwd()
        try:
            os.chdir(str(SHORTS_DIR))
            ff = get_ffmpeg_path()
        finally:
            try:
                os.chdir(prev)
            except Exception:
                pass
    except Exception:
        ff = "ffmpeg"
    cmd = [ff, "-v", "quiet", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=30).stdout
    except Exception:
        raw = b""
    return {"start": start, "end": start + dur, "peaks": _pcm_to_peaks(raw, buckets)}


@app.get("/api/dub/peaks")
def dub_session_peaks(session: str, start: float = 0.0, end: float = 0.0, buckets: int = 800) -> Dict[str, Any]:
    """Waveform data for the DubMaster word-timing editor."""
    sess = _dub_session_dir(session)
    try:
        source = json.loads((sess / "session.json").read_text(encoding="utf-8")).get("video_file", "")
    except Exception:
        source = ""
    if not source or not os.path.exists(source):
        raise HTTPException(404, "Brak pliku źródłowego")
    start = max(0.0, float(start)); end = float(end)
    if end <= start: end = start + 1.0
    dur = min(600.0, end - start); buckets = max(50, min(4000, int(buckets)))
    cmd = ["ffmpeg", "-v", "quiet", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
           "-ac", "1", "-ar", "8000", "-f", "f32le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=30).stdout
    except Exception:
        raw = b""
    return {"start": start, "end": start + dur, "peaks": _pcm_to_peaks(raw, buckets)}


class ShortEditRequest(BaseModel):
    # Edited subtitle data. `words` keep their Whisper start/end so subtitle↔speech
    # timing stays exact after corrections; `segments` text is rebuilt from words.
    segments: List[Dict[str, Any]]
    words: List[Dict[str, Any]]


class ShortSceneEditRequest(BaseModel):
    segments: List[Dict[str, Any]]
    restore: bool = False


def _word_time_key(word: Dict[str, Any]) -> str:
    try:
        return f"{float(word.get('start', 0.0)):.3f}_{float(word.get('end', 0.0)):.3f}"
    except Exception:
        return f"{word.get('start')}_{word.get('end')}"


def _segment_words_from_source(
    segments: List[Dict[str, Any]],
    source_words: List[Dict[str, Any]],
    edited_words: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Pull words for edited scene ranges from the full transcript.

    The scene editor previews text from project-wide ``global_words``. When a user extends
    a scene, the persisted short words must be rebuilt from the same source, otherwise the
    subtitle editor and ASS render keep using the old, shorter word list.
    """
    edits = {_word_time_key(w): str(w.get("word", "")) for w in (edited_words or [])}
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []

    for seg in sorted(segments or [], key=lambda x: float(x.get("start_time", 0.0))):
        try:
            start = float(seg.get("start_time", 0.0))
            end = float(seg.get("end_time", start))
        except Exception:
            continue
        if end <= start:
            continue
        for src in source_words or []:
            try:
                ws = float(src.get("start", 0.0))
                we = float(src.get("end", ws))
            except Exception:
                continue
            if we <= start + 0.02 or ws >= end - 0.02:
                continue
            key = f"{ws:.3f}_{we:.3f}"
            if key in seen:
                continue
            seen.add(key)
            word = json.loads(json.dumps(src, ensure_ascii=False))
            word["start"] = ws
            word["end"] = we
            if key in edits:
                word["word"] = edits[key]
            out.append(word)

    return sorted(out, key=lambda w: float(w.get("start", 0.0)))


def _segment_text_from_words(seg: Dict[str, Any], words: List[Dict[str, Any]]) -> str:
    try:
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start))
    except Exception:
        return ""
    return " ".join(
        str(w.get("word", "")).strip()
        for w in words
        if str(w.get("word", "")).strip()
        and float(w.get("end", 0.0)) > start + 0.02
        and float(w.get("start", 0.0)) < end - 0.02
    ).strip()


@app.put("/api/shorts/projects/{project_id}/shorts/{index}")
def update_short(project_id: str, index: int, req: ShortEditRequest) -> Dict[str, Any]:
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])
    if index < 0 or index >= len(shorts):
        raise HTTPException(400, "Nieprawidłowy indeks shorta")
    # Sort words by start so the karaoke/highlight order matches the spoken order.
    words = sorted(req.words, key=lambda w: float(w.get("start", 0.0)))
    shorts[index]["words"] = words
    shorts[index]["segments"] = req.segments
    shorts[index]["force_re_render"] = True  # subtitles changed → needs a fresh render
    data["ai_outputs"] = shorts
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "short": shorts[index]}


def _merge_fine_segments(segments: List[Dict[str, Any]], gap_threshold: float = 0.6) -> List[Dict[str, Any]]:
    """Collapse the fine, sentence-sized TTS sub-segments back into the coarse Gemini
    "scenes" for display/editing. Sub-segments of ONE scene tile it contiguously
    (gap≈0); DIFFERENT scenes are cut from far-apart moments of the film (gaps of
    seconds–minutes), and at creation Gemini scenes <2s apart were already merged — so
    a 0.6s threshold rejoins only the tiling and never fuses real, separate scenes.
    The merged spans are byte-identical to the originals, so a later re-render is
    unchanged. Used to fix shorts dubbed before 0.4.80 that show ~27 micro-rows."""
    segs = sorted(
        ({**s, "start_time": float(s.get("start_time", 0.0)), "end_time": float(s.get("end_time", 0.0))}
         for s in (segments or []) if float(s.get("end_time", 0.0)) > float(s.get("start_time", 0.0))),
        key=lambda s: s["start_time"],
    )
    out: List[Dict[str, Any]] = []
    for s in segs:
        txt = str(s.get("text", "")).strip()
        if out and s["start_time"] - out[-1]["end_time"] <= gap_threshold:
            out[-1]["end_time"] = max(out[-1]["end_time"], s["end_time"])
            if txt:
                out[-1]["text"] = (str(out[-1].get("text", "")).strip() + " " + txt).strip()
        else:
            out.append({"start_time": s["start_time"], "end_time": s["end_time"], "text": txt})
    return out


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/merge-scenes")
def merge_short_scenes(project_id: str, index: int) -> Dict[str, Any]:
    """Rejoin a short's micro TTS sub-segments into coarse scenes (see
    `_merge_fine_segments`). For shorts translated/dubbed before 0.4.80 whose scene
    editor shows dozens of tiny rows. No re-render needed — spans are identical."""
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])
    if index < 0 or index >= len(shorts):
        raise HTTPException(400, "Nieprawidłowy indeks shorta")
    short = shorts[index]
    before = len(short.get("segments", []))
    merged = _merge_fine_segments(short.get("segments", []))
    if not merged:
        raise HTTPException(400, "Brak scen do scalenia")
    short["segments"] = merged
    shorts[index] = short
    data["ai_outputs"] = shorts
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "short": short, "before": before, "after": len(merged)}


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}/merge-scenes")
def merge_version_scenes(project_id: str, index: int, language_slug: str) -> Dict[str, Any]:
    """Same coarse-scene rejoin for an already-rendered dub/lektor version's manifest."""
    safe_pid, _, base_short, version_dir, manifest_path, manifest = _load_short_version(project_id, index, language_slug)
    short_data = dict(manifest.get("short_data") or base_short or {})
    before = len(short_data.get("segments", []))
    merged = _merge_fine_segments(short_data.get("segments", []))
    if not merged:
        raise HTTPException(400, "Brak scen do scalenia")
    short_data["segments"] = merged
    manifest["short_data"] = short_data
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "short": short_data, "before": before, "after": len(merged)}


@app.put("/api/shorts/projects/{project_id}/shorts/{index}/scenes")
def update_short_scenes(project_id: str, index: int, req: ShortSceneEditRequest) -> Dict[str, Any]:
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])
    if index < 0 or index >= len(shorts):
        raise HTTPException(400, "Nieprawidłowy indeks shorta")

    short = shorts[index]
    if not short.get("backup_segments"):
        short["backup_segments"] = json.loads(json.dumps(short.get("segments", []), ensure_ascii=False))

    new_segments = short.get("backup_segments", []) if req.restore else req.segments
    clean_segments = []
    for seg in new_segments:
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", 0.0))
        if end <= start:
            continue
        clean_segments.append({**seg, "start_time": start, "end_time": end})
    if not clean_segments:
        raise HTTPException(400, "Brak poprawnych scen do zapisania")

    requested_segments = json.loads(json.dumps(clean_segments, ensure_ascii=False))

    # The scene editor previews text from the full project transcript. Rebuild the short's
    # words from that same full transcript instead of asking ai_processor to snap by the
    # old segment text; otherwise extending a scene can snap back to the old final word,
    # leaving the subtitle editor and ASS render without the newly covered words.
    source_words = data.get("global_words", []) or short.get("words", [])
    resynced_words = _segment_words_from_source(requested_segments, source_words, short.get("words", []))

    if not req.restore:
        rebuilt: List[Dict[str, Any]] = []
        for seg in sorted(requested_segments, key=lambda x: float(x.get("start_time", 0.0))):
            s = float(seg["start_time"])
            e = float(seg["end_time"])
            txt = _segment_text_from_words({"start_time": s, "end_time": e}, resynced_words)
            rebuilt.append({**seg, "start_time": s, "end_time": e, "text": txt or seg.get("text", "")})
        short["words"] = resynced_words
        short["segments"] = rebuilt
    else:
        short["words"] = resynced_words
        short["segments"] = requested_segments

    # Editing scenes invalidates any stale translated/original shadow state. Future
    # translation/dub steps should start from the current cut and current word list.
    short.pop("original_words", None)
    short.pop("original_segments", None)

    short["force_re_render"] = True
    short.pop("rendered_file", None)
    shorts[index] = short
    data["ai_outputs"] = shorts
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "short": short}


class CustomShortRequest(BaseModel):
    segments: List[Dict[str, Any]]
    title: Optional[str] = None


@app.post("/api/shorts/projects/{project_id}/shorts/custom")
def create_custom_short(project_id: str, req: CustomShortRequest) -> Dict[str, Any]:
    """Create a brand-new short by hand from the full film: the user picks the scenes
    (start/end), we pull the matching words from the film-wide transcription and append a
    short in the SAME shape as auto-generated ones — so the normal render then applies the
    style preset, subtitles, logo and watermark exactly like an automatic short."""
    safe = os.path.basename(project_id)
    data_file = _sessions_dir() / safe / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])

    clean_segments: List[Dict[str, Any]] = []
    for seg in sorted(req.segments, key=lambda x: float(x.get("start_time", 0.0))):
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", 0.0))
        if end <= start:
            continue
        clean_segments.append({"start_time": start, "end_time": end, "text": str(seg.get("text", ""))})
    if not clean_segments:
        raise HTTPException(400, "Brak poprawnych scen do utworzenia shorta")

    global_words = data.get("global_words", [])
    new_short: Dict[str, Any] = {
        "title": (req.title or "").strip() or f"Własny short {len(shorts) + 1}",
        "hook_text": "",
        "hashtags": "",
        "yt_tags": "",
        "score": 0,
        "language": data.get("source_language") or (shorts[0].get("language") if shorts else "Polski"),
        "source_language": data.get("source_language", ""),
        "segments": clean_segments,
        "custom": True,
    }

    prev_cwd = os.getcwd()
    try:
        os.chdir(str(SHORTS_DIR))
        from ai_processor import initialize_short_words  # type: ignore
        initialize_short_words(new_short, global_words)
    except Exception as exc:
        print(f"[dubcut-backend] initialize_short_words failed for custom short: {exc}", file=sys.stderr)
        new_short.setdefault("words", [])
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass

    # Per-scene subtitle text rebuilt from the pulled words (same convention as the editor).
    words = new_short.get("words", [])
    for seg in clean_segments:
        s, e = seg["start_time"], seg["end_time"]
        txt = " ".join(
            w.get("word", "") for w in words
            if w.get("start", 0.0) >= s - 0.5 and w.get("end", 0.0) <= e + 0.5
        ).strip()
        if txt:
            seg["text"] = txt

    shorts.append(new_short)
    data["ai_outputs"] = shorts
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "index": len(shorts) - 1, "short": new_short}


@app.delete("/api/shorts/projects/{project_id}/shorts/{index}")
def delete_short(project_id: str, index: int) -> Dict[str, Any]:
    safe = os.path.basename(project_id)
    project_root = _sessions_dir() / safe
    data_file = project_root / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])
    if index < 0 or index >= len(shorts):
        raise HTTPException(400, "Nieprawidłowy indeks shorta")

    removed = shorts.pop(index)
    removed_paths: List[str] = []

    def remove_path(path: Path):
        try:
            if path.exists() and path.is_file():
                path.unlink()
                removed_paths.append(str(path))
        except Exception:
            pass

    rendered = removed.get("rendered_file")
    if rendered:
        rendered_path = Path(rendered)
        if not rendered_path.is_absolute():
            rendered_path = project_root / "shorts" / rendered_path.name
        remove_path(rendered_path)
        remove_path(rendered_path.with_suffix(".ass"))

    shorts_dir = project_root / "shorts"
    if shorts_dir.exists():
        for p in shorts_dir.glob(f"{index}_*"):
            remove_path(p)
        for p in shorts_dir.glob(f"._{index}_*"):
            remove_path(p)

    versions_root = project_root / "short_versions"
    target_version_dir = versions_root / f"short_{index:02d}"
    if target_version_dir.exists():
        _force_rmtree(target_version_dir)
        removed_paths.append(str(target_version_dir))

    # Keep version directories aligned with the shifted short indexes.
    if versions_root.exists():
        for old_idx in range(index + 1, len(shorts) + 1):
            old_dir = versions_root / f"short_{old_idx:02d}"
            new_dir = versions_root / f"short_{old_idx - 1:02d}"
            if old_dir.exists() and not new_dir.exists():
                try:
                    old_dir.rename(new_dir)
                except Exception:
                    pass

    data["ai_outputs"] = shorts
    data["display_name"] = _display_name_with_count(data.get("display_name", safe), len(shorts))
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "removed": removed_paths, "shorts_count": len(shorts)}


def _display_name_with_count(display_name: str, count: int) -> str:
    base = re.sub(r"\s*\(\d+\s+short(?:ów|y|ow)?\)\s*$", "", str(display_name or "")).strip()
    return f"{base} ({count} shortów)" if base else f"{count} shortów"


@app.delete("/api/shorts/projects/{project_id}")
def delete_shorts_project(project_id: str) -> Dict[str, bool]:
    import shutil
    safe = os.path.basename(project_id)
    target_dir = _sessions_dir() / safe
    if target_dir.exists():
        _force_rmtree(target_dir)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Shorts render — burn subtitles/logo/watermark into the actual clip
# ---------------------------------------------------------------------------
class ShortsRenderRequest(BaseModel):
    project_id: str
    index: int                                   # which short within the project
    settings: Optional[Dict[str, Any]] = None    # current UI settings (font/preset/etc.)


class ShortsTranslateRequest(BaseModel):
    project_id: str
    index: int
    language: str


class ShortsDubRenderRequest(BaseModel):
    project_id: str
    index: int
    language: str
    settings: Optional[Dict[str, Any]] = None


class ShortsVersionUpdateRequest(BaseModel):
    segments: List[Dict[str, Any]]
    words: List[Dict[str, Any]]


class ShortsVersionRenderRequest(BaseModel):
    settings: Optional[Dict[str, Any]] = None


class ShortsVersionSubtitleRequest(BaseModel):
    subtitle_language: str
    settings: Optional[Dict[str, Any]] = None


class ShortsVersionSubtitleBatchRequest(BaseModel):
    subtitle_languages: List[str]
    settings: Optional[Dict[str, Any]] = None


class ShortsFavoriteRequest(BaseModel):
    favorite_id: str
    kind: str = "short"
    project_id: str
    index: int
    language_slug: Optional[str] = None
    project_title: Optional[str] = None
    total: Optional[int] = None
    short: Optional[Dict[str, Any]] = None


_PL_TRANSLIT = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
})


def _safe_title(title: str, index: int) -> str:
    # ASCII-ONLY on purpose. Non-ASCII filenames (e.g. „…Prądu…") on the user's exFAT
    # work disk spawn AppleDouble `._` sidecars that become UNDELETABLE (unlink reports
    # ENOENT via a NFC/NFD mismatch), so a deleted version's folder could never be fully
    # removed and 'resurrected' as a ghost. Transliterate Polish diacritics, then strip
    # any remaining non-ASCII so every generated file/folder name is plain ASCII.
    import unicodedata
    t = str(title or "").translate(_PL_TRANSLIT)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9 _-]", "", t).strip()
    base = re.sub(r"\s+", "_", base)[:60]
    return base or f"short_{index + 1}"


def _load_project_short(project_id: str, index: int):
    safe_pid = os.path.basename(project_id)
    data_file = _sessions_dir() / safe_pid / "data.json"
    if not data_file.exists():
        raise HTTPException(404, "Nie znaleziono projektu")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    shorts = data.get("ai_outputs", [])
    if index < 0 or index >= len(shorts):
        raise HTTPException(400, "Nieprawidłowy indeks shorta")
    return safe_pid, data_file, data, shorts[index]


def _load_short_version(project_id: str, index: int, language_slug: str):
    safe_pid, _, data, short = _load_project_short(project_id, index)
    safe_slug = os.path.basename(language_slug)
    version_dir = _sessions_dir() / safe_pid / "short_versions" / f"short_{index:02d}" / safe_slug
    if not version_dir.exists():
        raise HTTPException(404, "Nie znaleziono wersji dubbingu")
    manifest_path = version_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception:
        manifest = {}
    return safe_pid, data, short, version_dir, manifest_path, manifest


def _favorites_dir() -> Path:
    path = SHORTS_DIR / "workspace" / "favorites"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_favorite_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "")).strip("._")
    return safe[:160] or hashlib.sha1(str(_time.time()).encode()).hexdigest()


def _copy_if_file(path: Any, target_dir: Path, name: Optional[str] = None) -> str:
    if not path:
        return ""
    src = Path(str(path))
    if not src.exists() or not src.is_file():
        return ""
    target = target_dir / (name or src.name)
    shutil.copy2(src, target)
    return str(target)


class _JobStatus:
    def __init__(self, ctx):
        self.ctx = ctx

    def markdown(self, message: str):
        self.ctx.log(str(message), "info")

    def warning(self, message: str):
        self.ctx.log(str(message), "warning")

    def success(self, message: str):
        self.ctx.log(str(message), "info")


class _JobProgress:
    def __init__(self, ctx, start=0.0, span=1.0):
        self.ctx = ctx
        self.start = start
        self.span = span

    def progress(self, value: float):
        self.ctx.progress(self.start + max(0.0, min(1.0, float(value))) * self.span)


class _RenderProgress:
    """Bridge passed to video_engine.render_short_ffmpeg as BOTH status_text and
    progress_bar, so the AI-camera tracking loop surfaces a live MAIN status message
    (e.g. „Wirtualna kamera AI: klatka 540/2130 · kadr na osobie") + progress on the
    job — instead of the bar sitting at a frozen „Renderowanie wideo (FFmpeg)…" for
    minutes. The engine reports an internal 0..1 fraction; we remap it into
    [start, start+span] of the overall job."""
    def __init__(self, ctx, start=0.0, span=1.0):
        self.ctx = ctx
        self.start = start
        self.span = span
        self._v = 0.0
        self._m = ""

    def _emit(self):
        self.ctx.progress(self.start + self._v * self.span, self._m)

    def progress(self, value: float):
        self._v = max(0.0, min(1.0, float(value)))
        self._emit()

    def markdown(self, message: str):
        # Strip leftover markdown bold so the status line stays clean.
        self._m = str(message).replace("**", "").strip()
        self._emit()

    def warning(self, message: str):
        self.markdown(message)

    def success(self, message: str):
        self.markdown(message)


@app.post("/api/shorts/render")
def shorts_render(req: ShortsRenderRequest) -> Dict[str, Any]:
    safe_pid, data_file, data, short = _load_project_short(req.project_id, req.index)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")

    full = config_store.load()
    # Current UI settings drive the render (so "re-render with a different font" works),
    # falling back to what the project was created with.
    s = {**full["shorts"], **(data.get("render_settings") or {}), **(req.settings or {})}

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.step("Inicjalizacja renderera…")
        from video_engine import render_short_ffmpeg  # type: ignore

        aspect_ratio = "16:9" if "16:9" in str(s.get("aspect_ratio", "")) else "9:16"
        export_res = _export_res_label(s.get("export_resolution"))
        out_dir = _sessions_dir() / safe_pid / "shorts"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_title = _safe_title(short.get("title"), req.index)
        out_mp4 = out_dir / f"{safe_title}.mp4"
        ass_path = out_dir / f"{safe_title}.ass"

        segments = short.get("segments", [])
        words = short.get("words", [])

        ass_arg = None
        if s.get("enable_subtitles", True) and segments:
            ctx.progress(0.15, "Obliczanie sygnatur czasowych napisów…")
            _build_short_ass(s, segments, words, str(ass_path), aspect_ratio)
            ass_arg = str(ass_path)

        # Logo/watermark settings mapped exactly like the live preview.
        logo_path = _resolve_logo_path(s.get("logo_path"))
        logo_settings = _preview_logo_settings(s, logo_path)
        logo_settings["enable_logo"] = bool(s.get("enable_logo"))
        logo_settings["enable_text"] = bool(s.get("enable_text"))

        ctx.progress(0.4, "Renderowanie wideo (FFmpeg)…")
        _cam = _RenderProgress(ctx, 0.4, 0.55)
        render_short_ffmpeg(
            video_file, segments, str(out_mp4),
            aspect_ratio=aspect_ratio,
            ass_subtitle_file=ass_arg,
            export_res=export_res,
            export_bitrate=int(_num(s.get("export_bitrate"), 15)),
            export_codec=s.get("export_codec", "H.264 (Większa kompatybilność)"),
            face_tracking=bool(s.get("face_tracking")),
            ft_smoothness=int(_num(s.get("ft_smoothness"), 10)),
            ft_recheck=int(_num(s.get("ft_recheck"), 8)),
            ft_zoom=_num(s.get("ft_zoom"), 1.0),
            ft_y_offset=int(_num(s.get("ft_y_offset"), 0)),
            ft_strategy=s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
            ft_tracker=s.get("ft_tracker", "Auto"),
            smart_reframe=bool(s.get("face_tracking")) and bool(s.get("smart_reframe")),
            reframe_speed=int(_num(s.get("reframe_speed"), 50)),
            status_text=_cam, progress_bar=_cam,
            logo_settings=logo_settings,
        )
        if not out_mp4.exists():
            raise RuntimeError("Render nie wyprodukował pliku wyjściowego.")

        # Remember the last render path on the short, persisted back to the project.
        rel = f"{safe_pid}/shorts/{out_mp4.name}"
        short["rendered_file"] = str(out_mp4)
        short["rendered_at"] = _time.time()
        data["ai_outputs"][req.index] = short
        data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        ctx.progress(1.0, "Gotowe — wideo wyrenderowane.")
        return {
            "url": f"/api/shorts/file/{rel}",
            "title": short.get("title"),
            "file": str(out_mp4),
            "index": req.index,
            "rendered_at": short["rendered_at"],
        }

    job = manager.start("shorts.render", target)
    return {"job_id": job.id}


@app.post("/api/shorts/translate")
def shorts_translate(req: ShortsTranslateRequest) -> Dict[str, Any]:
    safe_pid, data_file, data, short = _load_project_short(req.project_id, req.index)
    # "Brak (Oryginał)" means "keep the source language" — it is not a translation
    # target. Reject it up front with a clear message instead of letting it fall
    # through to NLLB as an unsupported language (which fails with a misleading
    # "install the engine" error).
    if not (req.language or "").strip() or req.language.strip() == "Brak (Oryginał)":
        raise HTTPException(400, "Wybierz język docelowy tłumaczenia (np. Angielski) — „Brak (Oryginał)” zostawia napisy bez zmian.")
    full = config_store.load()
    api_key = (full.get("app", {}).get("gemini_api_key") or "").strip()
    engine = _translation_engine(full)
    glossary = (data.get("whisper_glossary") or full.get("app", {}).get("glossary") or "")

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.progress(0.05, f"Tłumaczenie shorta na: {req.language} (silnik: {engine})…")
        import local_translate  # type: ignore

        # The local NLLB engine downloads a ~1.2 GB model on first use — that step has
        # no sub-progress, so warn the user it can take a few minutes rather than look
        # frozen. Subsequent runs hit the cache and finish in seconds.
        if engine != "gemini" and not api_key:
            ctx.progress(0.10, "Ładowanie modelu tłumaczenia (przy pierwszym użyciu pobiera ~1.2 GB — to może potrwać kilka minut)…")

        translated = json.loads(json.dumps(short, ensure_ascii=False))
        ok = local_translate.translate_short(
            translated, req.language, engine=engine, gemini_api_key=api_key,
            glossary_text=glossary,
        )
        if not ok:
            raise RuntimeError(
                "Nie udało się przetłumaczyć shorta. Sprawdź, czy zainstalowano lokalny "
                "silnik tłumaczenia (NLLB lub Argos) w Ustawieniach → Środowisko lokalne."
            )
        translated["language"] = req.language
        repair = translated.pop("_translation_repair", None)
        data["ai_outputs"][req.index] = translated
        data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ctx.progress(1.0, "Gotowe — napisy i metadane przetłumaczone.")
        return {"short": translated, "language": req.language, "project_id": safe_pid, "repair": repair}

    job = manager.start("shorts.translate", target)
    return {"job_id": job.id}


@app.post("/api/shorts/render-dub")
def shorts_render_dub(req: ShortsDubRenderRequest) -> Dict[str, Any]:
    safe_pid, data_file, data, short = _load_project_short(req.project_id, req.index)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")
    full = config_store.load()
    api_key = (full.get("app", {}).get("gemini_api_key") or "").strip()
    s = {
        **full["shorts"],
        **(data.get("render_settings") or {}),
        **(req.settings or {}),
        "audio_mode": (req.settings or {}).get("audio_mode", "Czysty dubbing (usuń oryginalny głos)"),
        "dub_target_lang": req.language,
        "dub_auto_subtitles": (req.settings or {}).get("dub_auto_subtitles", True),
        "tts_engine": str(full.get("app", {}).get("tts_engine", "qwen")).lower(),
    }

    engine = _translation_engine(full)
    glossary = (data.get("whisper_glossary") or full.get("app", {}).get("glossary") or "")

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.step("Inicjalizacja renderowania dubbingu shorta…")
        import local_translate  # type: ignore
        from dubbing_engine import (  # type: ignore
            build_dubbed_audio, version_slug, update_version_manifest,
            get_short_versions_dir, load_cached_translation, save_cached_translation,
            sentences_from_words,
        )
        from video_engine import render_short_ffmpeg  # type: ignore

        version_short = json.loads(json.dumps(short, ensure_ascii=False))
        # SOURCE word timings (local only). Do NOT stamp version_short["original_words"]
        # here: that would make translate_short's _backup_originals early-return and skip
        # setting original_segments, which then makes the translation-cache source hash
        # mismatch every run (saved on EN segments, looked up on PL) → cache never hits.
        src_words = data.get("global_words") or short.get("words") or []
        # CUSTOM short → split the single long segment into real SENTENCE segments (true
        # Whisper timings) BEFORE translation, so each sentence is translated on its own
        # and later placed at its exact time. This is what stops sentences being cut
        # across silences or many sentences being crammed into one window.
        # The VIDEO cut must always cover the FULL contiguous clip (render concatenates
        # each segment's window). Sentence segments only cover the spoken parts, so keep
        # the original full segments for the video while the dub uses the sentences.
        custom_video_segments = None
        if short.get("custom") and src_words:
            custom_video_segments = json.loads(json.dumps(short.get("segments", [])))
            try:
                sent_segs = sentences_from_words(src_words)
                if len(sent_segs) > 1:
                    version_short["segments"] = sent_segs
            except Exception as _se:
                print(f"[dubcut-backend] podział na zdania nieudany: {_se}", file=sys.stderr)
        cache_version_dir = get_short_versions_dir(safe_pid, req.index) / version_slug(req.language, s)
        if s.get("dub_auto_subtitles", True):
            # Reuse a previous translation when the short hasn't changed — translate_short
            # hits the paid Gemini API, so caching by a content hash saves real money. The
            # cache lives in the version's work folder; editing the short invalidates it.
            if load_cached_translation(version_short, req.language, cache_version_dir):
                ctx.progress(0.16, "Użyto zapisanego tłumaczenia (bez ponownego API Gemini)…")
            else:
                ctx.progress(0.08, f"Tłumaczenie napisów i metadanych na: {req.language} (silnik: {engine})…")
                ok = local_translate.translate_short(
                    version_short, req.language, engine=engine, gemini_api_key=api_key,
                    glossary_text=glossary,
                )
                if not ok:
                    raise RuntimeError(
                        "Nie udało się przetłumaczyć shorta do języka dubbingu. Sprawdź, czy "
                        "zainstalowano lokalny silnik tłumaczenia (NLLB lub Argos)."
                    )
                try:
                    save_cached_translation(version_short, req.language, cache_version_dir)
                except Exception as _e:
                    print(f"[dubcut-backend] zapis cache tłumaczenia nieudany: {_e}", file=sys.stderr)

        ctx.progress(0.18, "Generowanie i synchronizacja audio dubbingu…")
        audio_path, version_dir = build_dubbed_audio(
            video_file,
            version_short,
            safe_pid,
            req.index,
            s,
            status_text=_JobStatus(ctx),
            progress_bar=_JobProgress(ctx, 0.18, 0.45),
        )

        aspect_ratio = "16:9" if "16:9" in str(s.get("aspect_ratio", "")) else "9:16"
        export_res = _export_res_label(s.get("export_resolution"))
        lang_slug = version_slug(req.language, s)
        # build_dubbed_audio returns a path relative to SHORTS_DIR (the job's cwd).
        # Normalise to absolute so the render, manifest, and URL all agree — otherwise
        # `relative_to(_sessions_dir())` blows up mixing relative + absolute paths.
        out_dir = Path(version_dir)
        if not out_dir.is_absolute():
            out_dir = SHORTS_DIR / out_dir
        out_dir = out_dir.resolve()
        version_dir = str(out_dir)
        safe_title = _safe_title(version_short.get("title"), req.index)
        out_mp4 = out_dir / f"{safe_title}_{lang_slug}.mp4"
        ass_path = out_dir / f"{safe_title}_{lang_slug}.ass"

        ass_arg = None
        if s.get("enable_subtitles", True) and version_short.get("segments"):
            ctx.progress(0.66, "Budowanie napisów dla wersji językowej…")
            _build_short_ass(s, version_short.get("segments", []), version_short.get("words", []), str(ass_path), aspect_ratio)
            ass_arg = str(ass_path)

        logo_path = _resolve_logo_path(s.get("logo_path"))
        logo_settings = _preview_logo_settings(s, logo_path)
        logo_settings["enable_logo"] = bool(s.get("enable_logo"))
        logo_settings["enable_text"] = bool(s.get("enable_text"))

        ctx.progress(0.72, "Renderowanie wideo z dubbingiem…")
        _cam = _RenderProgress(ctx, 0.72, 0.27)
        render_short_ffmpeg(
            video_file,
            custom_video_segments if custom_video_segments else version_short.get("segments", []),
            str(out_mp4),
            aspect_ratio=aspect_ratio,
            ass_subtitle_file=ass_arg,
            export_res=export_res,
            export_bitrate=int(_num(s.get("export_bitrate"), 15)),
            export_codec=s.get("export_codec", "H.264 (Większa kompatybilność)"),
            face_tracking=bool(s.get("face_tracking")),
            ft_smoothness=int(_num(s.get("ft_smoothness"), 10)),
            ft_recheck=int(_num(s.get("ft_recheck"), 8)),
            ft_zoom=_num(s.get("ft_zoom"), 1.0),
            ft_y_offset=int(_num(s.get("ft_y_offset"), 0)),
            ft_strategy=s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
            ft_tracker=s.get("ft_tracker", "Auto"),
            smart_reframe=bool(s.get("face_tracking")) and bool(s.get("smart_reframe")),
            reframe_speed=int(_num(s.get("reframe_speed"), 50)),
            status_text=_cam, progress_bar=_cam,
            logo_settings=logo_settings,
            audio_override_path=audio_path,
        )
        repair = version_short.pop("_translation_repair", None)
        update_version_manifest(version_dir, str(out_mp4), version_short)
        session_root = (_sessions_dir() / safe_pid).resolve()
        try:
            rel = out_mp4.relative_to(session_root).as_posix()
        except ValueError:
            # Path lives outside the expected session root — fall back to the
            # short_versions subtree so the artifact URL still resolves.
            rel = Path("short_versions") / out_mp4.parent.name / out_mp4.name
            rel = rel.as_posix()
        ctx.progress(1.0, f"Gotowe — wersja {req.language} wyrenderowana.")
        return {
            "url": f"/api/shorts/artifact/{safe_pid}?path={quote(rel)}",
            "title": version_short.get("title"),
            "language": req.language,
            "audio_path": audio_path,
            "file": str(out_mp4),
            "short": version_short,
            "repair": repair,
        }

    job = manager.start("shorts.render-dub", target)
    return {"job_id": job.id}


def _export_res_label(value: Any) -> str:
    """Map our export_resolution string to the engine's res labels."""
    t = str(value or "1080p")
    if "4K" in t or "2160" in t:
        return "4K (2160p)"
    if "2K" in t or "1440" in t:
        return "2K (1440p)"
    if "720" in t:
        return "720p"
    if "480" in t:
        return "480p"
    if "Zgodna" in t or "źród" in t:
        return "Zgodna ze źródłem"
    return "1080p"


@app.get("/api/shorts/file/{project_id}/{folder}/{filename}")
def get_shorts_file(project_id: str, folder: str, filename: str):
    safe = _sessions_dir() / os.path.basename(project_id) / os.path.basename(folder) / os.path.basename(filename)
    if not safe.exists() or safe.suffix.lower() != ".mp4":
        raise HTTPException(404, "Brak pliku")
    return FileResponse(str(safe), media_type="video/mp4", headers={"Cache-Control": "no-cache"})


def _srt_timestamp(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60); ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:  # rounding can spill into the next second
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _cues_from_words(words: List[Dict[str, Any]], *, max_words: int = 7, max_dur: float = 3.5,
                     max_gap: float = 0.7) -> List[Dict[str, Any]]:
    """Group word-level timings into short, readable SRT cues. A cue is flushed when it
    reaches `max_words`, would exceed `max_dur` seconds, the gap to the next word is
    large (a pause), or a word ends a sentence. This keeps each line tightly aligned to
    the spoken/animated word it covers — far better than dumping a whole 30 s scene as
    one cue."""
    cues: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []

    def flush() -> None:
        if not cur:
            return
        text = " ".join(str(w.get("word", "")).strip() for w in cur if str(w.get("word", "")).strip())
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append({"start_time": float(cur[0]["start"]), "end_time": float(cur[-1]["end"]), "text": text})
        cur.clear()

    for i, w in enumerate(words or []):
        try:
            ws, we = float(w.get("start")), float(w.get("end"))
        except (TypeError, ValueError):
            continue
        if not str(w.get("word", "")).strip():
            continue
        cur.append({"word": w.get("word", ""), "start": ws, "end": we})
        token = str(w.get("word", "")).strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap_next = 999.0
        if nxt is not None:
            try:
                gap_next = float(nxt.get("start")) - we
            except (TypeError, ValueError):
                gap_next = 0.0
        dur = we - float(cur[0]["start"])
        ends_sentence = token.endswith((".", "!", "?", "…", ":"))
        if len(cur) >= max_words or dur >= max_dur or gap_next >= max_gap or ends_sentence:
            flush()
    flush()
    return cues


def _segments_to_srt(segments: List[Dict[str, Any]], words: Optional[List[Dict[str, Any]]] = None) -> str:
    """Build a standard .srt. Prefer word-level timings (short, well-synced cues); fall
    back to one cue per segment when no usable word timings exist."""
    cues = _cues_from_words(words or [])
    if not cues:
        cues = []
        for seg in segments or []:
            text = str(seg.get("text", "") or "").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start_time", 0)); end = float(seg.get("end_time", start))
            except (TypeError, ValueError):
                continue
            cues.append({"start_time": start, "end_time": max(start, end), "text": text})

    lines: List[str] = []
    for n, cue in enumerate(cues, 1):
        start = float(cue["start_time"]); end = float(cue["end_time"])
        if end < start:
            end = start
        lines.append(str(n))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(str(cue["text"]).strip())
        lines.append("")
    return "\n".join(lines)


def _srt_response(segments: List[Dict[str, Any]], filename: str, words: Optional[List[Dict[str, Any]]] = None) -> Response:
    srt = _segments_to_srt(segments, words)
    if not srt.strip():
        raise HTTPException(404, "Brak napisów do wyeksportowania dla tego shorta.")
    safe_name = re.sub(r'[\\/:"*?<>|]+', "_", filename).strip() or "napisy"
    if not safe_name.lower().endswith(".srt"):
        safe_name += ".srt"
    return Response(
        content=srt.encode("utf-8"),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "Cache-Control": "no-cache"},
    )


@app.get("/api/shorts/srt/{project_id}/{index}")
def get_short_srt(project_id: str, index: int):
    """Download the base short's subtitles as .srt (built from its segments)."""
    _, _, _, short = _load_project_short(project_id, index)
    return _srt_response(short.get("segments", []), _safe_title(short.get("title"), index), short.get("words", []))


@app.get("/api/shorts/srt/{project_id}/{index}/versions/{language_slug}")
def get_short_version_srt(project_id: str, index: int, language_slug: str):
    """Download a rendered version's subtitles as .srt (translated text + timings)."""
    _, _, base_short, _, _, manifest = _load_short_version(project_id, index, language_slug)
    short_data = manifest.get("short_data") or base_short
    title = short_data.get("title") or base_short.get("title")
    return _srt_response(short_data.get("segments", []), f"{_safe_title(title, index)}_{os.path.basename(language_slug)}", short_data.get("words", []))


@app.get("/api/shorts/audio/{project_id}/{index}")
def get_shorts_audio(project_id: str, index: int):
    """Extract (and cache) the rendered short's audio as MP3 for download."""
    safe_pid, _, _, short = _load_project_short(project_id, index)
    rendered = short.get("rendered_file")
    if not rendered:
        raise HTTPException(404, "Short nie został jeszcze wyrenderowany")
    video = _sessions_dir() / safe_pid / "shorts" / os.path.basename(rendered)
    if not video.exists():
        raise HTTPException(404, "Brak pliku wideo shorta")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")
    mp3 = video.with_suffix(".mp3")
    if not mp3.exists() or mp3.stat().st_mtime < video.stat().st_mtime:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(mp3)], check=False, timeout=120)
    if not mp3.exists():
        raise HTTPException(500, "Nie udało się wyodrębnić audio")
    return FileResponse(str(mp3), media_type="audio/mpeg", filename=mp3.name)


@app.get("/api/shorts/projects/{project_id}/shorts/{index}/versions")
def shorts_versions(project_id: str, index: int) -> List[Dict[str, Any]]:
    safe_pid, _, _, _ = _load_project_short(project_id, index)
    prev_cwd = os.getcwd()
    try:
        os.chdir(str(SHORTS_DIR))
        from dubbing_engine import list_rendered_versions  # type: ignore
        versions = list_rendered_versions(safe_pid, index)
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass
    project_root = _sessions_dir() / safe_pid
    versions = _merge_detected_short_versions(project_root, index, versions)
    for version in versions:
        # Surface the audio mode used so the UI can label the version "dubbing" vs "lektor".
        version["audio_mode"] = (version.get("settings") or {}).get("audio_mode") or ""
        for key in ("video_path", "audio_path"):
            p = version.get(key) or ""
            try:
                rel = Path(p).resolve().relative_to(project_root.resolve()).as_posix()
                version[f"{key}_url"] = f"/api/shorts/artifact/{safe_pid}?path={quote(rel)}"
            except Exception:
                version[f"{key}_url"] = ""
        try:
            vp = Path(version.get("video_path", ""))
            ass = vp.with_suffix(".ass")
            version["subtitle_url"] = f"/api/shorts/artifact/{safe_pid}?path={quote(ass.resolve().relative_to(project_root.resolve()).as_posix())}" if ass.exists() else ""
        except Exception:
            version["subtitle_url"] = ""
    return versions


@app.put("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}")
def update_short_version(project_id: str, index: int, language_slug: str, req: ShortsVersionUpdateRequest):
    safe_pid, _, base_short, version_dir, manifest_path, manifest = _load_short_version(project_id, index, language_slug)
    short_data = dict(manifest.get("short_data") or {})
    if not short_data:
        short_data = json.loads(json.dumps(base_short, ensure_ascii=False))
    short_data["segments"] = req.segments
    short_data["words"] = req.words
    short_data["language"] = manifest.get("language", short_data.get("language"))
    manifest["short_data"] = short_data
    manifest["updated_at"] = int(_time.time())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_version_translation_cache(version_dir, manifest, short_data)
    return {"ok": True, "project_id": safe_pid, "short": short_data}


@app.delete("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}")
def delete_short_version(project_id: str, index: int, language_slug: str) -> Dict[str, Any]:
    safe_pid, _, _, version_dir, _, _ = _load_short_version(project_id, index, language_slug)
    removed = str(version_dir)
    # Robust against the macOS/exFAT AppleDouble race that left half-deleted folders
    # which then reappeared as ghost versions. Verify it's actually gone.
    if not _force_rmtree(version_dir) and version_dir.exists():
        raise HTTPException(500, "Nie udało się usunąć wersji z dysku (plik może być w użyciu — zamknij podgląd wideo i spróbuj ponownie).")
    return {"ok": True, "project_id": safe_pid, "removed": removed}


@app.delete("/api/shorts/projects/{project_id}/shorts/{index}/translation-cache")
def clear_short_translation_cache(project_id: str, index: int) -> Dict[str, Any]:
    """Delete every cached subtitle/metadata translation for this short so the next
    dub/translate re-calls the engine from scratch. Fixes a poisoned cache (e.g. a
    half-Polish/half-English mix that survived an engine hiccup) without forcing a
    global TRANSLATION_RULES_VERSION bump. Rendered videos/audio are NOT touched —
    only the `translation_*.json` files in the short's version folders."""
    safe_pid, _, _, _ = _load_project_short(project_id, index)
    prev_cwd = os.getcwd()
    removed = 0
    try:
        os.chdir(str(SHORTS_DIR))
        from dubbing_engine import get_short_versions_dir  # type: ignore
        root = get_short_versions_dir(safe_pid, index)
        root = root if root.is_absolute() else (SHORTS_DIR / root)
        if root.exists():
            for cache_file in root.glob("*/translation_*.json"):
                try:
                    cache_file.unlink()
                    removed += 1
                except Exception:
                    pass
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass
    return {"ok": True, "project_id": safe_pid, "removed": removed}


@app.delete("/api/shorts/projects/{project_id}/shorts/{index}/demucs-cache")
def clear_short_demucs_cache(project_id: str, index: int) -> Dict[str, Any]:
    """Delete the cached Demucs background separation for this short so the next dub
    re-runs it from scratch. Use when the separated track sounds corrupted/cut. Rendered
    videos are NOT touched — only the `_bg_cache` folder."""
    safe_pid, _, _, _ = _load_project_short(project_id, index)
    prev_cwd = os.getcwd()
    removed = 0
    try:
        os.chdir(str(SHORTS_DIR))
        from dubbing_engine import get_short_versions_dir  # type: ignore
        root = get_short_versions_dir(safe_pid, index)
        root = root if root.is_absolute() else (SHORTS_DIR / root)
        bg_cache = root / "_bg_cache"
        if bg_cache.exists():
            _force_rmtree(bg_cache)
            removed = 1
        # Also drop any per-version demucs leftovers from older builds.
        if root.exists():
            for d in root.glob("*/audio/demucs"):
                _force_rmtree(d)
                removed += 1
    finally:
        try:
            os.chdir(prev_cwd)
        except Exception:
            pass
    return {"ok": True, "project_id": safe_pid, "removed": removed}


@app.post("/api/shorts/favorites")
def save_short_favorite(req: ShortsFavoriteRequest) -> Dict[str, Any]:
    favorite_id = _safe_favorite_id(req.favorite_id)
    target_dir = _favorites_dir() / favorite_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: Dict[str, str] = {}
    safe_pid, data, base_short, project_root, manifest = "", {}, {}, None, {}
    short_data = dict(req.short or {})
    if req.kind == "version":
        safe_pid, data, base_short, version_dir, _, manifest = _load_short_version(req.project_id, req.index, req.language_slug or "")
        short_data = dict(manifest.get("short_data") or short_data or base_short)
        copied["video"] = _copy_if_file(manifest.get("video_path"), target_dir)
        copied["audio"] = _copy_if_file(manifest.get("audio_path"), target_dir)
        video_path = Path(manifest.get("video_path", ""))
        if video_path.exists():
            copied["subtitle"] = _copy_if_file(video_path.with_suffix(".ass"), target_dir)
    else:
        safe_pid, _, data, base_short = _load_project_short(req.project_id, req.index)
        short_data = dict(short_data or base_short)
        project_root = _sessions_dir() / safe_pid
        rendered = short_data.get("rendered_file") or base_short.get("rendered_file")
        if rendered:
            rendered_path = Path(str(rendered))
            if not rendered_path.is_absolute():
                rendered_path = project_root / "shorts" / rendered_path.name
            copied["video"] = _copy_if_file(rendered_path, target_dir)
            copied["subtitle"] = _copy_if_file(rendered_path.with_suffix(".ass"), target_dir)

    metadata = {
        "favorite_id": favorite_id,
        "kind": req.kind,
        "project_id": safe_pid or os.path.basename(req.project_id),
        "project_title": req.project_title or data.get("display_name", ""),
        "index": req.index,
        "total": req.total,
        "language_slug": req.language_slug,
        "language": manifest.get("language") if manifest else short_data.get("language"),
        "short": short_data,
        "copied": copied,
        "saved_at": _time.time(),
    }
    (target_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "favorite_id": favorite_id, "folder": str(target_dir), "copied": copied}


@app.delete("/api/shorts/favorites/{favorite_id}")
def delete_short_favorite(favorite_id: str) -> Dict[str, Any]:
    safe_id = _safe_favorite_id(favorite_id)
    target_dir = _favorites_dir() / safe_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    return {"ok": True, "favorite_id": safe_id}


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}/render")
def render_short_version(project_id: str, index: int, language_slug: str, req: ShortsVersionRenderRequest) -> Dict[str, Any]:
    safe_pid, data, base_short, version_dir, manifest_path, manifest = _load_short_version(project_id, index, language_slug)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    audio_path = manifest.get("audio_path", "")
    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(400, "Nie znaleziono audio dubbingu — wygeneruj dubbing ponownie.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")

    full = config_store.load()
    s = {
        **full["shorts"],
        **(data.get("render_settings") or {}),
        **(manifest.get("settings") or {}),
        **(req.settings or {}),
    }

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        from dubbing_engine import update_version_manifest  # type: ignore
        from video_engine import render_short_ffmpeg  # type: ignore

        short_data = json.loads(json.dumps(manifest.get("short_data") or base_short, ensure_ascii=False))
        aspect_ratio = "16:9" if "16:9" in str(s.get("aspect_ratio", "")) else "9:16"
        export_res = _export_res_label(s.get("export_resolution"))
        safe_title = _safe_title(short_data.get("title"), index)
        out_mp4 = version_dir / f"{safe_title}_{os.path.basename(language_slug)}.mp4"
        ass_path = version_dir / f"{safe_title}_{os.path.basename(language_slug)}.ass"

        ctx.progress(0.25, "Budowanie napisów wersji dubbingowej…")
        ass_arg = None
        if s.get("enable_subtitles", True) and short_data.get("segments"):
            _build_short_ass(s, short_data.get("segments", []), short_data.get("words", []), str(ass_path), aspect_ratio)
            ass_arg = str(ass_path)

        logo_path = _resolve_logo_path(s.get("logo_path"))
        logo_settings = _preview_logo_settings(s, logo_path)
        logo_settings["enable_logo"] = bool(s.get("enable_logo"))
        logo_settings["enable_text"] = bool(s.get("enable_text"))

        ctx.progress(0.55, "Renderowanie wersji dubbingowej…")
        _cam = _RenderProgress(ctx, 0.55, 0.43)
        render_short_ffmpeg(
            video_file,
            short_data.get("segments", []),
            str(out_mp4),
            aspect_ratio=aspect_ratio,
            ass_subtitle_file=ass_arg,
            export_res=export_res,
            export_bitrate=int(_num(s.get("export_bitrate"), 15)),
            export_codec=s.get("export_codec", "H.264 (Większa kompatybilność)"),
            face_tracking=bool(s.get("face_tracking")),
            ft_smoothness=int(_num(s.get("ft_smoothness"), 10)),
            ft_recheck=int(_num(s.get("ft_recheck"), 8)),
            ft_zoom=_num(s.get("ft_zoom"), 1.0),
            ft_y_offset=int(_num(s.get("ft_y_offset"), 0)),
            ft_strategy=s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
            ft_tracker=s.get("ft_tracker", "Auto"),
            smart_reframe=bool(s.get("face_tracking")) and bool(s.get("smart_reframe")),
            reframe_speed=int(_num(s.get("reframe_speed"), 50)),
            status_text=_cam, progress_bar=_cam,
            logo_settings=logo_settings,
            audio_override_path=audio_path,
        )
        update_version_manifest(str(version_dir), str(out_mp4), short_data)
        rel = out_mp4.relative_to(_sessions_dir() / safe_pid).as_posix()
        ctx.progress(1.0, "Gotowe — wersja dubbingowa przegenerowana.")
        return {
            "url": f"/api/shorts/artifact/{safe_pid}?path={quote(rel)}",
            "title": short_data.get("title"),
            "language": manifest.get("language"),
            "file": str(out_mp4),
            "short": short_data,
        }

    job = manager.start("shorts.render-version", target)
    return {"job_id": job.id}


def _build_subtitle_version(
    ctx, *, safe_pid, video_file, base_short, source_slug, version_dir, manifest,
    audio_abs, s, engine, api_key, glossary, audio_language, sub_language, index,
    frac0: float = 0.0, frac1: float = 1.0,
) -> Dict[str, Any]:
    """Render ONE subtitle-translated version: same dub audio, new burned subtitles
    in `sub_language`, as a separate version folder `<sourceSlug>-napisy-<subSlug>`.
    Shared by the single and batch endpoints. Progress is scaled into [frac0, frac1]
    so the batch loop can show a smooth per-language bar. Returns a result dict
    (includes a `repair` summary when the translator had to fix segments)."""
    import local_translate  # type: ignore
    from dubbing_engine import language_slug as _slug_fn  # type: ignore
    from video_engine import render_short_ffmpeg  # type: ignore

    def prog(frac: float, msg: str) -> None:
        ctx.progress(frac0 + (frac1 - frac0) * max(0.0, min(1.0, frac)), msg)

    src_short = manifest.get("short_data") or base_short
    translated = json.loads(json.dumps(src_short, ensure_ascii=False))

    # Translate the subtitles + metadata into the new language. Skip the API when
    # the requested subtitle language equals the audio language already.
    if _norm_lang(sub_language) != _norm_lang(audio_language):
        prog(0.10, f"Tłumaczenie napisów na: {sub_language} (silnik: {engine})…")
        ok = local_translate.translate_short(
            translated, sub_language, engine=engine, gemini_api_key=api_key,
            glossary_text=glossary,
        )
        if not ok:
            raise RuntimeError(
                "Nie udało się przetłumaczyć napisów. Sprawdź klucz Gemini lub lokalny "
                "silnik NLLB/Argos w ustawieniach."
            )
    else:
        prog(0.10, "Napisy już w języku audio — przegenerowuję z tym samym tekstem…")
    repair = translated.pop("_translation_repair", None)

    # New, separate version folder: <audioSlug><mode>-napisy-<subSlug>. NOTE:
    # `source_slug` is the path-param STRING (the source version's folder name);
    # `_slug_fn` is the dubbing_engine helper — kept under distinct names so the
    # import never shadows the slug string.
    sub_slug = _slug_fn(sub_language)
    new_slug = f"{os.path.basename(source_slug)}-napisy-{sub_slug}"
    new_dir = version_dir.parent / new_slug
    new_audio_dir = new_dir / "audio"
    new_audio_dir.mkdir(parents=True, exist_ok=True)
    # Copy the dub audio in so the new version is self-contained (survives deleting
    # the source version).
    new_audio = new_audio_dir / Path(audio_abs).name
    shutil.copy2(audio_abs, new_audio)

    aspect_ratio = "16:9" if "16:9" in str(s.get("aspect_ratio", "")) else "9:16"
    export_res = _export_res_label(s.get("export_resolution"))
    safe_title = _safe_title(translated.get("title"), index)
    out_mp4 = new_dir / f"{safe_title}_{new_slug}.mp4"
    ass_path = new_dir / f"{safe_title}_{new_slug}.ass"

    prog(0.45, "Budowanie napisów w nowym języku…")
    ass_arg = None
    if s.get("enable_subtitles", True) and translated.get("segments"):
        _build_short_ass(s, translated.get("segments", []), translated.get("words", []), str(ass_path), aspect_ratio)
        ass_arg = str(ass_path)

    logo_path = _resolve_logo_path(s.get("logo_path"))
    logo_settings = _preview_logo_settings(s, logo_path)
    logo_settings["enable_logo"] = bool(s.get("enable_logo"))
    logo_settings["enable_text"] = bool(s.get("enable_text"))

    prog(0.6, "Renderowanie wersji z nowymi napisami…")
    _cam = _RenderProgress(ctx, 0.6, 0.38)
    render_short_ffmpeg(
        video_file,
        translated.get("segments", []),
        str(out_mp4),
        aspect_ratio=aspect_ratio,
        ass_subtitle_file=ass_arg,
        export_res=export_res,
        export_bitrate=int(_num(s.get("export_bitrate"), 15)),
        export_codec=s.get("export_codec", "H.264 (Większa kompatybilność)"),
        face_tracking=bool(s.get("face_tracking")),
        ft_smoothness=int(_num(s.get("ft_smoothness"), 10)),
        ft_recheck=int(_num(s.get("ft_recheck"), 8)),
        ft_zoom=_num(s.get("ft_zoom"), 1.0),
        ft_y_offset=int(_num(s.get("ft_y_offset"), 0)),
        ft_strategy=s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
        ft_tracker=s.get("ft_tracker", "Auto"),
        smart_reframe=bool(s.get("face_tracking")) and bool(s.get("smart_reframe")),
        reframe_speed=int(_num(s.get("reframe_speed"), 50)),
        status_text=_cam, progress_bar=_cam,
        logo_settings=logo_settings,
        audio_override_path=str(new_audio),
    )

    new_manifest = {
        "language": audio_language,            # audio/voice stays the original dub language
        "language_slug": new_slug,
        "subtitle_language": sub_language,     # but the burned subtitles are this language
        "created_at": int(_time.time()),
        "updated_at": int(_time.time()),
        "settings": manifest.get("settings", s),
        "audio_path": str(new_audio),
        "video_path": str(out_mp4),
        "short_data": {
            "title": translated.get("title", ""),
            "hook_text": translated.get("hook_text", ""),
            "hashtags": translated.get("hashtags", ""),
            "yt_tags": translated.get("yt_tags", ""),
            "segments": translated.get("segments", []),
            "words": translated.get("words", []),
            "score": translated.get("score", 90),
        },
    }
    (new_dir / "manifest.json").write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rel = out_mp4.relative_to(_sessions_dir() / safe_pid).as_posix()
    return {
        "url": f"/api/shorts/artifact/{safe_pid}?path={quote(rel)}",
        "title": translated.get("title"),
        "language": audio_language,
        "subtitle_language": sub_language,
        "language_slug": new_slug,
        "file": str(out_mp4),
        "short": new_manifest["short_data"],
        "repair": repair,
    }


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}/translate-subtitles")
def translate_version_subtitles(project_id: str, index: int, language_slug: str, req: ShortsVersionSubtitleRequest) -> Dict[str, Any]:
    """Take an already-rendered dubbing/lektor version, translate its SUBTITLES
    (and metadata) into another language, and re-render — keeping the SAME dub
    audio. Lands as a new, separate version (e.g. English voice + Dutch subtitles)
    so the original is untouched. Useful for markets we can subtitle but not dub."""
    safe_pid, data, base_short, version_dir, manifest_path, manifest = _load_short_version(project_id, index, language_slug)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    # audio_path is stored relative to SHORTS_DIR (the job cwd at render time);
    # resolve against SHORTS_DIR so the check holds regardless of the server cwd.
    audio_path = manifest.get("audio_path", "")
    audio_abs = audio_path if os.path.isabs(audio_path) else str(SHORTS_DIR / audio_path)
    if not audio_path or not os.path.exists(audio_abs):
        raise HTTPException(400, "Nie znaleziono audio tej wersji — przegeneruj dubbing/lektor najpierw.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")

    full = config_store.load()
    api_key = (full.get("app", {}).get("gemini_api_key") or "").strip()
    engine = _translation_engine(full)
    glossary = (data.get("whisper_glossary") or full.get("app", {}).get("glossary") or "")
    audio_language = manifest.get("language", "")
    sub_language = req.subtitle_language
    s = {
        **full["shorts"],
        **(data.get("render_settings") or {}),
        **(manifest.get("settings") or {}),
        **(req.settings or {}),
    }

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        res = _build_subtitle_version(
            ctx, safe_pid=safe_pid, video_file=video_file, base_short=base_short,
            source_slug=language_slug, version_dir=version_dir, manifest=manifest,
            audio_abs=audio_abs, s=s, engine=engine, api_key=api_key, glossary=glossary,
            audio_language=audio_language, sub_language=sub_language, index=index,
        )
        ctx.progress(1.0, f"Gotowe — napisy {sub_language} na audio {audio_language or '—'}.")
        return res

    job = manager.start("shorts.translate-version-subs", target)
    return {"job_id": job.id}


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/versions/{language_slug}/translate-subtitles-batch")
def translate_version_subtitles_batch(project_id: str, index: int, language_slug: str, req: ShortsVersionSubtitleBatchRequest) -> Dict[str, Any]:
    """Batch variant: from ONE rendered dub/lektor version, render a separate
    subtitle-translated version for EACH requested language in a single job (same
    audio, new burned subs). One language failing does not abort the rest — each
    result carries its own ok/error so the UI can report per-language outcomes."""
    safe_pid, data, base_short, version_dir, manifest_path, manifest = _load_short_version(project_id, index, language_slug)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    audio_path = manifest.get("audio_path", "")
    audio_abs = audio_path if os.path.isabs(audio_path) else str(SHORTS_DIR / audio_path)
    if not audio_path or not os.path.exists(audio_abs):
        raise HTTPException(400, "Nie znaleziono audio tej wersji — przegeneruj dubbing/lektor najpierw.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")

    # De-dupe while preserving order; drop blanks.
    seen: set = set()
    langs: List[str] = []
    for lang in (req.subtitle_languages or []):
        key = _norm_lang(lang)
        if lang and lang.strip() and key not in seen:
            seen.add(key)
            langs.append(lang.strip())
    if not langs:
        raise HTTPException(400, "Nie wybrano żadnego języka napisów.")

    full = config_store.load()
    api_key = (full.get("app", {}).get("gemini_api_key") or "").strip()
    engine = _translation_engine(full)
    glossary = (data.get("whisper_glossary") or full.get("app", {}).get("glossary") or "")
    audio_language = manifest.get("language", "")
    s = {
        **full["shorts"],
        **(data.get("render_settings") or {}),
        **(manifest.get("settings") or {}),
        **(req.settings or {}),
    }

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        n = len(langs)
        results: List[Dict[str, Any]] = []
        for k, lang in enumerate(langs):
            f0, f1 = k / n, (k + 1) / n
            ctx.progress(f0, f"({k + 1}/{n}) Napisy: {lang}…")
            try:
                res = _build_subtitle_version(
                    ctx, safe_pid=safe_pid, video_file=video_file, base_short=base_short,
                    source_slug=language_slug, version_dir=version_dir, manifest=manifest,
                    audio_abs=audio_abs, s=s, engine=engine, api_key=api_key, glossary=glossary,
                    audio_language=audio_language, sub_language=lang, index=index,
                    frac0=f0, frac1=f1,
                )
                results.append({"ok": True, **res})
            except Exception as e:  # noqa: BLE001
                print(f"[dubcut-backend] napisy wsadowe '{lang}' nie powiodły się: {e}", file=sys.stderr)
                results.append({"ok": False, "subtitle_language": lang, "error": str(e)})
        done = sum(1 for r in results if r.get("ok"))
        ctx.progress(1.0, f"Gotowe — {done}/{n} wersji napisów.")
        return {"results": results, "project_id": safe_pid, "ok_count": done, "total": n}

    job = manager.start("shorts.translate-version-subs-batch", target)
    return {"job_id": job.id}


@app.post("/api/shorts/projects/{project_id}/shorts/{index}/translate-subtitles")
def translate_short_subtitles(project_id: str, index: int, req: ShortsVersionSubtitleRequest) -> Dict[str, Any]:
    """Translate the BASE short's burned subtitles (and metadata: title/opis/hashtagi/tagi)
    into another language, KEEP the original speech/audio, and render a brand-new video.
    Lands as a separate version under the short (e.g. polska mowa + angielskie napisy) so
    the base short is never modified. This is what the per-short „Przetłumacz" button does:
    translate → auto-render → new clip appears below, correctly labelled
    „Napisy: <docelowy> · Język: <oryginał>"."""
    safe_pid, _, data, short = _load_project_short(project_id, index)
    video_file = data.get("video_file", "")
    if not video_file or not os.path.exists(video_file):
        raise HTTPException(400, "Plik źródłowy wideo nie istnieje — nie można renderować.")
    if not _ffmpeg_ok():
        raise HTTPException(503, "FFmpeg nie jest dostępny")
    sub_language = (req.subtitle_language or "").strip()
    if not sub_language or sub_language == "Brak (Oryginał)":
        raise HTTPException(400, "Wybierz język docelowy napisów (np. Angielski) — „Brak (Oryginał)” zostawia napisy bez zmian.")

    full = config_store.load()
    api_key = (full.get("app", {}).get("gemini_api_key") or "").strip()
    engine = _translation_engine(full)
    glossary = (data.get("whisper_glossary") or full.get("app", {}).get("glossary") or "")
    # Speech language never changes here — it is the short's original spoken language.
    audio_language = short.get("source_language") or data.get("source_language") or short.get("language") or "Polski"
    s = {
        **full["shorts"],
        **(data.get("render_settings") or {}),
        **(req.settings or {}),
    }

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        import local_translate  # type: ignore
        from dubbing_engine import language_slug as _slug_fn, get_short_versions_dir  # type: ignore
        from video_engine import render_short_ffmpeg  # type: ignore
        from utils import get_ffmpeg_path  # type: ignore

        translated = json.loads(json.dumps(short, ensure_ascii=False))
        # ALWAYS run translate_short into the requested language — never short-circuit
        # on "target == source label". translate_short translates from the short's
        # ORIGINAL (backed-up) text and no-ops cheaply when the original is already in
        # the target language. The old skip compared the target to the SPOKEN language
        # label, so on a short whose base text had been mutated to another language by
        # an earlier build, "translate to Polish" was skipped and the English text was
        # kept verbatim — title/opis/hashtagi/tagi + napisy all stayed English.
        ctx.progress(0.10, f"Tłumaczenie napisów i metadanych na: {sub_language} (silnik: {engine})…")
        ok = local_translate.translate_short(
            translated, sub_language, engine=engine, gemini_api_key=api_key,
            glossary_text=glossary,
        )
        if not ok:
            raise RuntimeError(
                "Nie udało się przetłumaczyć napisów. Sprawdź klucz Gemini lub lokalny "
                "silnik NLLB/Argos w Ustawieniach → Środowisko lokalne."
            )
        repair = translated.pop("_translation_repair", None)

        versions_root = get_short_versions_dir(safe_pid, index)
        versions_root = versions_root if versions_root.is_absolute() else (SHORTS_DIR / versions_root)
        new_slug = f"oryginal-napisy-{_slug_fn(sub_language)}"
        new_dir = (versions_root / new_slug).resolve()
        new_dir.mkdir(parents=True, exist_ok=True)

        aspect_ratio = "16:9" if "16:9" in str(s.get("aspect_ratio", "")) else "9:16"
        export_res = _export_res_label(s.get("export_resolution"))
        safe_title = _safe_title(translated.get("title"), index)
        out_mp4 = new_dir / f"{safe_title}_{new_slug}.mp4"
        ass_path = new_dir / f"{safe_title}_{new_slug}.ass"

        ctx.progress(0.40, "Budowanie napisów w nowym języku…")
        ass_arg = None
        if s.get("enable_subtitles", True) and translated.get("segments"):
            _build_short_ass(s, translated.get("segments", []), translated.get("words", []), str(ass_path), aspect_ratio)
            ass_arg = str(ass_path)

        logo_path = _resolve_logo_path(s.get("logo_path"))
        logo_settings = _preview_logo_settings(s, logo_path)
        logo_settings["enable_logo"] = bool(s.get("enable_logo"))
        logo_settings["enable_text"] = bool(s.get("enable_text"))

        ctx.progress(0.55, "Renderowanie wideo z nowymi napisami (oryginalna mowa)…")
        _cam = _RenderProgress(ctx, 0.55, 0.43)
        render_short_ffmpeg(
            video_file,
            translated.get("segments", []),
            str(out_mp4),
            aspect_ratio=aspect_ratio,
            ass_subtitle_file=ass_arg,
            export_res=export_res,
            export_bitrate=int(_num(s.get("export_bitrate"), 15)),
            export_codec=s.get("export_codec", "H.264 (Większa kompatybilność)"),
            face_tracking=bool(s.get("face_tracking")),
            ft_smoothness=int(_num(s.get("ft_smoothness"), 10)),
            ft_recheck=int(_num(s.get("ft_recheck"), 8)),
            ft_zoom=_num(s.get("ft_zoom"), 1.0),
            ft_y_offset=int(_num(s.get("ft_y_offset"), 0)),
            ft_strategy=s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
            ft_tracker=s.get("ft_tracker", "Auto"),
            smart_reframe=bool(s.get("face_tracking")) and bool(s.get("smart_reframe")),
            reframe_speed=int(_num(s.get("reframe_speed"), 50)),
            status_text=_cam, progress_bar=_cam,
            logo_settings=logo_settings,
            # NO audio_override_path → the original speech is preserved.
        )
        if not out_mp4.exists():
            raise RuntimeError("Render nie wyprodukował pliku wyjściowego.")

        # Extract the rendered (original) audio so the version is self-contained and
        # „Przegeneruj" / further per-version subtitle translations have an audio track
        # to reuse (those endpoints require manifest.audio_path).
        audio_path_str = ""
        try:
            audio_dir = new_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_out = audio_dir / f"{safe_title}_{new_slug}.m4a"
            subprocess.run(
                [get_ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(out_mp4),
                 "-vn", "-c:a", "aac", "-b:a", "192k", str(audio_out)],
                check=True,
            )
            audio_path_str = str(audio_out)
        except Exception as _e:  # noqa: BLE001
            print(f"[dubcut-backend] ekstrakcja oryginalnego audio nieudana: {_e}", file=sys.stderr)

        new_manifest = {
            "language": audio_language,           # speech stays the original language
            "language_slug": new_slug,
            "subtitle_language": sub_language,    # burned subtitles are the target language
            "created_at": int(_time.time()),
            "updated_at": int(_time.time()),
            # Persist the ORIGINAL-audio mode so the version card doesn't mislabel this
            # as "(dubbing)" (audioModeTag keys off settings.audio_mode). This is a
            # subtitles-only version — the speech is untouched.
            "settings": {**s, "audio_mode": "Oryginalne audio"},
            "audio_path": audio_path_str,
            "video_path": str(out_mp4),
            "short_data": {
                "title": translated.get("title", ""),
                "hook_text": translated.get("hook_text", ""),
                "hashtags": translated.get("hashtags", ""),
                "yt_tags": translated.get("yt_tags", ""),
                "segments": translated.get("segments", []),
                "words": translated.get("words", []),
                "score": translated.get("score", 90),
            },
        }
        (new_dir / "manifest.json").write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        rel = out_mp4.relative_to((_sessions_dir() / safe_pid).resolve()).as_posix()
        ctx.progress(1.0, f"Gotowe — napisy {sub_language} na oryginalnej mowie ({audio_language}).")
        return {
            "url": f"/api/shorts/artifact/{safe_pid}?path={quote(rel)}",
            "title": translated.get("title"),
            "language": audio_language,
            "subtitle_language": sub_language,
            "language_slug": new_slug,
            "file": str(out_mp4),
            "short": new_manifest["short_data"],
            "repair": repair,
        }

    job = manager.start("shorts.translate-base-subs", target)
    return {"job_id": job.id}


def _write_version_translation_cache(version_dir: Path, manifest: Dict[str, Any], short_data: Dict[str, Any]) -> None:
    slug = manifest.get("language_slug") or version_dir.name
    payload = {
        "language": manifest.get("language", slug),
        "rules_version": manifest.get("rules_version", 0),
        "source_hash": "",
        "title": short_data.get("title", ""),
        "hook_text": short_data.get("hook_text", ""),
        "hashtags": short_data.get("hashtags", ""),
        "yt_tags": short_data.get("yt_tags", ""),
        "segments": short_data.get("segments", []),
        "words": short_data.get("words", []),
    }
    try:
        (version_dir / f"translation_{slug}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _merge_detected_short_versions(project_root: Path, index: int, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recover rendered dubbing versions even when the manifest is incomplete or missing."""
    root = project_root / "short_versions" / f"short_{index:02d}"
    if not root.exists():
        return versions

    by_dir = {str(Path(v.get("dir", "")).resolve()): v for v in versions if v.get("dir")}
    slug_names = {
        "en": "Angielski",
        "de": "Niemiecki",
        "fr": "Francuski",
        "es": "Hiszpański",
        "it": "Włoski",
        "pt": "Portugalski",
        "zh": "Chiński",
        "ru": "Rosyjski",
        "pl": "Polski",
    }

    for lang_dir in root.iterdir():
        if not lang_dir.is_dir() or _is_junk_name(lang_dir.name):
            continue
        manifest: Dict[str, Any] = {}
        manifest_path = lang_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        elif str(Path(lang_dir).resolve()) not in by_dir:
            # No manifest AND not already listed → this is a deletion remnant (a
            # delete that left leftover media behind), NOT a real version. Do not
            # resurrect it as a ghost entry with empty metadata.
            continue

        video_path = manifest.get("video_path", "")
        if not video_path or not Path(video_path).exists():
            mp4s = sorted(
                [p for p in _real_files(lang_dir, "*.mp4")],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if mp4s:
                video_path = str(mp4s[0])

        if not video_path:
            continue

        slug = manifest.get("language_slug") or lang_dir.name
        short_data = manifest.get("short_data") or {}
        if not short_data:
            translation_path = lang_dir / f"translation_{slug}.json"
            if translation_path.exists():
                try:
                    cached = json.loads(translation_path.read_text(encoding="utf-8"))
                    short_data = {
                        "title": cached.get("title", ""),
                        "hook_text": cached.get("hook_text", ""),
                        "hashtags": cached.get("hashtags", ""),
                        "yt_tags": cached.get("yt_tags", ""),
                        "segments": cached.get("segments", []),
                        "words": cached.get("words", []),
                    }
                except Exception:
                    short_data = {}

        key = str(lang_dir.resolve())
        recovered = {
            "language": manifest.get("language") or slug_names.get(slug, slug),
            "language_slug": slug,
            "subtitle_language": manifest.get("subtitle_language") or manifest.get("language") or slug_names.get(slug, slug),
            "created_at": manifest.get("created_at") or int(Path(video_path).stat().st_mtime),
            "updated_at": manifest.get("updated_at") or int(Path(video_path).stat().st_mtime),
            "video_path": video_path,
            "audio_path": manifest.get("audio_path", ""),
            "settings": manifest.get("settings", {}),
            "short_data": short_data,
            "dir": str(lang_dir),
        }
        if key in by_dir:
            by_dir[key].update({k: v for k, v in recovered.items() if v})
        else:
            versions.append(recovered)
            by_dir[key] = recovered

    return sorted(versions, key=lambda x: x.get("updated_at", x.get("created_at", 0)), reverse=True)


@app.get("/api/shorts/artifact/{project_id}")
def shorts_artifact(project_id: str, path: str):
    project_root = (_sessions_dir() / os.path.basename(project_id)).resolve()
    target = (project_root / path).resolve()
    if not _path_within(target, project_root) or not target.exists() or not target.is_file():
        raise HTTPException(404, "Brak pliku")
    media_map = {
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".ass": "text/plain; charset=utf-8",
        ".json": "application/json",
    }
    media = media_map.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(str(target), media_type=media, filename=target.name, headers={"Cache-Control": "no-cache"})


def _ensure_gemini_key_valid(api_key: str) -> None:
    """Raise a clear, user-facing error if the Gemini key is missing or rejected."""
    if not api_key:
        raise RuntimeError("Brak klucza API Gemini. Wpisz go w Ustawieniach → Klucze API.")
    try:
        from google import genai  # type: ignore
        client = genai.Client(api_key=api_key)
        next(iter(client.models.list()), None)  # cheap, auth-touching, no token cost
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if any(t in msg for t in ("API_KEY_INVALID", "API key not valid", "PERMISSION_DENIED", "UNAUTHENTICATED")):
            raise RuntimeError(
                "Klucz API Gemini jest nieprawidłowy lub odrzucony. Sprawdź go w Ustawieniach → Klucze API."
            ) from exc
        # Network/transient issues: don't block — let the main call surface them.


def _faster_whisper_fallback(video_file: Any, lang_code: Optional[str]):
    """Explicit faster-whisper fallback when the primary Whisper backend fails."""
    from faster_whisper import WhisperModel  # type: ignore
    from ai_processor import _preprocess_audio, _transcribe_faster_whisper, _normalize_word_timings  # type: ignore

    processed = _preprocess_audio(str(video_file))
    tmp_created = processed != str(video_file)
    try:
        model = WhisperModel("medium", device="cpu", compute_type="int8")
        transcript, words = _transcribe_faster_whisper(processed, model, lang_code)
        return transcript, _normalize_word_timings(words)
    finally:
        if tmp_created and os.path.exists(processed):
            try:
                os.remove(processed)
            except Exception:
                pass


class DubRequest(BaseModel):
    source: str
    settings: Optional[Dict[str, Any]] = None


class DubAnalyzeRequest(BaseModel):
    source: str
    settings: Optional[Dict[str, Any]] = None
    force: bool = False  # re-run Whisper from scratch (ignore cache / reused session)


class DubTranslateRequest(BaseModel):
    session: str
    segments: List[Dict[str, Any]]
    settings: Optional[Dict[str, Any]] = None


class DubRenderRequest(BaseModel):
    session: str
    segments: List[Dict[str, Any]]
    settings: Optional[Dict[str, Any]] = None


class DubSubtitlesBatchRequest(BaseModel):
    session: str
    segments: List[Dict[str, Any]]
    languages: List[str] = []
    include_original: bool = False
    settings: Optional[Dict[str, Any]] = None


def _dub_settings(req_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    full = config_store.load()
    s = {**full["dub"], **(req_settings or {})}
    s["translation_engine"] = str(full.get("app", {}).get("translation_engine", "nllb")).lower()
    # Voice engine is a global app setting (Qwen ↔ OmniVoice) — inject it so every
    # module (dubbing, Text→Audio) routes synthesis through the chosen engine.
    if not s.get("tts_engine"):
        s["tts_engine"] = str(full.get("app", {}).get("tts_engine", "qwen")).lower()
    if not s.get("gemini_api_key"):
        s["gemini_api_key"] = full["app"].get("gemini_api_key", "")
    if not (s.get("proper_name_glossary") or "").strip():
        s["proper_name_glossary"] = full["app"].get("glossary", "")
    return s


def _dub_session_dir(session: str) -> Path:
    d = (_dub_output_dir() / os.path.basename(session)).resolve()
    if not str(d).startswith(str(_dub_output_dir().resolve()) + os.sep) or not (d / "session.json").exists():
        raise HTTPException(404, "Nieznana sesja dubbingu — najpierw uruchom analizę.")
    return d


@app.get("/api/omnivoice/status")
def omnivoice_status() -> Dict[str, Any]:
    return omnivoice_engine.engine_status(config_store.load().get("app"))


@app.post("/api/omnivoice/install")
def omnivoice_install() -> Dict[str, Any]:
    """Install the OmniVoice engine (venv + package + model) as a background job."""
    def target(ctx) -> Any:
        return omnivoice_engine.install(ctx, prefetch_model=True)

    job = manager.start("omnivoice.install", target)
    return {"job_id": job.id}


@app.post("/api/dub/analyze")
def dub_analyze(req: DubAnalyzeRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    from datetime import datetime
    out_dir = _dub_output_dir() / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.step("Transkrypcja…")
        import dub_pipeline  # type: ignore
        result = dub_pipeline.analyze_dub(req.source, s, ctx, output_dir=out_dir, force=req.force)
        result["original_url"] = f"/api/dub/source?session={quote(str(result.get('session', '')))}"
        return result

    job = manager.start("dub.analyze", target)
    return {"job_id": job.id}


@app.get("/api/dub/source")
def dub_source(session: str):
    """Stream the original (downloaded/local) video of a dub session for preview."""
    sess = _dub_session_dir(session)
    try:
        manifest = json.loads((sess / "session.json").read_text(encoding="utf-8"))
        video = manifest.get("video_file", "")
    except Exception:
        video = ""
    if not video or not os.path.exists(video):
        raise HTTPException(404, "Brak pliku źródłowego")
    ext = os.path.splitext(video)[1].lower()
    media = "video/mp4" if ext in (".mp4", ".m4v", ".mov") else "application/octet-stream"
    return FileResponse(video, media_type=media, headers={"Cache-Control": "no-cache"})


@app.post("/api/dub/translate")
def dub_translate(req: DubTranslateRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    sess = _dub_session_dir(req.session)

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        import dub_pipeline  # type: ignore
        return dub_pipeline.translate_dub(sess, req.segments, s, ctx)

    job = manager.start("dub.translate", target)
    return {"job_id": job.id}


@app.post("/api/dub/render")
def dub_render(req: DubRenderRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    sess = _dub_session_dir(req.session)
    ref_audio_path = ""
    if s.get("voice_source") == "Sklonowany głos (własna próbka)":
        sid = str(s.get("selected_voice_id") or "")
        match = next((v for v in _list_voice_samples() if v["id"] == sid), None)
        ref_audio_path = match["path"] if match else ""

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.step("Inicjalizacja renderowania dubbingu…")
        import dub_pipeline  # type: ignore
        result = dub_pipeline.render_dub(sess, req.segments, s, ctx, ref_audio_path=ref_audio_path)
        rel = Path(result["file"]).resolve().relative_to(config_store.work_root().resolve()).as_posix()
        result["url"] = f"/api/dub/artifact?path={quote(rel)}"
        if result.get("subtitle"):
            try:
                srel = Path(result["subtitle"]).resolve().relative_to(config_store.work_root().resolve()).as_posix()
                result["subtitle_url"] = f"/api/dub/artifact?path={quote(srel)}"
            except Exception:
                pass
        return result

    job = manager.start("dub.render", target)
    return {"job_id": job.id}


@app.post("/api/dub/subtitles")
def dub_subtitles(req: DubRenderRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    sess = _dub_session_dir(req.session)

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        import dub_pipeline  # type: ignore
        result = dub_pipeline.subtitles_dub(sess, req.segments, s, ctx)
        for key, urlkey in (("subtitle", "subtitle_url"), ("vtt", "vtt_url")):
            if result.get(key):
                try:
                    rel = Path(result[key]).resolve().relative_to(config_store.work_root().resolve()).as_posix()
                    result[urlkey] = f"/api/dub/artifact?path={quote(rel)}"
                except Exception:
                    pass
        return result

    job = manager.start("dub.subtitles", target)
    return {"job_id": job.id}


class TtsTranslateRequest(BaseModel):
    text: str
    target_lang: str
    settings: Optional[Dict[str, Any]] = None


class TtsGenerateRequest(BaseModel):
    text: str
    settings: Optional[Dict[str, Any]] = None


def _tts_output_dir() -> Path:
    return config_store.module_dir("tts")


def _tts_url(run_id: str, fmt: str, *, dl: bool = False) -> str:
    qs = f"?id={quote(run_id)}&format={quote(fmt)}"
    if dl:
        qs += "&dl=1"
    return f"/api/tts/audio{qs}"


def _tts_title(text: str) -> str:
    title = re.sub(r"\s+", " ", str(text or "")).strip()
    if not title:
        return "Audio z tekstu"
    return title[:80] + ("..." if len(title) > 80 else "")


def _tts_manifest(run_dir: Path, fallback_id: str = "") -> Dict[str, Any]:
    man = run_dir / "manifest.json"
    data: Dict[str, Any] = {}
    if man.exists():
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    stat = run_dir.stat()
    run_id = data.get("id") or fallback_id or run_dir.name
    mp3 = run_dir / "speech.mp3"
    wav = run_dir / "speech.wav"
    return {
        "id": run_id,
        "title": data.get("title") or run_id,
        "language": data.get("language") or "",
        "created_at": data.get("created_at") or _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(stat.st_mtime)),
        "mtime": stat.st_mtime,
        "text": data.get("text") or "",
        "settings": data.get("settings") or {},
        "mp3": mp3.exists(),
        "wav": wav.exists(),
        "mp3_url": _tts_url(run_id, "mp3") if mp3.exists() else "",
        "wav_url": _tts_url(run_id, "wav") if wav.exists() else "",
        "path": str(run_dir.resolve()),
    }


def _tts_run_dir(run_id: str) -> Optional[Path]:
    root = _tts_output_dir().resolve()
    safe_id = os.path.basename(str(run_id or ""))
    if not safe_id:
        return None
    direct = (root / safe_id).resolve()
    if _path_within(direct, root) and direct.exists() and direct.is_dir():
        return direct
    if not root.exists():
        return None
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("._"):
            continue
        if child.name == safe_id:
            return child.resolve()
        man = child / "manifest.json"
        if not man.exists():
            continue
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("id") == run_id or data.get("title") == run_id:
            return child.resolve()
    return None


@app.post("/api/tts/translate")
def tts_translate(req: TtsTranslateRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        import tts_pipeline  # type: ignore
        return tts_pipeline.translate_text(req.text, req.target_lang, s, ctx)

    job = manager.start("tts.translate", target)
    return {"job_id": job.id}


@app.post("/api/tts/generate")
def tts_generate(req: TtsGenerateRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    ref_audio_path = ""
    if s.get("voice_source") == "Sklonowany głos (własna próbka)":
        sid = str(s.get("selected_voice_id") or "")
        match = next((v for v in _list_voice_samples() if v["id"] == sid), None)
        ref_audio_path = match["path"] if match else ""
    from datetime import datetime
    run_id = f"tts_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')}"
    out_dir = _tts_output_dir() / run_id

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        ctx.step("Synteza mowy…")
        import tts_pipeline  # type: ignore
        result = tts_pipeline.generate_tts(req.text, s, ctx, output_dir=out_dir, ref_audio_path=ref_audio_path)
        manifest = {
            "id": run_id,
            "title": _tts_title(req.text),
            "text": req.text,
            "language": result.get("language", ""),
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "settings": {
                "voice_source": s.get("voice_source"),
                "dubbing_qwen_speaker": s.get("dubbing_qwen_speaker"),
                "selected_voice_id": s.get("selected_voice_id"),
                "voiceover_style": s.get("voiceover_style"),
                "tts_model": s.get("tts_model"),
            },
            "files": {
                "mp3": Path(result.get("mp3", "")).name if result.get("mp3") else "",
                "wav": Path(result.get("wav", "")).name if result.get("wav") else "",
            },
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            result["url"] = _tts_url(run_id, "mp3")
            result["mp3_url"] = _tts_url(run_id, "mp3")
            result["wav_url"] = _tts_url(run_id, "wav")
            result["id"] = run_id
            result["history_item"] = _tts_manifest(out_dir, run_id)
        except Exception:
            pass
        return result

    job = manager.start("tts.generate", target)
    return {"job_id": job.id}


@app.get("/api/tts/history")
def tts_history() -> List[Dict[str, Any]]:
    root = _tts_output_dir()
    if not root.exists():
        return []
    items: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if not d.is_dir() or d.name.startswith("._"):
            continue
        if not (d / "speech.mp3").exists() and not (d / "speech.wav").exists():
            continue
        items.append(_tts_manifest(d, d.name))
    return items


@app.get("/api/tts/audio")
def tts_audio(id: str, format: str = "mp3", dl: int = 0):
    root = _tts_output_dir().resolve()
    fmt = "wav" if str(format).lower() == "wav" else "mp3"
    run_dir = _tts_run_dir(id)
    if not run_dir:
        raise HTTPException(404, "Brak pliku audio")
    safe_id = run_dir.name
    target = (run_dir / f"speech.{fmt}").resolve()
    if not _path_within(target, root) or not target.exists():
        raise HTTPException(404, "Brak pliku audio")
    media = "audio/wav" if fmt == "wav" else "audio/mpeg"
    if dl:
        return FileResponse(str(target), media_type=media, filename=f"{safe_id}.{fmt}")
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "no-cache"})


@app.delete("/api/tts/history/{run_id}")
def tts_history_delete(run_id: str) -> Dict[str, Any]:
    root = _tts_output_dir().resolve()
    target = _tts_run_dir(run_id)
    if not _path_within(target, root) or not target.exists() or not target.is_dir():
        raise HTTPException(404, "Brak generacji audio")
    removed = str(target)
    if not _force_rmtree(target) and target.exists():
        raise HTTPException(500, "Nie udało się usunąć pliku z dysku")
    return {"ok": True, "id": run_id, "removed": removed}


@app.post("/api/dub/subtitles-batch")
def dub_subtitles_batch(req: DubSubtitlesBatchRequest) -> Dict[str, Any]:
    s = _dub_settings(req.settings)
    sess = _dub_session_dir(req.session)

    def target(ctx) -> Any:
        os.chdir(str(SHORTS_DIR))
        import dub_pipeline  # type: ignore
        result = dub_pipeline.subtitles_batch(
            sess, req.segments, req.languages, s, ctx, include_original=req.include_original,
        )
        for item in result.get("results", []):
            for key, urlkey in (("srt", "srt_url"), ("vtt", "vtt_url")):
                if item.get(key):
                    try:
                        rel = Path(item[key]).resolve().relative_to(config_store.work_root().resolve()).as_posix()
                        item[urlkey] = f"/api/dub/artifact?path={quote(rel)}"
                    except Exception:
                        pass
        return result

    job = manager.start("dub.subtitles-batch", target)
    return {"job_id": job.id}


class DubTranscriptRequest(BaseModel):
    session: str
    segments: List[Dict[str, Any]]
    words: Optional[List[Dict[str, Any]]] = None


@app.post("/api/dub/transcript")
def dub_save_transcript(req: DubTranscriptRequest) -> Dict[str, Any]:
    """Autosave the hand-edited original transcript to the session (synchronous, fast)."""
    sess = _dub_session_dir(req.session)
    os.chdir(str(SHORTS_DIR))
    import dub_pipeline  # type: ignore
    return dub_pipeline.save_transcript(sess, req.segments, req.words)


def _dub_output_dir() -> Path:
    # Dubbing + subtitles sessions live in the "Dubbing i napisy" work-folder category.
    return config_store.module_dir("dub")


@app.get("/api/dub/artifact")
def dub_artifact(path: str, dl: int = 0):
    # Serve from the work root so both dub ("Dubbing i napisy/…") and TTS ("Tekst do audio/…")
    # artifacts resolve under one sandbox.
    root = config_store.work_root().resolve()
    target = (root / path).resolve()
    if not _path_within(target, root) or not target.exists():
        raise HTTPException(404, "Brak pliku")
    media = "video/mp4" if target.suffix.lower() == ".mp4" else "application/octet-stream"
    # dl=1 → force download with the real, language-suffixed filename.
    if dl:
        return FileResponse(str(target), media_type=media, filename=target.name)
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "no-cache"})


def _dub_artifact_url(abs_path: str) -> str:
    try:
        rel = Path(abs_path).resolve().relative_to(config_store.work_root().resolve()).as_posix()
        return f"/api/dub/artifact?path={quote(rel)}"
    except Exception:
        return ""


@app.get("/api/dub/projects")
def dub_projects() -> List[Dict[str, Any]]:
    """List finished dubbing projects (one folder per run, with a manifest)."""
    root = _dub_output_dir()
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        man = d / "manifest.json"
        if not d.is_dir() or not man.exists():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            continue
        video = m.get("video", "")
        if not video or not os.path.exists(video):
            continue
        out.append({
            "id": d.name,
            "title": m.get("title", d.name),
            "language": m.get("language", ""),
            "mix_mode": m.get("mix_mode", ""),
            "created_at": m.get("created_at", 0),
            "video_url": _dub_artifact_url(video),
            "subtitle_url": _dub_artifact_url(m.get("subtitle", "")) if m.get("subtitle") else "",
        })
    return out


@app.get("/api/subs/projects")
def subs_projects() -> List[Dict[str, Any]]:
    """List subtitle projects. Driven off each session's `session.json` so a video shows
    up in the History tab as soon as it's TRANSCRIBED (not only after subtitles are
    generated), and exposes the source-video URL + original transcript so the History
    player can play the film with switchable subtitle tracks."""
    root = _dub_output_dir()
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for dirp in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        sj = dirp / "session.json"
        if not dirp.is_dir() or not sj.exists():
            continue
        try:
            s = json.loads(sj.read_text(encoding="utf-8"))
        except Exception:
            continue
        orig = s.get("original_segments") or []
        if not orig:
            continue  # not a finished transcription yet
        video_file = str(s.get("video_file") or "")
        source_exists = bool(video_file and os.path.exists(video_file))
        original_segments = [
            {"id": i, "start": float(seg.get("start_time", seg.get("start", 0.0))),
             "end": float(seg.get("end_time", seg.get("end", 0.0))), "text": str(seg.get("text", ""))}
            for i, seg in enumerate(orig)
        ]
        # Generated language versions (if any subtitles were produced).
        versions = []
        man = dirp / "subs_manifest.json"
        if man.exists():
            try:
                for r in json.loads(man.read_text(encoding="utf-8")).get("results", []):
                    v = {"language": r.get("language", "")}
                    for key, urlkey in (("srt", "srt_url"), ("vtt", "vtt_url")):
                        p = r.get(key, "")
                        if p and os.path.exists(p):
                            v[urlkey] = _dub_artifact_url(p)
                    if v.get("srt_url") or v.get("vtt_url"):
                        versions.append(v)
            except Exception:
                pass
        out.append({
            "id": dirp.name,
            "title": s.get("title") or dirp.name,
            "created_at": int(s.get("created_at") or (dirp.stat().st_mtime if dirp.exists() else 0)),
            "source_lang": s.get("source_lang", ""),
            "source_exists": source_exists,
            "source_url": f"/api/dub/source?session={quote(dirp.name)}" if source_exists else None,
            "is_youtube": bool(s.get("is_youtube")),
            "source": str(s.get("source") or ""),
            "original_segments": original_segments,
            "versions": versions,
        })
    return out


@app.delete("/api/subs/projects/{project_id}")
def delete_subs_project(project_id: str) -> Dict[str, Any]:
    root = _dub_output_dir().resolve()
    target = (root / os.path.basename(project_id)).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_dir():
        raise HTTPException(404, "Brak projektu")
    shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}


@app.delete("/api/subs/projects/{project_id}/versions")
def delete_subs_version(project_id: str, language: str = "") -> Dict[str, Any]:
    """Delete generated subtitle versions for a project, removing the SRT/VTT files
    from disk and pruning the manifest. Pass `language` (the display label) to delete a
    single version; omit it (or pass empty) to delete ALL generated subtitle versions.
    The transcription/session itself is kept so the film stays in History."""
    root = _dub_output_dir().resolve()
    target = (root / os.path.basename(project_id)).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_dir():
        raise HTTPException(404, "Brak projektu")
    man_path = target / "subs_manifest.json"
    if not man_path.exists():
        return {"ok": True, "removed": 0}
    try:
        data = json.loads(man_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "removed": 0}
    wanted = language.strip()
    kept: List[Dict[str, Any]] = []
    removed = 0
    for r in data.get("results", []):
        if not wanted or r.get("language", "") == wanted:
            for key in ("srt", "vtt"):
                p = r.get(key, "")
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            removed += 1
        else:
            kept.append(r)
    data["results"] = kept
    man_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "removed": removed}


@app.delete("/api/dub/projects/{project_id}")
def delete_dub_project(project_id: str) -> Dict[str, Any]:
    root = _dub_output_dir().resolve()
    target = (root / os.path.basename(project_id)).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_dir():
        raise HTTPException(404, "Brak projektu")
    shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Image (FLUX/MFLUX) + Video (LTX) generators
# ---------------------------------------------------------------------------
class VideoGenRequest(BaseModel):
    settings: Optional[Dict[str, Any]] = None


class PromptHelperRequest(BaseModel):
    text: str
    kind: str = "image"   # "image" | "video"


def _videogen_settings(section: str, req_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    full = config_store.load()
    return {**full.get(section, {}), **(req_settings or {})}


@app.get("/api/videogen/status")
def videogen_status() -> Dict[str, Any]:
    return videogen_pipeline.engine_status(config_store.load().get("app"))


@app.get("/api/videogen/file")
def videogen_file(path: str, dl: int = 0):
    target = videogen_pipeline.find_media(path)
    if not target:
        raise HTTPException(404, "Brak pliku")
    suffix = target.suffix.lower()
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".mp4": "video/mp4"}.get(suffix, "application/octet-stream")
    if dl:
        return FileResponse(str(target), media_type=media, filename=target.name)
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "no-cache"})


class IngestImageRequest(BaseModel):
    path: str


@app.post("/api/videogen/ingest-image")
def videogen_ingest_image(req: IngestImageRequest) -> Dict[str, Any]:
    """Copy a user-picked base image into the WORK folder (not the config root), so it's
    deleted together with the clips and never left as orphaned garbage on disk."""
    src = Path(os.path.expanduser((req.path or "").strip()))
    if not src.is_file():
        raise HTTPException(400, "Nie znaleziono pliku obrazu.")
    ext = src.suffix.lower() or ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Nieobsługiwany format obrazu.")
    dest = videogen_pipeline.input_dir() / f"base-{int(_time.time()*1000)}{ext}"
    shutil.copyfile(src, dest)
    return {"path": str(dest.resolve()), "name": src.name, "size": dest.stat().st_size,
            "url": f"/api/videogen/file?path={quote(dest.name)}"}


@app.post("/api/image/generate")
def image_generate(req: VideoGenRequest) -> Dict[str, Any]:
    app_cfg = config_store.load().get("app")
    if videogen_pipeline.videogen_dir(app_cfg) is None:
        raise HTTPException(400, "Nie znaleziono silnika (VideoGenerator). Wskaż folder w Ustawieniach.")
    s = _videogen_settings("image", req.settings)

    def target(ctx) -> Any:
        return videogen_pipeline.generate_image(s, app_cfg, ctx)

    return {"job_id": manager.start("image.generate", target).id}


@app.post("/api/video/generate")
def video_generate(req: VideoGenRequest) -> Dict[str, Any]:
    app_cfg = config_store.load().get("app")
    if videogen_pipeline.videogen_dir(app_cfg) is None:
        raise HTTPException(400, "Nie znaleziono silnika (VideoGenerator). Wskaż folder w Ustawieniach.")
    s = _videogen_settings("video", req.settings)

    def target(ctx) -> Any:
        return videogen_pipeline.generate_video(s, app_cfg, ctx)

    return {"job_id": manager.start("video.generate", target).id}


@app.post("/api/videogen/enhance")
def videogen_enhance(req: PromptHelperRequest) -> Dict[str, Any]:
    app_cfg = config_store.load().get("app")
    text, kind = req.text, req.kind

    def target(ctx) -> Any:
        return videogen_pipeline.enhance_prompt(text, kind, app_cfg, ctx)

    return {"job_id": manager.start("videogen.enhance", target).id}


@app.post("/api/videogen/translate")
def videogen_translate(req: PromptHelperRequest) -> Dict[str, Any]:
    app_cfg = config_store.load().get("app")
    text = req.text

    def target(ctx) -> Any:
        return videogen_pipeline.translate_prompt(text, app_cfg, ctx)

    return {"job_id": manager.start("videogen.translate", target).id}


@app.get("/api/image/history")
def image_history() -> List[Dict[str, Any]]:
    return videogen_pipeline.list_history("image")


@app.get("/api/video/history")
def video_history() -> List[Dict[str, Any]]:
    return videogen_pipeline.list_history("video")


@app.delete("/api/image/history/{filename}")
def image_history_delete(filename: str) -> Dict[str, Any]:
    videogen_pipeline.delete_history("image", filename)
    return {"ok": True}


@app.delete("/api/video/history/{filename}")
def video_history_delete(filename: str) -> Dict[str, Any]:
    videogen_pipeline.delete_history("video", filename)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Music Generator (ACE-Step) — engine lifecycle + generation
# ---------------------------------------------------------------------------
class MusicGenerateRequest(BaseModel):
    settings: Optional[Dict[str, Any]] = None


class MusicLoadRequest(BaseModel):
    model: Optional[str] = None


def _music_settings(req_settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    full = config_store.load()
    return {**full.get("music", {}), **(req_settings or {})}


@app.get("/api/music/status")
def music_status() -> Dict[str, Any]:
    return music_pipeline.engine_status(config_store.load().get("app"))


@app.get("/api/music/estimate")
def music_estimate(lyrics: str = "") -> Dict[str, int]:
    return music_pipeline.estimate_duration(lyrics)


@app.post("/api/music/load")
def music_load(req: MusicLoadRequest) -> Dict[str, Any]:
    """Warm the engine: start ACE-Step and load the model into memory (background job)."""
    full = config_store.load()
    model = str(req.model or full.get("music", {}).get("model") or music_pipeline.DEFAULT_MODEL)
    app_cfg = full.get("app")

    def target(ctx) -> Any:
        ctx.step(f"Ładowanie modelu „{model}” do pamięci…")
        loaded = music_pipeline.ensure_loaded(model, app_cfg, ctx)
        ctx.progress(1.0, f"Model „{loaded}” gotowy w pamięci.")
        return {"loaded_model": loaded}

    job = manager.start("music.load", target)
    return {"job_id": job.id}


@app.post("/api/music/unload")
def music_unload() -> Dict[str, Any]:
    """Stop the engine and free its RAM/VRAM."""
    music_pipeline.stop_ace()
    return {"ok": True}


@app.post("/api/music/generate")
def music_generate(req: MusicGenerateRequest) -> Dict[str, Any]:
    s = _music_settings(req.settings)
    app_cfg = config_store.load().get("app")
    if music_pipeline.ace_dir(app_cfg) is None:
        raise HTTPException(
            400,
            "Nie znaleziono silnika ACE-Step. Zainstaluj go raz w systemie i wskaż folder "
            "w Ustawieniach → Music Generator.",
        )

    def target(ctx) -> Any:
        return music_pipeline.generate(s, app_cfg, ctx)

    job = manager.start("music.generate", target)
    return {"job_id": job.id}


@app.get("/api/music/history")
def music_history() -> List[Dict[str, Any]]:
    items = music_pipeline.list_history()
    for item in items:
        item["url"] = f"/api/music/audio?path={quote(item['file_name'])}"
        item["path"] = str((music_pipeline.output_dir() / item["file_name"]).resolve())
    return items


@app.get("/api/music/audio")
def music_audio(path: str, dl: int = 0):
    root = music_pipeline.output_dir().resolve()
    target = (root / os.path.basename(path)).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.exists():
        raise HTTPException(404, "Brak pliku audio")
    suffix = target.suffix.lower()
    media = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
        ".aac": "audio/aac", ".opus": "audio/opus",
    }.get(suffix, "application/octet-stream")
    if dl:
        return FileResponse(str(target), media_type=media, filename=target.name)
    return FileResponse(str(target), media_type=media, headers={"Cache-Control": "no-cache"})


@app.delete("/api/music/history/{filename}")
def music_history_delete(filename: str) -> Dict[str, Any]:
    root = music_pipeline.output_dir().resolve()
    target = (root / os.path.basename(filename)).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.exists():
        raise HTTPException(404, "Brak pliku")
    try:
        target.unlink()
        sidecar = target.with_suffix(target.suffix + ".json")
        sidecar.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Nie udało się usunąć pliku: {exc}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Job status & SSE event stream
# ---------------------------------------------------------------------------
@app.get("/api/jobs")
def jobs_list() -> List[Dict[str, Any]]:
    return manager.list()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Nieznane zadanie")
    return {
        "id": job.id, "kind": job.kind, "status": job.status,
        "progress": job.progress, "error": job.error,
        "result": job.result, "log": job.log[-200:],
    }


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> Dict[str, Any]:
    job = manager.cancel(job_id)
    if not job:
        raise HTTPException(404, "Nieznane zadanie")
    # Dubbing/TTS runs in a DETACHED Qwen worker process; the job thread is blocked in a
    # blocking read of the worker's stdout, so setting the cancel flag alone never stops
    # generation — "Przerwij" looked dead. Kill the worker process group so cancellation
    # is immediate at any segment. Safe no-op when no TTS worker is running.
    if any(k in job.kind for k in ("dub", "tts")):
        prev = os.getcwd()
        try:
            os.chdir(str(SHORTS_DIR))
            from dubbing_engine import kill_active_dubbing_processes  # type: ignore
            kill_active_dubbing_processes()
        except Exception as exc:
            print(f"[dubcut-backend] kill TTS on cancel failed: {exc}", file=sys.stderr)
        finally:
            try:
                os.chdir(prev)
            except Exception:
                pass
        # The worker owns the large model, but clear any allocator cache held by
        # the backend too. This is harmless when torch/MPS is not installed.
        try:
            import gc
            gc.collect()
            import torch  # type: ignore
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                torch.mps.empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Nieznane zadanie")

    async def gen():
        # Stream from the append-only log by seq cursor. This delivers every event
        # exactly once (no backlog/queue double-send) and lets multiple subscribers
        # follow the same job independently. The job always pushes a final "end".
        last = 0
        idle = 0.0
        while True:
            sent = False
            for e in list(job.log):
                if e.get("seq", 0) <= last:
                    continue
                last = e["seq"]
                sent = True
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                if e.get("type") == "end":
                    return
            # Heartbeat comment line during long, silent steps (e.g. first-run NLLB
            # model download holds the GIL for minutes with no progress events). Keeps
            # the EventSource connection from looking dead. Comments are ignored by the
            # browser's onmessage handler, so they never reach the job state.
            if sent:
                idle = 0.0
            else:
                idle += 0.2
                if idle >= 15.0:
                    idle = 0.0
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("DUBCUT_BACKEND_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
