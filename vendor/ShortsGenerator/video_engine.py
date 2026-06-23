import sys
import os
import subprocess
import time
import re
import urllib.request
import urllib.parse
import json
import hashlib
import shutil
import unicodedata
import math
import xml.sax.saxutils as saxutils
from pathlib import Path
from math import floor

# --- WYCISZENIE OSTRZEŻEŃ SYSTEMU macOS (objc) ---
_silenced = False
try:
    _stderr_fd = sys.stderr.fileno()
    _saved_stderr = os.dup(_stderr_fd)
    _devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_devnull, _stderr_fd)
    _silenced = True
except: pass

try:
    import cv2
    from ultralytics import YOLO
except: pass

if _silenced:
    try:
        os.dup2(_saved_stderr, _stderr_fd)
        os.close(_devnull)
        os.close(_saved_stderr)
    except: pass
# --- KONIEC WYCISZENIA ---

from subtitle_engine import generate_viral_ass_subtitles
from logo_handler import build_ffmpeg_filters
from utils import get_ffmpeg_path, get_ffprobe_path

FACE_TRACKING_AVAILABLE = False
FACE_TRACKING_ERROR = None
TRACKER_ENGINE = "None"
yolo_model = None

# Inicjalizacja potężnego silnika YOLOv8 Face
try:
    from ultralytics import YOLO
    os.makedirs("models", exist_ok=True)
    model_path = os.path.join("models", "yolov8n.pt")
    
    if not os.path.exists(model_path):
        print("[INFO] Pobieranie optymalnego modelu YOLOv8 Face (ok. 11MB)... To potrwa tylko chwilę.")
        try:
            url = "https://github.com/akanametov/yolo-face/releases/download/v0.0.0/yolov8n.pt"
            urllib.request.urlretrieve(url, model_path)
            print("[INFO] Model pobrany i zainstalowany pomyślnie!")
        except Exception as dl_e:
            print(f"[ERROR] Błąd automatycznego pobierania modelu: {dl_e}")
            
    if os.path.exists(model_path):
        yolo_model = YOLO(model_path)
        FACE_TRACKING_AVAILABLE = True
        TRACKER_ENGINE = "YOLO"
    else:
        FACE_TRACKING_ERROR = "Brak pliku modelu yolov8n.pt w folderze models."
        
except ImportError:
    FACE_TRACKING_ERROR = "Brak biblioteki. Zainstaluj ją wpisując w terminalu: python3.11 -m pip install ultralytics"
except Exception as e:
    FACE_TRACKING_ERROR = str(e)

# Tryb Awaryjny (OpenCV)
if not FACE_TRACKING_AVAILABLE:
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        if os.path.exists(cascade_path):
            FACE_TRACKING_AVAILABLE = True
            TRACKER_ENGINE = "OpenCV"
            print("[INFO] Aktywowano awaryjny silnik detekcji (OpenCV).")
    except Exception:
        pass

def create_proxy(input_file, proxy_res="1080p", proxy_bitrate=15, progress_bar=None, status_text=None):
    if not input_file or not os.path.exists(input_file):
        raise FileNotFoundError(f"Nie znaleziono pliku wejściowego: {input_file}")
        
    basename = os.path.basename(input_file)
    
    if "_proxy_" in basename:
        if status_text: status_text.success("Wybrano już plik proxy, pomijam konwersję.")
        if progress_bar: progress_bar.progress(1.0)
        return input_file
        
    name_without_ext = os.path.splitext(basename)[0]
    proxy_filename = f"{name_without_ext}_proxy_{proxy_res}.mp4"
    proxy_path = os.path.join("workspace", "downloads", proxy_filename)
    
    if os.path.exists(proxy_path):
        if status_text: status_text.success("Plik proxy już istnieje w folderze downloads, pomijam konwersję.")
        if progress_bar: progress_bar.progress(1.0)
        return proxy_path

    if status_text: status_text.info(f"Tworzenie pliku proxy ({proxy_res}, {proxy_bitrate}Mbps)... To może potrwać kilka minut.")
    if progress_bar: progress_bar.progress(0.0)

    duration_cmd = [get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_file]
    try:
        duration_out = subprocess.run(duration_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        total_duration = float(duration_out.stdout.strip())
    except Exception as e:
        print(f"Nie udało się odczytać czasu trwania wideo: {e}")
        total_duration = 0

    res_map = {
        "1080p": "1920:1080",
        "720p": "1280:720"
    }
    scale_val = res_map.get(proxy_res, "1920:1080")

    if sys.platform == "darwin":
        vcodec = "h264_videotoolbox"
    else:
        vcodec = "libx264"
        
    cmd = [
        get_ffmpeg_path(), "-y", "-i", input_file,
        "-vf", f"scale={scale_val}:force_original_aspect_ratio=decrease,pad={scale_val}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", vcodec, "-b:v", f"{proxy_bitrate}M",
        "-pix_fmt", "yuv420p", 
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        proxy_path
    ]

    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, universal_newlines=True, encoding='utf-8', errors='replace')
    time_pattern = re.compile(r"time=\s*(\d{2}):(\d{2}):(\d+\.\d+)")
    start_time_real = time.time()
    
    for line in process.stderr:
        match = time_pattern.search(line)
        if match and total_duration > 0:
            hours, mins, secs = match.groups()
            current_time = float(hours) * 3600 + float(mins) * 60 + float(secs)
            progress = max(0.0, min(current_time / total_duration, 1.0))
            
            elapsed_real = time.time() - start_time_real
            eta_str = "--:--"
            
            if current_time > 0 and elapsed_real > 0:
                speed = current_time / elapsed_real
                if speed > 0:
                    eta_real = max(0.0, (total_duration - current_time) / speed)
                    eta_str = f"{int(eta_real // 60):02d}:{int(eta_real % 60):02d}"
            
            elapsed_str = f"{int(elapsed_real // 60):02d}:{int(elapsed_real % 60):02d}"
            
            if progress_bar: progress_bar.progress(progress)
            if status_text: status_text.text(f"Konwersja Proxy: {progress*100:.1f}% | Minęło: {elapsed_str} | Pozostało: ~{eta_str}")

    process.wait()
    if process.returncode != 0:
        if os.path.exists(proxy_path):
            os.remove(proxy_path)
        raise Exception(f"Błąd podczas tworzenia pliku proxy FFmpeg (kod błędu: {process.returncode}).")
        
    if progress_bar: progress_bar.progress(1.0)
    if status_text: status_text.success("Zakończono tworzenie pliku proxy.")
    
    return proxy_path

def seconds_to_timecode(seconds, fps=30):
    h = floor(seconds / 3600); m = floor((seconds % 3600) / 60); s = floor(seconds % 60); f = floor((seconds - floor(seconds)) * fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

def generate_xml_content(short_data, video_filename, video_filepath, aspect_ratio, export_res, fps=30):
    """
    Generuje prawidłowy kod XML (Final Cut Pro 7 / DaVinci Resolve)
    aby zaoszczędzić Ci mnóstwo czasu w post-produkcji.
    """
    segments = short_data.get('segments', [])
    res_map = {
        "1080p": (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080),
        "720p": (720, 1280) if aspect_ratio == "9:16" else (1280, 720),
        "480p": (480, 854) if aspect_ratio == "9:16" else (854, 480),
        "2K (1440p)": (1440, 2560) if aspect_ratio == "9:16" else (2560, 1440),
        "4K (2160p)": (2160, 3840) if aspect_ratio == "9:16" else (3840, 2160)
    }
    width, height = res_map.get(export_res, ((1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)))

    seq_name = saxutils.escape(short_data.get('title', 'Short'))
    file_id = "file-1"

    total_duration_sec = sum([seg['end_time'] - seg['start_time'] for seg in segments])
    total_duration_frames = int(total_duration_sec * fps)

    xml = []
    xml.append('<?xml version="1.0" encoding="UTF-8"?>')
    xml.append('<!DOCTYPE xmeml>')
    xml.append('<xmeml version="4">')
    xml.append('  <sequence id="sequence-1">')
    xml.append(f'    <name>{seq_name}</name>')
    xml.append(f'    <duration>{total_duration_frames}</duration>')
    xml.append('    <rate>')
    xml.append('      <timebase>30</timebase>')
    xml.append('      <ntsc>FALSE</ntsc>')
    xml.append('    </rate>')
    xml.append('    <media>')
    xml.append('      <video>')
    xml.append('        <format>')
    xml.append('          <samplecharacteristics>')
    xml.append('            <rate>')
    xml.append('              <timebase>30</timebase>')
    xml.append('              <ntsc>FALSE</ntsc>')
    xml.append('            </rate>')
    xml.append(f'            <width>{width}</width>')
    xml.append(f'            <height>{height}</height>')
    xml.append('            <pixelaspectratio>square</pixelaspectratio>')
    xml.append('          </samplecharacteristics>')
    xml.append('        </format>')
    xml.append('        <track>')

    current_frame = 0

    for i, seg in enumerate(segments):
        start_sec = seg.get('start_time', 0.0)
        end_sec = seg.get('end_time', 0.0)
        
        in_frame = int(start_sec * fps)
        out_frame = int(end_sec * fps)
        duration_frames = out_frame - in_frame
        
        if duration_frames <= 0:
            continue

        xml.append('          <clipitem id="clipitem-{}">'.format(i+1))
        xml.append('            <name>{}</name>'.format(saxutils.escape(video_filename)))
        xml.append('            <duration>{}</duration>'.format(out_frame))
        xml.append('            <rate>')
        xml.append('              <timebase>30</timebase>')
        xml.append('              <ntsc>FALSE</ntsc>')
        xml.append('            </rate>')
        xml.append('            <start>{}</start>'.format(current_frame))
        xml.append('            <end>{}</end>'.format(current_frame + duration_frames))
        xml.append('            <in>{}</in>'.format(in_frame))
        xml.append('            <out>{}</out>'.format(out_frame))
        
        if i == 0:
            xml.append('            <file id="{}">'.format(file_id))
            xml.append('              <name>{}</name>'.format(saxutils.escape(video_filename)))
            xml.append('              <pathurl>file://localhost/{}</pathurl>'.format(saxutils.escape(video_filepath.replace('\\', '/'))))
            xml.append('              <rate>')
            xml.append('                <timebase>30</timebase>')
            xml.append('                <ntsc>FALSE</ntsc>')
            xml.append('              </rate>')
            xml.append('              <media>')
            xml.append('                <video/>')
            xml.append('                <audio/>')
            xml.append('              </media>')
            xml.append('            </file>')
        else:
            xml.append('            <file id="{}"/>'.format(file_id))
            
        xml.append('          </clipitem>')
        current_frame += duration_frames

    xml.append('        </track>')
    xml.append('      </video>')
    
    xml.append('      <audio>')
    xml.append('        <track>')
    current_frame = 0
    for i, seg in enumerate(segments):
        start_sec = seg.get('start_time', 0.0)
        end_sec = seg.get('end_time', 0.0)
        in_frame = int(start_sec * fps)
        out_frame = int(end_sec * fps)
        duration_frames = out_frame - in_frame
        if duration_frames <= 0: continue

        xml.append('          <clipitem id="clipitem-audio-{}">'.format(i+1))
        xml.append('            <name>{}</name>'.format(saxutils.escape(video_filename)))
        xml.append('            <rate>')
        xml.append('              <timebase>30</timebase>')
        xml.append('              <ntsc>FALSE</ntsc>')
        xml.append('            </rate>')
        xml.append('            <start>{}</start>'.format(current_frame))
        xml.append('            <end>{}</end>'.format(current_frame + duration_frames))
        xml.append('            <in>{}</in>'.format(in_frame))
        xml.append('            <out>{}</out>'.format(out_frame))
        xml.append('            <file id="{}"/>'.format(file_id))
        xml.append('          </clipitem>')
        current_frame += duration_frames
        
    xml.append('        </track>')
    xml.append('      </audio>')
    
    xml.append('    </media>')
    xml.append('  </sequence>')
    xml.append('</xmeml>')
    
    return "\n".join(xml)

def _resolve_tracker_cfg(name):
    """Map the UI tracker choice to an ultralytics tracker config. None = Auto
    (decide from a quick person-count pre-scan)."""
    n = str(name or "Auto").strip().lower()
    if "byte" in n:
        return "bytetrack.yaml"
    if "bot" in n or "sort" in n:
        return "botsort.yaml"
    return None  # Auto


def _sample_max_persons(input_video, total_frames, samples=12):
    """Quick pre-scan: detect persons on a handful of evenly spaced frames and return
    the MAX count seen. Used to auto-pick ByteTrack (≤1 person → fastest) vs BoT-SORT
    (≥2 / occlusion → camera-motion + ReID robustness)."""
    global yolo_model
    if not (yolo_model and total_frames and total_frames > 0):
        return 1
    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        return 1
    max_p = 0
    try:
        idxs = [int(total_frames * (i + 0.5) / samples) for i in range(samples)]
        for fi in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok:
                continue
            res = yolo_model.predict(frame, classes=[0], conf=0.35, verbose=False, imgsz=640)
            cnt = 0
            for r in res:
                if r.boxes is not None:
                    cnt += len(r.boxes)
            max_p = max(max_p, cnt)
    except Exception:
        pass
    finally:
        cap.release()
    return max_p


def _blurred_bg(frame, target_w, target_h, np, blur_sigma, blur_zoom, blur_bright):
    """Build a blurred, zoomed, dimmed COVER of the portrait canvas from `frame`, used to
    fill the letterbox area on ZOOM-OUT frames (a'la OpusClip). Blur is done at 1/4 res for
    speed (visually identical). `blur_bright` is a 0..2 multiplier (1.0 = unchanged)."""
    h, w = frame.shape[:2]
    z = max(1.0, float(blur_zoom or 1.0))
    cover = max(target_w / float(w), target_h / float(h)) * z
    cw = max(1, int(round(w * cover))); ch = max(1, int(round(h * cover)))
    bg = cv2.resize(frame, (cw, ch), interpolation=cv2.INTER_LINEAR)
    x0 = max(0, (cw - target_w) // 2); y0 = max(0, (ch - target_h) // 2)
    bg = bg[y0:y0 + target_h, x0:x0 + target_w]
    if bg.shape[0] != target_h or bg.shape[1] != target_w:
        bg = cv2.resize(bg, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    # blur at quarter resolution (sigma/4 → same visual strength, ~16x fewer pixels)
    sw = max(1, target_w // 4); sh = max(1, target_h // 4)
    k = max(1, int(blur_sigma) // 4)
    small = cv2.resize(bg, (sw, sh), interpolation=cv2.INTER_LINEAR)
    small = cv2.blur(small, (k, k))
    bg = cv2.resize(small, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    if abs(float(blur_bright) - 1.0) > 1e-3:
        bg = np.clip(bg.astype(np.float32) * float(blur_bright), 0, 255).astype(frame.dtype)
    return bg


def _zoomout_frame(frame, target_w, target_h, np, blur=False, blur_sigma=35, blur_zoom=1.1, blur_bright=0.6):
    """Smart-Reframe ZOOM-OUT: fit the WHOLE source frame into the target portrait canvas
    by width — everyone stays visible, nothing cropped. The empty top/bottom is either black
    bars or, when `blur` is on, a blurred zoom of the frame itself. Blur runs ONLY here (i.e.
    only on zoom-out frames); zoom-in frames skip it entirely, saving render time."""
    h, w = frame.shape[:2]
    scale = target_w / float(w)
    new_w = target_w
    new_h = max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_w, new_h), interpolation=interp)
    if new_h >= target_h:
        off = (new_h - target_h) // 2
        return resized[off:off + target_h, :].copy()
    if blur:
        canvas = _blurred_bg(frame, target_w, target_h, np, blur_sigma, blur_zoom, blur_bright)
    else:
        canvas = np.zeros((target_h, target_w, 3), dtype=frame.dtype)
    off = (target_h - new_h) // 2
    canvas[off:off + new_h, :] = resized
    return canvas


def _fill_frame(frame, x_int, y_int, crop_w, crop_h, target_w, target_h):
    """FILL mode: crop the tracked window and scale it to fill the portrait canvas."""
    cropped = frame[y_int:y_int + crop_h, x_int:x_int + crop_w]
    if cropped.shape[1] != target_w or cropped.shape[0] != target_h:
        cropped = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return cropped


_FACE_CASCADE = None


_FACE_CASCADES = None


def _get_face_cascade():
    """Single frontal cascade (kept for the OpenCV fallback path / tests)."""
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        try:
            _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        except Exception:
            _FACE_CASCADE = False
    return _FACE_CASCADE or None


def _get_face_cascades():
    """All bundled face cascades we use: two frontal detectors + a profile detector.
    Profiles matter a lot — a real subject who TURNS or MOVES (e.g. someone walking and
    talking) constantly shows a side face that the frontal-only detector misses, which
    used to make the Smart-Reframe wrongly drop them. No model download (offline-safe)."""
    global _FACE_CASCADES
    if _FACE_CASCADES is None:
        base = cv2.data.haarcascades
        _FACE_CASCADES = {}
        for key, fn in (("frontal", "haarcascade_frontalface_alt2.xml"),
                        ("frontal2", "haarcascade_frontalface_default.xml"),
                        ("profile", "haarcascade_profileface.xml")):
            try:
                c = cv2.CascadeClassifier(base + fn)
                if not c.empty():
                    _FACE_CASCADES[key] = c
            except Exception:
                pass
    return _FACE_CASCADES


_YUNET = None
_YUNET_FAILED = False


def _get_yunet():
    """YuNet DNN face detector (cv2.FaceDetectorYN) using the bundled ONNX (data-only,
    no code execution). Detects FRONTAL **and PROFILE** faces robustly and — crucially —
    does NOT false-fire on text/patterns the way Haar does (e.g. printed cushions). This
    is what makes the Smart-Reframe correct: a real (even side-on, moving) person is
    found → zoom-in; a product/hand scene has no face → zoom-out."""
    global _YUNET, _YUNET_FAILED
    if _YUNET is None and not _YUNET_FAILED:
        try:
            mp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "models", "face_detection_yunet_2023mar.onnx")
            if hasattr(cv2, "FaceDetectorYN") and os.path.exists(mp):
                _YUNET = cv2.FaceDetectorYN.create(mp, "", (320, 320), 0.6, 0.3, 5000)
            else:
                _YUNET_FAILED = True
        except Exception:
            _YUNET_FAILED = True
            _YUNET = None
    return _YUNET


_MP_DETECTOR = None
_MP_FAILED = False


def _get_mp_detector():
    """MediaPipe BlazeFace detector (bundled model, no download). Far more robust than
    Haar — handles profiles, movement, lighting — so a real moving/talking person is
    reliably detected (→ correct zoom-in) while a hand/arm/object yields no face."""
    global _MP_DETECTOR, _MP_FAILED
    if _MP_DETECTOR is None and not _MP_FAILED:
        try:
            import mediapipe as mp
            _MP_DETECTOR = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.4)
        except Exception:
            _MP_FAILED = True
            _MP_DETECTOR = None
    return _MP_DETECTOR


def _detect_faces(frame):
    """Robust face detection. Prefers MediaPipe BlazeFace (handles profiles/movement);
    falls back to bundled Haar cascades. Runs on a width-640 copy for speed, returns
    full-res boxes. A real person is reliably found; a bare hand/object yields none."""
    h, w = frame.shape[:2]

    # --- primary: YuNet DNN (frontal + profile, no text false-positives) ---
    yn = _get_yunet()
    if yn is not None:
        try:
            scale = 640.0 / max(1.0, float(w))
            small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
            if scale >= 1.0:
                scale = 1.0
            sh, sw = small.shape[:2]
            yn.setInputSize((sw, sh))
            _ret, dets = yn.detect(small)
            faces = []
            if dets is not None:
                inv = 1.0 / scale
                for d in dets:
                    FX = max(0.0, float(d[0])) * inv
                    FY = max(0.0, float(d[1])) * inv
                    FW = float(d[2]) * inv
                    FH = float(d[3]) * inv
                    if FW <= 1 or FH <= 1:
                        continue
                    faces.append({"x": FX, "y": FY, "w": FW, "h": FH, "cx": FX + FW / 2.0, "cy": FY + FH / 2.0, "area": FW * FH})
            return faces
        except Exception:
            pass

    det = _get_mp_detector()
    if det is not None:
        try:
            scale = 640.0 / max(1.0, float(w))
            small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
            res = det.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            faces = []
            if res.detections:
                for d in res.detections:
                    bb = d.location_data.relative_bounding_box
                    FX = max(0.0, bb.xmin) * w
                    FY = max(0.0, bb.ymin) * h
                    FW = bb.width * w
                    FH = bb.height * h
                    if FW <= 1 or FH <= 1:
                        continue
                    faces.append({"x": FX, "y": FY, "w": FW, "h": FH, "cx": FX + FW / 2.0, "cy": FY + FH / 2.0, "area": FW * FH})
            return faces
        except Exception:
            pass

    cascades = _get_face_cascades()
    if not cascades:
        return []
    h, w = frame.shape[:2]
    scale = 640.0 / max(1.0, float(w))
    if scale < 1.0:
        small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = frame
        scale = 1.0
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    raw = []
    for c in cascades.values():
        try:
            for r in c.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(28, 28)):
                raw.append((int(r[0]), int(r[1]), int(r[2]), int(r[3])))
        except Exception:
            pass
    # The profile cascade only finds ONE side; flip the image to catch the other.
    if "profile" in cascades:
        try:
            gw = gray.shape[1]
            flipped = cv2.flip(gray, 1)
            for r in cascades["profile"].detectMultiScale(flipped, scaleFactor=1.12, minNeighbors=5, minSize=(28, 28)):
                raw.append((int(gw - r[0] - r[2]), int(r[1]), int(r[2]), int(r[3])))
        except Exception:
            pass
    inv = 1.0 / scale
    faces = []
    for (fx, fy, fw, fh) in raw:
        FX, FY, FW, FH = fx * inv, fy * inv, fw * inv, fh * inv
        cx, cy = FX + FW / 2.0, FY + FH / 2.0
        dup = False
        for ff in faces:
            if abs(ff["cx"] - cx) < max(ff["w"], FW) * 0.5 and abs(ff["cy"] - cy) < max(ff["h"], FH) * 0.5:
                dup = True
                break
        if not dup:
            faces.append({"x": FX, "y": FY, "w": FW, "h": FH, "cx": cx, "cy": cy, "area": FW * FH})
    return faces


def _looks_like_face_box(box, frame_h):
    """Accept YOLO boxes only when they look like face boxes, not full-body boxes."""
    w = float(box.get("w", 0) or 0)
    h = float(box.get("h", 0) or 0)
    if w <= 1 or h <= 1:
        return False
    ratio = w / h
    return 0.55 <= ratio <= 1.55 and 0.025 * frame_h <= h <= 0.55 * frame_h


def _merge_face_candidates(primary, secondary):
    """Merge Haar/OpenCV and YOLO face candidates without double-counting the same face."""
    merged = [dict(f) for f in primary]
    for cand in secondary:
        duplicate = False
        for existing in merged:
            dx = abs(float(existing["cx"]) - float(cand["cx"]))
            dy = abs(float(existing["cy"]) - float(cand["cy"]))
            near = dx <= max(existing["w"], cand["w"]) * 0.45 and dy <= max(existing["h"], cand["h"]) * 0.45
            if near:
                duplicate = True
                if existing.get("id") is None and cand.get("id") is not None:
                    existing["id"] = cand["id"]
                if cand.get("area", 0) > existing.get("area", 0) * 1.25:
                    existing.update(cand)
                break
        if not duplicate:
            merged.append(dict(cand))
    return merged


def _prominent_faces(faces, frame_h):
    """Keep visible, meaningful faces; reject tiny false positives/noise."""
    if not faces:
        return []
    max_h = max(float(f.get("h", 0) or 0) for f in faces)
    floor_h = max(0.035 * frame_h, 0.40 * max_h)
    return [f for f in faces if float(f.get("h", 0) or 0) >= floor_h]


def _frames_with_persons(input_video, tracker_cfg, recheck_frames):
    """Yield (frame_bgr, faces, persons) per frame.

    `faces` contains only face-like boxes. `persons` is kept for id tagging and
    diagnostics; it must never become the camera target by itself.
    """
    global yolo_model, TRACKER_ENGINE

    def _tag_faces(faces, persons):
        for f in faces:
            f["id"] = None
            for p in persons:
                if p["x"] <= f["cx"] <= p["x"] + p["w"] and p["y"] <= f["cy"] <= p["y"] + p["h"]:
                    f["id"] = p["id"]
                    break
        return faces

    if TRACKER_ENGINE == "YOLO" and yolo_model:
        stream = yolo_model.track(
            source=input_video, stream=True, persist=True,
            tracker=tracker_cfg or "bytetrack.yaml",
            classes=[0], conf=0.3, iou=0.5, imgsz=512, verbose=False,
        )
        # Haar is the slow part per frame; people/faces move little between adjacent
        # frames, so detect every 3rd frame and reuse the boxes (re-tagged to the current
        # person tracks). The heavy camera smoothing makes the staleness invisible. This
        # roughly halves render time for the AI camera.
        _haar_every = 3
        _haar_idx = 0
        _haar_cache = []
        for res in stream:
            frame = res.orig_img
            frame_h = frame.shape[0]
            persons = []
            yolo_faces = []
            boxes = getattr(res, "boxes", None)
            if boxes is not None:
                for b in boxes:
                    try:
                        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                    except Exception:
                        continue
                    pid = None
                    if getattr(b, "id", None) is not None:
                        try:
                            pid = int(b.id[0])
                        except Exception:
                            pid = None
                    w = x2 - x1
                    h = y2 - y1
                    if w <= 1 or h <= 1:
                        continue
                    box = {"id": pid, "x": x1, "y": y1, "w": w, "h": h,
                           "cx": x1 + w / 2, "cy": y1 + h / 2, "area": w * h}
                    persons.append(box)
                    if _looks_like_face_box(box, frame_h):
                        yolo_faces.append(box)
            if _haar_idx % _haar_every == 0:
                _haar_cache = _detect_faces(frame)
            _haar_idx += 1
            haar = _tag_faces([dict(f) for f in _haar_cache], persons)
            faces = _merge_face_candidates(haar, yolo_faces)
            yield frame, faces, persons
    else:
        cap = cv2.VideoCapture(input_video)
        idx = 0
        cached = []
        step = max(1, int(recheck_frames))
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if idx % step == 0:
                cached = _detect_faces(frame)
                for i, f in enumerate(cached):
                    f["id"] = i
            yield frame, cached, cached
            idx += 1
        cap.release()


def apply_smooth_face_tracking(input_video, output_video, aspect_ratio, smooth_alpha=0.9, recheck_frames=8, zoom_factor=1.0, y_offset_pct=0, strategy="Główny mówca (Skupia na największej twarzy)", status_text=None, progress_bar=None, tracker="Auto", smart_reframe=False, reframe_speed=50, blur_bg=False, blur_sigma=35, blur_zoom=1.1, blur_bright=60):
    """AI virtual camera. Follows FACES (Haar) on top of a real multi-object person
    tracker (ByteTrack / BoT-SORT) for buttery, identity-stable following, and optionally
    Smart-Reframes: exactly ONE visible face → FILL the 9:16 frame and follow it; ZERO or
    2+ faces → ZOOM-OUT (whole 16:9 letterboxed into the portrait, nobody cut off).
    `reframe_speed` (1..100) controls how fast it switches between the two (debounce +
    cross-fade). Switches are debounced + cross-faded so they never snap."""
    global yolo_model, TRACKER_ENGINE
    import numpy as np

    if not FACE_TRACKING_AVAILABLE:
        shutil.copy(input_video, output_video)
        return

    probe = cv2.VideoCapture(input_video)
    if not probe.isOpened():
        shutil.copy(input_video, output_video)
        return
    orig_w = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = probe.get(cv2.CAP_PROP_FPS) or 30.0
    if not fps or fps != fps or fps <= 1:
        fps = 30.0
    total_frames = int(probe.get(cv2.CAP_PROP_FRAME_COUNT))
    probe.release()

    if aspect_ratio == "9:16":
        target_w, target_h = min(orig_w, int(orig_h * 9 / 16)), orig_h
    else:
        target_w, target_h = orig_w, min(orig_h, int(orig_w * 9 / 16))

    zoom_factor = max(1.0, float(zoom_factor or 1.0))
    crop_w = min(orig_w, max(2, int(target_w / zoom_factor)))
    crop_h = min(orig_h, max(2, int(target_h / zoom_factor)))
    y_offset_px = int((y_offset_pct / 100.0) * orig_h)

    # Pick the tracker. Auto → ByteTrack (fast) for ≤2 people; BoT-SORT only for genuinely
    # crowded scenes (3+), where its ReID earns the extra cost. Our count+hysteresis logic
    # doesn't depend on long-term identity, and BoT-SORT's camera-motion compensation was
    # both slow and failing ("not enough matching points") on this handheld footage.
    # Tracker: Auto → ByteTrack (fast) for ≤2 people; BoT-SORT only for crowds (3+).
    tracker_cfg = _resolve_tracker_cfg(tracker)
    if tracker_cfg is None:
        if TRACKER_ENGINE == "YOLO" and yolo_model:
            tracker_cfg = "botsort.yaml" if _sample_max_persons(input_video, total_frames) >= 3 else "bytetrack.yaml"
        else:
            tracker_cfg = "bytetrack.yaml"

    # ============================================================================
    # TWO-PASS AI CAMERA. Pass 1 ANALYSES the whole clip into a stable per-frame
    # plan (how many people + where the subject is), so a single noisy frame can
    # never cause a wrong cut. Pass 2 RENDERS from that plan with smooth motion and
    # cross-faded scene changes. This is why it never "loses" a moving person.
    # ============================================================================

    # Per-frame signals. HOW MANY PEOPLE is counted from BODIES (YOLO) — that count is
    # deterministic and rock-stable frame-to-frame, so the zoom-in/zoom-out decision can
    # never flicker the way a face count does (faces vanish on sunglasses, profiles, motion
    # → a face-based count bounces 1↔2 and the frame jumps). FACE is used only to (a) gate
    # out hand/object scenes (no face → never zoom-in) and (b) centre the crop.
    face_floor_h = 0.02 * orig_h          # ignore micro-detections (noise)
    body_floor_abs = 0.08 * orig_h

    def _frame_signals(persons, faces):
        """Returns (n_bodies, n_faces, face_cx, face_cy, body_cx, body_cy). n_bodies is the
        size-filtered person count (the people counter). face_* is the LARGEST face (NaN if
        none); body_* is the largest body centre (centring fallback when no face)."""
        rfaces = [f for f in (faces or []) if f.get("h", 0) >= face_floor_h]
        if rfaces:
            bf = max(rfaces, key=lambda f: f["area"])
            fcx = bf["cx"]; fcy = bf["cy"] - bf["h"] * 0.05
        else:
            fcx = np.nan; fcy = np.nan
        n_bodies = 0; bcx = np.nan; bcy = np.nan
        if persons:
            maxh = max(p["h"] for p in persons)
            floor = max(body_floor_abs, 0.45 * maxh)
            subs = [p for p in persons if p["h"] >= floor]
            n_bodies = len(subs)
            if subs:
                big = max(subs, key=lambda p: p["area"])
                bcx = big["cx"]; bcy = big["y"] + big["h"] * 0.18
        return n_bodies, len(rfaces), fcx, fcy, bcx, bcy

    bcnt = []; fcnt = []; fxs = []; fys = []; bxs = []; bys = []
    if TRACKER_ENGINE == "YOLO" and yolo_model:
        stream = yolo_model.track(
            source=input_video, stream=True, persist=True,
            tracker=tracker_cfg or "bytetrack.yaml",
            classes=[0], conf=0.3, iou=0.5, imgsz=512, verbose=False,
        )
        haar_every, haar_idx, haar_cache = 3, 0, []
        for res in stream:
            frame = res.orig_img
            persons = []
            boxes = getattr(res, "boxes", None)
            if boxes is not None:
                for b in boxes:
                    try:
                        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                    except Exception:
                        continue
                    w = x2 - x1; h = y2 - y1
                    if w <= 1 or h <= 1:
                        continue
                    persons.append({"x": x1, "y": y1, "w": w, "h": h,
                                    "cx": x1 + w / 2, "cy": y1 + h / 2, "area": w * h})
            if haar_idx % haar_every == 0:
                haar_cache = _detect_faces(frame)
            haar_idx += 1
            nbod, nf, fcx, fcy, bcx, bcy = _frame_signals(persons, haar_cache)
            bcnt.append(nbod); fcnt.append(nf); fxs.append(fcx); fys.append(fcy); bxs.append(bcx); bys.append(bcy)
            if status_text and len(fcnt) % 8 == 0:
                status_text.markdown(f"Wirtualna kamera AI: analiza sceny… klatka {len(fcnt)}/{total_frames or '?'}")
            if progress_bar and total_frames:
                progress_bar.progress(min(0.5 * len(fcnt) / total_frames, 0.5))
    else:
        cap0 = cv2.VideoCapture(input_video)
        while cap0.isOpened():
            ret, frame = cap0.read()
            if not ret:
                break
            hf = _detect_faces(frame)
            persons = [dict(f) for f in hf]
            nbod, nf, fcx, fcy, bcx, bcy = _frame_signals(persons, hf)
            bcnt.append(nbod); fcnt.append(nf); fxs.append(fcx); fys.append(fcy); bxs.append(bcx); bys.append(bcy)
        cap0.release()
    faces_present = [not (isinstance(v, float) and v != v) for v in fxs]  # True where face seen



    n = len(fcnt)
    if n == 0:
        shutil.copy(input_video, output_video)
        return

    # ---- build the stable plan (offline smoothing — no online lag/jitter) ----
    def _interp_nan(a):
        a = np.asarray(a, dtype=float)
        m = np.isnan(a)
        if m.all():
            return None
        idx = np.arange(len(a))
        a[m] = np.interp(idx[m], idx[~m], a[~m])
        return a

    def _hold_nan(a):
        """Forward-then-backward fill of NaN holes (hold last known value). Unlike linear
        interpolation it never ramps toward a far-away value across a long gap, so the crop
        stays put through brief face dropouts instead of sliding."""
        a = np.asarray(a, dtype=float)
        if np.isnan(a).all():
            return None
        out = a.copy(); last = np.nan
        for i in range(len(out)):
            if not np.isnan(out[i]):
                last = out[i]
            elif not np.isnan(last):
                out[i] = last
        last = np.nan
        for i in range(len(out) - 1, -1, -1):
            if not np.isnan(out[i]):
                last = out[i]
            elif not np.isnan(last):
                out[i] = last
        return out

    def _smooth(a, k):
        k = max(1, int(k)) | 1
        if k <= 1:
            return a
        pad = k // 2
        ap = np.pad(a, (pad, pad), mode="edge")
        return np.convolve(ap, np.ones(k) / k, mode="valid")

    if smart_reframe:
        # ZOOM-IN vs ZOOM-OUT via a HYSTERESIS STATE MACHINE counted on BODIES (stable),
        # gated by faces. Two guarantees against the flicker the old face-count had:
        #   • people counted from YOLO bodies → deterministic, doesn't bounce 1↔2;
        #   • a MIN DWELL of ~1.6 s between switches → it is mathematically impossible to
        #     flip zoom-in/out every second; one decision sticks before another can happen.
        # FILL is entered only on sustained "exactly 1 person + a real face" and is left on
        # sustained "2+ people" OR "no face" (hand/product) — so a hand never zooms in and a
        # second person (even if their face drops out) always forces zoom-out.
        nb = np.array([float(c) for c in bcnt])
        nf = np.array([float(c) for c in fcnt])
        fp = np.array([1.0 if x else 0.0 for x in faces_present])
        crowd = ((nb >= 2) | (nf >= 2)).astype(float)   # 2+ people anywhere
        solo = (nb == 1).astype(float)                  # exactly one body
        enter_w = max(3, int(fps * 1.2))
        exit_w = max(3, int(fps * 0.7))
        solo_f = _smooth(solo, enter_w)
        crowd_in = _smooth(crowd, enter_w)
        crowd_out = _smooth(crowd, exit_w)
        faceA = _smooth(fp, enter_w)     # face presence to ENTER fill
        faceB = _smooth(fp, exit_w)      # face presence to keep / LEAVE fill
        min_dwell = max(3, int(fps * 1.6))
        zoom = np.ones(n, dtype=bool)    # start in ZOOM-OUT
        state = True                     # True = zoom-out
        last = -min_dwell
        for i in range(n):
            if i - last >= min_dwell:
                if state:  # zoom-out → consider entering FILL
                    if solo_f[i] >= 0.80 and crowd_in[i] <= 0.12 and faceA[i] >= 0.30:
                        state = False; last = i
                else:      # FILL → consider leaving to zoom-out
                    if crowd_out[i] >= 0.30 or faceB[i] < 0.15 or solo_f[i] < 0.40:
                        state = True; last = i
            zoom[i] = state
        want_zoomout = zoom
    else:
        want_zoomout = np.zeros(n, dtype=bool)  # plain "follow": always fill

    # Camera path = the FACE itself (largest face per frame), holes held (not ramped) and
    # smoothed. Centring on the face — never the largest body — is what stops the camera
    # from drifting onto a hand and what keeps the speaker steady instead of floating.
    px = _hold_nan(fxs)
    py = _hold_nan(fys)
    if px is None:
        # No face anywhere in the clip → fall back to the body centre (then frame centre).
        px = _interp_nan(bxs)
        py = _interp_nan(bys)
        if px is None:
            px = np.full(n, orig_w / 2.0)
            py = np.full(n, orig_h / 2.0)
    else:
        bxf = _hold_nan(bxs); byf = _hold_nan(bys)
        if bxf is not None:
            # fill any still-NaN spots (leading/trailing all-face-less) from the body
            mnan = np.isnan(px)
            px[mnan] = bxf[mnan]; py[np.isnan(py)] = byf[np.isnan(py)]
        px = np.where(np.isnan(px), orig_w / 2.0, px)
        py = np.where(np.isnan(py), orig_h / 2.0, py)
    # "Płynność kamery" → centered moving-average window (offline → smoother = rounder
    # motion, NOT laggier, since we already have the whole plan). Min ~0.6 s kills jitter.
    alpha = max(0.0, min(0.985, float(smooth_alpha)))
    path_win = max(int(fps * 0.6), int(fps * (0.25 + (alpha - 0.8) * 1.8)))
    px = _smooth(np.asarray(px, dtype=float), path_win)
    py = _smooth(np.asarray(py, dtype=float), path_win)

    # Background blur is applied ONLY on zoom-out frames (Smart Reframe), so it costs
    # nothing on zoom-in frames. Normalise brightness (UI 0-100% → 0..2 multiplier).
    do_blur = bool(blur_bg) and smart_reframe
    try:
        _bb = float(blur_bright)
    except (TypeError, ValueError):
        _bb = 60.0
    if _bb > 2.0:
        _bb /= 100.0
    _bb = max(0.0, min(2.0, _bb))
    _bsig = max(1, int(blur_sigma or 35))
    _bzoom = max(1.0, float(blur_zoom or 1.1))

    # ---- PASS 2: render from the plan ----
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video, fourcc, fps, (target_w, target_h))
    rs = max(1.0, min(100.0, float(reframe_speed)))
    blend_step = 1.0 / max(1.0, fps * (0.5 - (rs / 100.0) * 0.38))
    blend = 1.0 if (n and want_zoomout[0]) else 0.0
    cap = cv2.VideoCapture(input_video)
    i = 0
    while cap.isOpened() and i < n:
        ret, frame = cap.read()
        if not ret:
            break
        target_blend = 1.0 if want_zoomout[i] else 0.0
        if blend < target_blend:
            blend = min(target_blend, blend + blend_step)
        elif blend > target_blend:
            blend = max(target_blend, blend - blend_step)

        cx = float(px[i]); cy = float(py[i]) + y_offset_px
        x_int = int(max(0, min(orig_w - crop_w, cx - crop_w / 2.0)))
        y_int = int(max(0, min(orig_h - crop_h, cy - crop_h / 2.0)))

        if blend <= 0.001:
            out_frame = _fill_frame(frame, x_int, y_int, crop_w, crop_h, target_w, target_h)
        elif blend >= 0.999:
            out_frame = _zoomout_frame(frame, target_w, target_h, np, do_blur, _bsig, _bzoom, _bb)
        else:
            fimg = _fill_frame(frame, x_int, y_int, crop_w, crop_h, target_w, target_h)
            zimg = _zoomout_frame(frame, target_w, target_h, np, do_blur, _bsig, _bzoom, _bb)
            out_frame = cv2.addWeighted(fimg, 1.0 - blend, zimg, blend, 0.0)
        out.write(out_frame)

        i += 1
        if i % 8 == 0:
            if status_text:
                lbl = "zoom-out" if blend > 0.5 else "kadr na osobie"
                status_text.markdown(f"Wirtualna kamera AI: renderowanie… klatka {i}/{n} · {lbl}")
            if progress_bar:
                progress_bar.progress(min(0.5 + 0.5 * i / n, 1.0))
    cap.release()
    out.release()


def create_preview_video(preset_name, custom_font, bcolor, hcolor, size, hsize, out_color, out_thick, shad_color, shad_size, is_bold, is_italic, is_upper, mode, words, punct, aspect_ratio, margin_v, auto_scale, anim, output_path, bg_padding=45):
    global_words = [
        {"word": "TUTAJ", "start": 0.2, "end": 0.6},
        {"word": "JEST", "start": 0.6, "end": 1.0},
        {"word": "TWÓJ", "start": 1.0, "end": 1.4},
        {"word": "PODGLĄD", "start": 1.4, "end": 1.8},
        {"word": "DYNAMICZNEGO", "start": 1.8, "end": 2.3},
        {"word": "TEKSTU", "start": 2.3, "end": 2.8}
    ]
    segments = [{'start_time': 0.0, 'end_time': 3.0}]
    ass_path = output_path.replace('.mp4', '.ass')
    
    generate_viral_ass_subtitles(
        segments, global_words, ass_path, preset_name, custom_font, aspect_ratio, 
        override_bcolor=bcolor, override_hcolor=hcolor, override_size=size, override_margin=margin_v, auto_scale=auto_scale,
        override_hsize=hsize, override_out_color=out_color, override_out_thick=out_thick, override_shad_color=shad_color,
        override_shad_size=shad_size, override_bold=is_bold, override_italic=is_italic, override_upper=is_upper,
        override_words=words, override_mode=mode, override_punct=punct, override_animation=anim, override_bg_padding=bg_padding
    )
    
    res = "1080x1920" if aspect_ratio == "9:16" else "1920x1080"
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts").replace('\\', '/').replace(':', '\\:')
    safe_ass = os.path.abspath(ass_path).replace('\\', '/').replace(':', '\\:')
    
    vcodec = "h264_videotoolbox" if sys.platform == "darwin" else "libx264"
    
    cmd = [
        get_ffmpeg_path(), "-y", "-f", "lavfi", "-i", f"color=c=0x333333:s={res}:d=3",
        "-vf", f"ass='{safe_ass}':fontsdir='{font_dir}'",
        "-c:v", vcodec, "-b:v", "5M", "-pix_fmt", "yuv420p",
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0 or not os.path.exists(output_path):
        err = (result.stderr or "").strip()
        raise RuntimeError(f"Nie udało się wygenerować podglądu napisów. {err}".strip())
    return output_path


class _SubProgress:
    """Wraps a progress/status sink and remaps its 0..1 progress into a sub-range [lo, hi]
    of the parent. Used so the AI-camera tracking pass (which itself reports 0..1) occupies
    only its slice of the overall render bar — otherwise tracking would run the bar up to
    ~100% and the following FFmpeg step would make it jump BACKWARDS. Status messages pass
    straight through."""
    def __init__(self, base, lo, hi):
        self._b = base; self._lo = float(lo); self._hi = float(hi)

    def progress(self, value):
        if not self._b:
            return
        try:
            v = max(0.0, min(1.0, float(value)))
            self._b.progress(self._lo + v * (self._hi - self._lo))
        except Exception:
            pass

    def markdown(self, message):
        if self._b:
            try: self._b.markdown(message)
            except Exception: pass

    def warning(self, message):
        self.markdown(message)

    def success(self, message):
        self.markdown(message)


def render_short_ffmpeg(input_video, segments, output_filename, aspect_ratio="9:16", ass_subtitle_file=None, export_res="Zgodna ze źródłem", export_bitrate=15, export_codec="H.264 (Większa kompatybilność)", face_tracking=False, ft_smoothness=10, ft_recheck=8, ft_zoom=1.0, ft_y_offset=0, ft_strategy="Główny mówca (Skupia na największej twarzy)", ft_tracker="Auto", smart_reframe=False, reframe_speed=50, status_text=None, progress_bar=None, logo_settings=None, audio_override_path=None):
    global FACE_TRACKING_AVAILABLE
    
    base_dir = os.path.dirname(output_filename)
    base_name = os.path.basename(output_filename)
    temp_concat = os.path.join(base_dir, "temp_concat_" + base_name)
    temp_tracked = os.path.join(base_dir, "temp_tracked_" + base_name)
    temp_output = os.path.join(base_dir, f".rendering_{int(time.time())}_{base_name}")
    
    if "H.265" in export_codec or "HEVC" in export_codec:
        mac_codec = "hevc_videotoolbox"
        cpu_codec = "libx265"
        is_hevc = True
    else:
        mac_codec = "h264_videotoolbox"
        cpu_codec = "libx264"
        is_hevc = False

    vcodec_final = ["-c:v", mac_codec, "-b:v", f"{export_bitrate}M"]
    if sys.platform == "darwin":
        if is_hevc:
            vcodec_final.extend(["-tag:v", "hvc1"])
        else:
            vcodec_final.extend(["-profile:v", "main"])
    else:
        vcodec_final = ["-c:v", cpu_codec, "-preset", "fast", "-b:v", f"{export_bitrate}M"]

    requires_tracking = (face_tracking or smart_reframe) and aspect_ratio == "9:16" and FACE_TRACKING_AVAILABLE

    if not requires_tracking:
        if status_text: status_text.markdown("**Krok 1/1:** 🎬 Renderowanie wideo (Sklejanie i eksport)...")
        if progress_bar: progress_bar.progress(0.4)

        filter_complex = ""
        concat_inputs = ""

        for i, seg in enumerate(segments):
            start = seg['start_time']; end = seg['end_time']
            filter_complex += f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]; "
            if not audio_override_path:
                filter_complex += f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo[a{i}]; "
                concat_inputs += f"[v{i}][a{i}]"
            else:
                concat_inputs += f"[v{i}]"

        if audio_override_path:
            filter_complex += f"{concat_inputs}concat=n={len(segments)}:v=1:a=0[concat_v]"
        else:
            filter_complex += f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[concat_v][outa]"

        res_map = {
            "1080p": (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080),
            "720p": (720, 1280) if aspect_ratio == "9:16" else (1280, 720),
            "480p": (480, 854) if aspect_ratio == "9:16" else (854, 480),
            "2K (1440p)": (1440, 2560) if aspect_ratio == "9:16" else (2560, 1440),
            "4K (2160p)": (2160, 3840) if aspect_ratio == "9:16" else (3840, 2160)
        }
        if export_res in res_map:
            C_W, C_H = res_map[export_res]
        else:
            C_W, C_H = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)
        actual_res_w = C_W
        
        map_v = "[concat_v]"
        
        if aspect_ratio == "9:16":
            fill_mode = logo_settings.get("fill_mode", 100) if logo_settings else 100
            f_val = fill_mode / 100.0

            # AUTO-FILL: when the SOURCE aspect already matches the OUTPUT aspect (e.g. a
            # 9:16 clip exported to 9:16), fill the whole screen no matter where the
            # fill-mode slider sits — no letterbox/blur. Only matching sources get this;
            # a 16:9 source into 9:16 still respects the slider + blurred background.
            try:
                import cv2 as _cv2
                _cap = _cv2.VideoCapture(input_video)
                _iw = _cap.get(_cv2.CAP_PROP_FRAME_WIDTH)
                _ih = _cap.get(_cv2.CAP_PROP_FRAME_HEIGHT)
                _cap.release()
                if _iw > 0 and _ih > 0 and abs((_iw / _ih) - (C_W / C_H)) / (C_W / C_H) < 0.03:
                    f_val = 1.0
            except Exception:
                pass

            blur_bg = logo_settings.get("blur_bg", True) if logo_settings else True
            blur_sigma = logo_settings.get("blur_sigma", 25) if logo_settings else 25
            blur_zoom = logo_settings.get("blur_zoom", 1.0) if logo_settings else 1.0
            # The UI's "Jasność tła" slider is a 0-100 PERCENTAGE, but
            # colorchannelmixer's rr/gg/bb coefficients only accept [-2, 2]
            # (rr=1.0 == unchanged). Passing the raw 60 made FFmpeg abort with
            # "Value 60 for parameter 'rr' out of range". Treat anything >2 as a
            # percentage → multiplier, then clamp into the filter's valid range.
            try:
                blur_bright = float(logo_settings.get("blur_bright", 60) if logo_settings else 60)
            except (TypeError, ValueError):
                blur_bright = 60.0
            if blur_bright > 2.0:
                blur_bright /= 100.0
            blur_bright = max(0.0, min(2.0, blur_bright))
            
            w_expr = f"min({C_W},{C_H}*iw/ih)+{f_val}*(max({C_W},{C_H}*iw/ih)-min({C_W},{C_H}*iw/ih))"
            h_expr = f"({w_expr})*ih/iw"
            
            if blur_bg and f_val < 1.0:
                # --- ROZMYTE TŁO A'LA OPUSCLIP ---
                # 1. Klonujemy wideo na tło [bg] i pierwszy plan [fg]
                filter_complex += f"; {map_v}split=2[bg][fg]; "
                
                # Używamy blur_zoom do powiększenia tła, blur_sigma do rozmycia, blur_bright do przyciemnienia
                bg_w_expr = f"{C_W}*{blur_zoom}"
                bg_h_expr = f"{C_H}*{blur_zoom}"
                
                # OPTYMALIZACJA: skaluj tło do 1/4 rozdzielczosci przed blur, potem z powrotem.
                # Identyczny wynik wizualnie, ~4x szybciej (16x mniej pikseli do przetworzenia przez CPU).
                # sigma/4 bo obraz jest 4x mniejszy, wiec ten sam efekt wizualny
                small_w = max(1, int(C_W // 4))
                small_h = max(1, int(C_H // 4))
                small_sigma = max(1, blur_sigma // 4)
                
                filter_complex += (
                    f"[bg]scale={bg_w_expr}:{bg_h_expr}:force_original_aspect_ratio=increase,"
                    f"crop={C_W}:{C_H},"
                    f"scale={small_w}:{small_h},"
                    f"boxblur={small_sigma}:{small_sigma},"
                    f"scale={C_W}:{C_H}:flags=bilinear,"
                    f"colorchannelmixer=rr={blur_bright}:gg={blur_bright}:bb={blur_bright}[bg_blurred]; "
                )
                # 3. Pierwszy plan: skalujemy według Twojego suwaka "Wypełnienie" z panelu po lewej
                filter_complex += f"[fg]scale=w='round({w_expr})':h='round({h_expr})',crop=w='min(iw,{C_W})':h='min(ih,{C_H})'[fg_scaled]; "
                # 4. Nakładamy wyostrzony pierwszy plan na środek rozmytego tła
                filter_complex += f"[bg_blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2[format_v]"
            else:
                if f_val >= 1.0:
                    # Pełne wypełnienie kadru (bez czarnych pasów)
                    filter_complex += f"; {map_v}scale={C_W}:{C_H}:force_original_aspect_ratio=increase,crop={C_W}:{C_H}[format_v]"
                else:
                    # Dopasowanie do kadru z czarnymi pasami (oryginalne zachowanie)
                    filter_complex += f"; {map_v}scale={C_W}:{C_H}:force_original_aspect_ratio=decrease,pad={C_W}:{C_H}:(ow-iw)/2:(oh-ih)/2:color=black[format_v]"
            
            map_v = "[format_v]"
        else:
            filter_complex += f"; {map_v}scale={C_W}:{C_H}[format_v]"
            map_v = "[format_v]"

        if ass_subtitle_file:
            safe_ass_path = os.path.abspath(ass_subtitle_file).replace('\\', '/').replace(':', '\\:')
            font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts").replace('\\', '/').replace(':', '\\:')
            filter_complex += f"; {map_v}ass='{safe_ass_path}':fontsdir='{font_dir}'[with_subs]"
            map_v = "[with_subs]"

        cmd_inputs = ["-i", input_video]
        audio_map = "[outa]"
        if audio_override_path:
            cmd_inputs.extend(["-i", audio_override_path])
            audio_map = "1:a"
        logo_idx = -1
        
        if logo_settings and logo_settings.get("enable_logo") and logo_settings.get("logo_path") and os.path.exists(logo_settings["logo_path"]):
            cmd_inputs.extend(["-loop", "1", "-framerate", "30", "-i", logo_settings["logo_path"]])
            logo_idx = 2 if audio_override_path else 1

        if logo_settings:
            extra_filters, map_v = build_ffmpeg_filters(map_v, logo_settings, logo_idx, actual_res_w)
            if extra_filters:
                filter_complex += f"; {extra_filters}"

        cmd = [
            get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner"
        ]

        cmd += cmd_inputs + [
            "-filter_complex", filter_complex,
            "-map", map_v, "-map", audio_map
        ] + vcodec_final + [
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-shortest", "-movflags", "+faststart", temp_output
        ]

        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if progress_bar: progress_bar.progress(1.0)
        if process.returncode != 0:
            if os.path.exists(temp_output):
                try: os.remove(temp_output)
                except: pass
            raise Exception(f"Błąd FFmpeg w finałowym procesie zapisu wideo: {process.stderr}")

        os.replace(temp_output, output_filename)
        return output_filename

    else:
        intermediate_bitrate = min(int(export_bitrate), 15)
        vcodec_intermediate = ["-c:v", mac_codec, "-b:v", f"{intermediate_bitrate}M"] if sys.platform == "darwin" else ["-c:v", cpu_codec, "-preset", "fast", "-crf", "18"]

        if status_text: status_text.markdown("**Krok 1/3:** ✂️ Przycinanie i sklejanie oryginalnych scen...")
        if progress_bar: progress_bar.progress(0.03)

        filter_complex = ""
        concat_inputs = ""
        
        for i, seg in enumerate(segments):
            start = seg['start_time']; end = seg['end_time']
            filter_complex += f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]; "
            filter_complex += f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo[a{i}]; "
            concat_inputs += f"[v{i}][a{i}]"

        filter_complex += f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[concat_v][outa]"

        cmd_concat = [
            get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner",
            "-i", input_video, "-filter_complex", filter_complex,
            "-map", "[concat_v]", "-map", "[outa]"
        ] + vcodec_intermediate + [
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-pix_fmt", "yuv420p",
            temp_concat
        ]
        subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

        if status_text: status_text.markdown("Wirtualna kamera AI: wykrywanie i śledzenie twarzy…")
        if progress_bar: progress_bar.progress(0.08)

        # Higher slider = SMOOTHER (more inertia). Keep the low end stable enough to
        # hide frame-to-frame face detector noise.
        _sm = max(1.0, min(100.0, float(ft_smoothness)))
        smooth_alpha = 0.90 + (_sm / 100.0) * 0.092
        # AI-camera background blur (zoom-out frames only). Reuses the Format-i-kadr blur
        # values; gated by its own toggle `cam_blur_bg` so it's independent of that tab.
        _cam_blur = bool(logo_settings.get("cam_blur_bg")) if logo_settings else False
        _cam_blur_sigma = (logo_settings.get("blur_sigma", 35) if logo_settings else 35)
        _cam_blur_zoom = (logo_settings.get("blur_zoom", 1.1) if logo_settings else 1.1)
        _cam_blur_bright = (logo_settings.get("blur_bright", 60) if logo_settings else 60)
        # Tracking reports its own 0..1 — confine it to [0.08, 0.80] of the render bar so it
        # never overruns and then jumps backwards when the final FFmpeg step starts.
        _track_prog = _SubProgress(progress_bar, 0.08, 0.80)
        apply_smooth_face_tracking(temp_concat, temp_tracked, aspect_ratio, smooth_alpha, ft_recheck, ft_zoom, ft_y_offset, ft_strategy, _track_prog, _track_prog, tracker=ft_tracker, smart_reframe=smart_reframe, reframe_speed=reframe_speed, blur_bg=_cam_blur, blur_sigma=_cam_blur_sigma, blur_zoom=_cam_blur_zoom, blur_bright=_cam_blur_bright)

        if os.path.exists(temp_tracked) and os.path.getsize(temp_tracked) > 1000:
            video_input_for_final = temp_tracked
        else:
            video_input_for_final = temp_concat

        if status_text: status_text.markdown("Renderowanie wideo z napisami i dźwiękiem…")
        if progress_bar: progress_bar.progress(0.82)
            
        res_map = {
            "1080p": (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080),
            "720p": (720, 1280) if aspect_ratio == "9:16" else (1280, 720),
            "480p": (480, 854) if aspect_ratio == "9:16" else (854, 480),
            "2K (1440p)": (1440, 2560) if aspect_ratio == "9:16" else (2560, 1440),
            "4K (2160p)": (2160, 3840) if aspect_ratio == "9:16" else (3840, 2160)
        }
        
        scale_str = f"scale={res_map[export_res][0]}:{res_map[export_res][1]}" if export_res in res_map else ""
        actual_res_w = res_map[export_res][0] if export_res in res_map else (1080 if aspect_ratio == "9:16" else 1920)
        
        filter_final_parts = []
        if scale_str:
            filter_final_parts.append(scale_str)
            
        if ass_subtitle_file:
            safe_ass_path = os.path.abspath(ass_subtitle_file).replace('\\', '/').replace(':', '\\:')
            font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts").replace('\\', '/').replace(':', '\\:')
            filter_final_parts.append(f"ass='{safe_ass_path}':fontsdir='{font_dir}'")

        logo_idx = -1
        cmd_inputs = [
            "-i", video_input_for_final,
            "-i", temp_concat 
        ]
        final_audio_map = "1:a"
        if audio_override_path:
            cmd_inputs.extend(["-i", audio_override_path])
            final_audio_map = "2:a"
        if logo_settings and logo_settings.get("enable_logo") and logo_settings.get("logo_path") and os.path.exists(logo_settings["logo_path"]):
            cmd_inputs.extend(["-loop", "1", "-framerate", "30", "-i", logo_settings["logo_path"]])
            logo_idx = 3 if audio_override_path else 2

        final_filter_str = ""
        current_v = "[0:v]"
        if filter_final_parts:
            chain = ",".join(filter_final_parts)
            final_filter_str = f"{current_v}{chain}[format_v]"
            current_v = "[format_v]"

        if logo_settings:
            extra_filters, current_v = build_ffmpeg_filters(current_v, logo_settings, logo_idx, actual_res_w)
            if extra_filters:
                if final_filter_str:
                    final_filter_str += f"; {extra_filters}"
                else:
                    final_filter_str = extra_filters

        cmd_final = [
            get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner"
        ] + cmd_inputs

        if final_filter_str:
            cmd_final.extend(["-filter_complex", final_filter_str, "-map", current_v])
            cmd_final.extend(vcodec_final)
            cmd_final.extend(["-pix_fmt", "yuv420p"])
        else:
            cmd_final.extend(["-map", "0:v", "-c:v", "copy"])

        if audio_override_path:
            cmd_final.extend(["-map", final_audio_map, "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-shortest", "-movflags", "+faststart", temp_output])
        else:
            cmd_final.extend(["-map", final_audio_map, "-c:a", "copy", "-movflags", "+faststart", temp_output])

        process = subprocess.run(cmd_final, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if os.path.exists(temp_concat): os.remove(temp_concat)
        if os.path.exists(temp_tracked): os.remove(temp_tracked)

        if progress_bar: progress_bar.progress(1.0)

        if process.returncode != 0:
            if os.path.exists(temp_output):
                try: os.remove(temp_output)
                except: pass
            raise Exception(f"Błąd FFmpeg w finałowym procesie zapisu wideo: {process.stderr}")
            
        os.replace(temp_output, output_filename)
        return output_filename
