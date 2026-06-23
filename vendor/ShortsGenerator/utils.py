import os
import shutil
import time
import glob
import json
import subprocess

def _find_working_binary(name):
    candidates = []
    if os.name == "posix":
        if name == "ffmpeg":
            candidates.append("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
        elif name == "ffprobe":
            candidates.append("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
        candidates.extend([
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
        ])
    found = shutil.which(name)
    if found:
        candidates.append(found)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not os.path.exists(candidate):
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            pass
    return name

def get_ffmpeg_path():
    return _find_working_binary("ffmpeg")

def get_ffprobe_path():
    return _find_working_binary("ffprobe")

def cleanup_trash():
    now = time.time()
    for trash in glob.glob("workspace/**/*.trash*", recursive=True):
        try:
            if now - os.path.getmtime(trash) > 60: 
                os.remove(trash)
        except: pass

def force_remove_dir(dir_path):
    if not os.path.exists(dir_path): return
    abs_path = os.path.abspath(dir_path)
    try: shutil.rmtree(abs_path)
    except Exception: pass
    if os.path.exists(abs_path): subprocess.run(["rm", "-rf", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def force_remove_file(file_path):
    if not os.path.exists(file_path): return
    abs_path = os.path.abspath(file_path)
    try: os.remove(abs_path)
    except Exception: pass
    if os.path.exists(abs_path): subprocess.run(["rm", "-f", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def safe_trash_file(filepath):
    if os.path.exists(filepath):
        try:
            os.rename(filepath, filepath + f".trash_{int(time.time())}")
        except: pass

def get_video_stats(filepath):
    size_mb = 0.0
    real_bitrate = 0.0
    real_codec = ""
    actual_res = ""
    if os.path.exists(filepath):
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        try:
            cmd = [get_ffprobe_path(), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath]
            result = subprocess.run(cmd, capture_output=True, text=True)
            info = json.loads(result.stdout)
            
            video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            if video_stream:
                w = int(video_stream.get("width", 0))
                h = int(video_stream.get("height", 0))
                if w > 0 and h > 0:
                    short_edge = min(w, h)
                    if short_edge >= 2100: actual_res = "4K"
                    elif short_edge >= 1400: actual_res = "2K (1440p)"
                    elif short_edge >= 1000: actual_res = "1080p"
                    elif short_edge >= 700: actual_res = "720p"
                    elif short_edge >= 400: actual_res = "480p"
                    else: actual_res = f"{w}x{h}"
                    
                c_name = video_stream.get("codec_name", "").lower()
                if c_name in ['h264', 'avc']: real_codec = "H264"
                elif c_name in ['hevc', 'h265']: real_codec = "H265"
                else: real_codec = c_name.upper()
                
            format_info = info.get("format", {})
            bitrate_str = format_info.get("bit_rate")
            if bitrate_str:
                real_bitrate = float(bitrate_str) / 1_000_000
            else:
                dur_str = format_info.get("duration")
                if dur_str and float(dur_str) > 0:
                    real_bitrate = (os.path.getsize(filepath) * 8 / float(dur_str)) / 1_000_000
        except Exception: pass
    return {"size_mb": size_mb, "bitrate": real_bitrate, "codec": real_codec, "resolution": actual_res}

def load_settings():
    try:
        if os.path.exists("workspace/settings.json"):
            with open("workspace/settings.json", "r", encoding="utf-8") as f: return json.load(f)
    except Exception: pass
    return {}

def save_settings(settings):
    with open("workspace/settings.json", "w", encoding="utf-8") as f: json.dump(settings, f)

def get_all_projects():
    projects = []
    for d in os.listdir("workspace/sessions"):
        dir_path = os.path.join("workspace/sessions", d)
        data_file = os.path.join(dir_path, "data.json")
        if os.path.isdir(dir_path) and os.path.exists(data_file): projects.append(d)
    projects.sort(reverse=True)
    return projects

def load_project(project_id):
    path = os.path.join("workspace", "sessions", project_id, "data.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return None

def format_project_name(pid):
    if pid == "-- Wybierz --": return pid
    data = load_project(pid)
    if data:
        if "display_name" in data: return data["display_name"]
        date_part = pid[:19].replace("_", " ")
        shorts_count = len(data.get("ai_outputs", []))
        return f"Wideo ({shorts_count} shortów) - {date_part}"
    return pid

def delete_project(project_id):
    proj_data = load_project(project_id)
    video_to_delete = proj_data.get("video_file", "") if proj_data else ""
    force_remove_dir(os.path.join("workspace", "sessions", project_id))
    
    if not video_to_delete: return
    is_used = any(
        (load_project(pid) or {}).get("video_file") == video_to_delete
        for pid in get_all_projects()
        if pid != project_id
    )
    if not is_used and "workspace/downloads" in video_to_delete.replace('\\', '/'): 
        force_remove_file(video_to_delete)
        basename = os.path.basename(video_to_delete)
        vid_id = basename.rsplit('_', 1)[0] if '_' in basename else os.path.splitext(basename)[0]
        force_remove_file(os.path.join("workspace", "downloads", f"{vid_id}_transcript.txt"))
        force_remove_file(os.path.join("workspace", "downloads", f"{vid_id}_words.json"))
        for f in glob.glob(os.path.join("workspace", "downloads", f"{vid_id}*.vtt")):
            force_remove_file(f)

def add_to_favorites(fav_id, video_path, short_data):
    fav_dir = os.path.join("workspace", "favorites")
    fav_vid = os.path.join(fav_dir, f"{fav_id}.mp4")
    fav_json = os.path.join(fav_dir, f"{fav_id}.json")
    try:
        shutil.copy2(video_path, fav_vid)
        with open(fav_json, "w", encoding="utf-8") as f:
            json.dump(short_data, f, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Błąd dodawania do ulubionych: {e}")
        return False

def remove_from_favorites(fav_id):
    fav_dir = os.path.join("workspace", "favorites")
    force_remove_file(os.path.join(fav_dir, f"{fav_id}.mp4"))
    force_remove_file(os.path.join(fav_dir, f"{fav_id}.json"))

def is_favorite(fav_id):
    return os.path.exists(os.path.join("workspace", "favorites", f"{fav_id}.mp4"))

def get_favorites():
    favs = []
    fav_dir = os.path.join("workspace", "favorites")
    if not os.path.exists(fav_dir): return favs
    for f in sorted(os.listdir(fav_dir), reverse=True):
        if f.endswith(".json"):
            fav_id = f.replace(".json", "")
            json_path = os.path.join(fav_dir, f)
            video_path = os.path.join(fav_dir, f"{fav_id}.mp4")
            if os.path.exists(video_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as file:
                        short_data = json.load(file)
                        favs.append({'fav_id': fav_id, 'video_path': video_path, 'short_data': short_data})
                except: pass
    return favs

def get_mac_file_path():
    script = '''try\n set theFile to choose file with prompt "Wybierz plik wideo z dysku:"\n return POSIX path of theFile\n on error\n return ""\n end try'''
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()
