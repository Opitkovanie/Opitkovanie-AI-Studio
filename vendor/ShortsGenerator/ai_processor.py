import re
import json
import copy
import math
import sys
import os
import platform
import subprocess
import tempfile
from google import genai
from google.genai import types
from utils import get_ffmpeg_path

def parse_vtt_to_transcript(vtt_file):
    with open(vtt_file, 'r', encoding='utf-8') as f: content = f.read()
    pattern = re.compile(r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})[^\n]*\n(.*?)(?=\n\n|\n\d{2}:\d{2}|\Z)', re.DOTALL)
    transcript = ""; global_words = []; last_text = ""
    for match in pattern.finditer(content):
        start_str, end_str, text = match.groups()
        start_sec = sum(x * float(t) for x, t in zip([3600, 60, 1], start_str.split(':')))
        end_sec = sum(x * float(t) for x, t in zip([3600, 60, 1], end_str.split(':')))
        clean_text = re.sub(r'<[^>]+>', '', text).replace('\n', ' ').strip()
        if not clean_text or clean_text == last_text or clean_text in last_text: continue
        transcript += f"[{start_sec:.1f} - {end_sec:.1f}] {clean_text}\n"
        last_text = clean_text
        words = clean_text.split()
        if words:
            dur = (end_sec - start_sec) / len(words)
            ct = start_sec
            for w in words:
                global_words.append({"word": w, "start": ct, "end": ct + dur})
                ct += dur
    return transcript, global_words


def _clean_json_text(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _load_json_array(text):
    cleaned = _clean_json_text(text)
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("Odpowiedź AI nie jest listą shortów.")
    return data


def _salvage_complete_json_objects(text):
    cleaned = _clean_json_text(text)
    start = cleaned.find("[")
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    pos = start + 1
    items = []
    while pos < len(cleaned):
        while pos < len(cleaned) and cleaned[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(cleaned) or cleaned[pos] == "]":
            break
        try:
            item, pos = decoder.raw_decode(cleaned, pos)
        except Exception:
            break
        if isinstance(item, dict):
            items.append(item)
    return items


def _repair_json_array_with_gemini(client, broken_text, json_schema):
    repair_prompt = f"""
Napraw poniższą odpowiedź JSON.

Zasady:
- Zwróć TYLKO poprawną tablicę JSON.
- Jeśli ostatni obiekt jest ucięty lub uszkodzony, usuń go albo domknij tylko wtedy, gdy da się to zrobić bez zgadywania.
- Nie dodawaj komentarzy, markdown ani wyjaśnień.
- Zachowaj istniejące pola: title, score, hook_text, hashtags, yt_tags, segments.

USZKODZONY JSON:
{broken_text}
"""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=repair_prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=json_schema,
            temperature=0.0,
            max_output_tokens=65536,
        ),
    )
    return _load_json_array(response.text)

# ---------------------------------------------------------------------------
# Wykrywanie backendu Whisper
# ---------------------------------------------------------------------------
def _is_apple_silicon():
    return sys.platform == "darwin" and platform.machine() == "arm64"

BACKEND = "mlx" if _is_apple_silicon() else "faster_whisper"

if BACKEND == "faster_whisper":
    from faster_whisper import WhisperModel

# Prompt wprowadzajacy Whisper w kontekst polskich tresci technicznych.
WHISPER_INITIAL_PROMPT = (
    "Transkrypcja po polsku. Tresc techniczna i biznesowa. "
    "Jednostki miar: V (wolt), W (wat), kW, MW, A (amper), Hz, kHz, MHz, GHz, "
    "cm, mm, m, km, kg, g, mg, l, ml, dB, procent, stopni Celsjusza. "
    "Firmy i marki: Samsung, Apple, Google, Microsoft, Intel, AMD, NVIDIA, "
    "Sony, LG, Tesla, BMW, Mercedes, Toyota, Amazon, Meta, OpenAI, "
    "YouTube, TikTok, Instagram, Facebook, Allegro, Spotify. "
    "Liczby i wartosci techniczne pisane cyframi. Poprawna interpunkcja."
)

# ---------------------------------------------------------------------------
# Słownik poprawek Whisper (post-processing)
# ---------------------------------------------------------------------------

DEFAULT_WHISPER_GLOSSARY = """# Słownik błędów Whisper
# Dwa formaty — jeden wpis na linię:
#   błędna wersja -> poprawna wersja   (zamień błąd Whispera)
#   PoprawnaWersja                     (pilnuj wielkości liter)
# Linie zaczynające się od # są ignorowane.

# --- Poprawki błędów Whisper ---
Humsieng -> Humsienk
Humsięk -> Humsienk
Humsienka -> Humsienk's
Amfropik -> Anthropic
Amthropic -> Anthropic
Amphropic -> Anthropic
Open AI -> OpenAI
Chat GPT -> ChatGPT

# --- Nazwy własne (pilnuj wielkości liter) ---
Humsienk
Claude Code
Anthropic
OpenAI
ChatGPT
Codex
Gemini
Whisper
Qwen
LiFePO4
WattCycle
Opitkovanie"""


def parse_whisper_glossary(glossary_text):
    """
    Parsuje słownik poprawek Whisper. Obsługuje dwa formaty:
      1. Para:    błędna wersja -> poprawna wersja
      2. Nazwa:   PoprawnaWersja  (pilnuje wielkości liter — case correction)

    Zwraca krotkę (rules, canonical_terms):
      - rules: lista (wrong, right) — zastąpienia dosłowne
      - canonical_terms: lista poprawnych nazw — koryguje wielkość liter w tekście
    Obie listy posortowane malejąco po długości, żeby dłuższe wzorce były
    dopasowywane przed krótszymi (np. "Chat GPT" przed "GPT").
    """
    rules = []
    canonical_terms = []
    seen_rules = set()
    seen_canonical = set()

    for raw_line in str(glossary_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            wrong, right = [part.strip() for part in line.split("->", 1)]
            if wrong and right:
                key = wrong.casefold()
                if key not in seen_rules:
                    rules.append((wrong, right))
                    seen_rules.add(key)
                # Prawa strona jako canonical term (żeby pilnować pisowni)
                rkey = right.casefold()
                if rkey not in seen_canonical:
                    canonical_terms.append(right)
                    seen_canonical.add(rkey)
        else:
            key = line.casefold()
            if key not in seen_canonical:
                canonical_terms.append(line)
                seen_canonical.add(key)

    rules.sort(key=lambda item: len(item[0]), reverse=True)
    canonical_terms.sort(key=len, reverse=True)
    return rules, canonical_terms


def _whisper_gloss_pattern(term):
    """Buduje regex dla bezpiecznego dopasowania słowa (obsługa polskich znaków)."""
    escaped = re.escape(term)
    return re.compile(
        rf"(?<![0-9A-Za-z_À-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]){escaped}(?![0-9A-Za-z_À-žĄąĆćĘęŁłŃńÓóŚśŹźŻż])",
        re.IGNORECASE
    )


def apply_whisper_glossary_to_transcript(text, glossary_text):
    """
    Aplikuje słownik poprawek do tekstu transkrypcji.
    1. Najpierw zamienia pary błędna -> poprawna.
    2. Potem koryguje wielkość liter dla nazw własnych (canonical terms).
    """
    if not text or not glossary_text:
        return text
    rules, canonical_terms = parse_whisper_glossary(glossary_text)
    for wrong, right in rules:
        text = _whisper_gloss_pattern(wrong).sub(right, text)
    for term in canonical_terms:
        text = _whisper_gloss_pattern(term).sub(term, text)
    return text


def apply_whisper_glossary_to_words(words, glossary_text):
    """
    Aplikuje słownik poprawek do listy słów z timestampami [{word, start, end}].
    1. Najpierw zamienia pary błędna -> poprawna.
    2. Potem koryguje wielkość liter dla nazw własnych (canonical terms).
    """
    if not words or not glossary_text:
        return words
    rules, canonical_terms = parse_whisper_glossary(glossary_text)
    if not rules and not canonical_terms:
        return words
    fixed = []
    for w in words:
        word_text = w.get("word", "")
        for wrong, right in rules:
            word_text = _whisper_gloss_pattern(wrong).sub(right, word_text)
        for term in canonical_terms:
            word_text = _whisper_gloss_pattern(term).sub(term, word_text)
        fixed.append({**w, "word": word_text})
    return fixed

def load_whisper():
    """Zwraca handle modelu lub identyfikator (mlx nie wymaga preladowania)."""
    if BACKEND == "mlx":
        # MLX Whisper nie wymaga jawnego ladowania — model pobierany lazily
        return "mlx-community/whisper-large-v3-mlx"
    else:
        # faster_whisper na CPU — medium dla dobrego balansu szybkosc/jakosc
        return WhisperModel("medium", device="cpu", compute_type="int8")


def _preprocess_audio(video_path: str) -> str:
    """
    Ekstrahuje i przetwarza audio z wideo przez ffmpeg:
    - highpass=f=80: usuwa bas/szum tla (muzyka, klimatyzacja)
    Nie używamy tu ``loudnorm``: podbija on szum i cyfrową ciszę, przez co Whisper
    potrafi dopisać tekst, którego w nagraniu nie ma.
    - 16 kHz mono PCM — format wymagany przez Whisper
    Zwraca sciezke do tymczasowego pliku WAV.
    """
    tmp = tempfile.NamedTemporaryFile(suffix="_whisper_in.wav", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        subprocess.run([
            get_ffmpeg_path(), "-y", "-i", video_path,
            "-af", "highpass=f=80",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            tmp_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        # Fallback: bez filtrow, tylko konwersja do WAV
        try:
            subprocess.run([
                get_ffmpeg_path(), "-y", "-i", video_path,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                tmp_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            return video_path  # ostatni fallback — oryginalny plik
    return tmp_path


def _transcribe_mlx(audio_path: str, lang_code=None):
    """
    Transkrypcja przez mlx-whisper w OSOBNYM SUBPROCESIE.
    
    Kluczowa roznica vs inline import: subproces laduje model do swojej pamieci,
    transkrybuje, zapisuje wynik do JSON, konczy dzialanie i ZWALNIA cala pamiec.
    Glowny proces Streamlit nie jest obciazony modelem Whisper.
    
    Model: whisper-large-v3-turbo (~4x szybszy od large-v3, praktycznie ta sama jakosc)
    """
    import uuid

    run_id = uuid.uuid4().hex[:8]
    result_file = f"/tmp/whisper_result_{run_id}.json"
    runner_file = f"/tmp/whisper_runner_{run_id}.py"

    lang_line = f'kwargs["language"] = {repr(lang_code)}' if lang_code else ""

    runner_code = f"""
import sys, json
try:
    import mlx_whisper
except ImportError:
    print("Brak mlx_whisper. Zainstaluj: pip install mlx-whisper", file=sys.stderr)
    sys.exit(1)

kwargs = {{
    "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
    "word_timestamps": True,
    "condition_on_previous_text": False,
    "initial_prompt": {repr(WHISPER_INITIAL_PROMPT)},
    "no_speech_threshold": 0.65,
    "logprob_threshold": -0.85,
    "compression_ratio_threshold": 2.2,
    "hallucination_silence_threshold": 1.0,
    "temperature": 0.0,
}}
{lang_line}

try:
    result = mlx_whisper.transcribe({repr(audio_path)}, **kwargs)
    segments_out = []
    for seg in result.get("segments", []):
        txt = seg.get("text", "").strip()
        if not txt:
            continue
        words_out = []
        for w in seg.get("words", []):
            wt = w.get("word", "").strip()
            if wt and "start" in w and "end" in w:
                words_out.append({{"word": wt, "start": w["start"], "end": w["end"]}})
        segments_out.append({{
            "start": seg.get("start", 0.0),
            "end":   seg.get("end", 0.0),
            "text":  txt,
            "words": words_out,
            "avg_logprob": seg.get("avg_logprob"),
            "no_speech_prob": seg.get("no_speech_prob"),
            "compression_ratio": seg.get("compression_ratio"),
        }})
    with open({repr(result_file)}, "w", encoding="utf-8") as f:
        json.dump(segments_out, f)
except Exception as e:
    print(f"Whisper error: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

    try:
        with open(runner_file, "w", encoding="utf-8") as f:
            f.write(runner_code)

        proc = subprocess.run(
            [sys.executable, runner_file],
            capture_output=True, text=True
        )

        if proc.returncode != 0:
            raise RuntimeError(f"mlx_whisper subprocess failed:\n{proc.stderr[-2000:]}")

        with open(result_file, "r", encoding="utf-8") as f:
            segments_out = json.load(f)

        transcript_text = ""
        global_words = []
        for seg in _filter_whisper_segments(segments_out):
            transcript_text += f"[{seg['start']:.1f} - {seg['end']:.1f}] {seg['text']}\n"
            for w in seg.get("words", []):
                global_words.append(w)

        return transcript_text, global_words

    finally:
        for p in [runner_file, result_file]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def _transcribe_faster_whisper(audio_path: str, model, lang_code=None):
    """Transkrypcja przez faster-whisper (CPU, inne platformy)."""
    kwargs = {
        "beam_size": 5,
        "task": "transcribe",
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "initial_prompt": WHISPER_INITIAL_PROMPT,
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 300,
            "speech_pad_ms": 100,
            "threshold": 0.45,
        },
        "temperature": 0.0,
        "no_speech_threshold": 0.6,
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
    }
    if lang_code:
        kwargs["language"] = lang_code

    segments, _ = model.transcribe(audio_path, **kwargs)
    rows = []
    for segment in segments:
        row = {"start": segment.start, "end": segment.end, "text": segment.text,
               "avg_logprob": getattr(segment, "avg_logprob", None),
               "no_speech_prob": getattr(segment, "no_speech_prob", None),
               "compression_ratio": getattr(segment, "compression_ratio", None), "words": []}
        if segment.words:
            for word in segment.words:
                clean_word = word.word.strip()
                if clean_word:
                    row["words"].append({"word": clean_word, "start": word.start, "end": word.end})
        rows.append(row)
    transcript_text = ""
    global_words = []
    for segment in _filter_whisper_segments(rows):
        transcript_text += f"[{segment['start']:.1f} - {segment['end']:.1f}] {segment['text']}\n"
        global_words.extend(segment["words"])
    return transcript_text, global_words


def _repeated_phrase_hallucination(text: str) -> bool:
    """Detect the classic silence loop: 'dzień dobry' repeated three times.

    We deliberately require a multi-word phrase repeated at least three times, so
    natural short repetitions such as 'ha ha' are not removed.
    """
    tokens = re.findall(r"[\wąćęłńóśźż]+", (text or "").lower(), flags=re.UNICODE)
    for width in range(2, min(5, len(tokens) // 3 + 1)):
        phrase = tokens[:width]
        if phrase and len(tokens) == width * 3 and tokens == phrase * 3:
            return True
    return False


def _norm_hallucination_text(text: str) -> str:
    text = (text or "").lower().strip()
    text = text.translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# These phrases are a known Whisper failure mode in quiet/background-heavy video.
# A real greeting is not discarded on its own; only a repeated adjacent loop is.
_SILENCE_LOOP_PHRASES = {
    "dzien dobry", "dziekuje", "dziekuje za ogladanie", "dzieki za ogladanie",
    "thanks for watching", "thank you for watching", "subtitles by", "napisy by",
}


def _filter_whisper_segments(segments):
    """Drop low-confidence / silent Whisper output before it reaches any UI.

    Both Shorts and Dubbing use this module, keeping their transcription behaviour
    identical. The numeric checks are intentionally conservative; the repeated
    phrase rule handles the high-confidence silence hallucination separately.
    """
    accepted = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        nsp, avg, compression = seg.get("no_speech_prob"), seg.get("avg_logprob"), seg.get("compression_ratio")
        try: nsp = float(nsp) if nsp is not None else None
        except (TypeError, ValueError): nsp = None
        try: avg = float(avg) if avg is not None else None
        except (TypeError, ValueError): avg = None
        try: compression = float(compression) if compression is not None else None
        except (TypeError, ValueError): compression = None
        if not text or _repeated_phrase_hallucination(text):
            continue
        if nsp is not None and nsp >= 0.80:
            continue
        if avg is not None and avg < -1.05:
            continue
        if compression is not None and compression > 2.45:
            continue
        accepted.append(seg)
    # MLX can emit a high-confidence familiar phrase twice in adjacent silent
    # chunks (e.g. "Dzień dobry. Dzień dobry."). Its confidence fields look
    # healthy, so remove the *loop* rather than any isolated phrase.
    remove: set[int] = set()
    for index in range(len(accepted) - 1):
        left, right = accepted[index], accepted[index + 1]
        phrase = _norm_hallucination_text(str(left.get("text", "")))
        if phrase not in _SILENCE_LOOP_PHRASES or phrase != _norm_hallucination_text(str(right.get("text", ""))):
            continue
        try:
            gap = float(right.get("start", 0)) - float(left.get("end", 0))
        except (TypeError, ValueError):
            gap = 999.0
        if -0.1 <= gap <= 3.0:
            remove.update((index, index + 1))
    return [segment for index, segment in enumerate(accepted) if index not in remove]


def _normalize_word_timings(words):
    """Guarantee editable, non-overlapping word ranges from Whisper output.

    MLX occasionally reports a word with identical start/end timestamps ("Mega"
    in the supplied clip was 97.62–97.62). Such a range cannot be edited and
    breaks subtitle rendering. Keep the detected start and introduce only a 40 ms
    minimum, pushing following boundaries forward only where needed.
    """
    clean = []
    for word in sorted(words or [], key=lambda item: float(item.get("start", 0.0))):
        text = str(word.get("word", "")).strip()
        try:
            start, end = float(word.get("start")), float(word.get("end"))
        except (TypeError, ValueError):
            continue
        if text:
            clean.append({**word, "word": text, "start": start, "end": end})
    previous_end = 0.0
    for word in clean:
        start = max(float(word["start"]), previous_end)
        end = max(float(word["end"]), start + 0.04)
        word["start"] = round(start, 3)
        word["end"] = round(end, 3)
        previous_end = end
    return clean


def transcribe_video(video_path, model, lang_code=None):
    """
    Glowna funkcja transkrypcji. Automatycznie wybiera backend:
    - Apple Silicon (M1/M2/M3/M4): mlx-whisper turbo w subprocesie (szybko, bez zjadania RAM Streamlita)
    - Inne platformy: faster-whisper medium na CPU
    Preprocessuje audio przez ffmpeg przed transkrypcja.
    """
    processed_audio = _preprocess_audio(str(video_path))
    tmp_created = processed_audio != str(video_path)

    try:
        if BACKEND == "mlx":
            transcript, words = _transcribe_mlx(processed_audio, lang_code)
        else:
            transcript, words = _transcribe_faster_whisper(processed_audio, model, lang_code)
        return transcript, _normalize_word_timings(words)
    finally:
        if tmp_created and os.path.exists(processed_audio):
            try:
                os.remove(processed_audio)
            except Exception:
                pass

def score_peak_line(text: str) -> float:
    if not text:
        return 0.0

    t = text.strip().lower()
    score = 0.0

    strong_words = [
        "szok", "sekret", "prawda", "błąd", "problem", "uwaga", "nagle",
        "najgorsze", "najlepsze", "nikt", "wszyscy", "okazało się",
        "wtedy", "ale", "jednak", "musisz", "nie rób", "to działa",
        "this", "truth", "secret", "mistake", "problem", "warning",
        "suddenly", "worst", "best", "nobody", "everyone", "turned out"
    ]

    for w in strong_words:
        if w in t:
            score += 1.5

    if "?" in text:
        score += 2.0
    if "!" in text:
        score += 2.0

    # liczby i konkrety
    if re.search(r"\b\d+\b", text):
        score += 1.5

    word_count = len(text.split())
    
    # bonus za krótkie, hookowe zdania (zmniejszony bias na prośbę)
    if 5 <= word_count <= 12:
        score += 0.5

    # preferuj zbalansowane zdania
    if 8 <= word_count <= 24:
        score += 1.5
    elif word_count > 40:
        score -= 1.5

    # bonus za kontrast / twist
    if any(x in t for x in ["ale", "jednak", "zamiast", "okazało się", "but", "however", "instead", "turned out"]):
        score += 2.0

    return score

def extract_peak_moments_from_transcript(transcript: str, top_n: int = 15):
    pattern = re.compile(r"\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]\s*(.+)")
    candidates = []

    for line in transcript.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue

        start_time = float(m.group(1))
        end_time = float(m.group(2))
        text = m.group(3).strip()

        if not text:
            continue

        score = score_peak_line(text)
        candidates.append({
            "start_time": start_time,
            "end_time": end_time,
            "text": text,
            "score": round(score, 2)
        })

    # sortuj po score malejąco
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # odfiltruj zbyt bliskie sobie momenty (co najmniej 20 sek różnicy)
    selected = []
    for c in candidates:
        too_close = any(abs(c["start_time"] - s["start_time"]) < 20 for s in selected)
        if not too_close:
            selected.append(c)
        if len(selected) >= top_n:
            break

    return selected

def analyze_with_gemini(transcript, api_key, shorts_count, min_dur, max_dur, prompt_mode="Precyzyjna", custom_prompt_text="", glossary_text=""):
    client = genai.Client(api_key=api_key)
    # Zwiększona pula powiększająca opcje do selekcji rozkładu długości
    pool_size = min(50, max(20, int(shorts_count * 3)))

    # --- Blok poprawnych nazw własnych dla Gemini ---
    _rules, _canonical = parse_whisper_glossary(glossary_text) if glossary_text.strip() else ([], [])
    if _canonical:
        _glossary_block = (
            "PROPER NAMES / BRAND SPELLING:\n"
            "Always use these exact spellings in title, hook_text, hashtags, yt_tags, and segment text. "
            "Never alter their capitalization or spelling:\n"
            + ", ".join(_canonical[:80])
            + "\n"
        )
    else:
        _glossary_block = ""
    
    peak_moments = extract_peak_moments_from_transcript(transcript, top_n=15)
    peak_hints = "\n".join(
        f'- [{m["start_time"]:.1f} - {m["end_time"]:.1f}] {m["text"]} (score: {m["score"]})'
        for m in peak_moments
    )
    
    # --- NOWA LOGIKA BUDOWANIA PROMPTU ---
    if "Własny prompt" in prompt_mode and custom_prompt_text.strip():
        # Tryb "Własny Prompt" buduje z zapytania użytkownika kompletny i bezpieczny prompt z wymogami JSON.
        prompt = f"""You are an expert short-form video editor and viral content strategist.
Analyze the ENTIRE transcript, not just the beginning.

Your task is to create up to {pool_size} strong short-video candidates for TikTok, YouTube Shorts, or Reels.

The final short must fit within {min_dur} to {max_dur} seconds total.
The total duration is the sum of all selected segments.
Prefer the strongest coherent version of a short, not the shortest possible version.

CRITICAL EDITORIAL RULES DEFINED BY USER:
{custom_prompt_text}

TIMESTAMPS & LANGUAGE RULES:
- Use exact transcript timestamps. Segments MUST be in strict chronological order. NEVER overlap segments — never start a segment before the previous one ends. If two moments are within 3 seconds of each other, merge them into one segment.
- Write title, hook_text, hashtags, yt_tags, and segment text in the exact same language as the transcript.
{_glossary_block}
Return ONLY a valid JSON array.

Format:
[
  {{
    "title": "Short title",
    "score": 92,
    "hook_text": "A short, natural, engaging social-media style description.",
    "hashtags": "#tag1 #tag2 #tag3",
    "yt_tags": "tag1, tag2, tag3",
    "segments": [
      {{
        "start_time": 12.5,
        "end_time": 20.0,
        "text": "Exact transcript text"
      }}
    ]
  }}
]

TRANSCRIPT:
{transcript}"""
             
    elif "Scenarzysta" in prompt_mode:
        prompt = f"""
    You are a master storyteller, narrative editor, and viral short-form content strategist for TikTok, Instagram Reels, and YouTube Shorts.

    Your task is to analyze the ENTIRE transcript and identify moments that carry a strong emotional or narrative arc.
    Then build the best {pool_size} potential short clips, each structured like a complete mini-story.

    IMPORTANT HINTS FROM PRE-RANKING:
    These transcript moments were automatically detected as potentially high-interest moments.
    Use them as starting points, but actively search the surrounding context for setup and payoff.

    {peak_hints}

    SEARCH STRATEGY:
    - Do NOT focus only on the beginning of the transcript.
    - Search the ENTIRE transcript: beginning, middle, and end.
    - The best story moments often require setup BEFORE the peak and resolution AFTER it.
    - Prefer selecting moments that span different timestamps and tell a complete story.

    CORE PHILOSOPHY — THE 3-BEAT STRUCTURE:
    Every clip must feel like it has three beats:
    1. HOOK / TENSION: Something that immediately creates curiosity, conflict, surprise, or emotional investment.
       The viewer must feel "I need to keep watching" within the first 2-3 seconds.
    2. DEVELOPMENT: The speaker unpacks the idea, reveals the conflict, shares the story, or builds the argument.
       This is where the viewer gets pulled into the narrative.
    3. PAYOFF / RESOLUTION: A strong closing moment — a punchline, a revelation, a clear conclusion, an emotional peak, or a memorable quote.
       The viewer must feel satisfied or deeply affected by the end.

    CRITICAL EDITORIAL RULES:

    1. COLD OPEN — START AT THE BEST MOMENT:
    Never start a clip with a generic introduction or filler.
    Start precisely at the moment where a viewer would instantly feel curiosity or tension.
    Examples: a provocative statement, an unexpected reveal, a direct opinion, a cliffhanger question.

    2. EMOTIONAL ARC OVER INFORMATION DENSITY:
    Prefer clips where the speaker's emotion changes during the clip — from doubt to certainty, from calm to anger, from setup to punchline.
    A clip that moves the viewer emotionally is stronger than a clip that is merely informative.

    3. CONVERSATION DYNAMICS (especially in podcasts and interviews):
    In interview or podcast content, look for:
    - Moments of genuine disagreement, pushback, or debate
    - Unexpected admissions, confessions, or revelations
    - Moments where the speaker contradicts themselves or changes their mind
    - Strong laughter, surprise, or emotional reaction
    These are natural story beats and perform exceptionally well.

    4. MULTI-SEGMENT ASSEMBLY — NARRATIVE CONTINUITY:
    You MAY combine multiple non-contiguous segments from different parts of the transcript.
    BUT: every combined segment must feel like it was always supposed to follow the previous one.
    Never stitch segments that create a logical gap, a confusing jump, or an emotional mismatch.
    The assembled clip must sound like one continuous, intentional piece of speech.

    5. COMPLETE THOUGHTS — NO BROKEN SENTENCES:
    Never start or end a clip in the middle of a sentence.
    If a speaker starts building toward a point, let them finish.

    6. CLIP VARIETY — NO DUPLICATE STORIES:
    Return clips covering different moments, topics, and emotional tones.
    Do not return multiple clips about the same story or idea.
    Vary the emotional tone: mix clips that are funny, clips that are serious, clips that are shocking.

    7. HOOK_TEXT RULE:
    The "hook_text" must NOT be a quote from the video.
    Write it like a professional creator's description: short, catchy, emotionally charged, and grammatically correct.
    Use 1-3 sentences.

    8. STANDALONE OPENING:
    The first spoken sentence of the clip must be fully understandable without needing any earlier context from the video.

    DURATION RULES:
    - Target duration is between {min_dur} and {max_dur} seconds.
    - Do not force the shortest possible clip. A complete 3-beat story takes the time it needs.
    - Return a balanced mix of clip durations: some near the lower bound, some medium, some near the upper bound.
    - NEVER return a clip shorter than {min_dur - 2} seconds.
    - Calculate total duration as the SUM of all selected segment durations: total_duration = Σ(end_time - start_time)

    TIMESTAMP RULES:
    - Use exact transcript timestamps. Never invent timestamps.
    - Every segment must have exact start_time and end_time.
    - Segments inside one clip MUST be ordered strictly chronologically. NEVER generate a segment that starts before or during a previous segment — this causes broken video.
    - If two moments are close in time (less than 3 seconds apart), combine them into ONE longer segment instead of two short ones.

    SCORING RULES:
    Assign a strict score from 0 to 100 based on: strength of the 3-beat arc, emotional impact, hook quality, payoff clarity, and narrative flow.

    LANGUAGE RULE:
    Write title, hook_text, hashtags, yt_tags, and segment text in the exact same language as the transcript.
    {_glossary_block}
    OUTPUT REQUIREMENTS:
    Return ONLY a valid JSON array of objects. No markdown. No commentary.

    Return format:
    [
      {{
        "title": "Short viral headline",
        "score": 96,
        "hook_text": "An engaging, natural-sounding social media description (1-3 sentences). Not a quote. Write like a real human creator.",
        "hashtags": "#tag1 #tag2 #tag3",
        "yt_tags": "tag1, tag2, tag3",
        "segments": [
          {{
            "start_time": 12.5,
            "end_time": 20.0,
            "text": "Exact transcript text for this segment"
          }}
        ]
      }}
    ]

    TRANSCRIPT:
    {transcript}
    """

    elif "Luźniejsza" in prompt_mode:
        prompt = f"""
        You are an expert short-form video editor and viral content strategist.

        Analyze the ENTIRE transcript, not just the beginning.

        Your task is to create up to {pool_size} strong short-video candidates for TikTok, YouTube Shorts, or Reels.

        Each short must follow these rules:

        1. ONE CLEAR TOPIC ONLY
        Each short must stay on one main topic, one idea, one story, or one emotional thread.
        Do not jump between unrelated topics inside the same short.

        2. COHERENT EDITING
        A short may contain one or many separate segments taken from different parts of the transcript,
        but all selected segments must belong together naturally and feel like one coherent edit.
        It should feel like a real editor intentionally built the short around one idea.

        3. FULL FILM AWARENESS
        Search across the whole transcript: beginning, middle, and end.
        Do not focus mainly on the start of the video.

        4. DURATION
        The final short must fit within {min_dur} to {max_dur} seconds total.
        The total duration is the sum of all selected segments.
        Prefer the strongest coherent version of a short, not the shortest possible version.
        Do not force the shortest possible short.
        If a longer version is more coherent, stronger, and still fits inside the allowed duration, prefer the better version.

        5. NATURAL FLOW
        Do not cut thoughts in a way that feels broken, random, or confusing.
        Whenever possible, keep complete ideas, complete sentences, and a clear flow from hook to payoff.

        6. VIRAL JUDGMENT
        Choose the version with the strongest viral potential:
        - curiosity
        - emotion
        - strong payoff
        - tension
        - surprise
        - humor
        - strong opinion
        - useful insight
        - memorable story moment

        If the video does not contain obvious viral moments, use editorial judgment and choose the most engaging, clear, and watchable moments.

        7. EDITOR MINDSET
        Think like a skilled human editor making shorts from a long video.
        The result must make sense, feel intentional, and be satisfying to watch.

        8. TIMESTAMPS
        Use exact transcript timestamps.
        Segments MUST be in strict chronological order — never overlap. If two segments are within 3 seconds of each other, merge into one.
        Do not invent timestamps.

        9. LANGUAGE
        Write title, hook_text, hashtags, yt_tags, and segment text in the same language as the transcript.
        {_glossary_block}
        Return ONLY a valid JSON array.

        Format:
        [
          {{
            "title": "Short title",
            "score": 92,
            "hook_text": "A short, natural, engaging social-media style description.",
            "hashtags": "#tag1 #tag2 #tag3",
            "yt_tags": "tag1, tag2, tag3",
            "segments": [
              {{
                "start_time": 12.5,
                "end_time": 20.0,
                "text": "Exact transcript text"
              }}
            ]
          }}
        ]

        TRANSCRIPT:
        {transcript}
        """
    else:
        prompt = f"""
    You are a master documentary video editor, story producer, and viral short-form content strategist for TikTok, Instagram Reels, and YouTube Shorts.

    Your task is to analyze the ENTIRE provided transcript.
    First identify the most interesting moments across the entire transcript.
    Then build the best {pool_size} potential highly viral clips around those moments.
    Prefer clips where the peak moment happens early in the clip, but allow a short setup if needed for context.

    IMPORTANT HINTS FROM PRE-RANKING:
    These transcript moments were automatically detected as potentially high-interest / high-retention moments.
    Use them as strong hints, but do not blindly follow them if the broader context suggests a better clip.

    {peak_hints}

    SEARCH STRATEGY:
    - Do NOT focus only on the beginning of the transcript.
    - Actively search the ENTIRE transcript: beginning, middle, and end.
    - The best clips are often located in the middle or later parts of the video.
    - Prefer selecting moments that occur at different timestamps across the transcript.

    IMPORTANT: You are NOT limited to selecting only one continuous moment from the transcript.
    You MAY combine multiple non-contiguous segments from different parts of the transcript if they belong to the same topic and together create one coherent, engaging, logical clip.

    CRITICAL EDITORIAL RULES:

    1. SCROLL-STOPPING MOMENTS:
    Prefer moments where a viewer would stop scrolling immediately after hearing the first sentence.
    Examples: shocking facts, strong opinions, unexpected historical facts, emotional reactions, mystery revelations.

    2. PEAK MOMENT STRATEGY:
    Prefer clips where the key moment happens early in the clip (within the first 3-5 seconds).
    If possible, start slightly before the peak moment so the viewer understands the context quickly.

    3. VISUAL CURIOSITY:
    Prefer segments where the spoken content likely matches something visually interesting in the video 
    (e.g. demonstrations, locations, reactions, discoveries, surprising visuals).

    4. VIRAL POTENTIAL & INFORMATION DENSITY:
    Prefer moments with controversy, humor, conflict, fear, useful advice, or strong payoff.
    Prefer segments where the speaker delivers a clear insight in a short amount of time.
    High information density is preferred over long explanations.
    Avoid: filler, repetitive explanation, weak context, low-energy speech, generic introductions.

    5. COMPLETE THOUGHTS ONLY:
    Never start or end a clip in the middle of a sentence.
    If the speaker starts an idea, argument, or explanation, allow it to finish naturally.

    6. MINI-STORY STRUCTURE & NATURAL FLOW:
    Each selected clip should feel like a complete short story with a hook, context, development, and payoff.
    The final chosen segments must read like natural spoken language. Do not create a clip that feels stitched together randomly.

    7. ENGAGING DESCRIPTION (HOOK_TEXT):
    The "hook_text" MUST NOT be a random quote from the video.
    It must be a professionally written, catchy, human-like summary of the clip's content.
    Write it exactly as a human YouTuber or TikToker would describe their own video in the description box. Make it sound natural, engaging, logically structured, and grammatically correct. Use 1-3 full sentences.

    8. CLIP VARIETY RULE:
    Return clips covering different topics or moments.
    Avoid returning multiple clips that are about the same idea.

    9. CLIP OPENING RULE:
    The first spoken sentence of the clip should be understandable without needing earlier context from the video.

    DURATION RULES (STRICT & BALANCED):
    - Target duration is between {min_dur} and {max_dur} seconds.
    - Across the full batch, return a balanced mix of clip durations whenever the transcript allows it.
    - Prefer: some clips near the lower bound, some medium-length clips, and some longer clips closer to the upper bound.
    - Do not return all clips clustered near the minimum duration unless the transcript truly does not support longer coherent stories.
    - If the selected peak moment is short, you MUST add surrounding context to reach AT LEAST {min_dur} seconds.
    - Do not be afraid to make clips longer (closer to {max_dur} seconds) if it helps build a better story.
    - NEVER return a clip shorter than {min_dur - 2} seconds.
    - Calculate total duration as the SUM of all selected segment durations: total_duration = Σ(end_time - start_time)

    TIMESTAMP RULES:
    - Use exact transcript timestamps.
    - Every segment must have exact start_time and end_time.
    - Segments inside one clip MUST be ordered strictly chronologically. NEVER generate a segment that starts before or during a previous segment — this causes broken video.
    - If two moments are close in time (less than 3 seconds apart), combine them into ONE longer segment instead of two short ones.
    - Do not create fake timestamps.

    SCORING RULES:
    Assign a strict virality score from 0 to 100 based on hook strength, emotional impact, curiosity, clarity, and shareability.

    LANGUAGE RULE:
    Write title, hook_text, hashtags, yt_tags, and segment text in the exact same language as the transcript.
    {_glossary_block}
    OUTPUT REQUIREMENTS:
    Return ONLY a valid JSON array of objects. No markdown. No commentary.
    
    Return format:
    [
      {{
        "title": "Short viral headline",
        "score": 96,
        "hook_text": "An engaging, natural-sounding social media description (1-3 sentences) summarizing the clip. Not a direct quote! Write it like a real human creator.",
        "hashtags": "#tag1 #tag2 #tag3",
        "yt_tags": "tag1, tag2, tag3",
        "segments": [
          {{
            "start_time": 12.5,
            "end_time": 20.0,
            "text": "Exact transcript text for this segment"
          }}
        ]
      }}
    ]

    TRANSCRIPT:
    {transcript}
    """

    max_retries = 3
    results = []
    last_error = ""
    
    # Zaktualizowany ścisły schemat JSON z wymogiem 'required' we wszystkich węzłach
    json_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "required": ["title", "score", "hook_text", "hashtags", "yt_tags", "segments"],
            "properties": {
                "title": {"type": "STRING"},
                "score": {"type": "INTEGER"},
                "hook_text": {"type": "STRING"},
                "hashtags": {"type": "STRING"},
                "yt_tags": {"type": "STRING"},
                "segments": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "required": ["start_time", "end_time", "text"],
                        "properties": {
                            "start_time": {"type": "NUMBER"},
                            "end_time": {"type": "NUMBER"},
                            "text": {"type": "STRING"}
                        }
                    }
                }
            }
        }
    }
    
    for attempt in range(max_retries):
	        try:
	            response = client.models.generate_content(
	                model='gemini-2.5-flash',
	                contents=prompt,
	                config=types.GenerateContentConfig(
	                    response_mime_type="application/json",
	                    response_schema=json_schema,
	                    temperature=0.2 + (attempt * 0.05),
	                    max_output_tokens=65536,
	                )
	            )
	            
	            results = _load_json_array(response.text)
	            if isinstance(results, list): break
	            
	        except Exception as e:
	            last_error = str(e)
	            print(f"Próba {attempt + 1}/{max_retries} - Błąd AI (JSON): {e}")
	            try:
	                raw_text = response.text if "response" in locals() and response is not None else ""
	                if raw_text:
	                    repaired = _repair_json_array_with_gemini(client, raw_text, json_schema)
	                    if isinstance(repaired, list) and repaired:
	                        results = repaired
	                        print(f"Naprawiono odpowiedź AI po błędzie JSON. Odzyskano {len(results)} shortów.")
	                        break
	            except Exception as repair_err:
	                print(f"Naprawa JSON przez AI nie powiodła się: {repair_err}")
	                recovered = _salvage_complete_json_objects(raw_text) if raw_text else []
	                if recovered and attempt == max_retries - 1:
	                    results = recovered
	                    print(f"Odzyskano {len(results)} kompletnych shortów z częściowej odpowiedzi AI.")
	                    break
	            if attempt == max_retries - 1:
	                raise Exception(f"Sztuczna Inteligencja zwróciła uszkodzony format danych po {max_retries} próbach. (Błąd: {last_error}). Spróbuj wygenerować klipy ponownie.")

    validated = []
    for sh in results:
        if not isinstance(sh, dict): continue
        
        if 'title' not in sh: sh['title'] = sh.get('headline', 'Viral Short')
        if 'hook_text' not in sh: sh['hook_text'] = sh.get('hook', '')
        
        seg_list = sh.get("segments", [])
        
        try:
            seg_list = sorted(seg_list, key=lambda x: float(x.get("start_time", 0)))
        except Exception:
            pass

        # KROK 1: Scal nakladajace sie segmenty i segmenty blisko siebie (< 2s przerwy)
        # Poprzedni kod przesuwał start o 0.1s zamiast scalać — powodowało to
        # mnogie małe cięcia zamiast jednego długiego bloku.
        MERGE_GAP = 2.0  # segmenty bliżej niż 2s od siebie → scal w jeden
        merged_segs = []
        for s in seg_list:
            try:
                st_time = float(s.get("start_time", 0))
                en_time = float(s.get("end_time", 0))
                if en_time <= st_time:
                    continue
                text_val = str(s.get("text", ""))

                if merged_segs:
                    last = merged_segs[-1]
                    gap = st_time - last["end_time"]
                    if gap <= MERGE_GAP:
                        # Scal: rozszerz koniec poprzedniego segmentu
                        last["end_time"] = max(last["end_time"], en_time)
                        if text_val and text_val not in last["text"]:
                            last["text"] += " " + text_val
                        continue

                merged_segs.append({"start_time": st_time, "end_time": en_time, "text": text_val})
            except Exception:
                continue

        # KROK 2: Policz łączny czas i zbuduj listę valid_segs
        total = 0.0
        valid_segs = []
        for s in merged_segs:
            dur = s["end_time"] - s["start_time"]
            if dur < 0.5:
                continue
            total += dur
            valid_segs.append(s)

        if not valid_segs: continue
        
        # Ostrzejsza walidacja (maksymalnie 3 sekundy poniżej minimum, góra mocno elastyczna)
        if total < (min_dur - 3) or total > (max_dur + 15):
            continue 

        sh["segments"] = valid_segs
        sh["total_duration_seconds"] = round(total, 2)
        validated.append(sh)
        
    # --- RE-RANKING: Rozkład długości wg wskazówek ChatGPT ---
    if not validated:
        return []
        
    validated.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    if len(validated) <= shorts_count:
        return validated
        
    # Tworzymy idealne cele długości dla zróżnicowania (np. dla 3 shortów: blisko min_dur, pośrodku, blisko max_dur)
    target_durations = [min_dur + (max_dur - min_dur) * i / max(1, shorts_count - 1) for i in range(shorts_count)]
    
    final_selection = []
    used_indices = set()
    
    # Dla każdego celu (wiaderka czasowego) wybieramy najlepiej dopasowany (pod kątem wyniku i długości) klip
    for target_dur in target_durations:
        best_clip_idx = -1
        best_clip_score = -float('inf')
        
        for i, clip in enumerate(validated):
            if i in used_indices: 
                continue
                
            clip_dur = clip.get("total_duration_seconds", 0)
            duration_diff = abs(clip_dur - target_dur)
            
            # Wzór: oryginalny score AI minus kara za odchylenie od targetowej długości.
            # Dzięki temu wciąż faworyzujemy viralowe klipy, ale wyciągamy też długie, jeśli brakuje nam ich w puli.
            combined_score = clip.get("score", 0) - (duration_diff * 0.8)
            
            if combined_score > best_clip_score:
                best_clip_score = combined_score
                best_clip_idx = i
                
        if best_clip_idx != -1:
            final_selection.append(validated[best_clip_idx])
            used_indices.add(best_clip_idx)
            
    # Na koniec sortujemy wynikowe shorty po jakości, żeby wyświetlić najlepsze u góry
    final_selection.sort(key=lambda x: x.get("score", 0), reverse=True)
    return final_selection

def snap_segments_to_words(segments, global_words, start_buffer=0.05, end_buffer=0.1):
    """
    Precyzyjne przyciecie segmentow do granic slow przez DOPASOWANIE TEKSTU.
    
    Poprzednie podejscie (szukanie po timestamp) bylo zawodne — Gemini zwraca
    przyblizone timestampy z transkrypcji, ktore moga byc odchylone o 0.5-2s od
    rzeczywistych granic slow w global_words. Powodowalo to lapanie zlych slow.
    
    Nowe podejscie: szukamy PIERWSZEGO i OSTATNIEGO slowa segmentu po TEKSCIE,
    nie po timestampie. Tekst segmentu pochodzi z tej samej transkrypcji Whisper,
    wiec slowa musza istniec w global_words — wystarczy je znalezc.
    """
    if not global_words:
        return segments

    import re as _re

    def normalize(text):
        """Usuwa interpunkcje i zamienia na lowercase dla porownan."""
        return _re.sub(r'[^\w]', '', text, flags=_re.UNICODE).lower()

    sorted_words = sorted(global_words, key=lambda w: w["start"])

    for seg in segments:
        target_start = seg["start_time"]
        target_end = seg["end_time"]
        seg_text = seg.get("text", "").strip()

        # Pierwsze i ostatnie znaczace slowa z tekstu segmentu (pomijamy 1-znakowe)
        text_words = [normalize(w) for w in seg_text.split()]
        text_words = [w for w in text_words if len(w) > 1]
        if not text_words:
            continue

        # Okno szukania — szerokie (5s) bo Gemini moze byc niedokladny o 1-2s
        WINDOW = 5.0
        nearby = [w for w in sorted_words
                  if w["start"] >= target_start - WINDOW
                  and w["start"] <= target_end + WINDOW]

        if not nearby:
            continue

        # --- SNAP START: znajdz pierwsze slowo segmentu po tekscie ---
        first_word = text_words[0]
        start_candidates = []
        for gw in nearby:
            gw_norm = normalize(gw["word"])
            if gw_norm == first_word:
                # Preferuj dopasowania blizej target_start, lekko karaj bardzo wczesne
                dist = abs(gw["start"] - target_start)
                early_penalty = max(0.0, (target_start - 1.5) - gw["start"]) * 2
                start_candidates.append((dist + early_penalty, gw["start"], gw))

        if start_candidates:
            start_candidates.sort()
            best_start = start_candidates[0][2]
            seg["start_time"] = max(0.0, best_start["start"] - start_buffer)
        else:
            # Fallback: pierwsze slowo AT lub PO target_start - 0.5s
            fallback = [w for w in nearby if w["start"] >= target_start - 0.5]
            if fallback:
                seg["start_time"] = max(0.0, fallback[0]["start"] - start_buffer)

        # --- SNAP END: znajdz ostatnie slowo segmentu po tekscie ---
        last_word = text_words[-1]
        end_candidates = []
        for gw in nearby:
            if gw["start"] < seg["start_time"]:
                continue
            gw_norm = normalize(gw["word"])
            if gw_norm == last_word:
                dist = abs(gw["end"] - target_end)
                end_candidates.append((dist, gw["end"], gw))

        if end_candidates:
            end_candidates.sort()
            best_end = end_candidates[0][2]
            seg["end_time"] = best_end["end"] + end_buffer
        else:
            # Fallback: ostatnie slowo konczace sie przed target_end + 0.3s
            fallback = [w for w in nearby
                        if w["start"] >= seg["start_time"]
                        and w["end"] <= target_end + 0.3]
            if fallback:
                seg["end_time"] = fallback[-1]["end"] + end_buffer

    return segments


def optimize_segments(segments, global_words=None):
    """
    Optymalizuje segmenty: najpierw scala nakladajace sie, potem przykleja
    do dokladnych granic slow z Whispera (jezeli global_words dostepne).

    BACKWARD COMPATIBLE: jesli pipeline.py przekaze stary float (padding_sec),
    ignorujemy go i uzywamy word snapping. Dziala poprawnie nawet gdy tylko
    ai_processor.py zostal zaktualizowany bez pipeline.py.
    """
    # Stara sygnatura: optimize_segments(segments, 0.5) — padding_sec jako float
    # Nowa sygnatura: optimize_segments(segments, global_words) — lista slow
    if isinstance(global_words, (int, float)):
        global_words = None  # ignoruj stary padding, snap_to_words zrobi lepiej

    if not segments:
        return segments

    sorted_segments = sorted(segments, key=lambda x: x.get("start_time", 0.0))
    merged = []
    for seg in sorted_segments:
        if "text" not in seg or seg["text"] is None:
            seg["text"] = ""
        st_val = float(seg.get("start_time", 0.0) or 0.0)
        en_val = float(seg.get("end_time", 0.0) or 0.0)
        seg["start_time"] = max(0.0, st_val)
        seg["end_time"] = en_val

        if not merged:
            merged.append(seg)
        else:
            if seg["start_time"] <= merged[-1]["end_time"]:
                merged[-1]["end_time"] = max(merged[-1]["end_time"], seg["end_time"])
                merged[-1]["text"] += " [...] " + seg.get("text", "")
            else:
                merged.append(seg)

    # Snap do granic slow — glowna naprawa precyzji ciec
    if global_words:
        merged = snap_segments_to_words(merged, global_words)

    return merged

def update_segments_and_resync_words(short, new_segments, global_words):
    """
    Funkcja PRO Edytora Scen. Scala nakładające się na siebie modyfikacje, 
    aktualizuje ramy czasowe wideo i ponownie zaciąga tekst z Whisper'a, 
    aby idealnie zsynchronizować napisy po zmianie cięcia wideo!
    """
    # 1. Sortujemy po czasie i chronimy przed nakładaniem się segmentów wideo (Overlap)
    sorted_segs = sorted(new_segments, key=lambda x: float(x.get('start_time', 0.0)))
    merged_segs = []
    
    for seg in sorted_segs:
        if not merged_segs:
            merged_segs.append(seg)
        else:
            last_seg = merged_segs[-1]
            if seg['start_time'] <= last_seg['end_time']:
                last_seg['end_time'] = max(last_seg['end_time'], seg['end_time'])
            else:
                merged_segs.append(seg)

    # 1b. Snap do granic slow — zapewnia precyzje ciec rowniez po edycji przez uzytkownika
    if global_words:
        merged_segs = snap_segments_to_words(merged_segs, global_words)
                
    # Tworzymy mapę dotychczasowych edycji słów przez użytkownika (po stemplach czasowych)
    user_edited_words = {}
    for w in short.get('words', []):
        user_edited_words[f"{w['start']}_{w['end']}"] = w.get('word', '')

    # 2. Odbudowujemy precyzyjny słownik (napisy do wideo) bazując na nowych ramach!
    short_words_dict = {}
    for seg in merged_segs:
        s_w = [copy.deepcopy(w) for w in global_words if w['start'] >= seg.get('start_time', 0.0) - 0.5 and w['end'] <= seg.get('end_time', 0.0) + 0.5]
        for w in s_w:
            key = f"{w['start']}_{w['end']}"
            if key in user_edited_words:
                w['word'] = user_edited_words[key]
            
            # Kluczem dict słownika staje się timestamp z uwzględnieniem oryginalnego słowa, żeby nie deduplikować identycznych słów z tym samym timestampem (co przy bezpiecznym copy jest łatwiejsze)
            short_words_dict[f"{w['start']}_{w['end']}_{hash(w['word'])}"] = w
            
    short['words'] = sorted(list(short_words_dict.values()), key=lambda x: x.get('start', 0.0))

    # 3. Przypisujemy na nowo ogólny tekst do segmentów korzystając z zachowanych słów!
    for seg in merged_segs:
        s_w_texts = [w['word'] for w in short['words'] if w['start'] >= seg['start_time'] - 0.5 and w['end'] <= seg['end_time'] + 0.5]
        seg['text'] = " ".join(s_w_texts)
        
    short['segments'] = merged_segs
    
    # 4. Bezpiecznik dla tłumaczeń (AI). Jeśli short był tłumaczony, musimy usunąć stary stan
    if 'original_words' in short:
        del short['original_words']
    if 'original_segments' in short:
        del short['original_segments']
        
    return short

def initialize_short_words(short, global_words):
    if "words" not in short:
        short_words_dict = {}
        for seg in short.get('segments', []):
            s_w = [w for w in global_words if w['start'] >= seg.get('start_time', 0.0) - 0.5 and w['end'] <= seg.get('end_time', 0.0) + 0.5]
            for w in s_w:
                short_words_dict[f"{w['start']}_{w['end']}_{w['word']}"] = w
        short['words'] = sorted(list(short_words_dict.values()), key=lambda x: x.get('start', 0.0))

def estimate_translated_word_timings(segments):
    words_out = []
    for seg in segments:
        text_words = str(seg.get("text", "")).strip().split()
        if not text_words:
            continue
        seg_start = float(seg.get("start_time", 0.0))
        seg_end = float(seg.get("end_time", seg_start + 0.1))
        seg_duration = max(0.1, seg_end - seg_start)
        speech_start = seg_start + min(0.05, seg_duration * 0.08)
        speech_end = seg_end - min(0.04, seg_duration * 0.06)
        speech_duration = max(0.08, speech_end - speech_start)

        weights = []
        for idx, word in enumerate(text_words):
            clean = re.sub(r"[^\wÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż]+", "", word, flags=re.UNICODE)
            weight = max(0.55, len(clean) ** 0.72) if clean else 0.45
            if re.search(r"\d", clean):
                weight += 0.35
            if re.search(r"[,;:]$", word):
                weight += 0.35
            if re.search(r"[.!?]$", word) and idx < len(text_words) - 1:
                weight += 0.55
            weights.append(weight)

        total_weight = sum(weights) or 1.0
        current = speech_start
        for word, weight in zip(text_words, weights):
            end = min(speech_end, current + max(0.035, speech_duration * (weight / total_weight)))
            words_out.append({"word": word, "start": round(current, 3), "end": round(end, 3)})
            current = end
    return words_out

def localize_measurement_units_for_tts(text, target_language):
    """
    Expands compact technical units after numbers so subtitles and TTS read them naturally.
    Example for English: "300 A" -> "300 amps", "150Ah" -> "150 amp-hours".
    """
    if not text:
        return text

    lang = str(target_language or "").lower()
    unit_words = {
        "angielski": {
            "a": "amps", "amp": "amps", "amps": "amps",
            "ah": "amp-hours", "mah": "milliamp-hours",
            "v": "volts", "mv": "millivolts",
            "w": "watts", "kw": "kilowatts", "mw": "megawatts",
            "wh": "watt-hours", "kwh": "kilowatt-hours",
            "hz": "hertz", "khz": "kilohertz", "mhz": "megahertz", "ghz": "gigahertz",
            "ohm": "ohms", "Ω": "ohms",
            "°c": "degrees Celsius", "c": "degrees Celsius",
        },
        "niemiecki": {
            "a": "Ampere", "amp": "Ampere", "amps": "Ampere",
            "ah": "Amperestunden", "mah": "Milliamperestunden",
            "v": "Volt", "mv": "Millivolt",
            "w": "Watt", "kw": "Kilowatt", "mw": "Megawatt",
            "wh": "Wattstunden", "kwh": "Kilowattstunden",
            "hz": "Hertz", "khz": "Kilohertz", "mhz": "Megahertz", "ghz": "Gigahertz",
        },
        "francuski": {
            "a": "ampères", "amp": "ampères", "amps": "ampères",
            "ah": "ampères-heures", "mah": "milliampères-heures",
            "v": "volts", "mv": "millivolts",
            "w": "watts", "kw": "kilowatts", "mw": "mégawatts",
            "wh": "wattheures", "kwh": "kilowattheures",
            "hz": "hertz", "khz": "kilohertz", "mhz": "mégahertz", "ghz": "gigahertz",
        },
        "hiszpański": {
            "a": "amperios", "amp": "amperios", "amps": "amperios",
            "ah": "amperio-horas", "mah": "miliamperio-horas",
            "v": "voltios", "mv": "milivoltios",
            "w": "vatios", "kw": "kilovatios", "mw": "megavatios",
            "wh": "vatio-horas", "kwh": "kilovatio-horas",
            "hz": "hercios", "khz": "kilohercios", "mhz": "megahercios", "ghz": "gigahercios",
        },
    }
    mapping = unit_words.get(lang)
    if not mapping:
        return text

    # Normalize forms sometimes produced by translators/LLMs: "200 m V" or
    # "200 m. V" still means millivolts, not meters + volts.
    text = re.sub(r"(?P<num>\b\d+(?:[.,]\d+)?)\s*m\.?\s+V\b", r"\g<num> mV", text, flags=re.IGNORECASE)
    text = re.sub(r"(?P<num>\b\d+(?:[.,]\d+)?)\s*m\.?\s+Ah\b", r"\g<num> mAh", text, flags=re.IGNORECASE)

    # Decimal comma before a technical unit sounds better as a decimal point in English TTS.
    if lang == "angielski":
        text = re.sub(r"(?<=\d),(?=\d+\s*(?:mAh|Ah|A|V|mV|W|kW|MW|Wh|kWh|Hz|kHz|MHz|GHz|Ω|ohm|°C)\b)", ".", text, flags=re.IGNORECASE)

    units = sorted(mapping.keys(), key=len, reverse=True)
    unit_pattern = "|".join(re.escape(u) for u in units)
    pattern = re.compile(rf"(?P<num>\b\d+(?:[.,]\d+)?)\s*(?P<unit>{unit_pattern})\b", re.IGNORECASE)

    def repl(match):
        unit_raw = match.group("unit")
        unit_key = unit_raw.lower()
        spoken = mapping.get(unit_key, unit_raw)
        return f"{match.group('num')} {spoken}"

    return pattern.sub(repl, text)

def preserve_milli_units_from_source(source_text, translated_text, target_language):
    """
    Prevents translation from accidentally changing milli-units into base units,
    e.g. Polish/source "200mV" becoming English "200 volts".
    """
    if not source_text or not translated_text:
        return translated_text

    lang = str(target_language or "").lower()
    base_unit_words = {
        "angielski": {"mv": "millivolts", "mah": "milliamp-hours"},
        "niemiecki": {"mv": "Millivolt", "mah": "Milliamperestunden"},
        "francuski": {"mv": "millivolts", "mah": "milliampères-heures"},
        "hiszpański": {"mv": "milivoltios", "mah": "miliamperio-horas"},
    }
    mapping = base_unit_words.get(lang)
    if not mapping:
        return translated_text

    protected_units = {
        "mv": {
            "source": r"m\s*V",
            "wrong": r"(?:V|volt|volts|Volt|voltios|volts?)",
        },
        "mah": {
            "source": r"m\s*Ah",
            "wrong": r"(?:Ah|amp-hours|ampere-hours|Amperestunden|ampères-heures|amperio-horas)",
        },
    }

    fixed = translated_text
    for unit_key, spec in protected_units.items():
        if unit_key not in mapping:
            continue
        for match in re.finditer(rf"\b(?P<num>\d+(?:[.,]\d+)?)\s*{spec['source']}\b", source_text, flags=re.IGNORECASE):
            number = re.escape(match.group("num"))
            wrong_pattern = re.compile(rf"\b({number})\s*{spec['wrong']}\b", re.IGNORECASE)
            fixed = wrong_pattern.sub(rf"\1 {mapping[unit_key]}", fixed)
    return fixed

def _important_source_tokens(text):
    tokens = set()
    for token in re.findall(r"\b[\wÀ-žĄąĆćĘęŁłŃńÓóŚśŹźŻż.-]{2,}\b", str(text), flags=re.UNICODE):
        clean = token.strip(".,;:!?()[]{}\"'").strip()
        if not clean:
            continue
        if re.search(r"\d", clean) or re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{2,}", clean):
            tokens.add(clean.lower())
            continue
        if clean[:1].isupper() and len(clean) >= 3:
            tokens.add(clean.lower())
    return tokens

def _translation_preserves_required_tokens(source_text, translated_text):
    required = _important_source_tokens(source_text)
    if not required:
        return True
    translated = str(translated_text or "").lower()
    missing = []
    for token in required:
        # Names/acronyms like TVN, Qwen, YouTube and numbers should survive localization.
        if token not in translated:
            missing.append(token)
    return len(missing) <= max(0, len(required) // 3)

def _translation_length_is_plausible(source_text, translated_text):
    source_words = str(source_text or "").split()
    translated_words = str(translated_text or "").split()
    if len(source_words) < 4:
        return bool(translated_words)
    return len(translated_words) >= max(2, int(len(source_words) * 0.45))

def translate_short_with_gemini(short, target_language, api_key):
    client = genai.Client(api_key=api_key)
    original_words = short.get('words', [])
    if not original_words: return False
    
    if 'original_words' not in short:
        short['original_words'] = copy.deepcopy(original_words)
        short['original_segments'] = copy.deepcopy(short.get('segments', []))
        short['original_title'] = short.get('title', '')
        short['original_hook_text'] = short.get('hook_text', '')
        short['original_hashtags'] = short.get('hashtags', '')
        short['original_yt_tags'] = short.get('yt_tags', '')
    
    orig_segs = short.get('original_segments', short.get('segments', []))
    segments_texts = [seg.get('text', '') for seg in orig_segs]
    
    prompt = f"""
    You are an expert video subtitle translator and localizer.
    Translate the following video metadata and subtitle segments into {target_language}.
    The translation MUST flow naturally, adapting idioms and context, but it MUST NOT summarize, shorten, omit facts, or drop named entities.
    Preserve every important name, brand, acronym, place, number, and factual reference from each source segment.
    If a source segment mentions TVN, YouTube, a badge, a person/place, or any acronym, the translated segment must mention it too.
    Keep the translated_segments array one-to-one: each item translates only the matching source segment, in the same order.

    TECHNICAL UNITS / TTS RULES:
    - This translation may be used for subtitles AND text-to-speech dubbing.
    - Expand compact measurement units after numbers into spoken words in {target_language}.
    - For English examples: "300 A" -> "300 amps", "12 V" -> "12 volts", "200 mV" -> "200 millivolts", "150 Ah" -> "150 amp-hours", "2 kW" -> "2 kilowatts", "1.5 kWh" -> "1.5 kilowatt-hours".
    - Do NOT leave forms like "300 A", because TTS may read "A" as the letter "ay".
    - Never change milli-units into base units: "mV" is millivolts, NOT volts; "mAh" is milliamp-hours, NOT amp-hours.
    - Preserve technical meaning and numbers exactly.

    Original Title: "{short.get('original_title', short.get('title', ''))}"
    Original Hook: "{short.get('original_hook_text', short.get('hook_text', ''))}"
    Original Hashtags: "{short.get('original_hashtags', short.get('hashtags', ''))}"
    Original YT Tags: "{short.get('original_yt_tags', short.get('yt_tags', ''))}"
    
    Subtitle Segments to translate (array of strings):
    {json.dumps(segments_texts, ensure_ascii=False)}
    
    Return ONLY a valid JSON object. 
    IMPORTANT: The "translated_segments" array MUST have exactly {len(segments_texts)} items, matching the input segments one-to-one in the exact same order!
    Do not merge neighboring segments. Do not delete details to make dubbing shorter.
    """
    
    json_schema = {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "hook_text": {"type": "STRING"},
            "hashtags": {"type": "STRING"},
            "yt_tags": {"type": "STRING"},
            "translated_segments": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        }
    }
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=json_schema,
                    temperature=0.3 + (attempt * 0.1)
                )
            )
            result = json.loads(response.text)
            
            if "translated_segments" in result and len(result["translated_segments"]) == len(segments_texts):
                if any(
                    not _translation_length_is_plausible(src, dst) or not _translation_preserves_required_tokens(src, dst)
                    for src, dst in zip(segments_texts, result["translated_segments"])
                ):
                    raise ValueError("Tłumaczenie pominęło ważne słowa lub zbyt mocno skróciło segment.")
                translated_title = preserve_milli_units_from_source(
                    short.get('original_title', short.get('title', '')),
                    result.get('title', short.get('title')),
                    target_language
                )
                translated_hook = preserve_milli_units_from_source(
                    short.get('original_hook_text', short.get('hook_text', '')),
                    result.get('hook_text', short.get('hook_text')),
                    target_language
                )
                short['title'] = localize_measurement_units_for_tts(translated_title, target_language)
                short['hook_text'] = localize_measurement_units_for_tts(translated_hook, target_language)
                short['hashtags'] = result.get('hashtags', short.get('hashtags', ''))
                short['yt_tags'] = localize_measurement_units_for_tts(result.get('yt_tags', short.get('yt_tags', '')), target_language)
                
                translated_segs = []
                for idx, seg_text in enumerate(result["translated_segments"]):
                    source_seg = segments_texts[idx] if idx < len(segments_texts) else ""
                    protected_text = preserve_milli_units_from_source(source_seg, seg_text, target_language)
                    translated_segs.append(localize_measurement_units_for_tts(protected_text, target_language))
                if len(translated_segs) < len(short.get('segments', [])):
                    translated_segs += [s.get('text', '') for s in short['segments'][len(translated_segs):]]
                
                for idx, seg in enumerate(short.get('segments', [])):
                    trans_text = translated_segs[idx].strip()
                    seg['text'] = trans_text

                short['words'] = estimate_translated_word_timings(short.get('segments', []))
                return True
        except Exception as e:
            print(f"Translation Error (Attempt {attempt}): {e}")
    return False


def translate_texts_with_gemini(texts, target_language, api_key, source_language_hint=""):
    """Translate a flat list of strings into `target_language`, one-to-one.

    Used to REPAIR segments that the main `translate_short_with_gemini` pass left
    in the source language. Gemini-2.5-flash, when handed many short
    conversational fragments in one array, sometimes echoes the source back for
    some items instead of translating them (and the length/token validators
    can't catch that, because an untranslated item trivially preserves its own
    tokens). This focused pass re-translates just the stubborn items, with an
    explicit "never echo the source" instruction. Returns a list the same length
    as `texts`; on failure returns the inputs unchanged.
    """
    texts = [str(t or "") for t in texts]
    if not api_key or not any(t.strip() for t in texts):
        return texts
    client = genai.Client(api_key=api_key)
    hint = f" The source language is {source_language_hint}." if source_language_hint else ""
    prompt = f"""You are a professional subtitle translator.
Translate EVERY string in the array below into {target_language}.{hint}
CRITICAL: You MUST translate every single item. Never return an item unchanged in the source language. Even one-word lines, fillers and casual interjections (for example "Tak.", "No tak, tak.", "Może pokaż.", "Są takie") MUST be rendered as natural {target_language}.
Keep proper names, brands, acronyms and numbers intact. Expand compact measurement units after numbers into spoken words (e.g. "300 A" -> "300 amps").
Return ONLY a JSON object with a "translations" array of EXACTLY {len(texts)} strings, one-to-one, in the same order.

Input array:
{json.dumps(texts, ensure_ascii=False)}
"""
    json_schema = {
        "type": "OBJECT",
        "properties": {
            "translations": {"type": "ARRAY", "items": {"type": "STRING"}}
        },
    }
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=json_schema,
                    temperature=0.4 + (attempt * 0.15),
                ),
            )
            out = json.loads(response.text).get("translations", [])
            if isinstance(out, list) and len(out) == len(texts):
                result = []
                for src, dst in zip(texts, out):
                    dst = str(dst or "").strip() or src
                    dst = preserve_milli_units_from_source(src, dst, target_language)
                    dst = localize_measurement_units_for_tts(dst, target_language)
                    result.append(dst)
                return result
        except Exception as e:
            print(f"[gemini-repair] attempt {attempt} failed: {e}")
    return texts
