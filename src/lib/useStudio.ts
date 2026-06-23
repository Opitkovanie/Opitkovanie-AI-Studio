import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setBase, type Health, type StudioConfig, type StudioMeta, type SystemStats } from './api'
import type { BackendStatus } from '../types/dubcut'
import { useJob } from './useJob'

// Local fallback so the UI is fully explorable even before the backend is installed.
// Mirrors backend/config_store.py DEFAULTS.
const FALLBACK_CONFIG: StudioConfig = {
  dub: {
    input_method: 'Lokalny plik', yt_quality: '1080p',
    source_lang: 'Automatyczne wykrywanie', target_lang: 'Niemiecki',
    voice_source: 'Głos z oryginalnego filmu', voice_store_mode: 'Próbki własne',
    selected_voice_id: '', dubbing_qwen_speaker: 'Aiden', text_tts_speaker: 'Ryan',
    ref_audio_length: 12, clone_mode: 'Strict Voice Clone (stabilniejsza barwa głosu)',
    tts_model: '1.7B (Wysoka jakość)', use_fp16: false, whisper_precise: true,
    mix_mode: 'Czysty dubbing (usuń oryginalny głos)',
    voiceover_tts_engine: 'Qwen TTS (eksperymentalny, naturalniejszy)', voiceover_style: '',
    dub_vol: 1.5, bg_music_vol: 1.4, voiceover_original_vol: 0.85, voiceover_duck_amount: 0.95,
    auto_sync: true, auto_min_tempo: false, auto_max_tempo: true,
    sync_min_tempo: 0.9, sync_max_tempo: 1.5, pitch_adj: 0.0,
    keep_bg: true, demucs_shifts: 2, ambient_vol: 0.0, ambient_eq_enabled: false,
    ambient_eq_hp: 200, ambient_eq_presence: 4.0, ambient_eq_lpf_speech: 3500,
    translation_model: 'Gemini 2.5 Flash (Lokalizacja 2-Etapowa)', proper_name_glossary: '',
    output_resolution: 'Auto (jak oryginał)', output_bitrate_mbps: 5.0,
  },
  shorts: {
    input_method: 'Lokalny plik', yt_quality: '1080p', use_yt_subs: false,
    whisper_lang: 'Auto-detekcja', target_lang: 'Brak (Oryginał)',
    shorts_count: 10, duration_min: 45, duration_max: 90,
    prompt_mode: 'Precyzyjna (Domyślna - bardziej restrykcyjna)', custom_prompt_text: '',
    whisper_glossary: '',
    aspect_ratio: '9:16 (Pionowy - Short/TikTok)', fill_mode: 100,
    blur_bg: false, blur_sigma: 35, blur_zoom: 1.1, blur_bright: 60,
    export_resolution: '1080p', export_codec: 'H.264 (Większa kompatybilność)', export_bitrate: 15,
    use_proxy: true, proxy_res: '1080p', proxy_bitrate: 15,
    audio_mode: 'Czysty dubbing (usuń oryginalny głos)',
    dub_target_lang: 'Angielski',
    dub_auto_subtitles: true,
    dub_keep_background: true,
    dub_voice_source: 'Głos z oryginalnego filmu',
    dub_selected_voice_path: '',
    dub_qwen_speaker: 'Aiden',
    dub_ref_audio_length: 12,
    dub_sync_min_tempo: 0.9,
    dub_sync_max_tempo: 1.0,
    dub_auto_min_tempo: false,
    dub_auto_max_tempo: true,
    dub_original_volume: 0.85,
    dub_background_volume: 1.4,
    dub_voice_volume: 1.5,
    dub_duck_amount: 0.95,
    dub_pitch_adjust: 0,
    dub_style_prompt: '',
    face_tracking: false, ft_strategy: 'Główny mówca (Skupia na największej twarzy)',
    ft_zoom: 1.0, ft_y_offset: 0, ft_smoothness: 60, ft_recheck: 8, cam_blur_bg: false,
    enable_subtitles: true, sub_preset: 'MrBeast Clean Hook', custom_font: 'Domyślna dla presetu',
    sub_bcolor: '#FFFFFF', sub_hcolor: '#FFD700', sub_out_color: '#000000', sub_shad_color: '#000000',
    sub_mode: 'highlight', sub_words: 3, sub_animation: 'Brak', sub_size: 32, sub_hsize: 38,
    sub_out_thick: 3, sub_shad_size: 2, sub_margin: 600, sub_bg_pad: 45,
    sub_bold: true, sub_italic: false, sub_upper: true, sub_punct: true, sub_autoscale: false,
    enable_logo: true, logo_path: 'workspace/logo.png', logo_scale: 45, logo_x: 2, logo_y: 4, logo_opacity: 100,
    enable_text: true, wm_text: 'SUBSCRIBE', wm_font: 'funzone-two-serif-bold.ttf', wm_size: 65,
    wm_color: '#ffe900', wm_x: 4, wm_y: 20, wm_opacity: 100, wm_out_color: '#000000',
    wm_out_thick: 10, wm_shad_color: '#ef0808', wm_shad_size: 10, wm_bold: true, wm_italic: false,
  },
  music: {
    title: 'Nowy utwór',
    prompt: 'energetic indie pop, warm live drums, bright guitars, emotional male vocal, polished radio mix',
    lyrics: '[Verse]\nPiszę melodię w ciszy dnia\nMiasto oddycha, rytm już zna\n\n[Chorus]\nNiech ten dźwięk prowadzi nas\nPrzez zielony, nocny blask',
    model: 'acestep-v15-turbo', language: 'unknown', duration: 120, audio_format: 'mp3',
    bpm_choice: 'auto', key_scale_choice: 'auto', time_signature_choice: 'auto', seed: '',
    vocal_type: 'male', variant_count: 2, inference_steps: 8, guidance_scale: 7.0,
    instrumental: false, thinking: true, auto_unload: true,
  },
  image: {
    model: 'dhairyashil/FLUX.1-schnell-mflux-4bit', model_label: 'FLUX.1 Schnell MFLUX Q4 - publiczny',
    format: 'Square', resolution_label: '1024 x 1024 - standard', width: 1024, height: 1024,
    steps: 4, guidance_enabled: false, guidance: null, low_ram: true, mlx_cache_limit_gb: 12,
    seed: 42, seed_random: true, batch_count: 1, style_label: 'Bez stylu', style_suffix: '',
    prompt: '', negative_prompt: '', image_to_image: false, image_path: '', image_strength: 0.6,
  },
  video: {
    model_label: 'LTX 2.3 Q4 - stabilny dla 24 GB', model: '', mode: 'Text to video',
    resolution_label: 'Fast preview 512 x 320', width: 512, height: 320,
    duration_label: '4 s', duration: 4.0, fps: 24.0, steps: 8, seed: 42, seed_random: true,
    prompt: '', audio_enabled: true, sound_prompt: '', spoken_text: '', image_path: '',
  },
  app: {
    theme: 'dubcut-dark', device: 'auto', gemini_api_key: '', ace_dir: '',
    glossary:
      'Humsieng -> Humsienk\nHumsienka -> Humsienk\nHumsinga -> Humsienk\nHumsięk -> Humsienk\nHumsing -> Humsienk\nHumsienk\nAmfropik -> Anthropic\nAmthropic -> Anthropic\nAmphropic -> Anthropic\nAnthropic\nOpen AI -> OpenAI\nOpenAI\nChat GPT -> ChatGPT\nChatGPT\nOpitkowanie -> Opitkovanie\nOpitkovanie\nAll Powers -> AllPowers\nAllPowers\nClaude Code\nCodex\nGemini\nWhisper\nQwen\nLiFePO4\nWattCycle\nLiThink\nLeething -> LiThink\nLeeting -> LiThink\nLitinka -> LiThink\nkodeksa -> Codex\nklotkot -> Claude Code\nExplorovanie\nCaravaning Ireland',
  },
}

const FALLBACK_PRESET_DATA: Record<string, any> = {
  'Hormozi (Classic)': { font_name: 'Montserrat-ExtraBold', font_size: 30, base_color: '#FFFFFF', highlight_color: '#00FF00', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 0, bold: true, italic: false, uppercase: true, highlight_size: 35, words_per_block: 2, mode: 'highlight', remove_punctuation: true, animation: 'none' },
  'MrBeast Clean Hook': { font_name: 'Montserrat-ExtraBold', font_size: 32, base_color: '#FFFFFF', highlight_color: '#FFD700', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 2, bold: true, italic: false, uppercase: true, highlight_size: 38, words_per_block: 3, mode: 'highlight', remove_punctuation: true, animation: 'none' },
  'Beasty (Loud)': { font_name: 'Arial', font_size: 34, base_color: '#FFFFFF', highlight_color: '#FF0000', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 3, bold: true, italic: false, uppercase: true, highlight_size: 40, words_per_block: 3, mode: 'highlight', remove_punctuation: true, animation: 'none' },
  'Word Killer (TikTok)': { font_name: 'Impact', font_size: 38, base_color: '#FFFFFF', highlight_color: '#FF0000', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 3, bold: true, italic: false, uppercase: true, highlight_size: 45, words_per_block: 1, mode: 'word_by_word', remove_punctuation: true, animation: 'none' },
  'Rapid Fire (Sprint)': { font_name: 'Impact', font_size: 36, base_color: '#FFFFFF', highlight_color: '#FFFF00', outline_color: '#000000', outline_thickness: 2, shadow_color: '#000000', shadow_size: 2, bold: true, italic: true, uppercase: true, highlight_size: 42, words_per_block: 1, mode: 'word_by_word', remove_punctuation: true, animation: 'none' },
  'Neon Cyber': { font_name: 'Arial', font_size: 30, base_color: '#FF00FF', highlight_color: '#00FFFF', outline_color: '#FFFFFF', outline_thickness: 1, shadow_color: '#000000', shadow_size: 3, bold: true, italic: false, uppercase: true, highlight_size: 36, words_per_block: 2, mode: 'highlight', remove_punctuation: true, animation: 'none' },
  'Ali Abdaal (Minimal)': { font_name: 'ProximaNova-Bold', font_size: 28, base_color: '#FFFFFF', highlight_color: '#FFD700', outline_color: '#000000', outline_thickness: 1, shadow_color: '#000000', shadow_size: 2, bold: false, italic: false, uppercase: false, highlight_size: 30, words_per_block: 4, mode: 'highlight', remove_punctuation: false, animation: 'none' },
  'Iman Gadzhi (Elegant)': { font_name: 'Times New Roman', font_size: 34, base_color: '#FFFFFF', highlight_color: '#E6C200', outline_color: '#000000', outline_thickness: 2, shadow_color: '#000000', shadow_size: 1, bold: true, italic: true, uppercase: false, highlight_size: 34, words_per_block: 2, mode: 'highlight', remove_punctuation: true, animation: 'none' },
  'Gaming / GTA (Action)': { font_name: 'Pricedown', font_size: 42, base_color: '#FFFFFF', highlight_color: '#FF00FF', outline_color: '#000000', outline_thickness: 4, shadow_color: '#000000', shadow_size: 4, bold: true, italic: true, uppercase: true, highlight_size: 50, words_per_block: 1, mode: 'word_by_word', remove_punctuation: true, animation: 'none' },
  'Cinematic Story (Netflix)': { font_name: 'Arial', font_size: 24, base_color: '#FFFFFF', highlight_color: '#FFFFFF', outline_color: '#000000', outline_thickness: 0, shadow_color: '#000000', shadow_size: 2, bold: false, italic: false, uppercase: false, highlight_size: 24, words_per_block: 7, mode: 'highlight', remove_punctuation: false, animation: 'none' },
  'Viral Spring Pop (Wyskakiwanie)': { font_name: 'Montserrat-ExtraBold', font_size: 35, base_color: '#FFFFFF', highlight_color: '#00FF00', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 0, bold: true, italic: false, uppercase: true, highlight_size: 35, words_per_block: 1, mode: 'word_by_word', remove_punctuation: true, animation: 'spring' },
  'Smooth Karaoke (Płynne kolorowanie)': { font_name: 'Montserrat-ExtraBold', font_size: 30, base_color: '#FFFFFF', highlight_color: '#FFD700', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 2, bold: true, italic: false, uppercase: true, highlight_size: 30, words_per_block: 3, mode: 'highlight', remove_punctuation: true, animation: 'karaoke' },
  'Podcast (Build-up)': { font_name: 'Montserrat-ExtraBold', font_size: 32, base_color: '#FFFFFF', highlight_color: '#FFFF00', outline_color: '#000000', outline_thickness: 3, shadow_color: '#000000', shadow_size: 0, bold: true, italic: false, uppercase: false, highlight_size: 32, words_per_block: 4, mode: 'build_up', remove_punctuation: false, animation: 'none' },
  'Cinematic (Blur)': { font_name: 'Arial', font_size: 28, base_color: '#FFFFFF', highlight_color: '#FFFFFF', outline_color: '#000000', outline_thickness: 1, shadow_color: '#000000', shadow_size: 2, bold: false, italic: false, uppercase: false, highlight_size: 28, words_per_block: 5, mode: 'highlight', remove_punctuation: false, animation: 'blur_reveal' },
  'Neon Pulse': { font_name: 'Arial', font_size: 34, base_color: '#FFFFFF', highlight_color: '#FF00FF', outline_color: '#000000', outline_thickness: 2, shadow_color: '#FF00FF', shadow_size: 4, bold: true, italic: true, uppercase: true, highlight_size: 36, words_per_block: 2, mode: 'highlight', remove_punctuation: true, animation: 'color_pulse' },
  'CapCut (Tło słowa)': { font_name: 'Montserrat-ExtraBold', font_size: 32, base_color: '#FFFFFF', highlight_color: '#FF00FF', outline_color: '#000000', outline_thickness: 0, shadow_color: '#000000', shadow_size: 1, bold: true, italic: false, uppercase: false, highlight_size: 32, words_per_block: 4, mode: 'highlight_box', remove_punctuation: false, animation: 'none', bg_padding: 45 },
}

const FALLBACK_META: StudioMeta = {
  shorts: {
    presets: [
      'Hormozi (Classic)', 'MrBeast Clean Hook', 'Beasty (Loud)', 'Word Killer (TikTok)',
      'Rapid Fire (Sprint)', 'Neon Cyber', 'Ali Abdaal (Minimal)', 'Iman Gadzhi (Elegant)',
      'Gaming / GTA (Action)', 'Cinematic Story (Netflix)', 'Viral Spring Pop (Wyskakiwanie)',
      'Smooth Karaoke (Płynne kolorowanie)', 'Podcast (Build-up)', 'Cinematic (Blur)',
      'Neon Pulse', 'CapCut (Tło słowa)',
    ],
    preset_data: FALLBACK_PRESET_DATA,
    animations: [
      'Brak', 'Wyskakiwanie (Spring Pop)', 'Płynne Karaoke', 'Trzęsienie (Jiggle)',
      'Wyłanianie (Blur Reveal)', 'Nalot (Zoom In)', 'Pulsowanie (Color Pulse)', 'Wjazd 3D (Slide Up)',
    ],
    fonts: [
      'aircut-onehundedandone.ttf', 'Arial', 'arial.ttf', 'base-neue-super-exp-extbd-obliq.ttf',
      'boltonoutline.ttf', 'buttercookie-regular.otf', 'calibri-bold-italic.ttf', 'calibri-bold.ttf',
      'calibri-italic.ttf', 'calibri-regular.ttf', 'camarine-extruderight-demo.otf', 'christmas-blood.ttf',
      'Consolas', 'erbosdraco-nova-nbp-regular.ttf', 'fat-chicken.ttf', 'frosty-faktur-deco.ttf',
      'funzone-two-serif-bold.ttf', 'golden-dragon-shadow.ttf', 'Impact', 'Impact.ttf',
      'lt-binary-neue-round-black.ttf', 'milford-hollow.ttf', 'Montserrat-ExtraBold.ttf',
      'montserrat-regular.ttf', 'onedirection.ttf', 'Pricedown Bl.otf', 'pricedown.ttf',
      'PricedownBl-Regular.ttf', 'Proxima-Nova-Bold.ttf', 'pwjoyeuxnoel.ttf', 'roadpixel.ttf',
      'roboto-black.ttf', 'second-hand-campus.ttf', 'splinterwood.ttf', 'swirlvetica.ttf',
      'the-rock.ttf', 'times new roman bold italic.ttf', 'times new roman bold.ttf',
      'times new roman italic.ttf', 'times new roman.ttf', 'timesnewromanps_italicmt.ttf',
      'timesnewromanpsmt.ttf', 'winter-dairy-shiny.ttf',
    ],
    logos: [
      { name: 'logo.png', path: 'workspace/logo.png' },
      { name: 'Opikovanie Sub 1.png', path: 'logo/Opikovanie Sub 1.png' },
      { name: 'Opitkovanie Sub 2.png', path: 'logo/Opitkovanie Sub 2.png' },
      { name: 'Opitkovanie Sub 3.png', path: 'logo/Opitkovanie Sub 3.png' },
      { name: 'Opitkovanie Sub 4.png', path: 'logo/Opitkovanie Sub 4.png' },
      { name: 'Opitkovanie Sub 5.png', path: 'logo/Opitkovanie Sub 5.png' },
      { name: 'opitkovanie1k.png', path: 'logo/opitkovanie1k.png' },
      { name: 'subscribe-button-33246.png', path: 'logo/subscribe-button-33246.png' },
      { name: 'subscribe-logo-33309.png', path: 'logo/subscribe-logo-33309.png' },
      { name: 'youtube-logo-png-2064.png', path: 'logo/youtube-logo-png-2064.png' },
      { name: 'youtube-logo-png-2065.png', path: 'logo/youtube-logo-png-2065.png' },
      { name: 'youtube-logo-png-2078.png', path: 'logo/youtube-logo-png-2078.png' },
      { name: 'youtube-logo-png-31793.png', path: 'logo/youtube-logo-png-31793.png' },
      { name: 'youtube-logo-png-31794.png', path: 'logo/youtube-logo-png-31794.png' },
      { name: 'youtube-logo-png-31807.png', path: 'logo/youtube-logo-png-31807.png' },
      { name: 'youtube-play-button-28308.png', path: 'logo/youtube-play-button-28308.png' },
      { name: 'YT Subskrybuj.png', path: 'logo/YT Subskrybuj.png' },
    ],
    languages: ['Auto-detekcja', 'Polski', 'Angielski', 'Niemiecki'],
    aspect_ratios: ['9:16 (Pionowy - Short/TikTok)', '16:9 (Oryginalny - YouTube)'],
    export_resolutions: ['Zgodna ze źródłem', '4K (2160p)', '2K (1440p)', '1080p', '720p', '480p'],
    proxy_resolutions: ['1080p', '720p'],
    yt_qualities: ['Najlepsza dostępna', '4K (2160p)', '2K (1440p)', '1080p', '720p', '480p'],
    codecs: ['H.264 (Większa kompatybilność)', 'H.265 / HEVC'],
    sub_modes: ['highlight', 'highlight_box', 'word_by_word', 'build_up', 'fade'],
    ft_strategies: ['Główny mówca (Skupia na największej twarzy)', 'Utrzymuj cel (Śledzi jedną wybraną twarz)'],
    ft_trackers: ['Auto (sam dobiera)', 'ByteTrack (1 osoba, najszybszy)', 'BoT-SORT (wiele osób / zasłanianie)'],
    prompt_modes: ['Precyzyjna (Domyślna - bardziej restrykcyjna)', 'Kreatywna (Więcej swobody AI)', 'Trailer / Zapowiedź', 'Własny prompt'],
  },
  dub: {
    source_languages: ['Automatyczne wykrywanie', 'Polski', 'Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Holenderski', 'Rosyjski', 'Ukraiński', 'Czeski', 'Słowacki', 'Szwedzki', 'Norweski', 'Duński', 'Fiński', 'Grecki', 'Rumuński', 'Węgierski', 'Bułgarski', 'Chorwacki', 'Serbski', 'Turecki', 'Arabski', 'Hebrajski', 'Hindi', 'Wietnamski', 'Tajski', 'Indonezyjski', 'Japoński', 'Koreański', 'Chiński'],
    target_languages: ['Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Rosyjski', 'Chiński', 'Japoński', 'Koreański'],
    languages: ['Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Rosyjski', 'Chiński', 'Japoński', 'Koreański'],
    dub_target_languages: ['Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Rosyjski', 'Chiński', 'Japoński', 'Koreański'],
    translate_target_languages: ['Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Holenderski', 'Polski', 'Rosyjski', 'Ukraiński', 'Czeski', 'Słowacki', 'Szwedzki', 'Norweski', 'Duński', 'Fiński', 'Grecki', 'Rumuński', 'Węgierski', 'Bułgarski', 'Chorwacki', 'Serbski', 'Turecki', 'Arabski', 'Hebrajski', 'Hindi', 'Wietnamski', 'Tajski', 'Indonezyjski', 'Japoński', 'Koreański', 'Chiński'],
    speakers: ['Ryan', 'Aiden', 'Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric'],
    tts_models: ['0.6B (Szybki)', '1.7B (Wysoka jakość)'],
    translation_models: ['Gemini 2.5 Flash (Lokalizacja 2-Etapowa)', 'Brak (Tylko transkrypcja)'],
    translation_engines: [
      { id: 'nllb', label: 'NLLB-200 (lokalny, najlepsza jakość)' },
      { id: 'argos', label: 'Argos (lokalny, lekki CPU)' },
      { id: 'gemini', label: 'Gemini 2.5 Flash (chmura, wymaga klucza)' },
    ],
    voice_sources: ['Głos z oryginalnego filmu', 'Sklonowany głos (własna próbka)', 'Głos presetowy (Qwen)'],
    voice_store_modes: ['Próbki własne', 'Biblioteka głosów'],
    mix_modes: ['Czysty dubbing (usuń oryginalny głos)', 'Lektor (oryginał + głos AI)', 'Lektor z duckingiem (oryginał ścisza się pod AI)'],
    voiceover_engines: ['Stabilny lektor systemowy (bez halucynacji)', 'Qwen TTS (eksperymentalny, naturalniejszy)'],
    clone_modes: ['Strict Voice Clone (stabilniejsza barwa głosu)', 'Expressive Clone (więcej ekspresji)'],
    output_resolutions: ['Auto (jak oryginał)', '480p', '720p', '1080p', '4K (2160p)'],
    yt_qualities: ['1080p', '1440p', '2160p (4K)', '720p', 'Najlepsza'],
  },
  music: {
    models: ['acestep-v15-turbo', 'acestep-v15-sft', 'acestep-v15-xl-turbo', 'acestep-v15-xl-sft'],
    default_model: 'acestep-v15-turbo',
    problematic_models: {
      'acestep-v15-xl-turbo': 'Ten model potrafi wyczerpać pamięć MPS na Macu. Zalecany: acestep-v15-turbo.',
      'acestep-v15-xl-sft': 'Ten model potrafi wyczerpać pamięć MPS na Macu. Zalecany: acestep-v15-turbo.',
      'acestep-v15-sft': 'Ten model daje tutaj metaliczne wyniki. Zalecany: acestep-v15-turbo.',
    },
    formats: ['mp3', 'wav', 'flac'],
    variants: [1, 2, 3, 4],
    languages: ['unknown', 'pl', 'en', 'de', 'fr', 'es', 'it'],
    language_labels: { unknown: 'Automatycznie', pl: 'Polski', en: 'Angielski', de: 'Niemiecki', fr: 'Francuski', es: 'Hiszpański', it: 'Włoski' },
    bpm_options: ['auto', '60', '70', '80', '90', '100', '110', '118', '120', '128', '135', '140', '150', '160', '180'],
    bpm_labels: { auto: 'Automatycznie' },
    key_scale_options: ['auto'],
    key_scale_labels: { auto: 'Automatycznie' },
    time_signature_options: ['auto', '4/4', '3/4', '2/4', '6/8'],
    time_signature_labels: { auto: 'Automatycznie', '4/4': '4/4 – najczęstsze', '3/4': '3/4 – walc / ballada', '2/4': '2/4 – marsz / proste tempo', '6/8': '6/8 – folk / kołysanie' },
    vocal_types: ['auto', 'male', 'female', 'duet'],
    vocal_type_labels: { auto: 'Automatycznie', male: 'Męski', female: 'Żeński', duet: 'Duet męski i żeński' },
  },
  image: {
    models: {
      'FLUX.1 Schnell MFLUX Q4 - publiczny': 'dhairyashil/FLUX.1-schnell-mflux-4bit',
      'FLUX.1 Dev MFLUX Q4 - publiczny, wolniej': 'dhairyashil/FLUX.1-dev-mflux-4bit',
      'FLUX.1 Krea Dev MFLUX Q4 - publiczny': 'filipstrand/FLUX.1-Krea-dev-mflux-4bit',
      'Z-Image Turbo MFLUX Q4 - szybki quality': 'filipstrand/Z-Image-Turbo-mflux-4bit',
      'FLUX.2 Klein 4B Q4 - nowy szybki': 'flux2-klein-4b',
    },
    resolutions: {
      Square: { '512 x 512 - szybki test': [512, 512], '768 x 768 - dobry podgląd': [768, 768], '1024 x 1024 - standard': [1024, 1024], '1280 x 1280 - ciężkie': [1280, 1280], '1536 x 1536 - bardzo ciężkie': [1536, 1536] },
      Wide: { '1216 x 832 - standard': [1216, 832], '1344 x 768 - cinematic': [1344, 768], '1536 x 864 - 16:9 ciężkie': [1536, 864], '1792 x 1024 - bardzo ciężkie': [1792, 1024], '2048 x 1152 - eksperymentalne': [2048, 1152] },
      Vertical: { '832 x 1216 - standard': [832, 1216], '768 x 1344 - social': [768, 1344], '864 x 1536 - 9:16 ciężkie': [864, 1536], '1024 x 1792 - bardzo ciężkie': [1024, 1792], '1152 x 2048 - eksperymentalne': [1152, 2048] },
    },
    styles: {
      'Bez stylu': '', 'Cinematic photo': 'cinematic photo, natural skin texture, realistic lighting, shallow depth of field, high detail',
      'Editorial fashion': 'editorial fashion photography, polished styling, elegant pose, soft studio lighting, magazine quality',
      'Product shot': 'premium product photography, clean composition, controlled studio lighting, sharp details, commercial finish',
      'Realistic portrait': 'realistic portrait photography, expressive face, detailed eyes, natural light, 85mm lens look',
      'Concept art': 'high-end concept art, dramatic composition, rich atmosphere, detailed environment, artstation quality',
      Anime: 'anime illustration, clean linework, expressive character design, vibrant colors, detailed background',
    },
  },
  video: {
    models: { 'LTX 2.3 Q4 - stabilny dla 24 GB': '', 'LTX 2.3 Q8 - eksperymentalna jakość': '' },
    resolutions: {
      'Fast preview 512 x 320': [512, 320], 'Square 512 x 512': [512, 512], 'Wide 640 x 384': [640, 384],
      'Wide 704 x 480': [704, 480], 'SD safe 864 x 480': [864, 480], 'HD safe 1280 x 736': [1280, 736],
      'Full HD safe 1920 x 1088': [1920, 1088], 'Vertical 384 x 640': [384, 640], 'Vertical 480 x 704': [480, 704],
      'Vertical HD safe 736 x 1280': [736, 1280], 'Vertical Full HD safe 1088 x 1920': [1088, 1920],
    },
    durations: { '0.4 s / szybki test': 0.4, '1 s': 1.0, '2 s': 2.0, '4 s': 4.0, '6 s': 6.0, '8 s': 8.0, '10 s': 10.0, '12 s': 12.0, '16 s': 16.0, '20 s': 20.0 },
    fps: [12.0, 16.0, 18.0, 24.0, 30.0],
  },
  voices: [],
}

export type LogLine = { level: string; message: string; ts: number }

export function useStudio() {
  const [status, setStatus] = useState<BackendStatus | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [config, setConfig] = useState<StudioConfig>(FALLBACK_CONFIG)
  const [meta, setMeta] = useState<StudioMeta>(FALLBACK_META)
  const [online, setOnline] = useState(false)
  const [logs, setLogs] = useState<string>('')
  const [sysStats, setSysStats] = useState<SystemStats | null>(null)
  // One debounce timer PER config section. A single shared timer dropped saves when
  // the user edited two different sections within the debounce window (the first
  // section's pending save got cancelled and only the last reached the backend).
  const saveTimers = useRef<Record<string, number>>({})
  // Sections with an in-flight/queued save. Non-empty ⇒ a reload would clobber a
  // pending local edit, so the poll guard checks this set.
  const pendingSections = useRef<Set<string>>(new Set())
  // True while any config edit is waiting to be (or is being) persisted. Guards reloads
  // from overwriting in-flight local edits.
  const pendingSave = useRef(false)
  // Config is client-authoritative — it only ever changes through this UI. We load it
  // once when the backend first becomes reachable and never let the periodic health
  // poll re-fetch + clobber it (that reset live edits like the auto-unload toggle right
  // after pressing Generate). The Settings "Odśwież" button reloads it explicitly.
  const configLoaded = useRef(false)

  // --- backend status (Electron IPC) ---
  useEffect(() => {
    if (!window.dubcut) return
    window.dubcut.getStatus().then((s) => {
      setStatus(s)
      if (s.url) setBase(s.url)
    })
    window.dubcut.getLogs().then(setLogs)
    const unsubStatus = window.dubcut.onStatus((s) => {
      setStatus(s)
      if (s.url) setBase(s.url)
    })
    const unsubLog = window.dubcut.onLog((p) => setLogs((c) => `${c}${p.line}`))
    return () => {
      unsubStatus?.()
      unsubLog?.()
    }
  }, [])

  // --- poll health + load config/meta when backend is reachable ---
  const refresh = useCallback(async (force = false) => {
    try {
      const h = await api.health()
      setHealth(h)
      setOnline(true)
      // Merge over the fallbacks so an older backend that lacks newer sections
      // (e.g. image/video) still leaves those keys intact instead of dropping them.
      setMeta({ ...FALLBACK_META, ...(await api.meta()) })
      // Load config only on first connect (or an explicit forced reload), and never
      // while a local edit is pending — so the poll can't clobber live settings.
      if ((force || !configLoaded.current) && !pendingSave.current) {
        setConfig({ ...FALLBACK_CONFIG, ...(await api.getConfig()) })
        configLoaded.current = true
      }
    } catch {
      setOnline(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const t = window.setInterval(refresh, 5000)
    return () => window.clearInterval(t)
  }, [refresh])

  // --- live CPU / RAM / GPU meters for the top bar ---
  useEffect(() => {
    if (!online) { setSysStats(null); return }
    let alive = true
    const tick = () => api.systemStats().then((s) => { if (alive) setSysStats(s) }).catch(() => {})
    tick()
    const t = window.setInterval(tick, 2000)
    return () => { alive = false; window.clearInterval(t) }
  }, [online])

  // --- update a section of config locally + debounced persist (per section) ---
  const update = useCallback(
    (section: 'dub' | 'shorts' | 'music' | 'image' | 'video' | 'app', patch: Record<string, unknown>) => {
      pendingSave.current = true
      pendingSections.current.add(section)
      setConfig((c) => {
        const next = { ...c, [section]: { ...c[section], ...patch } }
        // Debounce per SECTION so editing `music` then `app` within 400ms doesn't
        // cancel the music save — each section flushes its own latest state.
        const existing = saveTimers.current[section]
        if (existing) window.clearTimeout(existing)
        // Switching the voice or translation engine changes which dubbing/Text→Audio
        // languages are offered (server intersects TTS ∩ translation support), so refetch
        // meta after the save lands to update the language pickers live.
        const refetchMeta = section === 'app' && ('tts_engine' in patch || 'translation_engine' in patch)
        saveTimers.current[section] = window.setTimeout(() => {
          delete saveTimers.current[section]
          api.saveConfig({ [section]: next[section] } as any)
            .catch(() => {})
            .finally(() => {
              pendingSections.current.delete(section)
              pendingSave.current = pendingSections.current.size > 0
              if (refetchMeta) api.meta().then((m) => setMeta({ ...FALLBACK_META, ...m })).catch(() => {})
            })
        }, 400)
        return next
      })
    },
    [],
  )

  const install = useCallback((target: 'common' | 'shorts' | 'dubmaster' | 'all' | 'music' | 'videogen' = 'all') => window.dubcut?.install(target), [])
  const uninstall = useCallback((target: 'common' | 'shorts' | 'dubmaster' | 'all' | 'music' | 'videogen' = 'all') => window.dubcut?.uninstall(target), [])
  const openRuntime = useCallback(() => window.dubcut?.openRuntime(), [])
  const chooseVideo = useCallback(() => window.dubcut?.chooseVideo() ?? Promise.resolve(null), [])
  const chooseVoiceSample = useCallback(() => window.dubcut?.chooseVoiceSample() ?? Promise.resolve(null), [])
  const chooseImage = useCallback(() => window.dubcut?.chooseImage?.() ?? Promise.resolve(null), [])
  const openPath = useCallback((p: string) => window.dubcut?.openPath?.(p) ?? Promise.resolve(false), [])
  const revealPath = useCallback((p: string) => window.dubcut?.revealPath?.(p) ?? Promise.resolve(false), [])
  const chooseWorkDir = useCallback(() => window.dubcut?.chooseWorkDir?.() ?? Promise.resolve(null), [])

  // Long-running jobs live here, at the app root, so they survive view switches —
  // navigate away and back and the work is still streaming. (Fixes the regression
  // where leaving Shorts mid-analysis silently discarded the whole run.)
  const shortsJob = useJob('shorts.analyze')
  const shortsRenderJob = useJob('shorts.render')
  const musicGenJob = useJob('music.generate')
  const musicLoadJob = useJob('music.load')
  const imageGenJob = useJob('image.generate')
  const videoGenJob = useJob('video.generate')

  return { status, health, config, meta, online, logs, sysStats, update, install, uninstall, openRuntime, chooseVideo, chooseVoiceSample, chooseImage, openPath, revealPath, chooseWorkDir, refresh, shortsJob, shortsRenderJob, musicGenJob, musicLoadJob, imageGenJob, videoGenJob }
}
