import os
import logging
import streamlit as st

# Wyciszenie ostrzeżeń systemowych macOS i naprawa bezpieczeństwa wątków
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

# CAŁKOWITE wyciszenie fałszywych błędów Streamlita "MediaFileHandler: Missing file..."
logging.getLogger("streamlit.web.server.media_file_handler").setLevel(logging.CRITICAL)
logging.getLogger("streamlit.runtime.memory_media_file_storage").setLevel(logging.CRITICAL)

from state_manager import init_session_state, save_all_ui_settings
from pipeline import process_video_pipeline
from utils import cleanup_trash

# Importujemy wszystkie wydzielone komponenty z nowego pliku!
from ui_components import render_sidebar, render_start_view, render_editor

def hard_cleanup_zombie_projects():
    import shutil
    sessions_dir = os.path.join("workspace", "sessions")
    downloads_dir = os.path.join("workspace", "downloads")
    active_projects = 0
    if os.path.exists(sessions_dir):
        for f in os.listdir(sessions_dir):
            f_path = os.path.join(sessions_dir, f)
            if os.path.isdir(f_path):
                if not os.path.exists(os.path.join(f_path, "data.json")):
                    try: shutil.rmtree(f_path, ignore_errors=True)
                    except: pass
                else: active_projects += 1
    if active_projects == 0 and os.path.exists(downloads_dir) and not st.session_state.get("is_running", False):
        try:
            for filename in os.listdir(downloads_dir):
                file_path = os.path.join(downloads_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path): os.unlink(file_path)
                elif os.path.isdir(file_path): shutil.rmtree(file_path, ignore_errors=True)
        except: pass


# --- INICJALIZACJA I CSS ---
st.set_page_config(page_title="AI ViralCutter by Opitkovanie", page_icon="✂️", layout="wide", initial_sidebar_state="expanded")

cleanup_trash()
init_session_state()

hard_cleanup_zombie_projects()

if "cancel_renders" not in st.session_state: st.session_state.cancel_renders = False
for state_var in ["shorts_data", "is_running", "error_msg", "current_project", "local_file_path", "active_view_mode", "scroll_target", "active_expander"]:
    if state_var not in st.session_state: st.session_state[state_var] = None if state_var != "local_file_path" else ""
            
if st.session_state.is_running is None: st.session_state.is_running = False

st.markdown("""<style>
    /* Prawy ukryty pasek bez ruszania lewego paska bocznego */
    [data-testid="stHeaderActionElements"] {visibility: hidden !important;}
    .stAppDeployButton {display: none !important;}
    [data-testid="stStatusWidget"] {visibility: hidden !important;}
    #MainMenu {visibility: hidden !important;}

    /* Zabezpieczenie dla przycisku rozwijania (strzałki) */
    [data-testid="collapsedControl"] {visibility: visible !important; display: block !important;}

    .block-container { padding-top: 1.5rem !important; padding-bottom: 1rem !important; }
    div[data-testid="stVerticalBlock"] { gap: 0.8rem !important; }
    div[data-testid="stHorizontalBlock"] { gap: 1rem !important; }
    hr { margin-top: 0.8rem !important; margin-bottom: 0.8rem !important; }
    h4 { padding-top: 0.5rem !important; padding-bottom: 0.2rem !important; margin-bottom: 0 !important; margin-top: 0.2rem !important; }
    [data-testid="stExpander"] { margin-top: 0.5rem !important; }
    .score-badge { color: #00ff00; font-weight: bold; font-size: 20px; margin-bottom: 5px; background: #1e1e1e; padding: 4px 10px; border-radius: 8px; display: inline-block; border: 1px solid #333;}
    .hook-text { color: #aaaaaa; font-style: italic; font-size: 14px; margin-bottom: 15px; }
    a[data-testid="StyledLinkIconContainer"] { display: none !important; }
    .stMarkdown a svg { display: none !important; }
    h1 a, h2 a, h3 a, h4 a, h5 a, h6 a { display: none !important; pointer-events: none; }
</style>""", unsafe_allow_html=True)

if st.session_state.scroll_target:
    js_code = f"""
    <script>
        (function() {{
            var targetId = "{st.session_state.scroll_target}";
            var attempts = 0;
            var maxAttempts = 40;
            var timer = setInterval(function() {{
                var element = window.parent.document.getElementById(targetId);
                if (element) {{
                    element.scrollIntoView({{behavior: "smooth", block: "start"}});
                    clearInterval(timer);
                }}
                if (++attempts >= maxAttempts) clearInterval(timer);
            }}, 100);
        }})();
    </script>
    """
    st.components.v1.html(js_code, height=0, width=0)
    st.session_state.scroll_target = None

if st.session_state.get("_reset_subtitles_to_preset", False):
    from state_manager import apply_preset_to_subtitle_state
    apply_preset_to_subtitle_state(st.session_state.get("preset_selector", "Hormozi (Classic)"))
    save_all_ui_settings()
    st.session_state["_reset_subtitles_to_preset"] = False

# --- GŁÓWNA LOGIKA INTERFEJSU ---
with st.sidebar:
    render_sidebar()

st.markdown("<h1 style='text-align: center; margin-bottom: 20px;'>✂️ AI ViralCutter by Opitkovanie</h1>", unsafe_allow_html=True)
col_n1, col_n2, col_n3 = st.columns([1, 8, 1])
with col_n2:
    ui_input_method = st.radio(
        "Menu Główne:", 
        ["Link z YouTube", "Lokalny plik", "Zapisane Projekty", "Ulubione"], 
        horizontal=True, 
        key="ui_method_key", 
        on_change=save_all_ui_settings,
        label_visibility="collapsed"
    )
st.markdown("---")

if st.session_state.error_msg: st.error(st.session_state.error_msg)

if ui_input_method == "Ulubione":
    from ui_views import render_favorites_view
    render_favorites_view()
elif ui_input_method == "Zapisane Projekty" and not st.session_state.shorts_data:
    from ui_views import render_saved_projects_menu
    render_saved_projects_menu()
elif ui_input_method in ["Link z YouTube", "Lokalny plik"] and not st.session_state.shorts_data:
    render_start_view(st.session_state.is_running, ui_input_method)

if st.session_state.is_running:
    st.components.v1.html("""
        <button onclick="window.parent.location.reload()" style="
            display: block; width: 100%; background-color: #ff4b4b; color: white; 
            padding: 12px 15px; border: none; border-radius: 8px; text-align: center; 
            font-weight: 600; font-size: 15px; cursor: pointer;
            margin-top: 4px; margin-bottom: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.2);
        ">🛑 PRZERWIJ PROCES (Zatrzymaj i zresetuj)</button>
    """, height=56)
    
    process_video_pipeline(
        st.session_state.get("video_input_target", ""), ui_input_method, 
        st.session_state.get("ui_yt_qual_key", "1080p"), st.session_state.get("ui_whisper_lang_key", "Auto-detekcja"), 
        st.session_state.get("ui_yt_subs_key", True), st.session_state.get("ui_api_key_key", ""), 
        st.session_state.get("ui_shorts_count_key", 3), st.session_state.get("ui_dur_key", (45, 90)), 
        st.session_state.get("ui_target_lang_key", "Brak (Oryginał)"), st.session_state.get("render_settings", {}),
        st.session_state.get("ui_prompt_mode_key", "Precyzyjna (Domyślna - bardziej restrykcyjna)"),
        st.session_state.get("ui_custom_prompt_key", "")
    )

if st.session_state.shorts_data and st.session_state.current_project and ui_input_method != "Ulubione":
    if ui_input_method == "Zapisane Projekty":
        from ui_views import render_saved_projects_menu
        render_saved_projects_menu() 
        st.markdown("---")
    render_editor(st.session_state.shorts_data, st.session_state.current_project)
