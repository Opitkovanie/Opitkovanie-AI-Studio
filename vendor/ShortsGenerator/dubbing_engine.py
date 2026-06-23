import json
import hashlib
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from utils import get_ffmpeg_path, get_ffprobe_path
from ai_processor import localize_measurement_units_for_tts


DUB_LANGS = ["Polski", "Angielski", "Niemiecki", "Francuski", "Hiszpański", "Włoski", "Portugalski", "Chiński", "Rosyjski"]
DUB_VOICE_SOURCES = ["Głos z oryginalnego filmu", "Własna próbka głosu", "Głos z bazy Qwen TTS"]
DUB_MIX_MODES = [
    "Oryginalne audio",
    "Czysty dubbing (usuń oryginalny głos)",
    "Dubbing + tło z filmu",
    "Lektor na oryginalnym audio",
]
QWEN_SPEAKERS = ["Ryan", "Aiden", "Dylan", "Jada", "Sunny", "Ethan"]

LANG_SLUGS = {
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

# The 10 languages Qwen3-TTS can voice. NO Polish (no Polish voice in the model).
QWEN_LANGS = {
    "Angielski": "english",
    "Niemiecki": "german",
    "Francuski": "french",
    "Hiszpański": "spanish",
    "Włoski": "italian",
    "Portugalski": "portuguese",
    "Rosyjski": "russian",
    "Chiński": "chinese",
    "Japoński": "japanese",
    "Koreański": "korean",
}

# OmniVoice (k2-fsa/OmniVoice) speaks far more languages than Qwen — including Polish.
# Maps the app's Polish display names to OmniVoice's English language names. Anything
# not listed is sent as language=None (the model auto-detects from the text), so the
# table only needs the languages the app actually offers as dubbing targets.
OMNIVOICE_LANGS = {
    "Angielski": "English", "Niemiecki": "German", "Francuski": "French",
    "Hiszpański": "Spanish", "Włoski": "Italian", "Portugalski": "Portuguese",
    "Holenderski": "Dutch", "Polski": "Polish", "Rosyjski": "Russian",
    "Ukraiński": "Ukrainian", "Czeski": "Czech", "Słowacki": "Slovak",
    "Szwedzki": "Swedish", "Norweski": "Norwegian", "Duński": "Danish",
    "Fiński": "Finnish", "Grecki": "Greek", "Rumuński": "Romanian",
    "Węgierski": "Hungarian", "Bułgarski": "Bulgarian", "Chorwacki": "Croatian",
    "Serbski": "Serbian", "Turecki": "Turkish", "Arabski": "Arabic",
    "Hebrajski": "Hebrew", "Hindi": "Hindi", "Wietnamski": "Vietnamese",
    "Tajski": "Thai", "Indonezyjski": "Indonesian", "Japoński": "Japanese",
    "Koreański": "Korean", "Chiński": "Chinese",
}

# v6: invalidates the partial caches still produced by v5 (the repair pass only
# fell back to the reliable local NLLB engine when EVERY segment echoed; a mixed
# echo left source-language segments). v6 now backstops every leftover with NLLB.
TRANSLATION_RULES_VERSION = 6
ACTIVE_TTS_PID_FILE = Path("workspace") / "runtime" / "active_qwen_tts.pid"


def default_dubbing_settings():
    return {
        "audio_mode": "Czysty dubbing (usuń oryginalny głos)",
        "dub_target_lang": "Angielski",
        "dub_auto_subtitles": True,
        "dub_keep_background": True,
        "dub_voice_source": "Głos z oryginalnego filmu",
        "dub_selected_voice_path": "",
        "dub_qwen_speaker": "Aiden",
        "dub_ref_audio_length": 12,
        "dub_sync_min_tempo": 0.90,
        "dub_sync_max_tempo": 1.00,
        "dub_auto_min_tempo": False,
        "dub_auto_max_tempo": True,
        "dub_original_volume": 0.85,
        "dub_background_volume": 1.40,
        "dub_voice_volume": 1.50,
        "dub_duck_amount": 0.95,
        "dub_pitch_adjust": 0.0,
        "dub_style_prompt": "",
    }


def _pid_is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _read_active_tts_pid():
    try:
        if ACTIVE_TTS_PID_FILE.exists():
            return int(ACTIVE_TTS_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        pass
    return None


def kill_active_dubbing_processes():
    """Stops the current Qwen TTS worker and frees model memory."""
    pid = _read_active_tts_pid()
    killed = False
    if pid and _pid_is_running(pid):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
                killed = True
                time.sleep(0.8 if sig == signal.SIGTERM else 0.2)
                if not _pid_is_running(pid):
                    break
            except ProcessLookupError:
                break
            except Exception:
                try:
                    os.kill(pid, sig)
                    killed = True
                except Exception:
                    pass
    try:
        ACTIVE_TTS_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    return killed


def _register_active_tts_pid(pid):
    ACTIVE_TTS_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    old_pid = _read_active_tts_pid()
    if old_pid and old_pid != pid and _pid_is_running(old_pid):
        raise RuntimeError("Qwen TTS już działa w tle. Kliknij 'Zatrzymaj listę renderowania' i spróbuj ponownie.")
    ACTIVE_TTS_PID_FILE.write_text(str(pid), encoding="utf-8")


def _assert_no_active_tts_pid():
    old_pid = _read_active_tts_pid()
    if old_pid and _pid_is_running(old_pid):
        raise RuntimeError("Qwen TTS już działa w tle. Kliknij 'Zatrzymaj listę renderowania' i spróbuj ponownie.")
    try:
        ACTIVE_TTS_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _clear_active_tts_pid(pid):
    try:
        if _read_active_tts_pid() == pid:
            ACTIVE_TTS_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def language_slug(language):
    return LANG_SLUGS.get(language, re.sub(r"[^a-z0-9]+", "_", str(language).lower()).strip("_") or "lang")


def _audio_mode_suffix(settings):
    """Suffix that makes each *kind* of audio render a distinct version, so a
    lektor and a clean-dubbing of the same short+language don't share one folder
    (which made the second silently overwrite — and "disappear" — the first).
    Clean dubbing keeps the bare slug for backward-compat with existing dirs."""
    mode = str((settings or {}).get("audio_mode", "") or "")
    if "Lektor" in mode:
        return "-lektor"
    if "tło" in mode or "tlo" in mode:
        return "-dub-tlo"
    if "Oryginalne" in mode:
        return "-orig"
    return ""  # "Czysty dubbing (usuń oryginalny głos)" -> bare language slug


def version_slug(language, settings=None):
    """Per-version folder/file slug = language + audio-mode. Distinct audio modes
    of the same target language coexist instead of overwriting each other."""
    return f"{language_slug(language)}{_audio_mode_suffix(settings)}"


def is_dubbing_enabled(settings):
    return settings.get("audio_mode", "Oryginalne audio") != "Oryginalne audio"


def get_short_versions_dir(project_id, short_index):
    path = Path("workspace") / "sessions" / project_id / "short_versions" / f"short_{short_index:02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_translation_source_hash(short):
    source = {
        "title": short.get("original_title", short.get("title", "")),
        "hook_text": short.get("original_hook_text", short.get("hook_text", "")),
        "hashtags": short.get("original_hashtags", short.get("hashtags", "")),
        "yt_tags": short.get("original_yt_tags", short.get("yt_tags", "")),
        "segments": [
            {
                "start_time": s.get("start_time"),
                "end_time": s.get("end_time"),
                "text": s.get("text", ""),
            }
            for s in short.get("original_segments", short.get("segments", []))
        ],
    }
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cached_translation(short, language, version_dir):
    cache_path = Path(version_dir) / f"translation_{language_slug(language)}.json"
    if not cache_path.exists():
        return False
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if cached.get("source_hash") != get_translation_source_hash(short):
        return False
    if cached.get("rules_version") != TRANSLATION_RULES_VERSION:
        return False

    short["title"] = cached.get("title", short.get("title", ""))
    short["hook_text"] = cached.get("hook_text", short.get("hook_text", ""))
    short["hashtags"] = cached.get("hashtags", short.get("hashtags", ""))
    short["yt_tags"] = cached.get("yt_tags", short.get("yt_tags", ""))
    if isinstance(cached.get("segments"), list):
        short["segments"] = cached["segments"]
    if isinstance(cached.get("words"), list):
        short["words"] = cached["words"]
    return True


def save_cached_translation(short, language, version_dir):
    Path(version_dir).mkdir(parents=True, exist_ok=True)
    cache_path = Path(version_dir) / f"translation_{language_slug(language)}.json"
    payload = {
        "language": language,
        "rules_version": TRANSLATION_RULES_VERSION,
        "source_hash": get_translation_source_hash(short),
        "title": short.get("title", ""),
        "hook_text": short.get("hook_text", ""),
        "hashtags": short.get("hashtags", ""),
        "yt_tags": short.get("yt_tags", ""),
        "segments": short.get("segments", []),
        "words": short.get("words", []),
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_voice_samples_dir():
    path = Path("workspace") / "voice_samples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_uploaded_voice_sample(uploaded_file):
    if uploaded_file is None:
        return ""
    samples_dir = get_voice_samples_dir()
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", uploaded_file.name).strip("_") or "voice_sample.wav"
    out_path = samples_dir / f"{int(time.time())}_{clean_name}"
    with open(out_path, "wb") as f:
        f.write(uploaded_file.read())
    return str(out_path)


def list_voice_samples():
    samples_dir = get_voice_samples_dir()
    return sorted(
        [str(p) for p in samples_dir.iterdir() if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".m4a", ".aac", ".flac", ".mp4", ".mov", ".mkv"]],
        reverse=True,
    )


def get_audio_duration(path):
    cmd = [
        get_ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def _run(cmd, label):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label}: {result.stderr.strip()[:1200]}")


def _concat_audio_from_segments(source_audio_or_video, segments, output_wav):
    if not segments:
        raise RuntimeError("Brak segmentów do złożenia audio.")
    filter_parts = []
    concat_inputs = []
    for idx, seg in enumerate(segments):
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start + 0.1))
        filter_parts.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[a{idx}]"
        )
        concat_inputs.append(f"[a{idx}]")
    filter_complex = "; ".join(filter_parts) + f"; {''.join(concat_inputs)}concat=n={len(segments)}:v=0:a=1[outa]"
    cmd = [
        get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner",
        "-i", str(source_audio_or_video),
        "-filter_complex", filter_complex,
        "-map", "[outa]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output_wav)
    ]
    _run(cmd, "Składanie audio shorta")
    return output_wav


def _extract_voice_reference(source_video, segments, output_wav, ref_seconds, clean_voice=None):
    source_short = Path(output_wav).with_name("_short_original_for_ref.wav")
    if clean_voice and Path(clean_voice).exists():
        # Filtered voice: the Demucs vocals stem is already the concatenated short's
        # clean voice — use it directly (no room reverb / ambient bleeding into the clone).
        shutil.copy2(clean_voice, source_short)
    else:
        _concat_audio_from_segments(source_video, segments, source_short)
    cmd = [
        get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner",
        "-i", str(source_short),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
        "-t", str(max(3, int(ref_seconds))), str(output_wav)
    ]
    _run(cmd, "Przygotowanie próbki głosu")
    try:
        source_short.unlink(missing_ok=True)
    except Exception:
        pass
    return output_wav


def _stretch_to_window(input_wav, output_wav, target_duration, min_tempo, max_tempo):
    raw_duration = get_audio_duration(input_wav)
    if raw_duration <= 0 or target_duration <= 0:
        shutil.copy2(input_wav, output_wav)
        return output_wav
    fit = raw_duration / target_duration
    # Normally keep tempo within the [min, max] window. But trimming away speech
    # (atrim below) is worse than slightly-faster speech, so if the line is longer
    # than the window even at max_tempo, push the speed up to a hard ceiling so the
    # whole line fits instead of losing its final words.
    HARD_MAX_TEMPO = 1.85
    FILL_FLOOR = 0.85
    tempo = max(float(min_tempo), min(float(max_tempo), fit))
    if fit > float(max_tempo):
        # Line longer than the window — speed up to a hard ceiling instead of trimming.
        tempo = min(fit, HARD_MAX_TEMPO)
    elif fit < float(min_tempo):
        # Line SHORTER than the window — there is room, so slow down to fill it
        # (less rushed speech) instead of staying fast and leaving trailing silence.
        # Gentle floor so it never drags.
        tempo = max(FILL_FLOOR, fit)
    filters = []
    if abs(tempo - 1.0) > 0.03:
        filters.append(f"atempo={tempo:.5f}")
    filters.append(f"apad=pad_dur={max(0.0, target_duration):.3f}")
    filters.append(f"atrim=0:{target_duration:.3f}")
    cmd = [
        get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner",
        "-i", str(input_wav), "-af", ",".join(filters),
        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output_wav)
    ]
    _run(cmd, "Dopasowanie tempa dubbingu")
    return output_wav


def _resolve_tempo_limits(raw_path, target_duration, settings):
    raw_duration = get_audio_duration(raw_path)
    ratio = raw_duration / target_duration if raw_duration > 0 and target_duration > 0 else 1.0
    auto_min = bool(settings.get("dub_auto_min_tempo", False))
    auto_max = bool(settings.get("dub_auto_max_tempo", True))
    configured_min = float(settings.get("dub_sync_min_tempo", 0.90))
    configured_max = float(settings.get("dub_sync_max_tempo", 1.00))
    if auto_min:
        min_tempo = min(1.0, max(0.85, ratio))
    else:
        min_tempo = configured_min
    if auto_max:
        # Ceiling raised from 1.18 → 1.6: translated speech (esp. PL→EN) is often
        # 1.3–1.5× longer than the source window. With only 1.18 allowed, the
        # overflow was hard-trimmed by _stretch_to_window and the segment lost its
        # final words. 1.6 lets AUTO actually speed up enough to fit the whole line
        # while staying intelligible.
        max_tempo = max(1.0, min(1.6, ratio))
    else:
        max_tempo = configured_max
    if max_tempo < min_tempo:
        max_tempo = min_tempo
    return min_tempo, max_tempo


def _word_timing_weight(word):
    clean = re.sub(r"[^\wÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+", "", str(word or ""), flags=re.UNICODE)
    if not clean:
        return 0.45
    weight = max(0.55, len(clean) ** 0.72)
    if re.search(r"\d", clean):
        weight += 0.35
    return weight


_SENT_SPLIT_DUB = re.compile(r"(?<=[.!?…])\s+")


def _split_sentences_dub(text):
    """Sentence splitter mirrored from backend.local_translate._split_sentences so
    the dub's transient TTS chunking matches what the translator used to persist."""
    text = (text or "").strip()
    if not text:
        return []
    out = []
    for part in _SENT_SPLIT_DUB.split(text):
        part = part.strip()
        if not part:
            continue
        if len(part) > 160 and "," in part:
            buf = ""
            for chunk in part.split(","):
                cand = (buf + "," + chunk) if buf else chunk
                if len(cand) > 160 and buf:
                    out.append(buf.strip())
                    buf = chunk
                else:
                    buf = cand
            if buf.strip():
                out.append(buf.strip())
        else:
            out.append(part)
    return out


def _split_segments_for_tts(segments, max_dur=10.0):
    """Return a NEW, sentence-level split of long segments — used ONLY to build the
    dub audio. A short is a supercut: each segment's time-window plays back to
    back, so a 28s segment whose TTS speech is ~10s would leave dead air after the
    time-stretch. Splitting into sentence-sized sub-windows (proportional to
    sentence length, preserving the segment's total span) spreads the speech.

    Crucially this does NOT mutate the caller's segments: the canonical
    ``short["segments"]`` stay coarse (the scene editor shows those), while the dub
    consumes this finer list. The sub-windows exactly tile each coarse window, so
    the dub audio and the coarse video cut stay perfectly in sync."""
    out = []
    for seg in segments or []:
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start))
        span = end - start
        text = str(seg.get("text", "")).strip()
        sentences = _split_sentences_dub(text)
        if span <= max_dur or len(sentences) <= 1:
            out.append(dict(seg))
            continue
        total = sum(len(s) for s in sentences) or 1
        cursor = start
        for i, sentence in enumerate(sentences):
            if i == len(sentences) - 1:
                sub_end = end
            else:
                sub_end = min(end, cursor + span * (len(sentence) / total))
                sub_end = max(sub_end, cursor + 0.12)
            sub = dict(seg)
            sub["start_time"] = round(cursor, 3)
            sub["end_time"] = round(sub_end, 3)
            sub["text"] = sentence
            out.append(sub)
            cursor = sub_end
    return out


def sentences_from_words(words, max_gap=1.2, max_dur=14.0, max_chars=200):
    """Group real Whisper words into SENTENCE-level segments with their true start/end.
    Breaks at sentence punctuation, at silences > max_gap, or when a unit gets too long
    (max_dur / max_chars). Each segment = {start_time, end_time, text} in SOURCE language.
    Used to dub a custom short the DubMaster way: translate each sentence on its own and
    place it at its real time — so no sentence is ever split across a silence and no
    window is crammed with many sentences spoken at once."""
    wl = sorted(
        [w for w in (words or []) if isinstance(w, dict) and w.get("start") is not None],
        key=lambda w: float(w["start"]),
    )
    out = []
    cur_words = []
    cur_start = None
    prev_end = None

    def _flush():
        nonlocal cur_words, cur_start
        if cur_words:
            text = " ".join(str(w.get("word", "")).strip() for w in cur_words).strip()
            text = re.sub(r"\s+", " ", text)
            if text:
                out.append({
                    "start_time": round(float(cur_start), 3),
                    "end_time": round(float(cur_words[-1].get("end", cur_start)), 3),
                    "text": text,
                })
        cur_words = []
        cur_start = None

    for w in wl:
        ws = float(w["start"])
        we = float(w.get("end", ws))
        word_txt = str(w.get("word", "")).strip()
        if cur_words:
            gap = ws - float(prev_end)
            cur_text = " ".join(str(x.get("word", "")).strip() for x in cur_words)
            too_long = (we - cur_start) > max_dur or len(cur_text) > max_chars
            if gap > max_gap or too_long:
                _flush()
        if not cur_words:
            cur_start = ws
        cur_words.append(w)
        prev_end = we
        # End the sentence on terminal punctuation (.!?…) — natural prosodic unit.
        if re.search(r"[.!?…]['\"\)]?$", word_txt):
            _flush()
    _flush()
    return out


def _distribute_text(text, weights):
    """Split `text` into len(weights) chunks on WORD boundaries, sized proportionally to
    `weights`. Used to spread a translated line across the source speech spans."""
    words = str(text or "").split()
    n = len(weights)
    if n <= 0:
        return []
    if n == 1:
        return [text]
    total = float(sum(weights)) or 1.0
    parts = []
    idx = 0
    acc = 0.0
    for i in range(n):
        if i == n - 1:
            parts.append(" ".join(words[idx:]).strip())
            break
        acc += weights[i]
        target = int(round(len(words) * acc / total))
        # leave at least one word for each of the remaining spans, and take >=0 here
        target = max(idx, min(target, len(words) - (n - 1 - i)))
        parts.append(" ".join(words[idx:target]).strip())
        idx = target
    return parts


def _custom_dub_segments(short, max_gap=2.5, max_dur=16.0):
    """For a CUSTOM short (one long contiguous scene) rebuild the fine dub segments from
    the REAL Whisper word timings instead of distributing the text evenly across the
    whole 0–N s window. Words are grouped into speech spans (split at silences > max_gap);
    each span gets a slice of the translated text proportional to how much was spoken
    there, and the silences between spans are preserved as gaps in the dub timeline — so
    the translated voice lands only where the original actually speaks. Returns None when
    not applicable (no single long segment, or no usable word timings)."""
    segs = short.get("segments") or []
    # Prefer the ORIGINAL source word timings (injected from the project's global_words);
    # `short["words"]` can be rebuilt to synthetic per-segment timings by a prior dub.
    words = short.get("original_words") or short.get("words") or []
    if len(segs) != 1:
        return None
    seg = segs[0]
    start = float(seg.get("start_time", 0.0))
    end = float(seg.get("end_time", start))
    text = str(seg.get("text", "")).strip()
    wl = sorted(
        [w for w in words if isinstance(w, dict) and w.get("start") is not None],
        key=lambda w: float(w["start"]),
    )
    if len(wl) < 2 or not text:
        return None

    spans = []
    cur = None
    for w in wl:
        ws = float(w["start"])
        we = float(w.get("end", ws))
        wt = len(str(w.get("word", "")).strip()) or 1
        if cur is None:
            cur = {"start": ws, "end": we, "weight": wt}
        elif ws - cur["end"] > max_gap or (we - cur["start"]) > max_dur:
            # New unit at a real pause (>max_gap) OR once a run gets too long for stable
            # prosody. Merging the small in-between gaps into one unit (instead of one
            # generation per tiny burst) gives OmniVoice enough context to keep an even
            # tone and avoids the clipped/"dy…boat" one-word glitches.
            spans.append(cur)
            cur = {"start": ws, "end": we, "weight": wt}
        else:
            cur["end"] = we
            cur["weight"] += wt
    if cur:
        spans.append(cur)
    if len(spans) <= 1:
        return None  # continuous speech, no big pauses → nothing to fix

    parts = _distribute_text(text, [s["weight"] for s in spans])
    out = []
    n = len(spans)
    # Preserve a leading silence before the first spoken word.
    if spans[0]["start"] - start > 0.3:
        lead = dict(seg)
        lead["start_time"] = round(start, 3)
        lead["end_time"] = round(spans[0]["start"], 3)
        lead["text"] = ""
        out.append(lead)
    for i, sp in enumerate(spans):
        win_start = sp["start"]
        win_end = spans[i + 1]["start"] if i < n - 1 else end
        win_end = max(win_end, win_start + 0.12)
        sub = dict(seg)
        sub["start_time"] = round(win_start, 3)
        sub["end_time"] = round(win_end, 3)
        sub["text"] = parts[i] if i < len(parts) else ""
        out.append(sub)
    return out


def _rebuild_words_for_dub_segments(short, segments=None):
    rebuilt = []
    for seg in (segments if segments is not None else short.get("segments", [])):
        text = str(seg.get("text", "")).strip()
        words = text.split()
        if not words:
            continue
        seg_start = float(seg.get("start_time", 0.0))
        seg_end = float(seg.get("end_time", seg_start + 0.1))
        seg_duration = max(0.1, seg_end - seg_start)
        speech_start = seg_start + min(0.05, seg_duration * 0.08)
        speech_end = seg_end - min(0.04, seg_duration * 0.06)
        speech_duration = max(0.08, speech_end - speech_start)

        weights = []
        for idx, word in enumerate(words):
            weight = _word_timing_weight(word)
            if re.search(r"[,;:]$", word):
                weight += 0.35
            if re.search(r"[.!?]$", word) and idx < len(words) - 1:
                weight += 0.55
            weights.append(weight)

        total_weight = sum(weights) or 1.0
        current = speech_start
        for word, weight in zip(words, weights):
            dur = speech_duration * (weight / total_weight)
            end = min(speech_end, current + max(0.035, dur))
            rebuilt.append({"word": word, "start": round(current, 3), "end": round(end, 3)})
            current = end
    short["words"] = rebuilt
    return rebuilt


def _language_code(language):
    return {
        "Polski": "pl",
        "Angielski": "en",
        "Niemiecki": "de",
        "Francuski": "fr",
        "Hiszpański": "es",
        "Włoski": "it",
        "Portugalski": "pt",
        "Chiński": "zh",
        "Rosyjski": "ru",
    }.get(language)


def _align_words_to_dub_audio(dub_audio_path, short, language, status_text=None, segments=None, timeline_mode=False):
    """
    Align displayed subtitle words to the actual generated Qwen speech.
    Normal shorts are concatenated, so Whisper timings are mapped back from that
    concatenated timeline. Custom long shorts keep their real timeline (including
    pauses), where Whisper timings must be used directly.

    ``segments`` must be the SAME (possibly finer, TTS-split) list used to build
    the dub track. With ``timeline_mode=True`` their timestamps already match the
    source timeline and are never shifted by removed pauses.
    """
    try:
        if status_text:
            status_text.markdown("**Audio:** synchronizacja napisów z wygenerowanym dubbingiem...")
        from ai_processor import load_whisper, transcribe_video

        _, spoken_words = transcribe_video(str(dub_audio_path), load_whisper(), _language_code(language))
        spoken_words = [
            {
                "word": str(w.get("word", "")).strip(),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
            }
            for w in spoken_words
            if str(w.get("word", "")).strip() and float(w.get("end", 0.0)) > float(w.get("start", 0.0))
        ]
        if not spoken_words:
            return False

        aligned_words = []
        concat_offset = 0.0
        for seg in (segments if segments is not None else short.get("segments", [])):
            seg_start = float(seg.get("start_time", 0.0))
            seg_end = float(seg.get("end_time", seg_start + 0.1))
            seg_duration = max(0.1, seg_end - seg_start)
            concat_start = seg_start if timeline_mode else concat_offset
            concat_end = seg_end if timeline_mode else concat_offset + seg_duration

            desired_words = str(seg.get("text", "")).strip().split()
            if not desired_words:
                concat_offset = concat_end
                continue

            seg_spoken = [
                w for w in spoken_words
                if w["end"] >= concat_start - 0.08 and w["start"] <= concat_end + 0.08
            ]

            if seg_spoken:
                n = len(seg_spoken)
                m = len(desired_words)
                for idx, desired_word in enumerate(desired_words):
                    start_idx = min(n - 1, int(idx * n / m))
                    end_idx = min(n - 1, max(start_idx, int(((idx + 1) * n / m) - 1e-9)))
                    word_start_concat = max(concat_start, seg_spoken[start_idx]["start"])
                    word_end_concat = min(concat_end, seg_spoken[end_idx]["end"])
                    if word_end_concat <= word_start_concat:
                        word_end_concat = min(concat_end, word_start_concat + 0.04)
                    aligned_words.append({
                        "word": desired_word,
                        "start": round(seg_start + (word_start_concat - concat_start), 3),
                        "end": round(seg_start + (word_end_concat - concat_start), 3),
                    })
            else:
                fallback_short = {"segments": [seg]}
                _rebuild_words_for_dub_segments(fallback_short)
                aligned_words.extend(fallback_short.get("words", []))

            if not timeline_mode:
                concat_offset = concat_end

        if aligned_words:
            short["words"] = aligned_words
            return True
    except Exception as e:
        if status_text:
            status_text.warning(f"Nie udało się automatycznie zsynchronizować napisów z dubbingiem: {e}")
    return False


def align_short_words_to_dub_audio(dub_audio_path, short, language, status_text=None):
    return _align_words_to_dub_audio(dub_audio_path, short, language, status_text)


def _concat_ready_wavs(wav_paths, output_wav):
    list_file = Path(output_wav).with_suffix(".txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in wav_paths:
            f.write(f"file '{Path(p).resolve().as_posix()}'\n")
    cmd = [
        get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output_wav)
    ]
    try:
        _run(cmd, "Składanie segmentów dubbingu")
    finally:
        try:
            list_file.unlink(missing_ok=True)
        except Exception:
            pass
    return output_wav


def _place_fit_on_timeline(fit_paths, segments, total_dur, out_wav, sr=44100):
    """Zero-drift assembly (same principle as the old DubMaster `assemble_zero_drift`
    and dub_pipeline `_place_on_timeline`): lay each tempo-fit clip on a silent bed at
    its EXACT segment start, capped at the next segment's start so speech never overlaps.
    Used for CUSTOM shorts, where segment start_times are a real contiguous timeline —
    NOT for normal shorts (a supercut, where clips must be concatenated in cut order)."""
    import numpy as np
    import soundfile as sf
    total = max(1, int((float(total_dur) + 0.5) * sr) + 1)
    buf = np.zeros((total, 2), dtype=np.float32)
    for i, (seg, fit) in enumerate(zip(segments, fit_paths)):
        if not Path(fit).exists():
            continue
        data, fsr = sf.read(str(fit), dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        off = max(0, int(float(seg.get("start_time", 0.0)) * sr))
        end = min(total, off + len(data))
        if i + 1 < len(segments):
            nxt = int(float(segments[i + 1].get("start_time", 0.0)) * sr)
            if nxt > off:
                end = min(end, nxt)
        if end > off:
            buf[off:end] += data[: end - off]
    np.clip(buf, -1.0, 1.0, out=buf)
    sf.write(str(out_wav), buf, sr)
    return out_wav


def _audio_content_sig(path):
    """Cheap content fingerprint so a cached artifact is reused only when the INPUT audio
    is byte-identical (segments unchanged) and recomputed when it actually changed."""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _ensure_demucs_background(short_audio, cache_dir, status_text=None):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    no_vocals = cache_dir / "no_vocals.wav"
    stamp = cache_dir / "no_vocals.src"
    sig = _audio_content_sig(short_audio)
    # Reuse the separated background only when it was made from the SAME audio — Demucs is
    # slow, so this saves time on re-runs, but a content hash avoids serving a stale track
    # after the user edits the scenes.
    if (no_vocals.exists() and no_vocals.stat().st_size > 1024
            and stamp.exists() and stamp.read_text(encoding="utf-8").strip() == sig and sig):
        # Guard against a CORRUPT/TRUNCATED cached background (e.g. an interrupted Demucs
        # run): only reuse it when its duration matches the source within ~1s, otherwise
        # fall through and re-separate. Prevents a short/broken bg from silencing the mix.
        try:
            src_dur = get_audio_duration(short_audio)
            cached_dur = get_audio_duration(no_vocals)
            if src_dur <= 0 or abs(cached_dur - src_dur) <= 1.0:
                return no_vocals
            print(f"[Demucs] Cache tła uszkodzony (dł. {cached_dur:.1f}s vs {src_dur:.1f}s) — separuję ponownie.", flush=True)
        except Exception:
            return no_vocals

    if not shutil.which("demucs"):
        raise RuntimeError("Brak Demucs. Zainstaluj wymagania z DubMastera albo wyłącz pozostawianie tła.")

    if status_text:
        status_text.markdown("**Audio:** separacja tła z krótkiego shorta przez Demucs...")
    out_dir = cache_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["demucs", "--two-stems=vocals", str(short_audio), "-o", str(out_dir)]
    _run(cmd, "Separacja tła Demucs")
    candidates = list(out_dir.glob("**/no_vocals.wav"))
    if not candidates:
        raise RuntimeError("Demucs nie zwrócił pliku tła no_vocals.wav.")
    shutil.copy2(candidates[0], no_vocals)
    # Keep the clean VOCALS stem too — used as the "filtered voice" reference (no room
    # reverb/ambient), the alternative to cloning from the raw original-with-ambient.
    vocals = list(out_dir.glob("**/vocals.wav"))
    if vocals:
        try:
            shutil.copy2(vocals[0], cache_dir / "vocals.wav")
        except Exception:
            pass
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        stamp.write_text(sig, encoding="utf-8")
    except Exception:
        pass
    return no_vocals


def _demucs_vocals_path(cache_dir):
    """Path to the cached clean vocals stem (if Demucs has been run for this audio)."""
    p = Path(cache_dir) / "vocals.wav"
    return p if p.exists() and p.stat().st_size > 1024 else None


def _write_qwen_job_runner(job_file, result_file, runner_file):
    code = r'''
import gc, json, os, random, sys, warnings
warnings.filterwarnings("ignore")
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

JOB_FILE = __JOB_FILE__
RESULT_FILE = __RESULT_FILE__

def main():
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel
    except Exception as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"ok": False, "error": f"Brak biblioteki Qwen TTS: {e}"}, f)
        sys.exit(1)

    with open(JOB_FILE, encoding="utf-8") as f:
        job = json.load(f)

    random.seed(12345)
    np.random.seed(12345)
    torch.manual_seed(12345)
    torch.set_grad_enabled(False)
    device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "mps" else torch.float32
    # qwen_tts gates each method by checkpoint type: voice cloning needs the
    # *-Base model, preset speakers need the *-CustomVoice model. Pick the right
    # one for this run so generate_custom_voice / generate_voice_clone don't hit
    # the "does not support …" guard.
    tts_mode = job.get("tts_mode", "clone")
    if tts_mode == "custom":
        model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    else:
        model_id = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    def _load_model():
        print(f"[TTS] Ładowanie {model_id} ({device}, tryb={tts_mode})...", flush=True)
        return Qwen3TTSModel.from_pretrained(model_id, device_map=device, dtype=dtype)

    try:
        model = _load_model()
    except Exception as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"ok": False, "error": f"Nie można załadować Qwen TTS: {e}"}, f)
        sys.exit(1)

    ref_audio = job.get("ref_audio") or ""
    ref_text = " ".join([t for t in job.get("source_texts", []) if t]).strip()[:800]
    if not ref_text:
        ref_text = " ".join([t for t in job.get("texts", []) if t]).strip()[:800]
    qwen_lang = job.get("qwen_lang", "english")
    speaker = job.get("speaker", "Ryan")
    style = (job.get("style") or "").strip()
    voice_prompt = None
    try:
        if ref_audio and os.path.exists(ref_audio):
            try:
                voice_prompt = model.create_voice_clone_prompt(ref_audio=ref_audio, x_vector_only_mode=True)
            except TypeError:
                voice_prompt = model.create_voice_clone_prompt(ref_audio=ref_audio)
    except Exception as e:
        print(f"[TTS] Strict voice clone nieudany, próbuję X-Vector: {e}", flush=True)
        try:
            if ref_audio and os.path.exists(ref_audio):
                voice_prompt = model.create_voice_clone_prompt(ref_audio=ref_audio, x_vector_only_mode=True)
                print("[TTS] X-Vector voice prompt gotowy.", flush=True)
        except Exception as e2:
            print(f"[TTS] X-Vector też nieudany, spróbuję ref_audio bezpośrednio: {e2}", flush=True)
            voice_prompt = None

    # ---- generation core (ported from the perfect DubMaster pipeline) --------
    # One _gen() dispatches clone vs preset; the per-segment loop adds dynamic
    # token budgeting + short/long retries + split-on-fail + trim/pad/fade, which
    # together kill the dropped-words and "hiccup" (Qwen repetition-loop) problems.
    def _gen(gtxt, max_tok, temperature, top_p):
        kw = {"text": gtxt, "language": qwen_lang, "max_new_tokens": int(max_tok),
              "temperature": float(temperature), "top_p": float(top_p)}
        if style:
            kw["instruct"] = style
        if voice_prompt is not None:
            kw["voice_clone_prompt"] = voice_prompt
            wavs, sr = model.generate_voice_clone(**kw)
        elif ref_audio and os.path.exists(ref_audio):
            kw["ref_audio"] = ref_audio
            try:
                kw["x_vector_only_mode"] = True
                wavs, sr = model.generate_voice_clone(**kw)
            except TypeError:
                kw.pop("x_vector_only_mode", None)
                wavs, sr = model.generate_voice_clone(**kw)
        else:
            kw["speaker"] = speaker
            wavs, sr = model.generate_custom_voice(**kw)
        a = wavs[0]
        if hasattr(a, "detach"):
            a = a.detach().cpu().numpy()
        a = np.asarray(a, dtype=np.float32).copy()
        del wavs
        return a, sr

    def _dur(a, sr):
        return (len(a) / float(sr)) if (a is not None and sr) else 0.0

    def _split_two(s):
        w = s.split()
        if len(w) < 4:
            return None
        mid = len(w) // 2
        return " ".join(w[:mid]).strip(), " ".join(w[mid:]).strip()

    errors = []
    texts = job["texts"]
    outputs = job["outputs"]
    durations = job.get("durations") or [5.0] * len(texts)
    for idx, (text, out_path) in enumerate(zip(texts, outputs)):
        print(f"[PROGRESS] {idx + 1}/{len(texts)}", flush=True)
        txt = " ".join((text or "").split()).strip()
        audio = None
        try:
            if not txt:
                sf.write(out_path, np.zeros(int(0.3 * 24000), dtype=np.float32), 24000)
                continue
            win = float(durations[idx]) if idx < len(durations) else 5.0
            # A silent tensor is an MPS/Qwen failure mode: it produces a formally valid WAV
            # after a few good segments, so an old build exported a movie whose narrator
            # disappeared after ~10 seconds. Verify the signal before accepting it. A retry
            # is cheap; after two silent results reload the model to reset MPS state.
            max_tok = min(1600, max(96, int(len(txt) * 1.4), int(win * 38)))
            last_rms = 0.0
            for attempt in range(3):
                if device == "mps":
                    try:
                        torch.mps.synchronize()
                        torch.mps.empty_cache()
                    except Exception:
                        pass
                if attempt == 2:
                    # Reload only after two invalid attempts. This is deliberately rare,
                    # but restores a poisoned Metal generation state instead of exporting
                    # silence for the remainder of a long dub.
                    try:
                        del model
                        gc.collect()
                        if device == "mps":
                            torch.mps.empty_cache()
                        model = _load_model()
                    except Exception as reload_error:
                        raise RuntimeError(f"Nie można ponownie załadować Qwen TTS: {reload_error}")
                seed = 12345 + idx * 1009 + attempt * 7919
                random.seed(seed)
                np.random.seed(seed)
                torch.manual_seed(seed)
                if device == "mps":
                    try:
                        torch.mps.manual_seed(seed)
                    except Exception:
                        pass
                candidate, sr = _gen(txt, min(1600, max_tok + attempt * 96), 0.58 + attempt * 0.04, 0.82)
                last_rms = float(np.sqrt(np.mean(candidate.astype(np.float64) ** 2))) if candidate.size else 0.0
                peak = float(np.max(np.abs(candidate))) if candidate.size else 0.0
                if candidate.size >= int(sr * 0.08) and last_rms >= 0.003 and peak >= 0.012:
                    audio = candidate
                    break
                print(f"[TTS] Segment {idx + 1} jest cichy (rms={last_rms:.5f}) — ponawiam {attempt + 1}/3.", flush=True)
            if audio is None:
                raise RuntimeError(f"Qwen TTS zwrócił ciszę po 3 próbach (RMS={last_rms:.5f}).")

            # post-processing: trim leading silence, pad 60ms, fade in/out
            thr = 0.025
            win_s = int(sr * 0.025)
            if win_s > 0:
                st_idx = 0
                for w in range(0, max(0, min(int(sr * 0.5), len(audio) - win_s)), win_s):
                    if np.sqrt(np.mean(audio[w:w + win_s] ** 2)) > thr:
                        st_idx = w
                        break
                if st_idx > 0:
                    audio = audio[st_idx:]
            pad = np.zeros(int(sr * 0.06), dtype=np.float32)
            audio = np.concatenate([pad, audio, pad])
            fi = min(int(sr * 0.015), len(audio) // 4)
            fo = min(int(sr * 0.030), len(audio) // 4)
            if fi > 0:
                audio[:fi] *= np.linspace(0.0, 1.0, fi)
            if fo > 0:
                audio[-fo:] *= np.linspace(1.0, 0.0, fo)
            sf.write(out_path, audio, sr)
        except Exception as e:
            errors.append(f"Segment {idx + 1}: {e}")
            print(f"[TTS] Błąd segmentu {idx + 1}: {e}", flush=True)
        finally:
            audio = None
            if device == "mps":
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
            gc.collect()

    with open(RESULT_FILE, "w") as f:
        json.dump({"ok": not errors, "errors": errors}, f, ensure_ascii=False)
    sys.exit(0 if not errors else 1)

if __name__ == "__main__":
    main()
'''
    code = code.replace("__JOB_FILE__", repr(str(job_file))).replace("__RESULT_FILE__", repr(str(result_file)))
    with open(runner_file, "w", encoding="utf-8") as f:
        f.write(code)


def _run_qwen_tts(texts, source_texts, language, reference_audio, output_paths, durations, settings, status_text=None, progress_bar=None):
    run_id = uuid.uuid4().hex[:8]
    work_dir = Path(output_paths[0]).parent
    job_file = work_dir / f"qwen_job_{run_id}.json"
    result_file = work_dir / f"qwen_result_{run_id}.json"
    runner_file = work_dir / f"qwen_runner_{run_id}.py"
    job = {
        "texts": texts,
        "source_texts": source_texts,
        "outputs": [str(p) for p in output_paths],
        "durations": durations,
        "qwen_lang": QWEN_LANGS.get(language, "english"),
        "ref_audio": str(reference_audio or ""),
        "speaker": settings.get("dub_qwen_speaker", "Ryan"),
        "style": settings.get("dub_style_prompt", ""),
        # clone = use a reference voice (Base model); custom = preset Qwen
        # speaker (CustomVoice model). Driven by whether we have a ref clip.
        "tts_mode": "clone" if str(reference_audio or "").strip() else "custom",
    }
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    _write_qwen_job_runner(job_file, result_file, runner_file)

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "TRANSFORMERS_VERBOSITY": "error",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0",
    })
    _assert_no_active_tts_pid()
    process = subprocess.Popen(
        [sys.executable, "-u", str(runner_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    _register_active_tts_pid(process.pid)
    try:
        if process.stdout:
            for line in process.stdout:
                clean = line.strip()
                if clean.startswith("[PROGRESS]"):
                    try:
                        cur, total = clean.split()[-1].split("/")
                        ratio = int(cur) / max(1, int(total))
                        if progress_bar:
                            progress_bar.progress(min(0.75, 0.20 + ratio * 0.45))
                        if status_text:
                            status_text.markdown(f"**Audio:** Qwen TTS segment {cur}/{total}...")
                    except Exception:
                        pass
        rc = process.wait()
        try:
            result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
        except Exception:
            result = {}
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                pass
        _clear_active_tts_pid(process.pid)
        for p in [job_file, result_file, runner_file]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
    if rc != 0 or not result.get("ok", False):
        errors = result.get("errors") or [result.get("error", "Nieznany błąd Qwen TTS.")]
        raise RuntimeError("Qwen TTS nie wygenerował dubbingu: " + "; ".join(errors[:3]))


def _omnivoice_python() -> str:
    """Interpreter of the system-installed OmniVoice engine venv.

    The engine lives in its own uv venv (with torch + the `omnivoice` package),
    installed once into the system like the other DubCut engines. Electron may
    override the path via DUBCUT_OMNIVOICE_PYTHON.
    """
    override = os.environ.get("DUBCUT_OMNIVOICE_PYTHON")
    if override:
        return override
    return str(Path.home() / ".cache" / "omnivoice-tts" / "venv" / "bin" / "python")


def _write_omnivoice_job_runner(job_file, result_file, runner_file):
    code = r'''
import gc, json, os, sys, warnings
warnings.filterwarnings("ignore")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

JOB_FILE = __JOB_FILE__
RESULT_FILE = __RESULT_FILE__

def main():
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from omnivoice import OmniVoice
    except Exception as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"ok": False, "error": f"Brak silnika OmniVoice: {e}"}, f)
        sys.exit(1)

    with open(JOB_FILE, encoding="utf-8") as f:
        job = json.load(f)

    # Deterministic sampling so the voice character / prosody stays CONSISTENT across all
    # segments of one dub (and reproducible run-to-run). Without a fixed seed each segment
    # is a fresh random draw → the tone/tempo wanders between lines.
    import random as _random
    _SEED = 12345
    _random.seed(_SEED)
    np.random.seed(_SEED)
    torch.manual_seed(_SEED)
    try:
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            torch.mps.manual_seed(_SEED)
    except Exception:
        pass

    torch.set_grad_enabled(False)
    device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    dtype = torch.float16 if device == "mps" else torch.float32
    model_id = job.get("model_id", "k2-fsa/OmniVoice")
    ref_audio = job.get("ref_audio") or ""
    do_clone = bool(ref_audio and os.path.exists(ref_audio))
    def _load_model():
        print(f"[TTS] Ladowanie OmniVoice {model_id} ({device}, klon={do_clone})...", flush=True)
        return OmniVoice.from_pretrained(model_id, device_map=device, dtype=dtype, load_asr=do_clone)

    try:
        # load_asr is ON only when cloning: OmniVoice must transcribe the REFERENCE
        # sample itself to build a correct clone prompt. (Passing the target text as
        # ref_text — which we used to do — made the model speak gibberish, because the
        # reference audio and that text didn't match.)
        model = _load_model()
    except Exception as e:
        with open(RESULT_FILE, "w") as f:
            json.dump({"ok": False, "error": f"Nie mozna zaladowac OmniVoice: {e}"}, f)
        sys.exit(1)

    sr = int(model.sampling_rate or 24000)
    lang = job.get("language") or None
    instruct = (job.get("instruct") or "").strip() or None
    num_step = int(job.get("num_step", 32))
    guidance = float(job.get("guidance_scale", 2.0))
    speed = float(job.get("speed", 1.0) or 1.0)
    class_temp = float(job.get("class_temperature", 0.0) or 0.0)

    # ---- number normalization -------------------------------------------------
    # The model otherwise reads bare digits in whatever language it "feels like"
    # (e.g. English dub speaking "1, 2, 3" in Polish) and reads "1/4" as
    # "one over four". Spelling numbers out IN THE TARGET LANGUAGE before synthesis
    # forces the right language and natural fractions.
    import re as _re
    try:
        from num2words import num2words as _n2w
    except Exception:
        _n2w = None
    _N2W = {
        "English": "en", "Polish": "pl", "German": "de", "Spanish": "es",
        "French": "fr", "Italian": "it", "Portuguese": "pt", "Dutch": "nl",
        "Russian": "ru", "Ukrainian": "uk", "Czech": "cz", "Slovak": "sk",
        "Swedish": "sv", "Norwegian": "no", "Danish": "dk", "Finnish": "fi",
        "Hungarian": "hu", "Romanian": "ro", "Turkish": "tr", "Lithuanian": "lt",
        "Latvian": "lv",
    }
    nlang = _N2W.get(lang or "", "")
    _FRAC_EN = {(1, 2): "one half", (1, 4): "one quarter", (3, 4): "three quarters",
                (1, 3): "one third", (2, 3): "two thirds", (1, 8): "one eighth"}
    _FRAC_PL = {(1, 2): "jedna druga", (1, 4): "jedna czwarta", (3, 4): "trzy czwarte",
                (1, 3): "jedna trzecia", (2, 3): "dwie trzecie"}

    def _norm_numbers(s):
        if not _n2w or not nlang or not s:
            return s
        def _frac(m):
            a, b = int(m.group(1)), int(m.group(2))
            if nlang == "en" and (a, b) in _FRAC_EN:
                return _FRAC_EN[(a, b)]
            if nlang == "pl" and (a, b) in _FRAC_PL:
                return _FRAC_PL[(a, b)]
            try:
                return f"{_n2w(a, lang=nlang)} {_n2w(b, to='ordinal', lang=nlang)}"
            except Exception:
                return m.group(0)
        s = _re.sub(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", _frac, s)
        def _num(m):
            tok = m.group(0)
            try:
                return _n2w(float(tok) if "." in tok else int(tok), lang=nlang)
            except Exception:
                return tok
        return _re.sub(r"\d+(?:\.\d+)?", _num, s)

    # Build the voice-clone prompt ONCE and reuse it for every segment. ref_text=None
    # makes OmniVoice transcribe the reference audio with its own ASR, so the prompt
    # always matches the SAMPLE regardless of its language — this is what fixes the
    # "made-up language" output. Settings (num_step=32, guidance_scale=2.0, denoise)
    # match the HF Space; postprocess_output (default on) trims silence + fades.
    def _make_voice_prompt():
        if not do_clone:
            return None
        try:
            return model.create_voice_clone_prompt(ref_audio=ref_audio, ref_text=None)
        except Exception as e:
            print(f"[TTS] Prompt klonowania nieudany ({e}) — uzyje glosu wbudowanego.", flush=True)
            return None

    voice_prompt = _make_voice_prompt()

    def _gen(txt, use_lang, use_instruct, seed=_SEED):
        # Re-seed before every segment so each line starts from a known RNG state —
        # keeps the voice's tone/tempo uniform across the whole dub (retries vary it).
        torch.manual_seed(seed)
        try:
            if device == "mps":
                torch.mps.manual_seed(seed)
        except Exception:
            pass
        kw = dict(text=txt, num_step=num_step, guidance_scale=guidance, denoise=True)
        if use_lang:
            kw["language"] = use_lang
        if class_temp > 0:
            kw["class_temperature"] = class_temp
        if abs(speed - 1.0) > 1e-3:
            kw["speed"] = speed
        if voice_prompt is not None:
            kw["voice_clone_prompt"] = voice_prompt
        if use_instruct:
            kw["instruct"] = use_instruct
        au = model.generate(**kw)
        x = au[0]
        a = (x.float().cpu().numpy() if hasattr(x, "float") else np.asarray(x, dtype=np.float32)).squeeze()
        return np.asarray(a, dtype=np.float32)

    errors = []
    texts = job["texts"]
    outputs = job["outputs"]
    for idx, (text, out_path) in enumerate(zip(texts, outputs)):
        print(f"[PROGRESS] {idx + 1}/{len(texts)}", flush=True)
        txt = " ".join((text or "").split()).strip()
        txt = _norm_numbers(txt)
        try:
            if not txt:
                sf.write(out_path, np.zeros(int(0.3 * sr), dtype=np.float32), sr)
                continue
            # Generate with retries. OmniVoice on MPS sometimes returns a (near-)SILENT
            # clip for a segment even though it doesn't error — that's what made the dub
            # "go quiet after the first few lines". Detect it by RMS and retry with a
            # different seed (and progressively drop instruct/language) until we get real
            # audio. Never accept the last silent fallback: it used to make a render
            # appear successful while its narrator disappeared after the first seconds.
            audio = None
            last_rms = 0.0
            for attempt in range(4):
                if device == "mps":
                    try:
                        torch.mps.synchronize()
                        torch.mps.empty_cache()
                    except Exception:
                        pass
                if attempt == 3:
                    # A fresh model instance is the reliable recovery when Metal keeps
                    # returning silent tensors after otherwise valid inference calls.
                    try:
                        del model
                        gc.collect()
                        if device == "mps":
                            torch.mps.empty_cache()
                        model = _load_model()
                        sr = int(model.sampling_rate or sr)
                        voice_prompt = _make_voice_prompt()
                    except Exception as reload_error:
                        raise RuntimeError(f"Nie można ponownie załadować OmniVoice: {reload_error}")
                sd = _SEED + attempt * 7919
                ul = lang if attempt < 3 else None
                ui = instruct if attempt == 0 else None
                try:
                    cand = _gen(txt, ul, ui, sd)
                except Exception as e1:
                    print(f"[TTS] Segment {idx + 1} proba {attempt + 1} blad: {e1}", flush=True)
                    continue
                last_rms = float(np.sqrt(np.mean(cand.astype(np.float64) ** 2))) if cand.size else 0.0
                peak = float(np.max(np.abs(cand))) if cand.size else 0.0
                if cand.size >= int(sr * 0.08) and last_rms >= 0.004 and peak >= 0.012:
                    audio = cand
                    break
                print(f"[TTS] Segment {idx + 1} prawie cichy (rms={last_rms:.4f}) — ponawiam (proba {attempt + 1}).", flush=True)
            if audio is None or audio.size == 0:
                raise RuntimeError(f"OmniVoice zwrócił ciszę po 4 próbach (RMS={last_rms:.5f}).")
            sf.write(out_path, audio, sr)
        except Exception as e:
            errors.append(f"Segment {idx + 1}: {e}")
            print(f"[TTS] Blad segmentu {idx + 1}: {e}", flush=True)
        finally:
            if device == "mps":
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
            gc.collect()

    with open(RESULT_FILE, "w") as f:
        json.dump({"ok": not errors, "errors": errors}, f, ensure_ascii=False)
    sys.exit(0 if not errors else 1)

if __name__ == "__main__":
    main()
'''
    code = code.replace("__JOB_FILE__", repr(str(job_file))).replace("__RESULT_FILE__", repr(str(result_file)))
    with open(runner_file, "w", encoding="utf-8") as f:
        f.write(code)


def _run_omnivoice_tts(texts, source_texts, language, reference_audio, output_paths, durations, settings, status_text=None, progress_bar=None):
    """OmniVoice (k2-fsa) synthesis — mirrors _run_qwen_tts's detached-worker
    lifecycle (PID file, cancel, progress) but runs in the OmniVoice engine venv."""
    run_id = uuid.uuid4().hex[:8]
    work_dir = Path(output_paths[0]).parent
    job_file = work_dir / f"omni_job_{run_id}.json"
    result_file = work_dir / f"omni_result_{run_id}.json"
    runner_file = work_dir / f"omni_runner_{run_id}.py"
    # OmniVoice `instruct` only accepts a FIXED vocabulary (gender/age/pitch/whisper) —
    # free text errors out. Compose it from the structured settings; anything empty is
    # simply omitted (the model auto-decides). Never pass the free-text style here.
    _OV_GENDER = {"male", "female"}
    _OV_AGE = {"child", "teenager", "young adult", "middle-aged", "elderly"}
    _OV_PITCH = {"very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"}
    instruct_tokens = []
    g = str(settings.get("omnivoice_gender", "")).strip().lower()
    a = str(settings.get("omnivoice_age", "")).strip().lower()
    p = str(settings.get("omnivoice_pitch", "")).strip().lower()
    if g in _OV_GENDER:
        instruct_tokens.append(g)
    if a in _OV_AGE:
        instruct_tokens.append(a)
    if p in _OV_PITCH:
        instruct_tokens.append(p)
    if settings.get("omnivoice_whisper"):
        instruct_tokens.append("whisper")
    job = {
        "texts": list(texts),
        "source_texts": list(source_texts or []),
        "outputs": [str(p) for p in output_paths],
        "durations": list(durations or []),
        "language": OMNIVOICE_LANGS.get(language),  # None → model auto-detects
        "ref_audio": str(reference_audio or ""),
        "instruct": ", ".join(instruct_tokens),  # validated OmniVoice tags only
        "model_id": os.environ.get("DUBCUT_OMNIVOICE_MODEL", "k2-fsa/OmniVoice"),
        "num_step": int(settings.get("omnivoice_num_step", 32)),
        "guidance_scale": float(settings.get("omnivoice_guidance_scale", 2.0)),
        "speed": float(settings.get("omnivoice_speed", 1.0) or 1.0),
        "class_temperature": float(settings.get("omnivoice_class_temperature", 0.0) or 0.0),
    }
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    _write_omnivoice_job_runner(job_file, result_file, runner_file)

    py = _omnivoice_python()
    if not Path(py).exists():
        raise RuntimeError(
            "Silnik OmniVoice nie jest zainstalowany. Zainstaluj go w Ustawieniach "
            "(sekcja modeli — OmniVoice) lub przełącz silnik TTS na Qwen."
        )

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        "TRANSFORMERS_VERBOSITY": "error",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.0",
        "TOKENIZERS_PARALLELISM": "false",
    })
    _assert_no_active_tts_pid()
    process = subprocess.Popen(
        [py, "-u", str(runner_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    _register_active_tts_pid(process.pid)
    try:
        if process.stdout:
            for line in process.stdout:
                clean = line.strip()
                if clean.startswith("[PROGRESS]"):
                    try:
                        cur, total = clean.split()[-1].split("/")
                        ratio = int(cur) / max(1, int(total))
                        if progress_bar:
                            progress_bar.progress(min(0.75, 0.20 + ratio * 0.45))
                        if status_text:
                            status_text.markdown(f"**Audio:** OmniVoice segment {cur}/{total}...")
                    except Exception:
                        pass
        rc = process.wait()
        try:
            result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
        except Exception:
            result = {}
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                pass
        _clear_active_tts_pid(process.pid)
        for p in [job_file, result_file, runner_file]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
    if rc != 0 or not result.get("ok", False):
        errors = result.get("errors") or [result.get("error", "Nieznany błąd OmniVoice.")]
        raise RuntimeError("OmniVoice nie wygenerował dubbingu: " + "; ".join(errors[:3]))


def _run_tts(texts, source_texts, language, reference_audio, output_paths, durations, settings, status_text=None, progress_bar=None):
    """Route synthesis to the engine selected in Settings (app.tts_engine).

    "omnivoice" → k2-fsa OmniVoice (studio quality, voice cloning, Polish + many
    languages). Anything else → the proven Qwen3-TTS path (the default, unchanged)."""
    engine = str((settings or {}).get("tts_engine", "qwen")).strip().lower()
    if engine in ("omnivoice", "omni"):
        return _run_omnivoice_tts(texts, source_texts, language, reference_audio,
                                  output_paths, durations, settings, status_text, progress_bar)
    return _run_qwen_tts(texts, source_texts, language, reference_audio,
                         output_paths, durations, settings, status_text, progress_bar)


def build_dubbed_audio(source_video, short, project_id, short_index, settings, status_text=None, progress_bar=None):
    language = settings.get("dub_target_lang", "Angielski")
    lang_slug = language_slug(language)
    # Folder is mode-aware (e.g. en vs en-lektor) so a lektor and a clean-dubbing
    # of the same language don't overwrite each other; audio filenames stay on the
    # bare language slug since they live inside the per-version folder already.
    version_dir = get_short_versions_dir(project_id, short_index) / version_slug(language, settings)
    audio_dir = version_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    if progress_bar:
        progress_bar.progress(0.12)
    if status_text:
        status_text.markdown(f"**Audio:** przygotowanie dubbingu ({language})...")

    # Coarse "scenes" stay on the short (scene editor shows these); the dub audio
    # is built from a transient, finer sentence-split so long windows don't leave
    # dead air after the time-stretch. The fine list exactly tiles the coarse one.
    coarse_segments = short.get("segments", [])
    # CUSTOM short = one long contiguous scene. Rebuild the dub segments from the real
    # Whisper word timings so the translated speech lands only where the original speaks
    # (with the real pauses), instead of being smeared evenly across the whole window.
    segments = None
    custom_timeline = False
    if short.get("custom"):
        # Preferred: segments were pre-split into real SENTENCE segments (with their own
        # translated text + true timings) by the server before translation → use them
        # as-is and place each at its exact time.
        sent = [dict(s) for s in coarse_segments if str(s.get("text", "")).strip()]
        if len(sent) > 1:
            segments = sent
            custom_timeline = True
        else:
            # Legacy fallback (single-segment custom short): rebuild from word timings.
            segments = _custom_dub_segments(short)
            custom_timeline = bool(segments)
    if not segments:
        segments = _split_segments_for_tts(coarse_segments)
    _rebuild_words_for_dub_segments(short, segments)
    texts = [
        localize_measurement_units_for_tts(str(seg.get("text", "")).strip(), language)
        for seg in segments
    ]
    source_texts = [
        str(seg.get("text", "")).strip()
        for seg in short.get("original_segments", coarse_segments or segments)
    ]
    durations = [max(0.12, float(seg.get("end_time", 0.0)) - float(seg.get("start_time", 0.0))) for seg in segments]

    original_short = audio_dir / "original_short.wav"
    if custom_timeline:
        # Custom short plays on the FULL contiguous timeline, so the background/original
        # must be the whole clip (0→end) — NOT the concatenated speech windows, otherwise
        # the music would be compressed to the speech length and drift out of sync.
        full_end = max((float(s.get("end_time", 0.0)) for s in segments), default=0.0)
        _concat_audio_from_segments(source_video, [{"start_time": 0.0, "end_time": full_end}], original_short)
    else:
        _concat_audio_from_segments(source_video, segments, original_short)

    # Demucs background cache lives PER-SHORT (shared across languages + re-dubs), not
    # per-version — the separated background only depends on the source audio, so this
    # avoids re-running the slow (3+ min) separation on every dub of the same clip.
    bg_cache_dir = get_short_versions_dir(project_id, short_index) / "_bg_cache"
    voice_source = settings.get("dub_voice_source", "Głos z oryginalnego filmu")
    # Reference-voice mode: "filtered" (clean Demucs vocals, no room reverb/ambient) or
    # "ambient" (raw original). Default filtered = cleaner clone.
    use_filtered_voice = str(settings.get("dub_voice_ref", "filtered")).lower() != "ambient"
    keep_bg = settings.get("dub_keep_background", True) and settings.get("audio_mode") in ["Dubbing + tło z filmu", "Czysty dubbing (usuń oryginalny głos)"]
    need_demucs = keep_bg or (voice_source == "Głos z oryginalnego filmu" and use_filtered_voice)

    bg_short = None
    if need_demucs:
        try:
            no_vocals = _ensure_demucs_background(original_short, bg_cache_dir, status_text)
            if keep_bg:
                bg_short = no_vocals
        except Exception as e:
            if settings.get("audio_mode") == "Dubbing + tło z filmu":
                raise
            if status_text:
                status_text.warning(f"Nie udało się przygotować tła z Demucs: {e}")

    ref_audio = ""
    if voice_source == "Własna próbka głosu":
        selected = settings.get("dub_selected_voice_path", "")
        if selected and os.path.exists(selected):
            ref_audio = selected
        else:
            raise RuntimeError("Wybrano własną próbkę głosu, ale plik nie istnieje.")
    elif voice_source == "Głos z oryginalnego filmu":
        ref_audio = str(audio_dir / "voice_reference.wav")
        clean_voice = _demucs_vocals_path(bg_cache_dir) if use_filtered_voice else None
        _extract_voice_reference(source_video, segments, ref_audio, settings.get("dub_ref_audio_length", 12), clean_voice=clean_voice)

    raw_paths = [audio_dir / f"tts_{idx:03d}_raw.wav" for idx in range(len(texts))]
    _run_tts(texts, source_texts, language, ref_audio, raw_paths, durations, settings, status_text, progress_bar)

    fit_paths = []
    for idx, raw in enumerate(raw_paths):
        fit = audio_dir / f"tts_{idx:03d}_fit.wav"
        min_tempo, max_tempo = _resolve_tempo_limits(raw, durations[idx], settings)
        _stretch_to_window(raw, fit, durations[idx], min_tempo, max_tempo)
        fit_paths.append(fit)

    dub_track = audio_dir / f"dub_{lang_slug}.wav"
    if custom_timeline:
        # Custom short → real contiguous timeline: place each clip at its exact start
        # (zero-drift, preserves the original pauses) instead of concatenating.
        total_dur = max((float(s.get("end_time", 0.0)) for s in segments), default=0.0)
        _place_fit_on_timeline(fit_paths, segments, total_dur, dub_track)
    else:
        _concat_ready_wavs(fit_paths, dub_track)
    _align_words_to_dub_audio(
        dub_track, short, language, status_text,
        segments=segments, timeline_mode=custom_timeline,
    )

    final_audio = audio_dir / f"final_mix_{lang_slug}.m4a"
    mode = settings.get("audio_mode", "Oryginalne audio")
    dub_vol = float(settings.get("dub_voice_volume", 1.35))
    orig_vol = float(settings.get("dub_original_volume", 0.25))
    bg_vol = float(settings.get("dub_background_volume", 1.0))
    duck_amount = float(settings.get("dub_duck_amount", 0.75))

    if mode == "Lektor na oryginalnym audio":
        duck_ratio = 1.0 + max(0.0, min(1.0, duck_amount)) * 14.0
        filter_complex = (
            f"[0:a]volume={orig_vol}[orig];"
            f"[1:a]volume={dub_vol}[dub];"
            "[dub]asplit=2[dubmix][dubduck];"
            f"[orig][dubduck]sidechaincompress=threshold=0.025:ratio={duck_ratio:.2f}:attack=20:release=250[ducked];"
            "[ducked][dubmix]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[outa]"
        )
        cmd = [get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner", "-i", str(original_short), "-i", str(dub_track),
               "-filter_complex", filter_complex, "-map", "[outa]", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    elif bg_short and os.path.exists(bg_short):
        filter_complex = (
            f"[0:a]volume={bg_vol}[bg];"
            f"[1:a]volume={dub_vol}[dub];"
            "[bg][dub]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[outa]"
        )
        cmd = [get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner", "-i", str(bg_short), "-i", str(dub_track),
               "-filter_complex", filter_complex, "-map", "[outa]", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    else:
        cmd = [get_ffmpeg_path(), "-y", "-loglevel", "error", "-hide_banner", "-i", str(dub_track),
               "-af", f"volume={dub_vol}", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    _run(cmd, "Miks dubbingu")

    manifest = {
        "language": language,
        "language_slug": version_slug(language, settings),
        "created_at": int(time.time()),
        "settings": settings,
        "audio_path": str(final_audio),
    }
    with open(version_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if progress_bar:
        progress_bar.progress(0.78)
    save_cached_translation(short, language, version_dir)
    return str(final_audio), str(version_dir)


def list_rendered_versions(project_id, short_index):
    root = get_short_versions_dir(project_id, short_index)
    versions = []
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            video_path = manifest.get("video_path", "")
            short_data = manifest.get("short_data", {})
            if not short_data:
                lang_slug = manifest.get("language_slug", manifest_path.parent.name)
                translation_path = manifest_path.parent / f"translation_{lang_slug}.json"
                if translation_path.exists():
                    try:
                        cached = json.loads(translation_path.read_text(encoding="utf-8"))
                        short_data = {
                            "title": cached.get("title", ""),
                            "hook_text": cached.get("hook_text", ""),
                            "hashtags": cached.get("hashtags", ""),
                            "yt_tags": cached.get("yt_tags", ""),
                            "segments": cached.get("segments", []),
                            "words": cached.get("words", []),
                        }
                    except Exception:
                        short_data = {}
            versions.append({
                "language": manifest.get("language", manifest_path.parent.name),
                "language_slug": manifest.get("language_slug", manifest_path.parent.name),
                "subtitle_language": manifest.get("subtitle_language", manifest.get("language", "")),
                "created_at": manifest.get("created_at", 0),
                "updated_at": manifest.get("updated_at", manifest.get("created_at", 0)),
                "video_path": video_path,
                "audio_path": manifest.get("audio_path", ""),
                "settings": manifest.get("settings", {}),
                "short_data": short_data,
                # Absolute + symlink-resolved so the de-dupe in
                # _merge_detected_short_versions matches regardless of the caller's
                # current working directory (the endpoint chdir's back to its original
                # cwd between this call and the merge, which used to make the same
                # version folder resolve to two different paths → listed twice).
                "dir": str(manifest_path.parent.resolve()),
            })
        except Exception:
            pass
    return sorted(versions, key=lambda x: x.get("created_at", 0), reverse=True)


def update_version_manifest(version_dir, video_path, short_data=None):
    manifest_path = Path(version_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except Exception:
        manifest = {}
    manifest["video_path"] = str(video_path)
    manifest["updated_at"] = int(time.time())
    if short_data is not None:
        manifest["short_data"] = {
            "title": short_data.get("title", ""),
            "hook_text": short_data.get("hook_text", ""),
            "hashtags": short_data.get("hashtags", ""),
            "yt_tags": short_data.get("yt_tags", ""),
            "segments": short_data.get("segments", []),
            "words": short_data.get("words", []),
            "score": short_data.get("score", 90),
        }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
