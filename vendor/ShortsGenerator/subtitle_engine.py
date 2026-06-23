import os
import re
import base64
from math import floor
from config import SUBTITLE_PRESETS

def get_available_fonts():
    fonts = ["Arial", "Impact", "Consolas"] 
    if os.path.exists("fonts"):
        for file in os.listdir("fonts"):
            if file.startswith("._"):
                continue
            if file.lower().endswith(".ttf") or file.lower().endswith(".otf"):
                fonts.append(file)
    return sorted(set(fonts), key=str.lower)

def hex_to_ass_color(hex_str):
    if not hex_str: return "&H00FFFFFF&"
    hex_clean = hex_str.lstrip('#').strip()
    if len(hex_clean) == 6:
        r, g, b = hex_clean[0:2], hex_clean[2:4], hex_clean[4:6]
        return f"&H00{b}{g}{r}&".upper()
    return "&H00FFFFFF&"

# Natywny dekoder metadanych czcionek (wydobywa Prawdziwą Nazwę ukrytą w pliku TTF/OTF)
def get_font_family_from_file(filepath):
    best_name = None
    try:
        with open(filepath, 'rb') as f:
            f.seek(4)
            num_tables = int.from_bytes(f.read(2), 'big')
            f.seek(12)
            for _ in range(num_tables):
                tag = f.read(4)
                f.read(4) # checksum
                offset = int.from_bytes(f.read(4), 'big')
                length = int.from_bytes(f.read(4), 'big')
                if tag == b'name':
                    f.seek(offset)
                    f.read(2) # format
                    num_records = int.from_bytes(f.read(2), 'big')
                    string_offset = int.from_bytes(f.read(2), 'big')
                    
                    for _ in range(num_records):
                        platform_id = int.from_bytes(f.read(2), 'big')
                        encoding_id = int.from_bytes(f.read(2), 'big')
                        language_id = int.from_bytes(f.read(2), 'big')
                        name_id = int.from_bytes(f.read(2), 'big')
                        str_len = int.from_bytes(f.read(2), 'big')
                        str_off = int.from_bytes(f.read(2), 'big')
                        
                        if name_id == 1: # Kod '1' oznacza Font Family Name
                            curr_pos = f.tell()
                            f.seek(offset + string_offset + str_off)
                            name_bytes = f.read(str_len)
                            try:
                                name = ""
                                if platform_id == 3 or platform_id == 0: # Windows lub Unicode
                                    name = name_bytes.decode('utf-16-be')
                                elif platform_id == 1: # Mac
                                    name = name_bytes.decode('mac_roman', errors='ignore')
                                    
                                name = name.replace('\x00', '').strip()
                                if name:
                                    best_name = name
                                    if platform_id == 3 and language_id == 1033:
                                        return best_name # Priorytet dla poprawnej angielskiej nazwy, koniec szukania
                            except:
                                pass
                            f.seek(curr_pos)
                    break
    except Exception as e:
        print(f"[DEBUG] Błąd odczytu nagłówków TTF: {e}")
    
    return best_name if best_name else os.path.splitext(os.path.basename(filepath))[0]


def find_font_file(font_name):
    """Znajdź plik czcionki po nazwie w katalogu fonts/."""
    if not os.path.exists("fonts"):
        return None
    # Normalizacja: usuwa myślniki i spacje dla porównania
    def norm(s):
        return s.lower().replace('-', '').replace(' ', '')
    for f in os.listdir("fonts"):
        ext = f.lower().split('.')[-1]
        if ext not in ('ttf', 'otf'):
            continue
        name_no_ext = os.path.splitext(f)[0]
        if norm(name_no_ext) == norm(font_name):
            return os.path.join("fonts", f)
    # Szerokie dopasowanie cząstkowe
    for f in os.listdir("fonts"):
        if norm(font_name) in norm(f) and f.lower().endswith(('.ttf', '.otf')):
            return os.path.join("fonts", f)
    return None


def compute_word_positions_pillow(words, font_path, font_size, pos_x, res_x, margin_lr):
    """
    Oblicza pozycje słów używając Pillow z ciasnymi bbox liter.
    Zwraca listę słowników z left_x, advance_w, bbox_x0/y0/w/h, line_num.
    """
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_path, int(font_size))
    except Exception:
        return None

    usable_w = res_x - 2 * margin_lr

    def advance_w(text):
        try:
            return float(font.getlength(text))
        except Exception:
            try:
                x0, _y0, x1, _y1 = font.getbbox(text)
                return float(x1 - x0)
            except Exception:
                return float(len(text) * font_size * 0.6)

    def tight_bbox(text):
        try:
            x0, y0, x1, y1 = font.getbbox(text)
            return {'x0': int(x0), 'y0': int(y0), 'w': int(x1 - x0), 'h': int(y1 - y0)}
        except Exception:
            w = advance_w(text)
            h = font_size
            return {'x0': 0, 'y0': 0, 'w': int(w), 'h': int(h)}

    space_w = advance_w(" ")

    # Symulacja zawijania word-wrap (WrapStyle 1)
    lines = []
    cur_line = []
    cur_w = 0.0
    for idx, w in enumerate(words):
        adv = advance_w(w)
        need = adv if not cur_line else (space_w + adv)
        if cur_line and cur_w + need > usable_w:
            lines.append(cur_line)
            cur_line = [idx]
            cur_w = adv
        else:
            cur_line.append(idx)
            cur_w += need
    if cur_line:
        lines.append(cur_line)

    try:
        asc, desc = font.getmetrics()
    except Exception:
        asc, desc = int(font_size * 0.8), int(font_size * 0.2)

    result = [None] * len(words)
    for line_num, line_idxs in enumerate(lines):
        line_words_texts = [words[i] for i in line_idxs]
        line_text = " ".join(line_words_texts)
        line_w = advance_w(line_text)
        
        start_x = pos_x - line_w / 2
        
        for pos_in_line, idx in enumerate(line_idxs):
            word = words[idx]
            
            # X offset using precise kerning by measuring prefix
            if pos_in_line == 0:
                word_x_offset = 0
            else:
                prefix_text = " ".join(line_words_texts[:pos_in_line]) + " "
                word_x_offset = advance_w(prefix_text)
                
            x = start_x + word_x_offset
            
            adv  = advance_w(word)
            bbox = tight_bbox(word)
            result[idx] = {
                'left_x':    int(x),
                'advance_w': int(adv),
                'bbox_x0':   bbox['x0'],
                'bbox_y0':   bbox['y0'],
                'bbox_w':    bbox['w'],
                'bbox_h':    bbox['h'],
                'asc':       int(asc),
                'desc':      int(desc),
                'line_num':  line_num,
            }
    return result

def generate_font_preview_html(preset_name, font_name_override, bcolor, hcolor, size, hsize, out_color, out_thick, shad_color, shad_size, is_bold, is_italic, is_upper, mode, animation="none", bg_padding=45):
    p = SUBTITLE_PRESETS.get(preset_name, SUBTITLE_PRESETS["Hormozi (Classic)"])
    font_to_use = font_name_override if font_name_override and font_name_override != "Domyślna dla presetu" else p["font_name"]
    if font_to_use and font_to_use.startswith("._"):
        font_to_use = font_to_use[2:]

    font_file_path = None
    if os.path.exists("fonts"):
        for file in os.listdir("fonts"):
            if file.startswith("._"):
                continue
            if file.startswith(font_to_use) and file.lower().endswith((".ttf", ".otf")):
                font_file_path = os.path.join("fonts", file)
                break

    font_css = ""
    if font_file_path:
        with open(font_file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        font_css = f"@font-face {{ font-family: 'PreviewFont'; src: url(data:font/truetype;charset=utf-8;base64,{b64}); }}"

    transform = "uppercase" if is_upper else "none"
    font_weight = "bold" if is_bold else "normal"
    font_style = "italic" if is_italic else "normal"
    
    base_preview_px = 35
    ratio = hsize / size if size > 0 else 1.0
    highlight_preview_px = base_preview_px * ratio
    
    # Generowanie dynamicznych klatek kluczowych CSS dla poszczególnych efektów!
    css_anim = ""
    anim_class = ""
    
    if animation == "spring":
        css_anim = "@keyframes springPop { 0% { transform: scale(0.5); } 50% { transform: scale(1.2); } 100% { transform: scale(1); } }"
        anim_class = "animation: springPop 0.4s ease-out forwards;"
    elif animation == "jiggle":
        css_anim = "@keyframes jiggle { 0% { transform: rotate(0deg); } 25% { transform: rotate(-5deg); } 50% { transform: rotate(5deg); } 75% { transform: rotate(-3deg); } 100% { transform: rotate(0deg); } }"
        anim_class = "animation: jiggle 0.4s infinite;"
    elif animation == "karaoke":
        css_anim = f"@keyframes karaokeFill {{ 0% {{ color: {bcolor}; }} 100% {{ color: {hcolor}; }} }}"
        anim_class = "animation: karaokeFill 1s ease-in-out infinite alternate;"
    elif animation == "blur_reveal":
        css_anim = "@keyframes blurReveal { 0% { filter: blur(10px); opacity: 0; } 100% { filter: blur(0px); opacity: 1; } }"
        anim_class = "animation: blurReveal 0.4s ease-out forwards;"
    elif animation == "zoom_in":
        css_anim = "@keyframes zoomIn { 0% { transform: scale(0); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }"
        anim_class = "animation: zoomIn 0.4s ease-out forwards;"
    elif animation == "color_pulse":
        css_anim = f"@keyframes colorPulse {{ 0% {{ color: {bcolor}; transform: scale(1); }} 50% {{ color: {hcolor}; transform: scale(1.1); }} 100% {{ color: {bcolor}; transform: scale(1); }} }}"
        anim_class = "animation: colorPulse 1s ease-in-out infinite;"
    elif animation == "slide_up":
        css_anim = "@keyframes slideUp { 0% { transform: perspective(400px) rotateX(90deg); opacity: 0; } 100% { transform: perspective(400px) rotateX(0deg); opacity: 1; } }"
        anim_class = "animation: slideUp 0.4s ease-out forwards;"
        
    content_html = ""
    if mode == "word_by_word":
         content_html = f'<span style="font-size: {highlight_preview_px}px; color: {hcolor}; display: inline-block; {anim_class}">ABCdef 123!@#</span>'
    elif mode == "build_up":
         content_html = f'<span style="font-size: {base_preview_px}px; color: {bcolor}; display: inline-block;">Próbka</span> <span style="font-size: {highlight_preview_px}px; color: {hcolor}; display: inline-block; {anim_class}">ABCdef</span> <span style="opacity: 0;">123!@#</span>'
    elif mode == "fade":
         css_anim += " @keyframes fadeInOut { 0% { opacity: 0.2; } 100% { opacity: 1; } }"
         content_html = f'<span style="font-size: {base_preview_px}px; color: {bcolor}; display: inline-block; animation: fadeInOut 1.5s infinite alternate;">Próbka ABCdef 123!@#</span>'
    elif mode == "highlight_box":
         pad_x = int(highlight_preview_px * (bg_padding / 100.0) * 0.5)
         pad_y = int(highlight_preview_px * (bg_padding / 100.0) * 0.35)
         shadow_css = f"text-shadow: 2px 2px 0 {out_color}, -2px -2px 0 {out_color}, 2px -2px 0 {out_color}, -2px 2px 0 {out_color};" if out_thick > 0 else ""
         content_html = f'Próbka <span style="font-size: {highlight_preview_px}px; color: {bcolor}; background-color: {hcolor}; display: inline-block; padding: {pad_y}px {pad_x}px; border-radius: 0px; margin: 0 2px; line-height: 1.1; {shadow_css} {anim_class}">ABCdef</span> 123!@#'
    else:
         content_html = f'Próbka <span style="font-size: {highlight_preview_px}px; color: {hcolor}; display: inline-block; {anim_class}">ABCdef 123!@#</span>'

    html = f"""
    <style>
        {font_css}
        {css_anim}
        .sub-preview {{
            font-family: 'PreviewFont', Arial, sans-serif;
            background-color: #1a1a1a;
            background-image: radial-gradient(#333 1px, transparent 1px);
            background-size: 10px 10px;
            padding: 30px 10px;
            border-radius: 12px;
            text-align: center;
            font-size: {base_preview_px}px;
            font-weight: {font_weight};
            font-style: {font_style};
            text-transform: {transform};
            color: {bcolor};
            -webkit-text-stroke: {out_thick}px {out_color};
            text-shadow: {shad_size}px {shad_size}px 0 {shad_color};
            margin-top: 5px;
            margin-bottom: 15px;
            border: 1px solid #333;
        }}
    </style>
    <div class="sub-preview">
        {content_html}
    </div>
    """
    return html

def seconds_to_ass_time(seconds):
    h = floor(seconds / 3600); m = floor((seconds % 3600) / 60); s = floor(seconds % 60); cs = floor((seconds - floor(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_viral_ass_subtitles(segments, global_words, output_ass_path, preset_name="Hormozi (Classic)", custom_font=None, aspect_ratio="9:16", 
                                 override_bcolor=None, override_hcolor=None, override_size=None, override_margin=None, auto_scale=False,
                                 override_hsize=None, override_out_color=None, override_out_thick=None, override_shad_color=None, override_shad_size=None,
                                 override_bold=None, override_italic=None, override_upper=None, override_words=None, override_mode=None, override_punct=None,
                                 override_animation=None, override_bg_padding=None, timeline_mode=False):
    
    p = SUBTITLE_PRESETS.get(preset_name, SUBTITLE_PRESETS["Hormozi (Classic)"])
    
    # --- Poprawiona logika czcionek: zachowujemy font_path_for_metrics od razu ---
    font_family = p["font_name"]
    font_path_for_metrics = None  # bedzie uzyty przez highlight_box
    if custom_font and custom_font != "Domyślna dla presetu":
        candidate = os.path.join("fonts", custom_font)
        if os.path.exists(candidate):
            font_path_for_metrics = candidate
            font_family = get_font_family_from_file(candidate)
        else:
            font_family = os.path.splitext(custom_font)[0]
    # Jesli brak custom_font, szukaj pliku pasujacego do font_family presetu
    if font_path_for_metrics is None:
        found_font = find_font_file(font_family)
        if found_font:
            font_path_for_metrics = os.path.abspath(found_font)
            font_family = get_font_family_from_file(found_font)
    
    b_color_hex = override_bcolor if override_bcolor is not None else p["base_color"]
    h_color_hex = override_hcolor if override_hcolor is not None else p["highlight_color"]
    out_color_hex = override_out_color if override_out_color is not None else p["outline_color"]
    shad_color_hex = override_shad_color if override_shad_color is not None else p["shadow_color"]
    
    base_color = hex_to_ass_color(b_color_hex)
    high_color = hex_to_ass_color(h_color_hex)
    out_color = hex_to_ass_color(out_color_hex)
    shad_color = hex_to_ass_color(shad_color_hex)
    
    use_bold = override_bold if override_bold is not None else p["bold"]
    use_italic = override_italic if override_italic is not None else p["italic"]
    use_upper = override_upper if override_upper is not None else p["uppercase"]
    use_punct = override_punct if override_punct is not None else p["remove_punctuation"]
    use_mode = override_mode if override_mode is not None else p["mode"]
    use_animation = override_animation if override_animation is not None else p.get("animation", "none")
    words_per_block = override_words if override_words is not None else p["words_per_block"]
    use_bg_padding = override_bg_padding if override_bg_padding is not None else p.get("bg_padding", 45)
    
    bold_val = "-1" if use_bold else "0"
    italic_val = "-1" if use_italic else "0"
    
    raw_size = override_size if override_size is not None else p["font_size"]
    raw_hl_size = override_hsize if override_hsize is not None else p.get("highlight_size", raw_size + 5)
    raw_out_thick = override_out_thick if override_out_thick is not None else p["outline_thickness"]
    raw_shad_size = override_shad_size if override_shad_size is not None else p["shadow_size"]
    
    if aspect_ratio == "16:9":
        res_x, res_y = 1920, 1080
        scale_factor = 3
        margin_v = override_margin if override_margin is not None else 100 
    else:
        res_x, res_y = 1080, 1920
        scale_factor = 3
        margin_v = override_margin if override_margin is not None else 600

    base_size = raw_size * scale_factor
    highlight_size = raw_hl_size * scale_factor
    outline_thick = raw_out_thick * (scale_factor * 0.5)
    shadow_sz = raw_shad_size * (scale_factor * 0.5)
    
    pos_x = res_x // 2
    pos_y = res_y - margin_v
    margin_lr = 40

    processed_segments = []
    max_chars_in_chunk = 0

    for seg in segments:
        seg_start = seg['start_time']
        seg_end = seg['end_time']
        seg_duration = seg_end - seg_start
        
        seg_words = [w for w in global_words if w['start'] >= seg_start - 0.5 and w['end'] <= seg_end + 0.5]
        rel_words = []
        for w in seg_words:
            word_rel_start = max(0.0, w['start'] - seg_start)
            word_rel_end = min(seg_duration, w['end'] - seg_start)
            if word_rel_end > word_rel_start:
                clean_w = str(w['word']).strip()
                clean_w = clean_w.replace('{', '').replace('}', '')
                
                if use_punct: clean_w = re.sub(r'[.,!?;]', '', clean_w)
                if use_upper: clean_w = clean_w.upper()
                if clean_w: 
                    rel_words.append({'word': clean_w, 'rel_start': word_rel_start, 'rel_end': word_rel_end})
        
        # Group words into caption blocks. Besides the per-block word cap, START A NEW
        # BLOCK after a real speech pause (> GAP_SPLIT s) so a caption never spans a
        # silence — otherwise the block stays on screen through the whole gap and its
        # later words are visible long before they're actually spoken.
        GAP_SPLIT = 0.6
        chunks = []
        current_chunk = []
        for w in rel_words:
            if current_chunk and (
                (w['rel_start'] - current_chunk[-1]['rel_end'] > GAP_SPLIT)
                or len(current_chunk) >= words_per_block
            ):
                chunks.append(current_chunk)
                current_chunk = []
            current_chunk.append(w)
        if current_chunk: chunks.append(current_chunk)

        for chunk in chunks:
            if use_mode == "word_by_word":
                for w in chunk:
                    w_clean = w['word'].replace('\\N', '\n').replace('\\n', '\n')
                    longest_line = max((len(line) for line in w_clean.split('\n')), default=0)
                    if longest_line > max_chars_in_chunk:
                        max_chars_in_chunk = longest_line
            else:
                line_text = " ".join([w['word'] for w in chunk])
                line_text_clean = line_text.replace('\\N', '\n').replace('\\n', '\n')
                longest_line = max((len(line) for line in line_text_clean.split('\n')), default=0)
                if longest_line > max_chars_in_chunk:
                    max_chars_in_chunk = longest_line
                    
        # KRYTYCZNA POPRAWKA: Zapisanie przeprocesowanych bloków do głównej zmiennej, aby FFmpeg miał co nałożyć!
        processed_segments.append({
            'duration': seg_duration,
            'start_time': float(seg_start),
            'chunks': chunks
        })

    was_scaled = False
    final_raw_size = raw_size
    
    if auto_scale and max_chars_in_chunk > 0:
        max_w = res_x - (margin_lr * 2) 
        char_aspect = 0.58 
        max_used_size = highlight_size if use_mode != "no_highlight" else base_size
        estimated_width = max_chars_in_chunk * max_used_size * char_aspect
        
        if estimated_width > max_w:
            ratio_down = max_w / estimated_width
            base_size = int(base_size * ratio_down)
            highlight_size = int(highlight_size * ratio_down)
            outline_thick = max(1.0, outline_thick * ratio_down)
            shadow_sz = max(0.0, shadow_sz * ratio_down)
            final_raw_size = int(raw_size * ratio_down)
            was_scaled = True

    ass_fontname = font_family
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
ScaledBorderAndShadow: yes
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{ass_fontname},{base_size},{base_color},&H00000000,{out_color},{shad_color},{bold_val},{italic_val},0,0,100,100,0,0,1,{outline_thick},{shadow_sz},8,{margin_lr},{margin_lr},0,1
"""
    ass_content += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    current_short_time = 0.0
    
    # Tag resetujący transformacje dla wyrazów bez animacji (żeby animacja jednego słowa nie zepsuła reszty)
    reset_tags = "\\fscx100\\fscy100\\frz0\\frx0\\blur0\\alpha&H00&"
    
    for p_seg in processed_segments:
        chunks = p_seg['chunks']
        seg_duration = p_seg['duration']
        # Standard Shorts are concatenated during rendering, so their subtitle clock
        # advances scene by scene. A custom full-film Short keeps the source timeline;
        # using the concatenated clock there removed every real pause and made captions
        # race through the film at the start.
        segment_base_time = p_seg['start_time'] if timeline_mode else current_short_time

        for chunk_idx, chunk in enumerate(chunks):
            if not chunk: continue
            
            # Specjalna obsługa trybu Fade - generujemy tylko jedno zdarzenie na cały blok
            if use_mode == "fade":
                line_text = " ".join([w['word'] for w in chunk])
                line_text = f"{{\\fad(200,200)\\fs{base_size}\\c{base_color}}}{line_text}"
                start_str = seconds_to_ass_time(segment_base_time + chunk[0]['rel_start'])
                end_str = seconds_to_ass_time(segment_base_time + chunk[-1]['rel_end'] + 0.1)
                clean_text = line_text.strip().replace('\n', '\\N').replace('\\n', '\\N')
                pos_tag = f"{{\\an8\\pos({pos_x},{pos_y})}}"
                ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{pos_tag}{clean_text}\n"
                continue
            
            # Cap how long a word stays up so a pause never freezes the caption.
            MAX_HOLD = 0.8
            for i, active_word in enumerate(chunk):
                start_time = active_word['rel_start']

                if i < len(chunk)-1:
                    # Bridge to the next word, but never hold longer than MAX_HOLD past
                    # this word (blocks are already gap-split, so this is just a guard).
                    end_time = min(chunk[i+1]['rel_start'], active_word['rel_end'] + MAX_HOLD)
                else:
                    end_time = active_word['rel_end'] + 0.1
                    if chunk_idx < len(chunks) - 1 and len(chunks[chunk_idx+1]) > 0:
                        next_chunk_start = chunks[chunk_idx+1][0]['rel_start']
                        # Only bridge into the next block across SHORT gaps (no flicker);
                        # during a real silence the caption clears instead of lingering.
                        if next_chunk_start - active_word['rel_end'] <= MAX_HOLD:
                            end_time = next_chunk_start
                        else:
                            end_time = active_word['rel_end'] + 0.3
                
                line_text = ""
                word_dur_ms = int((end_time - start_time) * 1000)
                
                anim_tag = ""
                if use_animation == "spring": anim_tag = f"\\fscx50\\fscy50\\t(0,60,\\fscx120\\fscy120)\\t(60,120,\\fscx100\\fscy100)"
                elif use_animation == "jiggle": anim_tag = f"\\t(0,30,\\frz-5)\\t(30,60,\\frz5)\\t(60,90,\\frz-3)\\t(90,120,\\frz3)\\t(120,150,\\frz0)"
                elif use_animation == "karaoke": anim_tag = f"\\c{base_color}\\t(0,{word_dur_ms},\\c{high_color})"
                elif use_animation == "blur_reveal": anim_tag = f"\\alpha&HFF&\\blur15\\t(0,150,\\alpha&H00&\\blur0)"
                elif use_animation == "zoom_in": anim_tag = f"\\fscx0\\fscy0\\t(0,150,\\fscx100\\fscy100)"
                elif use_animation == "color_pulse": anim_tag = f"\\c{base_color}\\t(0,{word_dur_ms//2},\\c{high_color}\\fscx110\\fscy110)\\t({word_dur_ms//2},{word_dur_ms},\\c{base_color}\\fscx100\\fscy100)"
                elif use_animation == "slide_up": anim_tag = f"\\frx90\\alpha&HFF&\\t(0,150,\\frx0\\alpha&H00&)"
                
                if use_mode == "highlight":
                    for j, w in enumerate(chunk):
                        if i == j:
                            if use_animation == "karaoke":
                                line_text += f"{{\\fs{highlight_size}{anim_tag}}}{w['word']} "
                            else:
                                line_text += f"{{\\fs{highlight_size}\\c{high_color}{anim_tag}}}{w['word']} "
                        else:
                            line_text += f"{{\\fs{base_size}\\c{base_color}{reset_tags}}}{w['word']} "
                elif use_mode == "highlight_box":
                    # === PROSTOKAT \p1 pod aktywnym slowem + jedna linia \an8 na każdy wiersz ===
                    chunk_words = [w['word'] for w in chunk]

                    word_positions = None
                    if font_path_for_metrics:
                        # Udowodniony eksperymentalnie współczynnik dla libass -> rozmiar czcionki
                        ass_to_pil = 0.78125
                        pillow_font_size = int(highlight_size * ass_to_pil)
                        # Piksele Pillow = Piksele FFmpeg (1:1), wiec nie skalujemy pos_x ani res_x
                        word_positions_pil = compute_word_positions_pillow(
                            chunk_words, font_path_for_metrics, pillow_font_size,
                            pos_x, res_x, margin_lr
                        )
                        if word_positions_pil:
                            word_positions = word_positions_pil

                    pad_x     = int(highlight_size * (use_bg_padding / 100.0) * 0.5)
                    pad_y_box = int(highlight_size * (use_bg_padding / 100.0) * 0.35)
                    line_h    = int(highlight_size * 1.35)

                    box_dialogues = []
                    # Zgrupujemy foreground wg line_num żeby wypuścić po jednym \an8 na linię
                    lines_fg = {}

                    if word_positions and word_positions[i] is not None:
                        active_wp = word_positions[i]
                        lx = active_wp['left_x']
                        ln = active_wp['line_num']
                        bbox_w = active_wp['bbox_w']
                        
                        # Zunifikowane metryki
                        asc = active_wp.get('asc', int(highlight_size * 0.8))
                        desc = active_wp.get('desc', int(highlight_size * 0.2))
                    else:
                        lx = pos_x
                        ln = 0
                        bbox_w = int(highlight_size * max(1.2, min(len(chunk[i]['word']) * 0.65, 2.6)))
                        asc = int(highlight_size * 0.8)
                        desc = int(highlight_size * 0.2)
                        
                    top_y = pos_y + ln * line_h

                    pad_x     = int(highlight_size * (use_bg_padding / 100.0) * 0.5)
                    pad_y_box = int(highlight_size * (use_bg_padding / 100.0) * 0.35)

                    rw = bbox_w + 2 * pad_x
                    rh = (asc + desc) + 2 * pad_y_box
                    
                    cx = lx + bbox_w / 2
                    cy = top_y + (asc + desc) / 2
                    
                    rx = cx - rw / 2
                    ry = cy - rh / 2
                    
                    # Kąty zaokrąglone nieco mocniej, z zachowaniem górnej granicy wielkości kształtu
                    r = min(int(highlight_size * 0.15 + pad_y_box * 0.6), int(rh / 2), int(rw / 2)) if use_bg_padding > 0 else 0

                    anim_tag_box = "\\fad(80,80)"
                    if use_animation == "zoom_in":
                        dur = int((active_word['rel_end'] - active_word['rel_start']) * 1000)
                        anim_tag_box += f"\\t(0,{dur//2},\\fscx110\\fscy110)\\t({dur//2},{dur},\\fscx100\\fscy100)"

                    if r > 0:
                        path_str = (
                            f"m {int(r)} 0 l {int(rw-r)} 0 "
                            f"b {int(rw)} 0 {int(rw)} 0 {int(rw)} {int(r)} "
                            f"l {int(rw)} {int(rh-r)} "
                            f"b {int(rw)} {int(rh)} {int(rw)} {int(rh)} {int(rw-r)} {int(rh)} "
                            f"l {int(r)} {int(rh)} "
                            f"b 0 {int(rh)} 0 {int(rh)} 0 {int(rh-r)} "
                            f"l 0 {int(r)} "
                            f"b 0 0 0 0 {int(r)} 0"
                        )
                    else:
                        path_str = f"m 0 0 l {int(rw)} 0 l {int(rw)} {int(rh)} l 0 {int(rh)}"

                    rect = (
                        f"{{\\an7\\pos({rx},{ry}){anim_tag_box}"
                        f"\\1c{high_color}\\3c{high_color}\\4c{high_color}"
                        f"\\1a&H00&\\3a&H00&\\bord0\\shad0\\p1}}"
                        f"{path_str}"
                        f"{{\\p0}}"
                    )
                    box_dialogues.append(rect)
                    # Zbudowanie tekstów per linia 
                    for j, w in enumerate(chunk):
                        if word_positions and word_positions[j] is not None:
                            curr_ln = word_positions[j]['line_num']
                        else:
                            curr_ln = 0

                        if curr_ln not in lines_fg:
                            lines_fg[curr_ln] = ""

                        if j == i:
                            if use_animation == "karaoke":
                                lines_fg[curr_ln] += f"{{\\fs{highlight_size}{anim_tag}}}{w['word']} "
                            else:
                                lines_fg[curr_ln] += f"{{\\fs{highlight_size}\\c{base_color}{anim_tag}}}{w['word']} "
                        else:
                            lines_fg[curr_ln] += f"{{\\fs{highlight_size}\\c{base_color}{reset_tags}}}{w['word']} "

                    fg_dialogues = []
                    for ln, text_str in lines_fg.items():
                        ly = pos_y + ln * line_h
                        # używamy pos_x, ly jako center z an8
                        fw = (
                            f"{{\\an8\\pos({pos_x},{ly})\\fs{highlight_size}"
                            f"\\c{base_color}\\3c{out_color}"
                            f"\\bord{outline_thick}\\shad{shadow_sz}}}"
                            f"{text_str.strip()}"
                        )
                        fg_dialogues.append(fw)

                    line_text = (box_dialogues, fg_dialogues)

                elif use_mode == "word_by_word":
                    if use_animation == "karaoke":
                        line_text = f"{{\\fs{highlight_size}{anim_tag}}}{chunk[i]['word']}"
                    else:
                        line_text = f"{{\\fs{highlight_size}\\c{high_color}{anim_tag}}}{chunk[i]['word']}"
                elif use_mode == "build_up":
                    for j, w in enumerate(chunk):
                        if j < i:
                            line_text += f"{{\\fs{base_size}\\c{base_color}{reset_tags}}}{w['word']} "
                        elif j == i:
                            if use_animation == "karaoke":
                                line_text += f"{{\\fs{highlight_size}{anim_tag}}}{w['word']} "
                            else:
                                line_text += f"{{\\fs{highlight_size}\\c{high_color}{anim_tag}}}{w['word']} "
                        else:
                            # Przezroczystość zachowuje fizyczne pozycje słów "z przyszłości" w układzie tekstu!
                            line_text += f"{{\\alpha&HFF&}}{w['word']} "
                else: 
                    line_text = " ".join([w['word'] for w in chunk])
                
                start_str = seconds_to_ass_time(segment_base_time + start_time)
                end_str = seconds_to_ass_time(segment_base_time + end_time)
                pos_tag = f"{{\\an8\\pos({pos_x},{pos_y})}}"
                
                if isinstance(line_text, tuple):
                    boxes, fgs = line_text[0], line_text[1]
                    for rect_text in boxes:
                        ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{rect_text}\n"
                    for word_text in fgs:
                        ass_content += f"Dialogue: 1,{start_str},{end_str},Default,,0,0,0,,{word_text}\n"
                else:
                    clean_text = line_text.strip().replace('\n', '\\N').replace('\\n', '\\N')
                    ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{pos_tag}{clean_text}\n"

        if not timeline_mode:
            current_short_time += seg_duration

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
        
    return output_ass_path, was_scaled, final_raw_size
