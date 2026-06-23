import os
import re
import glob
import json
import subprocess
import yt_dlp
from utils import get_ffprobe_path

COOKIES_FILE = "cookies.txt"
MIN_GOOD_AUDIO_ABR = 128

def _base_opts():
    """Zwraca wspólne opcje yt-dlp, w tym ścieżkę do pliku cookies."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:npm'],
    }
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts

def get_yt_id(url):
    match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
    return match.group(1) if match else "local_video"

def get_video_title(url):
    try:
        opts = {**_base_opts(), 'noplaylist': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False).get('title', get_yt_id(url))
    except:
        return get_yt_id(url)

def _audio_bitrate_kbps(filepath):
    try:
        cmd = [
            get_ffprobe_path(), "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate", "-of", "json", filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(result.stdout or "{}")
        streams = info.get("streams") or []
        if not streams:
            return None
        bit_rate = streams[0].get("bit_rate")
        return int(bit_rate) / 1000 if bit_rate else None
    except Exception:
        return None

def _best_available_audio_abr(url):
    try:
        opts = {**_base_opts(), 'noplaylist': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        abrs = []
        for fmt in info.get("formats", []):
            if fmt.get("acodec") == "none":
                continue
            abr = fmt.get("abr")
            if abr:
                abrs.append(float(abr))
        return max(abrs) if abrs else None
    except Exception:
        return None

def _cached_download_path(base_filename, url):
    for ext in ['mp4', 'mkv', 'webm']:
        path = f"{base_filename}.{ext}"
        if not os.path.exists(path):
            continue
        cached_abr = _audio_bitrate_kbps(path)
        best_abr = _best_available_audio_abr(url)
        if (
            cached_abr is not None
            and cached_abr < MIN_GOOD_AUDIO_ABR
            and best_abr is not None
            and best_abr >= MIN_GOOD_AUDIO_ABR
        ):
            try:
                os.remove(path)
            except Exception:
                return path
            continue
        return path
    return None

def _format_cascade(*video_selectors):
    audio_selectors = [
        "bestaudio[abr>=160]",
        "bestaudio[abr>=128]",
        "bestaudio[asr>=48000]",
        "bestaudio",
    ]
    choices = [
        f"{video}+{audio}"
        for video in video_selectors
        for audio in audio_selectors
    ]
    choices.extend(["bestvideo+bestaudio", "best"])
    return "/".join(choices)

def download_video(url, quality_str="1080p"):
    video_id = get_yt_id(url)
    clean_quality_name = quality_str.split(' ')[0]
    base_filename = f"workspace/downloads/{video_id}_{clean_quality_name}"

    # Stary cache potrafil zawierac audio 48 kb/s. Jesli YouTube ma lepszy
    # strumien, pobieramy od nowa zamiast zwracac slaby plik.
    cached_path = _cached_download_path(base_filename, url)
    if cached_path:
        return cached_path

    base = _base_opts()

    if quality_str == "Najlepsza dostępna" or quality_str == "best":
        ydl_opts = {
            **base,
            'format': _format_cascade("bestvideo"),
            'outtmpl': f'{base_filename}.%(ext)s',
            'merge_output_format': 'mp4',
            'format_sort': ['abr', 'asr', 'res']
        }
    else:
        quality_match = re.search(r'(\d+)p', str(quality_str))
        if quality_match:
            target_height = int(quality_match.group(1))
            format_cascade = _format_cascade(
                f"bestvideo[height={target_height}]",
                f"bestvideo[height<={target_height}]",
                "bestvideo"
            )
        else:
            format_cascade = _format_cascade("bestvideo")
        ydl_opts = {
            **base,
            'format': format_cascade,
            'outtmpl': f'{base_filename}.%(ext)s',
            'merge_output_format': 'mp4',
            'format_sort': ['abr', 'asr', 'res']
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    for ext in ['mp4', 'mkv', 'webm']:
        if os.path.exists(f"{base_filename}.{ext}"):
            return f"{base_filename}.{ext}"

    return f"{base_filename}.mp4"

def download_yt_subtitles(url, video_id, target_lang_code=None):
    base_filename = f"workspace/downloads/{video_id}_subs"
    langs = [target_lang_code] if target_lang_code else ['pl', 'en']
    ydl_opts = {
        **_base_opts(),
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': langs,
        'subtitlesformat': 'vtt',
        'outtmpl': f'{base_filename}.%(ext)s',
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        pass
    vtt_files = glob.glob(f"{base_filename}*.vtt")
    return vtt_files[0] if vtt_files else None
