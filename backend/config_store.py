"""Unified, persisted settings for DubCut Studio.

Mirrors the exact knobs DubMaster (config.json) and ShortsGenerator (workspace/settings.json)
expose, but in one native store the desktop UI binds to. Persisted as JSON under the data dir
provided by Electron (DUBCUT_DATA_DIR) so it survives restarts.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

_LOCK = threading.Lock()

DEFAULT_GLOSSARY = (
    "Humsieng -> Humsienk\n"
    "Humsienka -> Humsienk\n"
    "Humsinga -> Humsienk\n"
    "Humsięk -> Humsienk\n"
    "Humsing -> Humsienk\n"
    "Humsienk\n"
    "Amfropik -> Anthropic\n"
    "Amthropic -> Anthropic\n"
    "Amphropic -> Anthropic\n"
    "Anthropic\n"
    "Open AI -> OpenAI\n"
    "OpenAI\n"
    "Chat GPT -> ChatGPT\n"
    "ChatGPT\n"
    "Opitkowanie -> Opitkovanie\n"
    "Opitkovanie\n"
    "All Powers -> AllPowers\n"
    "AllPowers\n"
    "Claude Code\n"
    "Codex\n"
    "Gemini\n"
    "Whisper\n"
    "Qwen\n"
    "LiFePO4\n"
    "WattCycle\n"
    "LiThink\n"
    "Leething -> LiThink\n"
    "Leeting -> LiThink\n"
    "Litinka -> LiThink\n"
    "kodeksa -> Codex\n"
    "klotkot -> Claude Code\n"
    "Explorovanie\n"
    "Caravaning Ireland"
)


def data_dir() -> Path:
    """Config root on the SYSTEM disk (userData). Holds the things that must survive app
    updates and must never live on a removable/cleanable work disk: settings.json,
    voice_labels.json, and imported assets. Never moved by the user."""
    d = Path(os.environ.get("DUBCUT_DATA_DIR", Path.home() / ".dubcut-studio"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "settings.json"


# Module → work-folder name. Each is a self-contained, deletable category folder; the app
# recreates it on demand. Order/labels also drive the Files & Memory view.
WORK_CATEGORIES = [
    ("music", "Muzyka"),
    ("image", "Obraz"),
    ("video", "Wideo"),
    ("dub", "Dubbing i napisy"),
    ("tts", "Tekst do audio"),
    ("shorts", "Shorts"),
    ("cache", "Cache (podglądy, logi)"),
]
_WORK_DIRNAME = {key: name for key, name in WORK_CATEGORIES}


def work_root() -> Path:
    """User-chosen working root for ALL generated / downloaded / temporary files. Defaults
    to a `work` folder under the config root, but the user can point it at an external disk.
    Anything here is disposable: delete a category folder and the app recreates it."""
    try:
        wd = str(load().get("app", {}).get("work_dir") or "").strip()
    except Exception:
        wd = ""
    if wd:
        p = Path(wd)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass  # fall back to the default if the external disk is gone
    p = data_dir() / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def module_dir(key: str) -> Path:
    """Per-module category folder under the work root (created on demand)."""
    name = _WORK_DIRNAME.get(key, key)
    p = work_root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- Defaults: faithful to DubMaster + ShortsGenerator ----------------------
DEFAULTS: Dict[str, Any] = {
    "dub": {
        # source
        "input_method": "Lokalny plik",
        "yt_quality": "1080p",
        # language
        "source_lang": "Automatyczne wykrywanie",
        "target_lang": "Niemiecki",
        # voice
        "voice_source": "Głos z oryginalnego filmu",
        "voice_store_mode": "Próbki własne",
        "selected_voice_id": "",
        "dubbing_qwen_speaker": "Aiden",
        "text_tts_speaker": "Ryan",
        "ref_audio_length": 12,
        "clone_mode": "Strict Voice Clone (stabilniejsza barwa głosu)",
        # Reference voice for "Głos z oryginalnego filmu": "filtered" (czysty głos bez
        # pogłosu/otoczenia) | "ambient" (oryginał z tłem). Mirror in shorts (dub_voice_ref).
        "voice_ref": "filtered",
        "tts_model": "1.7B (Wysoka jakość)",
        # OmniVoice engine knobs (used when app.tts_engine == "omnivoice").
        "omnivoice_num_step": 32,
        "omnivoice_guidance_scale": 2.0,
        "omnivoice_speed": 1.0,
        "omnivoice_class_temperature": 0.0,
        "omnivoice_gender": "",
        "omnivoice_age": "",
        "omnivoice_pitch": "",
        "omnivoice_whisper": False,
        "use_fp16": False,
        "whisper_precise": True,
        # mix + sync
        "mix_mode": "Czysty dubbing (usuń oryginalny głos)",
        "voiceover_tts_engine": "Qwen TTS (eksperymentalny, naturalniejszy)",
        "voiceover_style": "",
        "dub_vol": 1.5,
        "bg_music_vol": 1.4,
        "voiceover_original_vol": 0.85,
        "voiceover_duck_amount": 0.95,
        "auto_sync": True,
        "auto_min_tempo": False,
        "auto_max_tempo": True,
        "sync_min_tempo": 0.9,
        "sync_max_tempo": 1.5,
        "pitch_adj": 0.0,
        # background + ambient EQ
        "keep_bg": True,
        "demucs_shifts": 2,
        "ambient_vol": 0.0,
        "ambient_eq_enabled": False,
        "ambient_eq_hp": 200,
        "ambient_eq_presence": 4.0,
        "ambient_eq_lpf_speech": 3500,
        # translation + glossary
        "translation_model": "Gemini 2.5 Flash (Lokalizacja 2-Etapowa)",
        "proper_name_glossary": "",
        # output video
        "output_format_pref": "video",
        "output_resolution": "Auto (jak oryginał)",
        "output_bitrate_mbps": 5.0,
        # subtitles export
        "subtitle_target_langs": ["Niemiecki"],
        "subtitle_formats": ["SRT"],
        "subtitle_include_original": False,
    },
    "shorts": {
        # source
        "input_method": "Lokalny plik",
        "yt_quality": "1080p",
        "use_yt_subs": False,
        "whisper_lang": "Auto-detekcja",
        "target_lang": "Brak (Oryginał)",
        # AI
        "shorts_count": 10,
        "duration_min": 45,
        "duration_max": 90,
        "prompt_mode": "Precyzyjna (Domyślna - bardziej restrykcyjna)",
        "custom_prompt_text": "",
        "whisper_glossary": "",
        # format / frame
        "aspect_ratio": "9:16",
        "fill_mode": 100,
        "blur_bg": False,
        "blur_sigma": 35,
        "blur_zoom": 1.1,
        "blur_bright": 60,
        # export
        "export_resolution": "1080p",
        "export_codec": "H.264 (Większa kompatybilność)",
        "export_bitrate": 15,
        "use_proxy": True,
        "proxy_res": "1080p",
        "proxy_bitrate": 15,
        # audio / dubbing (faithful to ShortsGenerator dubbing_engine defaults)
        "audio_mode": "Czysty dubbing (usuń oryginalny głos)",
        "dub_target_lang": "Angielski",
        "dub_auto_subtitles": True,
        "dub_keep_background": True,
        "dub_voice_source": "Głos z oryginalnego filmu",
        "dub_voice_ref": "filtered",  # "filtered" (czysty) | "ambient" (oryginał z tłem)
        "dub_selected_voice_path": "",
        "dub_qwen_speaker": "Aiden",
        "omnivoice_num_step": 32,
        "omnivoice_guidance_scale": 2.0,
        "omnivoice_speed": 1.0,
        "omnivoice_class_temperature": 0.0,
        "omnivoice_gender": "",
        "omnivoice_age": "",
        "omnivoice_pitch": "",
        "omnivoice_whisper": False,
        "dub_ref_audio_length": 12,
        "dub_sync_min_tempo": 0.90,
        "dub_sync_max_tempo": 1.00,
        "dub_auto_min_tempo": False,
        "dub_auto_max_tempo": True,
        "dub_original_volume": 0.85,
        "dub_background_volume": 1.40,
        "dub_voice_volume": 1.50,
        "dub_duck_amount": 0.95,
        "dub_pitch_adjust": 0.0,
        "dub_style_prompt": "",
        # face tracking
        "face_tracking": False,
        "ft_strategy": "Główny mówca (Skupia na największej twarzy)",
        "ft_tracker": "Auto (sam dobiera)",
        "smart_reframe": False,
        "reframe_speed": 50,
        "ft_zoom": 1.0,
        "ft_y_offset": 0,
        "ft_smoothness": 60,
        "ft_recheck": 8,
        # AI-camera background blur — only applies on ZOOM-OUT frames (Smart Reframing on).
        # Reuses the blur_sigma / blur_zoom / blur_bright values from "Format i kadr".
        "cam_blur_bg": False,
        # subtitles
        "enable_subtitles": True,
        "sub_preset": "MrBeast Clean Hook",
        "custom_font": "Domyślna dla presetu",
        "sub_bcolor": "#FFFFFF",
        "sub_hcolor": "#FFD700",
        "sub_out_color": "#000000",
        "sub_shad_color": "#000000",
        "sub_mode": "highlight",
        "sub_words": 3,
        "sub_animation": "Brak",
        "sub_size": 32,
        "sub_hsize": 38,
        "sub_out_thick": 3,
        "sub_shad_size": 2,
        "sub_margin": 600,
        "sub_bg_pad": 45,
        "sub_bold": True,
        "sub_italic": False,
        "sub_upper": True,
        "sub_punct": True,
        "sub_autoscale": False,
        # logo
        "enable_logo": True,
        "logo_path": "workspace/logo.png",
        "logo_scale": 45,
        "logo_x": 2,
        "logo_y": 4,
        "logo_opacity": 100,
        # watermark text
        "enable_text": True,
        "wm_text": "SUBSCRIBE",
        "wm_font": "funzone-two-serif-bold.ttf",
        "wm_size": 65,
        "wm_color": "#ffe900",
        "wm_x": 4,
        "wm_y": 20,
        "wm_opacity": 100,
        "wm_out_color": "#000000",
        "wm_out_thick": 10,
        "wm_shad_color": "#ef0808",
        "wm_shad_size": 10,
        "wm_bold": True,
        "wm_italic": False,
    },
    "music": {
        "title": "Nowy utwór",
        "prompt": "energetic indie pop, warm live drums, bright guitars, emotional male vocal, polished radio mix",
        "lyrics": (
            "[Verse]\n"
            "Piszę melodię w ciszy dnia\n"
            "Miasto oddycha, rytm już zna\n\n"
            "[Chorus]\n"
            "Niech ten dźwięk prowadzi nas\n"
            "Przez zielony, nocny blask"
        ),
        "model": "acestep-v15-turbo",
        "language": "unknown",
        "duration": 120,
        "audio_format": "mp3",
        "bpm_choice": "auto",
        "key_scale_choice": "auto",
        "time_signature_choice": "auto",
        "seed": "",
        "vocal_type": "male",
        "variant_count": 2,
        "inference_steps": 8,
        "guidance_scale": 7.0,
        "instrumental": False,
        "thinking": True,
        # Free the model from RAM after each generation (keeps the rest of the app light).
        "auto_unload": True,
    },
    "image": {
        "model": "dhairyashil/FLUX.1-schnell-mflux-4bit",
        "model_label": "FLUX.1 Schnell MFLUX Q4 - publiczny",
        "format": "Square",
        "resolution_label": "1024 x 1024 - standard",
        "width": 1024,
        "height": 1024,
        "steps": 4,
        "guidance_enabled": False,
        "guidance": None,
        "low_ram": True,
        "mlx_cache_limit_gb": 12,
        "seed": 42,
        "seed_random": True,
        "batch_count": 1,
        "style_label": "Bez stylu",
        "style_suffix": "",
        "prompt": "",
        "negative_prompt": "",
        "image_to_image": False,
        "image_path": "",
        "image_strength": 0.6,
    },
    "video": {
        "model_label": "LTX 2.3 Q4 - stabilny dla 24 GB",
        "model": "",
        "mode": "Text to video",
        "resolution_label": "Fast preview 512 x 320",
        "width": 512,
        "height": 320,
        "duration_label": "4 s",
        "duration": 4.0,
        "fps": 24.0,
        "steps": 8,
        "seed": 42,
        "seed_random": True,
        "prompt": "",
        "audio_enabled": True,
        "sound_prompt": "",
        "spoken_text": "",
        "image_path": "",
    },
    "app": {
        "theme": "dubcut-dark",
        "device": "auto",
        "gemini_api_key": "",
        # Path to the system-installed ACE-Step engine (vendor/ACE-Step-1.5). Empty = auto-detect.
        "ace_dir": "",
        # Path to the system-installed VideoGenerator project (image + video). Empty = auto-detect.
        "videogen_dir": "",
        # User-chosen working folder for all generated/temp files (e.g. external disk).
        # Empty = default `work` folder under the config root. Settings/imports stay system-side.
        "work_dir": "",
        # Shorts translation engine: "nllb" (local, best) | "argos" (local, light) | "gemini" (cloud).
        # Default is the LOCAL engine to match the Settings promise ("domyślnie w pełni
        # lokalnie — bez kosztów i bez klucza API"). A user who prefers Gemini sets it
        # explicitly (their saved settings.json overrides this default). NOTE: automatic
        # SHORT scene-generation always uses Gemini regardless of this — this setting only
        # affects subtitle/metadata translation.
        "translation_engine": "nllb",
        # Text→Audio / dubbing voice engine: "qwen" (Qwen3-TTS, 10 languages, preset
        # speakers, no Polish) | "omnivoice" (k2-fsa OmniVoice — studio quality, voice
        # cloning, Polish + many more languages). OmniVoice is the default on a fresh
        # install (best quality + Polish); a user who switches to Qwen keeps that choice
        # because their saved settings.json overrides this default.
        "tts_engine": "omnivoice",
        "glossary": DEFAULT_GLOSSARY,
    },
}


def _merge_glossary_text(*values: str) -> str:
    lines = []
    seen = set()
    for value in values:
        for raw in str(value or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key = line.casefold()
            if key not in seen:
                lines.append(line)
                seen.add(key)
    return "\n".join(lines)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    with _LOCK:
        try:
            raw = json.loads(config_path().read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        merged = _deep_merge(DEFAULTS, raw)
        merged["app"]["glossary"] = _merge_glossary_text(DEFAULT_GLOSSARY, merged.get("app", {}).get("glossary", ""))
        return merged


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `patch` into the current config and persist atomically."""
    with _LOCK:
        try:
            current = json.loads(config_path().read_text(encoding="utf-8"))
        except Exception:
            current = {}
        merged = _deep_merge(_deep_merge(DEFAULTS, current), patch)
        # Persist only the merged result (defaults included is fine and keeps it self-describing)
        tmp = config_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, config_path())
        return merged
