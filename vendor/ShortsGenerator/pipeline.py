import streamlit as st
import os
import json
from datetime import datetime
from config import LANG_MAP
from downloader import get_yt_id, get_video_title, download_video, download_yt_subtitles
from ai_processor import parse_vtt_to_transcript, load_whisper, transcribe_video, analyze_with_gemini, optimize_segments, initialize_short_words, translate_short_with_gemini, apply_whisper_glossary_to_transcript, apply_whisper_glossary_to_words
from video_engine import create_proxy

def transcribe_with_faster_whisper_fallback(video_file, target_lang_code=None):
    """Jawny fallback na faster-whisper, gdy glowny backend Whispera zawiedzie."""
    from faster_whisper import WhisperModel
    from ai_processor import _preprocess_audio, _transcribe_faster_whisper

    processed_audio = _preprocess_audio(str(video_file))
    tmp_created = processed_audio != str(video_file)
    try:
        fw_model = WhisperModel("medium", device="cpu", compute_type="int8")
        return _transcribe_faster_whisper(processed_audio, fw_model, target_lang_code)
    finally:
        if tmp_created and os.path.exists(processed_audio):
            try:
                os.remove(processed_audio)
            except Exception:
                pass

# Dodano argument custom_prompt w definicji
def process_video_pipeline(video_input, ui_input_method, ui_video_quality, ui_whisper_lang, ui_yt_subs, ui_api_key, ui_shorts_count, ui_duration_range, ui_target_lang, render_settings, prompt_mode="Precyzyjna (Domyślna - bardziej restrykcyjna)", custom_prompt=""):
    try:
        video_id = get_yt_id(video_input) if ui_input_method == "Link z YouTube" else os.path.splitext(os.path.basename(video_input))[0]

        # Cache uwzglednia jezyk — inne jezyki = osobny plik transkrypcji
        target_lang_code = LANG_MAP.get(ui_whisper_lang)
        lang_suffix = f"_{target_lang_code}" if target_lang_code else ""
        transcript_file = f"workspace/downloads/{video_id}{lang_suffix}_transcript.txt"
        words_file      = f"workspace/downloads/{video_id}{lang_suffix}_words.json"

        # Backward compat: stare pliki bez sufiksu jezyka (np. Nr9pa6uSwmc_transcript.txt)
        # Jezeli nowy plik nie istnieje, sprawdz stara nazwe
        legacy_transcript = f"workspace/downloads/{video_id}_transcript.txt"
        legacy_words      = f"workspace/downloads/{video_id}_words.json"
        if (not os.path.exists(transcript_file) or not os.path.exists(words_file)) \
                and os.path.exists(legacy_transcript) and os.path.exists(legacy_words):
            transcript_file = legacy_transcript
            words_file      = legacy_words

        if not st.session_state.is_running: st.stop()

        # --- SEKCJA POBIERANIA I PROXY ---
        with st.spinner("Pobieranie/przygotowywanie wideo..."):
            original_video_file = download_video(video_input, ui_video_quality) if ui_input_method == "Link z YouTube" else video_input
            video_title = get_video_title(video_input) if ui_input_method == "Link z YouTube" else os.path.basename(video_input)

        video_file = original_video_file

        # Logika Proxy dla plików lokalnych
        if ui_input_method == "Lokalny plik" and render_settings.get("use_proxy", False):
            proxy_ph_status = st.empty()
            proxy_ph_bar = st.empty()
            try:
                proxy_res_val = render_settings.get("proxy_res", "1080p")
                proxy_bitrate_val = render_settings.get("proxy_bitrate", 15)
                proxy_file = create_proxy(original_video_file, proxy_res=proxy_res_val, proxy_bitrate=proxy_bitrate_val, progress_bar=proxy_ph_bar, status_text=proxy_ph_status)
                video_file = proxy_file
                proxy_ph_status.empty()
                proxy_ph_bar.empty()
            except Exception as e:
                st.warning(f"Nie udało się utworzyć proxy ({e}). Używam oryginalnego pliku.")
                proxy_ph_status.empty()
                proxy_ph_bar.empty()

        # --- TRANSKRYPCJA (z cache) ---
        if os.path.exists(transcript_file) and os.path.exists(words_file):
            st.success("✅ Znaleziono gotową transkrypcję z sygnaturami słów na dysku! (pominięto Whisper)")
            with open(transcript_file, "r", encoding="utf-8") as f: transcript_data = f.read()
            with open(words_file, "r", encoding="utf-8") as f: global_words = json.load(f)
        else:
            transcript_data = ""
            global_words = []

            # Próba napisów YT
            if ui_input_method == "Link z YouTube" and ui_yt_subs:
                with st.spinner("Próba pobrania oryginalnych napisów z YouTube..."):
                    vtt_file = download_yt_subtitles(video_input, video_id, target_lang_code)
                    if vtt_file:
                        transcript_data, global_words = parse_vtt_to_transcript(vtt_file)

            # Whisper (z fallbackiem mlx → faster_whisper)
            if not transcript_data.strip() or not global_words:
                with st.spinner("Odsłuchiwanie wideo (Precyzyjna detekcja słów Whisperem)..."):
                    try:
                        transcript_data, global_words = transcribe_video(video_file, load_whisper(), lang_code=target_lang_code)
                    except Exception as whisper_err:
                        # Fallback: jesli mlx subprocess zawiodl, probuj faster_whisper
                        st.warning(f"⚠️ Główny silnik Whisper zawiódł ({whisper_err}). Próbuję alternatywny tryb...")
                        transcript_data, global_words = transcribe_with_faster_whisper_fallback(video_file, target_lang_code)

            # Zapisz od razu po transkrypcji — PRZED dalszymi krokami
            # Dzieki temu kolejne uruchomienia z tym samym filmem pomijaja Whisper
            if transcript_data.strip():
                with open(transcript_file, "w", encoding="utf-8") as f: f.write(transcript_data)
                with open(words_file, "w", encoding="utf-8") as f: json.dump(global_words, f)
                st.success(f"✅ Transkrypcja zapisana do cache ({len(global_words)} słów wykrytych).")
        
        if not st.session_state.is_running: st.stop()
        
        # --- SŁOWNIK POPRAWEK WHISPER ---
        # Stosowany zawsze — zarówno po świeżej transkrypcji, jak i po załadowaniu z cache.
        # Cache przechowuje surowy wynik Whisper; glossary to post-processing na żywo.
        _whisper_glossary = st.session_state.get("ui_whisper_glossary_key", "").strip()
        if _whisper_glossary:
            transcript_data = apply_whisper_glossary_to_transcript(transcript_data, _whisper_glossary)
            global_words = apply_whisper_glossary_to_words(global_words, _whisper_glossary)

        with st.spinner("AI wybiera viralowe momenty i generuje hashtagi..."):
            # Przekazanie do ai_processor zarówno trybu jak i ew. zawartości z pola
            ai_outputs = analyze_with_gemini(transcript_data, ui_api_key, ui_shorts_count, ui_duration_range[0], ui_duration_range[1], prompt_mode, custom_prompt, _whisper_glossary)
        
        # --- SŁOWNIK POPRAWEK: korekcja outputu Gemini ---
        # Gemini generuje tytuły/opisy/hashtagi od siebie — może pisać błędne nazwy.
        # Stosujemy ten sam słownik na wszystkich polach tekstowych każdego shorta.
        if _whisper_glossary:
            for short in ai_outputs:
                for field in ("title", "hook_text", "hashtags", "yt_tags"):
                    if short.get(field):
                        short[field] = apply_whisper_glossary_to_transcript(short[field], _whisper_glossary)
                for seg in short.get("segments", []):
                    if seg.get("text"):
                        seg["text"] = apply_whisper_glossary_to_transcript(seg["text"], _whisper_glossary)

        for short in ai_outputs:
            # Przekazujemy global_words do precyzyjnego snap_to_word_boundaries
            # zamiast slepego paddingu ±0.5s
            short['segments'] = optimize_segments(short.get('segments', []), global_words)
            initialize_short_words(short, global_words)
            
            if ui_target_lang != "Brak (Oryginał)":
                with st.spinner(f"Tłumaczenie na {ui_target_lang} - {short.get('title', 'Short')[:20]}..."):
                    translate_short_with_gemini(short, ui_target_lang, ui_api_key)
                    if _whisper_glossary:
                        for field in ("title", "hook_text", "hashtags", "yt_tags"):
                            if short.get(field):
                                short[field] = apply_whisper_glossary_to_transcript(short[field], _whisper_glossary)
                        for seg in short.get("segments", []):
                            if seg.get("text"):
                                seg["text"] = apply_whisper_glossary_to_transcript(seg["text"], _whisper_glossary)
                        short["words"] = apply_whisper_glossary_to_words(short.get("words", []), _whisper_glossary)
        
        new_project_id = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{video_id}"
        session_dir = os.path.join("workspace", "sessions", new_project_id)
        os.makedirs(session_dir, exist_ok=True)
        
        # original_source_path = zawsze absolutna ścieżka do ORYGINALNEGO pliku wideo (nie proxy).
        # Używana przez generator XML do budowania pathurl dla DaVinci Resolve.
        # Działa niezależnie od systemu plików, nazwy dysku i polskich znaków w ścieżce.
        original_source_path = os.path.abspath(original_video_file)

        session_data = {"project_id": new_project_id, "display_name": f"{video_title} ({len(ai_outputs)} shortów)", "ai_outputs": ai_outputs, "video_file": video_file, "original_source_path": original_source_path, "global_words": global_words, "render_settings": render_settings}
        with open(os.path.join(session_dir, "data.json"), "w", encoding="utf-8") as f: json.dump(session_data, f)
        
        st.session_state.shorts_data = session_data
        st.session_state.current_project = new_project_id
        st.session_state.is_running = False
        st.session_state.cancel_renders = False
        st.rerun()

    except Exception as e: 
        st.session_state.error_msg = str(e)
        st.session_state.is_running = False
        st.rerun()
