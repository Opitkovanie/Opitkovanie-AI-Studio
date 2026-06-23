"""Local-first subtitle/metadata translation for DubCut Studio shorts.

Replaces the hard dependency on the paid Gemini API with on-device engines:

  * NLLB-200-distilled-600M (default) — Meta's 200-language NMT via
    `transformers`, MPS-accelerated on Apple Silicon. Weights download once to
    ~/.cache/huggingface (the MAIN system, not the app bundle), mirroring how
    OmniVoice Studio ships its offline translator.
  * Argos Translate (fallback) — pure-CPU, ~50 MB per language pair, no torch.
  * Gemini 2.5 Flash (optional) — only when explicitly selected AND a key is set.

The public entry point ``translate_short()`` mirrors the in-place mutation
contract of ``ai_processor.translate_short_with_gemini`` so the server's two
call sites are a drop-in swap. It auto-falls-back NLLB -> Argos -> Gemini, so a
missing optional dep or an unsupported language pair never dead-ends the user
the way the old Gemini-only path did.
"""
from __future__ import annotations

import copy
import importlib.util as _iu
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

# Let unsupported MPS ops fall back to CPU PER-OP instead of raising. Without this
# a single op-coverage gap made NLLB's `generate()` throw, and the old handler
# then moved the WHOLE model to CPU for the rest of the session — so one stray
# op turned every subsequent language into a 20-40x-slower CPU run (the root of
# "1 language = 1 min, 10 languages = 3 hours"). With the flag set, MPS stays the
# device and only the missing op dips to CPU. Must be set before torch imports.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

NLLB_MODEL = "facebook/nllb-200-distilled-600M"

# Polish display name (as used by DUB_TARGET_LANGUAGES) -> (ISO 639-1, FLORES-200).
# Accent-stripped aliases included so "hiszpanski" and "Hiszpański" both resolve.
_LANG: dict[str, Tuple[str, str]] = {
    "angielski": ("en", "eng_Latn"),
    "niemiecki": ("de", "deu_Latn"),
    "francuski": ("fr", "fra_Latn"),
    "hiszpanski": ("es", "spa_Latn"),
    "wloski": ("it", "ita_Latn"),
    "portugalski": ("pt", "por_Latn"),
    "holenderski": ("nl", "nld_Latn"),
    "polski": ("pl", "pol_Latn"),
    "rosyjski": ("ru", "rus_Cyrl"),
    "ukrainski": ("uk", "ukr_Cyrl"),
    "czeski": ("cs", "ces_Latn"),
    "slowacki": ("sk", "slk_Latn"),
    "szwedzki": ("sv", "swe_Latn"),
    "norweski": ("no", "nob_Latn"),
    "dunski": ("da", "dan_Latn"),
    "finski": ("fi", "fin_Latn"),
    "grecki": ("el", "ell_Grek"),
    "rumunski": ("ro", "ron_Latn"),
    "wegierski": ("hu", "hun_Latn"),
    "bulgarski": ("bg", "bul_Cyrl"),
    "chorwacki": ("hr", "hrv_Latn"),
    "serbski": ("sr", "srp_Cyrl"),
    "turecki": ("tr", "tur_Latn"),
    "arabski": ("ar", "arb_Arab"),
    "hebrajski": ("he", "heb_Hebr"),
    "hindi": ("hi", "hin_Deva"),
    "wietnamski": ("vi", "vie_Latn"),
    "tajski": ("th", "tha_Thai"),
    "indonezyjski": ("id", "ind_Latn"),
    "japonski": ("ja", "jpn_Jpan"),
    "koreanski": ("ko", "kor_Hang"),
    "chinski": ("zh", "zho_Hans"),
}

# ISO 639-1 (langdetect output) -> FLORES-200, for the auto-detected source side.
_ISO_TO_FLORES: dict[str, str] = {
    "en": "eng_Latn", "de": "deu_Latn", "fr": "fra_Latn", "es": "spa_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl", "ar": "arb_Arab",
    "ja": "jpn_Jpan", "ko": "kor_Hang", "zh": "zho_Hans", "zh-cn": "zho_Hans",
    "pl": "pol_Latn", "nl": "nld_Latn", "uk": "ukr_Cyrl", "cs": "ces_Latn",
    "tr": "tur_Latn", "sv": "swe_Latn", "hi": "hin_Deva", "ro": "ron_Latn",
}


def _norm(name: str) -> str:
    """Lowercase + strip Polish diacritics so display names match `_LANG` keys."""
    s = (name or "").strip().lower()
    table = str.maketrans("ąćęłńóśźż", "acelnoszz")
    return s.translate(table)


# Split on sentence terminators (., !, ?, …) keeping the delimiter with its
# sentence. Also splits very long comma runs so a single breath-less clause
# doesn't trip NLLB's content-dropping behaviour.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _split_sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    out: List[str] = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        # Hard cap pathological run-on clauses (no terminal punctuation) by
        # splitting on commas once they exceed ~160 chars.
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


# --- dependency probes ------------------------------------------------------
def _has(mod: str) -> bool:
    try:
        return _iu.find_spec(mod) is not None
    except Exception:
        return False


_HAS_LANGDETECT = _has("langdetect")


def nllb_ready() -> bool:
    return _has("transformers") and _has("torch")


def argos_ready() -> bool:
    return _has("argostranslate")


def engine_status() -> dict:
    """Readiness flags surfaced in the Settings → 'Gotowość modułów' panel."""
    return {"nllb": nllb_ready(), "argos": argos_ready()}


# --- source-language detection ----------------------------------------------
def _detect_source(text: str) -> Tuple[str, str]:
    """Return (iso, flores) for the source text.

    Uses `langdetect` when installed; otherwise a cheap diacritic heuristic so
    the common Polish->X and English->X cases still work without the dep.
    """
    iso = ""
    try:
        from langdetect import detect  # type: ignore
        iso = (detect(text) or "").lower().split("-")[0]
    except Exception:
        if re.search(r"[ąćęłńóśźż]", text or "", re.IGNORECASE):
            iso = "pl"
        elif re.search(r"[一-鿿]", text or ""):
            iso = "zh"
        elif re.search(r"[぀-ヿ]", text or ""):
            iso = "ja"
        elif re.search(r"[Ѐ-ӿ]", text or ""):
            iso = "ru"
        else:
            iso = "en"
    return iso, _ISO_TO_FLORES.get(iso, "eng_Latn")


# --- NLLB engine ------------------------------------------------------------
_nllb = {"model": None, "tok": None, "device": None}
_NLLB_BATCH_SIZE = 16
# Greedy decoding (1 beam) is ~7x faster than the old 4-beam search on Apple MPS
# and, for short subtitle sentences guarded by `no_repeat_ngram_size`, produces
# output of equal quality. Beam search was the single biggest per-language cost.
_NLLB_NUM_BEAMS = 1


def _load_nllb():
    import torch  # noqa
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    if _nllb["tok"] is None:
        _nllb["tok"] = AutoTokenizer.from_pretrained(NLLB_MODEL)
    if _nllb["model"] is None:
        model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL)
        model.eval()
        device = "cpu"
        try:
            if torch.backends.mps.is_available():
                model = model.to("mps")
                device = "mps"
            elif torch.cuda.is_available():
                model = model.to("cuda")
                device = "cuda"
        except Exception as e:  # noqa: BLE001
            print(f"[local_translate] NLLB device placement failed ({e}); using CPU.")
            device = "cpu"
        _nllb["model"] = model
        _nllb["device"] = device
    return _nllb["tok"], _nllb["model"], _nllb["device"]


def _nllb_generate(sentence: str, src_flores: str, tgt_flores: str) -> str:
    """Translate ONE sentence with NLLB. Length-aware + anti-degeneration.

    NLLB-200 is trained on single sentences; long multi-sentence inputs make it
    drop content and loop (e.g. "No, no, no, no…"). We therefore feed it one
    sentence at a time (see `_nllb_one`) and cap output length to the input so a
    short source can't balloon into a repetition loop.
    """
    tok, model, device = _load_nllb()
    tok.src_lang = src_flores
    inputs = tok(sentence, return_tensors="pt", truncation=True, max_length=400)
    if device != "cpu":
        inputs = {k: v.to(device) for k, v in inputs.items()}
    bos = tok.convert_tokens_to_ids(tgt_flores)
    n_in = int(inputs["input_ids"].shape[-1])
    gen_kw = dict(
        forced_bos_token_id=bos,
        num_beams=_NLLB_NUM_BEAMS,
        no_repeat_ngram_size=3,          # kills the "no, no, no…" degeneration
        max_new_tokens=max(48, int(n_in * 2) + 16),
    )
    try:
        out = model.generate(**inputs, **gen_kw)
    except Exception as e:  # noqa: BLE001
        # Known MPS op-coverage gaps: drop to CPU permanently for this session.
        if device == "mps":
            print(f"[local_translate] NLLB MPS generate failed ({e}); falling back to CPU.")
            model.to("cpu")
            _nllb["device"] = "cpu"
            inputs = {k: v.to("cpu") for k, v in inputs.items()}
            out = model.generate(**inputs, **gen_kw)
        else:
            raise
    return tok.batch_decode(out, skip_special_tokens=True)[0].strip()


CancelCheck = Optional[Callable[[], None]]


def _nllb_generate_batch(sentences: List[str], src_flores: str, tgt_flores: str,
                         cancel_check: CancelCheck = None) -> List[str]:
    """Translate a batch of already-split sentences with one model.generate call.

    The previous path called `generate()` for every single subtitle sentence. On
    a 30-minute transcript multiplied by 10+ languages that creates thousands of
    tiny ML calls, where scheduling/tokenization overhead dominates. Batching
    keeps the same sentence-level safety while letting torch/MPS do real work.
    """
    if not sentences:
        return []
    if cancel_check:
        cancel_check()
    tok, model, device = _load_nllb()
    tok.src_lang = src_flores
    inputs = tok(sentences, return_tensors="pt", truncation=True, max_length=400, padding=True)
    if device != "cpu":
        inputs = {k: v.to(device) for k, v in inputs.items()}
    bos = tok.convert_tokens_to_ids(tgt_flores)
    n_in = int(inputs["input_ids"].shape[-1])
    gen_kw = dict(
        forced_bos_token_id=bos,
        num_beams=_NLLB_NUM_BEAMS,
        no_repeat_ngram_size=3,
        max_new_tokens=max(48, int(n_in * 2) + 16),
    )
    if cancel_check:
        from transformers import StoppingCriteria, StoppingCriteriaList

        class _CancelStoppingCriteria(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):  # noqa: ANN001
                try:
                    cancel_check()
                except Exception:
                    return True
                return False

        stopping_criteria = StoppingCriteriaList([_CancelStoppingCriteria()])
        gen_kw["stopping_criteria"] = stopping_criteria

    try:
        import torch  # noqa
        with torch.inference_mode():
            out = model.generate(**inputs, **gen_kw)
    except Exception as e:  # noqa: BLE001
        if device == "mps":
            print(f"[local_translate] NLLB MPS batch generate failed ({e}); falling back to CPU.")
            model.to("cpu")
            _nllb["device"] = "cpu"
            inputs = {k: v.to("cpu") for k, v in inputs.items()}
            import torch  # noqa
            with torch.inference_mode():
                out = model.generate(**inputs, **gen_kw)
        else:
            raise
    if cancel_check:
        cancel_check()
    return [x.strip() for x in tok.batch_decode(out, skip_special_tokens=True)]


def _nllb_one(text: str, src_flores: str, tgt_flores: str) -> str:
    """Translate a segment, sentence-by-sentence, so no content is dropped."""
    if not text or not text.strip():
        return text
    sentences = _split_sentences(text) or [text.strip()]
    parts = [_nllb_generate(s, src_flores, tgt_flores) for s in sentences]
    return " ".join(p for p in parts if p).strip() or text


def _nllb_batch_texts(texts: List[str], src_flores: str, tgt_flores: str,
                      cancel_check: CancelCheck = None) -> List[str]:
    """Translate subtitle segments in batches while preserving segment boundaries."""
    split_per_text: List[List[str]] = []
    flat: List[str] = []
    owners: List[int] = []
    for idx, text in enumerate(texts):
        sentences = _split_sentences(text) if text and text.strip() else []
        split_per_text.append(sentences)
        for sentence in sentences:
            flat.append(sentence)
            owners.append(idx)
    if not flat:
        return list(texts)

    translated_flat: List[str] = []
    for off in range(0, len(flat), _NLLB_BATCH_SIZE):
        if cancel_check:
            cancel_check()
        translated_flat.extend(_nllb_generate_batch(
            flat[off:off + _NLLB_BATCH_SIZE], src_flores, tgt_flores, cancel_check))

    grouped: List[List[str]] = [[] for _ in texts]
    for owner, translated in zip(owners, translated_flat):
        if translated:
            grouped[owner].append(translated)

    out: List[str] = []
    for original, sentences, translated_parts in zip(texts, split_per_text, grouped):
        if not sentences:
            out.append(original)
        else:
            out.append(" ".join(translated_parts).strip() or original)
    return out


def _nllb_encode_batches(flat: List[str], src_flores: str, cancel_check: CancelCheck = None):
    """Run NLLB's encoder ONCE over every source sentence (in padded batches) and
    return the cached encoder outputs so the decoder can be replayed per language.

    NLLB is an encoder-decoder model and the source side is language-agnostic, so
    we never need to re-encode it for each target language — that re-encoding was
    what made cost scale linearly with language count. Returns a list of
    (last_hidden_state, attention_mask, max_new_tokens) tuples, one per batch."""
    import torch  # noqa
    tok, model, device = _load_nllb()
    tok.src_lang = src_flores
    cached = []
    for off in range(0, len(flat), _NLLB_BATCH_SIZE):
        if cancel_check:
            cancel_check()
        chunk = flat[off:off + _NLLB_BATCH_SIZE]
        inputs = tok(chunk, return_tensors="pt", truncation=True, max_length=400, padding=True)
        if device != "cpu":
            inputs = {k: v.to(device) for k, v in inputs.items()}
        attn = inputs["attention_mask"]
        n_in = int(inputs["input_ids"].shape[-1])
        with torch.inference_mode():
            enc = model.get_encoder()(input_ids=inputs["input_ids"], attention_mask=attn)
        cached.append((enc.last_hidden_state, attn, max(48, int(n_in * 2) + 16)))
    return cached


def _nllb_decode_language(cached, tgt_flores: str, cancel_check: CancelCheck = None) -> List[str]:
    """Decode every cached source batch into one target language (encoder reused)."""
    import torch  # noqa
    from transformers.modeling_outputs import BaseModelOutput
    tok, model, _device = _load_nllb()
    bos = tok.convert_tokens_to_ids(tgt_flores)
    out: List[str] = []
    for last_hidden, attn, max_new in cached:
        if cancel_check:
            cancel_check()
        # Fresh wrapper each call so generate's internal bookkeeping never mutates
        # the shared, cached encoder tensor.
        enc_out = BaseModelOutput(last_hidden_state=last_hidden)
        with torch.inference_mode():
            o = model.generate(encoder_outputs=enc_out, attention_mask=attn,
                               forced_bos_token_id=bos, num_beams=_NLLB_NUM_BEAMS,
                               no_repeat_ngram_size=3, max_new_tokens=max_new)
        out.extend(x.strip() for x in tok.batch_decode(o, skip_special_tokens=True))
    return out


# progress(fraction, current_target_flores) — fires as each language STARTS so the
# UI can name the language currently being generated.
MultiProgress = Optional[Callable[[float, str], None]]


def _nllb_batch_texts_multi(texts: List[str], src_flores: str,
                            tgt_flores_list: List[str],
                            cancel_check: CancelCheck = None,
                            progress: MultiProgress = None,
                            ) -> Dict[str, List[str]]:
    """Segment-boundary-preserving multi-language NLLB translation, encoding the
    source ONCE then decoding language-by-language (so progress can name the
    current language). Returns {tgt_flores: [seg texts]}."""
    split_per_text: List[List[str]] = []
    flat: List[str] = []
    owners: List[int] = []
    for idx, text in enumerate(texts):
        sentences = _split_sentences(text) if text and text.strip() else []
        split_per_text.append(sentences)
        for sentence in sentences:
            flat.append(sentence)
            owners.append(idx)
    if not flat:
        return {t: list(texts) for t in tgt_flores_list}

    cached = _nllb_encode_batches(flat, src_flores, cancel_check)

    out: Dict[str, List[str]] = {}
    total = max(1, len(tgt_flores_list))
    for li, t in enumerate(tgt_flores_list):
        if cancel_check:
            cancel_check()
        if progress:
            progress(li / total, t)
        flat_tr = _nllb_decode_language(cached, t, cancel_check)
        grouped: List[List[str]] = [[] for _ in texts]
        for owner, tr in zip(owners, flat_tr):
            if tr:
                grouped[owner].append(tr)
        merged: List[str] = []
        for original, sentences, parts in zip(texts, split_per_text, grouped):
            if not sentences:
                merged.append(original)
            else:
                merged.append(" ".join(parts).strip() or original)
        out[t] = merged
    return out


# --- Argos engine -----------------------------------------------------------
def _argos_batch(texts: List[str], src_iso: str, tgt_iso: str) -> List[str]:
    import argostranslate.package
    import argostranslate.translate

    installed = argostranslate.package.get_installed_packages()
    have = any(p.from_code == src_iso and p.to_code == tgt_iso for p in installed)
    if not have:
        argostranslate.package.update_package_index()
        avail = argostranslate.package.get_available_packages()
        pkg = next((p for p in avail if p.from_code == src_iso and p.to_code == tgt_iso), None)
        if pkg is None:
            raise RuntimeError(f"Argos: brak pakietu językowego {src_iso} -> {tgt_iso}")
        argostranslate.package.install_from_path(pkg.download())
    return [
        argostranslate.translate.translate(t, src_iso, tgt_iso) if t and t.strip() else t
        for t in texts
    ]


# --- vendor post-processing helpers (shared with the Gemini path) -----------
def _vendor_helpers():
    """Reuse ShortsGenerator's unit-localisation + timing helpers when importable.

    Falls back to identity functions so translation still works even if the
    vendor module layout changes.
    """
    try:
        from ai_processor import (  # type: ignore
            estimate_translated_word_timings,
            localize_measurement_units_for_tts,
            preserve_milli_units_from_source,
        )
        return (
            preserve_milli_units_from_source,
            localize_measurement_units_for_tts,
            estimate_translated_word_timings,
        )
    except Exception:
        return (lambda s, t, lang: t), (lambda t, lang: t), None


# --- public API -------------------------------------------------------------
def _resolve_chain(engine: str, gemini_api_key: str) -> List[str]:
    engine = (engine or "nllb").lower()
    have_key = bool((gemini_api_key or "").strip())
    if engine == "gemini":
        chain = (["gemini"] if have_key else []) + ["nllb", "argos"]
    elif engine == "argos":
        chain = ["argos", "nllb"]
    else:  # nllb (default)
        chain = ["nllb", "argos"]
    seen, out = set(), []
    for e in chain:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _backup_originals(short: dict) -> None:
    if "original_words" in short:
        return
    short["original_words"] = copy.deepcopy(short.get("words", []))
    short["original_segments"] = copy.deepcopy(short.get("segments", []))
    short["original_title"] = short.get("title", "")
    short["original_hook_text"] = short.get("hook_text", "")
    short["original_hashtags"] = short.get("hashtags", "")
    short["original_yt_tags"] = short.get("yt_tags", "")


def _translate_local(short: dict, target_language: str, engine: str,
                     cancel_check: CancelCheck = None) -> bool:
    tgt = _LANG.get(_norm(target_language))
    if not tgt:
        raise RuntimeError(f"Nieobsługiwany język docelowy: {target_language}")
    tgt_iso, tgt_flores = tgt
    preserve, localize, estimate = _vendor_helpers()

    orig_segs = short.get("original_segments", short.get("segments", []))
    seg_texts = [s.get("text", "") for s in orig_segs]
    sample = " ".join(t for t in seg_texts if t)[:1000] or short.get("original_title", "")
    src_iso, src_flores = _detect_source(sample)

    # Build the per-engine translate callable up-front so an unavailable engine
    # raises immediately (and the caller falls back) instead of mid-way through.
    translate: Callable[[List[str]], List[str]]
    if engine == "nllb":
        if not nllb_ready():
            raise RuntimeError("NLLB niedostępny (brak transformers/torch w środowisku).")
        translate = lambda texts: _nllb_batch_texts(texts, src_flores, tgt_flores, cancel_check)  # noqa: E731
    elif engine == "argos":
        if not argos_ready():
            raise RuntimeError("Argos niedostępny (brak pakietu argostranslate).")
        translate = lambda texts: _argos_batch(texts, src_iso, tgt_iso)  # noqa: E731
    else:
        raise RuntimeError(f"Nieznany silnik lokalny: {engine}")

    title_src = short.get("original_title", short.get("title", ""))
    hook_src = short.get("original_hook_text", short.get("hook_text", ""))

    if src_iso == tgt_iso:
        # Source already in the target language — copy through, recompute timings.
        seg_out = list(seg_texts)
        title_out, hook_out = title_src, hook_src
    else:
        seg_out = translate(seg_texts)
        title_out, hook_out = translate([title_src, hook_src])

    # Apply the same milli-unit protection + TTS unit-expansion the Gemini path uses.
    segments = short.get("segments", [])
    for idx, seg in enumerate(segments):
        if idx >= len(seg_out):
            break
        src_seg = seg_texts[idx] if idx < len(seg_texts) else ""
        txt = seg_out[idx] or seg.get("text", "")
        txt = localize(preserve(src_seg, txt, target_language), target_language)
        seg["text"] = (txt or "").strip()

    short["title"] = localize(preserve(title_src, title_out, target_language), target_language)
    short["hook_text"] = localize(preserve(hook_src, hook_out, target_language), target_language)

    # Hashtags / YT tags: translate the human words into the target language, but keep
    # brand names, acronyms and alphanumerics (BMS, LiFePO4, 150Ah, Opitkovanie) intact —
    # those would be corrupted by neural MT. Source-language passthrough when languages match.
    hashtags_src = short.get("original_hashtags", short.get("hashtags", "")) or ""
    yt_tags_src = short.get("original_yt_tags", short.get("yt_tags", "")) or ""

    def _keep_token(word: str) -> bool:
        w = word.strip()
        if not w:
            return True
        if any(c.isdigit() for c in w):
            return True                       # LiFePO4, 150Ah, 4K
        if w.isupper() and len(w) <= 6:
            return True                       # BMS, AI, YT acronyms
        return False

    def _translate_word(word: str) -> str:
        tr = translate([word])[0] if word else word
        return localize(preserve(word, tr or word, target_language), target_language).strip() or word

    if src_iso == tgt_iso:
        short["hashtags"] = hashtags_src
        short["yt_tags"] = yt_tags_src
    else:
        out_hashtags = []
        for raw in hashtags_src.split():
            if not raw.strip():
                continue
            body = raw.lstrip("#")
            if _keep_token(body):
                out_hashtags.append("#" + body)
            else:
                tr = _translate_word(body)
                tr = "".join(p[:1].upper() + p[1:] for p in re.split(r"\s+", tr) if p) or body
                out_hashtags.append("#" + tr)
        short["hashtags"] = " ".join(out_hashtags)

        out_yt = []
        for term in yt_tags_src.split(","):
            term = term.strip()
            if not term:
                continue
            out_yt.append(term if _keep_token(term) else _translate_word(term))
        short["yt_tags"] = ", ".join(out_yt)
    return True


def _split_long_segments(short: dict, max_dur: float = 10.0) -> None:
    """Split over-long segments into sentence sub-segments, in place.

    A short is a *supercut*: the dub plays each segment's time-window back to
    back, so a 28s segment whose TTS speech is only ~10s leaves ~18s of silence
    padding. Breaking it into sentence-sized windows (proportional to sentence
    length, preserving the segment's total span) spreads the speech across the
    window — no dead air — and also yields more readable subtitles.
    """
    new_segs: list = []
    for seg in short.get("segments", []):
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start))
        span = end - start
        text = str(seg.get("text", "")).strip()
        sentences = _split_sentences(text)
        if span <= max_dur or len(sentences) <= 1:
            new_segs.append(seg)
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
            new_segs.append(sub)
            cursor = sub_end
    short["segments"] = new_segs


_CMP_STRIP = re.compile(r"[^\w]+", re.UNICODE)
_PL_DIACRITICS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
_LATIN_TARGETS = {"en", "de", "fr", "es", "it", "pt", "nl", "sv", "ro", "cs", "tr"}


def _cmp_key(s: str) -> str:
    """Punctuation/space/case-insensitive key for detecting an echoed source."""
    return _CMP_STRIP.sub("", (s or "").strip().lower())


def _has_translatable_words(s: str) -> bool:
    """True if the text has at least one real word (not just numbers/acronyms/
    brand tokens) — i.e. something a translator should actually change."""
    for w in re.findall(r"[^\W\d_]+", s or "", re.UNICODE):
        if len(w) >= 2:
            return True
    return False


def _segment_needs_repair(src: str, cur: str, tgt_iso: str, src_iso: str) -> bool:
    """Flag a segment that did NOT translate cleanly 1:1 with its source.

    Two failure modes, both produced by Gemini's batch call and both invisible to
    the count/length validators:
      • UNTRANSLATED echo — byte-identical to source, still scans as the source
        language, or a Latin target still carrying Polish diacritics.
      • MISALIGNED explosion — Gemini ignores segment boundaries and dumps the
        translation of several source segments into one item, so a 19-word source
        comes back as 114 words. TTS then has to cram it and speaks unintelligibly
        fast. We flag anything far longer than its own source can justify.
    Pure number/acronym lines are never flagged.
    """
    cur = (cur or "").strip()
    if not cur or not _has_translatable_words(src):
        return False
    # untranslated echo
    if _cmp_key(cur) == _cmp_key(src):
        return True
    if tgt_iso in _LATIN_TARGETS and src_iso == "pl" and _PL_DIACRITICS.search(cur):
        return True
    # "Still in the source language?" — only trust this when langdetect is actually
    # installed. Without it, `_detect_source` defaults every Latin/no-diacritic
    # string to "en", which makes a correct Dutch/German/etc. translation of an
    # English source look untranslated (false positive → wasted repair pass).
    if _HAS_LANGDETECT:
        cur_iso, _ = _detect_source(cur)
        if cur_iso != tgt_iso and cur_iso == src_iso and len(cur) >= 12:
            return True
    # misaligned explosion: translation hugely longer than the source warrants
    src_words = len(str(src).split())
    cur_words = len(cur.split())
    if cur_words > max(8, int(src_words * 2.2) + 4):
        return True
    return False


def _repair_with_local(texts: List[str], src_iso: str, tgt_iso: str) -> Optional[List[str]]:
    """Translate stubborn segments with the local engines (no API cost). Returns
    None when no local engine is available."""
    src_flores = _ISO_TO_FLORES.get(src_iso, "pol_Latn")
    tgt_flores = _ISO_TO_FLORES.get(tgt_iso, "eng_Latn")
    try:
        if nllb_ready():
            return [_nllb_one(t, src_flores, tgt_flores) for t in texts]
    except Exception as e:  # noqa: BLE001
        print(f"[local_translate] naprawa NLLB nie powiodła się: {e}")
    try:
        if argos_ready():
            return _argos_batch(texts, src_iso, tgt_iso)
    except Exception as e:  # noqa: BLE001
        print(f"[local_translate] naprawa Argos nie powiodła się: {e}")
    return None


def _repair_untranslated_segments(short: dict, target_language: str, *, gemini_api_key: str = "") -> None:
    """Second-chance pass over segments the primary engine left in the source
    language. Re-translates only the failing ones (focused Gemini call, then a
    local-engine fallback). Runs on the un-split segments so indices still line
    up 1:1 with `original_segments`. No-op when source == target language."""
    short["_translation_repair"] = {"flagged": 0, "repaired": 0, "leftover": 0,
                                    "total": len(short.get("segments", []) or [])}
    tgt = _LANG.get(_norm(target_language))
    if not tgt:
        return
    tgt_iso = tgt[0]
    orig_segs = short.get("original_segments", []) or []
    segs = short.get("segments", []) or []
    if not orig_segs or not segs:
        return
    sample = " ".join((s.get("text") or "") for s in orig_segs)[:1000]
    src_iso, _ = _detect_source(sample)
    if src_iso == tgt_iso:
        return  # legit passthrough — nothing to repair

    def _bad_indices() -> List[int]:
        return [
            i for i, seg in enumerate(segs)
            if i < len(orig_segs)
            and _segment_needs_repair(
                (orig_segs[i].get("text") or "").strip(),
                (seg.get("text") or "").strip(),
                tgt_iso, src_iso,
            )
        ]

    def _apply(indices: List[int], translated: Optional[List[str]]) -> None:
        """Write back only the items that actually came out in the target language."""
        if not translated:
            return
        for j, i in enumerate(indices):
            if j >= len(translated):
                break
            new = (translated[j] or "").strip()
            src = (orig_segs[i].get("text") or "").strip()
            if new and not _segment_needs_repair(src, new, tgt_iso, src_iso):
                segs[i]["text"] = new

    bad = _bad_indices()
    short["_translation_repair"]["flagged"] = len(bad)
    if not bad:
        return
    print(f"[local_translate] naprawiam {len(bad)} nieprzetłumaczonych segmentów…")

    # 1) Focused Gemini pass (preferred quality) — but it can still echo some.
    if gemini_api_key.strip():
        try:
            from ai_processor import translate_texts_with_gemini  # type: ignore
            _apply(bad, translate_texts_with_gemini(
                [(orig_segs[i].get("text") or "") for i in bad],
                target_language, gemini_api_key, source_language_hint=src_iso))
        except Exception as e:  # noqa: BLE001
            print(f"[local_translate] naprawa Gemini nie powiodła się: {e}")

    # 2) Whatever Gemini STILL left in the source language → local NLLB/Argos.
    # transformers+torch are bundled, so this is the reliable, free backstop that
    # guarantees no source-language segments survive when a local engine exists.
    still = _bad_indices()
    if still:
        local = _repair_with_local([(orig_segs[i].get("text") or "") for i in still], src_iso, tgt_iso)
        if local:
            _apply(still, local)
            leftover = _bad_indices()
            if leftover:
                print(f"[local_translate] {len(leftover)} segmentów nadal nieprzetłumaczonych po naprawie lokalnej.")
        else:
            print("[local_translate] brak lokalnego silnika (NLLB/Argos) do naprawy — segmenty mogą zostać w języku źródłowym.")
    leftover_n = len(_bad_indices())
    rep = short["_translation_repair"]
    rep["leftover"] = leftover_n
    rep["repaired"] = max(0, rep["flagged"] - leftover_n)


def _finalize_segments(short: dict, target_language: str, split: bool = True) -> None:
    """Post-translation cleanup shared by every engine: recompute word timings so
    subtitles + dub alignment stay consistent with the translated text.

    NOTE: we deliberately DO NOT split the canonical ``short["segments"]`` anymore.
    Those are the coarse Gemini "scenes" the user cuts in the scene editor — a
    short used to show 1–3 of them, and the in-place sentence split turned that
    into 27 tiny rows (the QWEN-TTS chunks), which the user does not want to see.
    The dead-air-avoiding sentence split now happens transiently inside the dub
    engine (``dubbing_engine._split_segments_for_tts``) on a throwaway copy, so it
    never reaches the persisted/displayed segments. ``split`` is kept only for
    backward-compatible call sites."""
    # PASSTHROUGH (same-language "translation", e.g. Polski→Polski): the segment text is
    # byte-for-byte the original, so the precise per-word timings Whisper produced are
    # still valid. Re-estimating them (evenly by character length) would needlessly
    # DRIFT the subtitles off the speech. Keep the original Whisper words instead.
    orig_segs = short.get("original_segments") or []
    segs = short.get("segments") or []
    orig_words = short.get("original_words") or []
    unchanged = (
        bool(orig_words)
        and len(orig_segs) == len(segs)
        and all((str(a.get("text", "")).strip() == str(b.get("text", "")).strip())
                for a, b in zip(orig_segs, segs))
    )
    if unchanged:
        short["words"] = copy.deepcopy(orig_words)
        return
    _, _, estimate = _vendor_helpers()
    if estimate:
        short["words"] = estimate(short.get("segments", []))


def _apply_glossary_to_short(short: dict, glossary_text: str) -> None:
    """Enforce the user's correction dictionary (Słownik poprawek) on the TRANSLATED
    output — for every engine, not just Gemini. Translation can re-mangle proper
    names (WattCycle, OpenAI, Anthropic, Humsienk…) that were fixed in the
    transcript, so we re-apply the canonical spellings to the translated title,
    hook, hashtags, yt_tags, every segment and every word."""
    if not (glossary_text or "").strip():
        return
    try:
        from ai_processor import (  # type: ignore
            apply_whisper_glossary_to_transcript, apply_whisper_glossary_to_words,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[local_translate] słownik poprawek niedostępny: {e}")
        return
    for key in ("title", "hook_text", "hashtags", "yt_tags"):
        if short.get(key):
            short[key] = apply_whisper_glossary_to_transcript(short[key], glossary_text)
    for seg in short.get("segments", []):
        if seg.get("text"):
            seg["text"] = apply_whisper_glossary_to_transcript(seg["text"], glossary_text)
    if short.get("words"):
        short["words"] = apply_whisper_glossary_to_words(short["words"], glossary_text)


def translate_segments_multi(
    seg_texts: List[str],
    target_languages: List[str],
    *,
    engine: str = "nllb",
    glossary_text: str = "",
    cancel_check: CancelCheck = None,
    progress: MultiProgress = None,
) -> Dict[str, List[str]]:
    """Translate one list of subtitle segment texts into MANY languages at once
    with the local NLLB engine, encoding the source a single time.

    `progress(fraction, display_language)` fires as each language STARTS so the UI
    can show which language is currently being generated.

    This is the local counterpart to the Gemini "all languages in one call" fast
    path used by the Napisy batch export. Returns {display_language: [texts]} with
    one entry per requested language (aligned 1:1 with `seg_texts`). Units are
    localised + milli-unit-protected and the glossary re-applied per language, the
    same post-processing the per-language path does. Raises on a hard NLLB failure
    so the caller can fall back to the legacy per-language path.
    """
    targets = [str(l) for l in target_languages if str(l or "").strip()]
    if not targets or not seg_texts:
        return {}
    if engine not in ("nllb", ""):
        raise RuntimeError("translate_segments_multi obsługuje tylko silnik NLLB.")
    if not nllb_ready():
        raise RuntimeError("NLLB niedostępny (brak transformers/torch w środowisku).")

    preserve, localize, _estimate = _vendor_helpers()
    sample = " ".join(t for t in seg_texts if t)[:1000]
    src_iso, src_flores = _detect_source(sample)

    # Map every requested display language to its FLORES code; languages already in
    # the source language pass through unchanged (no needless round-trip).
    flores_for: Dict[str, str] = {}
    passthrough: List[str] = []
    flores_targets: List[str] = []
    for disp in targets:
        tgt = _LANG.get(_norm(disp))
        if not tgt:
            raise RuntimeError(f"Nieobsługiwany język docelowy: {disp}")
        tgt_iso, tgt_flores = tgt
        if tgt_iso == src_iso:
            passthrough.append(disp)
        else:
            flores_for[disp] = tgt_flores
            if tgt_flores not in flores_targets:
                flores_targets.append(tgt_flores)

    # Translate the non-passthrough languages, reporting progress with the human
    # display name (map FLORES code back to the first display language using it).
    flores_to_disp: Dict[str, str] = {}
    for disp, fl in flores_for.items():
        flores_to_disp.setdefault(fl, disp)
    inner_progress: Optional[Callable[[float, str], None]] = None
    if progress:
        def inner_progress(frac: float, fl: str) -> None:
            progress(frac, flores_to_disp.get(fl, fl))

    by_flores: Dict[str, List[str]] = {}
    if flores_targets:
        by_flores = _nllb_batch_texts_multi(
            list(seg_texts), src_flores, flores_targets, cancel_check, inner_progress)

    out: Dict[str, List[str]] = {}
    for disp in targets:
        if disp in passthrough:
            raw = list(seg_texts)
        else:
            raw = by_flores.get(flores_for[disp], list(seg_texts))
        processed: List[str] = []
        for src_seg, txt in zip(seg_texts, raw):
            t = txt or src_seg
            t = localize(preserve(src_seg, t, disp), disp)
            processed.append((t or "").strip())
        if glossary_text.strip():
            processed = _apply_glossary_to_texts(processed, glossary_text)
        out[disp] = processed
    return out


def _apply_glossary_to_texts(texts: List[str], glossary_text: str) -> List[str]:
    """Apply the correction dictionary to a flat list of translated strings."""
    if not (glossary_text or "").strip():
        return texts
    try:
        from ai_processor import apply_whisper_glossary_to_transcript  # type: ignore
    except Exception:  # noqa: BLE001
        return texts
    out = []
    for t in texts:
        try:
            out.append(apply_whisper_glossary_to_transcript(t, glossary_text) if t else t)
        except Exception:  # noqa: BLE001
            out.append(t)
    return out


def translate_short(
    short: dict,
    target_language: str,
    *,
    engine: str = "nllb",
    gemini_api_key: str = "",
    split_segments: bool = True,
    glossary_text: str = "",
    cancel_check: CancelCheck = None,
) -> bool:
    """Translate a short's subtitles + title/hook in place. Returns True on success.

    `engine` is the user's preferred engine ("nllb" | "argos" | "gemini"); the
    real run order is decided by `_resolve_chain` so a missing dep or
    unsupported pair degrades gracefully instead of hard-failing.

    `glossary_text` is the Słownik poprawek — applied to the translated output for
    ALL engines so proper-name spellings survive translation.
    """
    if not short.get("words"):
        return False
    _backup_originals(short)

    last_err: Optional[Exception] = None
    for eng in _resolve_chain(engine, gemini_api_key):
        try:
            if cancel_check:
                cancel_check()
            if eng == "gemini":
                from ai_processor import translate_short_with_gemini  # type: ignore
                if translate_short_with_gemini(short, target_language, gemini_api_key):
                    _repair_untranslated_segments(short, target_language, gemini_api_key=gemini_api_key)
                    _finalize_segments(short, target_language, split_segments)
                    _apply_glossary_to_short(short, glossary_text)
                    short["translation_engine"] = "gemini"
                    return True
                continue
            if _translate_local(short, target_language, eng, cancel_check=cancel_check):
                # The repair pass re-translates "suspect" segments with a DIFFERENT,
                # better engine. After a local primary, that only helps when a Gemini
                # key is available — re-running the same local engine sentence-by-
                # sentence (12x slower per sentence) yields the same text and was a
                # pure waste multiplied across every language. Skip it when local-only.
                if gemini_api_key.strip():
                    _repair_untranslated_segments(short, target_language, gemini_api_key=gemini_api_key)
                _finalize_segments(short, target_language, split_segments)
                _apply_glossary_to_short(short, glossary_text)
                short["translation_engine"] = eng
                return True
        except Exception as e:  # noqa: BLE001
            if cancel_check:
                cancel_check()
            last_err = e
            print(f"[local_translate] silnik '{eng}' nie powiódł się: {e}")
    if last_err:
        print(f"[local_translate] wszystkie silniki zawiodły; ostatni błąd: {last_err}")
    return False
