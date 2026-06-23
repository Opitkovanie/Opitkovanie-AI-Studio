import os
import subprocess
import sys
from utils import get_ffmpeg_path

def get_font_path(font_name, is_bold, is_italic):
    """
    Sprytne dobieranie wariantów czcionek dla FFmpeg.
    FFmpeg (drawtext) nie potrafi sztucznie pogrubiać tekstu - wymaga konkretnego pliku.
    """
    if font_name == "Domyślna dla presetu" or not font_name:
        font_name = "Arial.ttf" # Fallback bezpieczeństwa
    elif font_name.startswith("._"):
        font_name = font_name[2:]
        
    base_name, ext = os.path.splitext(font_name)
    if not ext:
        ext = ".ttf"
        
    font_dir = "fonts"
    
    # Próbujemy znaleźć wersję Bold/Italic, jeśli użytkownik ją zaznaczył
    candidates = []
    if is_bold and is_italic:
        candidates = [f"{base_name}-BoldItalic{ext}", f"{base_name}BoldItalic{ext}"]
    elif is_bold:
        candidates = [f"{base_name}-Bold{ext}", f"{base_name}Bold{ext}"]
    elif is_italic:
        candidates = [f"{base_name}-Italic{ext}", f"{base_name}Italic{ext}"]
        
    for cand in candidates:
        path = os.path.join(font_dir, cand)
        if os.path.exists(path):
            return os.path.abspath(path).replace('\\', '/').replace(':', '\\:')
            
    # Jeśli nie ma specjalnego pliku (lub nie wybrano Bold/Italic), zwracamy standardowy
    path = os.path.join(font_dir, font_name)
    if os.path.exists(path):
        return os.path.abspath(path).replace('\\', '/').replace(':', '\\:')
        
    return ""

def build_ffmpeg_filters(current_v, settings, logo_idx, base_w):
    filters = []
    next_v = current_v
    
    # 1. Nakładanie graficznego LOGO (PNG/JPG)
    if settings.get("enable_logo") and logo_idx != -1:
        # Zabezpieczenia matematyczne na wypadek błędnych wartości z interfejsu
        scale = max(1, min(settings.get("logo_scale", 20), 100)) / 100.0
        x_pct = max(0, min(settings.get("logo_x", 50), 100)) / 100.0
        y_pct = max(0, min(settings.get("logo_y", 50), 100)) / 100.0
        opacity = max(0, min(settings.get("logo_opacity", 100), 100)) / 100.0
        
        logo_w = int(base_w * scale)
        if logo_w < 2: logo_w = 2  # Absolutne zabezpieczenie przed crashem FFmpeg (scale=0)
        if logo_w % 2 != 0: logo_w -= 1
        
        filter_str = f"[{logo_idx}:v]scale={logo_w}:-2,format=rgba,colorchannelmixer=aa={opacity}[logo_scaled]; "
        
        x_expr = f"(main_w-overlay_w)*{x_pct}"
        y_expr = f"(main_h-overlay_h)*{y_pct}"
        
        filter_str += f"{next_v}[logo_scaled]overlay=x='{x_expr}':y='{y_expr}':shortest=1[with_logo]"
        filters.append(filter_str)
        next_v = "[with_logo]"

    # 2. Nakładanie zaawansowanego ZNAKU WODNEGO (Tekst)
    raw_text = settings.get("wm_text", settings.get("text", ""))
    
    if settings.get("enable_text") and raw_text.strip():
        # Bezpieczne parsowanie znaków specjalnych dla FFmpeg
        text = raw_text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
        font_name = settings.get("wm_font", settings.get("font", ""))
        size = max(1, settings.get("wm_size", settings.get("size", 50)))
        color = settings.get("wm_color", settings.get("color", "#FFFFFF"))
        
        raw_opacity = settings.get("wm_opacity", settings.get("opacity", 100))
        opacity = max(0, min(raw_opacity, 100)) / 100.0
        
        x_pct = max(0, min(settings.get("wm_x", 50), 100)) / 100.0
        y_pct = max(0, min(settings.get("wm_y", 50), 100)) / 100.0
        
        # Bezpieczne pobieranie dodatkowych stylizacji dla ZARÓWNO Podglądu jak i Finalnego Renderu
        out_color = settings.get("wm_out_color", settings.get("out_color", "#000000"))
        out_thick = settings.get("wm_out_thick", settings.get("out_thick", 0))
        shad_color = settings.get("wm_shad_color", settings.get("shad_color", "#000000"))
        shad_size = settings.get("wm_shad_size", settings.get("shad_size", 0))
        is_bold = settings.get("wm_bold", settings.get("bold", False))
        is_italic = settings.get("wm_italic", settings.get("italic", False))
        
        font_path = get_font_path(font_name, is_bold, is_italic)
        font_param = f":fontfile='{font_path}'" if font_path else ""
        
        # Konwersja hexów ucinając znaki '#'
        c_clean = color.replace("#", "") if color else "FFFFFF"
        oc_clean = out_color.replace("#", "") if out_color else "000000"
        sc_clean = shad_color.replace("#", "") if shad_color else "000000"
        
        alpha_hex = f"{int(opacity * 255):02X}"
        
        x_expr = f"(w-tw)*{x_pct}"
        y_expr = f"(h-th)*{y_pct}"
        
        # Ominięcie błędnych cudzysłowów w pozycjach oraz natywny parametr alpha w formacie HEX+Alpha
        drawtext = f"drawtext=text='{text}'{font_param}:fontsize={size}:fontcolor=0x{c_clean}{alpha_hex}:x={x_expr}:y={y_expr}"
        
        # Dodajemy Obrys (Outline) 
        if out_thick > 0:
            drawtext += f":borderw={out_thick}:bordercolor=0x{oc_clean}{alpha_hex}"
            
        # Dodajemy Cień (Shadow)
        if shad_size > 0:
            drawtext += f":shadowx={shad_size}:shadowy={shad_size}:shadowcolor=0x{sc_clean}{alpha_hex}"
            
        filters.append(f"{next_v}{drawtext}[with_text]")
        next_v = "[with_text]"
        
    return "; ".join(filters), next_v

def generate_logo_and_text_preview(logo_path, settings, aspect_ratio, output_path):
    """
    Szybki generator podglądu dla zakładki "Logo i Znak Wodny".
    """
    res = "1080x1920" if aspect_ratio == "9:16" else "1920x1080"
    base_w = 1080 if aspect_ratio == "9:16" else 1920
    
    cmd_inputs = ["-f", "lavfi", "-i", f"color=c=0x444444:s={res}:d=3"]
    logo_idx = -1
    
    if settings.get("enable_logo") and logo_path and os.path.exists(logo_path):
        cmd_inputs.extend(["-loop", "1", "-framerate", "30", "-i", logo_path])
        logo_idx = 1
        
    extra_filters, current_v = build_ffmpeg_filters("[0:v]", settings, logo_idx, base_w)
    
    vcodec = "h264_videotoolbox" if sys.platform == "darwin" else "libx264"
    
    cmd = [get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error"] + cmd_inputs
    
    if extra_filters:
        cmd.extend(["-filter_complex", extra_filters, "-map", current_v])
    else:
        cmd.extend(["-map", "0:v"])
        
    cmd.extend(["-c:v", vcodec, "-b:v", "2M", "-pix_fmt", "yuv420p", output_path])
    
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.exists(output_path):
        err = (result.stderr or "").strip()
        raise RuntimeError(f"Nie udało się wygenerować podglądu logo. {err}".strip())
    return output_path
