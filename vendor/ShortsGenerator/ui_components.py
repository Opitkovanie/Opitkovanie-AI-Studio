import os
import re
import copy
import json
import time
import base64
import shutil
import unicodedata
import streamlit as st
import streamlit.components.v1 as components

subtitle_editor_component = components.declare_component(
    "subtitle_editor_component",
    path=os.path.abspath("custom_components/subtitle_editor/frontend")
)

def make_download_filename(title, ext=".mp4", fallback="Short"):
    clean_title = str(title or "").strip()
    clean_title = re.sub(r'[/\\:*?"<>|]+', '', clean_title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" ._")
    if not clean_title:
        clean_title = fallback
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{clean_title}{ext}"

def get_current_global_render_settings():
    ar_val = "9:16" if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else "16:9"
    return {
        "subs": st.session_state.get("ui_enable_subs_key", True), 
        "preset": st.session_state.get("preset_selector", "Hormozi (Classic)"), 
        "font": st.session_state.get("custom_font_selection", "Domyślna dla presetu"), 
        "aspect_ratio": ar_val,
        "fill_mode": st.session_state.get("ui_fill_mode_key", 100),
        "blur_bg": st.session_state.get("ui_blur_bg_key", True),
        "blur_sigma": st.session_state.get("ui_blur_sigma_key", 25),
        "blur_zoom": st.session_state.get("ui_blur_zoom_key", 1.0),
        "blur_bright": st.session_state.get("ui_blur_bright_key", 40) / 100.0,
        "export_res": st.session_state.get("ui_res_key", "Zgodna ze źródłem"),
        "export_bitrate": st.session_state.get("ui_bitrate_key", 15), 
        "export_codec": st.session_state.get("ui_codec_key", "H.264 (Większa kompatybilność)"),
        "face_tracking": st.session_state.get("ui_face_tracking_key", True), 
        "ft_smoothness": st.session_state.get("ui_ft_smoothness_key", 10), 
        "ft_recheck": st.session_state.get("ui_ft_recheck_key", 8), 
        "ft_zoom": st.session_state.get("ui_ft_zoom_key", 1.0), 
        "ft_y_offset": st.session_state.get("ui_ft_y_offset_key", 0), 
        "ft_strategy": st.session_state.get("ui_ft_strategy_key", "Główny mówca (Skupia na największej twarzy)"),
        "bcolor": st.session_state.get("ui_override_bcolor_key", "#FFFFFF"), 
        "hcolor": st.session_state.get("ui_override_hcolor_key", "#00FF00"), 
        "size": st.session_state.get("ui_override_size_key", 30), 
        "margin": st.session_state.get("ui_override_margin_key", 600), 
        "auto_scale": st.session_state.get("ui_auto_scale_key", False),
        "hsize": st.session_state.get("ui_override_hsize_key", 35), 
        "out_color": st.session_state.get("ui_override_out_color_key", "#000000"), 
        "out_thick": st.session_state.get("ui_override_out_thick_key", 3), 
        "shad_color": st.session_state.get("ui_override_shad_color_key", "#000000"),
        "shad_size": st.session_state.get("ui_override_shad_size_key", 0), 
        "bold": st.session_state.get("ui_override_bold_key", True), 
        "italic": st.session_state.get("ui_override_italic_key", False), 
        "upper": st.session_state.get("ui_override_upper_key", True),
        "words": st.session_state.get("ui_override_words_key", 2), 
        "mode": st.session_state.get("ui_override_mode_key", "highlight"), 
        "punct": st.session_state.get("ui_override_punct_key", True),
        "bg_padding": st.session_state.get("ui_override_bg_pad_key", 45),
        "animation": st.session_state.get("ui_override_animation_key", "none"),
        "enable_logo": st.session_state.get("ui_enable_logo_key", False),
        "logo_scale": st.session_state.get("ui_logo_scale_key", 20),
        "logo_x": st.session_state.get("ui_logo_x_key", 50),
        "logo_y": st.session_state.get("ui_logo_y_key", 50),
        "logo_opacity": st.session_state.get("ui_logo_opacity_key", 100),
        "enable_text": st.session_state.get("ui_enable_text_key", False),
        "wm_text": st.session_state.get("ui_wm_text_key", ""),
        "wm_font": st.session_state.get("ui_wm_font_key", "Domyślna dla presetu"),
        "wm_size": st.session_state.get("ui_wm_size_key", 50),
        "wm_color": st.session_state.get("ui_wm_color_key", "#FFFFFF"),
        "wm_out_color": st.session_state.get("ui_wm_out_color_key", "#000000"),
        "wm_out_thick": st.session_state.get("ui_wm_out_thick_key", 0),
        "wm_shad_color": st.session_state.get("ui_wm_shad_color_key", "#000000"),
        "wm_shad_size": st.session_state.get("ui_wm_shad_size_key", 0),
        "wm_bold": st.session_state.get("ui_wm_bold_key", False),
        "wm_italic": st.session_state.get("ui_wm_italic_key", False),
        "wm_x": st.session_state.get("ui_wm_x_key", 50),
        "wm_y": st.session_state.get("ui_wm_y_key", 50),
        "wm_opacity": st.session_state.get("ui_wm_opacity_key", 100),
        "logo_path": "workspace/logo.png" if os.path.exists("workspace/logo.png") else None,
        "use_proxy": st.session_state.get("ui_use_proxy_key", False),
        "proxy_res": st.session_state.get("ui_proxy_res_key", "1080p"),
        "proxy_bitrate": st.session_state.get("ui_proxy_bitrate_key", 15),
        "audio_mode": st.session_state.get("ui_audio_mode_key", "Oryginalne audio"),
        "dub_target_lang": st.session_state.get("ui_dub_target_lang_key", "Angielski"),
        "dub_auto_subtitles": st.session_state.get("ui_dub_auto_subtitles_key", True),
        "dub_keep_background": st.session_state.get("ui_dub_keep_background_key", True),
        "dub_voice_source": st.session_state.get("ui_dub_voice_source_key", "Głos z oryginalnego filmu"),
        "dub_selected_voice_path": st.session_state.get("ui_dub_selected_voice_path_key", ""),
        "dub_qwen_speaker": st.session_state.get("ui_dub_qwen_speaker_key", "Ryan"),
        "dub_ref_audio_length": st.session_state.get("ui_dub_ref_audio_length_key", 12),
        "dub_sync_min_tempo": st.session_state.get("ui_dub_sync_min_tempo_key", 0.90),
        "dub_sync_max_tempo": st.session_state.get("ui_dub_sync_max_tempo_key", 1.00),
        "dub_auto_min_tempo": st.session_state.get("ui_dub_auto_min_tempo_key", False),
        "dub_auto_max_tempo": st.session_state.get("ui_dub_auto_max_tempo_key", True),
        "dub_original_volume": st.session_state.get("ui_dub_original_volume_key", 0.85),
        "dub_background_volume": st.session_state.get("ui_dub_background_volume_key", 1.4),
        "dub_voice_volume": st.session_state.get("ui_dub_voice_volume_key", 1.5),
        "dub_duck_amount": st.session_state.get("ui_dub_duck_amount_key", 0.95),
        "dub_pitch_adjust": st.session_state.get("ui_dub_pitch_adjust_key", 0.0),
        "dub_style_prompt": st.session_state.get("ui_dub_style_prompt_key", ""),
    }

from config import SUBTITLE_PRESETS, AVAILABLE_LANGS, ANIMATION_TYPES, ANIMATION_MAP, REVERSE_ANIMATION_MAP
from utils import load_settings, save_settings, get_mac_file_path, safe_trash_file, get_video_stats, is_favorite, remove_from_favorites, add_to_favorites
from subtitle_engine import get_available_fonts, generate_font_preview_html, generate_viral_ass_subtitles
from video_engine import FACE_TRACKING_AVAILABLE, FACE_TRACKING_ERROR, create_preview_video, generate_xml_content, render_short_ffmpeg, seconds_to_timecode
from ai_processor import initialize_short_words, translate_short_with_gemini, update_segments_and_resync_words
from state_manager import save_all_ui_settings, update_preset_settings, on_toggle_subtitles, normalize_blur_bright, restore_audio_state_from_settings, on_audio_mode_change
from dubbing_engine import (
    DUB_LANGS, DUB_MIX_MODES, DUB_VOICE_SOURCES, QWEN_SPEAKERS,
    build_dubbed_audio, is_dubbing_enabled, language_slug, list_rendered_versions,
    kill_active_dubbing_processes, list_voice_samples, load_cached_translation, save_cached_translation,
    save_uploaded_voice_sample, update_version_manifest, align_short_words_to_dub_audio
)

# ==============================================================================
# CALLBACKI EDYTORA
# ==============================================================================
def set_scroll_and_expander(s_id, e_id):
    st.session_state.scroll_target = s_id
    st.session_state.active_expander = e_id

def set_active_expander(e_id):
    st.session_state.active_expander = e_id

def adjust_slider(slider_key, new_start, new_end, expander_id):
    st.session_state[slider_key] = (new_start, new_end)
    st.session_state.active_expander = expander_id

def delete_segment(del_key, s_idx, expander_id):
    if del_key not in st.session_state:
        st.session_state[del_key] = []
    st.session_state[del_key].append(s_idx)
    st.session_state.active_expander = expander_id
    
def update_anim_ui():
    st.session_state["ui_override_animation_key"] = ANIMATION_MAP.get(st.session_state["_anim_ui_selector"], "none")
    save_all_ui_settings()


def get_dub_request_queue():
    if "dub_render_queue" not in st.session_state or not isinstance(st.session_state["dub_render_queue"], dict):
        st.session_state["dub_render_queue"] = {}
    return st.session_state["dub_render_queue"]


def queue_dub_render(project_id, short_index, settings, render_id=None, **extra):
    queue = get_dub_request_queue()
    request = {
        "settings": copy.deepcopy(settings),
        "render_id": render_id or int(time.time()),
        "language": settings.get("dub_target_lang", "Angielski"),
    }
    request.update(extra)
    queue[f"{project_id}:{short_index}"] = request


def pop_dub_render(project_id, short_index):
    get_dub_request_queue().pop(f"{project_id}:{short_index}", None)


def get_dub_render_request(project_id, short_index):
    return get_dub_request_queue().get(f"{project_id}:{short_index}")


def clear_project_dub_queue(project_id):
    queue = get_dub_request_queue()
    for key in list(queue.keys()):
        if key.startswith(f"{project_id}:"):
            queue.pop(key, None)


def audio_widget_key(base_key):
    return f"{base_key}__audio_{st.session_state.get('_audio_widget_rev', 0)}"


def prepare_audio_widget(base_key):
    widget_key = audio_widget_key(base_key)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(base_key)
    return widget_key


def sync_audio_widget(base_key):
    widget_key = audio_widget_key(base_key)
    if widget_key in st.session_state:
        st.session_state[base_key] = st.session_state[widget_key]
    save_all_ui_settings()


# ==============================================================================
# LEWY PANEL (SIDEBAR)
# ==============================================================================
@st.fragment
def render_sidebar():
    available_fonts = ["Domyślna dla presetu"] + get_available_fonts()
    font_css = ""
    for idx, font_file in enumerate(available_fonts):
        if font_file == "Domyślna dla presetu": continue
        font_path = os.path.join("fonts", font_file)
        if os.path.exists(font_path):
            try:
                with open(font_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                font_css += f"@font-face {{ font-family: 'GalleryFont_{idx}'; src: url(data:font/truetype;charset=utf-8;base64,{b64}); }}\n"
            except: pass
    st.markdown(f"<style>{font_css}</style>", unsafe_allow_html=True)

    st.markdown("## ⚙️ Ustawienia")
    tab_format, tab_audio, tab_sub, tab_logo, tab_ai = st.tabs(["🎬 Wideo", "🎧 Audio", "💬 Napisy", "🖼️ Logo", "🧠 AI"])
        
    with tab_format:
        st.radio("Format obrazu:", ["9:16 (Pionowy - Short/TikTok)", "16:9 (Oryginalny - YouTube)"], horizontal=True, key="ui_ratio_key", on_change=save_all_ui_settings)
            
        if "9:16" in st.session_state.get("ui_ratio_key", "9:16"):
            st.slider("Wypełnienie pionowego ekranu (%)", min_value=0, max_value=100, step=5, key="ui_fill_mode_key", on_change=save_all_ui_settings, help="0% = cały oryginalny obraz z czarnymi pasami na górze i dole. 100% = pełny ekran pionowy (przycięte boki). 💡 UWAGA: Zostanie zignorowane (wymusi 100%), jeśli w zakładce AI włączysz Face Tracking!")
                
            ui_blur_bg = st.checkbox("Włącz rozmyte tło", key="ui_blur_bg_key", on_change=save_all_ui_settings)
            if ui_blur_bg:
                with st.expander("⚙️ Ustawienia rozmytego tła", expanded=True):
                    # Gdy checkbox jest odznaczony, Streamlit usuwa klucze suwakow z session_state.
                    # Dlatego przy ponownym wlaczeniu czytamy z pliku ustawien, nie z session_state.
                    _saved = load_settings()

                    if "ui_blur_sigma_key" not in st.session_state:
                        st.session_state["ui_blur_sigma_key"] = int(_saved.get("blur_sigma", 25))
                    curr_sigma = st.session_state["ui_blur_sigma_key"]
                    try:
                        curr_sigma = int(curr_sigma)
                        curr_sigma = max(5, min(100, curr_sigma))
                    except:
                        curr_sigma = 25
                    st.session_state["ui_blur_sigma_key"] = curr_sigma
                    st.slider("Siła rozmycia (Blur)", min_value=5, max_value=100, step=5, key="ui_blur_sigma_key", on_change=save_all_ui_settings)

                    if "ui_blur_zoom_key" not in st.session_state:
                        st.session_state["ui_blur_zoom_key"] = float(_saved.get("blur_zoom", 1.0))
                    curr_zoom = st.session_state["ui_blur_zoom_key"]
                    try:
                        curr_zoom = float(curr_zoom)
                        curr_zoom = max(1.0, min(3.0, curr_zoom))
                    except:
                        curr_zoom = 1.0
                    st.session_state["ui_blur_zoom_key"] = round(curr_zoom, 1)
                    st.slider("Powiększenie tła", min_value=1.0, max_value=3.0, step=0.1, key="ui_blur_zoom_key", on_change=save_all_ui_settings)

                    if "ui_blur_bright_key" not in st.session_state:
                        raw = _saved.get("blur_bright", 40)
                        st.session_state["ui_blur_bright_key"] = normalize_blur_bright(raw)
                    curr_bright = st.session_state["ui_blur_bright_key"]
                    try:
                        curr_bright = normalize_blur_bright(curr_bright)
                    except:
                        curr_bright = 40
                    st.session_state["ui_blur_bright_key"] = curr_bright
                    st.slider("Jasność tła (%)", min_value=10, max_value=100, step=5, key="ui_blur_bright_key", on_change=save_all_ui_settings)
                
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            export_res_options = ["Zgodna ze źródłem", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p"]
            st.selectbox("Rozdzielczość:", export_res_options, key="ui_res_key", on_change=save_all_ui_settings)
        with col_f2:
            st.selectbox("Kodek:", ["H.264 (Większa kompatybilność)", "H.265 / HEVC"], key="ui_codec_key", on_change=save_all_ui_settings)
                
        st.slider("Bitrate (Mbps):", 1, 100, key="ui_bitrate_key", on_change=save_all_ui_settings, help="💡 TIP: Dla YouTube Shorts i TikToka (1080p) optymalny to 10-15 Mbps (H.264) lub 8-10 Mbps (H.265/HEVC). Dla eksportu w 4K ustaw 30-45 Mbps.")
            
        st.markdown("---")
        st.slider("Ile shortów stworzyć?", 1, 50, key="ui_shorts_count_key", on_change=save_all_ui_settings)
        st.slider("Długość klipu (s):", 15, 240, key="ui_dur_key", on_change=save_all_ui_settings)
            
        with st.expander("⚡ Proxy (Dla słabych PC)"):
            ui_use_proxy = st.checkbox("Włącz Proxy dla plików lokalnych", key="ui_use_proxy_key", on_change=save_all_ui_settings)
            if ui_use_proxy:
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    st.selectbox("Rozdz. Proxy:", ["1080p", "720p"], key="ui_proxy_res_key", on_change=save_all_ui_settings)
                with col_p2:
                    st.slider("Bitrate Proxy:", 5, 50, key="ui_proxy_bitrate_key", on_change=save_all_ui_settings)

    with tab_audio:
        restore_audio_state_from_settings(
            repair_reset_values=st.session_state.get("ui_audio_mode_key") != "Oryginalne audio"
        )
        st.selectbox(
            "Tryb audio",
            DUB_MIX_MODES,
            key="ui_audio_mode_key",
            on_change=on_audio_mode_change,
            help="Oryginalne audio zostawia film bez dubbingu. Pozostałe tryby generują nową ścieżkę Qwen TTS i podmieniają ją podczas renderowania shorta."
        )

        if st.session_state.get("ui_audio_mode_key") != "Oryginalne audio":
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                dub_lang_key = prepare_audio_widget("ui_dub_target_lang_key")
                st.selectbox(
                    "Język dubbingu",
                    DUB_LANGS,
                    key=dub_lang_key,
                    on_change=lambda: sync_audio_widget("ui_dub_target_lang_key"),
                    help="Język, w którym Qwen wygeneruje nowy głos. Pliki wyjściowe będą oznaczone skrótem tego języka."
                )
            with col_a2:
                auto_subs_key = prepare_audio_widget("ui_dub_auto_subtitles_key")
                st.checkbox(
                    "Dopasuj napisy do języka audio",
                    key=auto_subs_key,
                    on_change=lambda: sync_audio_widget("ui_dub_auto_subtitles_key"),
                    help="Po włączeniu aplikacja przy renderze dubbingu przetłumaczy napisy i metadane na ten sam język, żeby obraz, głos i napisy mówiły jednym językiem."
                )

            keep_bg_key = prepare_audio_widget("ui_dub_keep_background_key")
            st.checkbox(
                "Pozostaw tło z oryginalnego filmu",
                key=keep_bg_key,
                on_change=lambda: sync_audio_widget("ui_dub_keep_background_key"),
                help="Używa separacji audio Demucs, aby zostawić muzykę/szumy/tło, a usunąć oryginalny głos. Wymaga bibliotek z DubMastera."
            )

            st.markdown("#### Głos")
            voice_source_key = prepare_audio_widget("ui_dub_voice_source_key")
            st.selectbox(
                "Źródło głosu",
                DUB_VOICE_SOURCES,
                key=voice_source_key,
                on_change=lambda: sync_audio_widget("ui_dub_voice_source_key"),
                help="Głos z oryginału klonuje barwę mówcy z shorta. Własna próbka pozwala podmienić głos na zapisany lub wgrany plik. Baza Qwen używa gotowego głosu modelu."
            )

            voice_source = st.session_state.get(voice_source_key, st.session_state.get("ui_dub_voice_source_key"))
            if voice_source == "Własna próbka głosu":
                uploaded_voice = st.file_uploader(
                    "Dodaj próbkę głosu",
                    type=["wav", "mp3", "m4a", "aac", "flac", "mp4", "mov", "mkv"],
                    help="Najlepiej użyć 8-20 sekund czystej mowy bez muzyki. Plik zostanie zapisany w workspace/voice_samples."
                )
                if uploaded_voice is not None and st.button("💾 Zapisz próbkę głosu", use_container_width=True):
                    saved_path = save_uploaded_voice_sample(uploaded_voice)
                    st.session_state["ui_dub_selected_voice_path_key"] = saved_path
                    st.session_state[prepare_audio_widget("ui_dub_selected_voice_path_key")] = saved_path
                    save_all_ui_settings()
                    st.toast("Próbka głosu zapisana.")
                    st.rerun()

                samples = list_voice_samples()
                labels = ["Brak"] + [os.path.basename(p) for p in samples]
                current = st.session_state.get("ui_dub_selected_voice_path_key", "")
                current_idx = samples.index(current) + 1 if current in samples else 0
                selected_label = st.selectbox(
                    "Wybrana próbka",
                    labels,
                    index=current_idx,
                    help="Próbki są trzymane lokalnie w jednym folderze, aby nie śmiecić po projektach."
                )
                new_path = "" if selected_label == "Brak" else samples[labels.index(selected_label) - 1]
                if new_path != current:
                    st.session_state["ui_dub_selected_voice_path_key"] = new_path
                    st.session_state[prepare_audio_widget("ui_dub_selected_voice_path_key")] = new_path
                    save_all_ui_settings()
            elif voice_source == "Głos z bazy Qwen TTS":
                qwen_speaker_key = prepare_audio_widget("ui_dub_qwen_speaker_key")
                st.selectbox(
                    "Głos Qwen",
                    QWEN_SPEAKERS,
                    key=qwen_speaker_key,
                    on_change=lambda: sync_audio_widget("ui_dub_qwen_speaker_key"),
                    help="Gotowy głos modelu Qwen. Przydatne, gdy nie chcesz klonować głosu z filmu."
                )
            else:
                ref_len_key = prepare_audio_widget("ui_dub_ref_audio_length_key")
                st.slider(
                    "Długość próbki głosu (s)",
                    3, 25,
                    key=ref_len_key,
                    on_change=lambda: sync_audio_widget("ui_dub_ref_audio_length_key"),
                    help="Ile sekund mowy z shorta użyć do zbudowania profilu głosu. 10-15 s zwykle daje dobry balans jakości i szybkości."
                )

            st.markdown("#### Miks i synchronizacja")
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                voice_vol_key = prepare_audio_widget("ui_dub_voice_volume_key")
                bg_vol_key = prepare_audio_widget("ui_dub_background_volume_key")
                orig_vol_key = prepare_audio_widget("ui_dub_original_volume_key")
                st.slider("Głośność dubbingu", 0.1, 3.0, step=0.05, key=voice_vol_key, on_change=lambda: sync_audio_widget("ui_dub_voice_volume_key"), help="Reguluje głośność wygenerowanego głosu.")
                st.slider("Głośność tła", 0.0, 2.0, step=0.05, key=bg_vol_key, on_change=lambda: sync_audio_widget("ui_dub_background_volume_key"), help="Reguluje muzykę i tło po separacji Demucs.")
                st.slider("Głośność oryginału", 0.0, 1.5, step=0.05, key=orig_vol_key, on_change=lambda: sync_audio_widget("ui_dub_original_volume_key"), help="Używane w trybie lektora na oryginalnym audio.")
            with col_m2:
                auto_min_key = prepare_audio_widget("ui_dub_auto_min_tempo_key")
                min_tempo_key = prepare_audio_widget("ui_dub_sync_min_tempo_key")
                auto_max_key = prepare_audio_widget("ui_dub_auto_max_tempo_key")
                max_tempo_key = prepare_audio_widget("ui_dub_sync_max_tempo_key")
                duck_key = prepare_audio_widget("ui_dub_duck_amount_key")
                st.checkbox("AUTO min", key=auto_min_key, on_change=lambda: sync_audio_widget("ui_dub_auto_min_tempo_key"), help="Jak w DubMasterze: gdy AUTO jest włączone, aplikacja nie wymusza zwalniania krótkich fragmentów.")
                st.slider("Minimalne tempo", 0.5, 1.5, step=0.05, key=min_tempo_key, on_change=lambda: sync_audio_widget("ui_dub_sync_min_tempo_key"), disabled=st.session_state.get(auto_min_key, False), help="Dolny limit zwalniania głosu przy dopasowaniu do długości scen. Domyślnie 0.90 jak w DubMasterze.")
                st.checkbox("AUTO max", key=auto_max_key, on_change=lambda: sync_audio_widget("ui_dub_auto_max_tempo_key"), help="Aplikacja dobiera bezpieczne przyspieszenie, ale nie ściska głosu agresywnie. Jeśli potrzebujesz mocniejszego dopasowania, wyłącz AUTO i ustaw limit ręcznie.")
                st.slider("Maksymalne tempo", 1.0, 2.0, step=0.05, key=max_tempo_key, on_change=lambda: sync_audio_widget("ui_dub_sync_max_tempo_key"), disabled=st.session_state.get(auto_max_key, True), help="Górny limit przyspieszania głosu. Przy AUTO aplikacja używa bezpiecznego limitu, żeby dubbing nie brzmiał jak przyspieszony.")
                st.slider("Ducking oryginału", 0.0, 1.0, step=0.05, key=duck_key, on_change=lambda: sync_audio_widget("ui_dub_duck_amount_key"), help="Rezerwa pod pełniejsze ściszanie oryginału przy głosie. Ustawienie jest zapamiętywane dla rozwinięcia miksu.")

            style_key = prepare_audio_widget("ui_dub_style_prompt_key")
            st.text_area(
                "Styl głosu / instrukcja dla TTS",
                key=style_key,
                on_change=lambda: sync_audio_widget("ui_dub_style_prompt_key"),
                height=90,
                placeholder="np. Naturalny, energiczny lektor YouTube Shorts, bez przesadnej teatralności.",
                help="Krótka instrukcja stylu dla Qwen. Zostaw puste, jeśli chcesz neutralny dubbing."
            )

    with tab_sub:
        ui_enable_subs = st.checkbox("Generuj dynamiczne napisy", key="ui_enable_subs_key", on_change=on_toggle_subtitles)
            
        if ui_enable_subs:
            ui_preset = st.selectbox("Styl napisów:", list(SUBTITLE_PRESETS.keys()), key="preset_selector", on_change=update_preset_settings)
                
            if st.session_state.custom_font_selection not in available_fonts: st.session_state.custom_font_selection = available_fonts[0]
            ui_custom_font = st.session_state.custom_font_selection

            with st.expander(f"✒️ Czcionka: {ui_custom_font}"):
                with st.container(height=350, border=False):
                    cols = st.columns(2)
                    for idx, font_file in enumerate(available_fonts):
                        with cols[idx % 2]:
                            is_selected = (font_file == ui_custom_font)
                            with st.container(border=True):
                                if font_file == "Domyślna dla presetu":
                                    st.markdown(f'<div style="text-align: center; padding: 10px 0;"><span style="color: #9ca3af; font-size: 14px;">{font_file}</span></div>', unsafe_allow_html=True)
                                else:
                                    st.markdown(f'<div style="font-family: \'GalleryFont_{idx}\'; font-size: 18px; color: #fff; text-align: center; margin-bottom: 5px; line-height: 1.2; overflow: hidden; white-space: nowrap;" title="ABCdef1234!@#">ABCdef1234!@#</div><div style="font-size: 11px; color: #9ca3af; text-align: center; word-break: break-all; height: 16px; overflow: hidden; margin-bottom: 8px;" title="{font_file}">{font_file[:20]}</div>', unsafe_allow_html=True)
                                if st.button("Wybrano" if is_selected else "Wybierz", key=f"btn_font_{idx}", use_container_width=True, disabled=is_selected):
                                    st.session_state.custom_font_selection = font_file
                                    curr_cfg = load_settings()
                                    curr_cfg["custom_font"] = font_file
                                    save_settings(curr_cfg)
                                    st.rerun()
                
            curr_bcolor = st.session_state.get("ui_override_bcolor_key", "#FFFFFF")
            curr_hcolor = st.session_state.get("ui_override_hcolor_key", "#00FF00")
            curr_size = st.session_state.get("ui_override_size_key", 30)
            curr_hsize = st.session_state.get("ui_override_hsize_key", 35)
            curr_out_color = st.session_state.get("ui_override_out_color_key", "#000000")
            curr_out_thick = st.session_state.get("ui_override_out_thick_key", 3)
            curr_shad_color = st.session_state.get("ui_override_shad_color_key", "#000000")
            curr_shad_size = st.session_state.get("ui_override_shad_size_key", 0)
            curr_bold = st.session_state.get("ui_override_bold_key", True)
            curr_italic = st.session_state.get("ui_override_italic_key", False)
            curr_upper = st.session_state.get("ui_override_upper_key", True)
            curr_mode = st.session_state.get("ui_override_mode_key", "highlight")
            curr_anim = st.session_state.get("ui_override_animation_key", "none")
            curr_bg_pad = st.session_state.get("ui_override_bg_pad_key", 45)
                
            st.markdown(generate_font_preview_html(ui_preset, ui_custom_font, curr_bcolor, curr_hcolor, curr_size, curr_hsize, curr_out_color, curr_out_thick, curr_shad_color, curr_shad_size, curr_bold, curr_italic, curr_upper, curr_mode, curr_anim, curr_bg_pad), unsafe_allow_html=True)

            with st.expander("🎨 Opcje zaawansowane"):
                st.markdown("**Kolory i Zarys**")
                col_c1, col_c2, col_c3, col_c4 = st.columns(4)
                with col_c1: st.color_picker("Tekst", key="ui_override_bcolor_key", on_change=save_all_ui_settings)
                with col_c2: st.color_picker("Tło", key="ui_override_hcolor_key", on_change=save_all_ui_settings)
                with col_c3: st.color_picker("Obrys", key="ui_override_out_color_key", on_change=save_all_ui_settings)
                with col_c4: st.color_picker("Cień", key="ui_override_shad_color_key", on_change=save_all_ui_settings)
                    
                col_m1, col_m2, col_m3 = st.columns(3)
                with col_m1: st.selectbox("Tryb", ["highlight", "highlight_box", "word_by_word", "build_up", "fade"], key="ui_override_mode_key", on_change=save_all_ui_settings)
                with col_m2: st.number_input("Słów w bloku", min_value=1, max_value=10, key="ui_override_words_key", on_change=save_all_ui_settings)
                    
                if "_anim_ui_selector" not in st.session_state:
                    st.session_state["_anim_ui_selector"] = REVERSE_ANIMATION_MAP.get(curr_anim, "Brak")
                with col_m3: st.selectbox("Efekt Animacji", ANIMATION_TYPES, key="_anim_ui_selector", on_change=update_anim_ui)

                st.markdown("**Rozmiar i Pozycja**")
                col_s1, col_s2 = st.columns(2)
                with col_s1: 
                    st.number_input("Baza (px)", min_value=10, max_value=150, step=1, key="ui_override_size_key", on_change=save_all_ui_settings)
                    st.number_input("Od dołu", min_value=0, max_value=2000, step=10, key="ui_override_margin_key", on_change=save_all_ui_settings)
                    st.number_input("Wiel. cienia", min_value=0, max_value=20, step=1, key="ui_override_shad_size_key", on_change=save_all_ui_settings)
                with col_s2: 
                    st.number_input("Podśw.(px)", min_value=10, max_value=150, step=1, key="ui_override_hsize_key", on_change=save_all_ui_settings)
                    st.number_input("Gr. obrysu", min_value=0, max_value=20, step=1, key="ui_override_out_thick_key", on_change=save_all_ui_settings)
                    st.number_input("Wielkość tła(%)", min_value=0, max_value=200, step=5, key="ui_override_bg_pad_key", on_change=save_all_ui_settings, help="Zwiększa grubość pola dla trybu Tło Słowa.")
                    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
                    st.checkbox("Auto-skala", key="ui_auto_scale_key", on_change=save_all_ui_settings)
                        
                st.markdown("**Formatowanie**")
                col_f1, col_f2, col_f3, col_f4 = st.columns(4)
                with col_f1: st.checkbox("Pogrub", key="ui_override_bold_key", on_change=save_all_ui_settings)
                with col_f2: st.checkbox("Kursyw", key="ui_override_italic_key", on_change=save_all_ui_settings)
                with col_f3: st.checkbox("WIELKI", key="ui_override_upper_key", on_change=save_all_ui_settings)
                with col_f4: st.checkbox("Z.Przest", key="ui_override_punct_key", on_change=save_all_ui_settings)
                    
                if st.button("🔄 Reset do presetu", use_container_width=True):
                    st.session_state["_reset_subtitles_to_preset"] = True
                    st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🎬 Generuj podgląd", use_container_width=True):
                with st.spinner("Generowanie..."):
                    preview_path = os.path.join("workspace", "preview.mp4")
                    ass_path = os.path.join("workspace", "preview.ass")
                    if os.path.exists(preview_path):
                        try: os.remove(preview_path)
                        except: pass
                    if os.path.exists(ass_path):
                        try: os.remove(ass_path)
                        except: pass
                        
                    bcolor = st.session_state.get("ui_override_bcolor_key")
                    hcolor = st.session_state.get("ui_override_hcolor_key")
                    size = st.session_state.get("ui_override_size_key")
                    hsize = st.session_state.get("ui_override_hsize_key")
                    out_color = st.session_state.get("ui_override_out_color_key")
                    out_thick = st.session_state.get("ui_override_out_thick_key")
                    shad_color = st.session_state.get("ui_override_shad_color_key")
                    shad_size = st.session_state.get("ui_override_shad_size_key")
                    bold = st.session_state.get("ui_override_bold_key")
                    italic = st.session_state.get("ui_override_italic_key")
                    upper = st.session_state.get("ui_override_upper_key")
                    mode = st.session_state.get("ui_override_mode_key")
                    words = st.session_state.get("ui_override_words_key")
                    punct = st.session_state.get("ui_override_punct_key")
                    margin = st.session_state.get("ui_override_margin_key")
                    ascale = st.session_state.get("ui_auto_scale_key")
                    anim = st.session_state.get("ui_override_animation_key", "none")
                    bg_pad = st.session_state.get("ui_override_bg_pad_key", 45)
                                
                    ar_val = "9:16" if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else "16:9"
                    try:
                        create_preview_video(ui_preset, ui_custom_font, bcolor, hcolor, size, hsize, out_color, out_thick, shad_color, shad_size, bold, italic, upper, mode, words, punct, ar_val, margin, ascale, anim, preview_path, bg_pad)
                        st.session_state["preview_video_path"] = preview_path
                    except Exception as e:
                        st.session_state.pop("preview_video_path", None)
                        st.error(str(e))
                
            if st.session_state.get("preview_video_path") and os.path.exists(st.session_state["preview_video_path"]):
                st.video(os.path.abspath(st.session_state["preview_video_path"]), autoplay=True, loop=True)

    with tab_logo:
        ui_enable_logo = st.checkbox("Dodaj graficzne logo", key="ui_enable_logo_key", on_change=save_all_ui_settings)
        if ui_enable_logo:
            logo_file = st.file_uploader("Plik (PNG, JPG)", type=['png', 'jpg', 'jpeg'])
            if logo_file is not None:
                with open("workspace/logo.png", "wb") as f:
                    f.write(logo_file.read())
                st.success("Logo gotowe do montażu!")
                
            col_log1, col_log2 = st.columns(2)
            with col_log1:
                st.number_input("Skala (%)", min_value=5, max_value=100, step=1, key="ui_logo_scale_key", on_change=save_all_ui_settings)
                st.number_input("Poz. Y", step=1, key="ui_logo_y_key", on_change=save_all_ui_settings)
            with col_log2:
                st.number_input("Poz. X", step=1, key="ui_logo_x_key", on_change=save_all_ui_settings)
                
            st.slider("Przezroczystość (%)", min_value=0, max_value=100, key="ui_logo_opacity_key", on_change=save_all_ui_settings)

        st.markdown("---")
        ui_enable_text = st.checkbox("Dodaj napis (Znak wodny)", key="ui_enable_text_key", on_change=save_all_ui_settings)
        if ui_enable_text:
            st.text_input("Tekst znaku", key="ui_wm_text_key", on_change=save_all_ui_settings)

            with st.expander("🎨 Wygląd napisu (Znak wodny)"):
                ui_wm_font = st.session_state.get("ui_wm_font_key", "Domyślna dla presetu")
                st.markdown(f"**Obecna czcionka:** {ui_wm_font}")
                    
                with st.container(height=200, border=True):
                    cols_wm = st.columns(2)
                    for idx, font_file in enumerate(available_fonts):
                        with cols_wm[idx % 2]:
                            is_selected = (font_file == ui_wm_font)
                            with st.container(border=True):
                                if font_file == "Domyślna dla presetu":
                                    st.markdown(f'<div style="text-align: center; padding: 10px 0;"><span style="color: #9ca3af; font-size: 14px;">{font_file}</span></div>', unsafe_allow_html=True)
                                else:
                                    st.markdown(f'<div style="font-family: \'GalleryFont_{idx}\'; font-size: 18px; color: #fff; text-align: center; margin-bottom: 5px; line-height: 1.2; overflow: hidden; white-space: nowrap;" title="ABCdef1234!@#">ABCdef1234!@#</div><div style="font-size: 11px; color: #9ca3af; text-align: center; word-break: break-all; height: 16px; overflow: hidden; margin-bottom: 8px;" title="{font_file}">{font_file[:20]}</div>', unsafe_allow_html=True)
                                if st.button("Wybrano" if is_selected else "Wybierz", key=f"btn_wm_font_{idx}", use_container_width=True, disabled=is_selected):
                                    st.session_state.ui_wm_font_key = font_file
                                    curr_cfg = load_settings()
                                    curr_cfg["wm_font"] = font_file
                                    save_settings(curr_cfg)
                                    st.rerun()
                    
                st.markdown("**Podstawowe**")
                col_w1, col_w2 = st.columns(2)
                with col_w1: st.number_input("Rozmiar", min_value=10, max_value=200, step=1, key="ui_wm_size_key", on_change=save_all_ui_settings)
                with col_w2: st.color_picker("Kolor tekstu", key="ui_wm_color_key", on_change=save_all_ui_settings)
                    
                st.markdown("**Obrys i Cień**")
                col_o1, col_o2 = st.columns(2)
                with col_o1: st.color_picker("Kolor obrysu", key="ui_wm_out_color_key", on_change=save_all_ui_settings)
                with col_o2: st.number_input("Grubość obrysu", min_value=0, max_value=20, step=1, key="ui_wm_out_thick_key", on_change=save_all_ui_settings)
                    
                col_s1, col_s2 = st.columns(2)
                with col_s1: st.color_picker("Kolor cienia", key="ui_wm_shad_color_key", on_change=save_all_ui_settings)
                with col_s2: st.number_input("Wielkość cienia", min_value=0, max_value=20, step=1, key="ui_wm_shad_size_key", on_change=save_all_ui_settings)
                    
                st.markdown("**Formatowanie**")
                col_f1, col_f2 = st.columns(2)
                with col_f1: st.checkbox("Pogrubienie (Bold)", key="ui_wm_bold_key", on_change=save_all_ui_settings)
                with col_f2: st.checkbox("Kursywa (Italic)", key="ui_wm_italic_key", on_change=save_all_ui_settings)

            st.markdown("**Pozycja i Widoczność**")
            col_txt1, col_txt2 = st.columns(2)
            with col_txt1:
                st.number_input("Poz. X", step=1, key="ui_wm_x_key", on_change=save_all_ui_settings)
            with col_txt2:
                st.number_input("Poz. Y", step=1, key="ui_wm_y_key", on_change=save_all_ui_settings)
                    
            st.slider("Widoczność (%)", min_value=0, max_value=100, key="ui_wm_opacity_key", on_change=save_all_ui_settings)
                
        if ui_enable_logo or ui_enable_text:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🎬 Generuj podgląd logo", use_container_width=True):
                with st.spinner("Generowanie..."):
                    import logo_handler
                    preview_path = os.path.join("workspace", "preview_logo.mp4")
                    if os.path.exists(preview_path):
                        try: os.remove(preview_path)
                        except: pass
                        
                    text_settings = {
                        "enable_logo": ui_enable_logo,
                        "logo_scale": st.session_state.get("ui_logo_scale_key", 20),
                        "logo_x": st.session_state.get("ui_logo_x_key", 50),
                        "logo_y": st.session_state.get("ui_logo_y_key", 50),
                        "logo_opacity": st.session_state.get("ui_logo_opacity_key", 100),
                        "enable_text": ui_enable_text,
                        "text": st.session_state.get("ui_wm_text_key", ""),
                        "font": st.session_state.get("ui_wm_font_key", "Arial"),
                        "size": st.session_state.get("ui_wm_size_key", 50),
                        "color": st.session_state.get("ui_wm_color_key", "#FFFFFF"),
                        "out_color": st.session_state.get("ui_wm_out_color_key", "#000000"),
                        "out_thick": st.session_state.get("ui_wm_out_thick_key", 0),
                        "shad_color": st.session_state.get("ui_wm_shad_color_key", "#000000"),
                        "shad_size": st.session_state.get("ui_wm_shad_size_key", 0),
                        "bold": st.session_state.get("ui_wm_bold_key", False),
                        "italic": st.session_state.get("ui_wm_italic_key", False),
                        "wm_x": st.session_state.get("ui_wm_x_key", 50),
                        "wm_y": st.session_state.get("ui_wm_y_key", 50),
                        "wm_opacity": st.session_state.get("ui_wm_opacity_key", 100)
                    }
                        
                    ar_val = "9:16" if "9:16" in st.session_state.get("ui_ratio_key", "9:16") else "16:9"
                    logo_path = "workspace/logo.png" if os.path.exists("workspace/logo.png") else None
                        
                    try:
                        logo_handler.generate_logo_and_text_preview(logo_path, text_settings, ar_val, preview_path)
                        st.session_state["preview_logo_path"] = preview_path
                    except Exception as e:
                        st.session_state.pop("preview_logo_path", None)
                        st.error(str(e))

            if st.session_state.get("preview_logo_path") and os.path.exists(st.session_state["preview_logo_path"]):
                st.video(os.path.abspath(st.session_state["preview_logo_path"]), autoplay=True, loop=True)

    with tab_ai:
        ui_api_key = st.text_input("🔑 Klucz API Gemini:", type="password", key="ui_api_key_key", on_change=save_all_ui_settings)
            
        st.markdown("---")
        st.markdown("#### 🧠 Tryb Promtowania")
            
        ui_prompt_mode = st.selectbox(
            "Wybierz instrukcję dla AI:", 
            [
                "Precyzyjna (Domyślna - bardziej restrykcyjna)", 
                "Scenarzysta (Story-Driven - łuki narracyjne)",
                "Luźniejsza (Kreatywna - mniej błędów)", 
                "Własny prompt (Zaawansowane)"
            ], 
            key="ui_prompt_mode_key", 
            on_change=save_all_ui_settings,
            help="Precyzyjna: najlepsza do większości materiałów. Scenarzysta: idealna do podcastów i wywiadów — szuka napięcia, zwrotu akcji i emocjonalnego łuku. Luźniejsza: jeśli często napotykasz błąd JSON. Własny: edytujesz zasady ręcznie."
        )
            
        if ui_prompt_mode == "Własny prompt (Zaawansowane)":
            st.text_area(
                "Wpisz swoje własne zasady cięcia wideo:",
                key="ui_custom_prompt_key",
                on_change=save_all_ui_settings,
                height=350,
                help="Wpisz tutaj TYLKO swoje wytyczne dla AI (jakich momentów szukać). 💡 Zdecydowanie polecamy pisać prompt po ANGIELSKU - sztuczna inteligencja radzi sobie z tym o wiele lepiej! Aplikacja automatycznie zajmie się resztą (czasy, format, transkrypcja)."
            )

        st.markdown("---")
        st.markdown("#### 📖 Słownik poprawek Whisper")
        st.caption(
            "Wpisz nazwy firm/produktów, które Whisper myli lub źle pisze. "
            "**Sama nazwa** (lewe pole) = pilnuj wielkości liter. "
            "**Obie wersje** = zamień błędną na poprawną."
        )

        _wgl_c1, _wgl_c2 = st.columns(2)
        with _wgl_c1:
            _wgl_wrong = st.text_input(
                "Nazwa albo błędna wersja",
                key="whisper_glossary_wrong_input",
                placeholder="np. WattCycle albo Humsieng"
            )
        with _wgl_c2:
            _wgl_right = st.text_input(
                "Poprawna wersja, jeśli to poprawka",
                key="whisper_glossary_right_input",
                placeholder="np. Humsienk"
            )

        if st.button("➕ Dodaj i zapisz wpis", use_container_width=True, key="add_whisper_gloss_btn"):
            _w = (_wgl_wrong or "").strip()
            _r = (_wgl_right or "").strip()
            if not _w and not _r:
                st.warning("Wpisz nazwę albo poprawkę.")
            else:
                if _w and _r:
                    _new_entry = _r if _w.casefold() == _r.casefold() else f"{_w} -> {_r}"
                else:
                    _new_entry = _r or _w
                _current = str(st.session_state.get("ui_whisper_glossary_key", "") or "").strip()
                _lines = [l.rstrip() for l in _current.splitlines() if l.strip()]
                if _new_entry.casefold() in {l.casefold() for l in _lines}:
                    st.warning("Taki wpis już jest w słowniku.")
                else:
                    _lines.append(_new_entry)
                    st.session_state["ui_whisper_glossary_key"] = "\n".join(_lines)
                    save_all_ui_settings()
                    st.toast(f"✅ Dodano: {_new_entry}")

        st.text_area(
            "Aktualne wpisy słownika (edytuj ręcznie lub przez przycisk powyżej)",
            key="ui_whisper_glossary_key",
            height=200,
            on_change=save_all_ui_settings,
            help=(
                "Dwa formaty — jeden wpis na linię:\n\n"
                "• Sama nazwa własna (pilnuj wielkich liter):\n"
                "  WattCycle\n"
                "  OpenAI\n\n"
                "• Para poprawka (zamień błędną na poprawną):\n"
                "  Humsieng -> Humsienk\n\n"
                "Linie zaczynające się od # to komentarze.\n"
                "Zmiany działają bez ponownej transkrypcji."
            )
        )

        st.markdown("---")
        st.markdown("#### 🎥 Wirtualna Kamera (AI)")
        ui_face_tracking = st.checkbox("Śledź Twarz Mówcy", key="ui_face_tracking_key", on_change=save_all_ui_settings, help="Włączenie tej funkcji wymusza 100% wypełnienia pionowego ekranu (ignoruje suwak proporcji w zakładce 'Wideo'), aby AI miało miejsce na przesuwanie kamery po obrazie.")
            
        if ui_face_tracking:
            with st.container(border=True):
                st.markdown("**⚙️ Ustawienia Kadrowania**")
                    
                if not FACE_TRACKING_AVAILABLE:
                    st.error(f"⚠️ Face Tracking wyłączony. Błąd: {FACE_TRACKING_ERROR}")
                    
                strategy_options = ["Główny mówca (Skupia na największej twarzy)", "Utrzymuj cel (Śledzi jedną wybraną twarz)"]
                ui_ft_strategy = st.selectbox("Strategia", strategy_options, key="ui_ft_strategy_key", on_change=save_all_ui_settings, help="Główny mówca: dynamicznie przeskakuje na największą widoczną twarz w kadrze. Utrzymuj cel: stara się śledzić jedną, pierwszą namierzoną osobę.")
                    
                col_ft1, col_ft2 = st.columns(2)
                with col_ft1:
                    ui_ft_zoom = st.slider("Zoom", 1.0, 3.0, step=0.1, key="ui_ft_zoom_key", on_change=save_all_ui_settings, help="Zwiększa przybliżenie obrazu na twarz mówcy. 1.0 to całkowity brak przybliżenia.")
                with col_ft2:
                    ui_ft_y_offset = st.slider("Przesunięcie Y (%)", -50, 50, key="ui_ft_y_offset_key", on_change=save_all_ui_settings, help="Koryguje kamerę góra-dół. Jeśli kamera ucina czubek głowy mówcy, przesuń suwak na wartości minusowe.")
                        
                col_ft3, col_ft4 = st.columns(2)
                with col_ft3:
                    ui_ft_smoothness = st.slider("Płynność kamery", 1, 100, key="ui_ft_smoothness_key", on_change=save_all_ui_settings, help="Niska wartość (np. 1) = błyskawiczne, twarde ruchy i sztywne śledzenie. Wysoka wartość (np. 80-100) = bardzo miękkie, powolne i kinowe podążanie kamery (Gimbal).")
                with col_ft4:
                    ui_ft_recheck = st.slider("Reakcja AI (klatki)", 1, 30, key="ui_ft_recheck_key", on_change=save_all_ui_settings, help="Określa, co ile klatek silnik weryfikuje obraz od zera. Dla stabilnego trackingu optymalna wartość to od 6 do 10 klatek.")


# ==============================================================================
# WIDOK STARTOWY
# ==============================================================================
def render_start_view(is_disabled, ui_input_method):
    st.markdown("<h3 style='text-align: center;'>Rozpocznij nowy projekt</h3><br>", unsafe_allow_html=True)
    
    col_i1, col_i2, col_i3 = st.columns([1, 6, 1])
    with col_i2:
        if ui_input_method == "Link z YouTube": 
            video_input = st.text_input("Wklej link do YouTube:", placeholder="https://youtube.com/watch?v=...", disabled=is_disabled)
            
            col_q1, col_q2, col_q3 = st.columns(3)
            with col_q1:
                yt_qualities = ["Najlepsza dostępna", "4K (2160p)", "2K (1440p)", "1080p", "720p", "480p"]
                
                if "ui_yt_qual_key" in st.session_state:
                    saved_qual = st.session_state["ui_yt_qual_key"]
                    del st.session_state["ui_yt_qual_key"]
                else:
                    saved_qual = "1080p"
                qual_idx = yt_qualities.index(saved_qual) if saved_qual in yt_qualities else 3
                
                ui_video_quality = st.selectbox("Rozdzielczość pobierania z YT:", yt_qualities, index=qual_idx, key="ui_yt_qual_key", on_change=save_all_ui_settings, disabled=is_disabled)
            
            col_lang1, col_lang2, col_lang3 = st.columns(3)
            with col_lang1:
                if "ui_whisper_lang_key" in st.session_state:
                    saved_w_lang = st.session_state["ui_whisper_lang_key"]
                    del st.session_state["ui_whisper_lang_key"]
                else:
                    saved_w_lang = "Auto-detekcja"
                w_lang_idx = AVAILABLE_LANGS.index(saved_w_lang) if saved_w_lang in AVAILABLE_LANGS else 0
                
                ui_whisper_lang = st.selectbox("Język wideo (dla precyzji):", AVAILABLE_LANGS, index=w_lang_idx, key="ui_whisper_lang_key", on_change=save_all_ui_settings, disabled=is_disabled)
                
            with col_lang2:
                t_lang_opts = ["Brak (Oryginał)"] + AVAILABLE_LANGS[1:]
                if "ui_target_lang_key" in st.session_state:
                    saved_t_lang = st.session_state["ui_target_lang_key"]
                    del st.session_state["ui_target_lang_key"]
                else:
                    saved_t_lang = "Brak (Oryginał)"
                t_lang_idx = t_lang_opts.index(saved_t_lang) if saved_t_lang in t_lang_opts else 0
                
                ui_target_lang = st.selectbox("Przetłumacz na:", t_lang_opts, index=t_lang_idx, key="ui_target_lang_key", on_change=save_all_ui_settings, disabled=is_disabled)
                
            with col_lang3:
                st.markdown("<div style='margin-top: 32px;'></div>", unsafe_allow_html=True)
                if "ui_yt_subs_key" in st.session_state:
                    saved_yt_subs = st.session_state["ui_yt_subs_key"]
                    del st.session_state["ui_yt_subs_key"]
                else:
                    saved_yt_subs = True
                    
                ui_yt_subs = st.checkbox("Zaciągaj gotowe napisy z YT", value=saved_yt_subs, key="ui_yt_subs_key", on_change=save_all_ui_settings, disabled=is_disabled)
        else:
            if st.button("📁 Wskaż wideo na dysku", use_container_width=True, disabled=is_disabled): 
                path = get_mac_file_path()
                st.session_state.local_file_path = path if path else st.session_state.local_file_path
            video_input = st.session_state.local_file_path
            if video_input: st.success(f"Wybrano plik: {os.path.basename(video_input)}")
            ui_video_quality = "lokalny"
            
            col_lang1, col_lang2 = st.columns(2)
            with col_lang1:
                if "ui_whisper_lang_key" in st.session_state:
                    saved_w_lang = st.session_state["ui_whisper_lang_key"]
                    del st.session_state["ui_whisper_lang_key"]
                else:
                    saved_w_lang = "Auto-detekcja"
                w_lang_idx = AVAILABLE_LANGS.index(saved_w_lang) if saved_w_lang in AVAILABLE_LANGS else 0
                
                ui_whisper_lang = st.selectbox("Język wideo (dla precyzji):", AVAILABLE_LANGS, index=w_lang_idx, key="ui_whisper_lang_key", on_change=save_all_ui_settings, disabled=is_disabled)
                
            with col_lang2:
                t_lang_opts = ["Brak (Oryginał)"] + AVAILABLE_LANGS[1:]
                if "ui_target_lang_key" in st.session_state:
                    saved_t_lang = st.session_state["ui_target_lang_key"]
                    del st.session_state["ui_target_lang_key"]
                else:
                    saved_t_lang = "Brak (Oryginał)"
                t_lang_idx = t_lang_opts.index(saved_t_lang) if saved_t_lang in t_lang_opts else 0
                
                ui_target_lang = st.selectbox("Przetłumacz na:", t_lang_opts, index=t_lang_idx, key="ui_target_lang_key", on_change=save_all_ui_settings, disabled=is_disabled)
            
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚀 ROZPOCZNIJ PROCES I WYGENERUJ SHORTY", type="primary", use_container_width=True, disabled=is_disabled):
            if not st.session_state.get("ui_api_key_key"): 
                st.session_state.error_msg = "Brak Klucza API (Sprawdź zakładkę 'AI' w panelu po lewej stronie)!"
                st.rerun()
            if not video_input or not str(video_input).strip():
                st.session_state.error_msg = "Proszę podać link do YouTube lub wybrać plik wideo z dysku!"
                st.rerun()
                
            st.session_state.video_input_target = video_input
            save_all_ui_settings()
            
            st.session_state.render_settings = get_current_global_render_settings()
            st.session_state.error_msg = None
            st.session_state.is_running = True
            st.session_state.cancel_renders = False
            st.rerun()


@st.fragment
def render_scene_expander_fragment(current_proj, i, short, data, needs_render, pro_expander_id, inv_key, is_pro_expanded, short_element_id, short_render_settings, short_filename, ass_filename):
    with st.expander("✂️ Edytor Scen" + inv_key, expanded=is_pro_expanded):
        st.markdown("Precyzyjnie dostosuj początek i koniec każdej sceny za pomocą suwaków. Słowa podświetlone na **zielono** wejdą w skład finalnego wideo.")
        
        deleted_segs_key = f"deleted_segs_{current_proj}_{i}"
        if deleted_segs_key not in st.session_state:
            st.session_state[deleted_segs_key] = []
        
        current_segments = [s for idx, s in enumerate(short.get('segments', [])) if idx not in st.session_state[deleted_segs_key]]
        can_delete = (len(short.get('segments', [])) - len(st.session_state[deleted_segs_key])) > 1

        new_segments_state = []
        
        for seg_idx, seg in enumerate(short.get('segments', [])):
            if seg_idx in st.session_state[deleted_segs_key]:
                continue

            col_sh1, col_sh2 = st.columns([5, 1])
            with col_sh1:
                st.markdown(f"**🎬 Scena {seg_idx + 1}**")
            with col_sh2:
                st.button("🗑️ Usuń", key=f"del_seg_{current_proj}_{i}_{seg_idx}", disabled=needs_render or not can_delete, use_container_width=True, help="Usuń tę scenę z podglądu", on_click=delete_segment, args=(deleted_segs_key, seg_idx, pro_expander_id))

            buf = 15.0
            min_bound = max(0.0, float(seg.get('start_time', 0.0)) - buf)
            max_bound = float(seg.get('end_time', 0.0)) + buf
            
            if data.get('global_words'):
                abs_max = float(data['global_words'][-1].get('end', max_bound))
                max_bound = min(max_bound, abs_max + 2.0)
                
            if max_bound <= min_bound: max_bound = min_bound + 1.0

            slider_key = f"slider_seg_{current_proj}_{i}_{seg_idx}"
            if slider_key not in st.session_state:
                st.session_state[slider_key] = (float(seg.get('start_time', 0.0)), float(seg.get('end_time', 0.0)))

            active_start, active_end = st.session_state[slider_key]
            
            if active_start > active_end: 
                active_start, active_end = active_end, active_start

            # Odbudowa mapy na potrzeby podglądu, by pokazywać wyedytowane własnoręcznie słowa
            user_edits_for_preview = {f"{w['start']}_{w['end']}": w.get('word', '') for w in short.get('words', [])}
            
            window_words = [w for w in data.get('global_words', []) if w['end'] >= min_bound and w['start'] <= max_bound]
            
            html_text = []
            for w in window_words:
                display_word = user_edits_for_preview.get(f"{w['start']}_{w['end']}", w['word'])
                
                if active_start <= w['start'] and w['end'] <= active_end:
                    html_text.append(f"<b><span style='color:#10b981;'>{display_word}</span></b>")
                else:
                    html_text.append(f"<span style='color:#666666;'>{display_word}</span>")

            st.markdown(f"<div style='line-height:1.6; padding:15px; background:#1e1e1e; border-radius:8px; border: 1px solid #333; margin-bottom: 10px; max-height: 250px; overflow-y: auto; font-size: 15px;'>{' '.join(html_text)}</div>", unsafe_allow_html=True)
            
            st.markdown("<div style='font-size: 13px; color: #9ca3af; margin-bottom: 2px;'>Skok przycisków i precyzyjna korekta:</div>", unsafe_allow_html=True)
            col_b1, col_b2, col_step, col_b3, col_b4 = st.columns([2, 2, 1.5, 2, 2])
            
            with col_step:
                step_val = st.number_input("Skok", min_value=0.1, max_value=5.0, value=0.5, step=0.1, format="%.1f", key=f"step_{current_proj}_{i}_{seg_idx}", label_visibility="collapsed", disabled=needs_render, help="Wielkość skoku (w sekundach)")

            with col_b1:
                st.button(f"⏪ Start -{step_val:.1f}s", key=f"s_m_{slider_key}", disabled=needs_render, use_container_width=True, on_click=adjust_slider, args=(slider_key, max(min_bound, active_start - step_val), active_end, pro_expander_id))
            with col_b2:
                st.button(f"Start +{step_val:.1f}s ⏩", key=f"s_p_{slider_key}", disabled=needs_render, use_container_width=True, on_click=adjust_slider, args=(slider_key, min(active_end, active_start + step_val), active_end, pro_expander_id))
            with col_b3:
                st.button(f"⏪ Koniec -{step_val:.1f}s", key=f"e_m_{slider_key}", disabled=needs_render, use_container_width=True, on_click=adjust_slider, args=(slider_key, active_start, max(active_start, active_end - step_val), pro_expander_id))
            with col_b4:
                st.button(f"Koniec +{step_val:.1f}s ⏩", key=f"e_p_{slider_key}", disabled=needs_render, use_container_width=True, on_click=adjust_slider, args=(slider_key, active_start, min(max_bound, active_end + step_val), pro_expander_id))

            new_val = st.slider(
                "Dostosuj cięcie (sekundy):", 
                min_value=float(min_bound), 
                max_value=float(max_bound), 
                value=st.session_state[slider_key], 
                step=0.1, 
                key=slider_key, 
                disabled=needs_render,
                on_change=set_active_expander,
                args=(pro_expander_id,)
            )
            
            new_segments_state.append({"start_time": new_val[0], "end_time": new_val[1], "text": seg.get("text", "")})
            st.markdown("---")
        
        col_save, col_restore = st.columns(2)    
        with col_save:
            if st.button("💾 Zapisz zmiany w scenach i przegeneruj", key=f"save_scenes_{current_proj}_{i}", disabled=needs_render, type="primary", use_container_width=True):
                safe_trash_file(short_filename)
                safe_trash_file(ass_filename)
                
                data["ai_outputs"][i] = update_segments_and_resync_words(short, new_segments_state, data.get('global_words', []))
                
                data["ai_outputs"][i]["render_settings"] = get_current_global_render_settings()
                
                data["ai_outputs"][i]["force_re_render"] = True
                data["ai_outputs"][i]["render_id"] = int(time.time())
                if "file_stats" in data["ai_outputs"][i]:
                    del data["ai_outputs"][i]["file_stats"]
                    
                keys_to_delete = [k for k in st.session_state.keys() if k.startswith(f"slider_seg_{current_proj}_{i}_")]
                for k in keys_to_delete: del st.session_state[k]
                
                st.session_state[deleted_segs_key] = []
                    
                with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                st.session_state.cancel_renders = False
                set_scroll_and_expander(short_element_id, pro_expander_id) 
                st.rerun()

        with col_restore:
            has_backup_or_deleted = "backup_segments" in short or len(st.session_state.get(deleted_segs_key, [])) > 0
            if st.button("🔙 Przywróć oryginalne sceny", key=f"restore_scenes_{current_proj}_{i}", disabled=needs_render or not has_backup_or_deleted, use_container_width=True):
                safe_trash_file(short_filename)
                safe_trash_file(ass_filename)
                
                original_segs = copy.deepcopy(short.get("backup_segments", short.get("segments", [])))
                
                data["ai_outputs"][i] = update_segments_and_resync_words(short, original_segs, data.get('global_words', []))
                
                data["ai_outputs"][i]["render_settings"] = get_current_global_render_settings()
                
                data["ai_outputs"][i]["force_re_render"] = True
                data["ai_outputs"][i]["render_id"] = int(time.time())
                if "file_stats" in data["ai_outputs"][i]:
                    del data["ai_outputs"][i]["file_stats"]
                    
                keys_to_delete = [k for k in st.session_state.keys() if k.startswith(f"slider_seg_{current_proj}_{i}_")]
                for k in keys_to_delete: del st.session_state[k]
                
                st.session_state[deleted_segs_key] = []
                    
                with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                st.session_state.cancel_renders = False
                set_scroll_and_expander(short_element_id, pro_expander_id)
                st.rerun()

# ==============================================================================
# WIDOK EDYTORA KRYTYCZNEGO (PRO EDYTOR SHORTÓW)
# ==============================================================================
def render_editor(data, current_proj):
    if st.session_state.get("_dub_queue_project_seen") != current_proj:
        clear_project_dub_queue(current_proj)
        st.session_state["_dub_queue_project_seen"] = current_proj
        st.session_state.cancel_renders = False

    dirty_backup = False
    for sh in data.get("ai_outputs", []):
        if "backup_segments" not in sh:
            sh["backup_segments"] = copy.deepcopy(sh.get("segments", []))
            dirty_backup = True
        if "dub_render_request" in sh:
            sh.pop("dub_render_request", None)
            dirty_backup = True
    if dirty_backup:
        with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: 
            json.dump(data, f)
            
    col_hdr1, col_hdr2 = st.columns([5, 1])
    with col_hdr1:
        st.subheader(f"📂 Aktywny projekt: {data.get('display_name', current_proj)}")
    with col_hdr2:
        if st.button("❌ Zamknij projekt", use_container_width=True):
            kill_active_dubbing_processes()
            clear_project_dub_queue(current_proj)
            st.session_state.shorts_data = None
            st.session_state.current_project = None
            st.session_state.cancel_renders = False
            st.session_state.error_msg = None
            st.rerun()
            
    st.markdown("<br>", unsafe_allow_html=True)
            
    render_settings = data.get("render_settings", {})
    for key, value in st.session_state.render_settings.items() if hasattr(st.session_state, "render_settings") else {}.items():
         if key not in render_settings:
            render_settings[key] = value

    videos_to_render = []
    for idx_render, short_render_data in enumerate(data["ai_outputs"]):
        base_safe_t_render = re.sub(r'[^\w\s-]', '', short_render_data.get('title', f"short_{idx_render}")).strip().replace(" ", "_") or f"short_{idx_render}"
        safe_t_render = f"{idx_render}_{base_safe_t_render}"
        proj_dir_render = os.path.join("workspace", "sessions", current_proj, "shorts")
        
        render_id = short_render_data.get("render_id", "")
        if render_id: safe_t_render += f"_{render_id}"
            
        sf_render = os.path.join(proj_dir_render, f"{safe_t_render}.mp4")
        
        needs_render = short_render_data.get("force_re_render", False) or not os.path.exists(sf_render)
        if needs_render or get_dub_render_request(current_proj, idx_render):
            videos_to_render.append(idx_render)

    col_glob1, col_glob2 = st.columns([2, 1])
    with col_glob2:
        if st.button("🔄 Zastosuj styl z paska bocznego do wszystkich", use_container_width=True, type="primary"):
            new_global_settings = get_current_global_render_settings()
            
            curr_cfg = load_settings()
            curr_cfg.update(new_global_settings)
            save_settings(curr_cfg)
            
            for idx, short_data in enumerate(data["ai_outputs"]):
                if is_dubbing_enabled(new_global_settings):
                    queue_dub_render(current_proj, idx, new_global_settings, int(time.time()) + idx)
                    continue

                base_safe_t = re.sub(r'[^\w\s-]', '', short_data.get('title', f"short_{idx}")).strip().replace(" ", "_") or f"short_{idx}"
                old_render_id = short_data.get("render_id", "")
                old_safe_t = f"{idx}_{base_safe_t}_{old_render_id}" if old_render_id else f"{idx}_{base_safe_t}"
                
                sf = os.path.join("workspace", "sessions", current_proj, "shorts", f"{old_safe_t}.mp4")
                af = os.path.join("workspace", "sessions", current_proj, "shorts", f"{old_safe_t}.ass")
                
                safe_trash_file(sf)
                safe_trash_file(af)
                
                short_data["render_settings"] = new_global_settings
                short_data["force_re_render"] = True
                short_data["render_id"] = int(time.time()) + idx 
                if "file_stats" in short_data:
                    del short_data["file_stats"]
                    
            with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
            st.session_state.cancel_renders = False
            st.rerun()
            
    if videos_to_render:
        if st.session_state.get("cancel_renders", False):
            if st.button("▶️ Wznów renderowanie listy", type="primary", use_container_width=True):
                st.session_state.cancel_renders = False
                st.session_state.error_msg = None
                st.rerun()
        else:
            if st.button("🛑 Zatrzymaj listę renderowania", type="primary", use_container_width=True):
                kill_active_dubbing_processes()
                clear_project_dub_queue(current_proj)
                st.session_state.cancel_renders = True
                st.session_state.error_msg = None
                st.rerun()

    pending_renders = []

    for i, short in enumerate(data["ai_outputs"]):
        short_element_id = f"short_container_{current_proj}_{i}"
        pro_expander_id = f"pro_expander_{current_proj}_{i}"
        sub_expander_id = f"sub_expander_{current_proj}_{i}"
        trans_expander_id = f"trans_expander_{current_proj}_{i}"
        
        st.markdown(f"<div id='{short_element_id}' style='position: relative; top: -60px;'></div>", unsafe_allow_html=True)
        
        with st.container(border=True): 
            status_header_ph = st.empty()
            prog_bar_ph = st.empty()
            status_text_ph = st.empty()
            divider_ph = st.empty()
            
            raw_title = short.get('title', f'Short_{i}').strip()
            base_safe_title = re.sub(r'[^\w\s-]', '', raw_title).strip().replace(" ", "_") or f"short_{i}"
            ascii_title = unicodedata.normalize('NFKD', base_safe_title).encode('ascii', 'ignore').decode('utf-8')
            clean_dl_title = re.sub(r'[^\w\s-]', "", ascii_title).strip().replace(" ", "_")
            if not clean_dl_title: clean_dl_title = f"Short_{i}"
            
            render_id = short.get("render_id", "")
            safe_title = f"{i}_{clean_dl_title}_{render_id}" if render_id else f"{i}_{clean_dl_title}"
            fav_id = f"{current_proj}_{i}"
            
            safe_export_name = re.sub(r'[/\\:]', '_', raw_title)
            if not safe_export_name: safe_export_name = f"Short_{i}"
            
            project_folder = os.path.join("workspace", "sessions", current_proj, "shorts")
            os.makedirs(project_folder, exist_ok=True)
            
            initialize_short_words(short, data.get('global_words', []))

            base_short_render_settings = copy.deepcopy(short.get("render_settings", render_settings))

            short_render_settings = copy.deepcopy(base_short_render_settings)
            short_render_settings["audio_mode"] = "Oryginalne audio"
            version_folder = ""
            short_filename = os.path.join(project_folder, f"{safe_title}.mp4")
            ass_filename = os.path.join(project_folder, f"{safe_title}.ass")
            xml_filename = os.path.join(project_folder, f"{safe_title}.xml")
            
            legacy_short_filename = os.path.join(project_folder, f"{i}_{base_safe_title}.mp4")
            if os.path.exists(legacy_short_filename) and not os.path.exists(short_filename) and not render_id:
                os.rename(legacy_short_filename, short_filename)
                if os.path.exists(legacy_short_filename.replace('.mp4', '.ass')): os.rename(legacy_short_filename.replace('.mp4', '.ass'), ass_filename)
            
            # --- GLOBALNY BEZPIECZNIK LOGO ---
            # Gwarantuje, że każdy short wchodzący do pętli będzie miał przypisaną ścieżkę do obrazka
            if isinstance(short_render_settings, dict):
                short_render_settings["logo_path"] = "workspace/logo.png" if os.path.exists("workspace/logo.png") else None
            # ---------------------------------
                
            aspect_ratio_req = short_render_settings.get("aspect_ratio", "9:16")
            export_res_req = short_render_settings.get("export_res", "Zgodna ze źródłem")
            
            xml_source_path = os.path.abspath(data.get("video_file", data.get("original_source_path")))
            _, src_ext = os.path.splitext(xml_source_path)
            if not src_ext: src_ext = ".mp4"
            
            source_dl_name = f"{safe_export_name}_src{src_ext}"
            xml_str = generate_xml_content(short, source_dl_name, xml_source_path, aspect_ratio_req, export_res_req, fps=30)
            with open(xml_filename, "w", encoding="utf-8") as f: f.write(xml_str)
            
            needs_render = short.get("force_re_render", False) or not os.path.exists(short_filename)
            
            if needs_render:
                status_header_ph.markdown(f"#### ⏳ Oczekuje na generowanie: Short {i+1} z {len(data['ai_outputs'])}")
                prog_bar_ph.progress(0.0)
                status_text_ph.markdown("**Status:** W kolejce...")
                divider_ph.markdown("---")
                
                pending_renders.append({
                    "i": i,
                    "short": short,
                    "short_filename": short_filename,
                    "ass_filename": ass_filename,
                    "version_folder": version_folder,
                    "short_render_settings": short_render_settings,
                    "status_header_ph": status_header_ph,
                    "prog_bar_ph": prog_bar_ph,
                    "status_text_ph": status_text_ph,
                    "scroll_id": short_element_id,
                    "active_expander_id": st.session_state.get("active_expander")
                })

            dub_request = get_dub_render_request(current_proj, i)
            if isinstance(dub_request, dict):
                req_settings = copy.deepcopy(dub_request.get("settings", {}))
                req_lang = req_settings.get("dub_target_lang", "Angielski")
                req_slug = language_slug(req_lang)
                req_render_id = dub_request.get("render_id", int(time.time()))
                req_dir = os.path.join("workspace", "sessions", current_proj, "short_versions", f"short_{i:02d}", req_slug)
                os.makedirs(req_dir, exist_ok=True)
                req_stem = f"{safe_title}_{req_slug}_{req_render_id}"
                req_mp4 = dub_request.get("output_video_path") or os.path.join(req_dir, f"{req_stem}.mp4")
                req_ass = dub_request.get("output_ass_path") or os.path.join(req_dir, f"{req_stem}.ass")
                req_short = copy.deepcopy(dub_request.get("short_data") or short)

                status_header_ph.markdown(f"#### ⏳ Oczekuje na generowanie wersji: Short {i+1} z {len(data['ai_outputs'])} - {req_lang}")
                prog_bar_ph.progress(0.0)
                status_text_ph.markdown("**Status:** W kolejce wersji językowej...")
                divider_ph.markdown("---")
                pending_renders.append({
                    "i": i,
                    "short": req_short,
                    "short_filename": req_mp4,
                    "ass_filename": req_ass,
                    "version_folder": req_dir,
                    "short_render_settings": req_settings,
                    "status_header_ph": status_header_ph,
                    "prog_bar_ph": prog_bar_ph,
                    "status_text_ph": status_text_ph,
                    "scroll_id": f"version_{current_proj}_{i}_{req_slug}",
                    "active_expander_id": st.session_state.get("active_expander"),
                    "is_dub_version": True,
                    "version_language": req_lang,
                    "reuse_audio_path": dub_request.get("reuse_audio_path", ""),
                    "skip_auto_translation": dub_request.get("skip_auto_translation", False),
                })
            
            col_vid, col_info = st.columns([1.5, 3])
            with col_vid:
                if not needs_render and os.path.exists(short_filename):
                    exp_res = short_render_settings.get("export_res", "Nieznana")
                    
                    if "file_stats" not in short or "resolution" not in short.get("file_stats", {}):
                        short["file_stats"] = get_video_stats(short_filename)
                        with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                        
                    size_mb = short["file_stats"].get("size_mb", 0.0)
                    real_bitrate = short["file_stats"].get("bitrate", 0.0)
                    real_codec = short["file_stats"].get("codec", "")
                    real_res = short["file_stats"].get("resolution", exp_res)
                    if not real_res: real_res = exp_res
                    
                    st.caption(f"📏 {real_res} {real_codec} | 🎞️ {real_bitrate:.2f} Mbps | 💾 {size_mb:.2f} MB")
                    
                    st.video(os.path.abspath(short_filename))

                    st.markdown("<div style='margin-top: 15px; margin-bottom: 5px;'><b>Pobieranie plików (Przez przeglądarkę):</b></div>", unsafe_allow_html=True)
                    col_d1, col_d2, col_d3 = st.columns(3)
                    
                    with col_d1:
                        st.download_button(
                            "💾 Pobierz Short", 
                            data=lambda p=short_filename: open(p, "rb").read(), 
                            file_name=f"{safe_export_name}.mp4", 
                            mime="video/mp4",
                            key=f"btn_save_mp4_{current_proj}_{i}", 
                            use_container_width=True, 
                            help="Pobiera wyrenderowany plik na dysk."
                        )
                            
                    with col_d2:
                        st.download_button(
                            "🎬 Pobierz XML", 
                            data=xml_str.encode("utf-8"),
                            file_name=f"{safe_export_name}.xml",
                            mime="application/xml",
                            key=f"btn_save_xml_{current_proj}_{i}", 
                            use_container_width=True, 
                            help="Pobiera kod XML dla DaVinci Resolve."
                        )
                            
                    with col_d3:
                        btn_label = "📥 Pobierz Proxy" if "_proxy_" in xml_source_path else "📥 Pobierz Źródło"
                        st.download_button(
                            btn_label, 
                            data=lambda p=xml_source_path: open(p, "rb").read(), 
                            file_name=source_dl_name,
                            mime="video/mp4",
                            key=f"btn_save_src_{current_proj}_{i}", 
                            use_container_width=True, 
                            help="Pobiera wideo do montażu. Zapisz je RAZEM z plikiem XML w jednym folderze, aby DaVinci automatycznie je odnalazł!"
                        )
                        
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🔄 Przegeneruj z obecnym stylem", key=f"re_style_{current_proj}_{i}", use_container_width=True):
                        new_settings = get_current_global_render_settings()
                        
                        curr_cfg = load_settings()
                        curr_cfg.update(new_settings)
                        save_settings(curr_cfg)

                        if is_dubbing_enabled(new_settings):
                            queue_dub_render(current_proj, i, new_settings)
                            st.session_state.scroll_target = short_element_id
                        else:
                            safe_trash_file(short_filename)
                            safe_trash_file(ass_filename)
                            data["ai_outputs"][i]["render_settings"] = new_settings
                            data["ai_outputs"][i]["force_re_render"] = True
                            data["ai_outputs"][i]["render_id"] = int(time.time())
                            if "file_stats" in data["ai_outputs"][i]:
                                del data["ai_outputs"][i]["file_stats"]
                            st.session_state.scroll_target = short_element_id
                            
                        with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                        st.session_state.cancel_renders = False 
                        st.session_state.active_expander = None
                        st.rerun()
                else:
                    with st.container(height=350, border=True):
                        st.markdown(
                            """
                            <div style='height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #9ca3af;'>
                                <h3 style='margin: 0; color: #60a5fa;'>⚙️ W kolejce...</h3>
                                <p style='font-size: 14px;'>Wideo jest w trakcie przetwarzania lub oczekuje na swoją kolej.</p>
                            </div>
                            """, 
                            unsafe_allow_html=True
                        )
            
            with col_info:
                col_score, col_fav = st.columns([5, 1])
                with col_score:
                    st.markdown(f'<div style="font-size: 18px; font-weight: bold; margin-bottom: 4px; color: #e5e7eb;">Short {i+1} z {len(data["ai_outputs"])}</div><div class="score-badge">Viral Score 🔥 {short.get("score", 90)}/100</div>', unsafe_allow_html=True)
                with col_fav:
                    if is_favorite(fav_id):
                        if st.button("❤️", key=f"rm_fav_proj_{fav_id}", help="Usuń z ulubionych"):
                            remove_from_favorites(fav_id)
                            st.session_state.scroll_target = short_element_id
                            st.rerun()
                    else:
                        if st.button("🤍", key=f"add_fav_proj_{fav_id}", help="Dodaj do ulubionych"):
                            if os.path.exists(short_filename) and not needs_render:
                                short_to_save = copy.deepcopy(short)
                                short_to_save["render_settings"] = short_render_settings
                                add_to_favorites(fav_id, short_filename, short_to_save)
                                st.session_state.scroll_target = short_element_id
                                st.rerun()
                            else:
                                st.toast("Najpierw poczekaj na wygenerowanie wideo, aby móc dodać je do ulubionych!", icon="⚠️")
                
                st.markdown("**Tytuł:**")
                st.code(short.get('title', 'Brak tytułu'), language="text", wrap_lines=True)
                
                st.markdown("**Opis (Hook):**")
                clean_hook = short.get('hook_text', 'Brak opisu').strip().strip('"').strip("'")
                st.code(clean_hook, language="text", wrap_lines=True)
                
                col_tags1, col_tags2 = st.columns(2)
                with col_tags1:
                    hashtags_str = short.get('hashtags', '')
                    if hashtags_str:
                        st.markdown("**Hashtagi:**")
                        st.code(hashtags_str, language="text", wrap_lines=True)
                with col_tags2:
                    yt_tags_str = short.get('yt_tags', '')
                    if yt_tags_str:
                        st.markdown("**Tagi:**")
                        st.code(yt_tags_str, language="text", wrap_lines=True)
                
                inv_key = "\u200B" * i
                with st.expander("Szczegóły cięć" + inv_key):
                    for seg in short.get('segments', []): 
                        st.caption(f"[{seconds_to_timecode(seg.get('start_time', 0.0))} - {seconds_to_timecode(seg.get('end_time', 0.0))}] {seg.get('text', '')}")
                
                # =========================================================================
                # TUTAJ ZACZYNA SIĘ NOWA ZAKŁADKA "PRO EDYTORA SCEN"
                # =========================================================================
                is_pro_expanded = (st.session_state.get("active_expander") == pro_expander_id)
                
                render_scene_expander_fragment(current_proj, i, short, data, needs_render, pro_expander_id, inv_key, is_pro_expanded, short_element_id, short_render_settings, short_filename, ass_filename)

                # =========================================================================

                if short_render_settings.get("subs"):
                    is_sub_expanded = (st.session_state.get("active_expander") == sub_expander_id)
                    with st.expander("📝 Edytor Napisów i Czasów" + inv_key, expanded=is_sub_expanded):
                        st.caption("Zmień treść słowa lub jego czas. Aby zachować zmiany i wyrenderować shorta na nowo, kliknij przycisk poniżej. Chcesz złamać wyraz w innej linijce? Dodaj między wyrazami enter klawiszem!")
                        with st.form(key=f"form_subs_{current_proj}_{i}", border=False):
                            # Używamy render_id w kluczu, by wymusić odświeżenie komponentu React po edycji scen/tłumaczeniu!
                            comp_key = f"subtitle_ed_{current_proj}_{i}_{short.get('render_id', '0')}"
                            edited_words_raw = subtitle_editor_component(words=short.get('words', []), key=comp_key)
                            edited_words = edited_words_raw if edited_words_raw is not None else short.get('words', [])
                            
                            col_sub_btn1, col_sub_btn2 = st.columns([5, 2])
                            with col_sub_btn1:
                                submitted_subs = st.form_submit_button("💾 Zapisz poprawki napisów i przegeneruj wideo", disabled=needs_render, use_container_width=True)
                            with col_sub_btn2:
                                restore_subs = st.form_submit_button("🔙 Przywróć oryginał", disabled=needs_render, use_container_width=True, help="Odrzuca ręczne zmiany w napisach i przywraca słowa z oryginalnego wideo dopasowane do obecnych scen.")
                            
                        if submitted_subs:
                            sanitized_words = []
                            
                            if hasattr(edited_words, "to_dict"):
                                rows = edited_words.to_dict("records")
                            else:
                                rows = edited_words
                                
                            for w in rows:
                                try:
                                    s_val = float(w.get('start', 0.0)) if w.get('start') is not None else 0.0
                                    e_val = float(w.get('end', 0.0)) if w.get('end') is not None else 0.0
                                    w_val = str(w.get('word', '')).strip() if w.get('word') is not None else ""
                                    if w_val:
                                        sanitized_words.append({'word': w_val, 'start': s_val, 'end': e_val})
                                except Exception as err: 
                                    st.error(f"Błąd w edytorze napisów: {err}")
                                    continue
                                
                            safe_trash_file(short_filename)
                            safe_trash_file(ass_filename)
                            
                            data["ai_outputs"][i]['words'] = sanitized_words
                            
                            # Zabezpieczenie logo i zachowanie opcji renderowania!
                            data["ai_outputs"][i]["render_settings"] = get_current_global_render_settings()
                            
                            data["ai_outputs"][i]["force_re_render"] = True
                            data["ai_outputs"][i]["render_id"] = int(time.time())
                            if "file_stats" in data["ai_outputs"][i]:
                                del data["ai_outputs"][i]["file_stats"]
                                
                            with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                            st.session_state.cancel_renders = False
                            set_scroll_and_expander(short_element_id, sub_expander_id)
                            st.rerun()

                        elif restore_subs:
                            safe_trash_file(short_filename)
                            safe_trash_file(ass_filename)
                            
                            # Magia! Resynchronizujemy napisy na podstawie głównych globalnych słów z zachowaniem aktualnych scen!
                            data["ai_outputs"][i] = update_segments_and_resync_words(short, short.get('segments', []), data.get('global_words', []))
                            
                            data["ai_outputs"][i]["render_settings"] = get_current_global_render_settings()
                            
                            data["ai_outputs"][i]["force_re_render"] = True
                            data["ai_outputs"][i]["render_id"] = int(time.time())
                            if "file_stats" in data["ai_outputs"][i]:
                                del data["ai_outputs"][i]["file_stats"]
                                
                            with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                            st.session_state.cancel_renders = False
                            set_scroll_and_expander(short_element_id, sub_expander_id)
                            st.rerun()

                    is_trans_expanded = (st.session_state.get("active_expander") == trans_expander_id)
                    with st.expander("🌍 Przetłumacz / Przywróć (AI)" + inv_key, expanded=is_trans_expanded):
                        with st.form(key=f"form_trans_{current_proj}_{i}", border=False):
                            current_api_key = st.session_state.get("ui_api_key_key", "")
                            tr_lang = st.selectbox(
                                "Wybierz język docelowy:", 
                                ["Polski", "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Chiński"], 
                                key=f"tr_lang_{current_proj}_{i}", 
                                disabled=needs_render
                            )
                            
                            col_tr1, col_tr2 = st.columns(2)
                            with col_tr1:
                                tr_btn = st.form_submit_button("🌐 Przetłumacz i przegeneruj wideo", disabled=needs_render, use_container_width=True)
                            with col_tr2:
                                if 'original_words' in short:
                                    restore_btn = st.form_submit_button("🔙 Przywróć oryginał", disabled=needs_render, use_container_width=True)
                                else:
                                    restore_btn = False

                        if tr_btn:
                            if os.path.exists(short_filename):
                                with st.spinner("Tłumaczenie i synchronizacja czasowa przez AI..."):
                                    success = translate_short_with_gemini(short, tr_lang, current_api_key)
                                    if success:
                                        safe_trash_file(short_filename)
                                        safe_trash_file(ass_filename)
                                        
                                        # Zabezpieczenie logo i zachowanie opcji renderowania!
                                        data["ai_outputs"][i]["render_settings"] = get_current_global_render_settings()
                                        
                                        data["ai_outputs"][i]["force_re_render"] = True
                                        data["ai_outputs"][i]["render_id"] = int(time.time())
                                        if "file_stats" in data["ai_outputs"][i]:
                                            del data["ai_outputs"][i]["file_stats"]
                                        with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                                        st.session_state.cancel_renders = False
                                        set_scroll_and_expander(short_element_id, trans_expander_id)
                                        st.rerun()
                                    else:
                                        st.error("Błąd podczas tłumaczenia przez AI. Sprawdź czy wprowadzony klucz API jest poprawny w lewym panelu.")
                        
                        if restore_btn:
                            if os.path.exists(short_filename):
                                with st.spinner("Przywracanie i generowanie..."):
                                    short['words'] = copy.deepcopy(short['original_words'])
                                    if 'original_segments' in short:
                                        short['segments'] = copy.deepcopy(short['original_segments'])
                                    short['title'] = short.get('original_title', short.get('title', ''))
                                    short['hook_text'] = short.get('original_hook_text', short.get('hook_text', ''))
                                    short['hashtags'] = short.get('original_hashtags', short.get('hashtags', ''))
                                    short['yt_tags'] = short.get('original_yt_tags', short.get('yt_tags', ''))
                                    
                                    safe_trash_file(short_filename)
                                    safe_trash_file(ass_filename)
                                    
                                    # Zabezpieczenie logo i zachowanie opcji renderowania!
                                    short["render_settings"] = get_current_global_render_settings()
                                    
                                    short["force_re_render"] = True
                                    short["render_id"] = int(time.time())
                                    if "file_stats" in short:
                                        del short["file_stats"]
                                        
                                    with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                                    st.session_state.cancel_renders = False
                                    set_scroll_and_expander(short_element_id, trans_expander_id)
                                    st.rerun()

            versions_below = [v for v in list_rendered_versions(current_proj, i) if v.get("video_path") and os.path.exists(v.get("video_path", ""))]
            for v_idx, version in enumerate(versions_below):
                v_lang = version.get("language", "Wersja")
                v_path = version.get("video_path", "")
                v_slug = language_slug(v_lang)
                v_dir = version.get("dir", "")
                version_anchor = f"version_{current_proj}_{i}_{v_slug}"
                version_short = copy.deepcopy(short)
                version_short.update(version.get("short_data") or {})
                if v_dir:
                    load_cached_translation(version_short, v_lang, v_dir)
                st.markdown(f"<div id='{version_anchor}' style='position: relative; top: -70px;'></div>", unsafe_allow_html=True)
                st.markdown("---")
                with st.container(border=True):
                    v_stats = get_video_stats(v_path)
                    col_vvid, col_vmeta = st.columns([1.5, 3])
                    with col_vvid:
                        st.caption(
                            f"📏 {v_stats.get('resolution') or 'Wideo'} {v_stats.get('codec') or ''} | "
                            f"🎞️ {v_stats.get('bitrate', 0.0):.2f} Mbps | 💾 {v_stats.get('size_mb', 0.0):.2f} MB"
                        )
                        st.video(os.path.abspath(v_path))

                        col_vd1, col_vd2, col_vd3 = st.columns(3)
                        with col_vd1:
                            st.download_button(
                                f"💾 Pobierz Short",
                                data=lambda p=v_path: open(p, "rb").read(),
                                file_name=make_download_filename(
                                    version_short.get("title", f"Short {i+1}"),
                                    ".mp4",
                                    fallback=f"Short {i+1}"
                                ),
                                mime="video/mp4",
                                key=f"btn_full_version_{current_proj}_{i}_{v_idx}",
                                use_container_width=True,
                                help=f"Pobiera wersję: {v_lang}."
                            )
                        with col_vd2:
                            v_ass_path = os.path.splitext(v_path)[0] + ".ass"
                            if os.path.exists(v_ass_path):
                                st.download_button(
                                    "🎬 Pobierz XML",
                                    data=lambda p=v_ass_path: open(p, "rb").read(),
                                    file_name=os.path.basename(v_ass_path),
                                    mime="text/plain",
                                    key=f"btn_version_ass_{current_proj}_{i}_{v_slug}_{v_idx}",
                                    use_container_width=True,
                                    help="Pobiera plik napisów tej wersji językowej."
                                )
                            else:
                                st.button("🎬 Pobierz XML", key=f"btn_version_ass_missing_{current_proj}_{i}_{v_slug}_{v_idx}", disabled=True, use_container_width=True, help="Plik napisów tej wersji nie jest dostępny.")
                        with col_vd3:
                            if version.get("audio_path") and os.path.exists(version.get("audio_path", "")):
                                st.download_button(
                                    "🎙️ Pobierz Audio",
                                    data=lambda p=version.get("audio_path", ""): open(p, "rb").read(),
                                    file_name=os.path.basename(version.get("audio_path", "")),
                                    mime="audio/mp4",
                                    key=f"btn_version_audio_{current_proj}_{i}_{v_slug}_{v_idx}",
                                    use_container_width=True,
                                    help="Pobiera gotową ścieżkę audio dubbingu."
                                )
                            else:
                                st.button("🎙️ Pobierz Audio", key=f"btn_version_audio_missing_{current_proj}_{i}_{v_slug}_{v_idx}", disabled=True, use_container_width=True, help="Plik audio tej wersji nie jest dostępny.")

                        st.markdown("<br>", unsafe_allow_html=True)
                        confirm_delete_key = f"confirm_delete_version_{current_proj}_{i}_{v_slug}_{v_idx}"
                        if st.button("🗑️ Usuń wersję", key=f"delete_version_{current_proj}_{i}_{v_slug}_{v_idx}", use_container_width=True, help="Usuwa tylko tę wersję językową. Oryginalny short zostaje bez zmian."):
                            st.session_state[confirm_delete_key] = True
                            st.session_state.scroll_target = version_anchor
                            st.rerun()

                        if st.session_state.get(confirm_delete_key, False):
                            st.warning(f"Na pewno usunąć wersję: {v_lang}? Oryginalny short zostanie bez zmian.")
                            col_confirm_delete, col_cancel_delete = st.columns(2)
                            with col_confirm_delete:
                                if st.button("Tak, usuń tę wersję", key=f"confirm_delete_version_yes_{current_proj}_{i}_{v_slug}_{v_idx}", type="primary", use_container_width=True):
                                    if v_dir and os.path.exists(v_dir):
                                        shutil.rmtree(v_dir, ignore_errors=True)
                                    st.session_state[confirm_delete_key] = False
                                    st.session_state.scroll_target = short_element_id
                                    st.rerun()
                            with col_cancel_delete:
                                if st.button("Anuluj", key=f"confirm_delete_version_no_{current_proj}_{i}_{v_slug}_{v_idx}", use_container_width=True):
                                    st.session_state[confirm_delete_key] = False
                                    st.session_state.scroll_target = version_anchor
                                    st.rerun()

                        if st.button("🎙️ Wygeneruj audio od nowa", key=f"re_audio_version_{current_proj}_{i}_{v_slug}_{v_idx}", use_container_width=True, help="Generuje dubbing tej wersji od nowa. Użyj tego, gdy głos lub język wyszedł źle."):
                            current_style = get_current_global_render_settings()
                            version_settings = copy.deepcopy(version.get("settings") or {})
                            version_settings.update(current_style)
                            version_settings["audio_mode"] = version_settings.get("audio_mode") or "Czysty dubbing (usuń oryginalny głos)"
                            if version_settings.get("audio_mode") == "Oryginalne audio":
                                version_settings["audio_mode"] = "Czysty dubbing (usuń oryginalny głos)"
                            version_settings["dub_target_lang"] = current_style.get("dub_target_lang", v_lang) or v_lang
                            output_ass_path = os.path.splitext(v_path)[0] + ".ass"
                            queue_dub_render(
                                current_proj,
                                i,
                                version_settings,
                                short_data=version_short,
                                output_video_path=v_path,
                                output_ass_path=output_ass_path,
                            )
                            st.session_state.cancel_renders = False
                            st.session_state.scroll_target = version_anchor
                            st.rerun()

                        if st.button("🔄 Przegeneruj z obecnym stylem", key=f"re_style_version_{current_proj}_{i}_{v_slug}_{v_idx}", use_container_width=True, help="Odświeża wygląd tej wersji według ustawień z paska bocznego. Jeśli język audio się nie zmienił, użyje istniejącego dubbingu bez ponownego generowania audio."):
                            current_style = get_current_global_render_settings()
                            current_lang = current_style.get("dub_target_lang", v_lang)
                            version_settings = copy.deepcopy(version.get("settings") or {})
                            existing_audio_mode = version_settings.get("audio_mode") or "Czysty dubbing (usuń oryginalny głos)"
                            version_settings.update(current_style)
                            if version_settings.get("audio_mode") == "Oryginalne audio":
                                version_settings["audio_mode"] = existing_audio_mode
                            if version_settings.get("audio_mode") == "Oryginalne audio":
                                version_settings["audio_mode"] = "Czysty dubbing (usuń oryginalny głos)"
                            version_settings["dub_target_lang"] = current_lang
                            output_ass_path = os.path.splitext(v_path)[0] + ".ass"
                            if current_lang == v_lang and version.get("audio_path") and os.path.exists(version.get("audio_path", "")):
                                queue_dub_render(
                                    current_proj,
                                    i,
                                    version_settings,
                                    short_data=version_short,
                                    reuse_audio_path=version.get("audio_path", ""),
                                    output_video_path=v_path,
                                    output_ass_path=output_ass_path,
                                )
                            else:
                                queue_dub_render(current_proj, i, version_settings, short_data=version_short)
                            st.session_state.cancel_renders = False
                            st.session_state.scroll_target = version_anchor
                            st.rerun()
                    with col_vmeta:
                        v_fav_id = f"{current_proj}_{i}_{v_slug}"
                        col_vscore, col_vfav = st.columns([5, 1])
                        with col_vscore:
                            st.markdown(
                                f'<div style="font-size: 18px; font-weight: bold; margin-bottom: 4px; color: #e5e7eb;">Short {i+1} z {len(data["ai_outputs"])} - {v_lang}</div>'
                                f'<div class="score-badge">Viral Score 🔥 {version_short.get("score", short.get("score", 90))}/100</div>',
                                unsafe_allow_html=True
                            )
                        with col_vfav:
                            if is_favorite(v_fav_id):
                                if st.button("❤️", key=f"rm_fav_version_{v_fav_id}_{v_idx}", help="Usuń tę wersję z ulubionych"):
                                    remove_from_favorites(v_fav_id)
                                    st.session_state.scroll_target = version_anchor
                                    st.rerun()
                            else:
                                if st.button("🤍", key=f"add_fav_version_{v_fav_id}_{v_idx}", help="Dodaj tę wersję do ulubionych"):
                                    if os.path.exists(v_path):
                                        version_to_save = copy.deepcopy(version_short)
                                        version_to_save["render_settings"] = copy.deepcopy(version.get("settings") or short_render_settings)
                                        version_to_save["language"] = v_lang
                                        version_to_save["version_language"] = v_lang
                                        version_to_save["source_project_id"] = current_proj
                                        version_to_save["source_short_index"] = i
                                        add_to_favorites(v_fav_id, v_path, version_to_save)
                                        st.session_state.scroll_target = version_anchor
                                        st.rerun()
                                    else:
                                        st.toast("Najpierw poczekaj na wygenerowanie tej wersji, aby móc dodać ją do ulubionych!", icon="⚠️")

                        st.markdown("**Tytuł:**")
                        st.code(version_short.get('title', 'Brak tytułu'), language="text", wrap_lines=True)

                        st.markdown("**Opis (Hook):**")
                        clean_v_hook = version_short.get('hook_text', 'Brak opisu').strip().strip('"').strip("'")
                        st.code(clean_v_hook, language="text", wrap_lines=True)

                        col_vtags1, col_vtags2 = st.columns(2)
                        with col_vtags1:
                            v_hashtags_str = version_short.get('hashtags', '')
                            if v_hashtags_str:
                                st.markdown("**Hashtagi:**")
                                st.code(v_hashtags_str, language="text", wrap_lines=True)
                        with col_vtags2:
                            v_yt_tags_str = version_short.get('yt_tags', '')
                            if v_yt_tags_str:
                                st.markdown("**Tagi:**")
                                st.code(v_yt_tags_str, language="text", wrap_lines=True)

                        v_inv_key = "\u200B" * (i + v_idx + 1)
                        with st.expander("Szczegóły cięć" + v_inv_key):
                            for seg in version_short.get('segments', []):
                                st.caption(f"[{seconds_to_timecode(seg.get('start_time', 0.0))} - {seconds_to_timecode(seg.get('end_time', 0.0))}] {seg.get('text', '')}")

                        with st.expander("✂️ Edytor Scen" + v_inv_key):
                            for seg in version_short.get('segments', []):
                                st.caption(f"[{seconds_to_timecode(seg.get('start_time', 0.0))} - {seconds_to_timecode(seg.get('end_time', 0.0))}] {seg.get('text', '')}")

                        with st.expander("📝 Edytor Napisów i Czasów" + v_inv_key):
                            with st.form(key=f"form_subs_version_{current_proj}_{i}_{v_slug}_{v_idx}", border=False):
                                comp_key = f"subtitle_ed_version_{current_proj}_{i}_{v_slug}_{v_idx}_{version.get('updated_at', version.get('created_at', '0'))}"
                                edited_words_raw = subtitle_editor_component(words=version_short.get('words', []), key=comp_key)
                                edited_words = edited_words_raw if edited_words_raw is not None else version_short.get('words', [])
                                submitted_v_subs = st.form_submit_button("💾 Zapisz poprawki napisów i przegeneruj tę wersję", use_container_width=True)

                            if submitted_v_subs:
                                sanitized_words = []
                                rows = edited_words.to_dict("records") if hasattr(edited_words, "to_dict") else edited_words
                                for w in rows:
                                    try:
                                        s_val = float(w.get('start', 0.0)) if w.get('start') is not None else 0.0
                                        e_val = float(w.get('end', 0.0)) if w.get('end') is not None else 0.0
                                        w_val = str(w.get('word', '')).strip() if w.get('word') is not None else ""
                                        if w_val:
                                            sanitized_words.append({'word': w_val, 'start': s_val, 'end': e_val})
                                    except Exception as err:
                                        st.error(f"Błąd w edytorze napisów: {err}")
                                        continue

                                version_short['words'] = sanitized_words
                                for seg in version_short.get('segments', []):
                                    seg_words = [
                                        w['word'] for w in sanitized_words
                                        if w.get('start', 0.0) >= seg.get('start_time', 0.0) - 0.25
                                        and w.get('end', 0.0) <= seg.get('end_time', 0.0) + 0.25
                                    ]
                                    if seg_words:
                                        seg['text'] = " ".join(seg_words)
                                if v_dir:
                                    save_cached_translation(version_short, v_lang, v_dir)
                                    update_version_manifest(v_dir, v_path, version_short)

                                v_settings = copy.deepcopy(version.get("settings") or short_render_settings)
                                v_settings["audio_mode"] = v_settings.get("audio_mode") or "Czysty dubbing (usuń oryginalny głos)"
                                v_settings["dub_target_lang"] = v_lang
                                queue_dub_render(
                                    current_proj,
                                    i,
                                    v_settings,
                                    short_data=version_short,
                                    output_video_path=v_path,
                                    output_ass_path=os.path.splitext(v_path)[0] + ".ass",
                                    skip_auto_translation=True,
                                )
                                st.session_state.cancel_renders = False
                                set_scroll_and_expander(version_anchor, None)
                                st.rerun()

                        with st.expander("🌍 Przetłumacz / Przywróć (AI)" + v_inv_key):
                            with st.form(key=f"form_trans_version_{current_proj}_{i}_{v_slug}_{v_idx}", border=False):
                                current_api_key = st.session_state.get("ui_api_key_key", "")
                                tr_lang_v = st.selectbox(
                                    "Wybierz język docelowy:",
                                    ["Polski", "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Chiński"],
                                    index=1 if v_lang == "Angielski" else 0,
                                    key=f"tr_lang_version_{current_proj}_{i}_{v_slug}_{v_idx}"
                                )
                                tr_btn_v = st.form_submit_button("🌐 Przetłumacz i wygeneruj nową wersję", use_container_width=True)

                            if tr_btn_v:
                                if not current_api_key:
                                    st.error("Brakuje klucza Gemini w zakładce AI.")
                                else:
                                    new_v_settings = copy.deepcopy(version.get("settings") or short_render_settings)
                                    new_v_settings["audio_mode"] = new_v_settings.get("audio_mode") or "Czysty dubbing (usuń oryginalny głos)"
                                    new_v_settings["dub_target_lang"] = tr_lang_v
                                    new_v_settings["dub_auto_subtitles"] = True
                                    queue_dub_render(current_proj, i, new_v_settings)
                                    st.session_state.cancel_renders = False
                                    set_scroll_and_expander(version_anchor, None)
                                    st.rerun()

    if pending_renders:
        if st.session_state.get("cancel_renders", False):
            for task in pending_renders:
                task["status_header_ph"].warning(f"⏳ **Short {task['i']+1} z {len(data['ai_outputs'])}:** Renderowanie wstrzymane.")
            kill_active_dubbing_processes()
            clear_project_dub_queue(current_proj)
        else:
            for task in pending_renders[1:]:
                task["status_header_ph"].info(f"🕒 **Short {task['i']+1} z {len(data['ai_outputs'])}:** W kolejce do renderowania (Czeka na swoją turę)...")
                
            current_task = pending_renders[0]
            i = current_task["i"]
            short = current_task["short"]
            short_filename = current_task["short_filename"]
            ass_filename = current_task["ass_filename"]
            version_folder = current_task.get("version_folder", "")
            short_render_settings = current_task["short_render_settings"]
            is_dub_version_task = current_task.get("is_dub_version", False)
            reuse_audio_path = current_task.get("reuse_audio_path", "")
            skip_auto_translation = current_task.get("skip_auto_translation", False)
            
            status_header = current_task["status_header_ph"]
            prog_bar_container = current_task["prog_bar_ph"]
            status_text = current_task["status_text_ph"]
            scroll_target_id = current_task["scroll_id"]
            restore_expander_id = current_task.get("active_expander_id")
            
            if is_dub_version_task:
                status_header.markdown(f"#### ⚙️ Generowanie wersji {current_task.get('version_language', '')}: Short {i+1} z {len(data['ai_outputs'])}")
            else:
                status_header.markdown(f"#### ⚙️ Generowanie: Short {i+1} z {len(data['ai_outputs'])}")
            
            try:
                export_resolution = short_render_settings.get("export_res", "Zgodna ze źródłem")
                aspect_ratio_req = short_render_settings.get("aspect_ratio", "9:16")
                export_bitrate_req = short_render_settings.get("export_bitrate", 15)
                export_codec_req = short_render_settings.get("export_codec", "H.264 (Większa kompatybilność)")
                
                face_tracking_req = short_render_settings.get("face_tracking", True)
                smooth_val = short_render_settings.get("ft_smoothness", 10)
                recheck_val = short_render_settings.get("ft_recheck", 8)
                zoom_val = short_render_settings.get("ft_zoom", 1.0)
                y_offset_val = short_render_settings.get("ft_y_offset", 0)
                strategy_val = short_render_settings.get("ft_strategy", "Główny mówca (Skupia na największej twarzy)")
                audio_override_path = None
                active_version_folder = ""

                if reuse_audio_path and os.path.exists(reuse_audio_path):
                    status_text.markdown("**Audio:** używam istniejącego dubbingu, zmieniam tylko obraz/napisy...")
                    audio_override_path = reuse_audio_path
                    active_version_folder = version_folder
                    reuse_lang = short_render_settings.get("dub_target_lang", current_task.get("version_language", "Angielski"))
                    if align_short_words_to_dub_audio(reuse_audio_path, short, reuse_lang, status_text) and version_folder:
                        save_cached_translation(short, reuse_lang, version_folder)
                elif is_dubbing_enabled(short_render_settings):
                        dub_lang = short_render_settings.get("dub_target_lang", "Angielski")
                        if short_render_settings.get("dub_auto_subtitles", True) and not skip_auto_translation:
                            if version_folder and load_cached_translation(short, dub_lang, version_folder):
                                status_text.markdown(f"**Audio:** wczytano tłumaczenie {dub_lang} z cache...")
                            else:
                                current_api_key = st.session_state.get("ui_api_key_key", "")
                                if not current_api_key:
                                    raise Exception("Dubbing z automatycznymi napisami wymaga klucza Gemini w zakładce AI.")
                                status_text.markdown(f"**Audio:** tłumaczenie napisów na język audio ({dub_lang})...")
                                ok = translate_short_with_gemini(short, dub_lang, current_api_key)
                                if not ok:
                                    raise Exception("Nie udało się przetłumaczyć napisów do języka dubbingu.")
                                if version_folder:
                                    save_cached_translation(short, dub_lang, version_folder)

                        audio_override_path, active_version_folder = build_dubbed_audio(
                            data["video_file"],
                            short,
                            current_proj,
                            i,
                            short_render_settings,
                            status_text=status_text,
                            progress_bar=prog_bar_container
                        )
                    
                if short_render_settings.get("subs"):
                    status_text.markdown("**Przygotowywanie:** Obliczanie sygnatur czasowych dla napisów...")
                    ass_file_path, was_scaled, scaled_font_size = generate_viral_ass_subtitles(
                        short.get('segments', []), short.get('words', []), ass_filename, preset_name=short_render_settings.get("preset", "Hormozi (Classic)"), 
                        custom_font=short_render_settings.get("font"), aspect_ratio=aspect_ratio_req,
                        override_bcolor=short_render_settings.get("bcolor"), override_hcolor=short_render_settings.get("hcolor"),
                        override_size=short_render_settings.get("size"), override_margin=short_render_settings.get("margin"), auto_scale=short_render_settings.get("auto_scale", False),
                        override_hsize=short_render_settings.get("hsize"), override_out_color=short_render_settings.get("out_color"), override_out_thick=short_render_settings.get("out_thick"),
                        override_shad_color=short_render_settings.get("shad_color"), override_shad_size=short_render_settings.get("shad_size"), override_bold=short_render_settings.get("bold"),
                        override_italic=short_render_settings.get("italic"), override_upper=short_render_settings.get("upper"), override_words=short_render_settings.get("words"),
                        override_mode=short_render_settings.get("mode"), override_punct=short_render_settings.get("punct"),
                        override_animation=short_render_settings.get("animation"), override_bg_padding=short_render_settings.get("bg_padding")
                    )
                    
                    if was_scaled:
                        st.toast(f"📏 Zmniejszono czcionkę dla '{short.get('title', 'Wideo')}' do rozmiaru {scaled_font_size}, aby zmieściła się w ekranie.", icon="⚠️")

                    render_short_ffmpeg(data["video_file"], short.get('segments', []), short_filename, aspect_ratio=aspect_ratio_req, ass_subtitle_file=ass_file_path, export_res=export_resolution, export_bitrate=export_bitrate_req, export_codec=export_codec_req, face_tracking=face_tracking_req, ft_smoothness=smooth_val, ft_recheck=recheck_val, ft_zoom=zoom_val, ft_y_offset=y_offset_val, ft_strategy=strategy_val, status_text=status_text, progress_bar=prog_bar_container, logo_settings=short_render_settings, audio_override_path=audio_override_path)
                else:
                    render_short_ffmpeg(data["video_file"], short.get('segments', []), short_filename, aspect_ratio=aspect_ratio_req, export_res=export_resolution, export_bitrate=export_bitrate_req, export_codec=export_codec_req, face_tracking=face_tracking_req, ft_smoothness=smooth_val, ft_recheck=recheck_val, ft_zoom=zoom_val, ft_y_offset=y_offset_val, ft_strategy=strategy_val, status_text=status_text, progress_bar=prog_bar_container, logo_settings=short_render_settings, audio_override_path=audio_override_path)

                if active_version_folder:
                    update_version_manifest(active_version_folder, short_filename, short)

                if is_dub_version_task:
                    pop_dub_render(current_proj, i)
                    data["ai_outputs"][i].pop("last_dub_render_error", None)
                else:
                    data["ai_outputs"][i]["force_re_render"] = False
                    if "file_stats" in data["ai_outputs"][i]:
                        del data["ai_outputs"][i]["file_stats"]
                with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: json.dump(data, f)
                    
                status_text.success("✅ Wideo pomyślnie wyrenderowane!")
                prog_bar_container.progress(1.0)
                time.sleep(1.5) 
                
                st.session_state.scroll_target = scroll_target_id
                st.session_state.active_expander = restore_expander_id
                st.rerun()
            except Exception as e:
                if is_dub_version_task:
                    pop_dub_render(current_proj, i)
                else:
                    data["ai_outputs"][i]["last_render_error"] = str(e)
                    data["ai_outputs"][i]["force_re_render"] = False
                    data["ai_outputs"][i]["render_failed"] = True
                
                with open(os.path.join("workspace", "sessions", current_proj, "data.json"), "w", encoding="utf-8") as f: 
                    json.dump(data, f)
                    
                st.session_state.error_msg = f"Zatrzymano proces. Błąd renderowania shorta {i+1}: {e}"
                st.session_state.cancel_renders = True
                st.session_state.scroll_target = scroll_target_id
                st.session_state.active_expander = restore_expander_id
                st.rerun()
