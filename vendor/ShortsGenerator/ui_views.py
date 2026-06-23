import streamlit as st
import os
import json
import copy
import re
from datetime import datetime
from utils import (
    get_favorites, remove_from_favorites, get_video_stats, safe_trash_file,
    get_all_projects, load_project, delete_project, force_remove_dir
)
from dubbing_engine import kill_active_dubbing_processes

# Zaktualizowana, bezpieczna funkcja formatująca do list rozwijanych (z czytelną datą zamiast ID)
def format_project_name_safe(project_folder_name):
    if project_folder_name == "-- Wybierz --":
        return "-- Wybierz --"
        
    try:
        json_path = os.path.join("workspace", "sessions", project_folder_name, "data.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Pobieramy ładną nazwę wyświetlaną i ilość shortów
            display_name = data.get("display_name", project_folder_name)
            shorts_count = len(data.get("ai_outputs", []))
            
            # Czyścimy starą nazwę wyświetlaną z ewentualnych dopisków o ilości shortów, żeby ich nie dublować
            clean_display_name = re.sub(r'\s*\(\d+\s*shortów\)', '', display_name)
            
            # Tworzymy ładną i przyjazną dla użytkownika datę na podstawie nazwy folderu
            # Nazwy folderów mają format: YYYY-MM-DD_HH-MM-SS_idWideo
            date_str = ""
            try:
                # Wyciągamy pierwsze 19 znaków (YYYY-MM-DD_HH-MM-SS)
                raw_date = project_folder_name[:19]
                parsed_date = datetime.strptime(raw_date, "%Y-%m-%d_%H-%M-%S")
                # Formatujemy na polski, czytelny standard: DD.MM.YYYY o HH:MM
                date_str = parsed_date.strftime("%d.%m.%Y o %H:%M")
            except Exception:
                # Fallback, gdyby nazwa folderu nie pasowała do standardu
                date_str = "Data nieznana"
            
            return f"{clean_display_name} ({shorts_count} shortów) [Utworzono: {date_str}]"
    except Exception:
        pass
        
    return project_folder_name

def render_saved_projects_menu():
    # Pobieramy SUROWE nazwy folderów (są unikalne, np. moj_film_17123123)
    projects_folders = get_all_projects() 
    
    st.markdown("<h3 style='text-align: center;'>📂 Twoje Zapisane Projekty</h3>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    col_s1, col_s2, col_s3 = st.columns([1, 6, 1])
    with col_s2:
        # Używamy surowych folderów jako kluczy, a format_func zmienia je w ładny tekst dla widza
        selected_project_folder = st.selectbox(
            "Wybierz sesję z listy, aby kontynuować pracę:", 
            ["-- Wybierz --"] + projects_folders, 
            format_func=format_project_name_safe, 
            label_visibility="collapsed"
        )
        
        if selected_project_folder != "-- Wybierz --":
            st.markdown("<br>", unsafe_allow_html=True)
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                if st.button("📂 Wczytaj Projekt", type="primary", use_container_width=True):
                    try:
                        kill_active_dubbing_processes()
                        # Wczytujemy projekt po jego UNIKALNYM ID folderu, nie po nazwie wyświetlanej!
                        st.session_state.shorts_data = load_project(selected_project_folder)
                        st.session_state.current_project = selected_project_folder
                        st.session_state.active_view_mode = "Zapisane Projekty"
                        st.session_state.is_running = False
                        st.session_state.cancel_renders = False
                        st.session_state.error_msg = None
                        if isinstance(st.session_state.get("dub_render_queue"), dict):
                            for q_key in list(st.session_state["dub_render_queue"].keys()):
                                if q_key.startswith(f"{selected_project_folder}:"):
                                    st.session_state["dub_render_queue"].pop(q_key, None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Nie udało się wczytać projektu. Plik konfiguracyjny może być uszkodzony. Szegóły błędu: {e}")
            with col_b2:
                if st.button("🗑️ Usuń Projekt", use_container_width=True):
                    st.session_state.confirm_delete_single = True
                
            if st.session_state.get("confirm_delete_single", False):
                st.warning(f"⚠️ Czy na pewno chcesz trwale usunąć ten projekt ze wszystkimi klipami?")
                col_ds1, col_ds2 = st.columns(2)
                with col_ds1:
                    if st.button("Tak, usuń", type="primary", use_container_width=True):
                        # Usuwamy projekt bezpiecznie po nazwie folderu
                        delete_project(selected_project_folder)
                        st.session_state.confirm_delete_single = False
                        if st.session_state.current_project == selected_project_folder:
                            st.session_state.shorts_data = None
                            st.session_state.current_project = None
                        st.rerun()
                with col_ds2:
                    if st.button("Anuluj", key="cancel_single", use_container_width=True):
                        st.session_state.confirm_delete_single = False
                        st.rerun()
                        
            st.markdown("<hr style='margin-top: 40px;'>", unsafe_allow_html=True)
            
        if st.button("⚠️ Usuń wszystkie projekty z dysku (Wyczyść pamięć)", use_container_width=True):
            st.session_state.confirm_delete_all = True
            
        if st.session_state.get("confirm_delete_all", False):
            st.warning("Czy NA PEWNO chcesz usunąć wszystkie projekty, pobrane wideo i transkrypcje? Ta operacja jest nieodwracalna.")
            col_del1, col_del2 = st.columns(2)
            with col_del1:
                if st.button("Tak, usuń wszystko!", type="primary", use_container_width=True):
                    force_remove_dir("workspace/sessions")
                    force_remove_dir("workspace/downloads")
                    os.makedirs("workspace/sessions", exist_ok=True)
                    os.makedirs("workspace/downloads", exist_ok=True)
                    st.session_state.confirm_delete_all = False
                    st.session_state.shorts_data = None
                    st.session_state.current_project = None
                    st.rerun()
            with col_del2:
                if st.button("Anuluj", use_container_width=True):
                    st.session_state.confirm_delete_all = False
                    st.rerun()

def render_favorites_view():
    st.markdown("<h3 style='text-align: center;'>🤍 Ulubione Klipy</h3>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    favs = get_favorites()
    if not favs:
        st.info("Brak ulubionych klipów. Wygeneruj shorty z wideo i kliknij ikonkę 🤍 przy najlepszych z nich, aby dodać je do swojej kolekcji!")
        return

    # Globalny przycisk czyszczenia ulubionych
    col_btn1, col_btn2, col_btn3 = st.columns([1, 2, 1])
    with col_btn2:
        if st.button("⚠️ Usuń wszystkie ulubione klipy z dysku", use_container_width=True):
            st.session_state.confirm_delete_all_favs = True
            
        if st.session_state.get("confirm_delete_all_favs", False):
            st.warning("Czy NA PEWNO chcesz usunąć wszystkie ulubione klipy? Ta operacja jest nieodwracalna i usunie pliki wideo z dysku.")
            col_del1, col_del2 = st.columns(2)
            with col_del1:
                if st.button("Tak, usuń wszystko!", type="primary", use_container_width=True):
                    force_remove_dir("workspace/favorites")
                    os.makedirs("workspace/favorites", exist_ok=True)
                    st.session_state.confirm_delete_all_favs = False
                    st.rerun()
            with col_del2:
                if st.button("Anuluj", key="cancel_del_all_favs", use_container_width=True):
                    st.session_state.confirm_delete_all_favs = False
                    st.rerun()
                    
    st.markdown("<hr style='margin-top: 20px; margin-bottom: 30px;'>", unsafe_allow_html=True)

    for fav in favs:
        fav_id = fav['fav_id']
        short = fav['short_data']
        video_path = fav['video_path']
        
        with st.container(border=True):
            col_vid, col_info = st.columns([1.5, 3])
            with col_vid:
                if os.path.exists(video_path):
                    exp_res = short.get("render_settings", {}).get("export_res", "Nieznana")
                    
                    if "file_stats" not in short or "resolution" not in short.get("file_stats", {}):
                        short["file_stats"] = get_video_stats(video_path)
                        fav_json = os.path.join("workspace", "favorites", f"{fav_id}.json")
                        try:
                            with open(fav_json, "w", encoding="utf-8") as f: json.dump(short, f, ensure_ascii=False)
                        except: pass
                        
                    size_mb = short["file_stats"].get("size_mb", 0.0)
                    real_bitrate = short["file_stats"].get("bitrate", 0.0)
                    real_codec = short["file_stats"].get("codec", "")
                    real_res = short["file_stats"].get("resolution", exp_res)
                    if not real_res: real_res = exp_res
                    
                    st.video(os.path.abspath(video_path))
                    st.caption(f"📏 {real_res} {real_codec} | 🎞️ {real_bitrate:.2f} Mbps | 💾 {size_mb:.2f} MB")
                    
                    # --- Generowanie czystej nazwy dla pobieranego pliku ---
                    raw_title = short.get('title', f'Ulubione_{fav_id}')
                    clean_dl_title = re.sub(r'[\\\\/*?:"<>|]', "", raw_title).strip()
                    if not clean_dl_title: clean_dl_title = f"Ulubione_{fav_id}"
                    
                    st.download_button(
                        "💾 Pobierz MP4", 
                        data=lambda p=video_path: open(p, "rb").read(),
                        file_name=f"{clean_dl_title}.mp4", 
                        mime="video/mp4",
                        key=f"dl_fav_{fav_id}", 
                        use_container_width=True
                    )
                else:
                    st.error("Wideo zostało przeniesione lub usunięte z dysku.")
                    
            with col_info:
                col_score, col_fav = st.columns([6, 1])
                with col_score:
                    st.markdown(f'<div class="score-badge">🔥 Viral Score: {short.get("score", 90)}/100</div>', unsafe_allow_html=True)
                with col_fav:
                    if st.button("Usuń 💔", key=f"rm_fav_view_{fav_id}", help="Usuń z ulubionych"):
                        remove_from_favorites(fav_id)
                        st.rerun()
                        
                st.markdown("**Tytuł na YouTube / TikToka:**")
                st.code(short.get('title', 'Brak tytułu'), language="text", wrap_lines=True)
                
                st.markdown("**Opis Wideo (Streszczenie):**")
                clean_hook = short.get('hook_text', 'Brak opisu').strip().strip('\"').strip("'")
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
                        st.markdown("**Tagi YT:**")
                        st.code(yt_tags_str, language="text", wrap_lines=True)
