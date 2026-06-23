"""Text → Audio (standalone TTS) for the Text-to-Audio app.

Adapts the DubMaster "Generator Audio z Tekstu" flow: type text → (optionally
translate) → synthesize with Qwen TTS (preset speaker or cloned own-sample) →
download. Reuses the proven engine helpers from dubbing_engine + dub_pipeline.
Runs with cwd = ShortsGenerator dir and vendor on sys.path (the server sets both).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import dub_pipeline as dp  # shared helpers (_run, _ffmpeg, _chunk_text, adapters, _seg_in, _translate_internal)


def translate_text(text: str, target_lang: str, settings: Dict[str, Any], ctx) -> Dict[str, Any]:
    """Translate a free-form block of text to `target_lang` (Gemini-context or NLLB)."""
    text = " ".join((text or "").split()).strip()
    if not text:
        return {"text": ""}
    segs = [{"start_time": 0.0, "end_time": max(1.0, len(text) / 12.0), "text": text}]
    out = dp._translate_internal(segs, {**settings, "target_lang": target_lang}, ctx, 0.1, 0.95)
    ctx.progress(1.0, "Przetłumaczono.")
    return {"text": out[0]["text"] if out else text, "language": target_lang}


def _concat_tts(paths: List[Path], out_wav: Path, gap: float = 0.08) -> None:
    import numpy as np
    import soundfile as sf
    parts = []
    sr = 24000
    for i, p in enumerate(paths):
        if not Path(p).exists():
            continue
        a, s = sf.read(str(p), dtype="float32", always_2d=False)
        sr = s
        if a.ndim > 1:
            a = a.mean(axis=1)
        parts.append(a)
        if gap > 0 and i < len(paths) - 1:
            parts.append(np.zeros(int(s * gap), dtype="float32"))  # small gap between chunks
    if not parts:
        raise RuntimeError("TTS nie wygenerował żadnego audio.")
    sf.write(str(out_wav), np.concatenate(parts), sr)


def generate_tts(text: str, settings: Dict[str, Any], ctx, *, output_dir: Path, ref_audio_path: str = "") -> Dict[str, Any]:
    import dubbing_engine as de  # type: ignore
    work = Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    text = " ".join((text or "").split()).strip()
    if not text:
        raise RuntimeError("Brak tekstu do syntezy mowy.")

    language = settings.get("language") or settings.get("target_lang") or "Angielski"
    voice_source = settings.get("voice_source", "Głos presetowy (Qwen)")
    ref_audio = ""
    if voice_source == "Sklonowany głos (własna próbka)":
        ref_audio = ref_audio_path or ""
        if not ref_audio or not os.path.exists(ref_audio):
            raise RuntimeError("Wybrano własną próbkę głosu, ale plik nie istnieje.")
    # Preset Qwen voice → ref_audio stays empty (CustomVoice model + speaker).

    is_omni = str(settings.get("tts_engine", "qwen")).lower() in ("omnivoice", "omni")
    # OmniVoice handles long inputs natively (it splits at sentence boundaries with a
    # cross-fade), so feed it big chunks → far fewer joins → no choppy pauses. Qwen
    # needs the small, safe chunks to avoid dropped words.
    chunks = dp._chunk_text(text, max_chars=500, max_words=90) if is_omni else dp._chunk_text(text)
    durations = [max(2.0, len(c) / 11.0) for c in chunks]  # length hints for token budget
    raw_paths = [work / f"tts_{i:03d}.wav" for i in range(len(chunks))]
    engine_settings = {
        **settings,
        "dub_qwen_speaker": settings.get("dubbing_qwen_speaker", "Aiden"),
        "dub_style_prompt": settings.get("voiceover_style", ""),
    }
    engine_label = "OmniVoice" if str(engine_settings.get("tts_engine", "qwen")).lower() in ("omnivoice", "omni") else "Qwen TTS"
    ctx.progress(0.1, f"Synteza mowy ({engine_label})…")
    de._run_tts(chunks, chunks, language, ref_audio, raw_paths, durations,
                engine_settings, dp._StatusAdapter(ctx), dp._ProgressAdapter(ctx, 0.1, 0.85))

    ctx.progress(0.9, "Składanie ścieżki audio…")
    final_wav = work / "speech.wav"
    # OmniVoice already pads/fades each chunk, so keep joins nearly seamless.
    _concat_tts(raw_paths, final_wav, gap=0.02 if is_omni else 0.08)
    final = work / "speech.mp3"
    dp._run([dp._ffmpeg(), "-y", "-loglevel", "error", "-i", str(final_wav),
             "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(final)], "Eksport MP3")
    ctx.progress(1.0, "Gotowe — audio z tekstu wygenerowane.")
    return {"audio": str(final), "mp3": str(final), "wav": str(final_wav), "language": language, "dir": str(work)}
