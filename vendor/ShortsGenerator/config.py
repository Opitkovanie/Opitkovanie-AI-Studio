import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# --- INICJALIZACJA FOLDERÓW ---
os.makedirs("fonts", exist_ok=True)
os.makedirs("workspace/sessions", exist_ok=True)
os.makedirs("workspace/downloads", exist_ok=True)
os.makedirs("workspace/favorites", exist_ok=True)

# --- BAZA PRESETÓW NAPISÓW ---
SUBTITLE_PRESETS = {
    "Hormozi (Classic)": {"font_name": "Montserrat-ExtraBold", "font_size": 30, "base_color": "#FFFFFF", "highlight_color": "#00FF00", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 0, "bold": True, "italic": False, "uppercase": True, "highlight_size": 35, "words_per_block": 2, "mode": "highlight", "remove_punctuation": True, "animation": "none"},
    "MrBeast Clean Hook": {"font_name": "Montserrat-ExtraBold", "font_size": 32, "base_color": "#FFFFFF", "highlight_color": "#FFD700", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 2, "bold": True, "italic": False, "uppercase": True, "highlight_size": 38, "words_per_block": 3, "mode": "highlight", "remove_punctuation": True, "animation": "none"},
    "Beasty (Loud)": {"font_name": "Arial", "font_size": 34, "base_color": "#FFFFFF", "highlight_color": "#FF0000", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 3, "bold": True, "italic": False, "uppercase": True, "highlight_size": 40, "words_per_block": 3, "mode": "highlight", "remove_punctuation": True, "animation": "none"},
    "Word Killer (TikTok)": {"font_name": "Impact", "font_size": 38, "base_color": "#FFFFFF", "highlight_color": "#FF0000", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 3, "bold": True, "italic": False, "uppercase": True, "highlight_size": 45, "words_per_block": 1, "mode": "word_by_word", "remove_punctuation": True, "animation": "none"},
    "Rapid Fire (Sprint)": {"font_name": "Impact", "font_size": 36, "base_color": "#FFFFFF", "highlight_color": "#FFFF00", "outline_color": "#000000", "outline_thickness": 2, "shadow_color": "#000000", "shadow_size": 2, "bold": True, "italic": True, "uppercase": True, "highlight_size": 42, "words_per_block": 1, "mode": "word_by_word", "remove_punctuation": True, "animation": "none"},
    "Neon Cyber": {"font_name": "Arial", "font_size": 30, "base_color": "#FF00FF", "highlight_color": "#00FFFF", "outline_color": "#FFFFFF", "outline_thickness": 1, "shadow_color": "#000000", "shadow_size": 3, "bold": True, "italic": False, "uppercase": True, "highlight_size": 36, "words_per_block": 2, "mode": "highlight", "remove_punctuation": True, "animation": "none"},
    "Ali Abdaal (Minimal)": {"font_name": "ProximaNova-Bold", "font_size": 28, "base_color": "#FFFFFF", "highlight_color": "#FFD700", "outline_color": "#000000", "outline_thickness": 1, "shadow_color": "#000000", "shadow_size": 2, "bold": False, "italic": False, "uppercase": False, "highlight_size": 30, "words_per_block": 4, "mode": "highlight", "remove_punctuation": False, "animation": "none"},
    "Iman Gadzhi (Elegant)": {"font_name": "Times New Roman", "font_size": 34, "base_color": "#FFFFFF", "highlight_color": "#E6C200", "outline_color": "#000000", "outline_thickness": 2, "shadow_color": "#000000", "shadow_size": 1, "bold": True, "italic": True, "uppercase": False, "highlight_size": 34, "words_per_block": 2, "mode": "highlight", "remove_punctuation": True, "animation": "none"},
    "Gaming / GTA (Action)": {"font_name": "Pricedown", "font_size": 42, "base_color": "#FFFFFF", "highlight_color": "#FF00FF", "outline_color": "#000000", "outline_thickness": 4, "shadow_color": "#000000", "shadow_size": 4, "bold": True, "italic": True, "uppercase": True, "highlight_size": 50, "words_per_block": 1, "mode": "word_by_word", "remove_punctuation": True, "animation": "none"},
    "Cinematic Story (Netflix)": {"font_name": "Arial", "font_size": 24, "base_color": "#FFFFFF", "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_thickness": 0, "shadow_color": "#000000", "shadow_size": 2, "bold": False, "italic": False, "uppercase": False, "highlight_size": 24, "words_per_block": 7, "mode": "highlight", "remove_punctuation": False, "animation": "none"},
    
    # --- NOWE PRESETY ANIMOWANE I ZAAWANSOWANE ---
    "Viral Spring Pop (Wyskakiwanie)": {"font_name": "Montserrat-ExtraBold", "font_size": 35, "base_color": "#FFFFFF", "highlight_color": "#00FF00", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 0, "bold": True, "italic": False, "uppercase": True, "highlight_size": 35, "words_per_block": 1, "mode": "word_by_word", "remove_punctuation": True, "animation": "spring"},
    "Smooth Karaoke (Płynne kolorowanie)": {"font_name": "Montserrat-ExtraBold", "font_size": 30, "base_color": "#FFFFFF", "highlight_color": "#FFD700", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 2, "bold": True, "italic": False, "uppercase": True, "highlight_size": 30, "words_per_block": 3, "mode": "highlight", "remove_punctuation": True, "animation": "karaoke"},
    "Podcast (Build-up)": {"font_name": "Montserrat-ExtraBold", "font_size": 32, "base_color": "#FFFFFF", "highlight_color": "#FFFF00", "outline_color": "#000000", "outline_thickness": 3, "shadow_color": "#000000", "shadow_size": 0, "bold": True, "italic": False, "uppercase": False, "highlight_size": 32, "words_per_block": 4, "mode": "build_up", "remove_punctuation": False, "animation": "none"},
    "Cinematic (Blur)": {"font_name": "Arial", "font_size": 28, "base_color": "#FFFFFF", "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_thickness": 1, "shadow_color": "#000000", "shadow_size": 2, "bold": False, "italic": False, "uppercase": False, "highlight_size": 28, "words_per_block": 5, "mode": "highlight", "remove_punctuation": False, "animation": "blur_reveal"},
    "Neon Pulse": {"font_name": "Arial", "font_size": 34, "base_color": "#FFFFFF", "highlight_color": "#FF00FF", "outline_color": "#000000", "outline_thickness": 2, "shadow_color": "#FF00FF", "shadow_size": 4, "bold": True, "italic": True, "uppercase": True, "highlight_size": 36, "words_per_block": 2, "mode": "highlight", "remove_punctuation": True, "animation": "color_pulse"},
    "CapCut (Tło słowa)": {"font_name": "Montserrat-ExtraBold", "font_size": 32, "base_color": "#FFFFFF", "highlight_color": "#FF00FF", "outline_color": "#000000", "outline_thickness": 0, "shadow_color": "#000000", "shadow_size": 1, "bold": True, "italic": False, "uppercase": False, "highlight_size": 32, "words_per_block": 4, "mode": "highlight_box", "remove_punctuation": False, "animation": "none", "bg_padding": 45}
}

# --- BAZA ANIMACJI ---
ANIMATION_TYPES = [
    "Brak", 
    "Wyskakiwanie (Spring Pop)", 
    "Płynne Karaoke", 
    "Trzęsienie (Jiggle)",
    "Wyłanianie (Blur Reveal)",
    "Nalot (Zoom In)",
    "Pulsowanie (Color Pulse)",
    "Wjazd 3D (Slide Up)"
]

ANIMATION_MAP = {
    "Brak": "none",
    "Wyskakiwanie (Spring Pop)": "spring",
    "Płynne Karaoke": "karaoke",
    "Trzęsienie (Jiggle)": "jiggle",
    "Wyłanianie (Blur Reveal)": "blur_reveal",
    "Nalot (Zoom In)": "zoom_in",
    "Pulsowanie (Color Pulse)": "color_pulse",
    "Wjazd 3D (Slide Up)": "slide_up"
}
REVERSE_ANIMATION_MAP = {v: k for k, v in ANIMATION_MAP.items()}

# --- SŁOWNIKI JĘZYKOWE ---
AVAILABLE_LANGS = ["Auto-detekcja", "Polski", "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Chiński"]
LANG_MAP = {
    "Auto-detekcja": None,
    "Automatyczne wykrywanie": None,
    "Polski": "pl",
    "Angielski": "en",
    "Niemiecki": "de",
    "Francuski": "fr",
    "Hiszpański": "es",
    "Włoski": "it",
    "Portugalski": "pt",
    "Holenderski": "nl",
    "Rosyjski": "ru",
    "Ukraiński": "uk",
    "Czeski": "cs",
    "Słowacki": "sk",
    "Szwedzki": "sv",
    "Norweski": "no",
    "Duński": "da",
    "Fiński": "fi",
    "Grecki": "el",
    "Rumuński": "ro",
    "Węgierski": "hu",
    "Bułgarski": "bg",
    "Chorwacki": "hr",
    "Serbski": "sr",
    "Turecki": "tr",
    "Arabski": "ar",
    "Hebrajski": "he",
    "Hindi": "hi",
    "Wietnamski": "vi",
    "Tajski": "th",
    "Indonezyjski": "id",
    "Japoński": "ja",
    "Koreański": "ko",
    "Chiński": "zh",
}
