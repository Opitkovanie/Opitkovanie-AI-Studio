"""Full-length video dubbing pipeline for the Dubbing Studio section.

Unlike the Shorts path (which supercuts selected segments), this dubs a whole
video on its original timeline: transcribe → translate → TTS per segment →
place each clip at its real timestamp → mix (clean / voiceover / ducking) → mux
back onto the untouched video.

Reuses the proven ShortsGenerator engine helpers (Whisper, Qwen TTS, Demucs,
tempo-fit) so behaviour matches the working Shorts dubbing. Must run with cwd =
ShortsGenerator dir and vendor on sys.path (the server sets both before calling).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

SR = 44100


# ── ctx adapters so we can drive the existing engine helpers ──────────────────
class _StatusAdapter:
    def __init__(self, ctx):
        self._ctx = ctx
    def markdown(self, msg):
        self._ctx.log(re.sub(r"\*\*", "", str(msg)), "info")
    def warning(self, msg):
        self._ctx.log(str(msg), "warning")


class _ProgressAdapter:
    """Maps a 0..1 sub-progress into the [lo, hi] slice of the whole job."""
    def __init__(self, ctx, lo, hi):
        self._ctx, self._lo, self._hi = ctx, lo, hi
    def progress(self, ratio, text=None):
        frac = self._lo + max(0.0, min(1.0, float(ratio))) * (self._hi - self._lo)
        self._ctx.progress(frac, text)


def _ffmpeg() -> str:
    try:
        from utils import get_ffmpeg_path  # type: ignore
        return get_ffmpeg_path()
    except Exception:
        return "ffmpeg"


def _run(cmd: List[str], label: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} nie powiodło się: {proc.stderr[-800:]}")


def _media_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _parse_segments(transcript_text: str) -> List[Dict[str, Any]]:
    """Parse '[start - end] text' lines from transcribe_video into segments."""
    segs: List[Dict[str, Any]] = []
    for line in (transcript_text or "").splitlines():
        m = re.match(r"\s*\[\s*([\d.]+)\s*-\s*([\d.]+)\s*\]\s*(.*)", line)
        if not m:
            continue
        start, end, text = float(m.group(1)), float(m.group(2)), m.group(3).strip()
        if text and end > start:
            segs.append({"start_time": start, "end_time": end, "text": text})
    return segs


def _extract_full_audio(source_video: str, out_wav: Path) -> None:
    _run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(source_video),
          "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out_wav)], "Ekstrakcja audio")


def _place_on_timeline(fit_paths: List[Path], segments: List[Dict[str, Any]], total_dur: float, out_wav: Path) -> None:
    """Place each (already tempo-fit) clip at its segment start on a silent bed."""
    import numpy as np
    import soundfile as sf
    total = max(1, int(math.ceil((total_dur + 0.5) * SR)))
    buf = np.zeros((total, 2), dtype=np.float32)
    for i, (seg, fit) in enumerate(zip(segments, fit_paths)):
        if not Path(fit).exists():
            continue
        data, sr = sf.read(str(fit), dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        off = max(0, int(float(seg.get("start_time", 0.0)) * SR))
        end = min(total, off + len(data))
        # Never let a clip bleed into the next segment's start (overlapping
        # speech = the "hiccup"). Hard-cap at the next segment boundary.
        if i + 1 < len(segments):
            nxt = int(float(segments[i + 1].get("start_time", 0.0)) * SR)
            if nxt > off:
                end = min(end, nxt)
        if end > off:
            buf[off:end] += data[: end - off]
    np.clip(buf, -1.0, 1.0, out=buf)
    sf.write(str(out_wav), buf, SR)


# ── segment (de)serialisation for the UI editor ───────────────────────────────
def _seg_out(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"id": i, "start": float(s.get("start_time", 0.0)), "end": float(s.get("end_time", 0.0)),
         "text": str(s.get("text", ""))}
        for i, s in enumerate(segments)
    ]


def _words_for_segments(segments: List[Dict[str, Any]], words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep Whisper's exact word timings for the DubMaster editor.

    Older cached sessions do not contain them; in that case create a conservative
    evenly-spaced fallback so editing remains available without pretending it is
    a fresh Whisper alignment.
    """
    clean = []
    for word in words or []:
        try:
            start, end = float(word.get("start")), float(word.get("end"))
            text = str(word.get("word", "")).strip()
            if text and end > start:
                clean.append({"word": text, "start": start, "end": end})
        except (TypeError, ValueError):
            pass
    if clean:
        return sorted(clean, key=lambda w: w["start"])
    estimated: List[Dict[str, Any]] = []
    for seg in segments:
        text = str(seg.get("text", "")).split()
        start, end = float(seg.get("start_time", 0)), float(seg.get("end_time", 0))
        if not text or end <= start:
            continue
        span = (end - start) / len(text)
        for i, token in enumerate(text):
            estimated.append({"word": token, "start": round(start + i * span, 3), "end": round(start + (i + 1) * span, 3)})
    return estimated


def _seg_in(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in segments:
        out.append({
            "start_time": float(s.get("start", s.get("start_time", 0.0))),
            "end_time": float(s.get("end", s.get("end_time", 0.0))),
            "text": str(s.get("text", "")).strip(),
        })
    return out


def _resolve_video(source: str, settings: Dict[str, Any]) -> str:
    is_yt = str(settings.get("input_method", "")).lower().startswith("pobierz") or source.startswith("http")
    if is_yt:
        from downloader import download_video  # type: ignore
        return download_video(source, settings.get("yt_quality", "1080p"))
    return source


_GEMINI_DUB_SYSTEM = """You are an expert audiovisual translator and dubbing adapter working into {target_lang}.
You are translating a real spoken video transcript for dubbing with AI Text-to-Speech.

FULL CONTEXT (whole transcript, for understanding slang, idioms and references):
{full_context}

{glossary_block}

RULES:
1. Return EXACTLY {segment_count} lines in this format: [N] text
2. Keep the same segment order and count. Do NOT merge or split segments.
3. Each line must sound natural when SPOKEN aloud, not written.
4. Translate meaning, tone and intent faithfully — including casual/childish/onomatopoeic
   speech. Example: Polish "mieszu mieszu" (childish for mixing in a bowl) → "mix mix".
5. Use the FULL CONTEXT to disambiguate colloquialisms and references.
6. Do NOT invent details, props, foods, people or actions not present in the transcript.
7. Expand units of measurement into spoken words (e.g. "250A" → "250 amps", "3.4kW" → "3.4 kilowatts").

Return ONLY the numbered translations. No headers, no commentary."""


def _translate_gemini_dub(texts: List[str], target_lang: str, api_key: str, glossary: str, ctx) -> List[str]:
    """Context-aware Gemini translation (mirrors the DubMaster pipeline): the whole
    transcript is given as context + a dubbing-adapter prompt, in numbered batches —
    so slang/idioms ('mieszu mieszu' → 'mix mix') translate correctly."""
    import requests  # type: ignore
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    full_context = " ".join(t.strip() for t in texts if t.strip())[:1500]
    gloss = ("Preserve these company/app/model/product names exactly, do not translate them:\n" + glossary.strip()) if (glossary or "").strip() else ""
    out: List[str] = []
    CH = 35
    for off in range(0, len(texts), CH):
        chunk = texts[off:off + CH]
        numbered = "\n".join(f"[{i + 1}] {(t or '').strip()}" for i, t in enumerate(chunk))
        sysp = _GEMINI_DUB_SYSTEM.format(target_lang=target_lang, full_context=full_context, glossary_block=gloss, segment_count=len(chunk))
        payload = {
            "contents": [{"parts": [{"text": f"Translate these segments for dubbing:\n{numbered}"}]}],
            "systemInstruction": {"parts": [{"text": sysp}]},
        }
        res = requests.post(url, json=payload, timeout=120)
        res.raise_for_status()
        raw = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        parsed: Dict[int, str] = {}
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("[") and "]" in line:
                try:
                    end = line.index("]")
                    parsed[int(line[1:end])] = line[end + 1:].strip()
                except (ValueError, IndexError):
                    pass
        for i in range(len(chunk)):
            out.append(parsed.get(i + 1) or chunk[i])
    return out


def _json_from_gemini(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _translate_subtitle_languages_gemini(
    original: List[Dict[str, Any]],
    languages: List[str],
    settings: Dict[str, Any],
    ctx,
    lo: float,
    hi: float,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fast path for subtitle export: translate many languages in the same Gemini calls.

    Local NLLB is useful offline, but for a 30-minute transcript times 10+ languages it
    is the wrong tool. This batches by transcript segment and asks Gemini for every
    requested language at once, cutting request/model overhead by roughly the number of
    selected languages.
    """
    api_key = (settings.get("gemini_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("Brak klucza Gemini.")
    targets = [l for l in languages if str(l or "").strip()]
    if not targets:
        return {}

    import requests  # type: ignore
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    texts = [str(s.get("text", "") or "").strip() for s in original]
    out: Dict[str, List[str]] = {lang: [""] * len(texts) for lang in targets}
    full_context = " ".join(t for t in texts if t)[:2500]
    glossary = str(settings.get("proper_name_glossary") or "").strip()
    gloss = f"\nPreserve these names/terms exactly when they appear:\n{glossary}\n" if glossary else ""
    chunk_size = 24
    total_chunks = max(1, math.ceil(len(texts) / chunk_size))

    system = f"""You are a professional subtitle translator.
Translate video subtitle segments into ALL requested target languages.
Keep the same meaning and natural spoken style. Do not summarize, merge, split, omit, or add facts.
Return ONLY valid JSON matching this shape:
{{"translations": {{"Language name": ["translated segment 1", "translated segment 2"]}}}}
Every target language array must contain exactly the same number of strings as the input segment array.
Full context for consistency:
{full_context}
{gloss}"""

    for chunk_no, off in enumerate(range(0, len(texts), chunk_size), start=1):
        ctx.check_cancel()
        chunk = texts[off:off + chunk_size]
        ctx.progress(
            lo + ((chunk_no - 1) / total_chunks) * (hi - lo),
            f"Szybkie tłumaczenie Gemini: paczka {chunk_no}/{total_chunks}, {len(targets)} języków…",
        )
        payload = {
            "contents": [{
                "parts": [{
                    "text": json.dumps({
                        "target_languages": targets,
                        "segments": chunk,
                    }, ensure_ascii=False)
                }]
            }],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"response_mime_type": "application/json"},
        }
        res = requests.post(url, json=payload, timeout=180)
        res.raise_for_status()
        raw = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        data = _json_from_gemini(raw)
        trans = data.get("translations", {})
        if not isinstance(trans, dict):
            raise RuntimeError("Gemini zwrócił nieprawidłowy format tłumaczenia.")
        for lang in targets:
            arr = trans.get(lang)
            if not isinstance(arr, list) or len(arr) != len(chunk):
                raise RuntimeError(f"Gemini zwrócił złą liczbę segmentów dla języka {lang}.")
            for j, value in enumerate(arr):
                out[lang][off + j] = str(value or "").strip() or chunk[j]

    return {
        lang: [{**original[i], "text": out[lang][i] or texts[i]} for i in range(len(original))]
        for lang in targets
    }


def _translate_internal(original: List[Dict[str, Any]], settings: Dict[str, Any], ctx, lo: float, hi: float) -> List[Dict[str, Any]]:
    translation_model = str(settings.get("translation_model", ""))
    if "Brak" in translation_model:
        return [dict(s) for s in original]
    target = settings.get("target_lang", "Angielski")
    ctx.progress(lo, f"Tłumaczenie na: {target}…")
    engine = str(settings.get("translation_engine", "nllb") or "nllb").lower()
    api_key = (settings.get("gemini_api_key") or "").strip()

    # Preferred: context-aware Gemini (best for slang/idioms, like the old DubMaster).
    if engine == "gemini" and api_key:
        try:
            texts = _translate_gemini_dub([s.get("text", "") for s in original], target, api_key,
                                          settings.get("proper_name_glossary", ""), ctx)
            ctx.progress(hi, "Przetłumaczono (Gemini).")
            return [{**original[i], "text": texts[i]} for i in range(len(original))]
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Gemini nie powiódł się ({e}); używam lokalnego NLLB.", "warning")

    # Fallback: local NLLB (segment-by-segment).
    import local_translate  # type: ignore
    short = {
        "segments": [dict(s) for s in original],
        "words": [{"word": s["text"], "start": s["start_time"], "end": s["end_time"]} for s in original],
        "title": "", "hook_text": "", "hashtags": "", "yt_tags": "",
    }
    ok = local_translate.translate_short(
        short, target, engine=engine, gemini_api_key=(api_key if engine == "gemini" else ""), split_segments=False,
        glossary_text=settings.get("proper_name_glossary", ""),
        cancel_check=ctx.check_cancel,
    )
    if not ok:
        raise RuntimeError("Tłumaczenie nie powiodło się (sprawdź klucz Gemini lub lokalny silnik NLLB/Argos).")
    ctx.progress(hi, "Przetłumaczono (NLLB).")
    return short["segments"]


# ── Transcript cache ──────────────────────────────────────────────────────────
# Whisper is by far the slowest step; the same source re-uploaded would otherwise be
# re-transcribed from scratch. Cache the transcript text keyed by the resolved video
# (path + size + mtime) and the forced language, in a stable dir next to the session
# folders, so re-analysing an identical file is instant.
def _transcript_cache_dir(output_dir: Path) -> Path:
    d = Path(output_dir).parent / ".transcript_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _transcript_cache_key(video_file: str, lang_code) -> str:
    import hashlib
    try:
        st = os.stat(video_file)
        sig = f"{os.path.abspath(video_file)}|{st.st_size}|{int(st.st_mtime)}|{lang_code or 'auto'}"
    except OSError:
        sig = f"{os.path.abspath(video_file)}|{lang_code or 'auto'}"
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()


def _find_reusable_session(sessions_root: Path, video_file: str, lang_code):
    """Find an already-analysed session for the SAME source file + language so a
    re-upload skips Whisper AND avoids a duplicate history entry. Matches on the stored
    signature (path|size|mtime|lang); legacy sessions without one fall back to an
    absolute-path match (file must still exist). Returns (dir, session_data) or None."""
    try:
        want = _transcript_cache_key(video_file, lang_code)
        want_abs = os.path.abspath(video_file)
    except Exception:
        return None
    if not sessions_root.exists():
        return None
    dirs = [d for d in sessions_root.iterdir() if d.is_dir() and (d / "session.json").exists()]
    for d in sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads((d / "session.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data.get("original_segments"):
            continue
        sig = data.get("source_sig")
        if sig:
            if sig == want:
                return d, data
        else:
            sv = str(data.get("video_file") or "")
            if sv and os.path.abspath(sv) == want_abs and os.path.exists(sv):
                return d, data
    return None


def _apply_glossary_to_segments(segments, glossary: str):
    """Apply the proper-name glossary to each segment's text (used when reusing a stored
    session). Returns a NEW list; leaves the input untouched on any failure."""
    if not glossary:
        return segments
    try:
        from ai_processor import apply_whisper_glossary_to_transcript  # type: ignore
    except Exception:  # noqa: BLE001
        return segments
    out = []
    for s in segments:
        s2 = dict(s)
        txt = s2.get("text")
        if txt:
            try:
                s2["text"] = apply_whisper_glossary_to_transcript(str(txt), glossary)
            except Exception:  # noqa: BLE001
                pass
        out.append(s2)
    return out


def _split_long_segments_list(segments, max_dur: float = 9.0, max_chars: int = 200):
    """Whisper emits one segment per speech run between pauses — when the speaker talks
    continuously (reading instructions etc.) a single segment can be 20–40 s / many
    sentences, which is an unreadable subtitle and an awkward editor row. Split such
    segments into sentence-sized pieces, distributing the time span proportionally to
    sentence length (so each piece keeps a plausible start/end). Short segments pass
    through untouched. Idempotent: already-split single sentences won't split again."""
    try:
        from local_translate import _split_sentences  # type: ignore
    except Exception:  # noqa: BLE001
        return segments
    out = []
    for seg in segments:
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start))
        span = end - start
        text = str(seg.get("text", "")).strip()
        if (span <= max_dur and len(text) <= max_chars):
            out.append(seg)
            continue
        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            out.append(seg)
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


# ── STAGE 1: analyze (download → transcribe → translate) ──────────────────────
def analyze_dub(source: str, settings: Dict[str, Any], ctx, *, output_dir: Path,
                force: bool = False) -> Dict[str, Any]:
    work = Path(output_dir)
    ctx.progress(0.05, "Przygotowanie źródła wideo…")
    is_yt = str(settings.get("input_method", "")).lower().startswith("pobierz") or source.startswith("http")
    # Friendly title: real YouTube title, or the local file's name.
    display_title = ""
    if is_yt:
        try:
            from downloader import get_video_title  # type: ignore
            display_title = (get_video_title(source) or "").strip()
        except Exception:
            display_title = ""
    video_file = _resolve_video(source, settings)
    if not video_file or not os.path.exists(video_file):
        raise RuntimeError("Nie znaleziono pliku wideo do dubbingu.")
    if not display_title:
        display_title = Path(video_file).stem

    try:
        from config import LANG_MAP  # type: ignore
        lang_code = LANG_MAP.get(settings.get("source_lang", "Automatyczne wykrywanie"))
    except Exception:
        lang_code = None

    glossary = (settings.get("proper_name_glossary") or "").strip()
    existing = _find_reusable_session(work.parent, video_file, lang_code)

    # Already analysed this exact source (same file + language)? Reuse that session —
    # no Whisper, no audio re-extract, no duplicate in History. Skipped when `force` is
    # set (the "Transkrybuj ponownie" button) so Whisper truly runs again.
    if existing and not force:
        d, data = existing
        ctx.progress(1.0, "Ten film był już analizowany — wczytano zapisaną transkrypcję.")
        ctx.log(f"Reużyto sesji {d.name} — pominięto transkrypcję.", "info")
        # Re-apply the current glossary AND re-split over-long segments so older sessions
        # (made before these passes existed) get cleaned up; persist so it sticks + shows
        # in History.
        segs = data.get("original_segments", [])
        fixed = _split_long_segments_list(_apply_glossary_to_segments(segs, glossary))
        if fixed != segs:
            data["original_segments"] = fixed
            try:
                (d / "session.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return {
            "session": d.name,
            "original_segments": _seg_out(fixed),
            "original_words": _words_for_segments(fixed, data.get("original_words", [])),
            "translated_segments": [],
            "source_lang": settings.get("source_lang"), "target_lang": settings.get("target_lang"),
            "reused": True,
        }

    # Forced re-transcription: delete the previous session payload and cache before
    # starting again, so neither words nor subtitle edits from an older Whisper pass
    # can reappear in Dubbing Studio.
    cache_file = _transcript_cache_dir(output_dir) / f"{_transcript_cache_key(video_file, lang_code)}.txt"
    if force:
        if existing:
            work = existing[0]
            try:
                shutil.rmtree(work)
            except OSError:
                pass
        try:
            cache_file.unlink()
        except OSError:
            pass

    work.mkdir(parents=True, exist_ok=True)
    total_dur = _media_duration(video_file)

    full_audio = work / "original.wav"
    ctx.progress(0.15, "Wyodrębnianie ścieżki audio…")
    _extract_full_audio(video_file, full_audio)

    transcript = ""
    _words: List[Dict[str, Any]] = []
    if cache_file.exists():
        try:
            transcript = cache_file.read_text(encoding="utf-8")
            if transcript.strip():
                ctx.progress(0.9, "Użyto zapisanej transkrypcji (ten plik był już analizowany).")
                ctx.log("Transkrypcja z pamięci podręcznej — pominięto Whisper.", "info")
        except OSError:
            transcript = ""
    if not transcript.strip():
        ctx.progress(0.3, "Transkrypcja mowy (Whisper)…")
        from ai_processor import load_whisper, transcribe_video  # type: ignore
        transcript, _words = transcribe_video(video_file, load_whisper(), lang_code=lang_code)
        try:
            cache_file.write_text(transcript, encoding="utf-8")
        except OSError:
            pass
    original = _parse_segments(transcript)
    if not original:
        raise RuntimeError("Transkrypcja nie zwróciła żadnych segmentów mowy.")
    # Apply the proper-name glossary (so spellings like "Opitkovanie" are already correct
    # and every translation inherits them — the cache keeps RAW Whisper text so a glossary
    # edit is picked up next time), then split Whisper's over-long run-on segments into
    # readable sentence-sized lines.
    original = _split_long_segments_list(_apply_glossary_to_segments(original, glossary))
    ctx.log(f"Rozpoznano {len(original)} segmentów mowy.", "info")

    # Transcription only — translation is a separate, user-approved step.
    session = {
        "video_file": os.path.abspath(video_file), "full_audio": str(full_audio), "total_dur": total_dur,
        "title": display_title,
        "source_lang": settings.get("source_lang", ""),
        "source_sig": _transcript_cache_key(video_file, lang_code),
        # Remember where the film came from so the Napisy UI can list recent YouTube
        # films (by title, click to re-process) separately from local-file work.
        "source": source,
        "is_youtube": bool(is_yt),
        "created_at": int(time.time()),
        "original_segments": original, "original_words": _words_for_segments(original, _words), "translated_segments": [],
    }
    (work / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    ctx.progress(1.0, "Gotowe — sprawdź i popraw transkrypcję, potem przetłumacz.")
    return {
        "session": work.name,
        "original_segments": _seg_out(original),
        "original_words": _words_for_segments(original, _words),
        "translated_segments": [],
        "source_lang": settings.get("source_lang"), "target_lang": settings.get("target_lang"),
    }


# ── STAGE 2: re-translate edited original text ────────────────────────────────
def translate_dub(session_dir: Path, original_segments: List[Dict[str, Any]], settings: Dict[str, Any], ctx) -> Dict[str, Any]:
    work = Path(session_dir)
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    original = _seg_in(original_segments)
    translated = _translate_internal(original, settings, ctx, 0.2, 0.95)
    session["original_segments"] = original
    session["translated_segments"] = translated
    (work / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    ctx.progress(1.0, "Tłumaczenie zaktualizowane.")
    return {"translated_segments": _seg_out(translated)}


def save_transcript(session_dir: Path, original_segments: List[Dict[str, Any]], original_words: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Persist a hand-edited original transcript to the session WITHOUT generating
    anything — used by the editor's autosave so corrections survive app restarts and a
    later re-analysis reuses them instead of the raw Whisper text."""
    work = Path(session_dir)
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    orig = _seg_in(original_segments)
    if not orig:
        return {"ok": False, "saved": 0}
    session["original_segments"] = orig
    if original_words is not None:
        session["original_words"] = _words_for_segments(orig, original_words)
    (work / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "saved": len(orig)}


# ── Segment prep (ported from DubMaster — optimal TTS chunking per mode) ──────
def _chunk_text(text: str, max_chars: int = 115, max_words: int = 20) -> List[str]:
    clean = " ".join(str(text or "").split())
    if not clean:
        return [""]
    words = clean.split()
    if len(clean) <= max_chars and len(words) <= max_words:
        return [clean]
    parts = [p.strip() for p in re.split(r"(?<=[.!?;:])\s+|(?<=,)\s+", clean) if p.strip()] or [clean]
    chunks: List[str] = []
    cur = ""
    for part in parts:
        cand = (cur + " " + part).strip() if cur else part
        if cur and (len(cand) > max_chars or len(cand.split()) > max_words):
            chunks.append(cur)
            cur = part
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    final: List[str] = []
    for c in chunks:
        cw = c.split()
        if len(c) <= max_chars and len(cw) <= max_words:
            final.append(c)
            continue
        for i in range(0, len(cw), max_words):
            final.append(" ".join(cw[i:i + max_words]).strip())
    return [c for c in final if c.strip()] or [clean]


def _expand_for_dub(seg: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split long lines into sub-windows (>=1.35s each) inside the same span —
    shorter TTS lines = no dropped words, while avoiding choppy micro-segments."""
    out: List[Dict[str, Any]] = []
    for s in seg:
        start = float(s["start_time"]); end = float(s["end_time"]); dur = max(end - start, 0.05)
        text = str(s.get("text", "")).strip()
        chunks = _chunk_text(text)
        if len(chunks) > 1 and dur / len(chunks) < 1.35:
            chunks = [" ".join(text.split())]
        if len(chunks) == 1:
            out.append({"start_time": start, "end_time": end, "text": chunks[0]})
            continue
        weights = [max(len(c), 1) for c in chunks]
        tot = float(sum(weights))
        cur = start
        for i, (c, w) in enumerate(zip(chunks, weights)):
            ce = end if i == len(chunks) - 1 else cur + dur * (w / tot)
            ce = min(end, max(cur + 0.05, ce))
            out.append({"start_time": cur, "end_time": ce, "text": c})
            cur = ce
    return out


def _merge_for_voiceover(seg: List[Dict[str, Any]], max_chars: int = 260, max_duration: float = 14.0, max_gap: float = 1.0) -> List[Dict[str, Any]]:
    """Merge adjacent lines for lektor — longer, natural phrases = stable prosody."""
    units: List[Dict[str, Any]] = []
    cur = None
    cur_t: List[str] = []
    for s in seg:
        text = " ".join(str(s.get("text", "")).split()).strip()
        if not text:
            continue
        start = float(s["start_time"]); end = float(s["end_time"])
        if cur is None:
            cur = {"start_time": start, "end_time": end}; cur_t = [text]; continue
        gap = start - float(cur["end_time"])
        cand = " ".join(cur_t + [text]).strip()
        cand_dur = end - float(cur["start_time"])
        if gap <= max_gap and len(cand) <= max_chars and cand_dur <= max_duration:
            cur["end_time"] = end; cur_t.append(text)
        else:
            cur["text"] = " ".join(cur_t).strip(); units.append(cur)
            cur = {"start_time": start, "end_time": end}; cur_t = [text]
    if cur is not None:
        cur["text"] = " ".join(cur_t).strip(); units.append(cur)
    return units


# ── STAGE 3: render (TTS → timeline → mix → mux) ──────────────────────────────
def render_dub(session_dir: Path, segments: List[Dict[str, Any]], settings: Dict[str, Any], ctx, *, ref_audio_path: str = "") -> Dict[str, Any]:
    import dubbing_engine as de  # type: ignore
    work = Path(session_dir)
    # A cancelled render must leave no half-generated TTS/audio intermediates in
    # the session. The source, transcript and earlier successful exports stay intact.
    def _cancel_cleanup() -> None:
        for pattern in ("tts_*_raw.wav", "tts_*_fit.wav", "qwen_job_*.json", "qwen_result_*.json", "qwen_runner_*.py", "omni_job_*.json", "omni_result_*.json", "omni_runner_*.py"):
            for item in work.glob(pattern):
                try:
                    item.unlink()
                except OSError:
                    pass
        for name in ("dub_track.wav", "final_audio.m4a"):
            try:
                (work / name).unlink()
            except OSError:
                pass
    ctx.on_cancel(_cancel_cleanup)
    ctx.check_cancel()
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    video_file = session["video_file"]
    full_audio = Path(session["full_audio"])
    total_dur = float(session.get("total_dur") or _media_duration(video_file))
    seg = _seg_in(segments)
    if not seg:
        raise RuntimeError("Brak tekstu do dubbingu.")

    target_lang = settings.get("target_lang", "Angielski")
    mix_mode = settings.get("mix_mode", "Czysty dubbing (usuń oryginalny głos)")
    is_voiceover = str(mix_mode).startswith("Lektor")
    is_ducking = "ducking" in str(mix_mode).lower()

    # Per-mode segment prep (DubMaster method): merge for lektor prosody,
    # split long lines for clean dubbing so Qwen never drops words.
    seg = _merge_for_voiceover(seg) if is_voiceover else _expand_for_dub(seg)
    if not seg:
        raise RuntimeError("Brak tekstu do dubbingu.")
    keep_bg = bool(settings.get("keep_bg", True))
    voice_source = settings.get("voice_source", "Głos z oryginalnego filmu")

    # Reference-voice mode: "filtered" (clean Demucs vocals — no room reverb/ambient) or
    # "ambient" (raw original). Mirrors the Shorts module. Default filtered = cleaner clone.
    use_filtered_voice = str(settings.get("voice_ref", "filtered")).lower() != "ambient"
    need_demucs = keep_bg or is_voiceover or (voice_source == "Głos z oryginalnego filmu" and use_filtered_voice)

    bg_path = None
    if need_demucs:
        try:
            ctx.progress(0.06, "Oddzielanie tła od głosu (Demucs)…")
            no_vocals = de._ensure_demucs_background(str(full_audio), work / "demucs", _StatusAdapter(ctx))
            if keep_bg or is_voiceover:
                bg_path = no_vocals
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Nie udało się oddzielić tła: {e}", "warning")
            bg_path = None

    ref_audio = ""
    if voice_source == "Sklonowany głos (własna próbka)":
        ref_audio = ref_audio_path or ""
        if not ref_audio or not os.path.exists(ref_audio):
            raise RuntimeError("Wybrano własną próbkę głosu, ale plik nie istnieje.")
    elif voice_source == "Głos z oryginalnego filmu":
        ref_audio = str(work / "voice_reference.wav")
        clean_voice = de._demucs_vocals_path(work / "demucs") if use_filtered_voice else None
        de._extract_voice_reference(video_file, seg, ref_audio, int(settings.get("ref_audio_length", 12)), clean_voice=clean_voice)

    ctx.progress(0.12, "Synteza głosu (Qwen TTS)…")
    texts = [str(s.get("text", "")).strip() for s in seg]
    durations = [max(0.25, float(s["end_time"]) - float(s["start_time"])) for s in seg]
    raw_paths = [work / f"tts_{i:04d}_raw.wav" for i in range(len(texts))]
    engine_settings = {
        **settings,
        "dub_qwen_speaker": settings.get("dubbing_qwen_speaker", "Aiden"),
        "dub_style_prompt": settings.get("voiceover_style", ""),
        "dub_auto_min_tempo": bool(settings.get("auto_min_tempo", False)),
        "dub_auto_max_tempo": bool(settings.get("auto_max_tempo", True)),
        "dub_sync_min_tempo": float(settings.get("sync_min_tempo", 0.9)),
        "dub_sync_max_tempo": float(settings.get("sync_max_tempo", 1.0)),
    }
    de._run_tts(texts, texts, target_lang, ref_audio, raw_paths, durations,
                engine_settings, _StatusAdapter(ctx), _ProgressAdapter(ctx, 0.12, 0.78))

    ctx.progress(0.82, "Dopasowanie tempa do scen…")
    fit_paths = []
    for i, raw in enumerate(raw_paths):
        fit = work / f"tts_{i:04d}_fit.wav"
        if Path(raw).exists():
            min_t, max_t = de._resolve_tempo_limits(raw, durations[i], engine_settings)
            de._stretch_to_window(raw, fit, durations[i], min_t, max_t)
        fit_paths.append(fit)

    ctx.progress(0.86, "Składanie ścieżki dubbingu na osi czasu…")
    dub_track = work / "dub_track.wav"
    _place_on_timeline(fit_paths, seg, total_dur, dub_track)

    ctx.progress(0.9, "Miksowanie ścieżki dźwiękowej…")
    final_audio = work / "final_audio.m4a"
    dub_vol = float(settings.get("dub_vol", 1.5))
    bg_vol = float(settings.get("bg_music_vol", 1.4))
    orig_vol = float(settings.get("voiceover_original_vol", 1.0))
    duck = float(settings.get("voiceover_duck_amount", 0.8))
    ff = _ffmpeg()
    if is_voiceover:
        if is_ducking:
            duck_ratio = 1.0 + max(0.0, min(1.0, duck)) * 14.0
            fc = (f"[0:a]volume={orig_vol}[orig];[1:a]volume={dub_vol}[dub];"
                  "[dub]asplit=2[dm][dd];"
                  f"[orig][dd]sidechaincompress=threshold=0.03:ratio={duck_ratio:.2f}:attack=20:release=300[od];"
                  "[od][dm]amix=inputs=2:duration=longest:normalize=0[a]")
        else:
            fc = (f"[0:a]volume={orig_vol}[orig];[1:a]volume={dub_vol}[dub];"
                  "[orig][dub]amix=inputs=2:duration=longest:normalize=0[a]")
        cmd = [ff, "-y", "-loglevel", "error", "-i", str(full_audio), "-i", str(dub_track),
               "-filter_complex", fc, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    elif bg_path and os.path.exists(str(bg_path)):
        fc = (f"[0:a]volume={bg_vol}[bg];[1:a]volume={dub_vol}[dub];"
              "[bg][dub]amix=inputs=2:duration=longest:normalize=0[a]")
        cmd = [ff, "-y", "-loglevel", "error", "-i", str(bg_path), "-i", str(dub_track),
               "-filter_complex", fc, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    else:
        cmd = [ff, "-y", "-loglevel", "error", "-i", str(dub_track),
               "-af", f"volume={dub_vol}", "-c:a", "aac", "-b:a", "192k", str(final_audio)]
    _run(cmd, "Miks dubbingu")

    ctx.progress(0.95, "Łączenie z wideo…")
    display_title = session.get("title") or Path(video_file).stem
    safe_title = re.sub(r"[^\w-]+", "_", display_title)[:70].strip("_") or "dub"
    lang_slug = de.language_slug(target_lang)
    out_mp4 = work / f"{safe_title}_{lang_slug}.mp4"
    _run([ff, "-y", "-loglevel", "error", "-i", str(video_file), "-i", str(final_audio),
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-shortest", str(out_mp4)], "Mux wideo")

    srt_path = work / f"{safe_title}_{lang_slug}.srt"
    try:
        _write_srt(seg, srt_path)
    except Exception:
        srt_path = None

    manifest = {
        "title": display_title[:120],
        "language": target_lang, "language_slug": lang_slug,
        "created_at": int(time.time()), "video": str(out_mp4),
        "audio": str(final_audio), "subtitle": str(srt_path) if srt_path else "",
        "mix_mode": mix_mode, "segments": seg,
    }
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.progress(1.0, f"Gotowe — dubbing {target_lang} wyrenderowany.")
    return {"file": str(out_mp4), "audio": str(final_audio),
            "subtitle": str(srt_path) if srt_path else "", "language": target_lang, "dir": str(work)}


def _srt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60); ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(segments: List[Dict[str, Any]], path: Path) -> None:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(float(seg['start_time']))} --> {_srt_ts(float(seg['end_time']))}")
        lines.append(str(seg.get("text", "")).strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _vtt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = int(sec % 60); ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _write_vtt(segments: List[Dict[str, Any]], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_ts(float(seg['start_time']))} --> {_vtt_ts(float(seg['end_time']))}")
        lines.append(str(seg.get("text", "")).strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ── Subtitle-only export (no TTS / no render) ─────────────────────────────────
def subtitles_dub(session_dir: Path, segments: List[Dict[str, Any]], settings: Dict[str, Any], ctx) -> Dict[str, Any]:
    import dubbing_engine as de  # type: ignore
    work = Path(session_dir)
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    seg = _seg_in(segments)
    if not seg:
        raise RuntimeError("Brak tekstu do napisów.")
    target_lang = settings.get("target_lang", "Angielski")
    lang_slug = de.language_slug(target_lang)
    display_title = session.get("title") or Path(session["video_file"]).stem
    safe_title = re.sub(r"[^\w-]+", "_", display_title)[:70].strip("_") or "napisy"
    ctx.progress(0.4, "Zapisywanie napisów…")
    srt = work / f"{safe_title}_{lang_slug}.srt"
    vtt = work / f"{safe_title}_{lang_slug}.vtt"
    _write_srt(seg, srt)
    _write_vtt(seg, vtt)
    ctx.progress(1.0, f"Napisy {target_lang} gotowe (SRT + VTT).")
    return {"subtitle": str(srt), "vtt": str(vtt), "language": target_lang, "dir": str(work)}


# ── Batch subtitles in multiple languages (Subtitles app) ─────────────────────
def subtitles_batch(session_dir: Path, original_segments: List[Dict[str, Any]], languages: List[str],
                    settings: Dict[str, Any], ctx, *, include_original: bool = False) -> Dict[str, Any]:
    import dubbing_engine as de  # type: ignore
    work = Path(session_dir)
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    orig = _seg_in(original_segments)
    if not orig:
        raise RuntimeError("Brak transkrypcji do napisów.")
    # Persist the (possibly hand-corrected) transcript back to the session so edits
    # survive app restarts and a later re-analysis reuses the corrected text instead of
    # the raw Whisper output.
    if orig != session.get("original_segments"):
        session["original_segments"] = orig
        try:
            (work / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    display_title = session.get("title") or Path(session["video_file"]).stem
    safe = re.sub(r"[^\w-]+", "_", display_title)[:70].strip("_") or "napisy"

    jobs: List[tuple] = []
    if include_original:
        jobs.append((settings.get("source_lang") or "Oryginał", None))
    for lang in languages:
        jobs.append((lang, lang))
    if not jobs:
        raise RuntimeError("Nie wybrano żadnego języka napisów.")

    results = []
    translated_by_lang: Dict[str, List[Dict[str, Any]]] = {}
    target_langs = [str(tgt) for _label, tgt in jobs if tgt is not None]
    engine = str(settings.get("translation_engine", "nllb") or "nllb").lower()
    no_translate = "Brak" in str(settings.get("translation_model", ""))
    if (
        engine == "gemini"
        and target_langs
        and (settings.get("gemini_api_key") or "").strip()
        and not no_translate
    ):
        try:
            translated_by_lang = _translate_subtitle_languages_gemini(
                orig, target_langs, settings, ctx, 0.02, 0.88)
            ctx.progress(0.9, "Szybkie tłumaczenie Gemini zakończone — zapisuję pliki napisów…")
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Szybkie tłumaczenie Gemini nie powiodło się ({e}); wracam do starej ścieżki.", "warning")

    # Local fast path: translate EVERY requested language in one pass, encoding the
    # source transcript a single time (NLLB encoder shared across languages). This is
    # the local equivalent of the Gemini "all languages at once" call and is what keeps
    # 1 vs 15 languages roughly the same cost instead of multiplying per language.
    pending = [t for t in target_langs if t not in translated_by_lang]
    if pending and not no_translate and engine != "gemini":
        try:
            import local_translate  # type: ignore
            seg_texts = [str(s.get("text", "") or "") for s in orig]
            _started = {"n": 0}

            def _prog(frac: float, lang: str) -> None:
                _started["n"] += 1
                ctx.progress(0.02 + frac * 0.86,
                             f"Tłumaczenie napisów: {lang} ({_started['n']}/{len(pending)})…")

            multi = local_translate.translate_segments_multi(
                seg_texts, pending, engine=engine,
                glossary_text=settings.get("proper_name_glossary", ""),
                cancel_check=ctx.check_cancel,
                progress=_prog,
            )
            for lang, texts in multi.items():
                translated_by_lang[lang] = [
                    {**orig[i], "text": (texts[i] if i < len(texts) else orig[i].get("text", "")) or orig[i].get("text", "")}
                    for i in range(len(orig))
                ]
            ctx.progress(0.9, "Tłumaczenie lokalne zakończone — zapisuję pliki napisów…")
        except Exception as e:  # noqa: BLE001
            ctx.log(f"Szybkie tłumaczenie lokalne nie powiodło się ({e}); wracam do starej ścieżki.", "warning")

    n = len(jobs)
    for i, (label, tgt) in enumerate(jobs):
        ctx.check_cancel()
        if tgt is None:
            segs = orig
        elif tgt in translated_by_lang:
            segs = translated_by_lang[tgt]
        else:
            ctx.progress(i / n, f"Tłumaczenie napisów: {label}…")
            segs = _translate_internal(orig, {**settings, "target_lang": tgt}, ctx, i / n + 0.02, (i + 1) / n - 0.02)
        slug = de.language_slug(label)
        srt = work / f"{safe}_{slug}.srt"
        vtt = work / f"{safe}_{slug}.vtt"
        _write_srt(segs, srt)
        _write_vtt(segs, vtt)
        results.append({"language": label, "srt": str(srt), "vtt": str(vtt)})

    # Persist a subtitles manifest so the History tab can list every language
    # version produced for this video (merging across repeated runs).
    man_path = work / "subs_manifest.json"
    by_lang: Dict[str, Any] = {}
    if man_path.exists():
        try:
            for r in json.loads(man_path.read_text(encoding="utf-8")).get("results", []):
                by_lang[r.get("language", "")] = r
        except Exception:
            pass
    for r in results:
        by_lang[r["language"]] = r
    man_path.write_text(json.dumps({
        "title": display_title, "created_at": int(time.time()),
        "results": [v for v in by_lang.values() if v.get("language")],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    ctx.progress(1.0, "Napisy gotowe.")
    return {"results": results}
