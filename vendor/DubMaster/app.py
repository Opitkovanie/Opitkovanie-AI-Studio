# ============================================================
# DubMaster by Opitkovanie
# Pipeline: Demucs → MLX Whisper large-v3 → Gemini → Qwen3-TTS → Zero-Drift
# ============================================================

import streamlit as st
import os, subprocess, sys, gc, json, ssl, uuid, shutil, time, wave, hashlib, requests, selectors, threading, re, html, io, zipfile
from pathlib import Path
from collections import deque
import certifi
import yt_dlp
try:
    from streamlit_mic_recorder import mic_recorder
except Exception:
    mic_recorder = None
try:
    from audio_recorder_streamlit import audio_recorder
except Exception:
    audio_recorder = None

# ── SSL FIX (macOS) ──────────────────────────────────────────
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass
os.environ["SSL_CERT_FILE"]      = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# ── PATH FIX ─────────────────────────────────────────────────
_bin   = os.path.dirname(sys.executable)
_local = os.path.expanduser("~/.local/bin")
_cur   = os.environ.get("PATH", "")
_add   = [p for p in [_bin, _local] if p not in _cur]
if _add:
    os.environ["PATH"] = ":".join(_add) + ":" + _cur

# ── PAGE CONFIG ───────────────────────────────────────────────
st.set_page_config(page_title="DubMaster by Opitkovanie", page_icon="🎙️", layout="wide")
st.markdown("""
<style>
h1 a, h2 a, h3 a, h4 a, h5 a, h6 a,
a.header-anchor,
div[data-testid="stHeadingWithActionElements"] a { display: none !important; }
section[data-testid="stSidebar"] .stSlider {
    padding-top: 0.2rem !important; padding-bottom: 0.1rem !important; margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] .stSlider label { font-size: 0.82rem !important; margin-bottom: 0 !important; }
section[data-testid="stSidebar"] .stCheckbox { margin-bottom: 0.1rem !important; padding-bottom: 0 !important; }
section[data-testid="stSidebar"] hr {
    margin-top: 0.65rem !important;
    margin-bottom: 0.65rem !important;
    border: 0 !important;
    border-top: 2px solid rgba(255,255,255,0.18) !important;
}
section[data-testid="stSidebar"] hr::after {
    content: "";
    display: block;
    border-top: 1px solid rgba(0,0,0,0.35);
}
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 { margin-top: 0.3rem !important; margin-bottom: 0.2rem !important; }
section[data-testid="stSidebar"] .stExpander { margin-bottom: 0.2rem !important; }
section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    border-radius: 6px !important;
    text-align: left !important;
    font-size: 0.84rem !important;
    font-weight: 600 !important;
    padding: 0.25rem 0.6rem !important;
    margin-top: 0.1rem !important;
    margin-bottom: 0.15rem !important;
    width: 100% !important;
    color: inherit !important;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
    background: rgba(255,255,255,0.09) !important;
    border-color: rgba(255,255,255,0.25) !important;
}
.dm-download-btn {
    display: block;
    width: 100%;
    box-sizing: border-box;
    padding: 0.55rem 0.75rem;
    border: 1px solid rgba(250,250,250,0.2);
    border-radius: 6px;
    text-align: center;
    color: inherit !important;
    text-decoration: none !important;
    background: rgba(255,255,255,0.03);
    font-weight: 600;
    font: inherit;
    cursor: pointer;
}
.dm-download-btn:hover {
    border-color: rgba(250,250,250,0.4);
    background: rgba(255,255,255,0.08);
}
.dm-download-form {
    margin: 0;
    width: 100%;
}
</style>
""", unsafe_allow_html=True)

# ── ŚCIEŻKI ───────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
UPLOAD_DIR  = BASE_DIR / "uploads"
OUTPUT_DIR  = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config.json"
VOICE_DIR   = BASE_DIR / "voice_samples"
VOICE_INDEX_FILE = VOICE_DIR / "voices.json"
for d in [UPLOAD_DIR, OUTPUT_DIR, VOICE_DIR]:
    d.mkdir(exist_ok=True)

def _resolve_app_path(path_str, voice_id=None):
    """Resolve old absolute paths and portable relative paths against this app folder."""
    raw = str(path_str or "")
    p = Path(raw)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    elif raw:
        candidates.append(BASE_DIR / p)
    if voice_id:
        if raw:
            candidates.append(VOICE_DIR / str(voice_id) / p.name)
        candidates.append(VOICE_DIR / str(voice_id) / "voice.wav")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if voice_id:
        return VOICE_DIR / str(voice_id) / "voice.wav"
    if p.is_absolute():
        return p
    return BASE_DIR / p if raw else BASE_DIR

def _portable_app_path(path):
    try:
        return str(Path(path).resolve().relative_to(BASE_DIR))
    except Exception:
        return str(path)

# ── KONFIGURACJA ──────────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(data):
    tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass

saved = load_config()

# Globalny placeholder timera — aktualizowany z log_message podczas generowania
_g_timer_ph = None


def get_saved_proper_name_glossary():
    """Return the glossary from disk, falling back to built-in defaults."""
    disk_value = load_config().get("proper_name_glossary")
    if str(disk_value or "").strip():
        return disk_value
    saved_value = saved.get("proper_name_glossary")
    if str(saved_value or "").strip():
        return saved_value
    return DEFAULT_PROPER_NAME_GLOSSARY

# ── JĘZYKI ───────────────────────────────────────────────────
LANGUAGES = {
    "Automatyczne wykrywanie": None,
    "Polski": "pl", "Angielski": "en", "Niemiecki": "de",
    "Francuski": "fr", "Hiszpański": "es", "Włoski": "it",
    "Portugalski": "pt", "Arabski": "ar", "Rosyjski": "ru",
    "Japoński": "ja", "Koreański": "ko", "Chiński": "zh"
}
TARGET_LANGUAGES = [
    "Angielski", "Niemiecki", "Francuski", "Hiszpański",
    "Włoski", "Portugalski", "Rosyjski", "Arabski",
    "Japoński", "Koreański", "Chiński"
]

MIX_MODE_DUBBING = "Czysty dubbing (usuń oryginalny głos)"
MIX_MODE_VOICEOVER = "Lektor (oryginał + głos AI)"
MIX_MODE_VOICEOVER_DUCK = "Lektor z duckingiem (oryginał ścisza się pod AI)"
MIX_MODES = [MIX_MODE_DUBBING, MIX_MODE_VOICEOVER, MIX_MODE_VOICEOVER_DUCK]
DEFAULT_MIX_MODE = MIX_MODE_VOICEOVER_DUCK
DEFAULT_VOICEOVER_ORIGINAL_VOL = 0.85
DEFAULT_VOICEOVER_DUCK_AMOUNT = 0.95
VOICEOVER_ENGINE_SYSTEM = "Stabilny lektor systemowy (bez halucynacji)"
VOICEOVER_ENGINE_QWEN = "Qwen TTS (eksperymentalny, naturalniejszy)"
VOICEOVER_ENGINES = [VOICEOVER_ENGINE_SYSTEM, VOICEOVER_ENGINE_QWEN]
# Pusty styl jest celowy: Qwen potrafi potraktować długi instruct jako swobodę
# interpretacji i dopowiadać słowa. Stabilność lektora robimy segmentami i temperaturą.
DEFAULT_VOICEOVER_STYLE = ""

DEFAULT_PROPER_NAME_GLOSSARY = """Humsieng -> Humsienk
Humsięk -> Humsienk
Humsienk
Amfropik -> Anthropic
Amthropic -> Anthropic
Amphropic -> Anthropic
Open AI -> OpenAI
Chat GPT -> ChatGPT
Claude Code
Anthropic
OpenAI
ChatGPT
Codex
Gemini
Whisper
Qwen
LiFePO4
WattCycle"""

WHISPER_PIPELINE_VERSION = "whisper_v3_strict_no_speech_filter"

# ── SESSION STATE ─────────────────────────────────────────────
_DEFAULTS = {
    # API
    "api_key":               saved.get("gemini_api_key", ""),
    # Projekt
    "source_lang":           saved.get("source_lang", "Automatyczne wykrywanie"),
    "target_lang":           saved.get("target_lang", "Angielski"),
    "keep_bg":               saved.get("keep_bg", True),
    "auto_sync":             saved.get("auto_sync", True),
    "translation_model":     saved.get("translation_model", "Gemini 2.5 Flash (Lokalizacja 2-Etapowa)"),
    "proper_name_glossary":  get_saved_proper_name_glossary(),
    # Demucs
    "demucs_shifts":         int(saved.get("demucs_shifts", 2)),
    # TTS
    "tts_model":             saved.get("tts_model", "1.7B (Wysoka jakość)"),
    "use_fp16":              False,  # zawsze BF16 — natywny format Apple Silicon
    "whisper_precise":       bool(saved.get("whisper_precise", True)),
    "ref_audio_length":      int(saved.get("ref_audio_length", 12)),
    "clone_mode":            "Strict Voice Clone (stabilniejsza barwa głosu)",
    # Sync
    "sync_min_tempo":        float(saved.get("sync_min_tempo", 0.85)),
    "sync_max_tempo":        float(saved.get("sync_max_tempo", 1.20)),
    "auto_min_tempo":        bool(saved.get("auto_min_tempo", False)),
    "auto_max_tempo":        bool(saved.get("auto_max_tempo", False)),
    # Pitch
    "pitch_adj":             float(saved.get("pitch_adj", 0.0)),
    # Output
    "output_format_pref":    saved.get("output_format_pref", "video"),
    "output_resolution":     saved.get("output_resolution", "Auto (jak oryginał)"),
    "output_bitrate_mbps":   float(saved.get("output_bitrate_mbps", 5.0)),
    "show_log":              bool(saved.get("show_log", True)),
    "voice_source":          saved.get("voice_source", "Głos z oryginalnego filmu"),
    "selected_voice_id":     saved.get("selected_voice_id", ""),
    "voice_store_mode":      saved.get("voice_store_mode", "Próbki własne"),
    "dubbing_qwen_speaker":  saved.get("dubbing_qwen_speaker", saved.get("text_tts_speaker", "Ryan")),
    # Miks
    "mix_mode":              saved.get("mix_mode", DEFAULT_MIX_MODE),
    "voiceover_tts_engine":  saved.get("voiceover_tts_engine", VOICEOVER_ENGINE_SYSTEM),
    "voiceover_original_vol":float(saved.get("voiceover_original_vol", DEFAULT_VOICEOVER_ORIGINAL_VOL)),
    "voiceover_duck_amount": float(saved.get("voiceover_duck_amount", DEFAULT_VOICEOVER_DUCK_AMOUNT)),
    "bg_music_vol":          float(saved.get("bg_music_vol", 1.0)),
    "ambient_vol":           float(saved.get("ambient_vol", 0.7)),
    "dub_vol":               float(saved.get("dub_vol", 1.5)),
    "ambient_eq_hp":         int(saved.get("ambient_eq_hp", 200)),
    "ambient_eq_presence":   float(saved.get("ambient_eq_presence", 4.0)),
    "ambient_eq_lpf_speech": int(saved.get("ambient_eq_lpf_speech", 3500)),
    "ambient_eq_enabled":    bool(saved.get("ambient_eq_enabled", True)),
    # Stan zwiniecia/rozwinięcia sekcji bocznych
    "exp_tempo":             bool(saved.get("exp_tempo", True)),
    "exp_miks_vol":          bool(saved.get("exp_miks_vol", True)),
    "exp_eq":                bool(saved.get("exp_eq", True)),
    "exp_ai":                bool(saved.get("exp_ai", True)),
    "exp_voice_store":       bool(saved.get("exp_voice_store", False)),
    "exp_video":             bool(saved.get("exp_video", False)),
    # Runtime (nie zapisywane)
    "original_view":         "",
    "original_view_input":   "",
    "dub_edit":              "",
    "last_translated_lang":  "",
    "full_logs":             deque(maxlen=200),
    "last_log_update":       0,
    "final_output_path":     None,
    "final_output_is_video": True,
    "final_audio_path":      None,
    "final_subtitle_path":   None,
    "subtitle_target_langs": saved.get("subtitle_target_langs", ["Angielski"]),
    "subtitle_formats":      saved.get("subtitle_formats", ["SRT"]),
    "subtitle_include_original": bool(saved.get("subtitle_include_original", True)),
    "media_history":         saved.get("media_history", []),
    "subtitle_output_paths": [],
    "subtitle_zip_path":     None,
    "cancel_requested":      False,
    "current_process":       None,
    "is_generating":         False,
    "local_file_path":       "",
    "last_gen_time":         "",
    "last_saved_mic_id":     None,
    "last_saved_upload_sig": None,
    "voice_save_notice":     "",
    "text_tts_input":        "",
    "text_tts_generate_text":"",
    "text_tts_output_path":  "",
    "text_tts_last_text":    "",
    "text_tts_last_gen_time": "",
    "text_tts_speaker":      saved.get("text_tts_speaker", "Ryan"),
    "text_tts_style":        "",
    "active_main_panel":     "dubbing",
    "input_mode":            "Lokalny Plik z Dysku",
}

for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if not str(st.session_state.get("proper_name_glossary", "")).strip():
    st.session_state.proper_name_glossary = get_saved_proper_name_glossary()

if st.session_state.get("api_key"):
    pass  # API key używany bezpośrednio w requests


def ensure_proper_name_glossary():
    """Pilnuje, żeby pusty widget nie zgubił zapisanego słownika nazw."""
    current = str(st.session_state.get("proper_name_glossary", "") or "").strip()
    if current:
        return st.session_state.proper_name_glossary
    restored = get_saved_proper_name_glossary()
    st.session_state.proper_name_glossary = restored
    return restored


# ── ZAPIS USTAWIEŃ ────────────────────────────────────────────
def save_all_settings():
    glossary_value = ensure_proper_name_glossary()
    if not str(glossary_value or "").strip():
        glossary_value = get_saved_proper_name_glossary()
        st.session_state.proper_name_glossary = glossary_value
    save_config({
        "gemini_api_key":       st.session_state.api_key,
        "source_lang":          st.session_state.source_lang,
        "target_lang":          st.session_state.target_lang,
        "keep_bg":              st.session_state.keep_bg,
        "auto_sync":            st.session_state.auto_sync,
        "translation_model":    st.session_state.translation_model,
        "proper_name_glossary": glossary_value,
        "demucs_shifts":        st.session_state.demucs_shifts,
        "tts_model":            st.session_state.tts_model,
        "use_fp16":             False,
        "clone_mode":           "Strict Voice Clone (stabilniejsza barwa głosu)",
        "whisper_precise":      st.session_state.whisper_precise,
        "ref_audio_length":     st.session_state.ref_audio_length,
        "sync_min_tempo":       st.session_state.sync_min_tempo,
        "sync_max_tempo":       st.session_state.sync_max_tempo,
        "auto_min_tempo":       st.session_state.auto_min_tempo,
        "auto_max_tempo":       st.session_state.auto_max_tempo,
        "pitch_adj":            st.session_state.pitch_adj,
        "output_format_pref":   st.session_state.output_format_pref,
        "output_resolution":    st.session_state.output_resolution,
        "output_bitrate_mbps":  st.session_state.output_bitrate_mbps,
        "show_log":             st.session_state.show_log,
        "subtitle_target_langs": st.session_state.get("subtitle_target_langs", ["Angielski"]),
        "subtitle_formats":      st.session_state.get("subtitle_formats", ["SRT"]),
        "subtitle_include_original": st.session_state.get("subtitle_include_original", True),
        "media_history":        st.session_state.get("media_history", []),
        "voice_source":         st.session_state.get("voice_source", "Głos z oryginalnego filmu"),
        "selected_voice_id":    st.session_state.get("selected_voice_id", ""),
        "voice_store_mode":     st.session_state.get("voice_store_mode", "Próbki własne"),
        "dubbing_qwen_speaker": st.session_state.get("dubbing_qwen_speaker", st.session_state.get("text_tts_speaker", "Ryan")),
        "text_tts_speaker":     st.session_state.get("text_tts_speaker", "Ryan"),
        "mix_mode":             st.session_state.get("mix_mode", DEFAULT_MIX_MODE),
        "voiceover_tts_engine": st.session_state.get("voiceover_tts_engine", VOICEOVER_ENGINE_SYSTEM),
        "voiceover_original_vol": st.session_state.get("voiceover_original_vol", DEFAULT_VOICEOVER_ORIGINAL_VOL),
        "voiceover_duck_amount": st.session_state.get("voiceover_duck_amount", DEFAULT_VOICEOVER_DUCK_AMOUNT),
        "bg_music_vol":         st.session_state.bg_music_vol,
        "ambient_vol":          st.session_state.ambient_vol,
        "dub_vol":              st.session_state.dub_vol,
        "ambient_eq_hp":        st.session_state.ambient_eq_hp,
        "ambient_eq_presence":  st.session_state.ambient_eq_presence,
        "ambient_eq_lpf_speech":st.session_state.ambient_eq_lpf_speech,
        "ambient_eq_enabled":   st.session_state.ambient_eq_enabled,
        "exp_tempo":            st.session_state.get("exp_tempo", True),
        "exp_miks_vol":         st.session_state.get("exp_miks_vol", True),
        "exp_eq":               st.session_state.get("exp_eq", True),
        "exp_ai":               st.session_state.get("exp_ai", True),
        "exp_voice_store":      st.session_state.get("exp_voice_store", False),
        "exp_video":            st.session_state.get("exp_video", False),
    })



# ── TOGGLE SECTION (zapamiętuje stan zwinięcia) ───────────────
def remembered_section(label, state_key, default=False):
    if state_key not in st.session_state:
        st.session_state[state_key] = default
    is_open = bool(st.session_state.get(state_key, default))
    marker = "⌄" if is_open else "›"
    if st.button(f"{marker} {label}", key=f"{state_key}_toggle", use_container_width=True):
        st.session_state[state_key] = not is_open
        save_all_settings()
    return bool(st.session_state.get(state_key, default))


# ── HELPER FUNCTIONS ──────────────────────────────────────────
def cleanup_mps():
    try:
        if "torch" in sys.modules:
            import torch
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()
            gc.collect()
    except Exception:
        pass

def _kill_process_tree(proc):
    if not proc:
        return
    try:
        import signal
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def request_cancel():
    st.session_state.cancel_requested = True
    proc = st.session_state.get("current_process")
    if proc:
        _kill_process_tree(proc)
        st.session_state.current_process = None
    cleanup_mps()

def clear_temp_files():
    """Usuwa WSZYSTKO z folderów uploads i output — czysty reset."""

    def _delete_recursive(path_str):
        """Rekurencyjne usuwanie przez os.scandir — poprawnie obsługuje Unicode NFD na macOS."""
        try:
            with os.scandir(path_str) as it:
                entries = list(it)
        except Exception:
            return
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    _delete_recursive(entry.path)
                    try: os.rmdir(entry.path)
                    except Exception: pass
                else:
                    try: os.unlink(entry.path)
                    except Exception:
                        # Fallback: zdejmij immutable flag i spróbuj ponownie
                        try:
                            subprocess.run(["chflags", "nouchg", entry.path], check=False)
                            os.unlink(entry.path)
                        except Exception: pass
            except Exception:
                pass

    for folder in [UPLOAD_DIR, OUTPUT_DIR]:
        if folder.exists():
            _delete_recursive(str(folder))
        folder.mkdir(parents=True, exist_ok=True)

    for key in ["original_view", "dub_edit", "last_translated_lang", "full_logs",
                 "final_output_path", "final_output_is_video", "final_audio_path", "final_subtitle_path",
                 "subtitle_output_paths", "subtitle_zip_path",
                 "local_file_path", "original_view_input", "yt_url_input"]:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.final_output_is_video = True


def _clear_working_files():
    """Usuwa pliki robocze — ZACHOWUJE cache oraz pobrane/wskazane materiały."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Output — usuń wszystko OPRÓCZ podfolderu cache i eksportów napisów
    if OUTPUT_DIR.exists():
        for item in OUTPUT_DIR.iterdir():
            if item.name in {"cache", "subtitles"}:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
            except Exception:
                pass

    for key in ["original_view", "dub_edit", "last_translated_lang", "full_logs",
                 "final_output_path", "final_output_is_video", "final_audio_path", "final_subtitle_path",
                 "subtitle_output_paths", "subtitle_zip_path",
                 "local_file_path", "original_view_input", "yt_url_input"]:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.final_output_is_video = True


def is_appledouble_or_hidden_artifact(path):
    """Pomija pliki metadanych macOS z dysków zewnętrznych, np. ._film.mp4."""
    name = Path(path).name
    return name.startswith("._") or name == ".DS_Store"


def is_probably_media_file(path):
    """Szybka walidacja, czy plik jest realnym audio/wideo, a nie metadanymi."""
    p = Path(path)
    if is_appledouble_or_hidden_artifact(p):
        return False
    try:
        if not p.is_file() or p.stat().st_size < 8 * 1024:
            return False
    except Exception:
        return False
    return True


def _reset_project_runtime_state():
    for key in ["original_view", "original_view_input", "dub_edit",
                "last_translated_lang", "full_logs",
                "final_output_path", "final_output_is_video", "final_audio_path", "final_subtitle_path",
                "subtitle_output_paths", "subtitle_zip_path"]:
        st.session_state.pop(key, None)
    st.session_state.full_logs = deque(maxlen=200)
    st.session_state.final_output_is_video = True


def remember_media_file(path, source="Lokalny plik"):
    """Zapisuje istniejący plik w historii projektów."""
    try:
        p = Path(path).expanduser().resolve()
        if not is_probably_media_file(p):
            return
        stat = p.stat()
    except Exception:
        return

    history = []
    seen = set()
    now = time.time()
    new_item = {
        "path": str(p),
        "name": p.name,
        "source": source,
        "size": int(stat.st_size),
        "last_used": now,
    }
    for item in [new_item] + list(st.session_state.get("media_history", [])):
        item_path = str(item.get("path", "") or "")
        if not item_path or item_path in seen:
            continue
        ip = Path(item_path)
        if not is_probably_media_file(ip):
            continue
        seen.add(item_path)
        history.append(item)
        if len(history) >= 40:
            break
    st.session_state.media_history = history
    save_all_settings()


def get_media_history():
    """Zwraca historię plus filmy nadal leżące w uploads."""
    items = []
    seen = set()

    def _add(path, source, last_used=None):
        try:
            p = Path(path).expanduser().resolve()
            if str(p) in seen or not is_probably_media_file(p):
                return
            stat = p.stat()
            seen.add(str(p))
            items.append({
                "path": str(p),
                "name": p.name,
                "source": source,
                "size": int(stat.st_size),
                "last_used": float(last_used or stat.st_mtime),
            })
        except Exception:
            return

    for item in st.session_state.get("media_history", []):
        _add(item.get("path"), item.get("source", "Historia"), item.get("last_used"))

    if UPLOAD_DIR.exists():
        for p in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            if p.suffix.lower() in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]:
                _add(p, "Pobrane z YouTube", p.stat().st_mtime if p.exists() else None)

    items.sort(key=lambda x: x.get("last_used", 0), reverse=True)
    st.session_state.media_history = items[:40]
    return st.session_state.media_history


def media_history_label(item):
    try:
        size_mb = float(item.get("size", 0)) / (1024 * 1024)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(item.get("last_used", 0) or 0)))
        return f"{item.get('name', 'plik')} · {item.get('source', 'Historia')} · {size_mb:.0f} MB · {when}"
    except Exception:
        return str(item.get("name") or item.get("path") or "plik")


def static_output_url(path):
    """Udostępnia wynik przez Streamlit static bez kopiowania dużego pliku do pamięci."""
    from urllib.parse import quote
    p = Path(path).resolve()
    if not p.exists() or not p.is_file() or is_appledouble_or_hidden_artifact(p):
        return None
    static_dir = BASE_DIR / "static" / "dubmaster_output"
    static_dir.mkdir(parents=True, exist_ok=True)
    stat = p.stat()
    link_sig = f"{p}:{stat.st_size}:{stat.st_mtime_ns}"
    link_name = f"{hashlib.sha256(link_sig.encode('utf-8')).hexdigest()[:16]}_{p.name}"
    link_path = static_dir / link_name
    try:
        for old_link in static_dir.iterdir():
            if old_link.name != link_name and old_link.name.endswith(f"_{p.name}"):
                try:
                    old_link.unlink()
                except Exception:
                    pass
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink(missing_ok=True)
        try:
            os.link(str(p), str(link_path))
        except Exception:
            os.symlink(str(p), str(link_path))
        return f"/app/static/dubmaster_output/{quote(link_name)}"
    except Exception:
        return None


_MEDIA_SERVER = {"httpd": None, "port": None}


def get_media_server():
    """Mały lokalny serwer do płynnego streamingu dużych plików z obsługą Range."""
    if _MEDIA_SERVER.get("httpd") is not None:
        return _MEDIA_SERVER["httpd"], _MEDIA_SERVER["port"]

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import quote, unquote, urlparse
    import mimetypes

    class MediaHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def do_HEAD(self):
            self._serve(send_body=False)

        def do_GET(self):
            self._serve(send_body=True)

        def _serve(self, send_body=True):
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            if len(parts) < 3 or parts[0] not in {"media", "download"}:
                self.send_error(404)
                return

            mode, token = parts[0], parts[1]
            path_str = self.server.file_map.get(token)
            if not path_str:
                self.send_error(404)
                return

            p = Path(path_str)
            if not p.exists() or not p.is_file() or is_appledouble_or_hidden_artifact(p):
                self.send_error(404)
                return

            size = p.stat().st_size
            start, end = 0, size - 1
            status = 200
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                status = 206
                byte_range = range_header.replace("bytes=", "", 1).split(",", 1)[0].strip()
                left, _, right = byte_range.partition("-")
                try:
                    if left:
                        start = int(left)
                        end = int(right) if right else size - 1
                    else:
                        suffix = int(right)
                        start = max(0, size - suffix)
                        end = size - 1
                    start = max(0, min(start, size - 1))
                    end = max(start, min(end, size - 1))
                except Exception:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            length = end - start + 1
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            disposition = "attachment" if mode == "download" else "inline"
            ascii_name = p.name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "dubmaster_output"
            utf8_name = quote(p.name)

            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Disposition", f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}")
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            if not send_body:
                return
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MediaHandler)
    httpd.file_map = {}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _MEDIA_SERVER["httpd"] = httpd
    _MEDIA_SERVER["port"] = httpd.server_port
    return httpd, httpd.server_port


def media_file_url(path, download=False):
    from urllib.parse import quote
    p = Path(path).resolve()
    if not p.exists() or not p.is_file() or is_appledouble_or_hidden_artifact(p):
        return None
    stat = p.stat()
    token = hashlib.sha256(f"{p}:{stat.st_size}:{stat.st_mtime_ns}:{download}".encode("utf-8")).hexdigest()[:24]
    httpd, port = get_media_server()
    httpd.file_map[token] = str(p)
    mode = "download" if download else "media"
    return f"http://127.0.0.1:{port}/{mode}/{token}/{quote(p.name)}"


# ── HELPERY SEGMENTÓW / LINII ───────────────────────────────────
def split_lines_preserve_count(text):
    if text is None:
        return []
    norm = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in norm.split("\n")]

def fit_lines_to_segments(text, segments):
    """Dopasuj liczbę linii do liczby segmentów bez psucia mapowania 1:1."""
    lines = split_lines_preserve_count(text)
    seg_count = len(segments or [])

    if seg_count <= 0:
        return [line for line in lines if line.strip()]

    if len(lines) < seg_count:
        lines = lines + [""] * (seg_count - len(lines))
    elif len(lines) > seg_count:
        tail = " ".join(line for line in lines[seg_count - 1:] if line.strip())
        lines = lines[:seg_count - 1] + [tail]

    return [line.strip() for line in lines[:seg_count]]


def _fmt_ts(seconds):
    seconds = max(0.0, float(seconds or 0.0))
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


def _fmt_srt_ts(seconds):
    seconds = max(0.0, float(seconds or 0.0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    if secs >= 60:
        minutes += 1
        secs -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_youtube_srt(segments, subtitle_lines, output_path):
    """Zapisuje proste SubRip (.srt), czyli format polecany i obsługiwany przez YouTube."""
    fitted_lines = fit_lines_to_segments("\n".join(subtitle_lines or []), segments)
    entries = []
    for seg, text in zip(segments or [], fitted_lines):
        clean_text = " ".join(str(text or "").split()).strip()
        if not clean_text:
            continue
        start = max(0.0, float(seg.get("start", 0.0)))
        end = max(start + 0.10, float(seg.get("end", start + 0.10)))
        entries.append((start, end, clean_text))

    if not entries:
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        for idx, (start, end, text) in enumerate(entries, 1):
            f.write(f"{idx}\n")
            f.write(f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n")
            f.write(f"{text}\n\n")
    return output_path.exists() and output_path.stat().st_size > 0


def _fmt_vtt_ts(seconds):
    return _fmt_srt_ts(seconds).replace(",", ".")


def write_youtube_vtt(segments, subtitle_lines, output_path):
    """Zapisuje WebVTT (.vtt), drugi format dobrze obsługiwany przez YouTube."""
    fitted_lines = fit_lines_to_segments("\n".join(subtitle_lines or []), segments)
    entries = []
    for seg, text in zip(segments or [], fitted_lines):
        clean_text = " ".join(str(text or "").split()).strip()
        if not clean_text:
            continue
        start = max(0.0, float(seg.get("start", 0.0)))
        end = max(start + 0.10, float(seg.get("end", start + 0.10)))
        entries.append((start, end, clean_text))

    if not entries:
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("WEBVTT\n\n")
        for start, end, text in entries:
            f.write(f"{_fmt_vtt_ts(start)} --> {_fmt_vtt_ts(end)}\n")
            f.write(f"{text}\n\n")
    return output_path.exists() and output_path.stat().st_size > 0


def _safe_output_token(text):
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip())
    return token.strip("_") or "NAPISY"


def write_subtitle_file(segments, subtitle_lines, output_path, subtitle_format):
    fmt = str(subtitle_format or "SRT").upper()
    if fmt == "VTT":
        return write_youtube_vtt(segments, subtitle_lines, output_path)
    return write_youtube_srt(segments, subtitle_lines, output_path)


def segment_preview_audio(input_path, seg, pad=0.15):
    """Tworzy krótki odsłuch oryginalnego audio dla segmentu i zwraca URL."""
    try:
        src = Path(input_path).resolve()
        if not src.exists():
            return None
        start = max(0.0, float(seg.get("start", 0.0)) - pad)
        end = max(start + 0.10, float(seg.get("end", start + 0.10)) + pad)
        sig = f"{src}:{src.stat().st_size}:{src.stat().st_mtime_ns}:{start:.3f}:{end:.3f}"
        out_dir = OUTPUT_DIR / "cache" / "segment_previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{hashlib.sha256(sig.encode('utf-8')).hexdigest()[:20]}.wav"
        if not out.exists() or out.stat().st_size < 1024:
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start:.3f}",
                "-i", str(src),
                "-t", f"{end - start:.3f}",
                "-vn", "-ac", "1", "-ar", "22050",
                "-c:a", "pcm_s16le",
                str(out),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if out.exists() and out.stat().st_size > 1024:
            return media_file_url(out)
    except Exception:
        return None
    return None


def rebuild_segments_from_lines(old_segments, edited_lines):
    """Zachowuje oś czasu po edycji tekstu, nawet gdy zmieni się liczba linii."""
    old_segments = [dict(s) for s in (old_segments or [])]
    lines = [line.strip() for line in (edited_lines or []) if line.strip()]
    if not old_segments or not lines:
        return []

    if len(lines) >= len(old_segments):
        fitted = fit_lines_to_segments("\n".join(lines), old_segments)
        for i, seg in enumerate(old_segments):
            seg["text"] = fitted[i] if i < len(fitted) else ""
        return old_segments

    rebuilt = []
    seg_count = len(old_segments)
    line_count = len(lines)
    for i, line in enumerate(lines):
        start_idx = round(i * seg_count / line_count)
        end_idx = round((i + 1) * seg_count / line_count) - 1
        start_idx = max(0, min(start_idx, seg_count - 1))
        end_idx = max(start_idx, min(end_idx, seg_count - 1))
        chunk = old_segments[start_idx:end_idx + 1]
        rebuilt.append({
            "start": float(chunk[0]["start"]),
            "end": float(chunk[-1]["end"]),
            "text": line,
        })
    return rebuilt


def split_tts_text_into_chunks(text, max_chars=115, max_words=20):
    """Dzieli długą linię dubbingu na krótsze frazy bez zmiany treści."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return [""]
    words = clean.split()
    if len(clean) <= max_chars and len(words) <= max_words:
        return [clean]

    parts = [
        p.strip()
        for p in re.split(r"(?<=[.!?;:])\s+|(?<=,)\s+", clean)
        if p.strip()
    ]
    if not parts:
        parts = [clean]

    chunks = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}".strip() if current else part
        if current and (len(candidate) > max_chars or len(candidate.split()) > max_words):
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)

    final = []
    for chunk in chunks:
        chunk_words = chunk.split()
        if len(chunk) <= max_chars and len(chunk_words) <= max_words:
            final.append(chunk)
            continue
        for i in range(0, len(chunk_words), max_words):
            final.append(" ".join(chunk_words[i:i + max_words]).strip())

    return [c for c in final if c.strip()] or [clean]


def expand_segments_for_tts(segments, texts):
    """Tworzy krótsze podsegmenty TTS wewnątrz tych samych okien czasowych."""
    expanded_segments, expanded_texts, split_notes = [], [], []
    for idx, (seg, text) in enumerate(zip(segments or [], texts or []), 1):
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        seg_dur = max(seg_end - seg_start, 0.05)
        chunks = split_tts_text_into_chunks(text)

        # Nie rozbijaj na bardzo krótkie okna; krótkie segmenty lepiej zostawić w całości.
        if len(chunks) > 1 and seg_dur / len(chunks) < 1.35:
            chunks = [" ".join(str(text or "").split())]

        if len(chunks) == 1:
            item = dict(seg)
            item["text"] = chunks[0]
            expanded_segments.append(item)
            expanded_texts.append(chunks[0])
            continue

        weights = [max(len(c), 1) for c in chunks]
        total_weight = float(sum(weights))
        cursor = seg_start
        for chunk_i, (chunk, weight) in enumerate(zip(chunks, weights)):
            if chunk_i == len(chunks) - 1:
                chunk_end = seg_end
            else:
                chunk_end = cursor + seg_dur * (weight / total_weight)
            item = dict(seg)
            item["start"] = cursor
            item["end"] = min(seg_end, max(cursor + 0.05, chunk_end))
            item["text"] = chunk
            expanded_segments.append(item)
            expanded_texts.append(chunk)
            cursor = item["end"]
        split_notes.append((idx, len(chunks)))

    return expanded_segments, expanded_texts, split_notes


def build_voiceover_tts_units(segments, texts, max_chars=260, max_duration=14.0, max_gap=1.0):
    """Łączy sąsiednie segmenty dla stabilniejszej prozodii lektora."""
    units, notes = [], []
    current = None
    current_texts = []

    for seg, text in zip(segments or [], texts or []):
        text = " ".join(str(text or "").split()).strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        if current is None:
            current = {"start": start, "end": end}
            current_texts = [text]
            continue

        gap = start - float(current["end"])
        candidate_text = " ".join(current_texts + [text]).strip()
        candidate_duration = end - float(current["start"])
        if gap <= max_gap and len(candidate_text) <= max_chars and candidate_duration <= max_duration:
            current["end"] = end
            current_texts.append(text)
        else:
            current["text"] = " ".join(current_texts).strip()
            units.append(current)
            if len(current_texts) > 1:
                notes.append(len(current_texts))
            current = {"start": start, "end": end}
            current_texts = [text]

    if current is not None:
        current["text"] = " ".join(current_texts).strip()
        units.append(current)
        if len(current_texts) > 1:
            notes.append(len(current_texts))

    return units, [u["text"] for u in units], notes


def render_segment_text_editor(input_path, segments, values, key_prefix, title, helper, page_size=8):
    """Segmentowy edytor tekstu z odsłuchem oryginału. Zwraca listę po kliknięciu zapisu strony."""
    if not input_path or not segments:
        return None

    values = list(values or [])
    if len(values) < len(segments):
        values += [""] * (len(segments) - len(values))
    values = values[:len(segments)]

    editor_id = hashlib.sha256(
        f"{Path(input_path).resolve()}:{len(segments)}:{key_prefix}".encode("utf-8")
    ).hexdigest()[:10]
    page_key = f"{key_prefix}_page_{editor_id}"
    open_key = f"{key_prefix}_open_{editor_id}"
    max_page = max(0, (len(segments) - 1) // page_size)
    current_page = max(0, min(int(st.session_state.get(page_key, 0)), max_page))

    if open_key not in st.session_state:
        st.session_state[open_key] = False
    toggle_label = f"▾ {title}" if st.session_state[open_key] else f"› {title}"
    if st.button(toggle_label, use_container_width=True, key=f"{key_prefix}_toggle_{editor_id}"):
        st.session_state[open_key] = not st.session_state[open_key]
        st.rerun()
    if not st.session_state[open_key]:
        return None

    with st.container(border=True):
        st.caption(helper)
        st.caption(f"Segmenty {len(segments)} razem · pokazuję {page_size} na stronę.")

        def _nav_controls(position):
            nav_prev, nav_info, nav_next = st.columns([1, 1.2, 1])
            with nav_prev:
                if st.button("◀ Poprzednia", use_container_width=True, disabled=current_page <= 0, key=f"{key_prefix}_{position}_prev_{editor_id}"):
                    st.session_state[page_key] = current_page - 1
                    st.rerun()
            with nav_info:
                st.markdown(
                    f"<div style='text-align:center;color:#aaa;padding-top:0.45rem;'>Strona {current_page + 1}/{max_page + 1}</div>",
                    unsafe_allow_html=True,
                )
            with nav_next:
                if st.button("Następna ▶", use_container_width=True, disabled=current_page >= max_page, key=f"{key_prefix}_{position}_next_{editor_id}"):
                    st.session_state[page_key] = current_page + 1
                    st.rerun()

        _nav_controls("top")
        start_i = int(current_page) * page_size
        end_i = min(len(segments), start_i + page_size)
        st.caption(f"Strona {int(current_page) + 1}/{max_page + 1} · segmenty {start_i + 1}-{end_i}")

        for i in range(start_i, end_i):
            seg = segments[i]
            seg_key = f"{key_prefix}_{editor_id}_{i}"
            if seg_key not in st.session_state:
                st.session_state[seg_key] = values[i]
            dur = float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
            st.markdown(f"**#{i + 1} · {_fmt_ts(seg.get('start', 0.0))} → {_fmt_ts(seg.get('end', 0.0))} · {dur:.2f}s**")
            audio_url = segment_preview_audio(input_path, seg)
            if audio_url:
                st.audio(audio_url)
            else:
                st.caption("Odsłuch niedostępny dla tego segmentu.")
            st.text_area(
                f"Tekst segmentu #{i + 1}",
                key=seg_key,
                height=86,
                label_visibility="collapsed",
                help="Ten tekst jest przypięty do powyższego zakresu czasu. Edycja nie przesuwa innych segmentów."
            )
            txt_len = len(st.session_state.get(seg_key, "").strip())
            if dur > 0 and txt_len / dur > 30:
                st.warning("Ten segment ma dużo tekstu jak na swoje okno czasowe. Lektor może mówić za szybko albo stretch będzie mocny.")
            st.divider()

        _nav_controls("bottom")

        if st.button("💾 Zapisz widoczną stronę segmentów", use_container_width=True, key=f"{key_prefix}_save_{editor_id}"):
            updated = values[:]
            for i in range(start_i, end_i):
                updated[i] = st.session_state.get(f"{key_prefix}_{editor_id}_{i}", "").strip()
            st.session_state[open_key] = True
            st.session_state[page_key] = current_page
            return updated

    return None


# ── SŁOWNIK NAZW WŁASNYCH ─────────────────────────────────────
def parse_proper_name_glossary(glossary_text=None):
    """Zwraca (reguły_poprawek, lista_nazw) ze słownika użytkownika."""
    text = glossary_text
    if text is None:
        text = st.session_state.get("proper_name_glossary", DEFAULT_PROPER_NAME_GLOSSARY)

    rules, canonical_terms, seen_terms = [], [], set()
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            wrong, right = [part.strip() for part in line.split("->", 1)]
            if wrong and right:
                rules.append((wrong, right))
                key = right.casefold()
                if key not in seen_terms:
                    canonical_terms.append(right)
                    seen_terms.add(key)
        else:
            key = line.casefold()
            if key not in seen_terms:
                canonical_terms.append(line)
                seen_terms.add(key)
    rules.sort(key=lambda item: len(item[0]), reverse=True)
    canonical_terms.sort(key=len, reverse=True)
    return rules, canonical_terms


def _proper_name_pattern(term):
    # Granice bezpieczne dla nazw typu "ChatGPT", "LiFePO4", ale też dla polskich znaków.
    escaped = re.escape(term)
    return re.compile(rf"(?<![0-9A-Za-z_À-ž]){escaped}(?![0-9A-Za-z_À-ž])", re.IGNORECASE)


def apply_proper_name_glossary_to_text(text, glossary_text=None):
    if text is None:
        return text
    fixed = str(text)
    rules, _ = parse_proper_name_glossary(glossary_text)
    for wrong, right in rules:
        fixed = _proper_name_pattern(wrong).sub(right, fixed)
    return fixed


def apply_proper_name_glossary_to_segments(segments, glossary_text=None):
    fixed_segments = []
    for seg in segments or []:
        item = dict(seg)
        item["text"] = apply_proper_name_glossary_to_text(item.get("text", ""), glossary_text)
        fixed_segments.append(item)
    return fixed_segments


def append_proper_name_glossary_entry(wrong_text, correct_text):
    wrong = str(wrong_text or "").strip()
    correct = str(correct_text or "").strip()
    if not wrong and not correct:
        return False, "Wpisz nazwę albo poprawkę."
    if wrong and correct:
        entry = correct if wrong.casefold() == correct.casefold() else f"{wrong} -> {correct}"
    else:
        entry = correct or wrong

    current = str(ensure_proper_name_glossary() or "").strip()
    lines = [line.rstrip() for line in current.splitlines() if line.strip()]
    if entry.casefold() in {line.casefold() for line in lines}:
        return False, "Taki wpis już jest w słowniku."
    lines.append(entry)
    st.session_state.proper_name_glossary = "\n".join(lines)
    st.session_state.proper_name_glossary_editor = st.session_state.proper_name_glossary
    save_all_settings()
    return True, f"Dodano: {entry}"


def proper_name_glossary_prompt():
    rules, canonical_terms = parse_proper_name_glossary()
    if not rules and not canonical_terms:
        return ""

    lines = [
        "PROPER NAMES / BRAND SPELLING:",
        "Preserve these company, app, model, and product names exactly. Do not translate or respell them.",
    ]
    if rules:
        lines.append("Known speech-recognition corrections:")
        lines.extend(f"- {wrong} -> {right}" for wrong, right in rules[:80])
    if canonical_terms:
        lines.append("Canonical spellings:")
        lines.append(", ".join(canonical_terms[:80]))
    return "\n".join(lines)


# ── MAGAZYN GŁOSÓW ───────────────────────────────────────────
def _safe_voice_name(name):
    clean = " ".join(str(name or "").strip().split())
    return clean[:80] if clean else "Nowy głos"


def load_voice_index():
    if VOICE_INDEX_FILE.exists():
        try:
            with open(VOICE_INDEX_FILE, encoding="utf-8") as f:
                data = json.load(f)
            voices = data.get("voices", []) if isinstance(data, dict) else []
            out = []
            for voice in voices:
                if not isinstance(voice, dict) or not voice.get("id"):
                    continue
                fixed = dict(voice)
                fixed["path"] = str(_resolve_app_path(fixed.get("path", ""), fixed.get("id")))
                out.append(fixed)
            return out
        except Exception:
            return []
    return []


def save_voice_index(voices):
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VOICE_INDEX_FILE.with_suffix(".json.tmp")
    try:
        portable_voices = []
        for voice in voices:
            fixed = dict(voice)
            if fixed.get("path"):
                fixed["path"] = _portable_app_path(_resolve_app_path(fixed.get("path"), fixed.get("id")))
            portable_voices.append(fixed)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"voices": portable_voices}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, VOICE_INDEX_FILE)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def get_voice_by_id(voice_id):
    for voice in load_voice_index():
        if voice.get("id") == voice_id:
            path = Path(voice.get("path", ""))
            if path.exists():
                return voice
    return None


def extract_audio_to_voice_sample(source_path, out_wav):
    voice_filter = (
        "aresample=async=1:first_pts=0,"
        "highpass=f=70,lowpass=f=12000,"
        "alimiter=limit=0.95,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(source_path),
        "-vn", "-af", voice_filter,
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out_wav)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    except FileNotFoundError:
        return False, "Nie znaleziono ffmpeg."
    except subprocess.TimeoutExpired:
        return False, "Przetwarzanie próbki trwało zbyt długo."
    if res.returncode != 0 or not Path(out_wav).exists() or Path(out_wav).stat().st_size <= 1024:
        return False, "Nie udało się wyciągnąć audio z pliku."
    dur = get_audio_duration(out_wav)
    if dur < 1.0:
        return False, "Próbka jest za krótka albo nie zawiera audio."
    return True, ""


def add_voice_sample(name, file_bytes, original_name, source_type):
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    sample_id = uuid.uuid4().hex[:12]
    sample_dir = VOICE_DIR / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name or "sample.wav").suffix.lower() or ".wav"
    source_path = sample_dir / f"source{suffix}"
    out_wav = sample_dir / "voice.wav"
    with open(source_path, "wb") as f:
        f.write(file_bytes)
    ok, err = extract_audio_to_voice_sample(source_path, out_wav)
    if not ok:
        shutil.rmtree(sample_dir, ignore_errors=True)
        return None, err
    try:
        source_path.unlink(missing_ok=True)
    except Exception:
        pass
    duration = get_audio_duration(out_wav)
    voices = load_voice_index()
    voice = {
        "id": sample_id,
        "name": _safe_voice_name(name),
        "source_type": source_type,
        "original_name": original_name or "nagranie.wav",
        "path": _portable_app_path(out_wav),
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "duration": round(float(duration or 0.0), 2),
    }
    voices.append(voice)
    save_voice_index(voices)
    return voice, ""


def rename_voice_sample(voice_id, new_name):
    voices = load_voice_index()
    changed = False
    for voice in voices:
        if voice.get("id") == voice_id:
            voice["name"] = _safe_voice_name(new_name)
            changed = True
            break
    if changed:
        save_voice_index(voices)
    return changed


def delete_voice_sample(voice_id):
    voices = load_voice_index()
    keep = []
    removed = None
    for voice in voices:
        if voice.get("id") == voice_id:
            removed = voice
        else:
            keep.append(voice)
    if removed:
        try:
            shutil.rmtree(Path(removed.get("path", "")).parent, ignore_errors=True)
        except Exception:
            pass
        save_voice_index(keep)
        if st.session_state.get("selected_voice_id") == voice_id:
            st.session_state.selected_voice_id = keep[-1]["id"] if keep else ""
        return True
    return False


def clear_voice_store():
    try:
        for item in VOICE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            elif item.name != VOICE_INDEX_FILE.name:
                item.unlink(missing_ok=True)
        save_voice_index([])
        st.session_state.selected_voice_id = ""
        st.session_state.voice_source = "Głos z oryginalnego filmu"
    except Exception:
        pass

# ── TŁUMACZENIE GEMINI ────────────────────────────────────────
def translate_segments_ai(segments, target_lang):
    current_api_key = st.session_state.get("api_key", "")
    if not segments: return []
    segments = apply_proper_name_glossary_to_segments(segments)
    if not current_api_key:
        st.error("⚠️ Brak klucza API Gemini. Nie zapisuję oryginału jako tłumaczenia.")
        return []

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={current_api_key}"

    full_context = " ".join(s["text"] for s in segments)
    glossary_block = proper_name_glossary_prompt()
    chunk_size = 35

    system_prompt_template = """You are an expert audiovisual translator and dubbing adapter working into {target_lang}.
You are translating a real spoken video transcript for dubbing with AI Text-to-Speech.

FULL CONTEXT:
{full_context}

{glossary_block}

RULES:
1. Return EXACTLY {segment_count} lines in this format: [N] text
2. Keep the same segment order and count
3. Each line must sound natural when SPOKEN aloud, not written
4. Keep each line compact enough to fit its timing window
5. Translate the meaning, tone, and intent faithfully
6. Do NOT invent scene details, props, foods, people, or actions not present in the transcript
7. If the speaker sounds casual, keep it casual; if technical, keep the technical terms accurate
8. Do NOT merge or split segments

CRITICAL — UNITS OF MEASUREMENT (TTS reads abbreviations as letter names, not words):
- ALWAYS write units as full spoken words, matching how the speaker says them naturally
- Examples for English: "250A" → "250 amps", "3.4kW" → "3.4 kilowatts", "220V" → "220 volts",
  "5cm" → "5 centimeters", "10kg" → "10 kilograms", "321 A" → "321 amps", "2500W" → "2500 watts"
- Use singular or plural naturally: "1 amp", "2 amps", "1 watt", "500 watts"
- For other languages use the correct spoken form in {target_lang}
- Applies to ALL units: A/amps, V/volts, W/watts, kW/kilowatts, kWh, Hz, rpm,
  cm/centimeters, mm/millimeters, m/meters, km/kilometers, kg/kilograms, g/grams, °C/°F, Ω/ohms

Return ONLY the numbered translations. No headers, no commentary."""

    try:
        all_translated = []
        for offset in range(0, len(segments), chunk_size):
            chunk = segments[offset:offset + chunk_size]
            numbered_input = "\n".join(
                f"[{i+1}] {s['text'].strip()}" for i, s in enumerate(chunk)
            )
            system_prompt = system_prompt_template.format(
                target_lang=target_lang,
                full_context=full_context[:1200],
                glossary_block=glossary_block,
                segment_count=len(chunk),
            )
            payload = {
                "contents": [{"parts": [{"text": f"Translate these segments for dubbing:\n{numbered_input}"}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]}
            }
            res = requests.post(url, json=payload, timeout=120)
            res.raise_for_status()
            raw = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            parsed = {}
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("[") and "]" in line:
                    try:
                        end = line.index("]")
                        num = int(line[1:end])
                        seg_text = line[end+1:].strip()
                        if seg_text:
                            parsed[num] = seg_text
                    except (ValueError, IndexError):
                        pass
            missing = [i + 1 for i in range(len(chunk)) if i + 1 not in parsed]
            if missing:
                raise ValueError(
                    f"Gemini zwrócił niepełną partię {offset + 1}-{offset + len(chunk)} "
                    f"(brak linii: {missing[:8]})."
                )
            all_translated.extend(
                apply_proper_name_glossary_to_text(parsed[i + 1])
                for i in range(len(chunk))
            )
        return all_translated
    except Exception as e:
        st.error(f"[Tłumaczenie] Błąd: {e}. Nie zapisuję oryginału jako tłumaczenia.")
        return []

def translate_text_ai(text, target_lang):
    current_api_key = st.session_state.get("api_key", "")
    if not text.strip(): return ""
    text = apply_proper_name_glossary_to_text(text)
    if not current_api_key:
        st.error("⚠️ Brak klucza API Gemini.")
        return text
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={current_api_key}"
    glossary_block = proper_name_glossary_prompt()
    payload = {"contents": [{"parts": [{"text": f"Accurately translate the following video transcript into {target_lang}.\n\n{glossary_block}\n\nTranscript:\n{text}"}]}]}
    try:
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        translated = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", text).strip()
        return apply_proper_name_glossary_to_text(translated)
    except Exception as e:
        st.error(f"Błąd tłumaczenia: {e}")
        return text


QWEN_PRESET_SPEAKERS = {
    "Ryan": "dynamiczny męski głos, najlepszy start dla angielskiego",
    "Aiden": "jasny amerykański męski głos",
    "Vivian": "jasny młody żeński głos",
    "Serena": "ciepły, delikatny młody żeński głos",
    "Uncle_Fu": "niski, dojrzały męski głos",
    "Dylan": "młody męski głos",
    "Eric": "żywy męski głos",
    "Ono_Anna": "lekki japoński żeński głos",
    "Sohee": "ciepły koreański żeński głos",
}


# ── CACHE TRANSKRYPCJI ────────────────────────────────────────
def get_cache_path(file_path):
    fpath = Path(file_path)
    try:
        stat = fpath.stat()
        with open(fpath, "rb") as f:
            header = f.read(65536)
        # Tylko rozmiar + hash treści — bez mtime, żeby re-download tego samego pliku trafił w cache
        fingerprint = f"{WHISPER_PIPELINE_VERSION}_{stat.st_size}_{hashlib.sha256(header).hexdigest()[:16]}"
        file_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
    except Exception:
        file_hash = "unknown"
    cache_dir = OUTPUT_DIR / "cache"
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir / f"{fpath.stem}_{file_hash}.json"

def load_cache(file_path):
    cp = get_cache_path(file_path)
    if cp.exists():
        try:
            with open(cp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"transcription": "", "segments": [], "translations": {}}

def save_cache(file_path, data):
    cp = get_cache_path(file_path)
    try:
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def ensure_transcription_for_file(file_path):
    """Zwraca tekst i segmenty, korzystając z cache albo uruchamiając Whisper."""
    cache_data = load_cache(str(file_path))
    if cache_data.get("transcription") and cache_data.get("segments"):
        segments = cache_data["segments"]
        _bad = ["dimatorzok", "субтитр", "subscrib", "amara.org", "translation by"]
        segments = [s for s in segments if not any(b in s.get("text", "").lower() for b in _bad)]
        segments = apply_proper_name_glossary_to_segments(segments)
        original_text = "\n".join(s["text"] for s in segments if s.get("text", "").strip())
        if original_text != cache_data.get("transcription") or segments != cache_data.get("segments"):
            cache_data["transcription"] = original_text
            cache_data["segments"] = segments
            save_cache(str(file_path), cache_data)
        return original_text, segments, cache_data, True

    original_text, segments = transcribe_material(
        str(file_path),
        source_lang_hint=LANGUAGES[st.session_state.source_lang]
    )
    segments = apply_proper_name_glossary_to_segments(segments)
    original_text = "\n".join(s["text"] for s in segments if s.get("text", "").strip())
    if original_text:
        cache_data["transcription"] = original_text
        cache_data["segments"] = segments
        save_cache(str(file_path), cache_data)
    return original_text, segments, cache_data, False


def get_or_translate_segment_lines(file_path, original_text, segments, target_lang):
    """Zwraca tłumaczenie 1:1 z segmentami, zapisując je w cache."""
    cache_data = load_cache(str(file_path))
    key = f"seg_translations_{target_lang}"
    cached_val = cache_data.get("translations", {}).get(key)
    if cached_val:
        cached_lines = cached_val if isinstance(cached_val, list) else split_lines_preserve_count(cached_val)
        if len(cached_lines) == len(segments):
            fixed_lines = [apply_proper_name_glossary_to_text(line) for line in cached_lines]
            if fixed_lines != cached_lines:
                cache_data.setdefault("translations", {})[key] = fixed_lines
                save_cache(str(file_path), cache_data)
            return fixed_lines
        cache_data.get("translations", {}).pop(key, None)
        save_cache(str(file_path), cache_data)

    if segments:
        translated = translate_segments_ai(segments, target_lang)
        if translated:
            cache_data.setdefault("translations", {})[key] = translated
            save_cache(str(file_path), cache_data)
        return translated

    translated_text = translate_text_ai(original_text, target_lang)
    return [translated_text] if translated_text else []


def create_subtitle_exports(file_path, target_langs, include_original=True, formats=None):
    """Generuje pliki napisów dla YouTube i zwraca listę ścieżek."""
    formats = [str(f).upper() for f in (formats or ["SRT"]) if str(f).upper() in {"SRT", "VTT"}]
    if not formats:
        formats = ["SRT"]

    original_text, segments, _cache_data, from_cache = ensure_transcription_for_file(file_path)
    if not original_text or not segments:
        return [], None, from_cache

    out_dir = OUTPUT_DIR / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_output_token(Path(file_path).stem)
    outputs = []

    if include_original:
        original_lines = [s.get("text", "") for s in segments]
        original_label = st.session_state.source_lang
        if original_label == "Automatyczne wykrywanie":
            original_label = "Oryginal"
        for fmt in formats:
            out = out_dir / f"{base}_NAPISY_{_safe_output_token(original_label)}.{fmt.lower()}"
            if write_subtitle_file(segments, original_lines, out, fmt):
                outputs.append(out)

    for lang in target_langs or []:
        lines = get_or_translate_segment_lines(file_path, original_text, segments, lang)
        if not lines:
            continue
        for fmt in formats:
            out = out_dir / f"{base}_NAPISY_{_safe_output_token(lang)}.{fmt.lower()}"
            if write_subtitle_file(segments, lines, out, fmt):
                outputs.append(out)

    zip_path = None
    if len(outputs) > 1:
        zip_path = out_dir / f"{base}_NAPISY_YOUTUBE.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for out in outputs:
                zf.write(out, arcname=out.name)

    return outputs, zip_path, from_cache


def short_file_fingerprint(file_path):
    path = Path(file_path)
    try:
        h = hashlib.sha256()
        stat = path.stat()
        h.update(str(stat.st_size).encode("utf-8"))
        with open(path, "rb") as f:
            h.update(f.read(1024 * 1024))
            if stat.st_size > 1024 * 1024:
                f.seek(max(0, stat.st_size - 1024 * 1024))
                h.update(f.read(1024 * 1024))
        return h.hexdigest()[:12]
    except Exception:
        return "unknown"


def get_demucs_cache_dir(file_path):
    """Zwraca trwały katalog cache dla wyników Demucs (vocals/no_vocals) dla danego pliku.
    Używa stabilnego hasha pliku, niezależnego od wersji pipeline'u Whispera."""
    fpath = Path(file_path)
    try:
        stat = fpath.stat()
        with open(fpath, "rb") as f:
            header = f.read(65536)
        fingerprint = f"{stat.st_size}_{hashlib.sha256(header).hexdigest()[:16]}"
        file_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
    except Exception:
        file_hash = "unknown"
    key = f"{fpath.stem}_{file_hash}"
    d = OUTPUT_DIR / "cache" / "demucs" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── WHISPER TRANSKRYPCJA (MLX Whisper large-v3) ───────────────
def transcribe_material(file_path, source_lang_hint=None):
    """Transkrypcja przez MLX Whisper large-v3 w subprocesie. Zwraca (tekst, segmenty)."""
    file_stem  = Path(file_path).stem
    temp_audio = OUTPUT_DIR / f"{file_stem}_whisper_in.wav"
    temp_vad_audio = OUTPUT_DIR / f"{file_stem}_vad_in.wav"

    # Sprawdź czy vocals.wav istnieje — najpierw trwały cache, potem folder roboczy
    _demucs_cached_vocals = get_demucs_cache_dir(file_path) / "vocals.wav"
    vocals_path_work      = OUTPUT_DIR / "htdemucs" / file_stem / "vocals.wav"
    if _demucs_cached_vocals.exists():
        audio_source = str(_demucs_cached_vocals)
    elif vocals_path_work.exists():
        audio_source = str(vocals_path_work)
    else:
        audio_source = str(file_path)

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_source,
            # Preprocessing audio dla lepszej transkrypcji:
            # highpass odcina bas/muzykę, loudnorm normalizuje głośność cichych fragmentów
            "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(temp_audio)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_source,
            # Osobny tor VAD bez loudnorm: loudnorm podbija ciszę i prowokuje halucynacje.
            "-af", "highpass=f=80",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(temp_vad_audio)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except FileNotFoundError:
        st.error("❌ Nie znaleziono ffmpeg. Zainstaluj: brew install ffmpeg")
        return "", []
    except subprocess.CalledProcessError:
        st.error("❌ Błąd ekstrakcji audio.")
        return "", []

    if not temp_audio.exists():
        return "", []
    if not temp_vad_audio.exists():
        temp_vad_audio = temp_audio

    run_id      = uuid.uuid4().hex[:8]
    result_file = OUTPUT_DIR / f"whisper_result_{run_id}.json"
    runner_path = OUTPUT_DIR / f"whisper_runner_{run_id}.py"
    precise     = st.session_state.get("whisper_precise", True)

    runner_code = f"""
import sys, json, re
try:
    import mlx_whisper
except ImportError:
    print("Brak mlx_whisper. Zainstaluj: pip install mlx-whisper", file=sys.stderr)
    sys.exit(1)

kwargs = {{"path_or_hf_repo": "mlx-community/whisper-large-v3-mlx"}}
if {repr(source_lang_hint)}:
    kwargs["language"] = {repr(source_lang_hint)}
if {precise}:
    kwargs["word_timestamps"] = True
kwargs["condition_on_previous_text"] = False  # nie przenoś kontekstu przez ciszę
kwargs["temperature"] = 0.0                   # bez fallbacku na kreatywne temperatury
kwargs["no_speech_threshold"] = 0.55          # mniej false-positive na ciszy niż 0.3
kwargs["logprob_threshold"] = -0.85
kwargs["compression_ratio_threshold"] = 2.2
kwargs["hallucination_silence_threshold"] = 1.0

MAX_SEG_DURATION = 12.0   # sekundy — segmenty dłuższe niż to będą dzielone
COMMON_SHORT_HALLUCINATIONS = {{
    "dziekuje", "dziekuje.", "dziękuję", "dziękuję.",
    "dziekuje za ogladanie", "dziękuję za oglądanie",
    "dzieki za ogladanie", "dzięki za oglądanie",
    "dzieki za obejrzenie", "dzięki za obejrzenie",
    "thanks for watching", "thank you for watching",
    "dzien dobry", "dzień dobry", "dzień dobry.",
}}
SUBTITLE_HALLUCINATION_PATTERNS = [
    "napisy by", "napisy:", "napisy wykon", "napisy stwor",
    "jacek makarewicz", "jazek makarewicz",
    "subtitles by", "captions by", "captioning by",
    "amara.org", "opensubtitles",
]

def _norm_text(txt):
    return re.sub(r"\\s+", " ", str(txt).strip().lower().replace("ę", "e").replace("ń", "n").replace("ą", "a").replace("ł", "l").replace("ó", "o").replace("ś", "s").replace("ć", "c").replace("ż", "z").replace("ź", "z")).strip(" .,!?:;")

def _looks_like_known_hallucination(txt):
    norm = _norm_text(txt)
    if norm in COMMON_SHORT_HALLUCINATIONS:
        return True
    return any(pat in norm for pat in SUBTITLE_HALLUCINATION_PATTERNS)

def _looks_like_subtitle_hallucination(txt):
    norm = _norm_text(txt)
    return any(pat in norm for pat in SUBTITLE_HALLUCINATION_PATTERNS)

def _levels_db(audio_path, start, dur):
    out = _sp.run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-ss", f"{{max(0.0, start):.3f}}", "-t", f"{{max(0.05, dur):.3f}}",
        "-i", audio_path, "-af", "volumedetect", "-f", "null", "-"
    ], capture_output=True, text=True)
    mean_m = re.search(r"mean_volume:\\s*(-?[\\d.]+) dB", out.stderr)
    max_m = re.search(r"max_volume:\\s*(-?[\\d.]+) dB", out.stderr)
    mean_db = float(mean_m.group(1)) if mean_m else -120.0
    max_db = float(max_m.group(1)) if max_m else -120.0
    return mean_db, max_db

def _segment_quality_bad(seg):
    avg = seg.get("avg_logprob")
    nsp = seg.get("no_speech_prob")
    comp = seg.get("compression_ratio")
    if avg is not None and avg < -1.0:
        return True
    if nsp is not None and nsp > 0.65:
        return True
    if nsp is not None and avg is not None and nsp > 0.45 and avg < -0.55:
        return True
    if comp is not None and comp > 2.4:
        return True
    return False

def split_long_segment(seg, max_dur):
    \"\"\"Dzieli długi segment Whisper na krótsze używając word_timestamps.\"\"\"
    dur = seg["end"] - seg["start"]
    if dur <= max_dur:
        return [seg]
    words = [w for w in seg.get("words", []) if "start" in w and "end" in w]
    if not words:
        # Brak słów — dziel proporcjonalnie wg tekstu
        mid_time = seg["start"] + dur / 2
        txt = seg["text"].strip()
        mid_char = len(txt) // 2
        # Znajdź granicę słowa
        sp = txt.rfind(" ", 0, mid_char)
        if sp < 0: sp = mid_char
        return [
            {{"start": seg["start"], "end": mid_time, "text": txt[:sp].strip()}},
            {{"start": mid_time,     "end": seg["end"],   "text": txt[sp:].strip()}},
        ]
    # Dziel po słowach zachowując max_dur
    result, chunk_start, chunk_words = [], words[0]["start"], []
    for w in words:
        chunk_words.append(w)
        chunk_dur = w["end"] - chunk_start
        if chunk_dur >= max_dur:
            result.append({{
                "start": chunk_start,
                "end":   w["end"],
                "text":  " ".join(x.get("word", x.get("text", "")).strip() for x in chunk_words),
                "words": chunk_words
            }})
            chunk_start = w["end"]
            chunk_words = []
    if chunk_words:
        result.append({{
            "start": chunk_start,
            "end":   words[-1]["end"],
            "text":  " ".join(x.get("word", x.get("text", "")).strip() for x in chunk_words),
            "words": chunk_words
        }})
    return result

try:
    import subprocess as _sp, tempfile as _tmp, os as _os

    result = mlx_whisper.transcribe({repr(str(temp_audio))}, **kwargs)

    raw_segments = []
    for s in result.get("segments", []):
        txt = s["text"].strip()
        if not txt:
            continue
        start = s["start"]
        end   = s["end"]
        words = s.get("words", [])
        if words:
            valid = [w for w in words if "start" in w and "end" in w]
            if valid:
                start = valid[0]["start"]
                end   = valid[-1]["end"]
        raw_segments.append({{
            "start": start, "end": end, "text": txt, "words": words,
            "avg_logprob": s.get("avg_logprob"),
            "no_speech_prob": s.get("no_speech_prob"),
            "compression_ratio": s.get("compression_ratio"),
            "source": "main",
        }})

    # ── GAP FILLING ──────────────────────────────────────────────────────
    # Po głównej transkrypcji szukamy dużych luk (>8s) i retranskrybujemy
    # tylko te fragmenty — rozwiązuje problem z pomijaniem mowy po ciszy.
    GAP_MIN = 8.0   # sekund — luki większe niż to są podejrzane

    # Pobierz długość całego pliku audio
    try:
        import wave as _wave
        with _wave.open({repr(str(temp_audio))}, "rb") as _wf:
            _total_dur = _wf.getnframes() / float(_wf.getframerate())
    except Exception:
        _total_dur = raw_segments[-1]["end"] if raw_segments else 0

    # Zbuduj listę luk: (gap_start, gap_end)
    gap_zones = []
    # Luka przed pierwszym segmentem (jeśli > GAP_MIN od początku)
    if raw_segments and raw_segments[0]["start"] > GAP_MIN:
        gap_zones.append((0.0, raw_segments[0]["start"]))
    # Luki między segmentami
    for i in range(len(raw_segments) - 1):
        gap_start = raw_segments[i]["end"]
        gap_end   = raw_segments[i + 1]["start"]
        if gap_end - gap_start > GAP_MIN:
            gap_zones.append((gap_start, gap_end))
    # Luka po ostatnim segmencie
    if raw_segments and _total_dur - raw_segments[-1]["end"] > GAP_MIN:
        gap_zones.append((raw_segments[-1]["end"], _total_dur))

    # Dla każdej luki: wytnij fragment audio i retranskrybuj
    def _get_speech_windows(audio_path, gap_s, gap_e, window=3.0):
        # Dzieli lukę na okna co `window` sekund i zwraca tylko te,
        # które zawierają rzeczywistą mowę (nie są w >95% ciche).
        # Cisza nie trafia do Whispera, ale krótka mowa w ciszy jest wyłapywana.
        import re as _re
        windows = []
        t = gap_s
        while t < gap_e:
            w_end = min(t + window, gap_e)
            w_dur = w_end - t
            mean_db, max_db = _levels_db(audio_path, t, w_dur)
            if max_db < -42.0 or mean_db < -58.0:
                print(f"[Whisper] Pomijam okno {{t:.1f}}-{{w_end:.1f}}s (poziom mean={{mean_db:.1f}}dB max={{max_db:.1f}}dB).", flush=True)
                t = w_end
                continue
            sd = _sp.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", f"{{t:.3f}}", "-t", f"{{w_dur:.3f}}",
                "-af", "silencedetect=noise=-40dB:d=0.20",
                "-f", "null", "-"
            ], capture_output=True, text=True)
            silent = sum(
                float(m) for m in
                _re.findall(r"silence_duration:\\s*([\\d.]+)", sd.stderr)
            )
            if silent / w_dur < 0.90:   # okno ma >10% sygnału — bierzemy je
                windows.append((t, w_end))
            else:
                print(f"[Whisper] Pomijam okno {{t:.1f}}-{{w_end:.1f}}s ({{silent/w_dur*100:.0f}}% ciszy).", flush=True)
            t = w_end
        # Scalaj sąsiednie okna żeby uniknąć wielu małych wywołań Whispera
        merged = []
        for ws, we in windows:
            if merged and ws <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], we)
            else:
                merged.append((ws, we))
        return merged

    for gap_start, gap_end in gap_zones:
        gap_dur = gap_end - gap_start
        print(f"[Whisper] Gap {{gap_start:.1f}}-{{gap_end:.1f}}s ({{gap_dur:.1f}}s) — analiza okien mowy...", flush=True)

        # ── PODZIEL LUKĘ NA OKNA I SPRAWDŹ ENERGIĘ ───────────────────────
        try:
            speech_windows = _get_speech_windows(
                {repr(str(temp_vad_audio))}, gap_start, gap_end, window=3.0
            )
        except Exception as _e:
            print(f"[Whisper] Błąd analizy okien: {{_e}} — retranskrybuję całość.", flush=True)
            speech_windows = [(gap_start, gap_end)]

        if not speech_windows:
            print(f"[Whisper] Gap {{gap_start:.1f}}-{{gap_end:.1f}}s — brak mowy — pomijam.", flush=True)
            continue

        # Transkrybuj każde okno z mową osobno
        for win_start, win_end in speech_windows:
            win_dur = win_end - win_start
            print(f"[Whisper] Transkrybuję okno {{win_start:.1f}}-{{win_end:.1f}}s...", flush=True)

            gap_wav = {repr(str(temp_audio))}.replace(".wav", f"_gap_{{int(win_start)}}.wav")
            ret = _sp.run([
                "ffmpeg", "-y", "-i", {repr(str(temp_audio))},
                "-ss", f"{{win_start:.3f}}", "-t", f"{{win_dur:.3f}}",
                "-ar", "16000", "-ac", "1", gap_wav
            ], capture_output=True)

            if ret.returncode != 0 or not _os.path.exists(gap_wav):
                continue

            try:
                gap_result = mlx_whisper.transcribe(gap_wav, **kwargs)
                for s in gap_result.get("segments", []):
                    txt = s["text"].strip()
                    if not txt:
                        continue
                    seg_start = s["start"] + win_start
                    seg_end   = s["end"]   + win_start
                    candidate = {{
                        "start": seg_start,
                        "end": seg_end,
                        "text": txt,
                        "avg_logprob": s.get("avg_logprob"),
                        "no_speech_prob": s.get("no_speech_prob"),
                        "compression_ratio": s.get("compression_ratio"),
                        "source": "gap",
                    }}
                    mean_db, max_db = _levels_db({repr(str(temp_vad_audio))}, seg_start, max(0.1, seg_end - seg_start))
                    norm = _norm_text(txt)
                    if max_db < -42.0 or mean_db < -58.0:
                        print(f"[Whisper] Odrzucono gap — za cicho [{{seg_start:.1f}}-{{seg_end:.1f}}s, mean={{mean_db:.1f}}dB max={{max_db:.1f}}dB]: {{txt!r}}", flush=True)
                        continue
                    if _segment_quality_bad(candidate):
                        print(f"[Whisper] Odrzucono gap — niska pewność [{{seg_start:.1f}}-{{seg_end:.1f}}s, avg={{candidate.get('avg_logprob')}}, nsp={{candidate.get('no_speech_prob')}}]: {{txt!r}}", flush=True)
                        continue
                    if _looks_like_known_hallucination(txt) and (seg_end - seg_start) < 6.0:
                        print(f"[Whisper] Odrzucono typową krótką halucynację gap [{{seg_start:.1f}}-{{seg_end:.1f}}s]: {{txt!r}}", flush=True)
                        continue
                    words = s.get("words", [])
                    if words:
                        valid = [w for w in words if "start" in w and "end" in w]
                        if valid:
                            seg_start = valid[0]["start"] + win_start
                            seg_end   = valid[-1]["end"]   + win_start
                            candidate["start"] = seg_start
                            candidate["end"] = seg_end
                    candidate["words"] = []
                    raw_segments.append(candidate)
                    print(f"[Whisper] Gap znalazł: {{seg_start:.1f}}-{{seg_end:.1f}}s: {{txt[:60]!r}}", flush=True)
            finally:
                try: _os.remove(gap_wav)
                except Exception: pass

    # Sortuj wszystkie segmenty chronologicznie
    raw_segments.sort(key=lambda x: x["start"])
    # ── KONIEC GAP FILLING ───────────────────────────────────────────────

    # Podziel zbyt długie segmenty
    segments = []
    for s in raw_segments:
        chunks = split_long_segment(s, MAX_SEG_DURATION)
        for c in chunks:
            if c["text"].strip():
                segments.append({{
                    "start": c["start"], "end": c["end"], "text": c["text"].strip(),
                    "avg_logprob": c.get("avg_logprob", s.get("avg_logprob")),
                    "no_speech_prob": c.get("no_speech_prob", s.get("no_speech_prob")),
                    "compression_ratio": c.get("compression_ratio", s.get("compression_ratio")),
                    "source": c.get("source", s.get("source", "main")),
                }})

    # ── SCALANIE SEGMENTÓW ───────────────────────────────────────────────────
    # Reguła 1: scal jeśli segment jest krótki (< 3 znaki lub < 0.4s) i gap < 0.35s
    # Reguła 2: scal jeśli gap między segmentami < 0.15s (mowa bez przerwy = jedno zdanie)
    MIN_SEG_CHARS  = 3
    MIN_SEG_DUR    = 0.4
    MAX_MERGE_GAP  = 0.35   # dla krótkich segmentów
    ZERO_GAP       = 0.15   # dla wszystkich segmentów — mniejsza przerwa = to samo zdanie
    MAX_MERGE_DUR  = 8.0    # nie scalaj jeśli wynikowy segment przekraczałby tyle sekund
    merged = []
    for seg in segments:
        txt = seg["text"].strip()
        dur = seg["end"] - seg["start"]
        is_tiny = (len(txt) <= MIN_SEG_CHARS) or (dur < MIN_SEG_DUR)
        if merged:
            gap = seg["start"] - merged[-1]["end"]
            combined_dur = seg["end"] - merged[-1]["start"]
            # Scal jeśli: (segment krótki i gap mały) LUB (gap praktycznie zero i razem nie za długo)
            should_merge = (
                (is_tiny and gap <= MAX_MERGE_GAP) or
                (gap <= ZERO_GAP and combined_dur <= MAX_MERGE_DUR)
            )
            if should_merge:
                merged[-1]["text"] = merged[-1]["text"].rstrip() + " " + txt
                merged[-1]["end"] = seg["end"]
            else:
                merged.append(dict(seg))
        else:
            merged.append(dict(seg))
    segments = merged

    # Usuń duplikaty — np. "Aleksa" zaraz przed "Alexa, ustaw..."
    deduped = []
    for i, seg in enumerate(segments):
        if deduped:
            prev = deduped[-1]
            # Jeśli teksty są bardzo podobne lub jeden jest podciągiem drugiego
            t1 = seg["text"].lower().strip("., ")
            t2 = prev["text"].lower().strip("., ")
            if t1 in t2 or t2 in t1 or (len(t1) < 8 and t1 in seg["text"][:15].lower()):
                # Połącz — zachowaj dłuższy tekst z szerszym zakresem czasu
                if len(seg["text"]) > len(prev["text"]):
                    deduped[-1]["text"] = seg["text"]
                deduped[-1]["end"] = seg["end"]
                continue
        deduped.append(dict(seg))
    segments = deduped

    # Filtruj halucynacje Whisper: rosyjskie napisy, powtarzające się frazy
    HALLUCINATION_PATTERNS = [
        "\u0421\u0443\u0431\u0442\u0438\u0442\u0440",  # "Субтитр"
        "DimaTorzok", "Subscrib", "Subscribe",
        "amara.org", "Translation by",
    ]
    segments = [
        s for s in segments
        if not any(pat.lower() in s["text"].lower() for pat in HALLUCINATION_PATTERNS)
    ]

    # Filtruj podejrzane segmenty: bardzo długi czas trwania przy bardzo krótkim tekście
    # np. "Dziękuje za oglądanie!" rozciągnięte na 30 sekund = halucynacja w ciszy
    def _is_suspicious(s):
        dur  = s["end"] - s["start"]
        tlen = len(s["text"].strip())
        norm = _norm_text(s["text"])
        source = s.get("source", "")
        nsp = s.get("no_speech_prob")
        # Heurystyka: normalna mowa to ~10-15 znaków na sekundę;
        # jeśli segment trwa >10s a tekst ma <30 znaków — halucynacja
        if dur > 10.0 and tlen < 30:
            return True
        # Jeśli segment trwa >20s a tekst ma <60 znaków — halucynacja
        if dur > 20.0 and tlen < 60:
            return True
        # MLX Whisper potrafi przepuścić ciszę, gdy avg_logprob jest dobry.
        # Dlatego no_speech_prob filtrujemy po transkrypcji także dla głównego przebiegu.
        if nsp is not None and nsp >= 0.80 and dur < 8.0:
            return True
        if nsp is not None and nsp >= 0.60 and _looks_like_known_hallucination(s["text"]):
            return True
        if _looks_like_subtitle_hallucination(s["text"]):
            return True
        if source == "gap" and _segment_quality_bad(s):
            return True
        if source == "gap" and _looks_like_known_hallucination(s["text"]) and dur < 6.0:
            return True
        if source == "gap" and norm in COMMON_SHORT_HALLUCINATIONS and dur < 5.0:
            return True
        if dur < 2.5 and (source == "gap" or norm in COMMON_SHORT_HALLUCINATIONS):
            mean_db, max_db = _levels_db({repr(str(temp_vad_audio))}, s["start"], max(0.1, dur))
            if max_db < -42.0 or mean_db < -58.0:
                return True
        return False

    suspicious = [s for s in segments if _is_suspicious(s)]
    if suspicious:
        for s in suspicious:
            print(f"[Whisper] Usunięto podejrzany segment [{{s['start']:.1f}}-{{s['end']:.1f}}s, {{len(s['text'])}} znaków]: {{s['text'][:60]!r}}", flush=True)
    segments = [s for s in segments if not _is_suspicious(s)]

    # Pełny tekst = każdy segment na osobnej linii (wymagane dla sync per-segment)
    full_text = "\\n".join(s["text"] for s in segments)

    with open({repr(str(result_file))}, "w", encoding="utf-8") as f:
        json.dump({{"text": full_text, "segments": segments}}, f, ensure_ascii=False)
    sys.exit(0)
except Exception as e:
    import traceback
    print(traceback.format_exc(), file=sys.stderr)
    sys.exit(1)
"""

    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    env = os.environ.copy()
    env.update({
        "PYTORCH_ENABLE_MPS_FALLBACK":         "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "PYTHONHTTPSVERIFY":                   "0",
        "HF_HUB_DISABLE_SSL_VERIFICATION":     "1",
    })

    process = subprocess.run(
        [sys.executable, str(runner_path)],
        env=env, capture_output=True, text=True
    )

    for p in [temp_audio, temp_vad_audio, runner_path]:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass

    if result_file.exists():
        with open(result_file, encoding="utf-8") as f:
            data = json.load(f)
        try: result_file.unlink(missing_ok=True)
        except Exception: pass
        return data.get("text", ""), data.get("segments", [])
    else:
        st.error(f"❌ Whisper subprocess nieudany.\n{process.stderr[:500]}")
        return "", []


# ── LOG I URUCHAMIANIE POLECEŃ ────────────────────────────────
def log_message(msg, log_area, force_update=False):
    global _g_timer_ph
    if log_area is None:
        return
    msg_str = str(msg).strip()
    if msg_str:
        trash = [
            "pad_token_id", "eos_token_id", "The attention mask", "You are using a model",
            "Late SEI is not implemented", "h264 @", "SEI type", "If you want to help",
            "Update your FFmpeg", "streams.videolan", "upload a sample",
        ]
        if any(k in msg_str for k in trash):
            return
        if not isinstance(st.session_state.full_logs, deque):
            st.session_state.full_logs = deque(maxlen=200)
        st.session_state.full_logs.append(msg_str)

    now = time.time()
    should_update = (now - st.session_state.last_log_update > 0.4 or force_update)

    # Aktualizuj timer niezależnie od show_log — działa przez Python, bez JS
    if should_update and _g_timer_ph is not None:
        t0 = st.session_state.get("pipeline_start_time")
        if t0:
            elapsed = int(now - t0)
            _m, _s = divmod(elapsed, 60)
            try:
                _g_timer_ph.markdown(
                    f"<div style='color:#888;font-size:0.82rem;margin-top:4px;'>"
                    f"⏱️ {_m:02d}:{_s:02d}</div>",
                    unsafe_allow_html=True
                )
            except Exception:
                pass

    # WAŻNE: check show_log DOPIERO po zebraniu logu.
    # Return wcześniej blokowałby pipe subprocesów i zawieszał je.
    if not st.session_state.get("show_log", True):
        if should_update:
            st.session_state.last_log_update = now
        return

    if should_update:
        lines   = list(st.session_state.full_logs)
        escaped = "\n".join(lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        log_area.code(escaped)
        st.session_state.last_log_update = now

def _log_stream_chunk(chunk, stream_buffer, log_area, prefix="", line_callback=None):
    if not chunk:
        return stream_buffer
    stream_buffer += chunk.replace("\r", "\n")
    while "\n" in stream_buffer:
        line, stream_buffer = stream_buffer.split("\n", 1)
        clean = line.strip()
        if clean:
            if line_callback:
                try:
                    line_callback(clean)
                except Exception:
                    pass
            log_message(f"{prefix}{clean}", log_area)
    return stream_buffer


def _stream_process(cmd_list, env, log_area=None, cwd=None, timeout=None,
                    idle_timeout=1800, prefix="", line_callback=None):
    process = subprocess.Popen(
        cmd_list, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=cwd, start_new_session=True
    )
    st.session_state.current_process = process
    sel = selectors.DefaultSelector()
    if process.stdout:
        os.set_blocking(process.stdout.fileno(), False)
        sel.register(process.stdout, selectors.EVENT_READ)

    t0 = time.time()
    last_output = t0
    buffer = ""
    rc = 1
    try:
        while True:
            if st.session_state.get("cancel_requested"):
                log_message("[STOP] Zatrzymano przez użytkownika.", log_area, True)
                _kill_process_tree(process)
                rc = 1
                break

            now = time.time()
            if timeout and now - t0 > timeout:
                log_message(f"[TIMEOUT] Proces przekroczył limit {int(timeout)}s.", log_area, True)
                _kill_process_tree(process)
                rc = 1
                break
            if idle_timeout and now - last_output > idle_timeout:
                log_message(f"[TIMEOUT] Brak logów przez {int(idle_timeout)}s — przerywam proces.", log_area, True)
                _kill_process_tree(process)
                rc = 1
                break

            events = sel.select(timeout=0.25)
            for key, _ in events:
                try:
                    data = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    data = b""
                if data:
                    last_output = time.time()
                    buffer = _log_stream_chunk(
                        data.decode("utf-8", errors="replace"),
                        buffer, log_area, prefix, line_callback
                    )

            if process.poll() is not None:
                for key, _ in sel.select(timeout=0):
                    try:
                        data = os.read(key.fileobj.fileno(), 8192)
                    except BlockingIOError:
                        data = b""
                    if data:
                        buffer = _log_stream_chunk(
                            data.decode("utf-8", errors="replace"),
                            buffer, log_area, prefix, line_callback
                        )
                rc = process.returncode if process.returncode is not None else 1
                break
    finally:
        if buffer.strip():
            clean_tail = buffer.strip()
            if line_callback:
                try:
                    line_callback(clean_tail)
                except Exception:
                    pass
            log_message(f"{prefix}{clean_tail}", log_area)
        try:
            sel.close()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
        except Exception:
            _kill_process_tree(process)
        st.session_state.current_process = None
    return rc


def run_command(cmd_list, log_area=None, cwd=None, extra_env=None, timeout=None, idle_timeout=1800,
                line_callback=None):
    env = os.environ.copy()
    env.update({
        "PYTHONHTTPSVERIFY":                "0",
        "SSL_CERT_FILE":                    certifi.where(),
        "REQUESTS_CA_BUNDLE":               certifi.where(),
        "HF_HUB_DISABLE_SSL_VERIFICATION":  "1",
        "PYTORCH_ENABLE_MPS_FALLBACK":      "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "DYLD_LIBRARY_PATH":                "",
    })
    if extra_env:
        env.update(extra_env)

    exe = cmd_list[0]
    if not shutil.which(exe, path=env.get("PATH", "")):
        alt = os.path.join(os.path.dirname(sys.executable), exe)
        if os.path.exists(alt):
            cmd_list[0] = alt

    try:
        rc = _stream_process(cmd_list, env, log_area=log_area, cwd=cwd,
                             timeout=timeout, idle_timeout=idle_timeout,
                             line_callback=line_callback)
        if st.session_state.get("cancel_requested"):
            cleanup_mps()
            st.session_state.cancel_requested = False
            return 1
        cleanup_mps()
        log_message("", log_area, force_update=True)
        return rc
    except FileNotFoundError as e:
        log_message(f"[BŁĄD] Nie znaleziono: '{cmd_list[0]}' — {e}", log_area, True)
        return 127
    except Exception as e:
        log_message(f"[BŁĄD] {e}", log_area, True)
        return 1


_FFMPEG_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

def parse_ffmpeg_time_seconds(line):
    m = _FFMPEG_TIME_RE.search(line or "")
    if not m:
        return None
    try:
        hours = int(m.group(1))
        minutes = int(m.group(2))
        seconds = float(m.group(3))
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return None


# ── AUDIO UTILITIES ───────────────────────────────────────────
def find_best_speech_window(input_wav, duration=12):
    """Znajdź pierwszy ciągły fragment mowy w pliku audio. Zwraca (start_sec, end_sec).

    Strategia: szukamy pierwszego miejsca gdzie mowa trwa nieprzerwanie
    przez co najmniej 'duration' sekund — bez długich pauz i ciszy.
    To daje naturalną próbkę głosu bez przekłamań wynikających z wyboru
    najgłośniejszego/najbardziej energetycznego fragmentu.
    """
    try:
        import numpy as np
        with wave.open(str(input_wav), "rb") as wf:
            sr        = wf.getframerate()
            ch        = wf.getnchannels()
            raw       = wf.readframes(wf.getnframes())
            total_dur = wf.getnframes() / sr
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        del raw   # FIX: zwolnij surowe bajty — data to kopia
        if ch == 2:
            data = data.reshape(-1, 2).mean(axis=1)

        if total_dur <= duration:
            del data
            return 0.0, total_dur

        # ── DETEKCJA CISZY ────────────────────────────────────────
        win_s      = int(sr * 0.050)
        silence_db = 0.018
        is_speech = []
        for s in range(0, len(data) - win_s, win_s):
            rms = float(np.sqrt(np.mean(data[s:s + win_s] ** 2)))
            is_speech.append(rms > silence_db)
        del data   # FIX: zwolnij ~66 MB — is_speech to prosta lista boolów

        step_s = 0.050  # sekund na krok

        # ── SZUKAJ PIERWSZEGO OKNA BEZ DŁUGIEJ PRZERWY ────────────
        # "Długa przerwa" = >0.8s ciszy z rzędu wewnątrz okna
        max_silence_steps = int(0.8 / step_s)   # 16 kroków = 0.8s
        required_steps    = int(duration / step_s)

        best_start = None

        for start_step in range(len(is_speech) - required_steps):
            window = is_speech[start_step : start_step + required_steps]

            # Sprawdź czy w oknie jest zbyt długa cisza
            max_run = 0
            cur_run = 0
            for v in window:
                if not v:
                    cur_run += 1
                    max_run = max(max_run, cur_run)
                else:
                    cur_run = 0

            # Sprawdź czy okno zaczyna się od mowy (nie od ciszy)
            leading_silence = 0
            for v in window:
                if not v:
                    leading_silence += 1
                else:
                    break

            if max_run <= max_silence_steps and leading_silence <= int(0.3 / step_s):
                best_start = start_step
                break

        # Fallback: jeśli nie znaleziono idealnego okna — weź od początku
        if best_start is None:
            best_start = 0

        start_sec = best_start * step_s
        # Przesuń start do pierwszej klatki mowy (eliminuje wiodącą ciszę)
        end_sec = min(start_sec + duration, total_dur)
        return start_sec, end_sec

    except Exception:
        return 0.0, duration


def optimize_audio_reference(input_wav, output_wav, duration=12, log_area=None):
    """Czyści i normalizuje próbkę głosu — wybiera pierwszy ciągły fragment mowy."""
    start_sec, _ = find_best_speech_window(input_wav, duration)
    if log_area:
        log_message(
            f"[DubMaster] Próbka głosu: start={start_sec:.1f}s, długość={duration}s, EBU R128...",
            log_area, True
        )
    cmd = [
        "ffmpeg", "-y", "-i", str(input_wav),
        "-ss", str(start_sec),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
        "-t", str(duration), str(output_wav)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(output_wav) and os.path.getsize(output_wav) > 1024:
        return output_wav
    return input_wav

def get_audio_duration(file_path):
    if str(file_path).lower().endswith(".wav"):
        try:
            with wave.open(str(file_path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            pass
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0

_FFMPEG_HAS_RUBBERBAND = None

def _check_rubberband():
    global _FFMPEG_HAS_RUBBERBAND
    if _FFMPEG_HAS_RUBBERBAND is None:
        try:
            r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=5)
            _FFMPEG_HAS_RUBBERBAND = "arubberband" in (r.stdout + r.stderr)
        except Exception:
            _FFMPEG_HAS_RUBBERBAND = False
    return _FFMPEG_HAS_RUBBERBAND

def stretch_pitch_preserving(input_path, output_path, ratio: float) -> int:
    """Zmienia tempo audio zachowując ton. ratio > 1 = szybciej, ratio < 1 = wolniej."""
    if ratio <= 0.01:
        return 1
    if _check_rubberband():
        cmd = ["ffmpeg", "-y", "-i", str(input_path),
               "-filter:a", f"arubberband=tempo={ratio:.5f}:pitch=1.0", str(output_path)]
        ret = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ret.returncode == 0:
            return 0
    # Fallback: atempo (łańcuch dla ratio poza 0.5–2.0)
    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0"); r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5"); r *= 2.0
    filters.append(f"atempo={r:.5f}")
    cmd = ["ffmpeg", "-y", "-i", str(input_path),
           "-filter:a", ",".join(filters), str(output_path)]
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def download_youtube_video(url, max_height, log_area=None):
    if log_area:
        log_message(f"Pobieranie z YouTube (do {max_height}p)...", log_area)
    fmt = (f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best")
    ydl_opts = {
        "format": fmt,
        "outtmpl": str(UPLOAD_DIR / "%(title)s_%(id)s.%(ext)s"),
        "merge_output_format": "mkv",
        "format_sort": [
            "hasaud",
            "res",
            "fps",
            "vcodec:avc:h264",
            "acodec:opus:aac",
            "abr",
            "asr",
        ],
        "quiet": False,
        "no_warnings": True,
        "overwrites": True,
    }
    # Obsługa cookies — szukaj cookies.txt w folderze DubMaster
    cookies_path = BASE_DIR / "cookies.txt"
    if cookies_path.exists():
        ydl_opts["cookiefile"] = str(cookies_path)
        if log_area:
            log_message(f"[YT] Używam cookies.txt ({cookies_path.name})", log_area)
    else:
        if log_area:
            log_message("[YT] Brak cookies.txt — próba bez autoryzacji.", log_area)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            out  = ydl.prepare_filename(info)
            if not os.path.exists(out):
                out = out.rsplit(".", 1)[0] + ".mkv"
            if log_area:
                requested = info.get("requested_downloads") or info.get("requested_formats") or []
                audio_info = next(
                    (f for f in requested if f.get("acodec") and f.get("acodec") != "none"),
                    info if info.get("acodec") and info.get("acodec") != "none" else {},
                )
                acodec = audio_info.get("acodec") or "?"
                abr = audio_info.get("abr")
                asr = audio_info.get("asr")
                detail = f"audio={acodec}"
                if abr:
                    detail += f", ~{abr:g} kb/s"
                if asr:
                    detail += f", {asr:g} Hz"
                log_message(f"✅ Pobieranie zakończone ({detail}).", log_area)
            return out
    except Exception as e:
        if log_area:
            log_message(f"[BŁĄD YouTube]: {e}", log_area)
        return None


# ── QWEN3-TTS RUNNER TEMPLATE ─────────────────────────────────
_TTS_RUNNER_TEMPLATE = '''
import sys, json, gc, os, warnings
import numpy as _np          # FIX: importuj RAZ na górze, nie w każdej iteracji pętli
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

JOB_FILE    = __JOB_FILE__
RESULT_FILE = __RESULT_FILE__

def _audio_duration(audio_arr, audio_sr):
    return (len(audio_arr) / audio_sr) if audio_sr else 0.0

def _normalize_retry_text(txt):
    clean = " ".join((txt or "").split())
    clean = clean.replace(" ;", ";").replace(" ,", ",").replace(" .", ".")
    if clean and clean[-1] not in ".!?…":
        clean += "."
    return clean

def _split_text_for_retry(txt):
    words = [w for w in (txt or "").split() if w.strip()]
    if len(words) < 8:
        return None
    mids = [",", ";", ":", " — ", " - "]
    center = len(txt) // 2
    best = -1
    best_dist = 10**9
    for sep in mids:
        pos = txt.find(sep, max(0, center - 40), min(len(txt), center + 40))
        if pos != -1 and abs(pos - center) < best_dist:
            best = pos + len(sep)
            best_dist = abs(pos - center)
    if best == -1:
        half = len(words) // 2
        return " ".join(words[:half]).strip(), " ".join(words[half:]).strip()
    left = txt[:best].strip()
    right = txt[best:].strip()
    return (left, right) if left and right else None

# FIX: _gen() zdefiniowana RAZ poza pętlą — closure na model/voice_prompt
# przekazywane jako argumenty, nie przechwytywane przez zamknięcie
def _gen(model, qwen_lang, voice_prompt, txt, max_tok,
         temperature_override=0.7, top_p_override=0.85, instruct=None):
    # FIX: temperature=0.7 (nie 0.3) — niższa temp. powoduje pętle repetycji w Qwen3-TTS
    _kwargs_full = dict(
        text=txt, language=qwen_lang,
        voice_clone_prompt=voice_prompt,
        temperature=temperature_override, top_p=top_p_override,
        max_new_tokens=max_tok,
    )
    if instruct:
        _kwargs_full["instruct"] = instruct
    try:
        wavs, sr = model.generate_voice_clone(**_kwargs_full)
    except TypeError:
        # Fallback bez instruct jeśli model go nie obsługuje
        _kwargs_base = dict(
            text=txt, language=qwen_lang,
            voice_clone_prompt=voice_prompt,
            max_new_tokens=max_tok,
        )
        try:
            wavs, sr = model.generate_voice_clone(**_kwargs_base)
        except TypeError:
            wavs, sr = model.generate_voice_clone(
                text=txt, language=qwen_lang,
                voice_clone_prompt=voice_prompt,
            )
    audio = wavs[0]
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    result = audio.copy()
    # FIX: jawnie kasuj oryginał żeby MPS zwolnił pamięć
    del audio, wavs
    return result, sr

def main():
    try:
        import soundfile as sf
        import torch
        import transformers
        import random
        transformers.logging.set_verbosity_error()
        from qwen_tts import Qwen3TTSModel
        torch.set_grad_enabled(False)
        random.seed(12345)
        _np.random.seed(12345)
        torch.manual_seed(12345)
        if torch.backends.mps.is_available():
            try:
                torch.mps.manual_seed(12345)
            except Exception:
                pass
        try:
            import torchaudio
            torchaudio.set_audio_backend("soundfile")
        except Exception:
            pass
    except ImportError as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": str(e)}, f)
        sys.exit(1)

    with open(JOB_FILE, encoding="utf-8") as f:
        job = json.load(f)

    # ── WYBÓR URZĄDZENIA I DTYPE ──
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype  = torch.bfloat16 if device == "mps" else torch.float32

    # ── MODEL ID ──
    model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

    print(f"[TTS] Ładowanie {model_id} ({dtype})...", flush=True)

    # ── ŁADOWANIE MODELU ──
    model = None
    for try_device, try_dtype in [(device, dtype), (device, torch.bfloat16), ("cpu", torch.float32)]:
        try:
            model = Qwen3TTSModel.from_pretrained(model_id, device_map=try_device, dtype=try_dtype)
            device = try_device
            print(f"[TTS] Model załadowany ({try_device}, {try_dtype}).", flush=True)
            break
        except Exception as e:
            print(f"[TTS] Próba ({try_device}, {try_dtype}) nieudana: {e}", flush=True)

    if model is None:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": "Nie można załadować modelu TTS."}, f)
        sys.exit(1)

    ref_audio     = job["ref_audio"]
    qwen_lang     = job["qwen_lang"]
    segs_texts    = job["segments_texts"]
    out_paths     = job["output_paths"]
    seg_durations = job.get("segment_durations", [])
    instruct      = (job.get("style") or "").strip() or None
    total         = len(segs_texts)

    # ── BUDOWANIE VOICE CLONE PROMPT (pełny prompt = stabilniejsza barwa głosu) ──
    print("[TTS] Budowanie profilu głosu (strict clone prompt)...", flush=True)
    voice_prompt = None

    try:
        with torch.no_grad():
            try:
                voice_prompt = model.create_voice_clone_prompt(
                    ref_audio=ref_audio, x_vector_only_mode=False
                )
            except TypeError:
                voice_prompt = model.create_voice_clone_prompt(ref_audio=ref_audio)
            print("[TTS] Strict voice clone prompt gotowy.", flush=True)
    except Exception as e_clone:
        print(f"[TTS] Strict prompt nieudany: {e_clone}. Fallback do X-Vector.", flush=True)
        try:
            with torch.no_grad():
                voice_prompt = model.create_voice_clone_prompt(
                    ref_audio=ref_audio, x_vector_only_mode=True
                )
                print("[TTS] X-Vector prompt gotowy (fallback).", flush=True)
        except Exception as e_xvec:
            with open(RESULT_FILE, "w") as f:
                json.dump({"status": "error", "message": f"Prompt nieudany: {e_xvec}"}, f)
            sys.exit(1)

    if device == "mps":
        torch.mps.empty_cache()
    gc.collect()

    # ── GENEROWANIE SEGMENTÓW ──
    error_count = 0

    for idx, (text, out_path) in enumerate(zip(segs_texts, out_paths)):
        g_idx = job.get("global_offset", 0) + idx + 1
        g_tot = job.get("global_total", total)
        print(f"[PROGRESS] {g_idx}/{g_tot}", flush=True)
        print(f"[TTS] Segment {g_idx}/{g_tot}: {text[:60]!r}", flush=True)

        audio_data = None   # FIX: jawna inicjalizacja przed try, żeby finally mógł posprzątać

        try:
            with torch.no_grad():
                gen_text = text.strip()
                if not gen_text:
                    silence = _np.zeros(int(0.3 * 24000), dtype=_np.float32)
                    sf.write(str(out_path), silence, 24000)
                    continue

                win_dur = seg_durations[idx] if idx < len(seg_durations) else 5.0
                words_n = len([w for w in gen_text.split() if w.strip()])
                chars_n = len(gen_text)
                tok_from_window = int((win_dur * 2.6 + 2.0) * 12)
                tok_from_words  = int(words_n * 8.5)
                tok_from_chars  = int(chars_n * 1.25)
                dyn_max_tokens  = min(4800, max(48, tok_from_window, tok_from_words, tok_from_chars))
                print(
                    f"[TTS] okno={win_dur:.1f}s | slowa={words_n} | znaki={chars_n} → max_tokens={dyn_max_tokens}",
                    flush=True
                )

                # Niższa temperatura stabilizuje barwę głosu między segmentami.
                audio_data, sr = _gen(model, qwen_lang, voice_prompt, gen_text, dyn_max_tokens,
                                      temperature_override=0.55, top_p_override=0.78,
                                      instruct=instruct)

                # ── RETRY jeśli wynik za krótki ──────────────────────────────
                audio_dur    = _audio_duration(audio_data, sr)
                min_expected = max(0.30, min(win_dur * 0.38, max(0.75, win_dur - 0.55)))
                severe_short = max(0.22, min_expected * 0.30)

                if audio_dur < min_expected and win_dur > 0.8:
                    retry_text   = _normalize_retry_text(gen_text)
                    retry_tokens = min(1800, max(int(dyn_max_tokens * 1.55), dyn_max_tokens + 64))
                    print(
                        f"[TTS] Segment {g_idx} za krótki ({audio_dur:.2f}s < {min_expected:.2f}s), retry max_tokens={retry_tokens}...",
                        flush=True
                    )
                    # Retry trochę swobodniejszy, ale nadal pilnuje barwy głosu.
                    audio_retry, sr_retry = _gen(model, qwen_lang, voice_prompt,
                                                 retry_text or gen_text, retry_tokens,
                                                 temperature_override=0.62, top_p_override=0.84)
                    retry_dur = _audio_duration(audio_retry, sr_retry)
                    if retry_dur > audio_dur:
                        del audio_data          # FIX: zwolnij starą tablicę przed zastąpieniem
                        audio_data, sr = audio_retry, sr_retry
                        audio_dur = retry_dur
                    else:
                        del audio_retry         # FIX: retry gorszy — wyczyść go

                    # Jeśli nadal za krótki — rozbij na 2 części i sklej
                    if audio_dur < severe_short:
                        split_pair = _split_text_for_retry(retry_text or gen_text)
                        if split_pair:
                            left_txt, right_txt = split_pair
                            part_tokens = max(56, int(retry_tokens * 0.70))
                            try:
                                print(f"[TTS] Segment {g_idx}: retry przez split tekstu.", flush=True)
                                left_audio,  left_sr  = _gen(model, qwen_lang, voice_prompt,
                                                             left_txt, part_tokens,
                                                             temperature_override=0.62, top_p_override=0.84)
                                right_audio, right_sr = _gen(model, qwen_lang, voice_prompt,
                                                             right_txt, part_tokens,
                                                             temperature_override=0.62, top_p_override=0.84)
                                if left_sr == right_sr and left_sr:
                                    join_gap    = _np.zeros(int(left_sr * 0.040), dtype=_np.float32)
                                    split_audio = _np.concatenate([left_audio, join_gap, right_audio])
                                    split_dur   = _audio_duration(split_audio, left_sr)
                                    del left_audio, right_audio, join_gap  # FIX: zwolnij parts
                                    if split_dur > audio_dur:
                                        del audio_data
                                        audio_data, sr = split_audio, left_sr
                                        audio_dur = split_dur
                                    else:
                                        del split_audio
                                else:
                                    del left_audio, right_audio
                            except Exception as e_split:
                                print(f"[TTS] Segment {g_idx}: split retry nieudany: {e_split}", flush=True)

                    if audio_dur < severe_short:
                        print(f"[TTS] Segment {g_idx}: nadal krótki po retry ({audio_dur:.2f}s).", flush=True)

                # ── RETRY jeśli wynik jest podejrzanie za długi ───────────────
                # Qwen potrafi wejść w pętlę typu "ce que... ce que..." na długich
                # frazach. Taki segment później wymaga agresywnego stretchu i brzmi
                # jak czkawka, więc próbujemy go wygenerować drugi raz stabilniej.
                too_long_limit = max(win_dur * 1.58, win_dur + 2.2)
                if audio_dur > too_long_limit and win_dur > 1.2:
                    retry_text = _normalize_retry_text(gen_text)
                    retry_tokens = max(56, min(dyn_max_tokens, int(dyn_max_tokens * 0.78)))
                    print(
                        f"[TTS] Segment {g_idx} za długi ({audio_dur:.2f}s > {too_long_limit:.2f}s), retry stabilny...",
                        flush=True
                    )
                    audio_retry, sr_retry = _gen(model, qwen_lang, voice_prompt,
                                                 retry_text or gen_text, retry_tokens,
                                                 temperature_override=0.50, top_p_override=0.72)
                    retry_dur = _audio_duration(audio_retry, sr_retry)
                    if retry_dur < audio_dur and retry_dur > max(0.35, win_dur * 0.30):
                        del audio_data
                        audio_data, sr = audio_retry, sr_retry
                        audio_dur = retry_dur
                        print(f"[TTS] Segment {g_idx}: retry przyjęty ({audio_dur:.2f}s).", flush=True)
                    else:
                        del audio_retry
                        print(f"[TTS] Segment {g_idx}: retry odrzucony ({retry_dur:.2f}s).", flush=True)

                # ── POST-PROCESSING ───────────────────────────────────────────
                # 1. Trim leading silence
                silence_thresh = 0.025
                window_s = int(sr * 0.025)
                if window_s > 0:
                    start_idx = 0
                    for w in range(0, min(int(sr * 0.5), len(audio_data) - window_s), window_s):
                        if _np.sqrt(_np.mean(audio_data[w:w + window_s] ** 2)) > silence_thresh:
                            start_idx = w
                            break
                    if start_idx > 0:
                        audio_data = audio_data[start_idx:]

                # 2. Padding 60ms na obu końcach
                pad        = _np.zeros(int(sr * 0.060), dtype=_np.float32)
                audio_data = _np.concatenate([pad, audio_data, pad])
                del pad

                # 3. Fade-in 15ms + fade-out 30ms
                fade_in  = min(int(sr * 0.015), len(audio_data) // 4)
                fade_out = min(int(sr * 0.030), len(audio_data) // 4)
                if fade_in > 0:
                    audio_data[:fade_in] *= _np.linspace(0.0, 1.0, fade_in)
                if fade_out > 0:
                    audio_data[-fade_out:] *= _np.linspace(1.0, 0.0, fade_out)

                sf.write(str(out_path), audio_data, sr)

        except Exception as e:
            import traceback
            print(f"[TTS] BŁĄD segmentu {g_idx}: {e}\\n{traceback.format_exc()}", flush=True)
            error_count += 1

        finally:
            # FIX: zawsze czyść audio_data w finally — nawet gdy wyjątek
            if audio_data is not None:
                del audio_data
                audio_data = None

            if device == "mps":
                torch.mps.synchronize()
                torch.mps.empty_cache()
            gc.collect()

            # Co 8 segmentów — dłuższa pauza na odzyskanie RAM
            if (idx + 1) % 8 == 0:
                import time as _time
                _time.sleep(0.3)
                if device == "mps":
                    torch.mps.empty_cache()
                gc.collect()

    # ── SPRZĄTANIE MODELU ─────────────────────────────────────────
    del voice_prompt, model
    if device == "mps":
        torch.mps.synchronize()
        torch.mps.empty_cache()
    gc.collect()

    with open(RESULT_FILE, "w") as f:
        json.dump({"status": "ok" if error_count == 0 else "partial", "error_count": error_count}, f)

    print(f"[TTS] GOTOWE. Błędy: {error_count}", flush=True)
    sys.exit(0 if error_count == 0 else 1)

if __name__ == "__main__":
    main()
'''


_CUSTOM_VOICE_TTS_RUNNER_TEMPLATE = '''
import sys, json, gc, os, warnings
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

JOB_FILE    = __JOB_FILE__
RESULT_FILE = __RESULT_FILE__

def main():
    try:
        import soundfile as sf
        import torch
        import transformers
        import numpy as np
        import random
        transformers.logging.set_verbosity_error()
        from qwen_tts import Qwen3TTSModel
        torch.set_grad_enabled(False)
        random.seed(12345)
        np.random.seed(12345)
        torch.manual_seed(12345)
        if torch.backends.mps.is_available():
            try:
                torch.mps.manual_seed(12345)
            except Exception:
                pass
    except ImportError as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": str(e)}, f)
        sys.exit(1)

    with open(JOB_FILE, encoding="utf-8") as f:
        job = json.load(f)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype  = torch.bfloat16 if device == "mps" else torch.float32
    model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

    print(f"[TTS] Ładowanie {model_id} ({dtype})...", flush=True)
    model = None
    for try_device, try_dtype in [(device, dtype), (device, torch.bfloat16), ("cpu", torch.float32)]:
        try:
            model = Qwen3TTSModel.from_pretrained(model_id, device_map=try_device, dtype=try_dtype)
            device = try_device
            print(f"[TTS] Model załadowany ({try_device}, {try_dtype}).", flush=True)
            break
        except Exception as e:
            print(f"[TTS] Próba ({try_device}, {try_dtype}) nieudana: {e}", flush=True)

    if model is None:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": "Nie można załadować modelu CustomVoice."}, f)
        sys.exit(1)

    text     = (job.get("text") or "").strip()
    language = job.get("qwen_lang", "Auto")
    speaker  = job.get("speaker", "Ryan")
    instruct = (job.get("style") or "").strip() or None
    out_path = job["output_path"]

    if not text:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": "Brak tekstu."}, f)
        sys.exit(1)

    words_n = len([w for w in text.split() if w.strip()])
    chars_n = len(text)
    max_tokens = min(2400, max(96, int(words_n * 10), int(chars_n * 1.45)))
    print(f"[TTS] Głos Qwen: {speaker} | język={language} | max_tokens={max_tokens}", flush=True)

    try:
        with torch.no_grad():
            wavs, sr = model.generate_custom_voice(
                text=text,
                language=language,
                speaker=speaker,
                instruct=instruct,
                temperature=0.7,
                top_p=0.85,
                max_new_tokens=max_tokens,
            )
        audio = wavs[0]
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        audio = audio.astype("float32")
        pad = np.zeros(int(sr * 0.060), dtype=np.float32)
        audio = np.concatenate([pad, audio, pad])
        fade_in = min(int(sr * 0.015), len(audio) // 4)
        fade_out = min(int(sr * 0.030), len(audio) // 4)
        if fade_in > 0:
            audio[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
        if fade_out > 0:
            audio[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
        sf.write(str(out_path), audio, sr)
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "ok", "sample_rate": sr}, f)
        print("[TTS] GOTOWE.", flush=True)
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"[TTS] BŁĄD: {e}\\n{traceback.format_exc()}", flush=True)
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": str(e)}, f)
        sys.exit(1)
    finally:
        del model
        if device == "mps":
            torch.mps.synchronize()
            torch.mps.empty_cache()
        gc.collect()

if __name__ == "__main__":
    main()
'''


_PRESET_TTS_BATCH_RUNNER_TEMPLATE = '''
import sys, json, gc, os, warnings
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

JOB_FILE    = __JOB_FILE__
RESULT_FILE = __RESULT_FILE__

def main():
    try:
        import soundfile as sf
        import torch
        import transformers
        import numpy as np
        import random
        transformers.logging.set_verbosity_error()
        from qwen_tts import Qwen3TTSModel
        torch.set_grad_enabled(False)
        random.seed(12345)
        np.random.seed(12345)
        torch.manual_seed(12345)
        if torch.backends.mps.is_available():
            try:
                torch.mps.manual_seed(12345)
            except Exception:
                pass
    except ImportError as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": str(e), "error_count": 1}, f)
        sys.exit(1)

    with open(JOB_FILE, encoding="utf-8") as f:
        job = json.load(f)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype  = torch.bfloat16 if device == "mps" else torch.float32
    model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

    print(f"[TTS] Ładowanie {model_id} ({dtype})...", flush=True)
    model = None
    for try_device, try_dtype in [(device, dtype), (device, torch.bfloat16), ("cpu", torch.float32)]:
        try:
            model = Qwen3TTSModel.from_pretrained(model_id, device_map=try_device, dtype=try_dtype)
            device = try_device
            print(f"[TTS] Model załadowany ({try_device}, {try_dtype}).", flush=True)
            break
        except Exception as e:
            print(f"[TTS] Próba ({try_device}, {try_dtype}) nieudana: {e}", flush=True)

    if model is None:
        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "error", "message": "Nie można załadować modelu preset Qwen.", "error_count": 1}, f)
        sys.exit(1)

    texts = job.get("segments_texts", [])
    out_paths = job.get("output_paths", [])
    seg_durations = job.get("segment_durations", [])
    language = job.get("qwen_lang", "Auto")
    speaker = job.get("speaker", "Ryan")
    instruct = (job.get("style") or "").strip() or None
    temperature = float(job.get("temperature", 0.7))
    top_p = float(job.get("top_p", 0.85))
    strict_timing = bool(job.get("strict_timing", False))
    total = min(len(texts), len(out_paths))
    error_count = 0
    print(f"[TTS] Głos Qwen: {speaker} | segmenty={total} | język={language}", flush=True)

    try:
        for idx in range(total):
            text = " ".join((texts[idx] or "").split()).strip()
            out_path = out_paths[idx]
            if not text:
                sf.write(str(out_path), np.zeros(1200, dtype=np.float32), 24000)
                print(f"[PROGRESS] {idx+1}/{total}", flush=True)
                continue
            words_n = len([w for w in text.split() if w.strip()])
            chars_n = len(text)
            target_dur = float(seg_durations[idx]) if idx < len(seg_durations) else 5.0
            if strict_timing:
                text_need = max(32, int(words_n * 7.5), int(chars_n * 0.90))
                duration_cap = max(36, int((target_dur + 0.65) * 14))
                max_tokens = min(1800, max(32, min(text_need, duration_cap)))
            else:
                max_tokens = min(2400, max(96, int(words_n * 10), int(chars_n * 1.45)))
            try:
                with torch.no_grad():
                    wavs, sr = model.generate_custom_voice(
                        text=text,
                        language=language,
                        speaker=speaker,
                        instruct=instruct,
                        temperature=temperature,
                        top_p=top_p,
                        max_new_tokens=max_tokens,
                    )
                audio = wavs[0]
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                audio = audio.astype("float32")
                if strict_timing:
                    max_samples = int(sr * max(0.35, target_dur + 0.25))
                    if len(audio) > max_samples:
                        audio = audio[:max_samples]
                        trim_fade = min(int(sr * 0.035), len(audio) // 5)
                        if trim_fade > 0:
                            audio[-trim_fade:] *= np.linspace(1.0, 0.0, trim_fade)
                pad = np.zeros(int(sr * 0.060), dtype=np.float32)
                audio = np.concatenate([pad, audio, pad])
                fade_in = min(int(sr * 0.015), len(audio) // 4)
                fade_out = min(int(sr * 0.030), len(audio) // 4)
                if fade_in > 0:
                    audio[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
                if fade_out > 0:
                    audio[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
                sf.write(str(out_path), audio, sr)
                del audio, wavs
            except Exception as e:
                error_count += 1
                print(f"[TTS] Błąd segmentu {idx+1}: {e}", flush=True)
            print(f"[PROGRESS] {idx+1}/{total}", flush=True)
            if device == "mps":
                torch.mps.synchronize()
                torch.mps.empty_cache()
            gc.collect()

        with open(RESULT_FILE, "w") as f:
            json.dump({"status": "ok" if error_count == 0 else "partial", "error_count": error_count}, f)
        print(f"[TTS] GOTOWE. Błędy: {error_count}", flush=True)
        sys.exit(0 if error_count == 0 else 1)
    finally:
        del model
        if device == "mps":
            torch.mps.synchronize()
            torch.mps.empty_cache()
        gc.collect()

if __name__ == "__main__":
    main()
'''


# ── TTS SUBPROCESS ────────────────────────────────────────────
def _run_tts_subprocess(segments_texts, target_lang, ref_audio, output_paths,
                         segment_durations=None,
                         log_area=None, progress_callback=None, style_text=""):
    lang_map = {
        "Angielski": "english", "Niemiecki": "german", "Francuski": "french",
        "Hiszpański": "spanish", "Włoski": "italian", "Japoński": "japanese",
        "Koreański": "korean", "Chiński": "chinese", "Portugalski": "portuguese",
        "Arabski": "arabic", "Rosyjski": "russian"
    }
    qwen_lang = lang_map.get(target_lang, "english")

    run_id      = uuid.uuid4().hex[:8]
    job_file    = OUTPUT_DIR / f"tts_job_{run_id}.json"
    result_file = OUTPUT_DIR / f"tts_result_{run_id}.json"
    runner_path = OUTPUT_DIR / f"tts_runner_{run_id}.py"

    job_data = {
        "ref_audio":          str(ref_audio),
        "qwen_lang":          qwen_lang,
        "tts_model":          st.session_state.get("tts_model", "1.7B (Wysoka jakość)"),
        "segments_texts":     segments_texts,
        "output_paths":       [str(p) for p in output_paths],
        "segment_durations":  segment_durations or [5.0] * len(segments_texts),
        "global_offset":      0,
        "global_total":       len(segments_texts),
        "style":              style_text or "",
    }
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job_data, f, ensure_ascii=False)

    runner_code = (_TTS_RUNNER_TEMPLATE
        .replace("__JOB_FILE__",    repr(str(job_file)))
        .replace("__RESULT_FILE__", repr(str(result_file))))
    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    env = os.environ.copy()
    env.update({
        "PYTORCH_ENABLE_MPS_FALLBACK":         "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "PYTHONHTTPSVERIFY":                   "0",
        "HF_HUB_DISABLE_SSL_VERIFICATION":     "1",
        "TRANSFORMERS_VERBOSITY":              "error",
        "PYTHONUNBUFFERED":                    "1",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO":    "0.0",
        "TORCHAUDIO_BACKEND":                  "soundfile",  # fix: unika save_with_torchcodec
    })

    batch_rc = 1
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(runner_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, start_new_session=True
        )
        st.session_state.current_process = process
        sel = selectors.DefaultSelector()
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
            sel.register(process.stdout, selectors.EVENT_READ)

        t0 = time.time()
        last_output = t0
        buffer = ""
        while True:
            if st.session_state.get("cancel_requested"):
                log_message("[STOP] Zatrzymano TTS.", log_area, True)
                _kill_process_tree(process)
                break

            now = time.time()
            if now - t0 > 7200:
                log_message("[TTS] Timeout 7200s — przerywam.", log_area, True)
                _kill_process_tree(process)
                break
            if now - last_output > 1800:
                log_message("[TTS] Brak logów przez 1800s — przerywam.", log_area, True)
                _kill_process_tree(process)
                break

            events = sel.select(timeout=0.25)
            for key, _ in events:
                try:
                    data = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    data = b""
                if not data:
                    continue
                last_output = time.time()
                buffer += data.decode("utf-8", errors="replace").replace("\r", "\n")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    clean = line.strip()
                    if not clean:
                        continue
                    if clean.startswith("[PROGRESS]"):
                        try:
                            nums = clean.split()[-1].split("/")
                            cur, tot = int(nums[0]), int(nums[1])
                            if progress_callback:
                                progress_callback(cur / tot, f"Synteza głosu — segment {cur}/{tot}")
                        except Exception:
                            pass
                    elif not any(k in clean for k in ["pad_token_id", "eos_token_id", "UserWarning", "FutureWarning"]):
                        log_message(clean, log_area)

            if process.poll() is not None:
                break

        if buffer.strip():
            for line in buffer.strip().splitlines():
                clean = line.strip()
                if clean and not any(k in clean for k in ["pad_token_id", "eos_token_id", "UserWarning", "FutureWarning"]):
                    log_message(clean, log_area)
        try:
            sel.close()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except Exception:
            _kill_process_tree(process)
        st.session_state.current_process = None
        batch_rc = process.returncode if process.returncode is not None else 1

    except Exception as e:
        log_message(f"[TTS] Błąd krytyczny: {e}", log_area, True)
        batch_rc = 1
    finally:
        for p in [job_file, runner_path]:
            try: Path(p).unlink(missing_ok=True)
            except Exception: pass

    error_count = 0
    if result_file.exists():
        try:
            with open(result_file, encoding="utf-8") as f:
                res = json.load(f)
            error_count = res.get("error_count", 0 if batch_rc == 0 else len(segments_texts))
            result_file.unlink(missing_ok=True)
        except Exception:
            if batch_rc != 0:
                error_count = len(segments_texts)

    if st.session_state.cancel_requested:
        st.session_state.cancel_requested = False
        return 1

    return 0 if error_count == 0 else 1


def generate_qwen3_tts_batch(segments_texts, target_lang, ref_audio, output_paths,
                              segment_durations=None,
                              log_area=None, progress_callback=None,
                              ref_cache_dir=None, style_text=""):
    dur    = st.session_state.get("ref_audio_length", 12)
    run_id = uuid.uuid4().hex[:8]
    ref_hash = short_file_fingerprint(ref_audio)

    # ── CACHE PRÓBKI GŁOSU ──────────────────────────────────────
    # Klucz: zawartość pliku + długość próbki — zmiana głosu lub suwaka = nowa próbka
    _cached_ref = None
    if ref_cache_dir is not None:
        _cached_ref = Path(ref_cache_dir) / f"ref_voice_{dur}s_{ref_hash}.wav"

    if _cached_ref and _cached_ref.exists() and _cached_ref.stat().st_size > 1024:
        final_ref = str(_cached_ref)
        log_message(f"[TTS] Próbka głosu — z cache ({dur}s).", log_area, True)
    else:
        opt_ref   = str(Path(ref_audio).with_suffix(f".ref_{run_id}.wav"))
        final_ref = optimize_audio_reference(ref_audio, opt_ref, duration=dur, log_area=log_area)
        # Zapisz do trwałego cache
        if _cached_ref and Path(final_ref).exists():
            try:
                shutil.copy2(final_ref, _cached_ref)
                log_message(f"[TTS] Próbka głosu zapisana do cache: {_cached_ref.name}", log_area, True)
            except Exception:
                pass

    log_message(
        f"[TTS] Uruchamianie generatora — {len(segments_texts)} segmentów "
        f"| Model: {st.session_state.get('tts_model', '1.7B')} | Strict voice clone",
        log_area, True
    )

    ret = _run_tts_subprocess(
        segments_texts, target_lang, final_ref, output_paths,
        segment_durations=segment_durations,
        log_area=log_area,
        progress_callback=progress_callback,
        style_text=style_text
    )

    # Usuń tymczasowy opt_ref (nie kasuj pliku z cache)
    if _cached_ref is None or str(final_ref) != str(_cached_ref):
        try: Path(final_ref).unlink(missing_ok=True)
        except Exception: pass

    if ret == 0:
        log_message("[TTS] Zakończono — pamięć MPS zwolniona.", log_area, True)
    else:
        log_message(f"[TTS] Zakończono z błędami (kod: {ret}).", log_area, True)
    return ret


def generate_qwen3_preset_tts(text, target_lang, speaker, output_path, style_text="", log_area=None):
    lang_map = {
        "Angielski": "english", "Niemiecki": "german", "Francuski": "french",
        "Hiszpański": "spanish", "Włoski": "italian", "Japoński": "japanese",
        "Koreański": "korean", "Chiński": "chinese", "Portugalski": "portuguese",
        "Arabski": "arabic", "Rosyjski": "russian", "Polski": "Auto",
    }
    qwen_lang = lang_map.get(target_lang, "Auto")
    run_id      = uuid.uuid4().hex[:8]
    job_file    = OUTPUT_DIR / f"text_tts_job_{run_id}.json"
    result_file = OUTPUT_DIR / f"text_tts_result_{run_id}.json"
    runner_path = OUTPUT_DIR / f"text_tts_runner_{run_id}.py"

    job_data = {
        "text":        text,
        "qwen_lang":   qwen_lang,
        "speaker":     speaker,
        "style":       style_text,
        "tts_model":   st.session_state.get("tts_model", "1.7B (Wysoka jakość)"),
        "output_path": str(output_path),
    }
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job_data, f, ensure_ascii=False)

    runner_code = (_CUSTOM_VOICE_TTS_RUNNER_TEMPLATE
        .replace("__JOB_FILE__",    repr(str(job_file)))
        .replace("__RESULT_FILE__", repr(str(result_file))))
    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    log_message(
        f"[TTS] Generowanie audio z tekstu | Głos Qwen: {speaker} | Model: {st.session_state.get('tts_model', '1.7B')}",
        log_area, True
    )
    ret = run_command(
        [sys.executable, "-u", str(runner_path)],
        log_area=log_area,
        extra_env={
            "TRANSFORMERS_VERBOSITY": "error",
            "PYTHONUNBUFFERED": "1",
            "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0",
            "TORCHAUDIO_BACKEND": "soundfile",
        },
        timeout=7200,
        idle_timeout=1800,
    )

    ok = ret == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 1024
    if result_file.exists():
        try:
            with open(result_file, encoding="utf-8") as f:
                res = json.load(f)
            if res.get("status") != "ok":
                ok = False
                log_message(f"[TTS] {res.get('message', 'Błąd generowania.')}", log_area, True)
            result_file.unlink(missing_ok=True)
        except Exception:
            pass
    for p in [job_file, runner_path]:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass
    if ok:
        log_message("[TTS] Audio z tekstu gotowe.", log_area, True)
        return 0
    log_message("[TTS] Generowanie audio z tekstu nieudane.", log_area, True)
    return 1


def generate_qwen3_preset_tts_batch(segments_texts, target_lang, speaker, output_paths,
                                    segment_durations=None, log_area=None,
                                    progress_callback=None, style_text="",
                                    temperature_override=0.7, top_p_override=0.85,
                                    strict_timing=False):
    lang_map = {
        "Angielski": "english", "Niemiecki": "german", "Francuski": "french",
        "Hiszpański": "spanish", "Włoski": "italian", "Japoński": "japanese",
        "Koreański": "korean", "Chiński": "chinese", "Portugalski": "portuguese",
        "Arabski": "arabic", "Rosyjski": "russian", "Polski": "Auto",
    }
    qwen_lang = lang_map.get(target_lang, "Auto")
    run_id      = uuid.uuid4().hex[:8]
    job_file    = OUTPUT_DIR / f"preset_tts_job_{run_id}.json"
    result_file = OUTPUT_DIR / f"preset_tts_result_{run_id}.json"
    runner_path = OUTPUT_DIR / f"preset_tts_runner_{run_id}.py"

    job_data = {
        "qwen_lang":          qwen_lang,
        "speaker":            speaker,
        "style":              style_text or "",
        "temperature":        float(temperature_override),
        "top_p":              float(top_p_override),
        "strict_timing":      bool(strict_timing),
        "tts_model":          st.session_state.get("tts_model", "1.7B (Wysoka jakość)"),
        "segments_texts":     segments_texts,
        "output_paths":       [str(p) for p in output_paths],
        "segment_durations":  segment_durations or [5.0] * len(segments_texts),
    }
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job_data, f, ensure_ascii=False)

    runner_code = (_PRESET_TTS_BATCH_RUNNER_TEMPLATE
        .replace("__JOB_FILE__",    repr(str(job_file)))
        .replace("__RESULT_FILE__", repr(str(result_file))))
    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(runner_code)

    log_message(
        f"[TTS] Uruchamianie generatora preset Qwen — {len(segments_texts)} segmentów "
        f"| Głos: {speaker} | Model: {st.session_state.get('tts_model', '1.7B')}",
        log_area, True
    )

    env = os.environ.copy()
    env.update({
        "PYTHONHTTPSVERIFY":                   "0",
        "SSL_CERT_FILE":                       certifi.where(),
        "REQUESTS_CA_BUNDLE":                  certifi.where(),
        "HF_HUB_DISABLE_SSL_VERIFICATION":     "1",
        "PYTORCH_ENABLE_MPS_FALLBACK":         "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "TRANSFORMERS_VERBOSITY":              "error",
        "PYTHONUNBUFFERED":                    "1",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO":    "0.0",
        "TORCHAUDIO_BACKEND":                  "soundfile",
        "DYLD_LIBRARY_PATH":                   "",
    })

    ret = 1
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(runner_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        st.session_state.current_process = process
        sel = selectors.DefaultSelector()
        if process.stdout:
            os.set_blocking(process.stdout.fileno(), False)
            sel.register(process.stdout, selectors.EVENT_READ)

        t0 = time.time()
        last_output = t0
        buffer = ""
        while True:
            if st.session_state.get("cancel_requested"):
                log_message("[STOP] Zatrzymano preset Qwen TTS.", log_area, True)
                _kill_process_tree(process)
                break

            now = time.time()
            if now - t0 > 7200:
                log_message("[TTS] Timeout 7200s — przerywam preset Qwen.", log_area, True)
                _kill_process_tree(process)
                break
            if now - last_output > 1800:
                log_message("[TTS] Brak logów przez 1800s — przerywam preset Qwen.", log_area, True)
                _kill_process_tree(process)
                break

            events = sel.select(timeout=0.25)
            for key, _ in events:
                try:
                    data = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    data = b""
                if not data:
                    continue
                last_output = time.time()
                buffer += data.decode("utf-8", errors="replace").replace("\r", "\n")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    clean = line.strip()
                    if not clean:
                        continue
                    if clean.startswith("[PROGRESS]"):
                        try:
                            nums = clean.split()[-1].split("/")
                            cur, tot = int(nums[0]), int(nums[1])
                            if progress_callback:
                                progress_callback(cur / max(1, tot), f"Synteza głosu Qwen — segment {cur}/{tot}")
                        except Exception:
                            pass
                    elif not any(k in clean for k in ["pad_token_id", "eos_token_id", "UserWarning", "FutureWarning"]):
                        log_message(clean, log_area)

            if process.poll() is not None:
                break

        if buffer.strip():
            for line in buffer.strip().splitlines():
                clean = line.strip()
                if clean and not any(k in clean for k in ["pad_token_id", "eos_token_id", "UserWarning", "FutureWarning"]):
                    log_message(clean, log_area)
        try:
            sel.close()
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except Exception:
            _kill_process_tree(process)
        st.session_state.current_process = None
        ret = process.returncode if process.returncode is not None else 1
    except Exception as e:
        log_message(f"[TTS] Błąd krytyczny preset Qwen: {e}", log_area, True)
        ret = 1

    error_count = 0
    if result_file.exists():
        try:
            with open(result_file, encoding="utf-8") as f:
                res = json.load(f)
            error_count = res.get("error_count", 0 if ret == 0 else len(segments_texts))
            if res.get("status") not in ("ok", "partial"):
                log_message(f"[TTS] {res.get('message', 'Błąd generowania preset Qwen.')}", log_area, True)
            result_file.unlink(missing_ok=True)
        except Exception:
            if ret != 0:
                error_count = len(segments_texts)

    for p in [job_file, runner_path]:
        try: Path(p).unlink(missing_ok=True)
        except Exception: pass

    return 0 if ret == 0 and error_count == 0 else 1


def generate_system_voiceover_batch(segments_texts, target_lang, output_paths,
                                    segment_durations=None, log_area=None,
                                    progress_callback=None):
    voice_map = {
        "Angielski": "Daniel",
        "Niemiecki": "Anna",
        "Francuski": "Thomas",
        "Hiszpański": "Monica",
        "Włoski": "Alice",
        "Portugalski": "Joana",
        "Rosyjski": "Milena",
        "Japoński": "Kyoko",
        "Koreański": "Yuna",
        "Chiński": "Ting-Ting",
        "Polski": "Zosia",
    }
    voice = voice_map.get(target_lang, "Daniel")
    total = min(len(segments_texts or []), len(output_paths or []))
    log_message(
        f"[LEKTOR] Stabilny lektor systemowy macOS — {total} segmentów | Głos: {voice}",
        log_area, True,
    )
    errors = 0
    for idx in range(total):
        if st.session_state.get("cancel_requested"):
            return 1
        text = " ".join(str(segments_texts[idx] or "").split()).strip()
        out_path = Path(output_paths[idx])
        if not text:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", "0.08", "-c:a", "pcm_s16le", str(out_path)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if progress_callback:
                progress_callback((idx + 1) / max(total, 1), f"Lektor systemowy — segment {idx+1}/{total}")
            continue

        dur = float(segment_durations[idx]) if segment_durations and idx < len(segment_durations) else 4.0
        words = max(1, len(text.split()))
        rate = int(max(150, min(280, (words * 60.0 / max(dur, 1.0)) * 1.18)))
        aiff_path = out_path.with_suffix(f".system_{idx}.aiff")
        try:
            say_res = subprocess.run(
                ["say", "-v", voice, "-r", str(rate), "-o", str(aiff_path), text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
            )
            if say_res.returncode != 0 or not aiff_path.exists():
                say_res = subprocess.run(
                    ["say", "-r", str(rate), "-o", str(aiff_path), text],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                )
            conv = subprocess.run([
                "ffmpeg", "-y", "-i", str(aiff_path),
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out_path)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            if conv.returncode != 0 or not out_path.exists() or out_path.stat().st_size < 512:
                errors += 1
                log_message(f"[LEKTOR] Błąd syntezy systemowej segmentu {idx+1}.", log_area)
        except Exception as e:
            errors += 1
            log_message(f"[LEKTOR] Błąd systemowego TTS segmentu {idx+1}: {e}", log_area)
        finally:
            try: aiff_path.unlink(missing_ok=True)
            except Exception: pass
        if progress_callback:
            progress_callback((idx + 1) / max(total, 1), f"Lektor systemowy — segment {idx+1}/{total}")

    if errors:
        log_message(f"[LEKTOR] Stabilny lektor zakończony z błędami: {errors}.", log_area, True)
        return 1
    log_message("[LEKTOR] Stabilny lektor systemowy gotowy.", log_area, True)
    return 0


def render_text_tts_left_panel():
    """Lewa kolumna trybu Tekst → Audio: wpisywanie i przygotowanie tekstu."""
    st.markdown("### 2. Tekst źródłowy")
    st.caption("Wpisz tekst do syntezy mowy, opcjonalnie przetłumacz przed generowaniem.")

    st.text_area(
        "Tekst źródłowy",
        key="text_tts_input",
        height=260,
        placeholder="Wpisz tekst, np. I love you albo polski tekst do tłumaczenia..."
    )

    t_col1, t_col2 = st.columns(2)
    with t_col1:
        if st.button(f"🔄 Przetłumacz → {st.session_state.target_lang}", use_container_width=True, key="text_tts_translate_btn"):
            src_text = st.session_state.get("text_tts_input", "").strip()
            if not src_text:
                st.warning("Wpisz tekst do tłumaczenia.")
            elif st.session_state.translation_model == "Brak (Tylko transkrypcja)":
                st.session_state.text_tts_generate_text = apply_proper_name_glossary_to_text(src_text)
                st.toast("✅ Tekst przeniesiony do generowania.")
                st.rerun()
            else:
                with st.spinner(f"Tłumaczę na {st.session_state.target_lang}..."):
                    translated = translate_text_ai(src_text, st.session_state.target_lang)
                st.session_state.text_tts_generate_text = translated
                st.toast("✅ Tekst przetłumaczony.")
                st.rerun()
    with t_col2:
        if st.button("➡️ Użyj bez tłumaczenia", use_container_width=True, key="text_tts_use_raw_btn"):
            st.session_state.text_tts_generate_text = apply_proper_name_glossary_to_text(
                st.session_state.get("text_tts_input", "").strip()
            )
            st.toast("✅ Tekst gotowy do generowania.")
            st.rerun()

    if not st.session_state.get("text_tts_generate_text") and st.session_state.get("text_tts_input"):
        st.session_state.text_tts_generate_text = apply_proper_name_glossary_to_text(st.session_state.text_tts_input)

    st.text_area(
        f"Tekst do wygenerowania audio ({st.session_state.target_lang}):",
        key="text_tts_generate_text",
        height=260,
        placeholder="Tutaj pojawi się tekst po tłumaczeniu albo tekst użyty bez tłumaczenia."
    )


def render_text_to_audio_panel():
    """Prawa kolumna trybu Tekst → Audio: wybór głosu, generowanie i pobieranie."""
    st.markdown("### 3. Generator Audio z Tekstu")
    st.caption("Wybierz głos i wygeneruj plik audio na podstawie tekstu z lewego panelu.")

    # ── Lokalny przełącznik źródła głosu (niezależny od paska bocznego) ──
    _store_voices = load_voice_index()
    _has_store    = len(_store_voices) > 0

    _tts_voice_opts = ["🎙️ Głos z bazy Qwen TTS"]
    if _has_store:
        _tts_voice_opts.append("📦 Głos z magazynu")

    # Domyślnie: magazyn jeśli dostępny i był wcześniej wybrany, inaczej Qwen
    if "tts_local_voice_mode" not in st.session_state:
        st.session_state.tts_local_voice_mode = (
            "📦 Głos z magazynu"
            if (_has_store and st.session_state.get("voice_source") == "Głos z magazynu")
            else "🎙️ Głos z bazy Qwen TTS"
        )
    # Jeśli magazyn opróżniony — cofnij do Qwen
    if st.session_state.tts_local_voice_mode == "📦 Głos z magazynu" and not _has_store:
        st.session_state.tts_local_voice_mode = "🎙️ Głos z bazy Qwen TTS"

    st.session_state.tts_local_voice_mode = st.radio(
        "Źródło głosu:",
        _tts_voice_opts,
        index=_tts_voice_opts.index(st.session_state.tts_local_voice_mode),
        horizontal=True,
        key="tts_local_voice_mode_radio"
    )
    use_store_voice = (st.session_state.tts_local_voice_mode == "📦 Głos z magazynu")

    if use_store_voice:
        # Pokaż selectbox z głosami z magazynu
        _voice_names = [v.get("name", v.get("id", "?")) for v in _store_voices]
        _voice_ids   = [v.get("id", "") for v in _store_voices]
        _cur_id      = st.session_state.get("selected_voice_id", "")
        _cur_idx     = _voice_ids.index(_cur_id) if _cur_id in _voice_ids else 0
        _chosen_idx  = st.selectbox(
            "Głos z magazynu",
            range(len(_voice_names)),
            index=_cur_idx,
            format_func=lambda i: _voice_names[i],
            key="tts_store_voice_select"
        )
        st.session_state.selected_voice_id = _voice_ids[_chosen_idx]
        _voice = _store_voices[_chosen_idx]
        st.session_state.text_tts_style = st.text_input(
            "Styl głosu (opcjonalnie)",
            key="text_tts_style_input",
            placeholder="np. speak faster, speak slowly, speak with excitement"
        )
    else:
        speaker_names = list(QWEN_PRESET_SPEAKERS.keys())
        if st.session_state.get("text_tts_speaker") not in speaker_names:
            st.session_state.text_tts_speaker = "Ryan"
        st.session_state.text_tts_speaker = st.selectbox(
            "Głos z bazy Qwen TTS",
            speaker_names,
            index=speaker_names.index(st.session_state.text_tts_speaker),
            format_func=lambda s: f"{s} — {QWEN_PRESET_SPEAKERS.get(s, '')}",
            key="tts_qwen_speaker_select"
        )
        st.session_state.text_tts_style = st.text_input(
            "Styl głosu (opcjonalnie)",
            key="text_tts_style_input",
            placeholder="np. speak calmly and warmly"
        )

    timer_ph = st.empty()
    # Pokaż czas ostatniego generowania (przeżywa rerun)
    if st.session_state.get("text_tts_last_gen_time"):
        timer_ph.markdown(
            f"<div style='color:#a8ff78;font-size:0.82rem;margin-top:4px;'>"
            f"⏱️ Czas ostatniego generowania: {st.session_state['text_tts_last_gen_time']}</div>",
            unsafe_allow_html=True
        )

    log_area = st.empty()
    # Odtwórz logi po rerunie (np. po kliknięciu przycisku pobierania) — tylko gdy show_log włączony
    if st.session_state.get("show_log", True) and st.session_state.get("full_logs") and not st.session_state.get("is_generating"):
        _log_lines = list(st.session_state.full_logs)
        _log_esc   = "\n".join(_log_lines).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        log_area.code(_log_esc)

    if st.button("🎙️ Generuj audio z tekstu", use_container_width=True, type="primary", key="text_tts_generate_btn"):
        gen_text = apply_proper_name_glossary_to_text(st.session_state.get("text_tts_generate_text", "").strip())
        if not gen_text:
            st.warning("Brak tekstu do wygenerowania — wypełnij pole w lewym panelu.")
        else:
            global _g_timer_ph
            st.session_state.full_logs.clear()
            out_name = f"text_tts_{int(time.time())}_{uuid.uuid4().hex[:6]}.wav"
            out_path = OUTPUT_DIR / out_name
            _tts_start = time.time()
            st.session_state["pipeline_start_time"] = _tts_start
            _g_timer_ph = timer_ph
            timer_ph.markdown(
                "<div style='color:#888;font-size:0.82rem;margin-top:4px;'>⏱️ 00:00</div>",
                unsafe_allow_html=True
            )
            with st.spinner("Generuję audio..."):
                if use_store_voice:
                    _gen_voice = get_voice_by_id(st.session_state.get("selected_voice_id", ""))
                    rc = generate_qwen3_tts_batch(
                        [gen_text],
                        st.session_state.target_lang,
                        Path(_gen_voice["path"]),
                        [out_path],
                        segment_durations=[max(5.0, len(gen_text.split()) / 1.5)],
                        log_area=log_area,
                        ref_cache_dir=Path(_gen_voice["path"]).parent,
                        style_text=st.session_state.get("text_tts_style_input", "")
                    )
                else:
                    rc = generate_qwen3_preset_tts(
                        gen_text,
                        st.session_state.target_lang,
                        st.session_state.text_tts_speaker,
                        out_path,
                        style_text=st.session_state.get("text_tts_style_input", ""),
                        log_area=log_area
                    )
            _g_timer_ph = None
            st.session_state.pop("pipeline_start_time", None)
            _total = int(time.time() - _tts_start)
            _m, _s = divmod(_total, 60)
            st.session_state["text_tts_last_gen_time"] = f"{_m:02d}:{_s:02d}"
            if rc == 0 and out_path.exists():
                # Normalizacja głośności — identyczna jak w pipeline dubbingu
                _norm_path = out_path.with_suffix(".norm.wav")
                try:
                    _norm_rc = subprocess.run([
                        "ffmpeg", "-y", "-i", str(out_path),
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(_norm_path)
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                    if _norm_rc == 0 and _norm_path.exists() and _norm_path.stat().st_size > 512:
                        out_path.unlink(missing_ok=True)
                        _norm_path.rename(out_path)
                except Exception:
                    try: _norm_path.unlink(missing_ok=True)
                    except Exception: pass
                st.session_state.text_tts_output_path = str(out_path)
                st.session_state.text_tts_last_text = gen_text
                st.success("✅ Audio wygenerowane.")
            else:
                st.error("❌ Nie udało się wygenerować audio.")

    if st.session_state.get("text_tts_output_path") and Path(st.session_state.text_tts_output_path).exists():
        out_path = Path(st.session_state.text_tts_output_path)
        st.audio(str(out_path))
        with open(out_path, "rb") as f:
            st.download_button(
                "💾 Pobierz audio WAV",
                data=f.read(),
                file_name=out_path.name,
                mime="audio/wav",
                use_container_width=True,
                key="text_tts_download_btn",
            )


# ── ZERO-DRIFT ASSEMBLY ───────────────────────────────────────
TARGET_SR = 24000   # Hz — wspólna częstotliwość dla wszystkich segmentów
TARGET_CH = 1       # mono
TARGET_SW = 2       # 16-bit

def _normalize_wav(src, dst):
    """Normalizuje plik WAV do TARGET_SR/CH/SW przez ffmpeg."""
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src),
        "-ar", str(TARGET_SR), "-ac", str(TARGET_CH),
        "-sample_fmt", "s16", str(dst)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return Path(dst).exists() and Path(dst).stat().st_size > 512

def assemble_zero_drift(segments, dubbed_paths, total_duration, output_wav, log_area=None):
    """
    Wstawia każdy segment dubbingu na DOKŁADNĄ pozycję z Whisper timestamps.

    Zasada działania:
    - Czyta WSZYSTKIE klatki z każdego pliku audio — zero twardego cięcia
    - Następny segment naturalnie nadpisuje ewentualny overlap poprzedniego
    - Ostatni segment gra do końca bufora (rozszerzanego dynamicznie)
    - Wynik: żadne zdanie nie jest urwane
    """
    bpf          = TARGET_CH * TARGET_SW
    total_frames = int(total_duration * TARGET_SR)
    buf          = bytearray(total_frames * bpf)

    placed = 0
    for i, (seg, src_path) in enumerate(zip(segments, dubbed_paths)):
        src = Path(src_path)
        if not src.exists():
            log_message(f"[Assembly] ⚠️ Brak pliku segmentu {i+1}, pomijam.", log_area)
            continue

        # Normalizuj do wspólnego formatu
        norm = src.parent / f"_norm_{i}_{uuid.uuid4().hex[:6]}.wav"
        if not _normalize_wav(src, norm):
            log_message(f"[Assembly] ⚠️ Normalizacja segmentu {i+1} nieudana.", log_area)
            continue

        start_frame = int(seg["start"] * TARGET_SR)
        start_byte  = start_frame * bpf

        if start_byte >= len(buf):
            try: norm.unlink(missing_ok=True)
            except Exception: pass
            continue

        try:
            with wave.open(str(norm), "rb") as wf:
                # Czytaj WSZYSTKIE klatki — bez limitu opartego na oknie Whisper
                audio_bytes = wf.readframes(wf.getnframes())
                audio_frames = len(audio_bytes) // bpf
        except Exception as e:
            log_message(f"[Assembly] Błąd odczytu segmentu {i+1}: {e}", log_area)
            try: norm.unlink(missing_ok=True)
            except Exception: pass
            continue

        end_byte = start_byte + len(audio_bytes)

        # Rozszerz bufor dynamicznie jeśli segment wychodzi poza czas wideo
        # (dotyczy głównie ostatniego segmentu który może być dłuższy niż film)
        if end_byte > len(buf):
            extra = end_byte - len(buf)
            buf.extend(bytes(extra))
            log_message(
                f"[Assembly] Seg {i+1}: bufor rozszerzony o {extra // bpf / TARGET_SR:.2f}s "
                f"(segment dłuższy niż wideo).",
                log_area
            )

        buf[start_byte : end_byte] = audio_bytes
        placed += 1
        del audio_bytes   # FIX: zwolnij natychmiast po skopiowaniu do bufora

        try: norm.unlink(missing_ok=True)
        except Exception: pass

    actual_dur = len(buf) / bpf / TARGET_SR
    log_message(
        f"[Assembly] Umieszczono {placed}/{len(segments)} segmentów | "
        f"Czas bufora: {actual_dur:.2f}s (wideo: {total_duration:.2f}s).",
        log_area, True
    )

    with wave.open(str(output_wav), "wb") as out:
        out.setnchannels(TARGET_CH)
        out.setsampwidth(TARGET_SW)
        out.setframerate(TARGET_SR)
        out.writeframes(bytes(buf))

    del buf   # FIX: zwolnij ~33–66 MB po zapisie do pliku
    gc.collect()
    return placed > 0


# ─────────────────────────────────────────────────────────────
# UI — SIDEBAR
# ─────────────────────────────────────────────────────────────
st.title("🎙️ DubMaster by Opitkovanie: Profesjonalne Studio Dubbingu")

with st.sidebar:
    _sidebar_locked = st.session_state.get("is_generating", False)
    if _sidebar_locked:
        st.markdown("""
<style>
section[data-testid="stSidebar"] {
    opacity: 0.52 !important;
    filter: grayscale(0.85) !important;
}
section[data-testid="stSidebar"] * {
    pointer-events: none !important;
    cursor: not-allowed !important;
}
</style>
""", unsafe_allow_html=True)

    # ── USTAWIENIA PROJEKTU ──
    st.header("⚙️ Ustawienia Projektu")
    _src_keys = list(LANGUAGES.keys())
    _tgt_idx  = TARGET_LANGUAGES.index(st.session_state.target_lang) \
                 if st.session_state.target_lang in TARGET_LANGUAGES else 0

    st.session_state.source_lang = st.selectbox(
        "Język oryginału", _src_keys,
        index=_src_keys.index(st.session_state.source_lang)
               if st.session_state.source_lang in _src_keys else 0,
        help="Język mówiony w oryginalnym wideo. 'Automatyczne' = Whisper sam rozpozna język."
    )
    st.session_state.target_lang = st.selectbox(
        "Język docelowy (Dubbing)", TARGET_LANGUAGES, index=_tgt_idx,
        help="Język, na który zostanie przetłumaczone i udubbiowane wideo."
    )
    # Te opcje są stałym, bezpiecznym domyślnym trybem pracy i nie muszą zajmować miejsca w menu.
    st.session_state.auto_sync = True
    st.session_state.whisper_precise = True

    st.divider()

    # ── MAGAZYN GŁOSÓW ──
    st.subheader("🎤 Głos do dubbingu")
    voices = load_voice_index()
    _voice_store_mode_from_widget = st.session_state.get("voice_store_mode_radio")
    if _voice_store_mode_from_widget in ["Próbki własne", "Głos z bazy Qwen TTS"]:
        st.session_state.voice_store_mode = _voice_store_mode_from_widget
    _qwen_speaker_from_widget = st.session_state.get("dubbing_qwen_speaker_select")
    if _qwen_speaker_from_widget in QWEN_PRESET_SPEAKERS:
        st.session_state.dubbing_qwen_speaker = _qwen_speaker_from_widget
    _selected_store_voice = get_voice_by_id(st.session_state.get("selected_voice_id", ""))
    _store_mode = st.session_state.get("voice_store_mode", "Próbki własne")
    _qwen_speaker = st.session_state.get("dubbing_qwen_speaker", "Ryan")
    if _store_mode == "Głos z bazy Qwen TTS":
        _store_label = f"Głos z magazynu: {_qwen_speaker}"
    elif _selected_store_voice:
        _store_label = f"Głos z magazynu: {_selected_store_voice.get('name', 'bez nazwy')}"
    else:
        _store_label = "Głos z magazynu"
    voice_source_opts = ["Głos z oryginalnego filmu", _store_label]
    if st.session_state.get("voice_source") not in voice_source_opts:
        if str(st.session_state.get("voice_source", "")).startswith("Głos z magazynu"):
            st.session_state.voice_source = _store_label
        else:
            st.session_state.voice_source = voice_source_opts[0]
    _voice_source_choice = st.selectbox(
        "Źródło głosu",
        voice_source_opts,
        index=voice_source_opts.index(st.session_state.voice_source),
        key="voice_source_select",
        help="Możesz użyć głosu wyciągniętego z filmu albo własnej próbki zapisanej w magazynie."
    )
    st.session_state.voice_source = "Głos z magazynu" if _voice_source_choice.startswith("Głos z magazynu") else _voice_source_choice

    if remembered_section("📚 Magazyn próbek głosu", "exp_voice_store", False):
        if st.session_state.get("voice_save_notice"):
            st.success(st.session_state.voice_save_notice)
            st.session_state.voice_save_notice = ""

        voice_store_modes = ["Próbki własne", "Głos z bazy Qwen TTS"]
        if st.session_state.get("voice_store_mode") not in voice_store_modes:
            st.session_state.voice_store_mode = "Próbki własne"
        st.session_state.voice_store_mode = st.selectbox(
            "Wybór głosu w magazynie",
            voice_store_modes,
            index=voice_store_modes.index(st.session_state.voice_store_mode),
            key="voice_store_mode_radio",
            help="Próbki własne używają zapisanych plików/nagrań. Głos z bazy Qwen TTS używa gotowych głosów modelu, ale dalej trzyma timingi dubbingu."
        )

        if st.session_state.voice_store_mode == "Głos z bazy Qwen TTS":
            speaker_names = list(QWEN_PRESET_SPEAKERS.keys())
            if st.session_state.get("dubbing_qwen_speaker") not in speaker_names:
                st.session_state.dubbing_qwen_speaker = "Ryan"
            st.session_state.dubbing_qwen_speaker = st.selectbox(
                "Głos z bazy Qwen TTS",
                speaker_names,
                index=speaker_names.index(st.session_state.dubbing_qwen_speaker),
                format_func=lambda s: f"{s} — {QWEN_PRESET_SPEAKERS.get(s, '')}",
                key="dubbing_qwen_speaker_select"
            )
            st.caption("Ten głos będzie generowany per segment i układany przez Zero-Drift tak samo jak voice clone.")
            st.session_state.voice_source = "Głos z magazynu"
            st.markdown("---")

        if st.session_state.voice_store_mode == "Próbki własne" and voices:
            valid_ids = [v["id"] for v in voices]
            if st.session_state.get("selected_voice_id") not in valid_ids:
                st.session_state.selected_voice_id = valid_ids[-1]
            voice_labels = {
                v["id"]: f"{v['name']} · {v.get('duration', 0):.1f}s · {v.get('created_at', '')}"
                for v in voices
            }
            selected_idx = valid_ids.index(st.session_state.selected_voice_id)
            st.session_state.selected_voice_id = st.selectbox(
                "Wybierz próbkę",
                valid_ids,
                index=selected_idx,
                format_func=lambda vid: voice_labels.get(vid, vid),
                key="voice_select_id"
            )
            selected_voice = get_voice_by_id(st.session_state.selected_voice_id)
            if selected_voice:
                st.audio(selected_voice["path"])
                new_voice_name = st.text_input(
                    "Nazwa próbki",
                    value=selected_voice.get("name", ""),
                    key=f"voice_rename_{selected_voice['id']}"
                )
                c_ren, c_del = st.columns(2)
                with c_ren:
                    if st.button("Zmień nazwę", use_container_width=True, key="voice_rename_btn"):
                        rename_voice_sample(selected_voice["id"], new_voice_name)
                        st.rerun()
                with c_del:
                    if st.button("Usuń próbkę", use_container_width=True, key="voice_delete_btn"):
                        delete_voice_sample(selected_voice["id"])
                        st.rerun()
        elif st.session_state.voice_store_mode == "Próbki własne":
            st.caption("Brak zapisanych próbek głosu.")

        if st.session_state.voice_store_mode == "Próbki własne":
            st.markdown("---")
            upload_name = st.text_input("Nazwa dla nowego pliku", key="voice_upload_name", placeholder="np. Bartek, Lektor, Kasia")
            voice_upload = st.file_uploader(
                "Dodaj audio lub wideo",
                type=["wav", "mp3", "m4a", "aac", "flac", "ogg", "mp4", "mov", "mkv", "webm", "avi"],
                key="voice_upload_file"
            )
            upload_sig = None
            if voice_upload:
                upload_sig = f"{upload_name or Path(voice_upload.name).stem}|{voice_upload.name}|{getattr(voice_upload, 'size', 0)}"
                if st.session_state.get("last_saved_upload_sig") == upload_sig:
                    st.success("Ten plik jest już zapisany w magazynie. Wybierz inny plik albo zmień nazwę, żeby dodać kolejną kopię.")

            if st.button(
                "Dodaj plik do magazynu",
                use_container_width=True,
                key="voice_upload_btn",
                disabled=bool(upload_sig) and st.session_state.get("last_saved_upload_sig") == upload_sig
            ):
                if not voice_upload:
                    st.warning("Wybierz plik audio lub wideo.")
                else:
                    sample_name = upload_name or Path(voice_upload.name).stem
                    voice, err = add_voice_sample(sample_name, voice_upload.getvalue(), voice_upload.name, "plik")
                    if voice:
                        st.session_state.selected_voice_id = voice["id"]
                        st.session_state.voice_source = "Głos z magazynu"
                        st.session_state.last_saved_upload_sig = upload_sig
                        st.session_state.voice_save_notice = f"Dodano próbkę: {voice['name']} ({voice.get('duration', 0):.1f}s). Wybrano ją jako aktywny głos."
                        st.rerun()
                    else:
                        st.error(err or "Nie udało się dodać próbki.")

            st.markdown("---")
            rec_name = st.text_input("Nazwa nagrania", key="voice_record_name", placeholder="np. Bartek mikrofon")
            mic_audio_bytes = None
            mic_original_name = "microphone.wav"
            current_mic_id = None
            mic_recording_sig = None
            if mic_recorder is not None:
                mic_result = mic_recorder(
                    start_prompt="🎙️ Start nagrywania",
                    stop_prompt="⏹️ Nagrywam... kliknij, żeby zatrzymać",
                    just_once=False,
                    use_container_width=True,
                    format="wav",
                    key="voice_mic_recorder_stable"
                )
                current_mic_id = mic_result.get("id") if mic_result else None
                if mic_result and mic_result.get("bytes"):
                    mic_audio_bytes = mic_result["bytes"]
                    mic_recording_sig = f"id:{current_mic_id}" if current_mic_id else f"hash:{hashlib.sha256(mic_audio_bytes).hexdigest()}"
                    mic_original_name = "microphone.wav"
                    st.audio(mic_audio_bytes, format="audio/wav")
                    try:
                        with wave.open(io.BytesIO(mic_audio_bytes), "rb") as _mic_wav:
                            dur_hint = _mic_wav.getnframes() / max(1, _mic_wav.getframerate())
                    except Exception:
                        try:
                            _channels = max(1, int(mic_result.get("channels", 1) or 1))
                        except Exception:
                            _channels = 1
                        dur_hint = len(mic_audio_bytes) / max(1, mic_result.get("sample_rate", 44100)) / max(1, mic_result.get("sample_width", 2)) / _channels
                    if st.session_state.get("last_saved_mic_id") == mic_recording_sig:
                        st.success("To nagranie jest już zapisane w magazynie. Nagraj kolejną próbkę, żeby zapisać nową pozycję.")
                    else:
                        st.caption(f"Nagrano próbkę. Przybliżona długość: {dur_hint:.1f}s")
            elif audio_recorder is not None:
                st.caption("Tryb awaryjny nagrywania — bez podglądu czasu podczas nagrywania.")
                mic_audio_bytes = audio_recorder(
                    text="Kliknij mikrofon, nagraj próbkę i kliknij ponownie, żeby zatrzymać",
                    neutral_color="#4b5563",
                    recording_color="#dc2626",
                    icon_name="microphone",
                    icon_size="2x",
                    sample_rate=None,
                    key="voice_mic_recorder_component"
                )
                if mic_audio_bytes:
                    mic_recording_sig = f"hash:{hashlib.sha256(mic_audio_bytes).hexdigest()}"
                    st.audio(mic_audio_bytes, format="audio/wav")
            else:
                st.caption("Alternatywny komponent nagrywania nie jest zainstalowany — używam nagrywania Streamlit.")
                mic_audio = st.audio_input(
                    "Nagraj próbkę z mikrofonu",
                    sample_rate=None,
                    key="voice_mic_input",
                    help="Nagranie zostanie później przekonwertowane przez FFmpeg do formatu wymaganego przez TTS."
                )
                if mic_audio:
                    mic_audio_bytes = mic_audio.getvalue()
                    mic_recording_sig = f"hash:{hashlib.sha256(mic_audio_bytes).hexdigest()}"
                    mic_original_name = "microphone.wav"

            mic_already_saved = bool(mic_audio_bytes) and st.session_state.get("last_saved_mic_id") == mic_recording_sig
            can_save_recording = bool(mic_audio_bytes) and not mic_already_saved
            if st.button("Zapisz nagranie", use_container_width=True, key="voice_record_btn", disabled=bool(mic_audio_bytes) and not can_save_recording):
                if not mic_audio_bytes:
                    st.warning("Najpierw nagraj próbkę mikrofonem.")
                elif mic_already_saved:
                    st.info("To nagranie jest już zapisane. Nagraj nową próbkę przed kolejnym zapisem.")
                else:
                    sample_name = rec_name or "Nagranie mikrofonu"
                    voice, err = add_voice_sample(sample_name, mic_audio_bytes, mic_original_name, "mikrofon")
                    if voice:
                        st.session_state.selected_voice_id = voice["id"]
                        st.session_state.voice_source = "Głos z magazynu"
                        st.session_state.last_saved_mic_id = mic_recording_sig
                        st.session_state.voice_save_notice = f"Zapisano nagranie: {voice['name']} ({voice.get('duration', 0):.1f}s). Wybrano je jako aktywny głos."
                        st.rerun()
                    else:
                        st.error(err or "Nie udało się zapisać nagrania.")

            if voices:
                st.markdown("---")
                if st.button("Wyczyść cały magazyn głosów", use_container_width=True, key="voice_clear_all"):
                    clear_voice_store()
                    st.rerun()

    save_all_settings()

    st.divider()

    # ── LIMITY TEMPA ──
    if remembered_section("⏱️ Limity tempa (Stretch)", "exp_tempo", True):

        # ── MIN TEMPO ──
        _auto_min = st.checkbox(
            "🤖 AUTO Min (brak zwalniania = 1.0)",
            value=st.session_state.get("auto_min_tempo", False),
            key="auto_min_tempo_cb",
            help="AUTO: Min Tempo = 1.0 — segment TTS nigdy nie jest zwalniany.\n"
                 "Jeśli TTS jest krótszy niż okno Whisper, reszta to naturalna cisza.\n"
                 "Wyłącz żeby ręcznie ustawić zwolnienie."
        )
        st.session_state.auto_min_tempo = _auto_min
        if not _auto_min:
            st.session_state.sync_min_tempo = st.slider(
                "Min Tempo (zwolnienie)",
                min_value=0.50, max_value=1.00, step=0.01,
                value=st.session_state.sync_min_tempo,
                key="sync_min_tempo_sl",
                help="Domyślnie: 0.85 (do 15% zwolnienia).\n"
                     "• 1.00 = bez zwalniania (TTS za krótki → cisza)\n"
                     "• 0.70 = wolniejszy dubbing (ryzyko nienaturalności)\n"
                     "Im bliżej 0.85–0.90, tym lepiej brzmi."
            )
        else:
            st.caption("Min Tempo: **1.00** (AUTO — brak zwalniania)")

        st.markdown("---")

        # ── MAX TEMPO ──
        _auto_max = st.checkbox(
            "🤖 AUTO Max (dopasuj dokładnie do okna)",
            value=st.session_state.get("auto_max_tempo", False),
            key="auto_max_tempo_cb",
            help="AUTO: Max Tempo = dokładne ratio potrzebne żeby segment zmieścił się\n"
                 "w oknie do następnego segmentu — bez twardego ucinania słów.\n"
                 "AUTO może mocniej przyspieszyć trudny segment, jeśli inaczej słowo byłoby ucięte.\n"
                 "ZALECANE dla języków z długimi tłumaczeniami (DE, JA, PL→EN)."
        )
        st.session_state.auto_max_tempo = _auto_max
        if not _auto_max:
            st.session_state.sync_max_tempo = st.slider(
                "Max Tempo (przyspieszenie)",
                min_value=1.00, max_value=2.00, step=0.05,
                value=st.session_state.sync_max_tempo,
                key="sync_max_tempo_sl",
                help="Domyślnie: 1.20 (do 20% przyspieszenia).\n"
                     "• 1.25–1.50 = wyraźne, nadal brzmi naturalnie\n"
                     "• 1.60+ = ryzyko efektu chipmunk\n"
                     "AUTO Max dopasowuje segment tempem, bez twardego ucinania słów."
            )
        else:
            st.caption("Max Tempo: **AUTO** — algorytm dobiera ratio per-segment")
    st.divider()

    # ── MIKS DŹWIĘKU ──
    st.subheader("🎚️ Miks Dźwięku")
    if st.session_state.get("mix_mode") not in MIX_MODES:
        st.session_state.mix_mode = DEFAULT_MIX_MODE
    st.session_state.mix_mode = st.selectbox(
        "Tryb miksu głosu",
        MIX_MODES,
        index=MIX_MODES.index(st.session_state.mix_mode),
        help="Wybiera sposób połączenia oryginalnego dźwięku z wygenerowanym głosem AI.\n"
             "Czysty dubbing usuwa oryginalny głos. Tryby lektora zostawiają oryginał pod spodem.",
        key="mix_mode_select",
    )
    if st.session_state.mix_mode == MIX_MODE_DUBBING:
        st.caption("Czysty dubbing: oryginalny głos jest usuwany przez Demucs, a AI zastępuje mówcę. To obecny, domyślny tryb.")
    elif st.session_state.mix_mode == MIX_MODE_VOICEOVER:
        st.caption("Lektor: zostaje pełne oryginalne audio filmu razem z głosem osoby mówiącej, a głos AI jest nałożony na wierzch.")
    else:
        st.caption("Lektor z duckingiem: zostaje oryginalne audio, ale automatycznie ścisza się wtedy, gdy mówi głos AI.")

    if st.session_state.mix_mode == MIX_MODE_DUBBING:
        st.session_state.keep_bg = st.checkbox(
            "Dźwięki tła z oryginału (Demucs)",
            value=st.session_state.keep_bg,
            help="Demucs oddziela głos mówcy od reszty (muzyka, oklaski, ambience). "
                 "Oryginalny głos jest usuwany, a tło miksowane z dubbingiem AI. "
                 "Wyłącz jeśli wideo nie ma muzyki lub chcesz ciszone tło."
        )

    if st.session_state.mix_mode == MIX_MODE_DUBBING and st.session_state.keep_bg:
        if remembered_section("🎙️ Głośności miksu", "exp_miks_vol", True):
            st.session_state.dub_vol = st.slider(
                "🎙️ Dubbing AI", min_value=0.0, max_value=3.0,
                value=st.session_state.dub_vol, step=0.05, key="dub_vol_sl",
                help="Głośność wygenerowanego dubbingu.\n"
                     "Domyślnie: 1.5\n"
                     "• 1.0 = oryginalna głośność TTS\n"
                     "• 1.5 = zalecane (dubbing wyraźny ponad tłem)\n"
                     "• 2.0+ = bardzo wyraźny, może brzmieć krzykliwie"
            )
            st.session_state.bg_music_vol = st.slider(
                "🎵 Tło muzyczne", min_value=0.0, max_value=2.0,
                value=st.session_state.bg_music_vol, step=0.05, key="bg_vol_sl",
                help="Głośność muzyki i instrumentów wyodrębnionych przez Demucs.\n"
                     "Domyślnie: 1.0\n"
                     "• 0.0 = cisza (tylko dubbing)\n"
                     "• 0.8 = subtelne tło\n"
                     "• 1.0 = oryginalna głośność muzyki"
            )
            st.session_state.ambient_vol = st.slider(
                "🔊 Ambient (oklaski, śmiech)", min_value=0.0, max_value=3.0,
                value=st.session_state.ambient_vol, step=0.05, key="amb_vol_sl",
                help="Głośność dźwięków otoczenia (oklaski, publiczność, śmiech).\n"
                     "Domyślnie: 0.7\n"
                     "• 0.0 = wyłączony\n"
                     "• 0.5–0.8 = subtelny, naturalny\n"
                     "• 1.0+ = wyraźny ambient"
            )
        if remembered_section("🎛️ EQ Ambientu", "exp_eq", True):
            st.session_state.ambient_eq_enabled = st.checkbox(
                "Włącz EQ ambientu",
                value=st.session_state.ambient_eq_enabled,
                help="Filtruje kanał ambientu żeby brzmiał naturalnie. "
                     "Odcina bas muzyczny i częstotliwości mowy, zostają oklaski i wysokie dźwięki otoczenia."
            )
            st.session_state.ambient_eq_hp = st.slider(
                "HPF — odcięcie basu [Hz]", min_value=50, max_value=800,
                value=st.session_state.ambient_eq_hp, step=10, key="eq_hp_sl",
                help="Domyślnie: 200 Hz. Odcina bas i muzykę z kanału ambient.\n"
                     "• 100 Hz = delikatne odcięcie\n"
                     "• 200 Hz = zalecane (odcina bas)\n"
                     "• 400+ Hz = agresywne, zostają tylko wysokie tony"
            )
            st.session_state.ambient_eq_lpf_speech = st.slider(
                "HPF Mowy — odcięcie głosu [Hz]", min_value=1000, max_value=8000,
                value=st.session_state.ambient_eq_lpf_speech, step=100, key="eq_lpf_sl",
                help="Domyślnie: 3500 Hz. Odcina pasmo mowy z ambientu (200–3500 Hz).\n"
                     "• 2500 Hz = agresywne odcięcie mowy\n"
                     "• 3500 Hz = zalecane\n"
                     "• 5000+ Hz = zostają prawie tylko sybilantsy i oklaski"
            )
            st.session_state.ambient_eq_presence = st.slider(
                "Boost obecności [dB]", min_value=0.0, max_value=14.0,
                value=st.session_state.ambient_eq_presence, step=0.5, key="eq_pres_sl",
                help="Domyślnie: 4.0 dB. Wzmacnia pasmo 2–5 kHz (oklaski, powietrze).\n"
                     "• 0 = bez wzmocnienia\n"
                     "• 3–6 dB = zalecane\n"
                     "• 10+ dB = agresywne wzmocnienie, ryzyko syczenia"
            )
    elif st.session_state.mix_mode != MIX_MODE_DUBBING:
        if remembered_section("🎙️ Głośności lektora", "exp_miks_vol", True):
            if st.session_state.get("voiceover_tts_engine") not in VOICEOVER_ENGINES:
                st.session_state.voiceover_tts_engine = VOICEOVER_ENGINE_SYSTEM
            st.session_state.voiceover_tts_engine = st.radio(
                "Silnik lektora",
                VOICEOVER_ENGINES,
                index=VOICEOVER_ENGINES.index(st.session_state.voiceover_tts_engine),
                help="Stabilny lektor systemowy nie dopowiada słów i jest najlepszy do voice-over.\n"
                     "Qwen brzmi naturalniej, ale w trybie lektora potrafi uciąć, powtórzyć albo dopowiedzieć tekst.",
                key="voiceover_tts_engine_radio",
            )
            if st.session_state.voiceover_tts_engine == VOICEOVER_ENGINE_SYSTEM:
                st.caption("Ten tryb używa systemowego głosu macOS. Jest mniej efektowny niż Qwen, ale przewidywalny i nie halucynuje tekstu.")
            else:
                st.warning("Qwen jako lektor jest eksperymentalny. Jeśli usłyszysz dopowiadanie albo ucinanie, wróć do stabilnego lektora systemowego.")
            st.session_state.dub_vol = st.slider(
                "🎙️ Głos AI / lektor", min_value=0.0, max_value=3.0,
                value=st.session_state.dub_vol, step=0.05, key="voiceover_dub_vol_sl",
                help="Głośność wygenerowanego głosu AI nakładanego na oryginalny film.\n"
                     "• 1.0 = naturalna głośność TTS\n"
                     "• 1.3–1.7 = zwykle czytelny lektor\n"
                     "• 2.0+ = bardzo mocno z przodu"
            )
            st.session_state.voiceover_original_vol = st.slider(
                "🎬 Oryginalne audio filmu", min_value=0.0, max_value=1.5,
                value=float(st.session_state.voiceover_original_vol), step=0.05,
                key="voiceover_original_vol_sl",
                help="Głośność pełnego oryginalnego audio: oryginalny głos osoby, muzyka, tło i ambient.\n"
                     "• 0.20–0.35 = oryginał bardzo cicho pod lektorem\n"
                     "• 0.40–0.60 = typowy cichy voice-over\n"
                     "• 0.85 = zalecane, naturalny oryginał pod lektorem\n"
                     "• 1.0 = oryginał prawie jak w filmie"
            )
            if st.session_state.mix_mode == MIX_MODE_VOICEOVER_DUCK:
                st.session_state.voiceover_duck_amount = st.slider(
                    "📉 Przyciszenie oryginału pod lektorem", min_value=0.0, max_value=1.0,
                    value=float(st.session_state.voiceover_duck_amount), step=0.05,
                    key="voiceover_duck_amount_sl",
                    help="Jak mocno oryginalny dźwięk ma automatycznie schodzić w dół, gdy mówi głos AI.\n"
                         "• 0.0 = brak automatycznego ściszania\n"
                         "• 0.5 = delikatne zejście oryginału\n"
                         "• 0.7–0.9 = czytelny lektor, mniej konfliktu z oryginalnym głosem"
                )

    st.divider()

    # ── ZAAWANSOWANE OPCJE AI ──
    if remembered_section("🤖 Zaawansowane opcje AI", "exp_ai", True):
        # Klucz API Gemini
        new_key = st.text_input(
            "🔑 Klucz API Gemini (Tłumaczenie)", type="password",
            value=st.session_state.api_key,
            help="Klucz API Google Gemini — wymagany do tłumaczenia transkrypcji.\n"
                 "Uzyskaj bezpłatnie na: https://aistudio.google.com/apikey"
        )
        if new_key != st.session_state.api_key:
            st.session_state.api_key = new_key
            save_all_settings()
            st.success("Klucz zapisany.")

        st.divider()

        st.session_state.tts_model = "1.7B (Wysoka jakość)"
        st.caption(
            "Model Qwen3-TTS: 1.7B (wysoka jakość). "
            "Ten model jest używany na stałe dla lepszej jakości głosu i klonowania."
        )
        st.session_state.ref_audio_length = st.slider(
            "Długość próbki głosu [s]",
            min_value=5, max_value=30, value=st.session_state.ref_audio_length,
            help="Domyślnie: 12 sekund\n"
                 "• 5–8s = szybko, minimalna jakość klonowania\n"
                 "• 10–15s = optymalne — model uczy się barwy, rytmu i oddechu\n"
                 "• 20–30s = ryzyko zawieszenia modelu (znany bug Qwen3-TTS)\n"
                 "Próbka jest automatycznie wybierana z początku wokalu Demucs."
        )
        transl_opts = ["Gemini 2.5 Flash (Lokalizacja 2-Etapowa)", "Brak (Tylko transkrypcja)"]
        _ti = transl_opts.index(st.session_state.translation_model) \
               if st.session_state.translation_model in transl_opts else 0
        st.session_state.translation_model = st.selectbox(
            "Model tłumaczenia", transl_opts, index=_ti,
            help="Domyślnie: Gemini 2.5 Flash\n"
                 "• Gemini 2-etapowe: transkrypcja → surowe tłumaczenie → naturalizacja do stylu mówionego. Najlepsza jakość dla dubbingu.\n"
                 "• Brak — zostaje oryginalny tekst (np. do korekty ręcznej lub gdy źródło = cel)."
        )
        st.markdown("**Słownik nazw własnych**")
        st.caption(
            "Wpisz jedną poprawną nazwę, żeby aplikacja jej pilnowała, albo wpisz poprawkę: "
            "błędna wersja po lewej i poprawna po prawej."
        )
        _gl_col1, _gl_col2 = st.columns(2)
        with _gl_col1:
            glossary_wrong = st.text_input(
                "Nazwa albo błędna wersja",
                key="proper_name_wrong_input",
                placeholder="np. Opitkovanie albo Humsieng"
            )
        with _gl_col2:
            glossary_correct = st.text_input(
                "Poprawna wersja, jeśli to poprawka",
                key="proper_name_correct_input",
                placeholder="np. Humsienk"
            )
        if st.button("➕ Dodaj i zapisz wpis", use_container_width=True):
            ok, msg = append_proper_name_glossary_entry(glossary_wrong, glossary_correct)
            if ok:
                st.toast(f"✅ {msg}")
            else:
                st.warning(msg)
        _glossary_value = ensure_proper_name_glossary()
        if not str(st.session_state.get("proper_name_glossary_editor", "") or "").strip():
            st.session_state.proper_name_glossary_editor = _glossary_value
        _edited_glossary = st.text_area(
            "Aktualne wpisy słownika",
            key="proper_name_glossary_editor",
            height=190,
            help="Możesz też edytować ręcznie: jedna nazwa na linię albo poprawka w formacie 'błędnie -> poprawnie'."
        )
        if str(_edited_glossary or "").strip():
            st.session_state.proper_name_glossary = _edited_glossary
        else:
            st.session_state.proper_name_glossary = ensure_proper_name_glossary()
            st.rerun()
        st.session_state.demucs_shifts = st.slider(
            "Jakość separacji Demucs (shifts)",
            min_value=1, max_value=10, value=st.session_state.demucs_shifts,
            help="Domyślnie: 2\n"
                 "• 1–2 = szybko (ok. 2–5 min/film), dobra jakość. ZALECANE dla Mac.\n"
                 "• 4–6 = wyższa jakość separacji głosu, 2× wolniej\n"
                 "• 10 = maksimum, bardzo wolno. Sens tylko dla materiałów studyjnych.\n"
                 "Każdy 'shift' = jeden losowy przebieg Demucs; wynik jest uśredniany."
        )
        st.session_state.pitch_adj = st.slider(
            "🎵 Pitch Shift (korekta tonu głosu)",
            min_value=-12.0, max_value=12.0,
            value=float(st.session_state.pitch_adj), step=0.5,
            help="Domyślnie: 0.0 (bez zmian)\n"
                 "• +1 do +3 = głos wyższy\n"
                 "• -1 do -3 = głos niższy, bardziej basowy\n"
                 "Tempo pozostaje niezmienione — tylko ton."
        )

    save_all_settings()

    st.divider()

    # ── USTAWIENIA WYJŚCIA WIDEO ──
    if remembered_section("🎬 Jakość wyjściowego wideo", "exp_video", False):
        _res_opts = ["Auto (jak oryginał)", "480p", "720p", "1080p", "4K (2160p)"]
        _res_idx  = _res_opts.index(st.session_state.output_resolution) \
                    if st.session_state.output_resolution in _res_opts else 0
        st.session_state.output_resolution = st.selectbox(
            "Rozdzielczość",
            _res_opts,
            index=_res_idx,
            help="Domyślnie: Auto — wideo wyjściowe ma taką samą rozdzielczość jak oryginał.\n"
                 "• 480p = mały plik, niska jakość\n"
                 "• 720p = HD, dobry kompromis\n"
                 "• 1080p = Full HD\n"
                 "• 4K = bardzo duży plik, wymaga źródła 4K"
        )
        st.session_state.output_bitrate_mbps = st.slider(
            "Bitrate wideo [Mbps]",
            min_value=2.0, max_value=100.0,
            value=float(st.session_state.output_bitrate_mbps),
            step=1.0,
            help="Domyślnie: 5 Mbps — dobry dla 1080p przy przesyłaniu online.\n"
                 "• 2–4 Mbps = 480p/720p, mały plik\n"
                 "• 5–8 Mbps = 1080p — zalecane\n"
                 "• 10–20 Mbps = 1080p wysoka jakość / 4K skompresowane\n"
                 "• 35–50 Mbps = 4K dobra jakość (YouTube używa ~35–45 Mbps)\n"
                 "• 80–100 Mbps = 4K archiwalna jakość, bardzo duże pliki"
        )
        # Podpowiedź dla wybranej rozdzielczości
        _res_hints = {
            "Auto (jak oryginał)": "💡 Ustaw bitrate dopasowany do rozdzielczości źródła.",
            "480p":    "💡 Zalecany bitrate: 2–3 Mbps",
            "720p":    "💡 Zalecany bitrate: 3–5 Mbps",
            "1080p":   "💡 Zalecany bitrate: 5–10 Mbps",
            "4K (2160p)": "💡 Zalecany bitrate: 35–50 Mbps (YouTube) lub 80–100 Mbps (archiwum)",
        }
        st.caption(_res_hints.get(st.session_state.output_resolution, ""))

    save_all_settings()

    st.divider()
    _is_gen = st.session_state.get("is_generating", False)
    st.session_state.show_log = st.checkbox(
        "📋 Pokaż okno logów",
        value=st.session_state.show_log,
        disabled=_is_gen,
        help="Włącz/wyłącz szczegółowe logi procesu. Gdy wyłączone — widoczny jest tylko pasek postępu.\n"
             "⚠️ Zablokowane podczas generowania — zmiana wywołałaby restart pipeline."
    )
    save_all_settings()

    st.divider()
    if st.button("🗑️ Wyczyść wszystko", use_container_width=True,
                 disabled=_is_gen,
                 help="Usuwa wszystkie pliki z folderów uploads i output (łącznie z cache transkrypcji). Czysta karta."):
        clear_temp_files()
        st.rerun()


# ─────────────────────────────────────────────────────────────
# UI — GŁÓWNY PANEL
# ─────────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 1])

# ── COL1: PLIK WEJŚCIOWY + EDYTOR ────────────────────────────
with col1:
    st.markdown("### 1. Plik Wejściowy")

    # ── WYBÓR TRYBU WEJŚCIA ──────────────────────────────────────
    _input_mode = st.radio(
        "Źródło:",
        ["Lokalny Plik z Dysku", "Pobierz z YouTube", "Tekst → Audio"],
        horizontal=True,
        label_visibility="collapsed",
        key="input_mode"
    )
    _tts_mode = (_input_mode == "Tekst → Audio")

    # Auto-sync active_main_panel z wyborem trybu (bez extra kliknięcia)
    _desired_panel = "text_tts" if _tts_mode else "dubbing"
    if st.session_state.get("active_main_panel") != _desired_panel:
        st.session_state.active_main_panel = _desired_panel
        st.rerun()

    if not _tts_mode:
        history_items = get_media_history()
        if history_items:
            with st.container(border=True):
                st.markdown("**Ostatnie projekty / pliki z dysku**")
                selected_history_path = st.selectbox(
                    "Załaduj wcześniejszy materiał",
                    [item["path"] for item in history_items],
                    format_func=lambda path: media_history_label(
                        next((item for item in history_items if item["path"] == path), {"path": path})
                    ),
                    key="media_history_select",
                    label_visibility="collapsed",
                )
                h_col1, h_col2 = st.columns([1, 1])
                with h_col1:
                    if st.button("↩️ Załaduj z historii", use_container_width=True, key="load_media_history_btn"):
                        if selected_history_path and Path(selected_history_path).exists():
                            selected_history_item = next(
                                (item for item in history_items if item["path"] == selected_history_path),
                                {},
                            )
                            _clear_working_files()
                            _reset_project_runtime_state()
                            st.session_state.local_file_path = selected_history_path
                            st.session_state.active_main_panel = "dubbing"
                            remember_media_file(selected_history_path, selected_history_item.get("source", "Historia"))
                            st.rerun()
                        else:
                            st.warning("Ten plik nie jest już dostępny na dysku.")
                with h_col2:
                    if st.button("🔄 Odśwież listę", use_container_width=True, key="refresh_media_history_btn"):
                        st.session_state.media_history = get_media_history()
                        st.rerun()
        else:
            st.caption("Historia pojawi się po pierwszym załadowaniu pliku albo pobraniu filmu z YouTube.")

    if _input_mode == "Lokalny Plik z Dysku":
        if st.button("📂 Wybierz plik wideo/audio z dysku", use_container_width=True):
            try:
                res = subprocess.run(
                    ["osascript", "-e", 'POSIX path of (choose file with prompt "Wybierz plik wideo lub audio")'],
                    capture_output=True, text=True
                )
                if res.returncode == 0 and res.stdout.strip():
                    # Wyczyść poprzednią sesję przed załadowaniem nowego pliku
                    _clear_working_files()
                    _reset_project_runtime_state()
                    st.session_state.local_file_path = res.stdout.strip()
                    st.session_state.active_main_panel = "dubbing"
                    remember_media_file(res.stdout.strip(), "Lokalny plik")
                    st.rerun()
            except Exception:
                pass

    elif _input_mode == "Pobierz z YouTube":
        yt_url = st.text_input("Link YouTube:", key="yt_url_input")
        yt_q   = st.selectbox("Jakość:", ["1080p", "1440p", "2160p (4K)", "720p", "Najlepsza"])
        if st.button("⬇️ Pobierz z YouTube", use_container_width=True):
            if not yt_url:
                st.error("Podaj link YouTube.")
            else:
                # ── Sprawdź czy film już jest pobrany ──────────────────
                # yt-dlp zapisuje jako "tytuł_VIDEOID.mp4" — ID jest zawsze w nazwie
                def _extract_yt_id(url):
                    import re
                    patterns = [
                        r"(?:v=|youtu\.be/|shorts/|embed/|v/)([A-Za-z0-9_-]{11})",
                    ]
                    for pat in patterns:
                        m = re.search(pat, url)
                        if m:
                            return m.group(1)
                    return None

                yt_id = _extract_yt_id(yt_url)
                existing_file = None
                if yt_id and UPLOAD_DIR.exists():
                    candidates = sorted(
                        UPLOAD_DIR.iterdir(),
                        key=lambda p: p.stat().st_size if p.exists() and p.is_file() else 0,
                        reverse=True,
                    )
                    for f in candidates:
                        if (
                            f.suffix.lower() in [".mp4", ".mkv", ".webm", ".mov"]
                            and yt_id in f.name
                            and is_probably_media_file(f)
                        ):
                            existing_file = f
                            break

                if existing_file and existing_file.exists():
                    # Film już pobrany — pomiń pobieranie, tylko wyczyść pliki robocze
                    _reset_project_runtime_state()
                    st.session_state.local_file_path = str(existing_file)
                    st.session_state.active_main_panel = "dubbing"
                    remember_media_file(existing_file, "Pobrane z YouTube")
                    st.success(f"⚡ Film już pobrany: {existing_file.name}")
                    time.sleep(1)
                    st.rerun()
                else:
                    # Nowy film — wyczyść poprzednią sesję i pobierz
                    _clear_working_files()
                    _reset_project_runtime_state()

                    with st.spinner("Pobieranie..."):
                        log_yt = st.empty()
                        qmap   = {"1080p": 1080, "1440p": 1440, "2160p (4K)": 2160, "720p": 720, "Najlepsza": 9999}
                        dl     = download_youtube_video(yt_url, qmap.get(yt_q, 1080), log_yt)
                        if dl and os.path.exists(dl):
                            st.session_state.local_file_path = dl
                            st.session_state.active_main_panel = "dubbing"
                            remember_media_file(dl, "Pobrane z YouTube")
                            st.success("Pobrano!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Pobieranie nieudane.")

    input_path = None

    if _tts_mode:
        render_text_tts_left_panel()

    if not _tts_mode:
        local_path_input = st.text_input("Ścieżka do pliku:", key="local_file_path")
        input_path = None

        if local_path_input:
            clean = local_path_input.strip('"').strip("'").strip()
            if os.path.exists(clean):
                input_path = Path(clean)
                if not is_probably_media_file(input_path):
                    input_path = None
                    st.error("Wybrany plik wygląda na plik metadanych macOS albo jest pusty/uszkodzony. Wybierz właściwy plik wideo/audio.")
                else:
                    is_video   = input_path.suffix.lower()[1:] in ["mp4", "mov", "avi", "mkv"]
                    try:
                        preview_url = media_file_url(input_path)
                        if is_video:
                            if preview_url:
                                st.markdown(
                                    f"""
                                    <video controls preload="metadata"
                                           style="width:100%; max-height:46vh; background:#000; display:block; border-radius:6px;">
                                        <source src="{html.escape(preview_url, quote=True)}">
                                    </video>
                                    """,
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.video(str(input_path))
                        else:
                            if preview_url:
                                st.markdown(
                                    f"""
                                    <audio controls preload="metadata" style="width:100%;">
                                        <source src="{html.escape(preview_url, quote=True)}">
                                    </audio>
                                    """,
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.audio(str(input_path))
                    except Exception:
                        st.info("Podgląd niedostępny dla tego pliku.")
                    if not any(item.get("path") == str(input_path.resolve()) for item in st.session_state.get("media_history", [])):
                        remember_media_file(input_path, "Lokalny plik")
            else:
                st.error("Nie znaleziono pliku.")

        if input_path:
            if st.button("🔍 ANALIZA & TŁUMACZENIE", use_container_width=True):
                t0         = time.time()
                cache_data = load_cache(str(input_path))

                with st.spinner("Transkrypcja (Whisper large-v3)..."):
                    if cache_data.get("transcription") and cache_data.get("segments"):
                        segments = cache_data["segments"]
                        # Filtruj halucynacje z cache
                        _bad = ["dimatorzok", "субтитр", "subscrib", "amara.org", "translation by"]
                        segments = [s for s in segments if not any(b in s.get("text","").lower() for b in _bad)]
                        segments = apply_proper_name_glossary_to_segments(segments)
                        # Odbuduj tekst z segmentów — KAŻDY SEGMENT NA OSOBNEJ LINII
                        # (wymagane dla synchronizacji per-segment)
                        original_text = "\n".join(s["text"] for s in segments if s.get("text","").strip())
                        # Zaktualizuj cache jeśli tekst się zmienił (np. po gap-filling)
                        if original_text != cache_data["transcription"]:
                            cache_data["transcription"] = original_text
                            cache_data["segments"] = segments
                            save_cache(str(input_path), cache_data)
                        st.toast("⚡ Transkrypcja z cache.")
                    else:
                        original_text, segments = transcribe_material(
                            str(input_path),
                            source_lang_hint=LANGUAGES[st.session_state.source_lang]
                        )
                        segments = apply_proper_name_glossary_to_segments(segments)
                        original_text = "\n".join(s["text"] for s in segments if s.get("text","").strip())
                        if original_text:
                            cache_data["transcription"] = original_text
                            cache_data["segments"]      = segments
                            save_cache(str(input_path), cache_data)

                if not original_text:
                    st.error("❌ Transkrypcja nieudana. Sprawdź czy plik zawiera wyraźną mowę i czy zainstalowano mlx-whisper.")
                else:
                    st.session_state.original_view = original_text

                    if st.session_state.translation_model != "Brak (Tylko transkrypcja)":
                        target_lang    = st.session_state.target_lang
                        seg_trans_key  = f"seg_translations_{target_lang}"

                        with st.spinner(f"Tłumaczenie {len(segments)} segmentów → {target_lang}..."):
                            seg_translations = []
                            if seg_trans_key in cache_data.get("translations", {}):
                                cached_val = cache_data["translations"][seg_trans_key]
                                # Obsłuż zarówno listę (nowy format) jak i string (stary format)
                                if isinstance(cached_val, list):
                                    cached_lines = cached_val
                                else:
                                    cached_lines = split_lines_preserve_count(cached_val)
                                if len(cached_lines) == len(segments):
                                    seg_translations = [
                                        apply_proper_name_glossary_to_text(line)
                                        for line in cached_lines
                                    ]
                                    if seg_translations != cached_lines:
                                        cache_data.setdefault("translations", {})[seg_trans_key] = seg_translations
                                        save_cache(str(input_path), cache_data)
                                    st.toast(f"⚡ Tłumaczenie ({target_lang}) z cache.")
                                else:
                                    cache_data.get("translations", {}).pop(seg_trans_key, None)
                                    log_msg = (
                                        f"[Cache] Pomijam tłumaczenie z cache ({target_lang}): "
                                        f"{len(cached_lines)} linii vs {len(segments)} segmentów."
                                    )
                                    st.warning(log_msg)
                                    seg_translations = translate_segments_ai(segments, target_lang)
                                    if seg_translations:
                                        cache_data.setdefault("translations", {})[seg_trans_key] = seg_translations
                                        save_cache(str(input_path), cache_data)
                            elif segments:
                                seg_translations = translate_segments_ai(segments, target_lang)
                                if seg_translations:
                                    cache_data.setdefault("translations", {})[seg_trans_key] = seg_translations
                                    save_cache(str(input_path), cache_data)
                            else:
                                tr = translate_text_ai(original_text, target_lang)
                                seg_translations = [tr] if tr else [original_text]

                        st.session_state.original_view_input = original_text
                        if seg_translations:
                            st.session_state.dub_edit            = "\n".join(seg_translations)
                            st.session_state.last_translated_lang = target_lang
                        else:
                            st.session_state.last_translated_lang = ""
                    else:
                        st.session_state.original_view_input = original_text
                        st.session_state.dub_edit            = original_text
                        st.session_state.last_translated_lang = "Oryginał"

                    elapsed = time.time() - t0
                    st.success(
                        f"✅ Gotowe ({elapsed:.1f}s) — "
                        f"{len(segments)} segmentów · "
                        f"{len(st.session_state.dub_edit.splitlines())} linii dubbing."
                    )

        st.markdown("### 2. Edytor Scenariusza")
        st.markdown("**Oryginalny tekst (z Whisper large-v3):**")
        if st.session_state.pop("_pending_original_view_input_update", False):
            st.session_state.original_view_input = st.session_state.get("original_view", "")
        if st.session_state.pop("_pending_dub_edit_update", False):
            st.session_state.dub_edit = st.session_state.get("_pending_dub_edit_value", st.session_state.get("dub_edit", ""))
            st.session_state.pop("_pending_dub_edit_value", None)

        edited_original = st.text_area(
            "Oryginalny tekst",
            key="original_view_input",
            height=585,
            placeholder="Tekst transkrypcji pojawi się tutaj po analizie...",
            label_visibility="collapsed",
            help="Możesz poprawić błędy transkrypcji Whisper. Po zmianach kliknij 'Zatwierdź korektę'."
        )

        _editor_segments = []
        if input_path:
            try:
                _editor_segments = load_cache(str(input_path)).get("segments", [])
            except Exception:
                _editor_segments = []

        if _editor_segments:
            _original_values = [s.get("text", "") for s in _editor_segments]
            _updated_original_values = render_segment_text_editor(
                input_path,
                _editor_segments,
                _original_values,
                "orig_segment_editor",
                "🎧 Segmentowy edytor oryginału z odsłuchem",
                "Poprawiasz konkretny blok przypięty do konkretnego czasu. Odsłuch pod spodem to wycinek oryginalnego filmu dla tego segmentu."
            )
            if _updated_original_values is not None and input_path:
                _cd = load_cache(str(input_path))
                _segs = [dict(s) for s in _cd.get("segments", [])]
                _old_original = "\n".join(s.get("text", "").strip() for s in _segs if s.get("text", "").strip())
                for _i, _txt in enumerate(_updated_original_values[:len(_segs)]):
                    _segs[_i]["text"] = apply_proper_name_glossary_to_text(_txt)
                _new_original = "\n".join(s.get("text", "").strip() for s in _segs if s.get("text", "").strip())
                if _new_original == _old_original:
                    st.toast("Segmenty bez zmian — tłumaczenie zostaje.")
                    st.rerun()
                _cd["segments"] = _segs
                _cd["transcription"] = _new_original
                _cd["translations"] = {}
                save_cache(str(input_path), _cd)
                st.session_state.original_view = _new_original
                st.session_state["_pending_original_view_input_update"] = True
                if st.session_state.translation_model == "Brak (Tylko transkrypcja)":
                    st.session_state["_pending_dub_edit_value"] = _new_original
                    st.session_state["_pending_dub_edit_update"] = True
                    st.session_state.last_translated_lang = "Oryginał"
                st.toast("✅ Zapisano segmenty oryginału. Tłumaczenia wyczyszczone — przetłumacz ponownie.")
                st.rerun()

        def _run_translation(original_text, target_lang, inp):
            cache_data = load_cache(str(inp)) if inp else {"transcription": "", "segments": [], "translations": {}}
            segments   = cache_data.get("segments", [])
            key        = f"seg_translations_{target_lang}"
            if key in cache_data.get("translations", {}):
                cached_val = cache_data["translations"][key]
                # Obsłuż zarówno listę (nowy format) jak i string (stary format)
                if isinstance(cached_val, list):
                    cached_lines = cached_val
                else:
                    cached_lines = split_lines_preserve_count(cached_val)
                if len(cached_lines) == len(segments):
                    fixed_lines = [
                        apply_proper_name_glossary_to_text(line)
                        for line in cached_lines
                    ]
                    if fixed_lines != cached_lines and inp:
                        cache_data.setdefault("translations", {})[key] = fixed_lines
                        save_cache(str(inp), cache_data)
                    return fixed_lines
                cache_data.get("translations", {}).pop(key, None)
                if inp:
                    save_cache(str(inp), cache_data)
            elif segments:
                result = translate_segments_ai(segments, target_lang)
                if inp and result:
                    cache_data.setdefault("translations", {})[key] = result
                    save_cache(str(inp), cache_data)
                return result
            if segments:
                result = translate_segments_ai(segments, target_lang)
                if inp and result:
                    cache_data.setdefault("translations", {})[key] = result
                    save_cache(str(inp), cache_data)
                return result
            else:
                tr = translate_text_ai(original_text, target_lang)
                return [tr] if tr else [original_text]

        _orig_changed = (
            st.session_state.original_view_input != st.session_state.original_view
            and st.session_state.original_view_input.strip()
        )

        if st.button("✅ Zatwierdź korektę oryginału i przetłumacz ponownie",
                     use_container_width=True,
                     type="primary" if _orig_changed else "secondary"):
            new_text = st.session_state.original_view_input.strip()
            if not new_text:
                st.warning("Brak tekstu do zatwierdzenia.")
            else:
                new_text = apply_proper_name_glossary_to_text(new_text)
                st.session_state.original_view = new_text
                edited_lines = split_lines_preserve_count(new_text)

                if input_path:
                    cache_data   = load_cache(str(input_path))
                    old_segments = cache_data.get("segments", [])
                    if old_segments:
                        rebuilt_segments = rebuild_segments_from_lines(old_segments, edited_lines)
                        cache_data["segments"] = rebuilt_segments
                        if len(edited_lines) != len(old_segments):
                            st.warning("⚠️ Zmieniono liczbę linii — segmenty czasowe zostały zachowane i przeliczone proporcjonalnie.")
                    cache_data["transcription"] = new_text
                    cache_data["translations"]  = {}
                    save_cache(str(input_path), cache_data)

                target_lang = st.session_state.target_lang
                if st.session_state.translation_model != "Brak (Tylko transkrypcja)":
                    with st.spinner(f"Tłumaczę na {target_lang}..."):
                        segs = cache_data.get("segments", []) if input_path else []
                        if segs:
                            translated_segs = translate_segments_ai(segs, target_lang)
                            translated = "\n".join(translated_segs)
                            if input_path and translated_segs:
                                c2 = load_cache(str(input_path))
                                c2.setdefault("translations", {})[f"seg_translations_{target_lang}"] = translated_segs
                                save_cache(str(input_path), c2)
                        else:
                            translated = translate_text_ai(new_text, target_lang)
                        if translated:
                            st.session_state.dub_edit             = translated
                            st.session_state.last_translated_lang = target_lang
                            st.success(f"✅ Przetłumaczono na {target_lang}.")
                else:
                    st.session_state.dub_edit = new_text
                    st.session_state.last_translated_lang = "Oryginał"
                st.rerun()

        # Przycisk retranslacji — zawsze widoczny gdy jest transkrypcja.
        # Przy zmianie języka musi pojawić się od razu, nawet jeśli edytor ma lokalne zmiany.
        if st.session_state.original_view:
            current_dub_lang = st.session_state.last_translated_lang
            target = st.session_state.target_lang
            lang_changed = current_dub_lang != target and current_dub_lang != ""

            btn_label = f"🔄 Przetłumacz ponownie → {target}"
            btn_type = "primary" if lang_changed else "secondary"
            btn_help = (
                f"Język w ustawieniach zmieniony z '{current_dub_lang}' na '{target}'. "
                f"Kliknij żeby przetłumaczyć na nowy język."
                if lang_changed
                else f"Przetłumacz tekst oryginalny ponownie na {target} (np. po ręcznej korekcie transkrypcji)."
            )

            if st.button(btn_label, use_container_width=True, type=btn_type, help=btn_help):
                with st.spinner(f"Tłumaczę na {target}..."):
                    segs = _run_translation(st.session_state.original_view, target, input_path)
                    if segs:
                        st.session_state.dub_edit             = "\n".join(segs)
                        st.session_state.last_translated_lang = target
                        st.rerun()

        if input_path and not st.session_state.get("dub_edit", "").strip():
            _cached_dub = load_cache(str(input_path)).get("translations", {}).get(
                f"seg_translations_{st.session_state.target_lang}"
            )
            if _cached_dub:
                if isinstance(_cached_dub, list):
                    st.session_state.dub_edit = "\n".join(str(line) for line in _cached_dub)
                else:
                    st.session_state.dub_edit = str(_cached_dub)
                st.session_state.last_translated_lang = st.session_state.target_lang

        st.text_area(
            f"Tekst do Dubbingu ({st.session_state.target_lang}):",
            height=585,
            key="dub_edit",
            help="Tłumaczenie z Gemini. Każda linia = jeden segment czasowy z transkrypcji. "
                 "Możesz edytować — dodaj przecinki i kropki dla naturalnych pauz głosu AI."
        )

        if input_path and _editor_segments and st.session_state.get("dub_edit"):
            _dub_values = fit_lines_to_segments(st.session_state.dub_edit, _editor_segments)
            _updated_dub_values = render_segment_text_editor(
                input_path,
                _editor_segments,
                _dub_values,
                f"dub_segment_editor_{st.session_state.target_lang}",
                f"🎧 Segmentowy edytor dubbingu ({st.session_state.target_lang}) z odsłuchem oryginału",
                "Poprawiasz tekst dubbingu przypięty do tych samych czasów Whispera. Odsłuch jest nadal z oryginału, żeby łatwo sprawdzić, co naprawdę było powiedziane."
            )
            if _updated_dub_values is not None:
                _fixed = [apply_proper_name_glossary_to_text(x) for x in _updated_dub_values]
                _cd = load_cache(str(input_path))
                _lang = st.session_state.target_lang
                _cd.setdefault("translations", {})[f"seg_translations_{_lang}"] = _fixed
                save_cache(str(input_path), _cd)
                st.session_state["_pending_dub_edit_value"] = "\n".join(_fixed)
                st.session_state["_pending_dub_edit_update"] = True
                st.session_state.last_translated_lang = _lang
                st.toast(f"✅ Zapisano segmenty dubbingu ({_lang}).")
                st.rerun()

        if st.button("✅ Zatwierdź ręczne zmiany", use_container_width=True):
            if input_path and st.session_state.dub_edit:
                cd   = load_cache(str(input_path))
                lang = st.session_state.target_lang
                # Zapisz jako listę linii (spójny format z seg_translations_*)
                fixed_dub_edit = apply_proper_name_glossary_to_text(st.session_state.dub_edit)
                edited_lines = split_lines_preserve_count(fixed_dub_edit)
                cd.setdefault("translations", {})[f"seg_translations_{lang}"] = edited_lines
                # Zaktualizuj też segmenty jeśli liczba linii się zgadza
                segs = cd.get("segments", [])
                if segs and len(edited_lines) == len(segs):
                    pass  # segmenty OK — linie 1:1
                elif segs and len(edited_lines) != len(segs):
                    # Liczba linii się różni — wyczyść seg_translations żeby przy
                    # kolejnym załadowaniu Gemini przetłumaczył od nowa z poprawką
                    pass  # zachowaj zapis — użytkownik świadomie edytował
                save_cache(str(input_path), cd)
                st.toast(f"✅ Zmiany zapisane w cache ({lang}, {len(edited_lines)} linii).")


# ── COL2: GENERATOR ───────────────────────────────────────────
with col2:
    if st.session_state.get("active_main_panel") == "text_tts":
        render_text_to_audio_panel()
        st.stop()

    st.markdown("### 3. Generator napisów YouTube")
    if input_path:
        _saved_sub_langs = [
            lang for lang in st.session_state.get("subtitle_target_langs", [st.session_state.target_lang])
            if lang in TARGET_LANGUAGES
        ] or [st.session_state.target_lang]
        _saved_sub_formats = [
            fmt for fmt in st.session_state.get("subtitle_formats", ["SRT"])
            if fmt in ["SRT", "VTT"]
        ] or ["SRT"]
        st.session_state.subtitle_target_langs = _saved_sub_langs
        st.session_state.subtitle_formats = _saved_sub_formats

        st.multiselect(
            "Języki napisów",
            TARGET_LANGUAGES,
            key="subtitle_target_langs",
            help="Możesz wybrać kilka języków naraz. Tłumaczenia zapiszą się w cache, więc kolejne eksporty są szybkie."
        )
        sub_col1, sub_col2 = st.columns([1, 1])
        with sub_col1:
            st.multiselect(
                "Format",
                ["SRT", "VTT"],
                key="subtitle_formats",
                help="SRT jest najprostszym formatem dla YouTube. VTT też jest obsługiwany i przydaje się na stronach www."
            )
        with sub_col2:
            st.checkbox(
                "Dodaj napisy oryginalne",
                key="subtitle_include_original",
                help="Zapisuje dodatkowy plik z transkrypcją w języku oryginału."
            )

        if st.button("📝 Generuj pliki napisów", use_container_width=True, type="primary"):
            selected_langs = st.session_state.get("subtitle_target_langs", [])
            selected_formats = st.session_state.get("subtitle_formats", ["SRT"])
            include_original = bool(st.session_state.get("subtitle_include_original", True))
            if not selected_langs and not include_original:
                st.warning("Wybierz przynajmniej jeden język albo włącz napisy oryginalne.")
            elif st.session_state.translation_model == "Brak (Tylko transkrypcja)" and selected_langs:
                st.warning("Masz ustawione: Brak tłumaczenia. Włącz Gemini w opcjach AI albo generuj tylko napisy oryginalne.")
            else:
                t0 = time.time()
                with st.spinner("Tworzę napisy z timingami Whispera..."):
                    outputs, zip_path, from_cache = create_subtitle_exports(
                        input_path,
                        selected_langs,
                        include_original=include_original,
                        formats=selected_formats,
                    )
                if outputs:
                    st.session_state.subtitle_output_paths = [str(p) for p in outputs]
                    st.session_state.subtitle_zip_path = str(zip_path) if zip_path else None
                    st.session_state.final_subtitle_path = str(outputs[0])
                    cache_note = "z cache" if from_cache else "po transkrypcji"
                    st.success(f"✅ Gotowe ({time.time() - t0:.1f}s, {cache_note}) · plików: {len(outputs)}")
                    st.rerun()
                else:
                    st.error("Nie udało się wygenerować napisów. Sprawdź, czy materiał zawiera mowę i czy jest klucz Gemini dla tłumaczeń.")

        subtitle_paths = [
            Path(p) for p in st.session_state.get("subtitle_output_paths", [])
            if p and Path(p).exists()
        ]
        subtitle_zip = Path(st.session_state.get("subtitle_zip_path", "")) if st.session_state.get("subtitle_zip_path") else None
        if subtitle_paths:
            st.markdown("**Gotowe pliki:**")
            st.markdown(
                '<iframe name="dm_download_frame" style="display:none;width:0;height:0;border:0;"></iframe>',
                unsafe_allow_html=True,
            )
            if subtitle_zip and subtitle_zip.exists():
                zip_url = media_file_url(subtitle_zip, download=True)
                if zip_url:
                    st.markdown(
                        f"""
                        <form class="dm-download-form" action="{html.escape(zip_url, quote=True)}"
                              method="get" target="dm_download_frame">
                            <button class="dm-download-btn" type="submit">📦 Pobierz wszystkie napisy ZIP</button>
                        </form>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    with open(subtitle_zip, "rb") as _f:
                        st.download_button(
                            "📦 Pobierz wszystkie napisy ZIP",
                            data=_f,
                            file_name=subtitle_zip.name,
                            mime="application/zip",
                            use_container_width=True,
                            key="dl_subtitle_zip"
                        )

            for idx, spath in enumerate(subtitle_paths):
                dl_url = media_file_url(spath, download=True)
                label = f"📝 {spath.name}"
                if dl_url:
                    st.markdown(
                        f"""
                        <form class="dm-download-form" action="{html.escape(dl_url, quote=True)}"
                              method="get" target="dm_download_frame">
                            <button class="dm-download-btn" type="submit">{html.escape(label)}</button>
                        </form>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    with open(spath, "rb") as _f:
                        st.download_button(
                            label,
                            data=_f,
                            file_name=spath.name,
                            mime="text/vtt" if spath.suffix.lower() == ".vtt" else "application/x-subrip",
                            use_container_width=True,
                            key=f"dl_subtitle_single_{idx}"
                        )
    else:
        st.info("Wybierz lub pobierz film, żeby wygenerować napisy.")

    st.divider()
    st.markdown("### 4. Generator Dubbingu")

    btn_ph      = st.empty()
    status_ph   = st.empty()
    progress_ph = st.empty()
    timer_ph    = st.empty()
    log_box     = st.empty()

    # Pokaż czas ostatniego generowania (przeżywa rerun)
    if not st.session_state.is_generating and st.session_state.get("last_gen_time"):
        timer_ph.markdown(
            f"<div style='color:#a8ff78;font-size:0.82rem;margin-top:4px;'>"
            f"⏱️ Czas ostatniego generowania: {st.session_state['last_gen_time']}</div>",
            unsafe_allow_html=True
        )

    if not st.session_state.is_generating:
        if st.session_state.get("show_log", True):
            log_box.markdown(
                "<div style='min-height:120px;background:#0e1117;border:1px solid #333;"
                "border-radius:6px;color:#a8ff78;display:flex;align-items:center;"
                "justify-content:center;font-size:0.85rem;'>Logi pojawią się tutaj podczas generowania.</div>",
                unsafe_allow_html=True
            )

    generation_lang_mismatch = (
        not st.session_state.is_generating
        and st.session_state.translation_model != "Brak (Tylko transkrypcja)"
        and bool(st.session_state.get("dub_edit", "").strip())
        and bool(st.session_state.get("last_translated_lang", ""))
        and st.session_state.get("last_translated_lang") != st.session_state.target_lang
    )
    if generation_lang_mismatch:
        st.warning(
            f"Tekst dubbingu jest dla języka: {st.session_state.last_translated_lang}. "
            f"Przetłumacz ponownie na {st.session_state.target_lang}, zanim wygenerujesz dubbing."
        )

    btn_label = "⛔ ZATRZYMAJ" if st.session_state.is_generating else "🚀 GENERUJ DUBBING"
    if btn_ph.button(
        btn_label,
        use_container_width=True,
        key="gen_btn",
        disabled=generation_lang_mismatch
    ):
        if st.session_state.is_generating:
            request_cancel()
            st.session_state.is_generating = False
            st.warning("Zatrzymywanie procesu...")
            time.sleep(1)
            st.rerun()
        else:
            st.session_state.cancel_requested = False
            st.session_state.is_generating    = True
            st.rerun()

    # ── PIPELINE GENEROWANIA ──────────────────────────────────
    if st.session_state.is_generating:
        if not input_path or not st.session_state.dub_edit:
            st.error("Brak pliku lub tekstu dubbing. Wykonaj najpierw analizę.")
            st.session_state.is_generating = False
            time.sleep(1)
            st.rerun()
        else:
            try:
                gc.collect()
                st.session_state.full_logs.clear()

                if "pipeline_start_time" not in st.session_state:
                    st.session_state.pipeline_start_time = time.time()
                _pipeline_start = st.session_state.pipeline_start_time

                # Ustaw globalny placeholder — log_message aktualizuje go co ~0.4s (Python-side timer)
                _g_timer_ph = timer_ph
                timer_ph.markdown(
                    "<div style='color:#888;font-size:0.82rem;margin-top:4px;'>⏱️ 00:00</div>",
                    unsafe_allow_html=True
                )

                def _prog(ratio, text):
                    prog.progress(ratio, text=text)

                filename   = input_path.stem
                cache_data = load_cache(str(input_path))
                segments   = cache_data.get("segments", [])
                is_video   = input_path.suffix.lower()[1:] in ["mp4", "mov", "avi", "mkv"]

                expected_lang = st.session_state.target_lang
                current_lang = st.session_state.get("last_translated_lang", "")
                if (
                    st.session_state.translation_model != "Brak (Tylko transkrypcja)"
                    and current_lang
                    and current_lang != expected_lang
                ):
                    st.error(
                        f"Tekst dubbingu jest teraz dla języka: {current_lang}, "
                        f"a wybrany język dubbingu to: {expected_lang}. "
                        f"Kliknij najpierw 'Przetłumacz ponownie → {expected_lang}'."
                    )
                    st.session_state.is_generating = False
                    st.stop()

                use_qwen_preset_voice = (
                    st.session_state.get("voice_source") == "Głos z magazynu"
                    and st.session_state.get("voice_store_mode") == "Głos z bazy Qwen TTS"
                )
                if (
                    st.session_state.get("voice_source") == "Głos z magazynu"
                    and not use_qwen_preset_voice
                    and not get_voice_by_id(st.session_state.get("selected_voice_id", ""))
                ):
                    st.error("Wybrano głos z magazynu, ale nie ma dostępnej próbki. Dodaj próbkę albo wybierz głos z oryginalnego filmu.")
                    st.session_state.is_generating = False
                    time.sleep(1)
                    st.rerun()

                bg_path    = OUTPUT_DIR / "htdemucs" / filename / "no_vocals.wav"
                vocal_path = OUTPUT_DIR / "htdemucs" / filename / "vocals.wav"

                # ── TRWAŁY CACHE DEMUCS (per plik, przeżywa zmianę projektu) ──
                _demucs_cache_dir    = get_demucs_cache_dir(str(input_path))
                _cached_vocals       = _demucs_cache_dir / "vocals.wav"
                _cached_bg           = _demucs_cache_dir / "no_vocals.wav"
                selected_voice       = get_voice_by_id(st.session_state.get("selected_voice_id", ""))
                use_custom_voice     = (
                    st.session_state.get("voice_source") == "Głos z magazynu"
                    and not use_qwen_preset_voice
                    and selected_voice is not None
                )
                tts_ref_audio        = Path(selected_voice["path"]) if use_custom_voice else vocal_path
                ref_cache_dir        = Path(selected_voice["path"]).parent if use_custom_voice else _demucs_cache_dir

                _mix_mode  = st.session_state.get("mix_mode", DEFAULT_MIX_MODE)
                if _mix_mode not in MIX_MODES:
                    _mix_mode = DEFAULT_MIX_MODE
                _is_voiceover = _mix_mode in (MIX_MODE_VOICEOVER, MIX_MODE_VOICEOVER_DUCK)
                _voiceover_engine = st.session_state.get("voiceover_tts_engine", VOICEOVER_ENGINE_SYSTEM)
                if _voiceover_engine not in VOICEOVER_ENGINES:
                    _voiceover_engine = VOICEOVER_ENGINE_SYSTEM
                _use_system_voiceover = _is_voiceover and _voiceover_engine == VOICEOVER_ENGINE_SYSTEM
                _bg_vol    = float(st.session_state.bg_music_vol)
                _amb_vol   = float(st.session_state.ambient_vol)
                _dub_vol   = float(st.session_state.dub_vol)
                _orig_vol  = float(st.session_state.get("voiceover_original_vol", DEFAULT_VOICEOVER_ORIGINAL_VOL))
                _duck_amt  = float(st.session_state.get("voiceover_duck_amount", DEFAULT_VOICEOVER_DUCK_AMOUNT))
                _eq_hp     = int(st.session_state.ambient_eq_hp)
                _eq_pres   = float(st.session_state.ambient_eq_presence)
                _eq_lpf    = int(st.session_state.ambient_eq_lpf_speech)
                _shifts    = int(st.session_state.demucs_shifts)
                _auto_min  = bool(st.session_state.get("auto_min_tempo", False))
                _auto_max  = bool(st.session_state.get("auto_max_tempo", False))
                _min_t     = 1.0 if _auto_min else float(st.session_state.sync_min_tempo)
                _max_t     = float(st.session_state.sync_max_tempo)  # używane tylko gdy nie AUTO
                pitch_adj  = float(st.session_state.pitch_adj)

                N_STEPS = 6
                prog = progress_ph.progress(0.0, text="Startowanie pipeline...")
                status = status_ph.status("Produkcja dubbing...", expanded=True)

                # ── KROK 1: DEMUCS ──────────────────────────
                if _cached_vocals.exists() and _cached_bg.exists():
                    # ✅ Trwały cache — przywróć do folderu roboczego jeśli trzeba
                    (OUTPUT_DIR / "htdemucs" / filename).mkdir(parents=True, exist_ok=True)
                    if not vocal_path.exists():
                        shutil.copy2(_cached_vocals, vocal_path)
                    if not bg_path.exists():
                        shutil.copy2(_cached_bg, bg_path)
                    log_message(
                        f"[1/6] Demucs — pominięto (trwały cache: {_demucs_cache_dir.name[:24]}...).",
                        log_box, True
                    )
                elif bg_path.exists() and vocal_path.exists():
                    # Już w folderze roboczym — zapisz do trwałego cache
                    shutil.copy2(vocal_path, _cached_vocals)
                    shutil.copy2(bg_path, _cached_bg)
                    log_message("[1/6] Demucs — pominięto (już istnieje, zapisano do cache).", log_box, True)
                else:
                    _prog(0.02, text=f"[1/{N_STEPS}] Separacja głosu i tła (Demucs)...")
                    status.update(label=f"[1/{N_STEPS}] Demucs — separacja audio...")
                    log_message("[Demucs] Start separacji wokalu i tła...", log_box, True)
                    rc = run_command([
                        "demucs",
                        f"--shifts={_shifts}",
                        "--two-stems=vocals",
                        str(input_path),
                        "-o", str(OUTPUT_DIR)
                    ], log_box)
                    if rc != 0 or not vocal_path.exists():
                        log_message("❌ Demucs nieudany — brak vocals.wav.", log_box, True)
                        status.update(label="❌ Błąd separacji Demucs.", state="error")
                        st.session_state.is_generating = False
                        st.rerun()
                    # Zapisz wyniki do trwałego cache
                    if vocal_path.exists():
                        shutil.copy2(vocal_path, _cached_vocals)
                    if bg_path.exists():
                        shutil.copy2(bg_path, _cached_bg)
                    log_message(f"[Demucs] Wyniki zapisane do trwałego cache.", log_box, True)

                if _mix_mode == MIX_MODE_DUBBING and st.session_state.keep_bg and not bg_path.exists():
                    log_message("❌ Demucs nieudany — brak no_vocals.wav wymaganego do miksu tła.", log_box, True)
                    status.update(label="❌ Błąd separacji Demucs — brak tła.", state="error")
                    st.session_state.is_generating = False
                    st.rerun()

                _prog(0.17, text=f"[1/{N_STEPS}] Separacja — gotowa.")
                if _use_system_voiceover:
                    log_message("[TTS] Lektor: używam stabilnego lektora systemowego macOS.", log_box, True)
                elif use_custom_voice:
                    log_message(f"[TTS] Używam próbki głosu z magazynu: {selected_voice.get('name', 'bez nazwy')}", log_box, True)
                elif use_qwen_preset_voice:
                    log_message(f"[TTS] Używam głosu z bazy Qwen TTS: {st.session_state.get('dubbing_qwen_speaker', 'Ryan')}", log_box, True)
                else:
                    log_message("[TTS] Używam głosu z oryginalnego filmu.", log_box, True)

                # ── KROK 2: QWEN3-TTS BATCH ─────────────────
                dub_text_for_tts = apply_proper_name_glossary_to_text(st.session_state.dub_edit)
                dubbed_lines  = split_lines_preserve_count(dub_text_for_tts)
                use_per_seg   = st.session_state.auto_sync and bool(segments)
                dubbed_audio  = OUTPUT_DIR / f"{filename}_dubbed.wav"
                num_items     = len(segments) if use_per_seg else 0
                tts_style_text = DEFAULT_VOICEOVER_STYLE if _is_voiceover else ""

                if use_per_seg and num_items > 0:
                    processed_texts = fit_lines_to_segments(dub_text_for_tts, segments)
                    non_empty_count = len([x for x in processed_texts if x.strip()])
                    if len(dubbed_lines) != len(segments):
                        log_message(
                            f"[SYNC] Dopasowano liczbę linii dubbingu ({len(dubbed_lines)}) do liczby segmentów Whisper ({len(segments)}).",
                            log_box, True
                        )
                    if non_empty_count == 0:
                        log_message("❌ Brak tekstu do wygenerowania TTS.", log_box, True)
                        status.update(label="❌ Brak tekstu dubbingu.", state="error")
                        st.session_state.is_generating = False
                        st.rerun()

                    if _is_voiceover:
                        tts_segments = [dict(s) for s in segments[:num_items]]
                        for _seg, _txt in zip(tts_segments, processed_texts):
                            _seg["text"] = _txt
                        log_message(
                            "[LEKTOR] Tryb bezpieczny: bez łączenia segmentów i bez rozciągania głosu.",
                            log_box, True
                        )
                    else:
                        tts_segments, processed_texts, split_notes = expand_segments_for_tts(
                            segments[:num_items],
                            processed_texts
                        )
                        if split_notes:
                            details = ", ".join(f"{idx}→{count}" for idx, count in split_notes)
                            log_message(
                                f"[SYNC] Długie linie podzielone na krótsze frazy TTS ({details}).",
                                log_box, True
                            )
                    num_items = len(tts_segments)

                    _prog(0.18, text=f"[2/{N_STEPS}] Synteza głosu — start...")
                    _tts_label = "Lektor systemowy" if _use_system_voiceover else "Qwen3-TTS"
                    status.update(label=f"[2/{N_STEPS}] {_tts_label} — synteza {num_items} segmentów...")
                    log_message(f"[2/6] TTS: {num_items} segmentów | Silnik: {_tts_label}", log_box, True)

                    qwen_paths = [OUTPUT_DIR / f"{filename}_seg_{i}_raw.wav" for i in range(num_items)]

                    # Okna czasowe z Whisper — klucz do dynamicznego max_new_tokens
                    seg_durs = [
                        float(tts_segments[i]["end"]) - float(tts_segments[i]["start"])
                        for i in range(num_items)
                    ]

                    def _tts_progress(ratio, text):
                        _prog(0.18 + ratio * 0.45, text=f"[2/{N_STEPS}] {text}")

                    if _use_system_voiceover:
                        batch_rc = generate_system_voiceover_batch(
                            processed_texts,
                            st.session_state.target_lang,
                            qwen_paths,
                            segment_durations=seg_durs,
                            log_area=log_box,
                            progress_callback=_tts_progress,
                        )
                    elif use_qwen_preset_voice:
                        batch_rc = generate_qwen3_preset_tts_batch(
                            processed_texts,
                            st.session_state.target_lang,
                            st.session_state.get("dubbing_qwen_speaker", "Ryan"),
                            qwen_paths,
                            segment_durations=seg_durs,
                            log_area=log_box,
                            progress_callback=_tts_progress,
                            style_text=tts_style_text,
                            temperature_override=0.60 if _is_voiceover else 0.7,
                            top_p_override=0.82 if _is_voiceover else 0.85,
                            strict_timing=_is_voiceover,
                        )
                    else:
                        batch_rc = generate_qwen3_tts_batch(
                            processed_texts,
                            st.session_state.target_lang,
                            tts_ref_audio, qwen_paths,
                            segment_durations=seg_durs,
                            log_area=log_box,
                            progress_callback=_tts_progress,
                            ref_cache_dir=ref_cache_dir,
                            style_text=tts_style_text,
                        )
                    if batch_rc != 0:
                        log_message("❌ TTS batch nieudany.", log_box, True)
                        status.update(label="❌ Błąd generowania TTS.", state="error")
                        st.session_state.is_generating = False
                        st.rerun()

                    # ── KROK 3: STRETCH ──────────────────────
                    _prog(0.63, text=f"[3/{N_STEPS}] Dopasowanie tempa segmentów...")
                    status.update(label=f"[3/{N_STEPS}] Dopasowanie do okien czasowych...")
                    if _use_system_voiceover:
                        log_message("[3/6] Lektor systemowy: dopasowuję tylko segmenty, które nachodzą na następny tekst.", log_box, True)
                    elif _is_voiceover:
                        log_message("[3/6] Lektor Qwen: pomijam stretch, żeby nie wzmacniać artefaktów generatywnych.", log_box, True)
                    else:
                        log_message("[3/6] Stretch segmentów do okien Whisper...", log_box, True)

                    # Pobierz total_dur już tutaj — potrzebny dla ostatniego segmentu
                    total_dur = get_audio_duration(input_path)

                    stretched_paths = []
                    for i in range(num_items):
                        seg      = tts_segments[i]
                        seg_raw  = OUTPUT_DIR / f"{filename}_seg_{i}_raw.wav"
                        seg_fit  = OUTPUT_DIR / f"{filename}_seg_{i}_fit.wav"
                        seg_dur  = max(float(seg["end"]) - float(seg["start"]), 0.05)
                        raw_dur  = get_audio_duration(seg_raw) if seg_raw.exists() else 0
                        final_f  = seg_raw
                        is_last  = (i == num_items - 1)

                        if _use_system_voiceover and raw_dur > 0 and seg_dur > 0:
                            next_limit = total_dur if is_last else float(tts_segments[i + 1]["start"])
                            available_dur = max(next_limit - float(seg["start"]) - 0.035, 0.08)
                            if raw_dur > available_dur:
                                applied = min(max(raw_dur / available_dur, 1.01), 1.75)
                                rc2 = stretch_pitch_preserving(seg_raw, seg_fit, applied)
                                if rc2 == 0 and seg_fit.exists() and seg_fit.stat().st_size > 512:
                                    final_f = seg_fit
                                    log_message(
                                        f"[LEKTOR] Seg {i+1}: tempo {applied:.2f}x "
                                        f"(raw={raw_dur:.2f}s, okno={available_dur:.2f}s).",
                                        log_box
                                    )

                        elif (not _is_voiceover) and raw_dur > 0 and seg_dur > 0:
                            ratio = raw_dur / seg_dur

                            if ratio > 1.01:
                                # Segment za długi — trzeba przyspieszyć
                                if _auto_max:
                                    # AUTO: oblicz ile miejsca mamy do następnego segmentu,
                                    # a dla ostatniego segmentu do końca filmu.
                                    next_limit = total_dur if is_last else float(tts_segments[i + 1]["start"])
                                    available_dur = next_limit - float(seg["start"]) - 0.030
                                    available_dur = max(available_dur, seg_dur)
                                    auto_ratio = raw_dur / available_dur
                                    # AUTO Max ma dopasować dokładnie do okna — bez twardego limitu,
                                    # bo limit może kończyć się uciętym słowem lub overlapem.
                                    applied = max(auto_ratio, 1.01)
                                    log_message(
                                        f"[Stretch] Seg {i+1}: AUTO ratio={auto_ratio:.2f}→applied={applied:.2f} "
                                        f"(raw={raw_dur:.2f}s, avail={available_dur:.2f}s)",
                                        log_box
                                    )
                                else:
                                    applied = min(ratio, _max_t)
                                    log_message(
                                        f"[Stretch] Seg {i+1}: ratio={ratio:.2f}→applied={applied:.2f} "
                                        f"(raw={raw_dur:.2f}s)",
                                        log_box
                                    )

                                rc2 = stretch_pitch_preserving(seg_raw, seg_fit, applied)
                                if rc2 == 0 and seg_fit.exists() and seg_fit.stat().st_size > 512:
                                    final_f = seg_fit

                            elif ratio < _min_t and _min_t < 1.0:
                                # Segment za krótki — zwolnij (tylko gdy nie AUTO min)
                                applied = max(ratio, _min_t)
                                rc2 = stretch_pitch_preserving(seg_raw, seg_fit, applied)
                                if rc2 == 0 and seg_fit.exists() and seg_fit.stat().st_size > 512:
                                    final_f = seg_fit

                        stretched_paths.append(final_f)

                    # ── KROK 4: ZERO-DRIFT ASSEMBLY ──────────
                    _prog(0.70, text=f"[4/{N_STEPS}] Zero-drift assembly...")
                    status.update(label=f"[4/{N_STEPS}] Zero-Drift — układanie na osi czasu...")
                    log_message("[4/6] Zero-Drift Assembly — każdy segment na dokładną pozycję...", log_box, True)

                    total_dur = get_audio_duration(input_path)
                    ok = assemble_zero_drift(
                        tts_segments[:num_items],
                        stretched_paths,
                        total_dur,
                        dubbed_audio,
                        log_area=log_box
                    )
                    if not ok:
                        log_message("❌ Błąd assembly — brak pliku wyjściowego.", log_box, True)
                        status.update(label="❌ Błąd składania audio.", state="error")
                        st.session_state.is_generating = False
                        st.rerun()

                    log_message("[4/6] Zero-Drift Assembly gotowy.", log_box, True)

                    # ── NORMALIZACJA DŁUGOŚCI AUDIO DO DOKŁADNEJ DŁUGOŚCI WIDEO ──
                    # dubbed_audio może być dłuższy (ostatni segment rozszerzył bufor)
                    # lub krótszy — normalizujemy do total_dur co do próbki
                    dubbed_audio_dur = get_audio_duration(dubbed_audio)
                    if abs(dubbed_audio_dur - total_dur) > 0.001:
                        dubbed_norm = OUTPUT_DIR / f"{filename}_dubbed_norm.wav"
                        cmd_norm = [
                            "ffmpeg", "-y",
                            "-i", str(dubbed_audio),
                            # apad dopełnia ciszą jeśli za krótki, atrim przycina jeśli za długi
                            "-af", f"apad=pad_dur={total_dur:.6f},atrim=0:{total_dur:.6f}",
                            "-ar", str(TARGET_SR), "-ac", str(TARGET_CH),
                            "-sample_fmt", "s16",
                            str(dubbed_norm)
                        ]
                        ret_norm = subprocess.run(
                            cmd_norm, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        if ret_norm.returncode == 0 and dubbed_norm.exists() and dubbed_norm.stat().st_size > 512:
                            dubbed_norm.replace(dubbed_audio)
                            log_message(
                                f"[4/6] Audio znormalizowane: {dubbed_audio_dur:.3f}s → {total_dur:.3f}s",
                                log_box, True
                            )
                        else:
                            log_message("[4/6] Normalizacja długości nieudana — używam oryginału.", log_box)

                    # Sprzątanie plików tymczasowych segmentów
                    for i in range(num_items):
                        for suf in ["_raw.wav", "_fit.wav"]:
                            p = OUTPUT_DIR / f"{filename}_seg_{i}{suf}"
                            try: p.unlink(missing_ok=True)
                            except Exception: pass

                else:
                    # Fallback bez synchronizacji per-segment
                    _prog(0.18, text=f"[2/{N_STEPS}] TTS bez synchronizacji...")
                    status.update(label=f"[2/{N_STEPS}] TTS — generowanie całości...")
                    all_text  = "\n".join(line for line in dubbed_lines if line.strip())
                    tmp_paths = [OUTPUT_DIR / f"{filename}_full_0.wav"]
                    if _use_system_voiceover:
                        batch_rc = generate_system_voiceover_batch(
                            [all_text],
                            st.session_state.target_lang,
                            tmp_paths,
                            log_area=log_box,
                        )
                    elif use_qwen_preset_voice:
                        batch_rc = generate_qwen3_preset_tts_batch(
                            [all_text], st.session_state.target_lang,
                            st.session_state.get("dubbing_qwen_speaker", "Ryan"),
                            tmp_paths,
                            log_area=log_box,
                            style_text=tts_style_text,
                            temperature_override=0.60 if _is_voiceover else 0.7,
                            top_p_override=0.82 if _is_voiceover else 0.85,
                            strict_timing=_is_voiceover,
                        )
                    else:
                        batch_rc = generate_qwen3_tts_batch(
                            [all_text], st.session_state.target_lang,
                            tts_ref_audio, tmp_paths,
                            log_area=log_box,
                            ref_cache_dir=ref_cache_dir,
                            style_text=tts_style_text,
                        )
                    if batch_rc == 0 and tmp_paths[0].exists():
                        shutil.copy2(tmp_paths[0], dubbed_audio)
                        try: tmp_paths[0].unlink(missing_ok=True)
                        except Exception: pass
                    else:
                        status.update(label="❌ Błąd TTS.", state="error")
                        st.session_state.is_generating = False
                        st.rerun()
                    _prog(0.70, text=f"[2-4/{N_STEPS}] TTS gotowy (bez per-segment sync).")

                # ── KROK 5: PITCH SHIFT ──────────────────────
                if pitch_adj != 0.0 and dubbed_audio.exists():
                    _prog(0.77, text=f"[5/{N_STEPS}] Pitch shift ({pitch_adj:+.1f} st.)...")
                    status.update(label=f"[5/{N_STEPS}] Pitch Shift — korekta tonu głosu...")
                    try:
                        import torchaudio
                        waveform, sr2 = torchaudio.load(str(dubbed_audio))
                        shifted = torchaudio.functional.pitch_shift(waveform, sr2, pitch_adj)
                        torchaudio.save(str(dubbed_audio), shifted, sr2)
                        del waveform, shifted
                        gc.collect()
                        log_message(f"[5/6] Pitch shift: {pitch_adj:+.1f} półtonów.", log_box)
                    except Exception as e:
                        log_message(f"[5/6] Pitch shift nieudany: {e}", log_box)

                _prog(0.82, text=f"[5/{N_STEPS}] Gotowe.")

                # ── KROK 6: RENDER FINALNY ───────────────────
                _prog(0.0, text=f"[6/{N_STEPS}] Renderowanie finalnego pliku...")
                status.update(label=f"[6/{N_STEPS}] FFmpeg — finalny render...")
                log_message("[6/6] Składanie finalnego pliku...", log_box, True)

                lang_sfx     = st.session_state.target_lang.upper().replace(" ", "_")
                ext          = "mp4" if is_video else "wav"
                final_output = OUTPUT_DIR / f"{filename}_DUBBED_{lang_sfx}.{ext}"
                try:
                    final_output.unlink(missing_ok=True)
                except Exception:
                    pass

                def _build_filter(bg_in, dub_in, dv, bgv, av, hp, pres, lpf, amb_on):
                    if amb_on:
                        ambient_cutoff = max(int(hp), int(lpf))
                        eq = f"highpass=f={ambient_cutoff},equalizer=f=2500:t=o:w=2000:g={pres:.1f}"
                        return (
                            f"[{bg_in}:a]volume={bgv:.3f}[bg];"
                            f"[{bg_in}:a]{eq},volume={av:.3f}[amb];"
                            f"[{dub_in}:a]volume={dv:.3f}[dub];"
                            "[bg][amb][dub]amix=inputs=3:duration=longest:normalize=0[a]"
                        )
                    else:
                        return (
                            f"[{bg_in}:a]volume={bgv:.3f}[bg];"
                            f"[{dub_in}:a]volume={dv:.3f}[dub];"
                            "[bg][dub]amix=inputs=2:duration=longest:normalize=0[a]"
                        )

                def _build_voiceover_filter(orig_in, dub_in, dv, origv, duck_on, duck_amount):
                    if duck_on and duck_amount > 0.01:
                        ratio = 1.5 + max(0.0, min(1.0, duck_amount)) * 14.0
                        threshold = 0.035 - max(0.0, min(1.0, duck_amount)) * 0.020
                        return (
                            f"[{orig_in}:a]volume={origv:.3f}[orig];"
                            f"[{dub_in}:a]volume={dv:.3f},asplit=2[dubmix][dubsc];"
                            f"[orig][dubsc]sidechaincompress=threshold={threshold:.4f}:"
                            f"ratio={ratio:.2f}:attack=20:release=650[origduck];"
                            "[origduck][dubmix]amix=inputs=2:duration=longest:normalize=0[a]"
                        )
                    return (
                        f"[{orig_in}:a]volume={origv:.3f}[orig];"
                        f"[{dub_in}:a]volume={dv:.3f}[dub];"
                        "[orig][dub]amix=inputs=2:duration=longest:normalize=0[a]"
                    )

                # ── Parametry wyjścia wideo ───────────────────
                _out_res  = st.session_state.get("output_resolution", "Auto (jak oryginał)")
                _out_mbps = float(st.session_state.get("output_bitrate_mbps", 5.0))
                _out_bps  = f"{int(_out_mbps * 1000)}k"   # np. "5000k"

                # Filtr skalowania — tylko gdy nie Auto
                _res_map = {
                    "480p":      "scale=-2:480",
                    "720p":      "scale=-2:720",
                    "1080p":     "scale=-2:1080",
                    "4K (2160p)":"scale=-2:2160",
                }
                _scale_filter = _res_map.get(_out_res, None)

                def _build_vfilter(scale_f):
                    """Zwraca -vf arg jeśli potrzebne skalowanie, inaczej pusty."""
                    if scale_f:
                        return ["-vf", scale_f]
                    return []

                log_message(
                    f"[6/6] Render: rozdzielczość={_out_res}, bitrate={_out_mbps:.0f} Mbps",
                    log_box, True
                )
                log_message(f"[6/6] Tryb miksu: {_mix_mode}", log_box, True)

                def _fmt_elapsed(seconds):
                    seconds = max(0, int(seconds or 0))
                    m, s = divmod(seconds, 60)
                    h, m = divmod(m, 60)
                    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

                def _ffmpeg_progress(start_ratio, end_ratio, label):
                    last_update = {"t": 0.0}

                    def _on_line(line):
                        ff_time = parse_ffmpeg_time_seconds(line)
                        if ff_time is None or total_dur <= 0:
                            return
                        now = time.time()
                        if now - last_update["t"] < 0.45 and ff_time < total_dur:
                            return
                        last_update["t"] = now
                        local_ratio = max(0.0, min(ff_time / total_dur, 1.0))
                        ratio = start_ratio + (end_ratio - start_ratio) * local_ratio
                        _prog(
                            ratio,
                            text=(
                                f"[6/{N_STEPS}] {label} — "
                                f"{_fmt_elapsed(ff_time)} / {_fmt_elapsed(total_dur)} "
                                f"(zostało {_fmt_elapsed(total_dur - ff_time)})"
                            )
                        )

                    return _on_line

                if is_video:
                    _vf_args = _build_vfilter(_scale_filter)
                    if _is_voiceover:
                        fc = _build_voiceover_filter(
                            0, 1, _dub_vol, _orig_vol,
                            _mix_mode == MIX_MODE_VOICEOVER_DUCK,
                            _duck_amt,
                        )
                        cmd = [
                            "ffmpeg", "-i", str(input_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc,
                            "-map", "0:v", "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            *_vf_args,
                            "-c:v", "libx264", "-preset", "fast",
                            "-b:v", _out_bps, "-maxrate", _out_bps, "-bufsize", f"{int(_out_mbps * 2000)}k",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                            "-y", str(final_output)
                        ]
                    elif st.session_state.keep_bg:
                        fc = _build_filter(1, 2, _dub_vol, _bg_vol, _amb_vol,
                                           _eq_hp, _eq_pres, _eq_lpf,
                                           st.session_state.ambient_eq_enabled)
                        cmd = [
                            "ffmpeg", "-i", str(input_path),
                            "-i", str(bg_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc,
                            "-map", "0:v", "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            *_vf_args,
                            "-c:v", "libx264", "-preset", "fast",
                            "-b:v", _out_bps, "-maxrate", _out_bps, "-bufsize", f"{int(_out_mbps * 2000)}k",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                            "-y", str(final_output)
                        ]
                    else:
                        cmd = [
                            "ffmpeg", "-i", str(input_path), "-i", str(dubbed_audio),
                            "-map", "0:v", "-map", "1:a",
                            "-t", f"{total_dur:.6f}",
                            *_vf_args,
                            "-c:v", "libx264", "-preset", "fast",
                            "-b:v", _out_bps, "-maxrate", _out_bps, "-bufsize", f"{int(_out_mbps * 2000)}k",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                            "-y", str(final_output)
                        ]
                    render_rc = run_command(
                        cmd, log_box, idle_timeout=1800,
                        line_callback=_ffmpeg_progress(0.0, 0.95, "Render wideo")
                    )
                    if render_rc != 0:
                        log_message(f"❌ FFmpeg render nieudany (kod: {render_rc}).", log_box, True)
                else:
                    if _is_voiceover:
                        fc = _build_voiceover_filter(
                            0, 1, _dub_vol, _orig_vol,
                            _mix_mode == MIX_MODE_VOICEOVER_DUCK,
                            _duck_amt,
                        )
                        cmd = [
                            "ffmpeg", "-i", str(input_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc, "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            "-y", str(final_output)
                        ]
                        render_rc = run_command(
                            cmd, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.0, 0.99, "Render audio")
                        )
                        if render_rc != 0:
                            log_message(f"❌ FFmpeg render audio nieudany (kod: {render_rc}).", log_box, True)
                    elif st.session_state.keep_bg:
                        fc = _build_filter(0, 1, _dub_vol, _bg_vol, _amb_vol,
                                           _eq_hp, _eq_pres, _eq_lpf,
                                           st.session_state.ambient_eq_enabled)
                        cmd = [
                            "ffmpeg", "-i", str(bg_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc, "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            "-y", str(final_output)
                        ]
                        render_rc = run_command(
                            cmd, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.0, 0.99, "Render audio")
                        )
                        if render_rc != 0:
                            log_message(f"❌ FFmpeg render audio nieudany (kod: {render_rc}).", log_box, True)
                    else:
                        # Samo audio — przytnij/dopełnij do dokładnej długości
                        cmd = [
                            "ffmpeg", "-y", "-i", str(dubbed_audio),
                            "-t", f"{total_dur:.6f}",
                            str(final_output)
                        ]
                        audio_rc = run_command(
                            cmd, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.0, 0.99, "Render audio")
                        )
                        if audio_rc != 0:
                            log_message(f"❌ FFmpeg audio nieudany (kod: {audio_rc}).", log_box, True)

                _prog(0.95 if is_video else 0.99, text=f"[6/{N_STEPS}] Finalizowanie plików...")
                _total = int(time.time() - _pipeline_start)
                _m, _s = divmod(_total, 60)
                # Zapamiętaj czas w session_state — przeżyje st.rerun()
                st.session_state["last_gen_time"] = f"{_m:02d}:{_s:02d}"

                # ── Zawsze zapisz zmixowane audio WAV (identyczne jak ścieżka w filmie) ──
                lang_sfx_audio = st.session_state.target_lang.upper().replace(" ", "_")
                audio_output = OUTPUT_DIR / f"{filename}_DUBBED_{lang_sfx_audio}.wav"
                try:
                    audio_output.unlink(missing_ok=True)
                except Exception:
                    pass

                if is_video:
                    # Dla wideo: wyrenderuj osobne WAV z tym samym mikserem co film
                    if _is_voiceover:
                        _prog(0.95, text=f"[6/{N_STEPS}] Renderowanie pliku audio WAV...")
                        log_message("[6/6] Renderowanie pliku audio WAV (miks lektora)...", log_box, True)
                        fc_audio = _build_voiceover_filter(
                            0, 1, _dub_vol, _orig_vol,
                            _mix_mode == MIX_MODE_VOICEOVER_DUCK,
                            _duck_amt,
                        )
                        cmd_audio = [
                            "ffmpeg", "-i", str(input_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc_audio, "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            "-c:a", "pcm_s16le", "-ar", "48000",
                            "-y", str(audio_output)
                        ]
                        audio_render_rc = run_command(
                            cmd_audio, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.95, 0.99, "Render WAV")
                        )
                        if audio_render_rc != 0:
                            log_message(f"❌ Render WAV nieudany (kod: {audio_render_rc}).", log_box, True)
                    elif st.session_state.keep_bg and bg_path.exists():
                        _prog(0.95, text=f"[6/{N_STEPS}] Renderowanie pliku audio WAV...")
                        log_message("[6/6] Renderowanie pliku audio WAV (miks)...", log_box, True)
                        fc_audio = _build_filter(0, 1, _dub_vol, _bg_vol, _amb_vol,
                                                 _eq_hp, _eq_pres, _eq_lpf,
                                                 st.session_state.ambient_eq_enabled)
                        cmd_audio = [
                            "ffmpeg", "-i", str(bg_path), "-i", str(dubbed_audio),
                            "-filter_complex", fc_audio, "-map", "[a]",
                            "-t", f"{total_dur:.6f}",
                            "-c:a", "pcm_s16le", "-ar", "48000",
                            "-y", str(audio_output)
                        ]
                        audio_render_rc = run_command(
                            cmd_audio, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.95, 0.99, "Render WAV")
                        )
                        if audio_render_rc != 0:
                            log_message(f"❌ Render WAV nieudany (kod: {audio_render_rc}).", log_box, True)
                    else:
                        _prog(0.95, text=f"[6/{N_STEPS}] Renderowanie pliku audio WAV...")
                        # Brak tła — samo TTS, przytnij do dokładnej długości
                        cmd_audio = [
                            "ffmpeg", "-y", "-i", str(dubbed_audio),
                            "-t", f"{total_dur:.6f}",
                            "-c:a", "pcm_s16le", "-ar", "48000",
                            str(audio_output)
                        ]
                        audio_render_rc = run_command(
                            cmd_audio, log_box, idle_timeout=1800,
                            line_callback=_ffmpeg_progress(0.95, 0.99, "Render WAV")
                        )
                        if audio_render_rc != 0:
                            log_message(f"❌ Render WAV nieudany (kod: {audio_render_rc}).", log_box, True)
                else:
                    # Dla audio: final_output to już zmixowany plik — skopiuj jako WAV
                    if final_output.exists() and str(final_output) != str(audio_output):
                        shutil.copy2(final_output, audio_output)

                # ── Napisy YouTube: SubRip (.srt), UTF-8, jeden wpis na segment Whisper ──
                subtitle_output = OUTPUT_DIR / f"{filename}_DUBBED_{lang_sfx_audio}.srt"
                try:
                    subtitle_output.unlink(missing_ok=True)
                except Exception:
                    pass
                if segments:
                    subtitle_lines = fit_lines_to_segments(dub_text_for_tts, segments)
                    if write_youtube_srt(segments, subtitle_lines, subtitle_output):
                        log_message(
                            f"[6/6] Napisy SRT zapisane: {subtitle_output.name}",
                            log_box, True
                        )
                    else:
                        log_message("[6/6] Nie udało się zapisać napisów SRT.", log_box, True)
                else:
                    log_message("[6/6] Brak segmentów czasowych — pomijam zapis napisów.", log_box, True)

                if final_output.exists() and final_output.stat().st_size > 1024:
                    st.session_state.final_output_path      = str(final_output)
                    st.session_state.final_output_is_video  = is_video
                    st.session_state.final_audio_path       = str(audio_output) if audio_output.exists() else None
                    st.session_state.final_subtitle_path    = str(subtitle_output) if subtitle_output.exists() else None
                    status.update(label="✅ Dubbing gotowy.", state="complete")
                    log_box.empty()
                    prog.empty()
                else:
                    status.update(label="❌ Finalny plik nie powstał. Sprawdź logi.", state="error")

            except Exception as ex:
                import traceback
                log_message(f"[BŁĄD KRYTYCZNY] {ex}\n{traceback.format_exc()}", log_box, True)
                status_ph.error(f"Nieoczekiwany błąd: {ex}")
            finally:
                _g_timer_ph = None
                st.session_state.is_generating = False
                st.session_state.pop("pipeline_start_time", None)
                # FIX: jawne sprzątanie RAM po zakończeniu pipeline
                cleanup_mps()
                gc.collect()
                st.rerun()

    if st.session_state.full_logs:
        log_message("", log_box, force_update=True)

    # Ukryj log box jeśli użytkownik wyłączył logi
    if not st.session_state.get("show_log", True):
        log_box.empty()

    # ── WYNIK ─────────────────────────────────────────────────
    if input_path:
        _res_filename = input_path.stem
        _res_is_video = input_path.suffix.lower()[1:] in ["mp4", "mov", "avi", "mkv"]
        _res_lang_sfx = st.session_state.target_lang.upper().replace(" ", "_")
        _res_ext = "mp4" if _res_is_video else "wav"
        _expected_fp = OUTPUT_DIR / f"{_res_filename}_DUBBED_{_res_lang_sfx}.{_res_ext}"
        _expected_ap = OUTPUT_DIR / f"{_res_filename}_DUBBED_{_res_lang_sfx}.wav"
        _expected_sp = OUTPUT_DIR / f"{_res_filename}_DUBBED_{_res_lang_sfx}.srt"
        if _expected_fp.exists() and not _expected_sp.exists() and st.session_state.get("dub_edit", "").strip():
            try:
                _subtitle_segments = load_cache(str(input_path)).get("segments", [])
                if _subtitle_segments:
                    _subtitle_lines = fit_lines_to_segments(st.session_state.dub_edit, _subtitle_segments)
                    write_youtube_srt(_subtitle_segments, _subtitle_lines, _expected_sp)
            except Exception:
                pass
        _current_fp = Path(st.session_state.final_output_path) if st.session_state.get("final_output_path") else None
        if _expected_fp.exists() and (_current_fp is None or _current_fp != _expected_fp):
            st.session_state.final_output_path = str(_expected_fp)
            st.session_state.final_output_is_video = _res_is_video
            st.session_state.final_audio_path = str(_expected_ap) if _expected_ap.exists() else None
            st.session_state.final_subtitle_path = str(_expected_sp) if _expected_sp.exists() else None
        elif _current_fp and _current_fp.exists() and _res_lang_sfx not in _current_fp.stem:
            st.session_state.final_output_path = None
            st.session_state.final_audio_path = None
            st.session_state.final_subtitle_path = None

    if st.session_state.get("final_output_path"):
        fp = Path(st.session_state.final_output_path)
        ap = Path(st.session_state.get("final_audio_path", "")) if st.session_state.get("final_audio_path") else None
        sp = Path(st.session_state.get("final_subtitle_path", "")) if st.session_state.get("final_subtitle_path") else None

        if fp.exists():
            fp_url = media_file_url(fp)
            ap_url = media_file_url(ap) if ap and ap.exists() else None
            sp_url = media_file_url(sp) if sp and sp.exists() else None
            fp_download_url = media_file_url(fp, download=True)
            ap_download_url = media_file_url(ap, download=True) if ap and ap.exists() else None
            sp_download_url = media_file_url(sp, download=True) if sp and sp.exists() else None
            if st.session_state.get("final_output_is_video", True):
                if fp_url:
                    st.markdown(
                        f"""
                        <video controls preload="metadata"
                               style="width:100%; max-height:70vh; background:#000; display:block;">
                            <source src="{html.escape(fp_url, quote=True)}" type="video/mp4">
                        </video>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.video(str(fp))
            else:
                if fp_url:
                    st.markdown(
                        f"""
                        <audio controls preload="metadata" style="width:100%;">
                            <source src="{html.escape(fp_url, quote=True)}" type="audio/wav">
                        </audio>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.audio(str(fp))

            # Przyciski pobierania
            is_vid = st.session_state.get("final_output_is_video", True)
            if fp_download_url or ap_download_url or sp_download_url:
                st.markdown(
                    '<iframe name="dm_download_frame" style="display:none;width:0;height:0;border:0;"></iframe>',
                    unsafe_allow_html=True,
                )
            if is_vid:
                dl_col1, dl_col2, dl_col3 = st.columns(3)
                with dl_col1:
                    if fp_download_url:
                        st.markdown(
                            f"""
                            <form class="dm-download-form" action="{html.escape(fp_download_url, quote=True)}"
                                  method="get" target="dm_download_frame">
                                <button class="dm-download-btn" type="submit">💾 Pobierz wideo</button>
                            </form>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        with open(fp, "rb") as _f:
                            st.download_button(
                                "💾 Pobierz wideo",
                                data=_f, file_name=fp.name,
                                use_container_width=True,
                                key="dl_video"
                            )
                with dl_col2:
                    if ap and ap.exists():
                        if ap_download_url:
                            st.markdown(
                                f"""
                                <form class="dm-download-form" action="{html.escape(ap_download_url, quote=True)}"
                                      method="get" target="dm_download_frame">
                                    <button class="dm-download-btn" type="submit">🎵 Pobierz audio</button>
                                </form>
                                """,
                                unsafe_allow_html=True,
                            )
                        else:
                            with open(ap, "rb") as _f:
                                st.download_button(
                                    "🎵 Pobierz audio",
                                    data=_f, file_name=ap.name,
                                    use_container_width=True,
                                    key="dl_audio"
                                )
                with dl_col3:
                    if sp and sp.exists():
                        if sp_download_url:
                            st.markdown(
                                f"""
                                <form class="dm-download-form" action="{html.escape(sp_download_url, quote=True)}"
                                      method="get" target="dm_download_frame">
                                    <button class="dm-download-btn" type="submit">📝 Pobierz napisy</button>
                                </form>
                                """,
                                unsafe_allow_html=True,
                            )
                        else:
                            with open(sp, "rb") as _f:
                                st.download_button(
                                    "📝 Pobierz napisy",
                                    data=_f, file_name=sp.name,
                                    mime="application/x-subrip",
                                    use_container_width=True,
                                    key="dl_subtitles"
                                )
            else:
                dl_col1, dl_col2 = st.columns(2) if sp and sp.exists() else (st.container(), None)
                with dl_col1:
                    if fp_download_url:
                        st.markdown(
                            f"""
                            <form class="dm-download-form" action="{html.escape(fp_download_url, quote=True)}"
                                  method="get" target="dm_download_frame">
                                <button class="dm-download-btn" type="submit">🎵 Pobierz audio</button>
                            </form>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        with open(fp, "rb") as _f:
                            st.download_button(
                                "🎵 Pobierz audio",
                                data=_f, file_name=fp.name,
                                use_container_width=True,
                                key="dl_audio_only"
                            )
                if sp and sp.exists() and dl_col2 is not None:
                    with dl_col2:
                        if sp_download_url:
                            st.markdown(
                                f"""
                                <form class="dm-download-form" action="{html.escape(sp_download_url, quote=True)}"
                                      method="get" target="dm_download_frame">
                                    <button class="dm-download-btn" type="submit">📝 Pobierz napisy</button>
                                </form>
                                """,
                                unsafe_allow_html=True,
                            )
                        else:
                            with open(sp, "rb") as _f:
                                st.download_button(
                                    "📝 Pobierz napisy",
                                    data=_f, file_name=sp.name,
                                    mime="application/x-subrip",
                                    use_container_width=True,
                                    key="dl_subtitles_audio"
                                )
