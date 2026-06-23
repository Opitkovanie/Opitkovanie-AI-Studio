import streamlit as st
from config import SUBTITLE_PRESETS, REVERSE_ANIMATION_MAP
from utils import load_settings, save_settings
from ai_processor import DEFAULT_WHISPER_GLOSSARY
from dubbing_engine import default_dubbing_settings


AUDIO_UI_TO_SETTING = {
    "ui_dub_target_lang_key": "dub_target_lang",
    "ui_dub_auto_subtitles_key": "dub_auto_subtitles",
    "ui_dub_keep_background_key": "dub_keep_background",
    "ui_dub_voice_source_key": "dub_voice_source",
    "ui_dub_selected_voice_path_key": "dub_selected_voice_path",
    "ui_dub_qwen_speaker_key": "dub_qwen_speaker",
    "ui_dub_ref_audio_length_key": "dub_ref_audio_length",
    "ui_dub_sync_min_tempo_key": "dub_sync_min_tempo",
    "ui_dub_sync_max_tempo_key": "dub_sync_max_tempo",
    "ui_dub_auto_min_tempo_key": "dub_auto_min_tempo",
    "ui_dub_auto_max_tempo_key": "dub_auto_max_tempo",
    "ui_dub_original_volume_key": "dub_original_volume",
    "ui_dub_background_volume_key": "dub_background_volume",
    "ui_dub_voice_volume_key": "dub_voice_volume",
    "ui_dub_duck_amount_key": "dub_duck_amount",
    "ui_dub_pitch_adjust_key": "dub_pitch_adjust",
    "ui_dub_style_prompt_key": "dub_style_prompt",
}


def _audio_defaults_from_settings(settings=None):
    s = settings or load_settings()
    dub_defaults = default_dubbing_settings()
    return {
        "ui_audio_mode_key": s.get("audio_mode", dub_defaults["audio_mode"]),
        "ui_dub_target_lang_key": s.get("dub_target_lang", dub_defaults["dub_target_lang"]),
        "ui_dub_auto_subtitles_key": bool(s.get("dub_auto_subtitles", dub_defaults["dub_auto_subtitles"])),
        "ui_dub_keep_background_key": bool(s.get("dub_keep_background", dub_defaults["dub_keep_background"])),
        "ui_dub_voice_source_key": s.get("dub_voice_source", dub_defaults["dub_voice_source"]),
        "ui_dub_selected_voice_path_key": s.get("dub_selected_voice_path", dub_defaults["dub_selected_voice_path"]),
        "ui_dub_qwen_speaker_key": s.get("dub_qwen_speaker", dub_defaults["dub_qwen_speaker"]),
        "ui_dub_ref_audio_length_key": int(s.get("dub_ref_audio_length", dub_defaults["dub_ref_audio_length"])),
        "ui_dub_sync_min_tempo_key": float(s.get("dub_sync_min_tempo", dub_defaults["dub_sync_min_tempo"])),
        "ui_dub_sync_max_tempo_key": float(s.get("dub_sync_max_tempo", dub_defaults["dub_sync_max_tempo"])),
        "ui_dub_auto_min_tempo_key": bool(s.get("dub_auto_min_tempo", dub_defaults["dub_auto_min_tempo"])),
        "ui_dub_auto_max_tempo_key": bool(s.get("dub_auto_max_tempo", dub_defaults["dub_auto_max_tempo"])),
        "ui_dub_original_volume_key": float(s.get("dub_original_volume", dub_defaults["dub_original_volume"])),
        "ui_dub_background_volume_key": float(s.get("dub_background_volume", dub_defaults["dub_background_volume"])),
        "ui_dub_voice_volume_key": float(s.get("dub_voice_volume", dub_defaults["dub_voice_volume"])),
        "ui_dub_duck_amount_key": float(s.get("dub_duck_amount", dub_defaults["dub_duck_amount"])),
        "ui_dub_pitch_adjust_key": float(s.get("dub_pitch_adjust", dub_defaults["dub_pitch_adjust"])),
        "ui_dub_style_prompt_key": s.get("dub_style_prompt", dub_defaults["dub_style_prompt"]),
    }


def restore_audio_state_from_settings(repair_reset_values=False):
    """Keeps hidden Audio widgets from coming back with Streamlit minimum values."""
    saved = load_settings()
    defaults = _audio_defaults_from_settings(saved)
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if not repair_reset_values:
        return

    reset_markers = {
        "ui_dub_target_lang_key": {"Polski"},
        "ui_dub_ref_audio_length_key": {3},
        "ui_dub_voice_volume_key": {0.10},
        "ui_dub_background_volume_key": {0.0},
        "ui_dub_original_volume_key": {0.0},
        "ui_dub_sync_min_tempo_key": {0.50},
        "ui_dub_duck_amount_key": {0.0},
    }
    for key, markers in reset_markers.items():
        current = st.session_state.get(key)
        saved_value = defaults.get(key)
        if current in markers and saved_value not in markers:
            st.session_state[key] = saved_value

def normalize_blur_bright(value, default=40):
    """Zamienia stare ustawienie 0.1-1.0 na procenty suwaka 10-100."""
    try:
        val = float(value)
        if val <= 1.0:
            val *= 100
        return int(max(10, min(100, val)))
    except Exception:
        return default

def init_session_state():
    """Inicjalizuje wszystkie wartości domyślne dla aplikacji i chroni je przed utratą."""
    s = load_settings()

    if "sub_preset" not in s:
        s["sub_preset"] = "Hormozi (Classic)"
        save_settings(s)

    # Domyślny, czysty prompt dla użytkownika (W JĘZYKU ANGIELSKIM DLA LEPSZYCH REZULTATÓW). 
    # TYLKO zasady merytoryczne, zero kodu i JSONa!
    default_custom_prompt = """1. ONE CLEAR TOPIC
Each short must stay on one main topic, one idea, one story, or one emotional thread. Do not jump between unrelated topics inside the same short.

2. COHERENT EDITING
A short may contain one or many separate segments taken from different parts of the transcript, but all selected segments must belong together naturally and feel like one coherent edit.

3. FULL FILM AWARENESS
Search across the whole transcript: beginning, middle, and end. Do not focus mainly on the start of the video.

4. NATURAL FLOW
Do not cut thoughts in a way that feels broken, random, or confusing. Whenever possible, keep complete ideas, complete sentences, and a clear flow from hook to payoff.

5. VIRAL JUDGMENT
Choose the version with the strongest viral potential: curiosity, emotion, strong payoff, tension, surprise, humor, strong opinion, or useful insight.

6. EDITOR MINDSET
Think like a skilled human editor making shorts from a long video. The result must make sense, feel intentional, and be satisfying to watch."""

    # Zabezpieczenie: jeśli w zapisanym z poprzednich wersji pliku ustawień są "śmieci" (np. nawiasy od JSONa, {transcript}) 
    # LUB omyłkowa polska wersja promptu, resetujemy do czystego, prawidłowego angielskiego!
    custom_p = s.get("custom_prompt_text", default_custom_prompt)
    if "{transcript}" in custom_p or "JSON array" in custom_p or "pool_size" in custom_p or "JEDEN GŁÓWNY TEMAT" in custom_p:
        custom_p = default_custom_prompt

    # --- Podstawowe parametry aplikacji ---
    app_defaults = {
        "ui_method_key": s.get("input_method", "Link z YouTube"),
        "ui_yt_qual_key": s.get("yt_quality", "1080p"),
        "ui_ratio_key": s.get("aspect_ratio", "9:16 (Pionowy - Short/TikTok)"),
        "ui_fill_mode_key": int(s.get("fill_mode", 100)),
        "ui_res_key": s.get("export_resolution", "Zgodna ze źródłem"),
        "ui_codec_key": s.get("export_codec", "H.264 (Większa kompatybilność)"),
        "ui_bitrate_key": int(s.get("export_bitrate", 15)),
        "ui_api_key_key": s.get("api_key", ""),
        "ui_yt_subs_key": s.get("use_yt_subs", True),
        "ui_whisper_lang_key": s.get("whisper_lang", "Auto-detekcja"),
        "ui_whisper_glossary_key": s.get("whisper_glossary") or DEFAULT_WHISPER_GLOSSARY,
        "ui_target_lang_key": s.get("target_lang", "Brak (Oryginał)"),
        "ui_shorts_count_key": int(s.get("shorts_count", 3)),
        "ui_dur_key": (int(s.get("duration_min", 45)), int(s.get("duration_max", 90))),
        "ui_prompt_mode_key": s.get("prompt_mode", "Precyzyjna (Domyślna - bardziej restrykcyjna)"),
        "ui_custom_prompt_key": custom_p,
        "ui_enable_subs_key": s.get("enable_subtitles", True),
        "ui_use_proxy_key": s.get("use_proxy", False),
        "ui_proxy_res_key": s.get("proxy_res", "1080p"),
        "ui_proxy_bitrate_key": int(s.get("proxy_bitrate", 15)),
        "ui_face_tracking_key": s.get("face_tracking", True),
        "ui_ft_smoothness_key": int(s.get("ft_smoothness", 10)),
        "ui_ft_recheck_key": int(s.get("ft_recheck", 8)),
        "ui_ft_zoom_key": float(s.get("ft_zoom", 1.0)),
        "ui_ft_y_offset_key": int(s.get("ft_y_offset", 0)),
        "ui_ft_strategy_key": s.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)"),
        "preset_selector": s.get("sub_preset", "Hormozi (Classic)"),
        "custom_font_selection": s.get("custom_font", "Domyślna dla presetu"),
        "ui_blur_bg_key": s.get("blur_bg", True),
        "ui_blur_sigma_key": int(s.get("blur_sigma", 25)),
        "ui_blur_zoom_key": float(s.get("blur_zoom", 1.0)),
        "ui_blur_bright_key": normalize_blur_bright(s.get("blur_bright", 40))
    }

    for k, v in app_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # --- Parametry Napisów ---
    p_def = SUBTITLE_PRESETS.get(st.session_state["preset_selector"], SUBTITLE_PRESETS["Hormozi (Classic)"])

    def get_valid_val(key, preset_key, default_fallback):
        val = s.get(key)
        if val is None or val == "": return p_def.get(preset_key, default_fallback)
        return val

    subtitle_defaults = {
        "ui_override_bcolor_key": get_valid_val("sub_bcolor", "base_color", "#FFFFFF"),
        "ui_override_hcolor_key": get_valid_val("sub_hcolor", "highlight_color", "#00FF00"),
        "ui_override_size_key": int(get_valid_val("sub_size", "font_size", 30)),
        "ui_override_hsize_key": int(get_valid_val("sub_hsize", "highlight_size", 35)),
        "ui_override_out_color_key": get_valid_val("sub_out_color", "outline_color", "#000000"),
        "ui_override_out_thick_key": int(get_valid_val("sub_out_thick", "outline_thickness", 3)),
        "ui_override_shad_color_key": get_valid_val("sub_shad_color", "shadow_color", "#000000"),
        "ui_override_shad_size_key": int(get_valid_val("sub_shad_size", "shadow_size", 0)),
        "ui_override_bold_key": s.get("sub_bold", p_def.get("bold", True)),
        "ui_override_italic_key": s.get("sub_italic", p_def.get("italic", False)),
        "ui_override_upper_key": s.get("sub_upper", p_def.get("uppercase", True)),
        "ui_override_punct_key": s.get("sub_punct", p_def.get("remove_punctuation", True)),
        "ui_override_words_key": int(get_valid_val("sub_words", "words_per_block", 2)),
        "ui_override_mode_key": get_valid_val("sub_mode", "mode", "highlight"),
        "ui_override_animation_key": get_valid_val("sub_animation", "animation", "none"),
        "ui_override_margin_key": int(s.get("sub_margin", 600 if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else 100)),
        "ui_override_bg_pad_key": int(s.get("sub_bg_pad", p_def.get("bg_padding", 45))),
        "ui_auto_scale_key": bool(s.get("sub_autoscale", False)),
    }

    for k, v in subtitle_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # --- Parametry Logo i Znaku Wodnego ---
    logo_defaults = {
        "ui_enable_logo_key": s.get("enable_logo", False),
        "ui_logo_scale_key": s.get("logo_scale", 20),
        "ui_logo_x_key": s.get("logo_x", 50),
        "ui_logo_y_key": s.get("logo_y", 50),
        "ui_logo_opacity_key": s.get("logo_opacity", 100),
        "ui_enable_text_key": s.get("enable_text", False),
        "ui_wm_text_key": s.get("wm_text", ""),
        "ui_wm_font_key": s.get("wm_font", "Domyślna dla presetu"),
        "ui_wm_size_key": s.get("wm_size", 50),
        "ui_wm_color_key": s.get("wm_color", "#FFFFFF"),
        "ui_wm_x_key": s.get("wm_x", 50),
        "ui_wm_y_key": s.get("wm_y", 50),
        "ui_wm_opacity_key": s.get("wm_opacity", 100),
        "ui_wm_out_color_key": s.get("wm_out_color", "#000000"),
        "ui_wm_out_thick_key": int(s.get("wm_out_thick", 0)),
        "ui_wm_shad_color_key": s.get("wm_shad_color", "#000000"),
        "ui_wm_shad_size_key": int(s.get("wm_shad_size", 0)),
        "ui_wm_bold_key": bool(s.get("wm_bold", False)),
        "ui_wm_italic_key": bool(s.get("wm_italic", False))
    }

    for k, v in logo_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # --- Parametry Audio / Dubbingu ---
    audio_defaults = _audio_defaults_from_settings(s)

    for k, v in audio_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    st.session_state["session_initialized"] = True

def save_all_ui_settings():
    cfg = load_settings()
    mapping = {
        "ui_method_key": "input_method", "ui_yt_qual_key": "yt_quality", "ui_ratio_key": "aspect_ratio",
        "ui_fill_mode_key": "fill_mode",
        "ui_res_key": "export_resolution", "ui_codec_key": "export_codec", "ui_bitrate_key": "export_bitrate",
        "ui_api_key_key": "api_key", "ui_yt_subs_key": "use_yt_subs", "ui_whisper_lang_key": "whisper_lang",
        "ui_whisper_glossary_key": "whisper_glossary",
        "ui_target_lang_key": "target_lang", "ui_shorts_count_key": "shorts_count", "ui_dur_key": "duration_range",
        "ui_prompt_mode_key": "prompt_mode", "ui_custom_prompt_key": "custom_prompt_text",
        "ui_face_tracking_key": "face_tracking", "ui_ft_strategy_key": "ft_strategy", "ui_ft_zoom_key": "ft_zoom",
        "ui_ft_y_offset_key": "ft_y_offset", "ui_ft_smoothness_key": "ft_smoothness", "ui_ft_recheck_key": "ft_recheck",
        "ui_enable_subs_key": "enable_subtitles", "preset_selector": "sub_preset", "ui_override_bcolor_key": "sub_bcolor",
        "ui_override_hcolor_key": "sub_hcolor", "ui_override_size_key": "sub_size", "ui_override_hsize_key": "sub_hsize",
        "ui_override_out_color_key": "sub_out_color", "ui_override_out_thick_key": "sub_out_thick", "ui_override_shad_color_key": "sub_shad_color",
        "ui_override_shad_size_key": "sub_shad_size", "ui_override_bold_key": "sub_bold", "ui_override_italic_key": "sub_italic",
        "ui_override_upper_key": "sub_upper", "ui_override_punct_key": "sub_punct", "ui_override_words_key": "sub_words",
        "ui_override_mode_key": "sub_mode", "ui_override_animation_key": "sub_animation", "ui_override_margin_key": "sub_margin", 
        "ui_auto_scale_key": "sub_autoscale", "ui_override_bg_pad_key": "sub_bg_pad",
        "ui_blur_bg_key": "blur_bg", "ui_blur_sigma_key": "blur_sigma", "ui_blur_zoom_key": "blur_zoom", "ui_blur_bright_key": "blur_bright",
        "ui_use_proxy_key": "use_proxy", "ui_proxy_res_key": "proxy_res", "ui_proxy_bitrate_key": "proxy_bitrate",
        "ui_enable_logo_key": "enable_logo", "ui_logo_scale_key": "logo_scale", 
        "ui_logo_x_key": "logo_x", "ui_logo_y_key": "logo_y", "ui_logo_opacity_key": "logo_opacity",
        "ui_enable_text_key": "enable_text", "ui_wm_text_key": "wm_text", "ui_wm_font_key": "wm_font", 
        "ui_wm_size_key": "wm_size", "ui_wm_color_key": "wm_color", "ui_wm_x_key": "wm_x", 
        "ui_wm_y_key": "wm_y", "ui_wm_opacity_key": "wm_opacity",
        "ui_wm_out_color_key": "wm_out_color", "ui_wm_out_thick_key": "wm_out_thick",
        "ui_wm_shad_color_key": "wm_shad_color", "ui_wm_shad_size_key": "wm_shad_size",
        "ui_wm_bold_key": "wm_bold", "ui_wm_italic_key": "wm_italic",
        "custom_font_selection": "custom_font",
        "ui_audio_mode_key": "audio_mode", "ui_dub_target_lang_key": "dub_target_lang",
        "ui_dub_auto_subtitles_key": "dub_auto_subtitles", "ui_dub_keep_background_key": "dub_keep_background",
        "ui_dub_voice_source_key": "dub_voice_source", "ui_dub_selected_voice_path_key": "dub_selected_voice_path",
        "ui_dub_qwen_speaker_key": "dub_qwen_speaker", "ui_dub_ref_audio_length_key": "dub_ref_audio_length",
        "ui_dub_sync_min_tempo_key": "dub_sync_min_tempo", "ui_dub_sync_max_tempo_key": "dub_sync_max_tempo",
        "ui_dub_auto_min_tempo_key": "dub_auto_min_tempo", "ui_dub_auto_max_tempo_key": "dub_auto_max_tempo",
        "ui_dub_original_volume_key": "dub_original_volume", "ui_dub_background_volume_key": "dub_background_volume",
        "ui_dub_voice_volume_key": "dub_voice_volume", "ui_dub_duck_amount_key": "dub_duck_amount",
        "ui_dub_pitch_adjust_key": "dub_pitch_adjust", "ui_dub_style_prompt_key": "dub_style_prompt"
    }
    for s_key, s_name in mapping.items():
        if s_key in st.session_state:
            if s_key == "ui_dur_key":
                cfg["duration_min"] = st.session_state[s_key][0]
                cfg["duration_max"] = st.session_state[s_key][1]
            else:
                cfg[s_name] = st.session_state[s_key]
    save_settings(cfg)


def on_audio_mode_change():
    st.session_state["_audio_widget_rev"] = st.session_state.get("_audio_widget_rev", 0) + 1
    restore_audio_state_from_settings(repair_reset_values=True)
    save_all_ui_settings()

def restore_subtitle_state_from_settings():
    cfg = load_settings()
    preset_name = cfg.get("sub_preset", "Hormozi (Classic)")
    if preset_name not in SUBTITLE_PRESETS:
        preset_name = "Hormozi (Classic)"

    p = SUBTITLE_PRESETS[preset_name]

    st.session_state["preset_selector"] = preset_name
    st.session_state["ui_override_bcolor_key"] = cfg.get("sub_bcolor", p.get("base_color", "#FFFFFF"))
    st.session_state["ui_override_hcolor_key"] = cfg.get("sub_hcolor", p.get("highlight_color", "#00FF00"))
    st.session_state["ui_override_size_key"] = int(cfg.get("sub_size", p.get("font_size", 30)))
    st.session_state["ui_override_hsize_key"] = int(cfg.get("sub_hsize", p.get("highlight_size", 35)))
    st.session_state["ui_override_out_color_key"] = cfg.get("sub_out_color", p.get("outline_color", "#000000"))
    st.session_state["ui_override_out_thick_key"] = int(cfg.get("sub_out_thick", p.get("outline_thickness", 3)))
    st.session_state["ui_override_shad_color_key"] = cfg.get("sub_shad_color", p.get("shadow_color", "#000000"))
    st.session_state["ui_override_shad_size_key"] = int(cfg.get("sub_shad_size", p.get("shadow_size", 0)))
    st.session_state["ui_override_bold_key"] = bool(cfg.get("sub_bold", p.get("bold", True)))
    st.session_state["ui_override_italic_key"] = bool(cfg.get("sub_italic", p.get("italic", False)))
    st.session_state["ui_override_upper_key"] = bool(cfg.get("sub_upper", p.get("uppercase", True)))
    st.session_state["ui_override_punct_key"] = bool(cfg.get("sub_punct", p.get("remove_punctuation", True)))
    st.session_state["ui_override_words_key"] = int(cfg.get("sub_words", p.get("words_per_block", 2)))
    st.session_state["ui_override_mode_key"] = cfg.get("sub_mode", p.get("mode", "highlight"))
    st.session_state["ui_override_animation_key"] = cfg.get("sub_animation", p.get("animation", "none"))
    st.session_state["ui_override_margin_key"] = int(cfg.get("sub_margin", 600 if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else 100))
    st.session_state["ui_override_bg_pad_key"] = int(cfg.get("sub_bg_pad", p.get("bg_padding", 45)))
    st.session_state["ui_auto_scale_key"] = bool(cfg.get("sub_autoscale", False))
    st.session_state["custom_font_selection"] = cfg.get("custom_font", "Domyślna dla presetu")
    
    st.session_state["_anim_ui_selector"] = REVERSE_ANIMATION_MAP.get(st.session_state["ui_override_animation_key"], "Brak")

def apply_preset_to_subtitle_state(preset_name):
    if preset_name not in SUBTITLE_PRESETS:
        preset_name = "Hormozi (Classic)"

    p = SUBTITLE_PRESETS[preset_name]

    st.session_state["ui_override_bcolor_key"] = p.get("base_color", "#FFFFFF")
    st.session_state["ui_override_hcolor_key"] = p.get("highlight_color", "#00FF00")
    st.session_state["ui_override_size_key"] = int(p.get("font_size", 30))
    st.session_state["ui_override_hsize_key"] = int(p.get("highlight_size", p.get("font_size", 30) + 5))
    st.session_state["ui_override_out_color_key"] = p.get("outline_color", "#000000")
    st.session_state["ui_override_out_thick_key"] = int(p.get("outline_thickness", 3))
    st.session_state["ui_override_shad_color_key"] = p.get("shadow_color", "#000000")
    st.session_state["ui_override_shad_size_key"] = int(p.get("shadow_size", 0))
    st.session_state["ui_override_bold_key"] = bool(p.get("bold", True))
    st.session_state["ui_override_italic_key"] = bool(p.get("italic", False))
    st.session_state["ui_override_upper_key"] = bool(p.get("uppercase", True))
    st.session_state["ui_override_punct_key"] = bool(p.get("remove_punctuation", True))
    st.session_state["ui_override_words_key"] = int(p.get("words_per_block", 2))
    st.session_state["ui_override_mode_key"] = p.get("mode", "highlight")
    st.session_state["ui_override_animation_key"] = p.get("animation", "none")
    st.session_state["ui_override_margin_key"] = 600 if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else 100
    st.session_state["ui_override_bg_pad_key"] = int(p.get("bg_padding", 45))
    st.session_state["ui_auto_scale_key"] = False
    
    st.session_state["custom_font_selection"] = "Domyślna dla presetu"
    
    st.session_state["_anim_ui_selector"] = REVERSE_ANIMATION_MAP.get(p.get("animation", "none"), "Brak")

def update_preset_settings():
    preset = st.session_state.get("preset_selector", "Hormozi (Classic)")
    apply_preset_to_subtitle_state(preset)
    save_all_ui_settings()

def on_toggle_subtitles():
    cfg = load_settings()
    cfg["enable_subtitles"] = st.session_state.get("ui_enable_subs_key", True)
    save_settings(cfg)

    if st.session_state.get("ui_enable_subs_key", True):
        restore_subtitle_state_from_settings()
