// Thin client for the native DubCut backend (FastAPI).

export type DubConfig = Record<string, unknown>
export type StudioConfig = {
  dub: Record<string, any>
  shorts: Record<string, any>
  music: Record<string, any>
  image: Record<string, any>
  video: Record<string, any>
  app: Record<string, any>
}

export type StudioMeta = {
  shorts: {
    presets: string[]
    preset_data: Record<string, any>
    animations: string[]
    fonts: string[]
    logos: { name: string; path: string }[]
    languages: string[]
    aspect_ratios: string[]
    export_resolutions: string[]
    proxy_resolutions: string[]
    yt_qualities: string[]
    codecs: string[]
    sub_modes: string[]
    ft_strategies: string[]
    ft_trackers: string[]
    prompt_modes: string[]
  }
  dub: {
    source_languages: string[]
    target_languages: string[]
    languages: string[]
    dub_target_languages?: string[]
    translate_target_languages?: string[]
    speakers: string[]
    tts_models: string[]
    translation_models: string[]
    translation_engines: { id: string; label: string }[]
    tts_engines?: { id: string; label: string }[]
    tts_engine?: string
    voice_sources: string[]
    voice_store_modes: string[]
    mix_modes: string[]
    voiceover_engines: string[]
    clone_modes: string[]
    output_resolutions: string[]
    yt_qualities: string[]
  }
  music: {
    models: string[]
    default_model: string
    problematic_models: Record<string, string>
    formats: string[]
    variants: number[]
    languages: string[]
    language_labels: Record<string, string>
    bpm_options: string[]
    bpm_labels: Record<string, string>
    key_scale_options: string[]
    key_scale_labels: Record<string, string>
    time_signature_options: string[]
    time_signature_labels: Record<string, string>
    vocal_types: string[]
    vocal_type_labels: Record<string, string>
  }
  image: {
    models: Record<string, string>
    resolutions: Record<string, Record<string, [number, number]>>
    styles: Record<string, string>
  }
  video: {
    models: Record<string, string>
    resolutions: Record<string, [number, number]>
    durations: Record<string, number>
    fps: number[]
  }
  voices: { id: string; label: string; path: string }[]
}

export type VideogenStatus = {
  engine_available: boolean
  engine_dir: string
  uv_available: boolean
  ltx_models_ok: boolean
  mflux_ok: boolean
}

export type MediaItem = { file_name: string; url: string; path?: string }
export type ImageHistoryItem = { file_name: string; prompt: string; model: string; seed: number; created_at: string; url: string; path?: string; settings?: Record<string, any> }
export type VideoHistoryItem = { file_name: string; prompt: string; mode: string; seed: number; created_at: string; url: string; path?: string; settings?: Record<string, any> }

export type StorageCategory = { key: string; label: string; path: string; bytes: number; count: number; clearable: boolean }
export type StorageUsage = { data_dir: string; config_dir?: string; categories: StorageCategory[]; total_bytes: number }

export type ModelEntry = { key: string; label: string; path: string; bytes: number; deletable: boolean; required?: boolean; sublabel?: string }
export type ModelList = { models: ModelEntry[]; total_bytes: number }

export type MusicEngineStatus = {
  state: 'ready' | 'loading' | 'stopped'
  running: boolean
  loaded_model: string | null
  models_initialized: boolean
  engine_available: boolean
  engine_dir: string
  uv_available: boolean
  error: string
}

export type SystemStats = {
  cpu: number | null
  ram: number | null
  ram_used_gb: number | null
  ram_total_gb: number | null
  gpu: number | null
}

export type MusicTrack = { file_name: string; url: string }
export type MusicHistoryItem = { file_name: string; title: string; prompt: string; created_at: string; url: string; path?: string; settings?: Record<string, any> }
export type TtsHistoryItem = {
  id: string
  title: string
  language: string
  created_at: string
  text?: string
  mp3: boolean
  wav: boolean
  mp3_url?: string
  wav_url?: string
  path?: string
  settings?: Record<string, any>
}

export type HealthDeps = {
  python: boolean
  torch: boolean
  torchaudio: boolean
  torchcodec: boolean
  whisper: boolean
  gemini: boolean
  yt_dlp: boolean
  opencv: boolean
  ultralytics: boolean
  pillow: boolean
  demucs: boolean
  transformers: boolean
  accelerate: boolean
  sentencepiece: boolean
  soundfile: boolean
  qwen_tts: boolean
  omnivoice: boolean
  ffmpeg: boolean
  nllb: boolean
  argos: boolean
  uv: boolean
  ace_step: boolean
  videogen: boolean
}

export type SystemHealth = {
  ok: boolean
  ffmpeg: boolean
  python: boolean
  python_version?: string
  data_path?: string
  external_disk?: boolean
  data_disk_mounted?: boolean
  disk_name?: string
  disk_free_gb?: number
  disk_total_gb?: number
  disk_free_pct?: number
  low_disk?: boolean
  warnings: string[]
}

export type Health = {
  ok: boolean
  version: string
  shorts_dir: string
  dub_dir: string
  data_dir: string
  deps: HealthDeps
  system?: SystemHealth
}

export type JobSummary = {
  id: string
  kind: string
  status: 'running' | 'done' | 'error' | 'cancelled'
  progress: number
  error?: string | null
  created: number
  finished?: number | null
}

export type JobEvent = {
  ts?: number
  seq?: number
  type: string
  level?: string
  message?: string
  value?: number
  result?: unknown
  status?: string
  trace?: string
}

let BASE = 'http://127.0.0.1:8765'
export function setBase(url: string) {
  if (url) BASE = url.replace(/\/$/, '')
}
export function getBase() {
  return BASE
}

export function logoUrl(path?: string) {
  if (!path) return ''
  const file = path.split('/').pop()
  return file ? `${BASE}/api/logos/${encodeURIComponent(file)}` : ''
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    // Surface FastAPI's `detail` so the UI shows the real reason (e.g. missing API key).
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => j<Health>('/api/health'),
  getConfig: () => j<StudioConfig>('/api/config'),
  saveConfig: (patch: Partial<StudioConfig>) =>
    j<StudioConfig>('/api/config', { method: 'POST', body: JSON.stringify(patch) }),
  meta: () => j<StudioMeta>('/api/meta'),
  shortsPreview: (settings: Record<string, unknown>) =>
    j<{ url: string }>('/api/shorts/preview', { method: 'POST', body: JSON.stringify({ settings }) }),
  analyzeShorts: (body: { input_method: string; source: string; settings?: Record<string, unknown>; force_transcribe?: boolean }) =>
    j<{ job_id: string }>('/api/shorts/analyze', { method: 'POST', body: JSON.stringify(body) }),
  prepareManualShort: (body: { input_method: string; source: string; settings?: Record<string, unknown>; force_transcribe?: boolean }) =>
    j<{ job_id: string }>('/api/shorts/prepare-manual', { method: 'POST', body: JSON.stringify(body) }),
  runDub: (body: { source: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/dub/run', { method: 'POST', body: JSON.stringify(body) }),
  dubAnalyze: (body: { source: string; settings?: Record<string, unknown>; force?: boolean }) =>
    j<{ job_id: string }>('/api/dub/analyze', { method: 'POST', body: JSON.stringify(body) }),
  dubTranslate: (body: { session: string; segments: any[]; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/dub/translate', { method: 'POST', body: JSON.stringify(body) }),
  dubRender: (body: { session: string; segments: any[]; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/dub/render', { method: 'POST', body: JSON.stringify(body) }),
  dubSubtitles: (body: { session: string; segments: any[]; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/dub/subtitles', { method: 'POST', body: JSON.stringify(body) }),
  dubSubtitlesBatch: (body: { session: string; segments: any[]; languages: string[]; include_original?: boolean; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/dub/subtitles-batch', { method: 'POST', body: JSON.stringify(body) }),
  dubSaveTranscript: (body: { session: string; segments: any[]; words?: any[] }) =>
    j<{ ok: boolean; saved: number }>('/api/dub/transcript', { method: 'POST', body: JSON.stringify(body) }),
  dubPeaks: (session: string, start: number, end: number, buckets: number) =>
    j<{ start: number; end: number; peaks: number[] }>(`/api/dub/peaks?session=${encodeURIComponent(session)}&start=${start.toFixed(3)}&end=${end.toFixed(3)}&buckets=${buckets}`),
  subsProjects: () => j<{
    id: string; title: string; created_at: number;
    source_lang?: string; source_exists?: boolean; source_url?: string | null;
    is_youtube?: boolean; source?: string;
    original_segments?: { id: number; start: number; end: number; text: string }[];
    versions: { language: string; srt_url?: string; vtt_url?: string }[];
  }[]>('/api/subs/projects'),
  deleteSubsProject: (id: string) => j<{ ok: boolean }>(`/api/subs/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  deleteSubsVersion: (id: string, language?: string) =>
    j<{ ok: boolean; removed: number }>(`/api/subs/projects/${encodeURIComponent(id)}/versions${language ? `?language=${encodeURIComponent(language)}` : ''}`, { method: 'DELETE' }),
  ttsTranslate: (body: { text: string; target_lang: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/tts/translate', { method: 'POST', body: JSON.stringify(body) }),
  ttsGenerate: (body: { text: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/tts/generate', { method: 'POST', body: JSON.stringify(body) }),
  omnivoiceStatus: () =>
    j<{ ready: boolean; venv: string; python: string; uv_available: boolean; model_cached: boolean; model_id: string }>('/api/omnivoice/status'),
  omnivoiceInstall: () =>
    j<{ job_id: string }>('/api/omnivoice/install', { method: 'POST' }),
  ttsHistory: () => j<TtsHistoryItem[]>('/api/tts/history'),
  deleteTtsHistory: (id: string) =>
    j<{ ok: boolean }>(`/api/tts/history/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  renderShort: (body: { project_id: string; index: number; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/shorts/render', { method: 'POST', body: JSON.stringify(body) }),
  translateShort: (body: { project_id: string; index: number; language: string }) =>
    j<{ job_id: string }>('/api/shorts/translate', { method: 'POST', body: JSON.stringify(body) }),
  // Translate the BASE short's burned subtitles + metadata into another language,
  // keep the original speech, render a new clip → appears below as a version.
  translateShortSubtitles: (projectId: string, index: number, body: { subtitle_language: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/translate-subtitles`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  renderShortDub: (body: { project_id: string; index: number; language: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>('/api/shorts/render-dub', { method: 'POST', body: JSON.stringify(body) }),
  renderShortVersion: (projectId: string, index: number, languageSlug: string, body: { settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}/render`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  translateVersionSubtitles: (projectId: string, index: number, languageSlug: string, body: { subtitle_language: string; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}/translate-subtitles`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  translateVersionSubtitlesBatch: (projectId: string, index: number, languageSlug: string, body: { subtitle_languages: string[]; settings?: Record<string, unknown> }) =>
    j<{ job_id: string }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}/translate-subtitles-batch`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  clearShortDemucsCache: (projectId: string, index: number) =>
    j<{ ok: boolean; removed: number }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/demucs-cache`, {
      method: 'DELETE',
    }),
  clearShortTranslationCache: (projectId: string, index: number) =>
    j<{ ok: boolean; removed: number }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/translation-cache`, {
      method: 'DELETE',
    }),
  shortVersions: (projectId: string, index: number) =>
    j<any[]>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions`),
  dubProjects: () => j<{ id: string; title: string; language: string; mix_mode: string; created_at: number; video_url: string; subtitle_url: string }[]>('/api/dub/projects'),
  deleteDubProject: (id: string) => j<{ ok: boolean }>(`/api/dub/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  voiceSamples: () => j<{ id: string; label: string; path: string }[]>('/api/shorts/voice-samples'),
  renameVoiceSample: (path: string, label: string) =>
    j<{ ok: boolean; path: string; label: string }>('/api/shorts/voice-samples/rename', {
      method: 'POST', body: JSON.stringify({ path, label }),
    }),
  deleteVoiceSample: (path: string) =>
    j<{ ok: boolean; path: string }>('/api/shorts/voice-samples/delete', {
      method: 'POST', body: JSON.stringify({ path }),
    }),
  updateShort: (projectId: string, index: number, body: { segments: any[]; words: any[] }) =>
    j<{ ok: boolean; short: any }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  deleteShort: (projectId: string, index: number) =>
    j<{ ok: boolean; shorts_count: number }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}`, {
      method: 'DELETE',
    }),
  updateShortScenes: (projectId: string, index: number, body: { segments: any[]; restore?: boolean }) =>
    j<{ ok: boolean; short: any }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/scenes`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  createCustomShort: (projectId: string, body: { segments: any[]; title?: string }) =>
    j<{ ok: boolean; index: number; short: any }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/custom`, {
      method: 'POST', body: JSON.stringify(body),
    }),
  updateShortVersion: (projectId: string, index: number, languageSlug: string, body: { segments: any[]; words: any[] }) =>
    j<{ ok: boolean; short: any }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}`, {
      method: 'PUT', body: JSON.stringify(body),
    }),
  deleteShortVersion: (projectId: string, index: number, languageSlug: string) =>
    j<{ ok: boolean }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}`, {
      method: 'DELETE',
    }),
  saveFavoriteClip: (body: {
    favorite_id: string;
    kind: 'short' | 'version';
    project_id: string;
    index: number;
    language_slug?: string;
    project_title?: string;
    total?: number;
    short?: any;
  }) =>
    j<{ ok: boolean; folder: string; copied: Record<string, string> }>('/api/shorts/favorites', {
      method: 'POST', body: JSON.stringify(body),
    }),
  deleteFavoriteClip: (favoriteId: string) =>
    j<{ ok: boolean }>(`/api/shorts/favorites/${encodeURIComponent(favoriteId)}`, { method: 'DELETE' }),
  systemStats: () => j<SystemStats>('/api/system/stats'),
  storageUsage: () => j<StorageUsage>('/api/storage/usage'),
  storageCleanup: (target: string) =>
    j<{ ok: boolean; freed_bytes: number; removed: number }>('/api/storage/cleanup', {
      method: 'POST', body: JSON.stringify({ target }),
    }),
  modelsList: () => j<ModelList>('/api/models/list'),
  deleteModel: (path: string) =>
    j<{ ok: boolean; freed_bytes: number }>('/api/models/delete', { method: 'POST', body: JSON.stringify({ path }) }),
  videogenStatus: () => j<VideogenStatus>('/api/videogen/status'),
  imageGenerate: (settings: Record<string, unknown>) =>
    j<{ job_id: string }>('/api/image/generate', { method: 'POST', body: JSON.stringify({ settings }) }),
  videoGenerate: (settings: Record<string, unknown>) =>
    j<{ job_id: string }>('/api/video/generate', { method: 'POST', body: JSON.stringify({ settings }) }),
  videogenEnhance: (text: string, kind: 'image' | 'video') =>
    j<{ job_id: string }>('/api/videogen/enhance', { method: 'POST', body: JSON.stringify({ text, kind }) }),
  videogenTranslate: (text: string, kind: 'image' | 'video') =>
    j<{ job_id: string }>('/api/videogen/translate', { method: 'POST', body: JSON.stringify({ text, kind }) }),
  imageHistory: () => j<ImageHistoryItem[]>('/api/image/history'),
  videoHistory: () => j<VideoHistoryItem[]>('/api/video/history'),
  deleteImageHistory: (filename: string) =>
    j<{ ok: boolean }>(`/api/image/history/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  deleteVideoHistory: (filename: string) =>
    j<{ ok: boolean }>(`/api/video/history/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  musicStatus: () => j<MusicEngineStatus>('/api/music/status'),
  musicEstimate: (lyrics: string) =>
    j<{ min: number; max: number; suggested: number; words: number }>(`/api/music/estimate?lyrics=${encodeURIComponent(lyrics)}`),
  musicLoad: (model?: string) =>
    j<{ job_id: string }>('/api/music/load', { method: 'POST', body: JSON.stringify({ model }) }),
  musicUnload: () => j<{ ok: boolean }>('/api/music/unload', { method: 'POST', body: JSON.stringify({}) }),
  musicGenerate: (settings: Record<string, unknown>) =>
    j<{ job_id: string }>('/api/music/generate', { method: 'POST', body: JSON.stringify({ settings }) }),
  musicHistory: () => j<MusicHistoryItem[]>('/api/music/history'),
  deleteMusicHistory: (filename: string) =>
    j<{ ok: boolean }>(`/api/music/history/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  jobStatus: (id: string) => j<any>(`/api/jobs/${id}`),
  jobsList: () => j<JobSummary[]>('/api/jobs'),
  cancelJob: (id: string) => j<any>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  mergeShortScenes: (projectId: string, index: number) =>
    j<{ ok: boolean; short: any; before: number; after: number }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/merge-scenes`, { method: 'POST' }),
  mergeVersionScenes: (projectId: string, index: number, languageSlug: string) =>
    j<{ ok: boolean; short: any; before: number; after: number }>(`/api/shorts/projects/${encodeURIComponent(projectId)}/shorts/${index}/versions/${encodeURIComponent(languageSlug)}/merge-scenes`, { method: 'POST' }),
  addVoiceSample: (path: string, label?: string) =>
    j<{ ok: boolean; path: string; label: string; voices: { id: string; label: string; path: string }[] }>('/api/shorts/voice-samples/add', { method: 'POST', body: JSON.stringify({ path, label }) }),
  voiceSampleAudioUrl: (path: string) => `${BASE}/api/shorts/voice-samples/audio?path=${encodeURIComponent(path)}`,
  shortsProjects: () => j<ShortsProject[]>('/api/shorts/projects'),
  shortsProject: (id: string) => j<any>(`/api/shorts/projects/${encodeURIComponent(id)}`),
  shortsPeaks: (id: string, start: number, end: number, buckets: number) =>
    j<{ start: number; end: number; peaks: number[] }>(
      `/api/shorts/projects/${encodeURIComponent(id)}/peaks?start=${start.toFixed(3)}&end=${end.toFixed(3)}&buckets=${buckets}`,
    ),
  deleteShortsProject: (id: string) =>
    j<{ ok: boolean }>(`/api/shorts/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  shortsDownloads: () => j<ShortsDownload[]>('/api/shorts/downloads'),
  deleteShortsDownload: (videoId: string) =>
    j<{ ok: boolean; removed: number; in_use?: boolean; projects?: string[] }>('/api/shorts/downloads/delete', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId }),
    }),
}

export type ShortsDownload = {
  video_id: string
  title: string
  url: string
  quality: string
  has_transcript: boolean
  size: number
  mtime: number
}

export type ShortsProject = {
  id: string
  display_name: string
  shorts_count: number
  created: number
  video_file: string
}

// Subscribe to a job's SSE event stream. Returns an unsubscribe fn.
export function streamJob(id: string, onEvent: (e: JobEvent) => void): () => void {
  const es = new EventSource(`${BASE}/api/jobs/${id}/events`)
  es.onmessage = (m) => {
    try {
      const e = JSON.parse(m.data) as JobEvent
      onEvent(e)
      if (e.type === 'end') es.close()
    } catch {
      /* ignore malformed */
    }
  }
  es.onerror = () => es.close()
  return () => es.close()
}
