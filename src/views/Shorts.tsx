import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePersistentState } from '../lib/usePersistentState'
import {
  Scissors, Captions, Brain, Crop, Image, Link2, Sparkles, FolderOpen,
  Clapperboard, ScanFace, Stamp, Upload, History, Trash2, X, Mic2, Copy, Check,
  Heart, Flame, Languages,
} from 'lucide-react'
import { api, getBase, type ShortsProject, type ShortsDownload } from '../lib/api'
import type { useStudio } from '../lib/useStudio'
import { useJob } from '../lib/useJob'
import type { DubcutMediaFile } from '../types/dubcut'
import { DropZone } from '../components/DropZone'
import { InlineJobProgress } from '../components/JobProgress'
import { timeTag, audioModeTag } from '../lib/jobFormat'
import { StyledPreview } from '../components/StyledPreview'
import { SubtitleEditor } from '../components/SubtitleEditor'
import { SceneEditor } from '../components/SceneEditor'
import { FontSelect } from '../components/FontSelect'
import {
  ColorField, Field, Section, Select, Slider, TextArea, Toggle,
} from '../components/ui'
import { VoiceSamplePreview, OmniVoiceParams, ttsEngineOf } from '../components/VoiceSynth'

type Studio = ReturnType<typeof useStudio>

type Short = {
  score?: number
  title?: string
  hook_text?: string
  hashtags?: string
  yt_tags?: string
  language?: string
  source_language?: string
  segments?: { start_time: number; end_time: number; text?: string }[]
  backup_segments?: { start_time: number; end_time: number; text?: string }[]
  words?: { word: string; start: number; end: number }[]
  rendered_file?: string
  rendered_at?: number
}

type ShortVersion = {
  language?: string
  language_slug?: string
  subtitle_language?: string
  created_at?: number
  updated_at?: number
  video_path?: string
  video_path_url?: string
  audio_path_url?: string
  subtitle_url?: string
  short_data?: Short
  audio_mode?: string
}

type FavoriteShort = {
  id: string
  kind?: 'short' | 'version'
  projectId: string
  projectTitle?: string
  index: number
  total: number
  languageSlug?: string
  language?: string
  short: Short
  savedAt: number
}

const SHORT_LANGS = ['Polski', 'Angielski', 'Niemiecki', 'Francuski', 'Hiszpański', 'Włoski', 'Portugalski', 'Chiński', 'Rosyjski']
const DUB_MIX_MODES = [
  'Oryginalne audio',
  'Czysty dubbing (usuń oryginalny głos)',
  'Dubbing + tło z filmu',
  'Lektor na oryginalnym audio',
]
const DUB_VOICE_SOURCES = ['Głos z oryginalnego filmu', 'Własna próbka głosu', 'Głos z bazy Qwen TTS']
const QWEN_SPEAKERS = ['Ryan', 'Aiden', 'Dylan', 'Jada', 'Sunny', 'Ethan']

export function Shorts({ studio }: { studio: Studio }) {
  const { config, meta, update, chooseVideo, chooseVoiceSample, refresh, online } = studio
  const s = config.shorts
  // Dubbing is limited to the languages Qwen3-TTS can voice; subtitle translation
  // covers the much wider NLLB set (Dutch, Nordic, etc.) since we don't need a voice.
  const dubLangs = meta.dub.dub_target_languages ?? meta.dub.target_languages ?? SHORT_LANGS
  const translateLangs = meta.dub.translate_target_languages ?? SHORT_LANGS
  const [media, setMedia] = usePersistentState<DubcutMediaFile | null>('dubcut.shorts.media', null)
  const [ytUrl, setYtUrl] = usePersistentState('dubcut.shorts.ytUrl', '')
  const [sourceMode, setSourceMode] = usePersistentState<'youtube' | 'file' | 'history' | 'favorites'>('dubcut.shorts.sourceMode', 'youtube')
  // Last 10 YouTube links, so they can be picked from a list instead of retyped.
  const [ytRecents, setYtRecents] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.ytRecents') || '[]') } catch { return [] }
  })
  const [favorites, setFavorites] = useState<FavoriteShort[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.favoriteShorts') || '[]') } catch { return [] }
  })
  const [workspaceResultVisible, setWorkspaceResultVisible] = useState(false)
  const switchSourceMode = (mode: typeof sourceMode) => {
    setSourceMode(mode)
    if (mode === 'youtube' || mode === 'file') {
      // Keep the currently open project in memory — switching to an input tab
      // and back to History/Favorites should still show it. Only generating a
      // new project (or closing it explicitly) clears it. Just hide the
      // fresh-generation result workspace here.
      setWorkspaceResultVisible(false)
    }
  }
  const rememberYt = useCallback((url: string) => {
    const u = url.trim()
    if (!u) return
    setYtRecents((prev) => {
      const next = [u, ...prev.filter((x) => x !== u)].slice(0, 10)
      try { localStorage.setItem('dubcut.ytRecents', JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [])
  // Videos already downloaded to disk — titled, click to re-run shorts from the
  // same source without re-downloading or re-transcribing.
  const [downloads, setDownloads] = useState<ShortsDownload[]>([])
  const loadDownloads = useCallback(() => {
    api.shortsDownloads().then(setDownloads).catch(() => { /* backend offline */ })
  }, [])
  const forgetDownload = async (videoId: string) => {
    if (!window.confirm('Usunąć pobrany film i jego transkrypcję z dysku?\n\nPlik jest współdzielony przez wszystkie projekty zrobione z tego wideo — usunięcie jest możliwe tylko, gdy żaden projekt go nie używa.')) return
    try {
      const res = await api.deleteShortsDownload(videoId)
      if (res && res.in_use) {
        const list = (res.projects ?? []).map((p) => `• ${p}`).join('\n')
        window.alert(`Nie usunięto — ten film jest nadal używany przez ${res.projects?.length ?? 0} projekt(ów):\n\n${list}\n\nNajpierw usuń te projekty z Historii, żeby zwolnić plik.`)
        return
      }
      setDownloads((prev) => prev.filter((d) => d.video_id !== videoId))
    } catch { /* ignore */ }
  }
  useEffect(() => {
    const u = ytUrl.trim()
    if (!/youtu\.?be|youtube\.com/i.test(u)) return
    const t = window.setTimeout(() => rememberYt(u), 900)
    return () => window.clearTimeout(t)
  }, [rememberYt, ytUrl])
  // Shared, app-level job — survives navigating to other views and back.
  const job = studio.shortsJob
  const manualJob = useJob('shorts.prepare-manual')
  // One-shot "force fresh transcription" — ignores the cached Whisper transcript for the
  // next analyze / custom-short run (YouTube or local file). Resets after it fires.
  const [forceTranscribe, setForceTranscribe] = useState(false)
  const [keyModal, setKeyModal] = useState(false)
  const [keyInput, setKeyInput] = useState('')
  const [keySaving, setKeySaving] = useState(false)

  const set = (patch: Record<string, unknown>) => update('shorts', patch)

  // Selecting a preset applies its full look (colors, sizes, flags) so the live preview matches —
  // mirrors update_preset_settings() in the original ViralCutter.
  const applyPreset = (name: string) => {
    const p = meta.shorts.preset_data[name]
    if (!p) {
      set({ sub_preset: name })
      return
    }
    set({
      sub_preset: name,
      custom_font: 'Domyślna dla presetu',
      sub_bcolor: p.base_color ?? s.sub_bcolor,
      sub_hcolor: p.highlight_color ?? s.sub_hcolor,
      sub_out_color: p.outline_color ?? s.sub_out_color,
      sub_shad_color: p.shadow_color ?? s.sub_shad_color,
      sub_size: p.font_size ?? s.sub_size,
      sub_hsize: p.highlight_size ?? s.sub_hsize,
      sub_out_thick: p.outline_thickness ?? s.sub_out_thick,
      sub_shad_size: p.shadow_size ?? s.sub_shad_size,
      sub_words: p.words_per_block ?? s.sub_words,
      sub_mode: p.mode ?? s.sub_mode,
      sub_animation: animationLabel(p.animation ?? s.sub_animation),
      sub_bold: p.bold ?? s.sub_bold,
      sub_italic: p.italic ?? s.sub_italic,
      sub_upper: p.uppercase ?? s.sub_upper,
      sub_punct: p.remove_punctuation ?? s.sub_punct,
      sub_bg_pad: p.bg_padding ?? s.sub_bg_pad,
    })
  }

  const pick = async () => {
    const file = await chooseVideo()
    if (file) setMedia(file)
  }

  const usingYt = sourceMode === 'youtube'
  const canRun = usingYt ? !!ytUrl.trim() : !!media
  const logoOptions = unique([
    s.logo_path,
    'workspace/logo.png',
    ...meta.shorts.logos.map((logo) => logo.path),
  ].filter(Boolean))

  const addLogo = async () => {
    const logo = await window.dubcut?.chooseLogo?.()
    if (logo) set({ logo_path: logo.path, logo_url: logo.url, enable_logo: true })
  }

  const addVoiceSample = async () => {
    const sample = await chooseVoiceSample()
    if (sample) {
      set({ dub_selected_voice_path: sample.path, dub_voice_source: 'Własna próbka głosu' })
      refresh()
    }
  }

  const [manageVoices, setManageVoices] = useState(false)
  const renameVoice = async (path: string, label: string) => {
    try { await api.renameVoiceSample(path, label) } catch { /* ignore */ }
    refresh()
  }
  const deleteVoice = async (path: string) => {
    try { await api.deleteVoiceSample(path) } catch { /* ignore */ }
    if (s.dub_selected_voice_path === path) set({ dub_selected_voice_path: '' })
    refresh()
  }

  const startAnalyze = (inputMethod: string, source: string) => {
    setLoaded(null)
    setHistoryPick('')
    setWorkspaceResultVisible(true)
    const force = forceTranscribe
    setForceTranscribe(false)  // one-shot: don't silently re-force on the next run
    job.start(() => api.analyzeShorts({ input_method: inputMethod, source, settings: s, force_transcribe: force }))
  }

  const doRun = () => {
    if (usingYt) rememberYt(ytUrl)
    startAnalyze(usingYt ? 'Link z YouTube' : 'Lokalny plik', usingYt ? ytUrl.trim() : media!.path)
  }

  // Manual short: download/load + transcribe (NO Gemini), then open the scene editor on
  // the full film. The finished cut is saved into the freshly-prepared project.
  const runManual = () => {
    if (!canRun) return
    if (usingYt) rememberYt(ytUrl)
    setLoaded(null)
    setHistoryPick('')
    setWorkspaceResultVisible(true)
    const force = forceTranscribe
    setForceTranscribe(false)  // one-shot
    manualJob.start(() => api.prepareManualShort({
      input_method: usingYt ? 'Link z YouTube' : 'Lokalny plik',
      source: usingYt ? ytUrl.trim() : media!.path,
      settings: s,
      force_transcribe: force,
    }))
  }

  // Pick an already-downloaded video: just load its URL into the form (source is
  // served from cache by the YouTube id — no re-download, no Whisper). It does NOT
  // auto-start anything; the user decides by clicking "Generuj shorty
  // automatycznie" or "Stwórz własnego shorta".
  const runDownloaded = (d: ShortsDownload) => {
    setSourceMode('youtube')
    setYtUrl(d.url)
    rememberYt(d.url)
  }

  const run = () => {
    if (!canRun) return
    // No key yet → ask for it up front instead of failing mid-run.
    if (!(config.app.gemini_api_key ?? '').trim()) {
      setKeyInput('')
      setKeyModal(true)
      return
    }
    doRun()
  }

  // Save the key to the system, then continue (or just save if not mid-flow).
  const saveKey = async (thenRun: boolean) => {
    const key = keyInput.trim()
    if (!key) return
    setKeySaving(true)
    try {
      await api.saveConfig({ app: { gemini_api_key: key } })
      update('app', { gemini_api_key: key })
      setKeyModal(false)
      if (thenRun && canRun) doRun()
    } finally {
      setKeySaving(false)
    }
  }

  // If a run fails because the key is missing/invalid, surface the modal so the
  // user can fix it in place rather than hunting through Settings.
  useEffect(() => {
    if (job.state.status === 'error' && /klucz api gemini|api key/i.test(job.state.error ?? '')) {
      setKeyInput((config.app.gemini_api_key ?? '').trim())
      setKeyModal(true)
    }
  }, [job.state.status, job.state.error])

  // --- project history ---
  const [projects, setProjects] = useState<ShortsProject[]>([])
  const [loaded, setLoaded] = usePersistentState<{ title?: string; shorts: Short[]; id: string } | null>('dubcut.shorts.loaded', null)
  const [historyPick, setHistoryPick] = usePersistentState('dubcut.shorts.historyPick', '')

  const refreshProjects = useCallback(() => {
    if (!online) return
    api.shortsProjects().then(setProjects).catch(() => {})
  }, [online])

  useEffect(() => { refreshProjects() }, [refreshProjects])
  useEffect(() => { if (online) loadDownloads() }, [online, loadDownloads])
  // Reload the lists whenever an analysis finishes (a new project + download).
  useEffect(() => {
    if (job.state.status === 'done') { refreshProjects(); loadDownloads() }
  }, [job.state.status, refreshProjects, loadDownloads])

  const shortRefs = useRef<Record<number, HTMLDivElement | null>>({})

  const openProject = async (id: string, scrollIndex?: number) => {
    try {
      const data = await api.shortsProject(id)
      setLoaded({ id, title: data.display_name, shorts: data.ai_outputs ?? [] })
      if (typeof scrollIndex === 'number') {
        window.setTimeout(() => {
          shortRefs.current[scrollIndex]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 250)
      }
    } catch { /* ignore */ }
  }
  const removeProject = async (id: string) => {
    if (!window.confirm('Usunąć ten projekt z historii i jego wyrenderowane shorty z dysku?\n\nPobrany film źródłowy i jego transkrypcja pozostaną na dysku (są współdzielone z innymi projektami z tego wideo).')) return
    await api.deleteShortsProject(id).catch(() => {})
    if (loaded?.id === id) setLoaded(null)
    if (historyPick === id) setHistoryPick('')
    refreshProjects()
  }

  // Manual prepare finished → load the fresh (empty) project and open the scene editor.
  const manualHandled = useRef<string | null>(null)
  useEffect(() => {
    if (manualJob.state.status !== 'done') return
    const r = manualJob.state.result as { project_id?: string } | null
    if (!r?.project_id || manualHandled.current === r.project_id) return
    manualHandled.current = r.project_id
    setSourceMode('history')
    refreshProjects()
    openProject(r.project_id)
    setCustomShortMin(false)
    setCustomShortOpen(true)
  }, [manualJob.state.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const result = job.state.result as { shorts?: Short[]; title?: string; project_id?: string } | null
  const showGeneratedResult = workspaceResultVisible && (sourceMode === 'youtube' || sourceMode === 'file') && job.state.status === 'done'
  const showLoadedProject = (sourceMode === 'history' || sourceMode === 'favorites') && !!loaded
  // A freshly loaded project takes precedence when browsing history/favorites.
  const shorts = showLoadedProject ? loaded?.shorts ?? [] : showGeneratedResult ? result?.shorts ?? [] : []
  const resultsTitle = showLoadedProject ? loaded?.title : showGeneratedResult ? result?.title : undefined
  const projectId = showLoadedProject ? loaded?.id ?? null : showGeneratedResult ? result?.project_id ?? null : null
  // Per-project key under which the custom-short editor mirrors its in-progress draft, so it
  // survives unmount/minimise/reload and can't bleed across projects.
  const customDraftKey = projectId ? `dubcut.shorts.customDraft.${projectId}` : undefined

  // --- render / re-generate a single short into a finished video ---
  const renderJob = studio.shortsRenderJob
  const translateJob = useJob('shorts.translate')
  const dubJob = useJob('shorts.render-dub')
  const versionRenderJob = useJob('shorts.render-version')
  const versionSubsJob = useJob('shorts.translate-version-subs')
  const [versionSubsKey, setVersionSubsKey] = useState<string | null>(null)
  const activeVersionSubs = useRef<{ index: number; languageSlug: string; subLang: string } | null>(null)
  const versionSubsBatchJob = useJob('shorts.translate-version-subs-batch')
  const [versionSubsBatchKey, setVersionSubsBatchKey] = useState<string | null>(null)
  const activeVersionSubsBatch = useRef<{ index: number; languageSlug: string; langs: string[] } | null>(null)
  const [clearingCacheIdx, setClearingCacheIdx] = useState<number | null>(null)
  const [clearingDemucsIdx, setClearingDemucsIdx] = useState<number | null>(null)
  const [mergingIdx, setMergingIdx] = useState<number | null>(null)
  const [mergingVersionKey, setMergingVersionKey] = useState<string | null>(null)
  const [renderingIdx, setRenderingIdx] = useState<number | null>(null)
  const [translatingIdx, setTranslatingIdx] = useState<number | null>(null)
  const [dubbingIdx, setDubbingIdx] = useState<number | null>(null)
  const [versionRenderingKey, setVersionRenderingKey] = useState<string | null>(null)
  const activeTranslateIndex = useRef<number | null>(null)
  const activeDubIndex = useRef<number | null>(null)
  const activeRenderIndex = useRef<number | null>(null)
  const activeVersionRender = useRef<{ index: number; languageSlug: string } | null>(null)
  const [autoRenderProject, setAutoRenderProject] = useState<string | null>(null)
  const [autoQueue, setAutoQueue] = useState<number[]>([])
  const [renderedPlayers, setRenderedPlayers] = useState<Record<number, { url: string; title?: string }>>({})
  const [versions, setVersions] = useState<Record<number, ShortVersion[]>>({})
  // Bumped when the backend transitions offline→online; used to remount the result
  // players so cold-start videos (whose src 404'd before the backend was up) reload.
  const [mediaEpoch, setMediaEpoch] = useState(0)

  // --- overall "generation" progress: NOT done until every short's video is rendered ---
  // The analysis job finishes first (proposals), then each short auto-renders into a clip.
  // We only report "Gotowe" once all of those renders have a finished file.
  const totalShorts = shorts.length
  const renderedCount = useMemo(
    () => shorts.reduce((n, sh, i) => n + ((renderedPlayers[i] || sh.rendered_file) ? 1 : 0), 0),
    [shorts, renderedPlayers],
  )
  const isFreshGen = !showLoadedProject && !!result?.project_id && autoRenderProject === result.project_id
  const autoRenderActive = isFreshGen && job.state.status === 'done' && totalShorts > 0
    && (autoQueue.length > 0 || renderJob.state.status === 'running' || renderingIdx !== null || renderedCount < totalShorts)
  const allRendered = isFreshGen && job.state.status === 'done' && totalShorts > 0 && !autoRenderActive
  const [translateLang, setTranslateLang] = useState<Record<number, string>>({})
  const [dubLang, setDubLang] = useState<Record<number, string>>({})
  const [dubNotices, setDubNotices] = useState<Record<string, { kind: 'ok' | 'err'; message: string }>>({})
  // One short can have notices from rendering, dubbing and subtitle versions.
  // Starting any new operation supersedes all of them; leaving an old green
  // "gotowe" notice beside a running job is misleading.
  const clearShortNotices = useCallback((index: number) => {
    setDubNotices((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !(
      key === `render:${index}` || key === `dub:${index}` ||
      key.startsWith(`subs:${index}:`) || key.startsWith(`version:${index}:`)
    ))))
  }, [])

  // Turn the backend's translation-repair report into a short human notice. When
  // an engine echoed/garbled some segments, we re-translated them with the local
  // backstop — this tells the user how many, so a partially-translated result is
  // never silent.
  const repairTag = (repair: unknown): string => {
    const r = repair as { flagged?: number; repaired?: number; leftover?: number } | null | undefined
    if (!r || !r.flagged) return ''
    const repaired = r.repaired ?? 0
    const leftover = r.leftover ?? 0
    if (leftover > 0) return ` ⚠️ Naprawiono ${repaired}/${r.flagged} segmentów; ${leftover} mogło zostać w języku źródłowym.`
    return ` ✓ Auto-naprawiono ${repaired}/${r.flagged} segmentów tłumaczenia.`
  }

  const saveFavorites = (next: FavoriteShort[]) => {
    setFavorites(next)
    try { localStorage.setItem('dubcut.favoriteShorts', JSON.stringify(next)) } catch { /* ignore */ }
  }
  // Remove a clip from favorites only. This deletes the favorite mark and its
  // exported copy in the favorites folder — it NEVER touches the source project
  // or its files on disk. Works by exact id, so it removes a favorite from any
  // project regardless of which one is currently open.
  const removeFavoriteById = async (id: string) => {
    if (!window.confirm('Czy na pewno usunąć ten short z ulubionych?\n\nProjekt i pliki na dysku pozostaną nienaruszone — znika tylko oznaczenie ulubionego.')) return
    saveFavorites(favorites.filter((x) => x.id !== id))
    await api.deleteFavoriteClip(id).catch(() => {})
  }
  const favoriteId = (pid: string, index: number, languageSlug?: string) =>
    languageSlug ? `${pid}:${index}:${languageSlug}` : `${pid}:${index}`
  const isFavorite = (index: number) => !!projectId && favorites.some((fav) => fav.id === favoriteId(projectId, index))
  const isVersionFavorite = (index: number, languageSlug: string) =>
    !!projectId && favorites.some((fav) => fav.id === favoriteId(projectId, index, languageSlug))
  const toggleFavorite = async (index: number, short: Short) => {
    if (!projectId) return
    const id = favoriteId(projectId, index)
    if (favorites.some((fav) => fav.id === id)) {
      await removeFavoriteById(id)
      return
    }
    const next = [{ id, projectId, projectTitle: resultsTitle, index, total: shorts.length, short, savedAt: currentEpochMs() }, ...favorites]
    saveFavorites(next.slice(0, 200))
    await api.saveFavoriteClip({
      favorite_id: id,
      kind: 'short',
      project_id: projectId,
      index,
      project_title: resultsTitle,
      total: shorts.length,
      short,
    }).catch(() => {})
  }
  const toggleVersionFavorite = async (index: number, version: ShortVersion) => {
    if (!projectId) return
    const languageSlug = version.language_slug ?? slugLanguage(version.language ?? '')
    const id = favoriteId(projectId, index, languageSlug)
    const exists = favorites.some((fav) => fav.id === id)
    const versionShort = version.short_data ?? {}
    if (exists) {
      await removeFavoriteById(id)
      return
    }
    const next = [{
      id,
      kind: 'version' as const,
      projectId,
      projectTitle: resultsTitle,
      index,
      total: shorts.length,
      languageSlug,
      language: version.language,
      short: versionShort,
      savedAt: currentEpochMs(),
    }, ...favorites]
    saveFavorites(next.slice(0, 200))
    await api.saveFavoriteClip({
      favorite_id: id,
      kind: 'version',
      project_id: projectId,
      index,
      language_slug: languageSlug,
      project_title: resultsTitle,
      total: shorts.length,
      short: versionShort,
    }).catch(() => {})
  }

  const renderedFileUrl = (project: string, file: string, bust?: number | string) => {
    const base = `${getBase()}/api/shorts/file/${project}/shorts/${encodeURIComponent(file.split('/').pop() || '')}`
    return bust ? `${base}?v=${encodeURIComponent(String(bust))}` : base
  }

  const artifactUrl = (url?: string) => (url ? `${getBase()}${url}` : '')
  const shortAudioUrl = (project: string, index: number) =>
    `${getBase()}/api/shorts/audio/${project}/${index}`

  const scrollToShort = useCallback((index: number | null) => {
    if (index === null) return
    window.setTimeout(() => {
      shortRefs.current[index]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 80)
  }, [])

  const renderShort = useCallback((index: number, clearQueue = true) => {
    if (!projectId) return
    if (clearQueue) setAutoQueue([])
    clearShortNotices(index)
    activeRenderIndex.current = index
    setRenderingIdx(index)
    scrollToShort(index)
    renderJob.start(() => api.renderShort({ project_id: projectId, index, settings: s }))
  }, [clearShortNotices, projectId, renderJob, s, scrollToShort])

  const refreshVersions = useCallback(async (keepExistingOnEmpty = false) => {
    if (!projectId || !shorts.length) {
      setVersions({})
      return
    }
    const entries = await Promise.all(
      Array.from({ length: shorts.length }, (_, idx) =>
        api.shortVersions(projectId, idx)
          .then((v) => [idx, v] as const)
          .catch(() => [idx, [] as ShortVersion[]] as const),
      ),
    )
    const fetched = Object.fromEntries(entries)
    if (!keepExistingOnEmpty) {
      setVersions(fetched)
      return
    }
    setVersions((prev) => {
      const merged: Record<number, ShortVersion[]> = {}
      for (const [idx, items] of entries) {
        merged[idx] = items.length ? items : prev[idx] ?? []
      }
      return merged
    })
  }, [projectId, shorts.length])

  // Clear version badges THE MOMENT the project changes, before the async refetch
  // resolves — otherwise the previous project's "dubbing gotowy" badges linger on the
  // new project's shorts (same indices) until the fetch returns.
  useEffect(() => { setVersions({}); setDubNotices({}) }, [projectId])
  useEffect(() => { refreshVersions() }, [refreshVersions])

  // Cold start on the history/gallery tab: the players mount before the backend is
  // answering, so their <video src> 404s once and never retries — the user had to
  // switch tabs and back to force a remount. Detect the offline→online flip and do it
  // for them: re-fetch version lists and bump mediaEpoch so every player reloads.
  const wasOnline = useRef(online)
  useEffect(() => {
    if (online && !wasOnline.current) {
      setMediaEpoch((e) => e + 1)
      refreshVersions(true)
    }
    wasOnline.current = online
  }, [online, refreshVersions])

  // Effective target language for the per-short "Przetłumacz" control. MUST mirror
  // the dropdown's displayed value (see the select below): when the global
  // `s.target_lang` is "Brak (Oryginał)" the dropdown shows "Angielski", so we must
  // submit "Angielski" too — submitting "Brak (Oryginał)" hits NLLB with an
  // unsupported language and fails instantly.
  const effectiveTranslateLang = (index: number) =>
    translateLang[index] || (s.target_lang && s.target_lang !== 'Brak (Oryginał)' ? s.target_lang : 'Angielski')

  // „Przetłumacz" on the base short: translate the burned subtitles + metadata into the
  // chosen language while KEEPING the original speech, auto-render a new clip, and add it
  // below as a separate version (Napisy: <docelowy> · Język: <oryginał>). The base short
  // is never relabelled/overwritten — only a new version is produced.
  const translateShort = (index: number) => {
    if (!projectId) return
    activeTranslateIndex.current = index
    setTranslatingIdx(index)
    clearShortNotices(index)
    scrollToShort(index)
    translateJob.start(() => api.translateShortSubtitles(projectId, index, {
      subtitle_language: effectiveTranslateLang(index),
      settings: s,
    }))
  }

  const renderDub = (index: number) => {
    if (!projectId) return
    const language = dubLang[index] || 'Angielski'
    activeDubIndex.current = index
    setDubbingIdx(index)
    clearShortNotices(index)
    scrollToShort(index)
    dubJob.start(() => api.renderShortDub({
      project_id: projectId,
      index,
      language,
      settings: {
        ...s,
        audio_mode: s.audio_mode || 'Czysty dubbing (usuń oryginalny głos)',
        dub_target_lang: language,
        dub_auto_subtitles: s.dub_auto_subtitles ?? true,
      },
    }))
  }

  useEffect(() => {
    if (loaded || !result?.project_id || !result.shorts?.length || job.state.status !== 'done') return
    if (autoRenderProject === result.project_id) return
    setAutoRenderProject(result.project_id)
    setRenderedPlayers({})
    setAutoQueue(result.shorts.map((_, idx) => idx))
  }, [autoRenderProject, job.state.status, loaded, result?.project_id, result?.shorts])

  useEffect(() => {
    if (!projectId || !autoQueue.length) return
    if (renderJob.state.status === 'running' || renderingIdx !== null) return
    const [next, ...rest] = autoQueue
    setAutoQueue(rest)
    scrollToShort(next)
    renderShort(next, false)
  }, [autoQueue, projectId, renderJob.state.status, renderShort, renderingIdx, scrollToShort])

  useEffect(() => {
    if (renderJob.state.status === 'done') {
      const r = renderJob.state.result as { url?: string; title?: string; index?: number; rendered_at?: number } | null
      const idx = activeRenderIndex.current ?? (typeof r?.index === 'number' ? r.index : null)
      if (idx !== null && r?.url) {
        const bust = r.rendered_at ?? Date.now()
        const url = r.url
        setRenderedPlayers((prev) => ({
          ...prev,
          [idx]: { url: `${getBase()}${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(String(bust))}`, title: r.title },
        }))
        setDubNotices((prev) => ({
          ...prev,
          [`render:${idx}`]: { kind: 'ok', message: `Short został przegenerowany${timeTag(renderJob.state)}. Podgląd i pobieranie wskazują świeży plik.` },
        }))
      }
      if (projectId) {
        api.shortsProject(projectId)
          .then((fresh) => {
            if (loaded?.id === projectId) setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
          })
          .catch(() => {})
      }
      activeRenderIndex.current = null
      setRenderingIdx(null)
    } else if (renderJob.state.status === 'error') {
      const idx = activeRenderIndex.current
      if (idx !== null) {
        setDubNotices((prev) => ({
          ...prev,
          [`render:${idx}`]: { kind: 'err', message: renderJob.state.error || 'Renderowanie shorta nie powiodło się.' },
        }))
      }
      activeRenderIndex.current = null
      setRenderingIdx(null)
    }
  }, [loaded?.id, projectId, renderJob.state.error, renderJob.state.finishedAt, renderJob.state.result, renderJob.state.startedAt, renderJob.state.status])

  useEffect(() => {
    if (translateJob.state.status === 'done') {
      const idx = activeTranslateIndex.current
      const r = translateJob.state.result as {
        language?: string; subtitle_language?: string; language_slug?: string; short?: Short; repair?: unknown
      } | null
      // The translated clip is a brand-new version — pull it into the list below the short.
      refreshVersions(true)
      window.setTimeout(() => refreshVersions(true), 900)
      // When browsing favorites, mark the new subtitle version as a favorite too, so it
      // lands in the same favorites section the user is currently looking at.
      if (projectId && idx !== null && sourceMode === 'favorites' && r?.language_slug) {
        const favId = favoriteId(projectId, idx, r.language_slug)
        if (!favorites.some((fav) => fav.id === favId)) {
          const fav: FavoriteShort = {
            id: favId,
            kind: 'version',
            projectId,
            projectTitle: resultsTitle,
            index: idx,
            total: shorts.length,
            languageSlug: r.language_slug,
            language: r.language,
            short: r.short ?? {},
            savedAt: currentEpochMs(),
          }
          saveFavorites([fav, ...favorites].slice(0, 200))
          api.saveFavoriteClip({
            favorite_id: favId,
            kind: 'version',
            project_id: projectId,
            index: idx,
            language_slug: r.language_slug,
            project_title: resultsTitle,
            total: shorts.length,
            short: r.short ?? {},
          }).catch(() => {})
        }
      }
      if (idx !== null) {
        const tag = repairTag(r?.repair)
        setDubNotices((prev) => ({
          ...prev,
          [`dub:${idx}`]: { kind: 'ok', message: `Nowa wersja: napisy ${r?.subtitle_language || translateLang[idx] || '—'} na oryginalnej mowie (${r?.language || '—'})${timeTag(translateJob.state)}. Jest na liście poniżej.${tag}` },
        }))
      }
      activeTranslateIndex.current = null
      setTranslatingIdx(null)
    } else if (translateJob.state.status === 'error') {
      const idx = activeTranslateIndex.current
      if (idx !== null) {
        setDubNotices((prev) => ({
          ...prev,
          [`dub:${idx}`]: { kind: 'err', message: translateJob.state.error || 'Tłumaczenie napisów nie powiodło się.' },
        }))
      }
      activeTranslateIndex.current = null
      setTranslatingIdx(null)
    }
  }, [projectId, translateJob.state.status, translateJob.state.result])

  useEffect(() => {
    if (dubJob.state.status === 'done') {
      const r = dubJob.state.result as { url?: string; language?: string; title?: string; short?: Short; repair?: unknown } | null
      const idx = activeDubIndex.current
      if (idx !== null && r?.url) {
        const language = r.language || dubLang[idx] || 'Angielski'
        setVersions((prev) => {
          const existing = prev[idx] ?? []
          const nextVersion: ShortVersion = {
            language,
            language_slug: slugLanguage(language),
            created_at: currentEpochMs() / 1000,
            video_path_url: r.url,
            audio_mode: s.audio_mode,
            short_data: r.short ?? { ...shorts[idx], language, title: r.title ?? shorts[idx]?.title },
          }
          return {
            ...prev,
            [idx]: [
              nextVersion,
              ...existing.filter((version) => version.video_path_url !== r.url),
            ],
          }
        })
      }
      refreshVersions(true)
      window.setTimeout(() => refreshVersions(true), 900)
      if (idx !== null) {
        setDubNotices((prev) => ({
          ...prev,
          [`dub:${idx}`]: { kind: 'ok', message: `Dubbing ${r?.language || dubLang[idx] || 'Angielski'} gotowy${timeTag(dubJob.state)}. Wersja jest pod tym shortem.${repairTag(r?.repair)}` },
        }))
      }
      activeDubIndex.current = null
      setDubbingIdx(null)
    } else if (dubJob.state.status === 'error') {
      const idx = activeDubIndex.current
      if (idx !== null) {
        setDubNotices((prev) => ({
          ...prev,
          [`dub:${idx}`]: { kind: 'err', message: dubJob.state.error || 'Dubbing nie powiódł się. Szczegóły są w logu zadania.' },
        }))
      }
      activeDubIndex.current = null
      setDubbingIdx(null)
    }
  }, [dubJob.state.result, dubJob.state.status, dubLang, refreshVersions, shorts])

  useEffect(() => {
    if (versionRenderJob.state.status === 'done') {
      const r = versionRenderJob.state.result as { url?: string; language?: string; short?: Short } | null
      const active = activeVersionRender.current
      if (active && r?.url) {
        const versionUrl = r.url
        setVersions((prev) => ({
          ...prev,
          [active.index]: (prev[active.index] ?? []).map((version) => (
            version.language_slug === active.languageSlug
              ? { ...version, video_path_url: `${versionUrl}${versionUrl.includes('?') ? '&' : '?'}t=${Date.now()}`, short_data: r.short ?? version.short_data }
              : version
          )),
        }))
      }
      refreshVersions(true)
      if (active) {
        setDubNotices((prev) => ({
          ...prev,
          [`version:${active.index}:${active.languageSlug}`]: { kind: 'ok', message: `Wersja została przegenerowana${timeTag(versionRenderJob.state)}.` },
        }))
      }
      activeVersionRender.current = null
      setVersionRenderingKey(null)
    } else if (versionRenderJob.state.status === 'error') {
      const active = activeVersionRender.current
      if (active) {
        setDubNotices((prev) => ({
          ...prev,
          [`version:${active.index}:${active.languageSlug}`]: { kind: 'err', message: versionRenderJob.state.error || 'Przegenerowanie wersji nie powiodło się.' },
        }))
      }
      activeVersionRender.current = null
      setVersionRenderingKey(null)
    }
  }, [refreshVersions, versionRenderJob.state.result, versionRenderJob.state.status])

  useEffect(() => {
    if (versionSubsJob.state.status === 'done') {
      const active = activeVersionSubs.current
      const r = versionSubsJob.state.result as { subtitle_language?: string; language?: string; repair?: unknown } | null
      refreshVersions(true)
      if (active) {
        setDubNotices((prev) => ({
          ...prev,
          [`subs:${active.index}:${active.languageSlug}`]: {
            kind: 'ok',
            message: `Nowa wersja: napisy ${r?.subtitle_language || active.subLang} na audio ${r?.language || '—'}${timeTag(versionSubsJob.state)}. Jest na liście poniżej.${repairTag(r?.repair)}`,
          },
        }))
      }
      activeVersionSubs.current = null
      setVersionSubsKey(null)
    } else if (versionSubsJob.state.status === 'error') {
      const active = activeVersionSubs.current
      if (active) {
        setDubNotices((prev) => ({
          ...prev,
          [`subs:${active.index}:${active.languageSlug}`]: { kind: 'err', message: versionSubsJob.state.error || 'Tłumaczenie napisów nie powiodło się.' },
        }))
      }
      activeVersionSubs.current = null
      setVersionSubsKey(null)
    }
  }, [refreshVersions, versionSubsJob.state.result, versionSubsJob.state.status])

  useEffect(() => {
    if (versionSubsBatchJob.state.status === 'done') {
      const active = activeVersionSubsBatch.current
      const r = versionSubsBatchJob.state.result as { results?: { ok: boolean; subtitle_language?: string; error?: string }[]; ok_count?: number; total?: number } | null
      refreshVersions(true)
      if (active) {
        const failed = (r?.results || []).filter((x) => !x.ok)
        const okCount = r?.ok_count ?? ((r?.results || []).length - failed.length)
        const total = r?.total ?? (r?.results || []).length
        const failNote = failed.length
          ? ` Nie udało się: ${failed.map((x) => x.subtitle_language).join(', ')}.`
          : ''
        setDubNotices((prev) => ({
          ...prev,
          [`subs:${active.index}:${active.languageSlug}`]: {
            kind: failed.length ? 'err' : 'ok',
            message: `Wygenerowano ${okCount}/${total} wersji napisów${timeTag(versionSubsBatchJob.state)}. Są na liście poniżej.${failNote}`,
          },
        }))
      }
      activeVersionSubsBatch.current = null
      setVersionSubsBatchKey(null)
    } else if (versionSubsBatchJob.state.status === 'error') {
      const active = activeVersionSubsBatch.current
      if (active) {
        setDubNotices((prev) => ({
          ...prev,
          [`subs:${active.index}:${active.languageSlug}`]: { kind: 'err', message: versionSubsBatchJob.state.error || 'Wsadowe tłumaczenie napisów nie powiodło się.' },
        }))
      }
      activeVersionSubsBatch.current = null
      setVersionSubsBatchKey(null)
    }
  }, [refreshVersions, versionSubsBatchJob.state.result, versionSubsBatchJob.state.status])

  // --- subtitle editor ---
  const [editorIdx, setEditorIdx] = useState<number | null>(null)
  const [versionEditor, setVersionEditor] = useState<{ index: number; languageSlug: string; short: Short } | null>(null)
  const [sceneEditorIdx, setSceneEditorIdx] = useState<number | null>(null)
  // Custom-short editor state is PERSISTENT: switching modules in the left rail unmounts
  // this view, so a plain useState would silently drop an in-progress custom short (the
  // project then sits in history with 0 shorts and no way back). `customShortOpen` = there
  // is an active custom short; `customShortMin` = it's collapsed to the restore bar.
  const [customShortOpen, setCustomShortOpen] = usePersistentState('dubcut.shorts.customOpen', false)
  const [customShortMin, setCustomShortMin] = usePersistentState('dubcut.shorts.customMin', false)
  const [editorSaving, setEditorSaving] = useState(false)
  // The whole film's word-level transcription (for the editors' live subtitle text +
  // waveform). Fetched lazily when an editor opens.
  const [globalWords, setGlobalWords] = useState<{ word: string; start: number; end: number }[]>([])
  useEffect(() => {
    const open = sceneEditorIdx !== null || editorIdx !== null || versionEditor !== null || customShortOpen
    if (!open || !projectId) return
    let cancelled = false
    api.shortsProject(projectId)
      .then((d) => { if (!cancelled) setGlobalWords((d?.global_words ?? []) as any) })
      .catch(() => { if (!cancelled) setGlobalWords([]) })
    return () => { cancelled = true }
  }, [sceneEditorIdx, editorIdx, versionEditor, customShortOpen, projectId])

  const saveEdit = async (data: { segments: any[]; words: any[] }, thenRender: boolean) => {
    if (!projectId || editorIdx === null) return
    setEditorSaving(true)
    try {
      await api.updateShort(projectId, editorIdx, data)
      const fresh = await api.shortsProject(projectId)
      setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
      const idx = editorIdx
      setEditorIdx(null)
      if (thenRender) {
        clearShortNotices(idx)
        activeRenderIndex.current = idx
        setRenderingIdx(idx)
        scrollToShort(idx)
        renderJob.start(() => api.renderShort({ project_id: projectId, index: idx, settings: s }))
      }
    } finally {
      setEditorSaving(false)
    }
  }

  const saveScenes = async (data: { segments: any[]; restore?: boolean }, thenRender: boolean) => {
    if (!projectId || sceneEditorIdx === null) return
    setEditorSaving(true)
    try {
      await api.updateShortScenes(projectId, sceneEditorIdx, data)
      const fresh = await api.shortsProject(projectId)
      setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
      const idx = sceneEditorIdx
      setSceneEditorIdx(null)
      if (thenRender) {
        clearShortNotices(idx)
        activeRenderIndex.current = idx
        setRenderingIdx(idx)
        scrollToShort(idx)
        renderJob.start(() => api.renderShort({ project_id: projectId, index: idx, settings: s }))
      }
    } finally {
      setEditorSaving(false)
    }
  }

  const createCustomShort = async (data: { segments: any[]; restore?: boolean }, thenRender: boolean) => {
    if (!projectId) return
    setEditorSaving(true)
    try {
      const res = await api.createCustomShort(projectId, { segments: data.segments })
      const fresh = await api.shortsProject(projectId)
      setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
      if (customDraftKey) { try { localStorage.removeItem(customDraftKey) } catch { /* ignore */ } }
      setCustomShortMin(false)
      setCustomShortOpen(false)
      const idx = res.index
      if (thenRender) {
        clearShortNotices(idx)
        activeRenderIndex.current = idx
        setRenderingIdx(idx)
        scrollToShort(idx)
        renderJob.start(() => api.renderShort({ project_id: projectId, index: idx, settings: s }))
      }
    } finally {
      setEditorSaving(false)
    }
  }

  // Collapse the custom-short editor to the restore bar — the draft stays mirrored in
  // localStorage, so the user can reopen and finish later.
  const minimizeCustomShort = () => setCustomShortMin(true)
  const restoreCustomShort = () => setCustomShortMin(false)
  // Explicit discard (X / „Porzuć") — only this throws the in-progress short away.
  const discardCustomShort = async () => {
    if (!window.confirm('Porzucić tego własnego shorta? Wycięte sceny zostaną utracone.')) return
    const id = projectId
    if (customDraftKey) { try { localStorage.removeItem(customDraftKey) } catch { /* ignore */ } }
    setCustomShortMin(false)
    setCustomShortOpen(false)
    // Nothing was ever saved (the project has no shorts) → don't leave an empty 0-shorts
    // project in history. Delete it; the shared source film + its transcription stay on
    // disk (cached for reuse), so re-starting a custom short is instant.
    if (id && shorts.length === 0) {
      await api.deleteShortsProject(id).catch(() => {})
      if (loaded?.id === id) setLoaded(null)
      if (historyPick === id) setHistoryPick('')
      setSourceMode('youtube')
      refreshProjects()
    }
  }

  const saveVersionEdit = async (data: { segments: any[]; words: any[] }, thenRender: boolean) => {
    if (!projectId || !versionEditor) return
    const { index, languageSlug } = versionEditor
    setEditorSaving(true)
    try {
      await api.updateShortVersion(projectId, index, languageSlug, data)
      setVersionEditor(null)
      await refreshVersions(true)
      if (thenRender) renderDubVersion(index, languageSlug)
    } finally {
      setEditorSaving(false)
    }
  }

  const renderDubVersion = (index: number, languageSlug: string) => {
    if (!projectId) return
    const key = `${index}:${languageSlug}`
    activeVersionRender.current = { index, languageSlug }
    setVersionRenderingKey(key)
    clearShortNotices(index)
    scrollToShort(index)
    versionRenderJob.start(() => api.renderShortVersion(projectId, index, languageSlug, { settings: s }))
  }

  // Translate an existing dubbed/lektor version's SUBTITLES into another language
  // and re-render it (same audio) as a new, separate version.
  const translateVersionSubs = (index: number, languageSlug: string, subLang: string) => {
    if (!projectId) return
    const key = `${index}:${languageSlug}`
    activeVersionSubs.current = { index, languageSlug, subLang }
    setVersionSubsKey(key)
    clearShortNotices(index)
    scrollToShort(index)
    versionSubsJob.start(() => api.translateVersionSubtitles(projectId, index, languageSlug, { subtitle_language: subLang, settings: s }))
  }

  const translateVersionSubsBatch = (index: number, languageSlug: string, langs: string[]) => {
    if (!projectId || langs.length === 0) return
    const key = `${index}:${languageSlug}`
    activeVersionSubsBatch.current = { index, languageSlug, langs }
    setVersionSubsBatchKey(key)
    clearShortNotices(index)
    scrollToShort(index)
    versionSubsBatchJob.start(() => api.translateVersionSubtitlesBatch(projectId, index, languageSlug, { subtitle_languages: langs, settings: s }))
  }

  const mergeScenes = async (index: number) => {
    if (!projectId) return
    setMergingIdx(index)
    try {
      const r = await api.mergeShortScenes(projectId, index)
      const fresh = await api.shortsProject(projectId)
      setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
      setDubNotices((prev) => ({
        ...prev,
        [`dub:${index}`]: { kind: 'ok', message: `Sceny scalone: ${r.before} → ${r.after}. Edytor scen pokaże teraz właściwe sceny.` },
      }))
    } catch (e) {
      setDubNotices((prev) => ({ ...prev, [`dub:${index}`]: { kind: 'err', message: `Nie udało się scalić scen: ${(e as Error).message}` } }))
    } finally {
      setMergingIdx(null)
    }
  }

  const mergeVersionScenes = async (index: number, languageSlug: string) => {
    if (!projectId) return
    const key = `${index}:${languageSlug}`
    setMergingVersionKey(key)
    try {
      const r = await api.mergeVersionScenes(projectId, index, languageSlug)
      refreshVersions(true)
      setDubNotices((prev) => ({
        ...prev,
        [`subs:${key}`]: { kind: 'ok', message: `Sceny scalone: ${r.before} → ${r.after}.` },
      }))
    } catch (e) {
      setDubNotices((prev) => ({ ...prev, [`subs:${key}`]: { kind: 'err', message: `Nie udało się scalić scen: ${(e as Error).message}` } }))
    } finally {
      setMergingVersionKey(null)
    }
  }

  const clearDemucsCache = async (index: number) => {
    if (!projectId) return
    setClearingDemucsIdx(index)
    try {
      const r = await api.clearShortDemucsCache(projectId, index)
      setDubNotices((prev) => ({
        ...prev,
        [`dub:${index}`]: {
          kind: 'ok',
          message: r.removed > 0
            ? 'Wyczyszczono cache tła (Demucs). Następny dubbing rozdzieli tło od nowa.'
            : 'Brak zapisanego cache tła dla tego shorta.',
        },
      }))
    } catch (e) {
      setDubNotices((prev) => ({ ...prev, [`dub:${index}`]: { kind: 'err', message: `Nie udało się wyczyścić cache tła: ${(e as Error).message}` } }))
    } finally {
      setClearingDemucsIdx(null)
    }
  }

  const clearTranslationCache = async (index: number) => {
    if (!projectId) return
    setClearingCacheIdx(index)
    try {
      const r = await api.clearShortTranslationCache(projectId, index)
      setDubNotices((prev) => ({
        ...prev,
        [`dub:${index}`]: {
          kind: 'ok',
          message: r.removed > 0
            ? `Wyczyszczono cache tłumaczenia (${r.removed} plik(ów)). Następny dubbing/tłumaczenie przeliczy się od nowa.`
            : 'Brak zapisanego cache tłumaczenia dla tego shorta.',
        },
      }))
    } catch (e) {
      setDubNotices((prev) => ({
        ...prev,
        [`dub:${index}`]: { kind: 'err', message: `Nie udało się wyczyścić cache: ${(e as Error).message}` },
      }))
    } finally {
      setClearingCacheIdx(null)
    }
  }

  const deleteShort = async (index: number) => {
    if (!projectId) return
    if (!window.confirm(`Usunąć Short ${index + 1} z ${shorts.length}? To skasuje też jego pliki z dysku.`)) return
    await api.deleteShort(projectId, index)
    setFavorites((prev) => {
      const next = prev.filter((fav) => !(fav.projectId === projectId && fav.index === index))
      try { localStorage.setItem('dubcut.favoriteShorts', JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
    setVersions((prev) => {
      const next: Record<number, ShortVersion[]> = {}
      Object.entries(prev).forEach(([key, value]) => {
        const idx = Number(key)
        if (idx < index) next[idx] = value
        if (idx > index) next[idx - 1] = value
      })
      return next
    })
    if (loaded?.id === projectId) {
      const fresh = await api.shortsProject(projectId)
      setLoaded({ id: projectId, title: fresh.display_name, shorts: fresh.ai_outputs ?? [] })
    } else {
      setWorkspaceResultVisible(false)
    }
    refreshProjects()
  }

  const deleteDubVersion = async (index: number, languageSlug: string) => {
    if (!projectId) return
    if (!window.confirm('Usunąć tę wersję i jej pliki z dysku?')) return
    await api.deleteShortVersion(projectId, index, languageSlug)
    setVersions((prev) => ({
      ...prev,
      [index]: (prev[index] ?? []).filter((version) => (version.language_slug ?? slugLanguage(version.language ?? '')) !== languageSlug),
    }))
    refreshVersions(true)
  }

  // Analysis settings, shown in the CENTER directly under the source input (moved out of
  // the right sidebar) — the user sets everything for the run in one place.
  const analysisSettings = (withYt: boolean) => (
    <div className="shorts-setup">
      {withYt && (
        <div className="shorts-setup-row2">
          <Field label="Rozdzielczość pobierania z YT">
            <Select value={s.yt_quality} options={meta.shorts.yt_qualities} onChange={(v) => set({ yt_quality: v })} />
          </Field>
          <div className="shorts-setup-toggle">
            <Toggle checked={!!s.use_yt_subs} label="Zaciągaj gotowe napisy z YT" onChange={(v) => set({ use_yt_subs: v })} />
          </div>
        </div>
      )}
      <Field label="Tryb promptu" hint="Jak AI ma szukać momentów na shorty: „Precyzyjna” trzyma się najmocniejszych, najbardziej viralowych fragmentów; „Kreatywna” daje AI więcej swobody; „Trailer / Zapowiedź” buduje zwiastun; „Własny prompt” pozwala wpisać własne wytyczne dla AI.">
        <Select value={s.prompt_mode} options={meta.shorts.prompt_modes} onChange={(v) => set({ prompt_mode: v })} />
      </Field>
      {s.prompt_mode === 'Własny prompt' && (
        <Field label="Własny prompt AI">
          <TextArea value={s.custom_prompt_text ?? ''} rows={4} onChange={(v) => set({ custom_prompt_text: v })} />
        </Field>
      )}
      <div className="shorts-setup-row2">
        <Field label="Język pliku wejściowego" hint="Język mowy w oryginalnym filmie — w nim aplikacja spisze, co mówią. „Auto-detekcja” sam wykryje język; wybierz konkretny, jeśli wykrywanie się myli.">
          <Select value={s.whisper_lang} options={meta.shorts.languages} onChange={(v) => set({ whisper_lang: v })} />
        </Field>
        <Field label="Język tłumaczenia napisów" hint="Po wybraniu języka aplikacja przetłumaczy napisy i metadane shortów na ten język. „Brak (Oryginał)” zostawia mowę i napisy w języku źródłowym.">
          <Select value={s.target_lang} options={['Brak (Oryginał)', ...meta.dub.target_languages]} onChange={(v) => set({ target_lang: v })} />
        </Field>
      </div>
      <div className="shorts-setup-toggle">
        <Toggle
          checked={forceTranscribe}
          label="Wymuś ponowną transkrypcję (pomiń zapisaną)"
          hint="Usuwa wcześniejszą transkrypcję, słowa i projekty tego filmu, a potem robi wszystko od nowa Whisperem. Użyj, gdy poprzedni wynik był błędny lub zawierał halucynacje. Działa dla YouTube i plików lokalnych."
          onChange={setForceTranscribe}
        />
      </div>
      <Slider label="Ile shortów stworzyć?" value={s.shorts_count} min={1} max={50} hint="Maksymalna liczba klipów, które AI spróbuje wybrać z materiału." onChange={(v) => set({ shorts_count: v })} />
      <div className="shorts-setup-row2">
        <Slider label="Min. długość klipu" value={s.duration_min} min={15} max={240} suffix=" s" hint="Dolna granica długości pojedynczego shorta." onChange={(v) => set({ duration_min: v })} />
        <Slider label="Maks. długość klipu" value={s.duration_max} min={15} max={240} suffix=" s" hint="Górna granica długości pojedynczego shorta." onChange={(v) => set({ duration_max: v })} />
      </div>
    </div>
  )

  return (
    <div className="studio-grid">
      <section className="editor">
        <header className="editor-head">
          <div>
            <span className="eyebrow">Generator shortów</span>
            <h2>AI ViralCutter</h2>
          </div>
        </header>

        <div className="source-tabs">
          <button
            type="button"
            className={sourceMode === 'youtube' ? 'source-tab active' : 'source-tab'}
            onClick={() => switchSourceMode('youtube')}
          >
            <Link2 size={16} /> Link YouTube
          </button>
          <button
            type="button"
            className={sourceMode === 'file' ? 'source-tab active' : 'source-tab'}
            onClick={() => switchSourceMode('file')}
          >
            <FolderOpen size={16} /> Plik lokalny
          </button>
          <button
            type="button"
            className={sourceMode === 'history' ? 'source-tab active' : 'source-tab'}
            onClick={() => switchSourceMode('history')}
          >
            <History size={16} /> Historia projektów{projects.length ? ` (${projects.length})` : ''}
          </button>
          <button
            type="button"
            className={sourceMode === 'favorites' ? 'source-tab active' : 'source-tab'}
            onClick={() => switchSourceMode('favorites')}
          >
            <Heart size={16} /> Ulubione{favorites.length ? ` (${favorites.length})` : ''}
          </button>
        </div>

        {sourceMode === 'youtube' && (
          <div className="yt-card">
            <label className="yt-card-label">
              <Link2 size={15} /> Adres wideo YouTube
            </label>
            <div className="yt-card-input">
              <input
                className="yt-input"
                placeholder="https://www.youtube.com/watch?v=…"
                value={ytUrl}
                list="yt-recents"
                onChange={(e) => setYtUrl(e.target.value)}
                onBlur={() => rememberYt(ytUrl)}
              />
              {ytUrl.trim() && (
                <button type="button" className="yt-clear" onClick={() => setYtUrl('')}>
                  Wyczyść
                </button>
              )}
            </div>
            <datalist id="yt-recents">
              {ytRecents.map((u) => <option key={u} value={u} />)}
            </datalist>
            {downloads.length > 0 && (
              <div className="yt-downloads">
                <span className="yt-downloads-label">
                  <FolderOpen size={13} /> Pobrane filmy — kliknij, by wybrać, potem użyj przycisku poniżej:
                </span>
                <div className="yt-downloads-list">
                  {downloads.map((d) => (
                    <span className={`yt-download ${ytUrl === d.url ? 'active' : ''}`} key={d.video_id}>
                      <button
                        type="button"
                        className="yt-download-pick"
                        title={`${d.title}${d.has_transcript ? ' • transkrypcja w pamięci' : ''}`}
                        disabled={job.state.status === 'running'}
                        onClick={() => runDownloaded(d)}
                      >
                        <Scissors size={12} />
                        <span className="yt-download-title">{d.title}</span>
                        {d.has_transcript && <Check size={11} className="yt-download-cached" />}
                      </button>
                      <button
                        type="button"
                        className="yt-download-x"
                        title="Usuń pobrany film z dysku"
                        onClick={() => forgetDownload(d.video_id)}
                      >
                        <Trash2 size={11} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {analysisSettings(true)}
            <div className="analyze-actions">
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || job.state.status === 'running' || manualJob.state.status === 'running'} onClick={run}>
                <Scissors size={15} /> Generuj shorty automatycznie
              </button>
              <button type="button" className="ghost-btn analyze-btn analyze-btn-manual" disabled={!canRun || job.state.status === 'running' || manualJob.state.status === 'running'} onClick={runManual} title="Pobierz/wczytaj film, zrób transkrypcję i otwórz edytor — sam wytniesz scenę po scenie. Na końcu nałoży się styl, napisy, logo i watermark.">
                <Scissors size={15} /> Stwórz własnego shorta
              </button>
            </div>
          </div>
        )}

        {sourceMode === 'file' && (
          <div className="yt-card">
            <DropZone media={media} formats="MP4 · MOV · MKV · WEBM" onPick={pick} compact />
            {analysisSettings(false)}
            <div className="analyze-actions">
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || job.state.status === 'running' || manualJob.state.status === 'running'} onClick={run}>
                <Scissors size={15} /> Generuj shorty automatycznie
              </button>
              <button type="button" className="ghost-btn analyze-btn analyze-btn-manual" disabled={!canRun || job.state.status === 'running' || manualJob.state.status === 'running'} onClick={runManual} title="Pobierz/wczytaj film, zrób transkrypcję i otwórz edytor — sam wytniesz scenę po scenie. Na końcu nałoży się styl, napisy, logo i watermark.">
                <Scissors size={15} /> Stwórz własnego shorta
              </button>
            </div>
          </div>
        )}

        {sourceMode === 'history' && (
          <div className="shorts-history">
            {projects.length === 0 ? (
              <p className="settings-desc">Brak zapisanych projektów. Wygeneruj pierwszy short, a pojawi się tutaj.</p>
            ) : (
              <div className="history-dropdown">
                <select
                  className="text-field"
                  value={historyPick || loaded?.id || ''}
                  onChange={(e) => {
                    const id = e.target.value
                    setHistoryPick(id)
                    if (id) openProject(id)
                  }}
                >
                  <option value="">Wybierz projekt z historii...</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name} ({p.shorts_count}) - {fmtDate(p.created)}
                    </option>
                  ))}
                </select>
                <button type="button" className="ghost-btn" disabled={!(historyPick || loaded?.id)} onClick={() => openProject(historyPick || loaded!.id)}>
                  Otwórz
                </button>
                <button type="button" className="ghost-btn icon-btn history-del" disabled={!(historyPick || loaded?.id)} title="Usuń projekt" onClick={() => removeProject(historyPick || loaded!.id)}>
                  <Trash2 size={14} />
                </button>
              </div>
            )}
          </div>
        )}

        {sourceMode === 'favorites' && (
          <div className="shorts-history">
            {favorites.length === 0 ? (
              <p className="settings-desc">Brak ulubionych shortów. Kliknij serduszko przy shorcie, żeby go tutaj zapisać.</p>
            ) : (
              <div className="favorite-list">
                {favorites.map((fav) => (
                  <div className="favorite-item" key={fav.id}>
                    <button type="button" className="favorite-open" onClick={() => openProject(fav.projectId, fav.index)}>
                      <strong>
                        Short {fav.index + 1} z {fav.total}
                        {fav.kind === 'version' && fav.language ? ` (${fav.language})` : ''}
                        {' - '}{fav.short.title ?? fav.projectTitle ?? 'Ulubiony short'}
                      </strong>
                      <span>{fav.projectTitle ?? fav.projectId} · {fmtDate(fav.savedAt / 1000)}</span>
                    </button>
                    <button type="button" className="favorite-toggle active" title="Usuń z ulubionych (projekt zostaje na dysku)" onClick={() => removeFavoriteById(fav.id)}>
                      <Heart size={20} fill="currentColor" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Analysis phase — only while it's actually running/erroring (its own "Gotowe"
            is suppressed so the overall status doesn't claim done before videos render). */}
        {(job.state.status === 'running' || job.state.status === 'error' || job.state.status === 'cancelled'
          || (job.state.status === 'done' && !isFreshGen)) && (
          <InlineJobProgress state={job.state} label="Analiza AI — wybór viralowych momentów" onCancel={job.cancel} />
        )}
        {(manualJob.state.status === 'running' || manualJob.state.status === 'error') && (
          <InlineJobProgress state={manualJob.state} label="Przygotowanie filmu do własnego shorta (pobieranie + transkrypcja)" onCancel={manualJob.cancel} />
        )}
        {/* Video phase — the real "done" gate: not finished until every clip is rendered. */}
        {autoRenderActive && (
          <div className="gen-progress">
            <span className="gen-progress-spin" />
            <div className="gen-progress-body">
              <strong>Renderowanie wideo shortów… {renderedCount}/{totalShorts}</strong>
              <div className="gen-progress-bar"><em style={{ width: `${totalShorts ? (renderedCount / totalShorts) * 100 : 0}%` }} /></div>
            </div>
          </div>
        )}
        {allRendered && (
          <div className="gen-progress done">
            <Check size={16} />
            <strong>Gotowe — {totalShorts} {totalShorts === 1 ? 'short wygenerowany' : 'shortów wygenerowanych'} (wideo gotowe).</strong>
          </div>
        )}

        {shorts.length > 0 && (
          <div className="shorts-results" key={`results-${mediaEpoch}`}>
            <h3>
              <Sparkles size={16} /> {resultsTitle ?? 'Propozycje shortów'}
              {loaded && (
                <button type="button" className="ghost-btn icon-btn history-close" title="Zamknij wczytany projekt" onClick={() => setLoaded(null)}>
                  <X size={14} />
                </button>
              )}
            </h3>
            {shorts.map((short, i) => {
              const busy = renderJob.state.status === 'running' && renderingIdx === i
              const translating = translateJob.state.status === 'running' && translatingIdx === i
              const dubbing = dubJob.state.status === 'running' && dubbingIdx === i
              const dubNotice = dubNotices[`render:${i}`] ?? dubNotices[`dub:${i}`]
              // The base short always keeps its ORIGINAL speech AND original burned subtitles.
              // Translating subtitles produces a separate version below — it never relabels
              // this card. Speech = source language; subtitles = whatever text is baked in
              // (same as speech for a fresh short).
              const speechLang = short.source_language ?? short.language ?? 'Polski'
              const subLang = short.language ?? speechLang
              const renderedPlayer = renderedPlayers[i] ?? (
                short.rendered_file && projectId
                  ? { url: renderedFileUrl(projectId, short.rendered_file, short.rendered_at), title: short.title }
                  : null
              )
              return (
                <div className="short-item" key={i} ref={(el) => { shortRefs.current[i] = el }}>
                  <ViralScore score={short.score} />
                  <div className="short-item-main">
                    <div className="short-item-top">
                      <div className="short-item-body">
                        <strong>Short {i + 1} z {shorts.length}</strong>
                        <span>Napisy: {subLang} · Język: {speechLang}</span>
                      </div>
                      <button
                        type="button"
                        className={isFavorite(i) ? 'favorite-toggle active' : 'favorite-toggle'}
                        disabled={!projectId}
                        title={isFavorite(i) ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'}
                        onClick={() => toggleFavorite(i, short)}
                      >
                        <Heart size={22} fill={isFavorite(i) ? 'currentColor' : 'none'} />
                      </button>
                    </div>
                    <div className="short-content-grid">
                      <div className="short-video-col">
                        {renderedPlayer ? (
                          <div className="short-player inline">
                            <video src={renderedPlayer.url} controls playsInline preload="metadata" className="short-player-video" />
                          </div>
                        ) : (
                          <div className="short-video-placeholder">
                            <Clapperboard size={20} />
                            <span>Short czeka na render</span>
                          </div>
                        )}
                      </div>
                      <div className="short-publish">
                        <div className="short-meta-grid">
                          <MetaBox label="Tytuł" value={short.title} />
                          <MetaBox label="Opis" value={short.hook_text} />
                          <MetaBox label="Hashtagi" value={short.hashtags} />
                          <MetaBox label="Tagi YouTube" value={short.yt_tags} />
                        </div>
                      </div>
                    </div>
                      <div className="short-tools">
                        {projectId && short.rendered_file && (
                          <div className="download-row priority">
                            <a className="primary-btn download-main" href={renderedFileUrl(projectId, short.rendered_file, short.rendered_at)} download title="Pobierz gotowy short (wideo MP4)">
                              <Clapperboard size={15} /> Pobierz wideo
                            </a>
                            <a className="ghost-btn download-secondary" href={shortAudioUrl(projectId, i)} download title="Pobierz samą ścieżkę audio (MP3)">
                              <Mic2 size={15} /> Pobierz audio
                            </a>
                            <a className="ghost-btn download-secondary" href={`${getBase()}/api/shorts/srt/${encodeURIComponent(projectId)}/${i}`} download title="Pobierz plik napisów (.srt)">
                              <Captions size={15} /> Pobierz napisy
                            </a>
                          </div>
                        )}
                        <div className="short-tool-group">
                          <span>Tłumaczenie napisów</span>
                          <select
                            className="mini-select"
                            value={effectiveTranslateLang(i)}
                            onChange={(e) => setTranslateLang((prev) => ({ ...prev, [i]: e.target.value }))}
                          >
                            {translateLangs.map((lang) => <option key={lang} value={lang}>{lang}</option>)}
                          </select>
                          <button
                            type="button"
                            className="ghost-btn"
                            disabled={!projectId || translateJob.state.status === 'running'}
                            onClick={() => translateShort(i)}
                          >
                            {translatingIdx === i ? `${Math.round(translateJob.state.progress * 100)}%` : 'Przetłumacz'}
                          </button>
                        </div>
                        <div className="short-tool-group">
                          <span>Dubbing shorta</span>
                          <select
                            className="mini-select"
                            value={dubLang[i] || 'Angielski'}
                            onChange={(e) => setDubLang((prev) => ({ ...prev, [i]: e.target.value }))}
                          >
                            {dubLangs.map((lang) => <option key={lang} value={lang}>{lang}</option>)}
                          </select>
                          <button
                            type="button"
                            className="primary-btn render-btn"
                            disabled={!projectId || dubJob.state.status === 'running'}
                            onClick={() => renderDub(i)}
                          >
                            {dubbingIdx === i ? `${Math.round(dubJob.state.progress * 100)}%` : 'Generuj dubbing'}
                          </button>
                          <button
                            type="button"
                            className="ghost-btn"
                            disabled={!projectId || clearingCacheIdx === i}
                            title="Usuń zapisane tłumaczenia tego shorta (cache). Następny dubbing/tłumaczenie policzy się od zera — naprawia np. mieszany język po wcześniejszym błędzie."
                            onClick={() => clearTranslationCache(i)}
                          >
                            <Trash2 size={14} /> {clearingCacheIdx === i ? 'Czyszczę…' : 'Wyczyść cache tłumaczenia'}
                          </button>
                          <button
                            type="button"
                            className="ghost-btn"
                            disabled={!projectId || clearingDemucsIdx === i}
                            title="Usuń zapisaną separację tła (Demucs) tego shorta. Następny dubbing rozdzieli tło od nowa — użyj, gdy dźwięk tła brzmi uszkodzony lub urwany."
                            onClick={() => clearDemucsCache(i)}
                          >
                            <Trash2 size={14} /> {clearingDemucsIdx === i ? 'Czyszczę…' : 'Wyczyść cache tła (Demucs)'}
                          </button>
                        </div>
                        <button
                          type="button"
                          className="ghost-btn short-action-btn"
                          title={projectId ? 'Edytuj napisy (timing zostaje idealny)' : 'Najpierw przeanalizuj lub wczytaj projekt'}
                          disabled={!projectId}
                          onClick={() => setEditorIdx(i)}
                        >
                          <Captions size={15} /> Edytuj napisy
                        </button>
                        <button
                          type="button"
                          className="ghost-btn short-action-btn"
                          title={projectId ? 'Edytuj sceny i cięcia shorta' : 'Najpierw przeanalizuj lub wczytaj projekt'}
                          disabled={!projectId}
                          onClick={() => setSceneEditorIdx(i)}
                        >
                          <Scissors size={15} /> Edytuj sceny
                        </button>
                        {(shorts[i]?.segments?.length ?? 0) > 4 && (
                          <button
                            type="button"
                            className="ghost-btn short-action-btn"
                            title="Stare shorty pokazują dziesiątki drobnych wycinków TTS zamiast scen — scal je z powrotem w sceny (bez zmiany wideo)."
                            disabled={!projectId || mergingIdx === i}
                            onClick={() => mergeScenes(i)}
                          >
                            <Scissors size={15} /> {mergingIdx === i ? 'Scalam…' : 'Napraw sceny'}
                          </button>
                        )}
                        <button
                          type="button"
                          className="primary-btn short-action-btn"
                          disabled={!projectId || renderJob.state.status === 'running'}
                          title={projectId ? 'Renderuj / przegeneruj z aktualnymi ustawieniami' : 'Najpierw przeanalizuj lub wczytaj projekt'}
                          onClick={() => renderShort(i)}
                        >
                          <Clapperboard size={15} /> {busy ? `${Math.round(renderJob.state.progress * 100)}%` : renderedPlayer ? 'Przegeneruj' : 'Renderuj'}
                        </button>
                        <button
                          type="button"
                          className="ghost-btn danger short-action-btn"
                          title={projectId ? 'Usuń shorta i jego pliki z dysku' : 'Najpierw przeanalizuj lub wczytaj projekt'}
                          disabled={!projectId}
                          onClick={() => deleteShort(i)}
                        >
                          <Trash2 size={15} /> Usuń
                        </button>
                      </div>
                      {busy && (
                        <InlineJobProgress state={renderJob.state} label="Generowanie / renderowanie shorta" onCancel={renderJob.cancel} />
                      )}
                      {translating && (
                        <InlineJobProgress state={translateJob.state} label="Tłumaczenie napisów i renderowanie nowej wersji" onCancel={translateJob.cancel} />
                      )}
                      {dubbing && (
                        <InlineJobProgress state={dubJob.state} label="Dubbing shorta" onCancel={dubJob.cancel} />
                      )}
                      {dubNotice && (
                        <div className={dubNotice.kind === 'err' ? 'short-inline-status err' : 'short-inline-status ok'}>
                          {dubNotice.message}
                        </div>
                      )}
                      {(versions[i] ?? []).length > 0 && (
                        <div className="version-list">
                          {(versions[i] ?? []).map((version, vi) => {
                            const versionShort = version.short_data ?? {}
                            return (
                              <DubbedVersionCard
                                key={`${version.language}-${vi}`}
                                index={i}
                                total={shorts.length}
                                version={version}
                                short={versionShort}
                                artifactUrl={artifactUrl}
                                srtUrl={projectId ? `${getBase()}/api/shorts/srt/${encodeURIComponent(projectId)}/${i}/versions/${encodeURIComponent(version.language_slug ?? slugLanguage(version.language ?? ''))}` : ''}
                                rendering={versionRenderingKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`}
                                renderProgress={versionRenderJob.state.progress}
                                renderMessage={versionRenderJob.state.message}
                                favorite={isVersionFavorite(i, version.language_slug ?? slugLanguage(version.language ?? ''))}
                                notice={dubNotices[`version:${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`]
                                  ?? dubNotices[`subs:${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`]}
                                subLangs={translateLangs}
                                translatingSubs={versionSubsKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`}
                                batchingSubs={versionSubsBatchKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`}
                                subsProgress={versionSubsBatchKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}` ? versionSubsBatchJob.state.progress : versionSubsJob.state.progress}
                                subsMessage={versionSubsBatchKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}` ? versionSubsBatchJob.state.message : versionSubsJob.state.message}
                                onFavorite={() => toggleVersionFavorite(i, version)}
                                onEdit={() => setVersionEditor({
                                  index: i,
                                  languageSlug: version.language_slug ?? slugLanguage(version.language ?? ''),
                                  short: versionShort,
                                })}
                                onRender={() => renderDubVersion(i, version.language_slug ?? slugLanguage(version.language ?? ''))}
                                onTranslateSubs={(subLang) => translateVersionSubs(i, version.language_slug ?? slugLanguage(version.language ?? ''), subLang)}
                                onTranslateSubsBatch={(subLangsSel) => translateVersionSubsBatch(i, version.language_slug ?? slugLanguage(version.language ?? ''), subLangsSel)}
                                mergingScenes={mergingVersionKey === `${i}:${version.language_slug ?? slugLanguage(version.language ?? '')}`}
                                onMergeScenes={() => mergeVersionScenes(i, version.language_slug ?? slugLanguage(version.language ?? ''))}
                                onDelete={() => deleteDubVersion(i, version.language_slug ?? slugLanguage(version.language ?? ''))}
                              />
                            )
                          })}
                        </div>
                      )}
                  </div>
                </div>
              )
            })}
            <button
              type="button"
              className="ghost-btn custom-short-add"
              disabled={!projectId}
              onClick={() => setCustomShortOpen(true)}
              title="Otwórz pełny film i wytnij własny short — sceny, precyzyjny start/koniec, podgląd napisów. Na końcu nałoży się ten sam styl, napisy, logo i watermark."
            >
              <Scissors size={16} /> Stwórz własny short z całego filmu
            </button>
          </div>
        )}

        {!online && (
          <p className="offline-note">
            Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>, aby aktywować silniki.
          </p>
        )}
      </section>

      <aside className="inspector accordions">
        <StyledPreview
          s={s}
          media={media}
          fonts={meta.shorts.fonts}
          presetFont={meta.shorts.preset_data[s.sub_preset]?.font_name}
        />

        <Section icon={<Crop size={16} />} title="Format i kadr">
          <Field label="Format obrazu">
            <Select value={s.aspect_ratio} options={meta.shorts.aspect_ratios} onChange={(v) => set({ aspect_ratio: v })} />
          </Field>
          <Slider label="Wypełnienie pionowego ekranu (%)" value={s.fill_mode} min={0} max={100} step={5}
            hint="0% = cały obraz z czarnymi pasami. 100% = pełny ekran pionowy (przycięte boki). Ignorowane (wymusza 100%), gdy włączysz Śledzenie twarzy."
            onChange={(v) => set({ fill_mode: v })} />
          <Toggle checked={!!s.blur_bg} label="Włącz rozmyte tło" hint="Wypełnia czarne pasy rozmytą, powiększoną kopią obrazu zamiast czerni. Uwaga: gdy włączysz wirtualną kamerę AI ze Smart Reframingiem, to ustawienie jest pomijane — rozmyciem tła steruje wtedy opcja „Rozmyte tło (tylko przy zoom-out)” w sekcji „Wirtualna kamera (AI)”." onChange={(v) => set({ blur_bg: v })} />
          {s.smart_reframe && s.blur_bg && (
            <p className="settings-desc">Wirtualna kamera AI jest włączona — rozmyciem tła steruje opcja w sekcji „Wirtualna kamera (AI)”, a to ustawienie jest tu ignorowane.</p>
          )}
          {s.blur_bg && (
            <>
              <Slider label="Siła rozmycia" value={s.blur_sigma} min={5} max={100} step={5} hint="Im wyżej, tym mocniej rozmyte tło." onChange={(v) => set({ blur_sigma: v })} />
              <Slider label="Powiększenie tła" value={s.blur_zoom} min={1} max={3} step={0.1} hint="Powiększa tło, aby wypełnić kadr bez widocznych krawędzi." onChange={(v) => set({ blur_zoom: v })} />
              <Slider label="Jasność tła (%)" value={s.blur_bright} min={10} max={100} step={5} hint="Przyciemnia tło, aby napisy i główny obraz były lepiej widoczne." onChange={(v) => set({ blur_bright: v })} />
            </>
          )}
        </Section>

        <Section icon={<Captions size={16} />} title="Napisy">
          <Toggle checked={!!s.enable_subtitles} label="Generuj dynamiczne napisy" onChange={(v) => set({ enable_subtitles: v })} />
          <Field label="Styl napisów (preset)" hint="Gotowy styl napisów. Wybór ustawia kolory, rozmiary i krój — podgląd po prawej zmienia się od razu.">
            <Select value={s.sub_preset} options={meta.shorts.presets} onChange={applyPreset} />
          </Field>
          <Field label="Czcionka" hint="Lista pokazuje podgląd każdej czcionki. 'Domyślna dla presetu' używa kroju zdefiniowanego w wybranym stylu.">
            <FontSelect
              value={s.custom_font}
              options={['Domyślna dla presetu', ...meta.shorts.fonts]}
              onChange={(v) => set({ custom_font: v })}
            />
          </Field>
          <div className="color-row">
            <Field label="Tekst"><ColorField value={s.sub_bcolor} onChange={(v) => set({ sub_bcolor: v })} /></Field>
            <Field label="Podświetlenie"><ColorField value={s.sub_hcolor} onChange={(v) => set({ sub_hcolor: v })} /></Field>
          </div>
          <div className="color-row">
            <Field label="Obrys"><ColorField value={s.sub_out_color} onChange={(v) => set({ sub_out_color: v })} /></Field>
            <Field label="Cień"><ColorField value={s.sub_shad_color} onChange={(v) => set({ sub_shad_color: v })} /></Field>
          </div>
          <Field label="Tryb wyświetlania" hint="Jak pojawiają się napisy: „highlight” — widać całą linijkę, aktywne słowo jest podświetlone kolorem; „word_by_word” — słowa pojawiają się pojedynczo; „build_up” — tekst narasta słowo po słowie; „highlight_box” — pod aktualnym słowem jest kolorowe tło; „fade” — łagodne pojawianie się.">
            <Select value={s.sub_mode} options={meta.shorts.sub_modes} onChange={(v) => set({ sub_mode: v })} />
          </Field>
          <Field label="Efekt animacji" hint="Animacja pojawiania się napisów (np. wyskakiwanie, karaoke, rozmycie).">
            <Select value={s.sub_animation} options={meta.shorts.animations} onChange={(v) => set({ sub_animation: v })} />
          </Field>
          <Slider label="Rozmiar bazowy" value={s.sub_size} min={10} max={150} onChange={(v) => set({ sub_size: v })} />
          <Slider label="Rozmiar podświetlenia" value={s.sub_hsize} min={10} max={150} onChange={(v) => set({ sub_hsize: v })} />
          <Slider label="Słów w bloku" value={s.sub_words} min={1} max={10} onChange={(v) => set({ sub_words: v })} />
          <Slider label="Grubość obrysu" value={s.sub_out_thick} min={0} max={20} onChange={(v) => set({ sub_out_thick: v })} />
          <Slider label="Wielkość cienia" value={s.sub_shad_size} min={0} max={20} onChange={(v) => set({ sub_shad_size: v })} />
          <Slider label="Pozycja od dołu" value={s.sub_margin} min={0} max={2000} step={10} suffix=" px" hint="Odległość napisów od dolnej krawędzi kadru." onChange={(v) => set({ sub_margin: v })} />
          <Slider label="Wielkość tła słowa (%)" value={s.sub_bg_pad} min={0} max={200} step={5} hint="Grubość kolorowego tła pod aktywnym słowem. Działa tylko w trybie wyświetlania z tłem pod słowem („highlight_box”)." onChange={(v) => set({ sub_bg_pad: v })} />
          <div className="toggle-grid">
            <Toggle checked={!!s.sub_bold} label="Pogrub" onChange={(v) => set({ sub_bold: v })} />
            <Toggle checked={!!s.sub_italic} label="Kursywa" onChange={(v) => set({ sub_italic: v })} />
            <Toggle checked={!!s.sub_upper} label="WIELKIE" onChange={(v) => set({ sub_upper: v })} />
            <Toggle checked={!!s.sub_punct} label="Z. przest." onChange={(v) => set({ sub_punct: v })} />
            <Toggle checked={!!s.sub_autoscale} label="Auto-skala" onChange={(v) => set({ sub_autoscale: v })} />
          </div>
        </Section>

        <Section icon={<Image size={16} />} title="Logo graficzne">
          <Toggle checked={!!s.enable_logo} label="Dodaj graficzne logo" onChange={(v) => set({ enable_logo: v })} />
          {s.enable_logo && (
            <>
              <Field label="Plik logo">
                <div className="logo-picker">
                  <Select value={s.logo_path ?? 'workspace/logo.png'} options={logoOptions} onChange={(v) => set({ logo_path: v, logo_url: '' })} />
                  <button type="button" className="ghost-btn icon-btn" onClick={addLogo} title="Dodaj własne logo">
                    <Upload size={15} />
                  </button>
                </div>
              </Field>
              <Slider label="Skala (%)" value={s.logo_scale} min={5} max={100} hint="Wielkość logo względem kadru. Więcej = większe logo." onChange={(v) => set({ logo_scale: v })} />
              <Slider label="Pozycja X (%)" value={s.logo_x} min={0} max={100} hint="Pozycja logo w poziomie: 0% = przy lewej krawędzi, 100% = przy prawej." onChange={(v) => set({ logo_x: v })} />
              <Slider label="Pozycja Y (%)" value={s.logo_y} min={0} max={100} hint="Pozycja logo w pionie: 0% = przy górnej krawędzi, 100% = przy dolnej." onChange={(v) => set({ logo_y: v })} />
              <Slider label="Przezroczystość (%)" value={s.logo_opacity} min={0} max={100} hint="100% = logo w pełni widoczne, mniej = bardziej prześwitujące." onChange={(v) => set({ logo_opacity: v })} />
            </>
          )}
        </Section>

        <Section icon={<Stamp size={16} />} title="Napis / znak wodny">
          <Toggle checked={!!s.enable_text} label="Dodaj napis (znak wodny)" onChange={(v) => set({ enable_text: v })} />
          {s.enable_text && (
            <>
              <Field label="Tekst znaku">
                <input className="text-field" value={s.wm_text ?? ''} onChange={(e) => set({ wm_text: e.target.value })} />
              </Field>
              <Field label="Czcionka znaku">
                <FontSelect value={s.wm_font} options={meta.shorts.fonts} onChange={(v) => set({ wm_font: v })} />
              </Field>
              <div className="color-row">
                <Field label="Kolor tekstu"><ColorField value={s.wm_color} onChange={(v) => set({ wm_color: v })} /></Field>
                <Field label="Kolor obrysu"><ColorField value={s.wm_out_color} onChange={(v) => set({ wm_out_color: v })} /></Field>
              </div>
              <Field label="Kolor cienia"><ColorField value={s.wm_shad_color} onChange={(v) => set({ wm_shad_color: v })} /></Field>
              <Slider label="Rozmiar" value={s.wm_size} min={10} max={200} hint="Wielkość liter napisu." onChange={(v) => set({ wm_size: v })} />
              <Slider label="Grubość obrysu" value={s.wm_out_thick} min={0} max={20} hint="Grubość konturu wokół liter — poprawia czytelność na jasnym tle. 0 = bez obrysu." onChange={(v) => set({ wm_out_thick: v })} />
              <Slider label="Wielkość cienia" value={s.wm_shad_size} min={0} max={20} hint="Wielkość cienia rzucanego przez napis. 0 = bez cienia." onChange={(v) => set({ wm_shad_size: v })} />
              <Slider label="Pozycja X (%)" value={s.wm_x} min={0} max={100} hint="Pozycja napisu w poziomie: 0% = lewa krawędź, 100% = prawa." onChange={(v) => set({ wm_x: v })} />
              <Slider label="Pozycja Y (%)" value={s.wm_y} min={0} max={100} hint="Pozycja napisu w pionie: 0% = góra, 100% = dół." onChange={(v) => set({ wm_y: v })} />
              <Slider label="Widoczność (%)" value={s.wm_opacity} min={0} max={100} hint="100% = napis w pełni widoczny, mniej = bardziej prześwitujący." onChange={(v) => set({ wm_opacity: v })} />
              <div className="toggle-grid">
                <Toggle checked={!!s.wm_bold} label="Pogrub" onChange={(v) => set({ wm_bold: v })} />
                <Toggle checked={!!s.wm_italic} label="Kursywa" onChange={(v) => set({ wm_italic: v })} />
              </div>
            </>
          )}
        </Section>

        <Section icon={<Mic2 size={16} />} title="Audio i dubbing">
          <Field label="Tryb audio" hint="Co zrobić z dźwiękiem: „Oryginalne audio” — bez zmian. „Czysty dubbing” — kasuje oryginalny głos, zostaje sam głos AI (plus tło, jeśli włączysz). „Dubbing + tło z filmu” — głos AI na muzyce i dźwiękach z filmu. „Lektor na oryginalnym audio” — oryginał gra w tle, a lektor AI czyta na wierzchu (jak w telewizji).">
            <Select value={s.audio_mode ?? 'Czysty dubbing (usuń oryginalny głos)'} options={DUB_MIX_MODES} onChange={(v) => set({ audio_mode: v })} />
          </Field>

          {s.audio_mode !== 'Oryginalne audio' && (() => {
            const mode = s.audio_mode ?? 'Czysty dubbing (usuń oryginalny głos)'
            const isLektor = mode === 'Lektor na oryginalnym audio'
            const isBgMode = mode === 'Czysty dubbing (usuń oryginalny głos)' || mode === 'Dubbing + tło z filmu'
            const usesBackground = isBgMode && (s.dub_keep_background ?? true)
            const isOmni = ttsEngineOf(config) === 'omnivoice'
            // OmniVoice has no preset speakers — hide that source when it's active.
            const voiceSourceOptions = isOmni ? DUB_VOICE_SOURCES.filter((v) => v !== 'Głos z bazy Qwen TTS') : DUB_VOICE_SOURCES
            const rawVoiceSource = s.dub_voice_source ?? 'Głos z oryginalnego filmu'
            const voiceSource = isOmni && rawVoiceSource === 'Głos z bazy Qwen TTS' ? 'Głos z oryginalnego filmu' : rawVoiceSource
            return (
            <>
              <p className="settings-desc" style={{ marginTop: 0 }}>
                Aktywny silnik: <strong>{isOmni ? 'OmniVoice' : 'Qwen TTS'}</strong>. Zmienisz go w Ustawieniach → „Silnik głosu (TTS)”.
              </p>
              <div className="color-row">
                <Field label="Język dubbingu" hint={isOmni ? 'Język, w którym OmniVoice wygeneruje nowy głos (obsługuje m.in. polski).' : 'Język, w którym Qwen wygeneruje nowy głos.'}>
                  <Select value={s.dub_target_lang ?? 'Angielski'} options={dubLangs} onChange={(v) => set({ dub_target_lang: v })} />
                </Field>
                <Field label="Źródło głosu" hint="Skąd wziąć głos lektora. „Z oryginalnego filmu” — kopiuje (klonuje) barwę głosu mówcy z klipu. „Własna próbka” — używa Twojego nagrania głosu. „Baza Qwen” — gotowy, syntetyczny głos modelu, nic nie musisz nagrywać.">
                  <Select value={voiceSource} options={voiceSourceOptions} onChange={(v) => set({ dub_voice_source: v })} />
                </Field>
              </div>

              {/* Voice-source-specific selector sits directly under Język/Źródło. */}
              {voiceSource === 'Głos z bazy Qwen TTS' ? (
                <Field label="Głos Qwen" hint="Wybierz jeden z gotowych głosów modelu (różne barwy męskie i żeńskie). Nic nie nagrywasz — od razu gotowe do użycia.">
                  <Select value={s.dub_qwen_speaker ?? 'Aiden'} options={QWEN_SPEAKERS} onChange={(v) => set({ dub_qwen_speaker: v })} />
                </Field>
              ) : voiceSource === 'Własna próbka głosu' ? (
                <Field label="Próbka głosu" hint="Wybierz zapisaną próbkę albo wgraj własne nagranie głosu, który chcesz sklonować. Najlepiej 8–20 sekund czystej mowy, bez muzyki i szumów w tle.">
                  <div className="voice-picker">
                    <select
                      className="text-field voice-select"
                      value={s.dub_selected_voice_path ?? ''}
                      onChange={(e) => set({ dub_selected_voice_path: e.target.value })}
                    >
                      <option value="">Wybierz próbkę głosu...</option>
                      {meta.voices.map((voice) => (
                        <option key={voice.id} value={voice.path}>{voice.label}</option>
                      ))}
                    </select>
                    <VoiceSamplePreview path={s.dub_selected_voice_path ?? ''} />
                    <button type="button" className="ghost-btn upload-voice-btn" onClick={addVoiceSample}>
                      <Upload size={15} /> Wgraj próbkę
                    </button>
                  </div>
                  {s.dub_selected_voice_path && (
                    <input
                      className="text-field voice-path"
                      value={s.dub_selected_voice_path ?? ''}
                      onChange={(e) => set({ dub_selected_voice_path: e.target.value })}
                    />
                  )}
                  <button type="button" className="link-btn manage-voices-toggle" onClick={() => setManageVoices((v) => !v)}>
                    {manageVoices ? 'Zamknij zarządzanie' : `Zarządzaj próbkami (${meta.voices.length})`}
                  </button>
                  {manageVoices && (
                    <div className="voice-manager">
                      {meta.voices.length === 0 && <p className="settings-desc">Brak zapisanych próbek głosu.</p>}
                      {meta.voices.map((voice) => (
                        <VoiceManagerRow
                          key={voice.path}
                          voice={voice}
                          selected={s.dub_selected_voice_path === voice.path}
                          onRename={renameVoice}
                          onDelete={deleteVoice}
                        />
                      ))}
                    </div>
                  )}
                </Field>
              ) : (
                <>
                  <Field label="Rodzaj głosu z oryginału" hint="„Odfiltrowany głos” — czysty głos mówcy bez pogłosu, muzyki i odgłosów otoczenia (zalecane — najwierniejszy klon). „Oryginalny z ambientem” — głos wraz z tłem i pogłosem oryginału.">
                    <Select
                      value={String(s.dub_voice_ref ?? 'filtered') === 'ambient' ? 'Oryginalny z ambientem' : 'Odfiltrowany głos (czysty)'}
                      options={['Odfiltrowany głos (czysty)', 'Oryginalny z ambientem']}
                      onChange={(v) => set({ dub_voice_ref: v.startsWith('Oryginalny') ? 'ambient' : 'filtered' })}
                    />
                  </Field>
                  <Slider label="Długość próbki głosu" value={s.dub_ref_audio_length ?? 12} min={3} max={25} suffix=" s" hint="Ile sekund mowy z filmu nagrać jako wzór głosu do klonowania. 8–15 s zwykle wystarcza — za krótka próbka daje gorsze podobieństwo." onChange={(v) => set({ dub_ref_audio_length: v })} />
                </>
              )}

              <div className="toggle-grid">
                <Toggle checked={s.dub_auto_subtitles ?? true} label="Napisy w języku audio" hint="Przy dubbingu automatycznie przetłumaczy też napisy na język lektora, żeby napisy zgadzały się z tym, co słychać." onChange={(v) => set({ dub_auto_subtitles: v })} />
                {isBgMode && (
                  <Toggle checked={s.dub_keep_background ?? true} label="Zostaw tło filmu" hint="Zostawia muzykę i dźwięki tła z oryginału, a usuwa tylko oryginalny głos. Włącz, gdy w filmie gra muzyka — inaczej zniknie razem z głosem." onChange={(v) => set({ dub_keep_background: v })} />
                )}
              </div>

              <Slider label="Głośność dubbingu" value={s.dub_voice_volume ?? 1.5} min={0.1} max={3} step={0.05} hint="Głośność nowego głosu AI w gotowym filmie. 1 = bez zmian, wyżej = głośniej. Podnieś, jeśli lektor ginie pod muzyką." onChange={(v) => set({ dub_voice_volume: v })} />
              {usesBackground && (
                <Slider label="Głośność tła" value={s.dub_background_volume ?? 1.4} min={0} max={2} step={0.05} hint="Głośność muzyki i dźwięków tła z oryginalnego filmu (działa, gdy włączysz „Zostaw tło filmu”). 1 = bez zmian." onChange={(v) => set({ dub_background_volume: v })} />
              )}
              {isLektor && (
                <Slider label="Głośność oryginału" value={s.dub_original_volume ?? 0.85} min={0} max={1.5} step={0.05} hint="Głośność oryginalnej ścieżki filmu, gdy lektor NIE mówi. 1 = normalnie. To bazowa głośność tła pod lektorem — osobno ustawiasz, jak mocno ma się ściszać w trakcie mowy (suwak niżej)." onChange={(v) => set({ dub_original_volume: v })} />
              )}

              <div className="toggle-grid">
                <Toggle checked={!!s.dub_auto_min_tempo} label="AUTO min tempo" hint="Włączone = aplikacja sama decyduje, jak bardzo może zwolnić głos AI, gdy tłumaczenie jest krótsze niż scena. Wyłącz, by ustawić to ręcznie suwakiem poniżej." onChange={(v) => set({ dub_auto_min_tempo: v })} />
                <Toggle checked={s.dub_auto_max_tempo ?? true} label="AUTO max tempo" hint="Włączone = aplikacja sama dobiera, jak bardzo przyspieszyć głos AI, by zmieścił się w czasie sceny, ale wciąż brzmiał naturalnie. Wyłącz, by ustawić ręcznie." onChange={(v) => set({ dub_auto_max_tempo: v })} />
              </div>
              {!s.dub_auto_min_tempo && (
                <Slider label="Minimalne tempo" value={s.dub_sync_min_tempo ?? 0.9} min={0.5} max={1.5} step={0.05} hint="Najwolniej, jak głos AI może być odtworzony (1 = normalne tempo, mniej = wolniej). Niższa wartość pomaga, gdy tłumaczenie jest krótsze niż scena — głos rozciąga się, by ją wypełnić." onChange={(v) => set({ dub_sync_min_tempo: v })} />
              )}
              {!s.dub_auto_max_tempo && (
                <Slider label="Maksymalne tempo" value={s.dub_sync_max_tempo ?? 1.0} min={1} max={2} step={0.05} hint="Najszybciej, jak głos AI może być odtworzony (1 = normalne tempo, więcej = szybciej). Wyższa wartość pomaga zmieścić dłuższe tłumaczenie w krótszej scenie, ale za dużo brzmi nienaturalnie." onChange={(v) => set({ dub_sync_max_tempo: v })} />
              )}
              {isLektor && (
                <Slider label="Ściszanie oryginału pod lektorem" value={s.dub_duck_amount ?? 0.95} min={0} max={1} step={0.05} hint="Jak mocno oryginalny dźwięk przycisza się, GDY mówi lektor (poza tym gra normalnie). Im wyżej, tym ciszej oryginał pod głosem AI: 0 = wcale nie ścisza, 0,8 = mocno przyciszony, 1 = prawie niesłyszalny. Po słowach lektora oryginał wraca do normalnej głośności." onChange={(v) => set({ dub_duck_amount: v })} />
              )}
              <Slider label="Korekta tonu" value={s.dub_pitch_adjust ?? 0} min={-12} max={12} step={0.5} hint="Podnosi (+) lub obniża (−) wysokość głosu AI. 0 = bez zmian. Minus = niższy, poważniejszy głos; plus = wyższy. Zwykle wystarcza zakres od −3 do +3." onChange={(v) => set({ dub_pitch_adjust: v })} />

              {isOmni && <OmniVoiceParams d={s as Record<string, unknown>} set={set} showSpeed={false} />}

              {!isOmni && (
              <Field label="Styl głosu / instrukcja dla TTS" hint="Opisz krótko, jak ma brzmieć lektor, np. „spokojny, ciepły” albo „energiczny, dynamiczny lektor YouTube”. Zostaw puste dla neutralnego brzmienia.">
                <TextArea
                  value={s.dub_style_prompt ?? ''}
                  rows={4}
                  placeholder="np. Naturalny, energiczny lektor YouTube Shorts, bez przesadnej teatralności."
                  onChange={(v) => set({ dub_style_prompt: v })}
                />
              </Field>
              )}
            </>
            )
          })()}
        </Section>

        <Section icon={<ScanFace size={16} />} title="Wirtualna kamera (AI)">
          <Toggle checked={!!s.face_tracking} label="Śledź osobę (wirtualna kamera)"
            hint="Wirtualna kamera płynnie podąża za osobą w kadrze (śledzenie z prawdziwym trackerem ByteTrack/BoT-SORT — bez skoków i szarpnięć). Wymusza 100% wypełnienia pionowego ekranu, aby AI miało miejsce na ruch kamery."
            onChange={(v) => set({ face_tracking: v })} />
          {!!s.face_tracking && (
            <>
              <Toggle checked={!!s.smart_reframe} label="Smart Reframing (auto-kadr)"
                hint="Automatyczny kadr zależny od liczby osób: gdy w kadrze jest DOKŁADNIE JEDNA osoba — kamera wypełnia pionowy ekran 9:16 i podąża za nią. Gdy nie ma nikogo ALBO są co najmniej dwie osoby — robi ZOOM-OUT: cały kadr 16:9 trafia na środek pionowego ekranu (z czarnymi pasami u góry i dołu), nikt nie zostaje ucięty. Przejścia są płynnie przenikane." onChange={(v) => set({ smart_reframe: v })} />
              <Field label="Tracker AI" hint="Silnik śledzenia. „ByteTrack” — najszybszy, idealny gdy w kadrze jest jedna osoba. „BoT-SORT” — odporny na zasłanianie i wiele osób (kompensacja ruchu kamery + ReID), nieco wolniejszy. „Auto” sam dobiera na podstawie liczby osób w klipie. Zalecane: Auto.">
                <Select value={s.ft_tracker ?? 'Auto (sam dobiera)'} options={meta.shorts.ft_trackers ?? ['Auto (sam dobiera)']} onChange={(v) => set({ ft_tracker: v })} />
              </Field>
              {s.smart_reframe && (
                <p className="settings-desc">
                  Smart Reframing decyduje po LICZBIE OSÓB w kadrze: 1 osoba → zoom na nią; 0 osób (np. produkt) lub 2+ osób → zoom-out i pokazujemy całą scenę (wszyscy widoczni, bez skakania między mówiącymi). Poniższe Zoom / Przesunięcie Y / Płynność działają tylko gdy kamera zoomuje na jedną osobę. Dla automatu zostaw: Zoom 1.0, Przesunięcie Y 0, Płynność ok. 60.
                </p>
              )}
              {s.face_tracking && !s.smart_reframe && (
                <Field label="Strategia" hint="Kogo śledzić, gdy w kadrze jest więcej osób (tylko w trybie „Śledź osobę” bez Smart Reframingu). „Główny mówca” — trzyma się największej/najbardziej wyeksponowanej osoby. „Utrzymuj cel” — blokuje się na pierwszej namierzonej osobie i trzyma ją mimo zasłonięć.">
                  <Select value={s.ft_strategy} options={meta.shorts.ft_strategies} onChange={(v) => set({ ft_strategy: v })} />
                </Field>
              )}
              {s.smart_reframe && (
                <>
                  <Toggle checked={!!s.cam_blur_bg} label="Rozmyte tło (tylko przy zoom-out)"
                    hint="Gdy Smart Reframing robi ZOOM-OUT, zamiast czarnych pasów u góry i dołu wstawia rozmytą, powiększoną kopię kadru. Działa TYLKO przy zoom-out — przy kadrze na osobie (zoom-in) jest pomijane, więc nie spowalnia renderu. Gdy włączone, przełącznik „Włącz rozmyte tło” z zakładki „Format i kadr” jest ignorowany (kamera AI używa tych ustawień)." onChange={(v) => set({ cam_blur_bg: v })} />
                  {s.cam_blur_bg && (
                    <>
                      <Slider label="Siła rozmycia" value={s.blur_sigma} min={5} max={100} step={5} hint="Im wyżej, tym mocniej rozmyte tło. Te same ustawienia co w „Format i kadr”." onChange={(v) => set({ blur_sigma: v })} />
                      <Slider label="Powiększenie tła" value={s.blur_zoom} min={1} max={3} step={0.1} hint="Powiększa tło, aby wypełnić kadr bez widocznych krawędzi." onChange={(v) => set({ blur_zoom: v })} />
                      <Slider label="Jasność tła (%)" value={s.blur_bright} min={10} max={100} step={5} hint="Przyciemnia tło, aby napisy i główny obraz były lepiej widoczne." onChange={(v) => set({ blur_bright: v })} />
                    </>
                  )}
                </>
              )}
              <Slider label="Zoom" value={s.ft_zoom} min={1} max={3} step={0.1} hint="Jak ciasny ma być kadr na osobie (tryb wypełnienia). 1.0 = pełna wysokość kadru, osoba wypełnia ekran w pionie — zalecane. 1.2–1.5 = ciaśniej, plan na głowę i ramiona. Bez znaczenia przy zoom-out." onChange={(v) => set({ ft_zoom: v })} />
              <Slider label="Przesunięcie Y (%)" value={s.ft_y_offset} min={-50} max={50} hint="Korekta góra-dół kadru (tryb wypełnienia). 0 = środek. Ustaw wartość ujemną, jeśli ucina czubek głowy. Bez znaczenia przy zoom-out." onChange={(v) => set({ ft_y_offset: v })} />
              <Slider label="Płynność kamery" value={s.ft_smoothness} min={1} max={100} hint="Jak gładko kamera podąża za osobą (tryb wypełnienia). Niska = szybkie, sprężyste reakcje. Wysoka (60–90) = miękkie, kinowe podążanie jak na gimbalu. Zalecane ok. 60. Bez znaczenia przy zoom-out." onChange={(v) => set({ ft_smoothness: v })} />
              {s.smart_reframe && (
                <Slider label="Szybkość przełączania kadru" value={s.reframe_speed ?? 50} min={1} max={100} hint="Jak szybko Smart Reframing przełącza się między „kadr na osobie” (9:16) a „zoom-out” (16:9), gdy zmienia się liczba OSÓB w kadrze. Niska = ostrożnie, dłużej czeka zanim przełączy (mniej migotania). Wysoka = reaguje błyskawicznie. Zalecane ok. 50–70." onChange={(v) => set({ reframe_speed: v })} />
              )}
            </>
          )}
        </Section>

        <Section icon={<Clapperboard size={16} />} title="Eksport i wydajność">
          <Field label="Rozdzielczość" hint="Jakość/wielkość gotowego pliku. „Zgodna ze źródłem” zachowuje jakość oryginału. Dla Shorts/TikToka/Reels w zupełności wystarcza 1080p.">
            <Select value={s.export_resolution} options={meta.shorts.export_resolutions} onChange={(v) => set({ export_resolution: v })} />
          </Field>
          <Field label="Kodek" hint="Sposób kompresji wideo. „H.264” — odtworzy się wszędzie (najbezpieczniejszy wybór). „H.265 / HEVC” — mniejszy plik przy tej samej jakości, ale wymaga nowszego sprzętu/telefonu.">
            <Select value={s.export_codec} options={meta.shorts.codecs} onChange={(v) => set({ export_codec: v })} />
          </Field>
          <Slider label="Bitrate" value={s.export_bitrate} min={1} max={100} suffix=" Mb/s"
            hint="Dla Shorts/TikToka (1080p) optymalnie 10–15 Mbps (H.264). Dla 4K ustaw 30–45 Mbps." onChange={(v) => set({ export_bitrate: v })} />
          <Toggle checked={!!s.use_proxy} label="Proxy dla plików lokalnych (słabe PC)"
            hint="Tworzy lżejszą kopię roboczą do szybszego podglądu i montażu na słabszym sprzęcie." onChange={(v) => set({ use_proxy: v })} />
          {s.use_proxy && (
            <>
              <Field label="Rozdz. proxy" hint="Rozdzielczość roboczej kopii do szybkiego podglądu i montażu. Nie wpływa na jakość finalnego eksportu.">
                <Select value={s.proxy_res} options={meta.shorts.proxy_resolutions} onChange={(v) => set({ proxy_res: v })} />
              </Field>
              <Slider label="Bitrate proxy" value={s.proxy_bitrate} min={5} max={50} suffix=" Mb/s" hint="Jakość roboczej kopii podglądowej. Niżej = lżejszy plik i płynniejszy podgląd na słabszym sprzęcie." onChange={(v) => set({ proxy_bitrate: v })} />
            </>
          )}
        </Section>

      </aside>

      {keyModal && (
        <div className="modal-overlay" onClick={() => !keySaving && setKeyModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <Brain size={18} />
              <h3>Klucz API Google Gemini</h3>
            </header>
            <p className="modal-desc">
              Generowanie shortów wymaga klucza Gemini — to on wybiera viralowe momenty.
              Wpisz go poniżej, a zapiszemy go w systemie (Ustawienia → Klucze API).
            </p>
            <input
              className="modal-input"
              type="password"
              autoFocus
              placeholder="AIza…"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') saveKey(true) }}
            />
            <a className="modal-link" href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer">
              Skąd wziąć klucz? → aistudio.google.com/app/apikey
            </a>
            <div className="modal-actions">
              <button type="button" className="ghost-btn" disabled={keySaving} onClick={() => setKeyModal(false)}>
                Anuluj
              </button>
              <button type="button" className="ghost-btn" disabled={keySaving || !keyInput.trim()} onClick={() => saveKey(false)}>
                Zapisz
              </button>
              <button type="button" className="primary-btn" disabled={keySaving || !keyInput.trim() || !canRun} onClick={() => saveKey(true)}>
                <Scissors size={15} /> {keySaving ? 'Zapisywanie…' : 'Zapisz i generuj'}
              </button>
            </div>
          </div>
        </div>
      )}

      {editorIdx !== null && shorts[editorIdx] && (
        <SubtitleEditor
          title={shorts[editorIdx].title}
          segments={(shorts[editorIdx].segments ?? []) as any}
          words={(shorts[editorIdx].words ?? []) as any}
          sourceUrl={projectId ? `${getBase()}/api/shorts/projects/${projectId}/source` : undefined}
          projectId={projectId ?? undefined}
          saving={editorSaving}
          onClose={() => setEditorIdx(null)}
          onSave={saveEdit}
        />
      )}

      {versionEditor && (
        <SubtitleEditor
          title={versionEditor.short.title}
          segments={(versionEditor.short.segments ?? []) as any}
          words={(versionEditor.short.words ?? []) as any}
          sourceUrl={projectId ? `${getBase()}/api/shorts/projects/${projectId}/source` : undefined}
          projectId={projectId ?? undefined}
          saving={editorSaving}
          onClose={() => setVersionEditor(null)}
          onSave={saveVersionEdit}
        />
      )}

      {sceneEditorIdx !== null && shorts[sceneEditorIdx] && (
        <SceneEditor
          title={shorts[sceneEditorIdx].title}
          segments={(shorts[sceneEditorIdx].segments ?? []) as any}
          words={(shorts[sceneEditorIdx].words ?? []) as any}
          globalWords={globalWords as any}
          sourceUrl={projectId ? `${getBase()}/api/shorts/projects/${projectId}/source` : undefined}
          projectId={projectId ?? undefined}
          saving={editorSaving}
          onClose={() => setSceneEditorIdx(null)}
          onSave={saveScenes}
        />
      )}

      {customShortOpen && !customShortMin && projectId && (
        <SceneEditor
          createMode
          title={loaded?.title}
          segments={[] as any}
          words={[] as any}
          globalWords={globalWords as any}
          sourceUrl={`${getBase()}/api/shorts/projects/${projectId}/source`}
          projectId={projectId}
          saving={editorSaving}
          persistKey={customDraftKey}
          onClose={discardCustomShort}
          onMinimize={minimizeCustomShort}
          onSave={createCustomShort}
        />
      )}

      {customShortOpen && customShortMin && (
        <div className="custom-restore-bar" role="status">
          <Scissors size={16} />
          <div className="custom-restore-text">
            <strong>Niedokończony własny short</strong>
            <span>{loaded?.title || 'Projekt'} — praca zapisana, możesz wrócić w każdej chwili.</span>
          </div>
          <button type="button" className="primary-btn" onClick={restoreCustomShort}>
            <Scissors size={14} /> Wróć do edycji
          </button>
          <button type="button" className="ghost-btn icon-btn" onClick={discardCustomShort} title="Porzuć tego shorta">
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  )
}

function unique(values: string[]) {
  return [...new Set(values)]
}

function MetaBox({ label, value }: { label: string; value?: string }) {
  const [copied, setCopied] = useState(false)
  const text = value?.trim() || ''
  const copy = async () => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', 'true')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1100)
  }
  return (
    <div className="meta-box">
      <div className="meta-box-head">
        <span>{label}</span>
        <button
          type="button"
          className={copied ? 'copy-meta copied' : 'copy-meta'}
          disabled={!text}
          title={text ? `Kopiuj: ${label}` : 'Brak tekstu do skopiowania'}
          onClick={copy}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
      <code>{value?.trim() || '—'}</code>
    </div>
  )
}

function ViralScore({ score }: { score?: number }) {
  const hot = typeof score === 'number' && score >= 90
  return (
    <div className={hot ? 'viral-score hot' : 'viral-score'}>
      <span>Viral Score</span>
      <strong>{score ?? '—'}</strong>
      {hot && <Flame size={13} className="score-fire" />}
    </div>
  )
}

function VoiceManagerRow({ voice, selected, onRename, onDelete }: {
  voice: { id: string; label: string; path: string }
  selected: boolean
  onRename: (path: string, label: string) => void
  onDelete: (path: string) => void
}) {
  const [name, setName] = useState(voice.label)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setName(voice.label) }, [voice.label])
  const dirty = name.trim().length > 0 && name.trim() !== voice.label
  const save = async () => {
    if (!dirty || busy) return
    setBusy(true)
    await onRename(voice.path, name.trim())
    setBusy(false)
  }
  const remove = () => {
    if (busy) return
    if (window.confirm(`Usunąć próbkę głosu „${voice.label}”? Tej operacji nie można cofnąć.`)) {
      setBusy(true)
      onDelete(voice.path)
    }
  }
  return (
    <div className={selected ? 'voice-row selected' : 'voice-row'}>
      <input
        className="text-field"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save() }}
        title={voice.path}
      />
      <button type="button" className="ghost-btn icon-btn" disabled={!dirty || busy} title="Zapisz nazwę" onClick={save}>
        <Check size={15} />
      </button>
      <button type="button" className="ghost-btn icon-btn danger" disabled={busy} title="Usuń próbkę" onClick={remove}>
        <Trash2 size={15} />
      </button>
    </div>
  )
}


function DubbedVersionCard({
  index, total, version, short, artifactUrl, srtUrl, rendering, renderProgress, renderMessage, favorite, notice,
  subLangs, translatingSubs, subsProgress, subsMessage, batchingSubs, mergingScenes, onFavorite, onEdit, onRender, onTranslateSubs, onTranslateSubsBatch, onMergeScenes, onDelete,
}: {
  index: number
  total: number
  version: ShortVersion
  short: Short
  artifactUrl: (url?: string) => string
  srtUrl: string
  rendering: boolean
  renderProgress: number
  renderMessage: string
  favorite: boolean
  notice?: { kind: 'ok' | 'err'; message: string }
  subLangs: string[]
  translatingSubs: boolean
  subsProgress: number
  subsMessage: string
  batchingSubs: boolean
  mergingScenes: boolean
  onFavorite: () => void
  onEdit: () => void
  onRender: () => void
  onTranslateSubs: (subLang: string) => void
  onTranslateSubsBatch: (subLangs: string[]) => void
  onMergeScenes: () => void
  onDelete: () => void
}) {
  const lang = version.language ?? short.language ?? 'język'
  const overFragmented = (short.segments?.length ?? 0) > 4
  const subLang = version.subtitle_language ?? lang
  const modeTag = audioModeTag(version.audio_mode)
  // Cache-buster tied to the file's render time. The artifact URL is otherwise
  // IDENTICAL across re-renders (same path + filename), so without this the Electron
  // media cache could keep showing a stale/half-buffered clip after „Przegeneruj"
  // (the symptom: video reports 1:11 but only ~10 s plays). updated_at bumps on every
  // re-render, so each render yields a fresh URL — and it's stable between list
  // refreshes (won't flicker), unlike a Date.now() buster.
  const rawVideoUrl = artifactUrl(version.video_path_url)
  const bust = version.updated_at || version.created_at || 0
  const videoUrl = rawVideoUrl ? `${rawVideoUrl}${rawVideoUrl.includes('?') ? '&' : '?'}v=${bust}` : ''
  // Default the subtitle-translation picker to a language different from the audio.
  const [subPick, setSubPick] = useState<string>(() => subLangs.find((l) => l !== lang) ?? subLangs[0] ?? 'Angielski')
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchPick, setBatchPick] = useState<Record<string, boolean>>({})
  const batchSelected = subLangs.filter((l) => batchPick[l])
  const busy = rendering || translatingSubs || batchingSubs
  const activeProgress = rendering ? renderProgress : (translatingSubs || batchingSubs ? subsProgress : 0)
  const activeMessage = rendering
    ? (renderMessage || 'Renderowanie wersji...')
    : batchingSubs
      ? (subsMessage || 'Generowanie wersji napisów...')
      : translatingSubs
        ? (subsMessage || 'Tłumaczenie napisów i renderowanie wersji...')
        : ''
  return (
    <div className="version-item">
      {/* Full-width header (title + heart), mirroring the base short's .short-item-top so
          the favorite heart sits at the FAR RIGHT of the card — not tucked next to the
          title inside the narrow video column. */}
      <div className="version-title-row version-header">
        <div className="version-title-copy">
          <strong>Short {index + 1} z {total}</strong>
          <span>Napisy: {subLang} · Język: {lang}{modeTag ? ` (${modeTag})` : ''}</span>
        </div>
        <button
          type="button"
          className={favorite ? 'favorite-toggle version-favorite active' : 'favorite-toggle version-favorite'}
          title={favorite ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'}
          onClick={onFavorite}
        >
          <Heart size={22} fill={favorite ? 'currentColor' : 'none'} />
        </button>
      </div>
      <div className="version-layout">
        <div className="version-media">
          {videoUrl ? (
            <video src={videoUrl} controls playsInline preload="metadata" className="short-player-video version-video" />
          ) : (
            <div className="short-video-placeholder">
              <Clapperboard size={20} />
              <span>Wersja czeka na plik wideo</span>
            </div>
          )}
        </div>
        <div className="short-meta-grid compact version-meta">
          <MetaBox label="Tytuł" value={short.title} />
          <MetaBox label="Opis" value={short.hook_text} />
          <MetaBox label="Hashtagi" value={short.hashtags} />
          <MetaBox label="Tagi YouTube" value={short.yt_tags} />
        </div>
      </div>
      <div className="short-tools version-tools">
        {version.video_path_url && <a className="primary-btn download-main" href={videoUrl} download title="Pobierz wideo tej wersji (MP4)"><Clapperboard size={15} /> Pobierz wideo</a>}
        {version.audio_path_url && <a className="ghost-btn download-secondary" href={artifactUrl(version.audio_path_url)} download title="Pobierz samą ścieżkę audio tej wersji"><Mic2 size={15} /> Pobierz audio</a>}
        {srtUrl && <a className="ghost-btn download-secondary" href={srtUrl} download title="Pobierz przetłumaczone napisy (.srt)"><Captions size={15} /> Pobierz napisy</a>}
        <button type="button" className="ghost-btn short-action-btn" onClick={onEdit} title="Edytuj napisy tej wersji">
          <Captions size={15} /> Edytuj napisy
        </button>
        {overFragmented && (
          <button type="button" className="ghost-btn short-action-btn" disabled={mergingScenes} onClick={onMergeScenes} title="Scal drobne wycinki TTS z powrotem w sceny (bez zmiany wideo)">
            <Scissors size={15} /> {mergingScenes ? 'Scalam…' : 'Napraw sceny'}
          </button>
        )}
        <button type="button" className="primary-btn short-action-btn" disabled={busy} onClick={onRender}>
          <Clapperboard size={15} /> {rendering ? `${Math.round(renderProgress * 100)}%` : 'Przegeneruj'}
        </button>
        <button type="button" className="ghost-btn danger short-action-btn" onClick={onDelete}>
          <Trash2 size={15} /> Usuń
        </button>
      </div>
      <div className="short-tool-group version-subs-tool">
        <span>Napisy w innym języku (to samo audio)</span>
        <select className="mini-select" value={subPick} onChange={(e) => setSubPick(e.target.value)} disabled={busy}>
          {subLangs.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <button
          type="button"
          className="ghost-btn short-action-btn"
          disabled={busy}
          onClick={() => onTranslateSubs(subPick)}
          title="Przetłumacz napisy na wybrany język i wyrenderuj nową wersję z tym samym dubbingiem/lektorem"
        >
          <Languages size={15} /> {translatingSubs ? `${Math.round(subsProgress * 100)}%` : 'Przetłumacz napisy i przegeneruj'}
        </button>
        <button
          type="button"
          className="ghost-btn short-action-btn"
          disabled={busy}
          onClick={() => setBatchOpen((v) => !v)}
          title="Wygeneruj naraz wersje z napisami w kilku językach (to samo audio)"
        >
          <Languages size={15} /> Wiele języków…
        </button>
      </div>
      {batchOpen && (
        <div className="short-tool-group version-subs-batch">
          <span>Zaznacz języki napisów do wygenerowania naraz (audio bez zmian):</span>
          <div className="subs-batch-grid">
            {subLangs.map((l) => (
              <label key={l} className={batchPick[l] ? 'subs-batch-chip on' : 'subs-batch-chip'}>
                <input
                  type="checkbox"
                  checked={!!batchPick[l]}
                  disabled={busy}
                  onChange={(e) => setBatchPick((prev) => ({ ...prev, [l]: e.target.checked }))}
                />
                {l}
              </label>
            ))}
          </div>
          <button
            type="button"
            className="primary-btn short-action-btn"
            disabled={busy || batchSelected.length === 0}
            onClick={() => onTranslateSubsBatch(batchSelected)}
            title="Wyrenderuj osobną wersję dla każdego zaznaczonego języka napisów"
          >
            <Languages size={15} /> {batchingSubs ? `${Math.round(subsProgress * 100)}%` : `Wygeneruj ${batchSelected.length || ''} wersji napisów`}
          </button>
        </div>
      )}
      {(rendering || translatingSubs || batchingSubs) && (
        <div className="inline-job version-inline-job">
          <div className="inline-job-head">
            <span>{activeMessage} · {Math.round(activeProgress * 100)}%</span>
          </div>
          <div className="run-progress">
            <em style={{ width: `${Math.round(activeProgress * 100)}%` }} />
          </div>
        </div>
      )}
      {notice && (
        <div className={notice.kind === 'err' ? 'short-inline-status err' : 'short-inline-status ok'}>
          {notice.message}
        </div>
      )}
    </div>
  )
}

function animationLabel(value: string) {
  const map: Record<string, string> = {
    none: 'Brak',
    spring: 'Wyskakiwanie (Spring Pop)',
    karaoke: 'Płynne Karaoke',
    jiggle: 'Trzęsienie (Jiggle)',
    blur_reveal: 'Wyłanianie (Blur Reveal)',
    zoom_in: 'Nalot (Zoom In)',
    color_pulse: 'Pulsowanie (Color Pulse)',
    slide_up: 'Wjazd 3D (Slide Up)',
  }
  return map[value] || value || 'Brak'
}

function slugLanguage(value: string) {
  // Mirror backend dubbing_engine.LANG_SLUGS so the fallback key matches the
  // folder the server actually writes when a version manifest lacks a slug.
  const map: Record<string, string> = {
    Polski: 'pl', Angielski: 'en', Niemiecki: 'de', Francuski: 'fr',
    'Hiszpański': 'es', 'Włoski': 'it', Portugalski: 'pt', Holenderski: 'nl',
    Rosyjski: 'ru', 'Ukraiński': 'uk', Czeski: 'cs', 'Słowacki': 'sk',
    Szwedzki: 'sv', Norweski: 'no', 'Duński': 'da', 'Fiński': 'fi',
    Grecki: 'el', Rumuński: 'ro', 'Węgierski': 'hu', Bułgarski: 'bg',
    Chorwacki: 'hr', Serbski: 'sr', Turecki: 'tr', Arabski: 'ar',
    Hebrajski: 'he', Hindi: 'hi', Wietnamski: 'vi', Tajski: 'th',
    Indonezyjski: 'id', 'Japoński': 'ja', 'Koreański': 'ko', 'Chiński': 'zh',
  }
  return map[value] || value.toLowerCase().replace(/\s+/g, '-')
}

function fmtDate(epochSeconds: number) {
  if (!epochSeconds) return ''
  const d = new Date(epochSeconds * 1000)
  return d.toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function currentEpochMs() {
  return Date.now()
}
