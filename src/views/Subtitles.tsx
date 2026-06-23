import { useEffect, useState, useCallback, useRef } from 'react'
import { Captions, FolderOpen, Link2, WandSparkles, Languages, Download, Check, History, Trash2, Play, X, Heart, RotateCcw } from 'lucide-react'
import { api, getBase } from '../lib/api'
import { useJob } from '../lib/useJob'
import type { useStudio } from '../lib/useStudio'
import type { DubcutMediaFile } from '../types/dubcut'
import { DropZone } from '../components/DropZone'
import { InlineJobProgress } from '../components/JobProgress'
import { type Seg } from '../components/SegmentEditor'
import { SubtitlePreview, type SubTrack } from '../components/SubtitlePreview'
import { Field, Select } from '../components/ui'
import { usePersistentState } from '../lib/usePersistentState'

type Studio = ReturnType<typeof useStudio>

export function Subtitles({ studio }: { studio: Studio }) {
  const { config, meta, online } = studio
  const d = config.dub
  const [media, setMedia] = usePersistentState<DubcutMediaFile | null>('dubcut.subs.media', null)
  const [ytUrl, setYtUrl] = usePersistentState('dubcut.subs.ytUrl', '')
  const [sourceMode, setSourceMode] = usePersistentState<'youtube' | 'file' | 'history' | 'favorites'>('dubcut.subs.sourceMode', 'youtube')
  type SubsProject = {
    id: string; title: string; created_at: number
    source_lang?: string; source_exists?: boolean; source_url?: string | null
    is_youtube?: boolean; source?: string
    original_segments?: Seg[]
    versions: { language: string; srt_url?: string; vtt_url?: string }[]
  }
  const [projects, setProjects] = useState<SubsProject[]>([])
  const [openProject, setOpenProject] = usePersistentState<string | null>('dubcut.subs.openProject', null)
  const loadProjects = useCallback(() => { api.subsProjects().then(setProjects).catch(() => {}) }, [])
  // Recent YouTube links — same store as Shorts ('dubcut.ytRecents') so links typed in
  // either module autocomplete in both (cross-module consistency).
  const [ytRecents, setYtRecents] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.ytRecents') || '[]') } catch { return [] }
  })
  const rememberYt = useCallback((url: string) => {
    const u = url.trim()
    if (!u || !/youtu\.?be|youtube\.com/i.test(u)) return
    setYtRecents((prev) => {
      const next = [u, ...prev.filter((x) => x !== u)].slice(0, 10)
      try { localStorage.setItem('dubcut.ytRecents', JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [])
  // Which source produced the current stage-1 work, so the YouTube tab never shows
  // local-file work and vice versa.
  const [sessionSrc, setSessionSrc] = usePersistentState<'youtube' | 'file' | null>('dubcut.subs.sessionSrc', null)
  // Favorite projects (mirrors DubMaster/Shorts) — local list of project ids.
  const [favs, setFavs] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.subs.favs') || '[]') } catch { return [] }
  })
  const toggleFav = (id: string) => setFavs((prev) => {
    const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    try { localStorage.setItem('dubcut.subs.favs', JSON.stringify(next)) } catch { /* ignore */ }
    return next
  })
  const [srcLang, setSrcLang] = useState<string>(() => {
    try { return localStorage.getItem('dubcut.subs.srcLang') || 'Automatyczne wykrywanie' } catch { return 'Automatyczne wykrywanie' }
  })
  const [langs, setLangs] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.subs.langs') || '["Angielski"]') } catch { return ['Angielski'] }
  })
  const [includeOrig, setIncludeOrig] = usePersistentState('dubcut.subs.includeOrig', false)
  const [session, setSession] = usePersistentState<string | null>('dubcut.subs.session', null)
  const [origSegs, setOrigSegs] = usePersistentState<Seg[]>('dubcut.subs.origSegs', [])
  const [origUrl, setOrigUrl] = usePersistentState<string | null>('dubcut.subs.origUrl', null)
  type SubResult = { language: string; srt_url?: string; vtt_url?: string }
  const [subResults, setSubResults] = usePersistentState<SubResult[]>('dubcut.subs.results', [])

  // Languages already generated for the CURRENTLY loaded session. Lets us mark them
  // "done" (green), skip them when generating (only the missing ones run), and load
  // their subtitle tracks into the preview when a film is re-opened/re-analysed.
  const sessionProject = projects.find((p) => p.id === session) || null
  const ytProjects = projects.filter((p) => p.is_youtube)
  const fileProjects = projects.filter((p) => !p.is_youtube)
  const doneVersions = sessionProject?.versions ?? []
  // Everything generated for this session = stored manifest versions MERGED with this
  // run's fresh results (fresh wins). Source of truth for the "done" markers AND the
  // download list, so finished subs are downloadable without regenerating.
  const generatedVersions: SubResult[] = (() => {
    const map = new Map<string, SubResult>()
    for (const v of doneVersions) map.set(v.language, v)
    for (const r of subResults) if (r.srt_url || r.vtt_url) map.set(r.language, r)
    return Array.from(map.values())
  })()
  const isLangDone = (l: string) => generatedVersions.some((v) => v.language === l)
  // The original-subtitle version is stored under the source-language label
  // (`source_lang` at generation time → "Automatyczne wykrywanie" for auto) or "Oryginał".
  const origLabel = srcLang || 'Oryginał'
  const origDone = generatedVersions.some((v) => v.language === origLabel || v.language === 'Oryginał')
  // Tracks for the preview = generated versions for this session, so re-opening a film
  // shows the subs already made for it without re-running anything.
  const previewTracks: SubTrack[] = generatedVersions
    .filter((v): v is SubResult & { vtt_url: string } => !!v.vtt_url && v.language !== srcLang && v.language !== origLabel && v.language !== 'Oryginał')
    .map((v) => ({ language: v.language, vttUrl: `${getBase()}${v.vtt_url}` }))

  const analyzeJob = useJob('subs.analyze')
  const batchJob = useJob('subs.batch')
  const anyRunning = analyzeJob.state.status === 'running' || batchJob.state.status === 'running'
  // Snapshot of the transcript as last loaded/saved to disk, so the autosave effect
  // only fires when the user actually edits (not on every load/reopen).
  const savedSegsRef = useRef<string>('')

  const usingYt = sourceMode === 'youtube'
  const canRun = usingYt ? !!ytUrl.trim() : !!media
  const dlUrl = (u: string) => u + (u.includes('?') ? '&' : '?') + 'dl=1'
  const settingsPayload = () => ({
    input_method: usingYt ? 'Pobierz z YouTube' : 'Lokalny plik',
    source_lang: srcLang,
    translation_model: d.translation_model,
    yt_quality: d.yt_quality,
  })

  const pick = async () => { const f = await studio.chooseVideo(); if (f) setMedia(f) }
  const setSrc = (v: string) => { setSrcLang(v); try { localStorage.setItem('dubcut.subs.srcLang', v) } catch { /* */ } }
  const toggleLang = (l: string) => setLangs((prev) => {
    const next = prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l]
    try { localStorage.setItem('dubcut.subs.langs', JSON.stringify(next)) } catch { /* */ }
    return next
  })

  const analyze = (force = false) => {
    if (!canRun || anyRunning) return
    if (force && !window.confirm('Uruchomić transkrypcję Whispera od nowa? Obecna transkrypcja tego pliku zostanie zastąpiona.')) return
    if (usingYt) rememberYt(ytUrl)
    setSessionSrc(usingYt ? 'youtube' : 'file')
    setSession(null); setOrigSegs([]); setOrigUrl(null); setSubResults([]); batchJob.reset()
    analyzeJob.start(() => api.dubAnalyze({ source: usingYt ? ytUrl.trim() : media!.path, settings: settingsPayload(), force }))
  }
  // Re-open a finished project straight into the editable stage 1 (transcript + preview),
  // so the user can add more subtitle languages without re-downloading/re-transcribing.
  const reopenProject = (p: SubsProject) => {
    if (anyRunning) return
    const mode = p.is_youtube ? 'youtube' : 'file'
    setSessionSrc(mode)
    setSourceMode(mode)          // leave History/Favorites → show the editable stage 1
    setOpenProject(null)         // close the read-only history preview
    if (p.is_youtube && p.source) setYtUrl(p.source)
    if (p.source_lang) setSrc(p.source_lang)
    setSession(p.id)
    savedSegsRef.current = JSON.stringify(p.original_segments ?? [])
    setOrigSegs(p.original_segments ?? [])
    setOrigUrl(p.source_url ?? null)
    setSubResults([])
    batchJob.reset()
    analyzeJob.reset()
  }
  // Only translate languages that don't already exist for this film — re-pressing
  // "Generuj" never redoes work that's done; it just fills in what's missing.
  const missingLangs = langs.filter((l) => !isLangDone(l))
  const needOrig = includeOrig && !origDone
  const generate = () => {
    if (!session || anyRunning || (missingLangs.length === 0 && !needOrig)) return
    batchJob.start(() => api.dubSubtitlesBatch({ session, segments: origSegs, languages: missingLangs, include_original: needOrig, settings: settingsPayload() }))
  }
  // Force-regenerate ALL selected languages, overwriting versions that already
  // exist — used after the user edits the original transcript (fixing Whisper
  // errors) and wants the translations rebuilt from the corrected text.
  const regenerate = () => {
    if (!session || anyRunning || (langs.length === 0 && !includeOrig)) return
    if (!window.confirm('Wygenerować zaznaczone napisy od nowa? Istniejące wersje (w tym już gotowe) zostaną zastąpione — użyj tego po poprawieniu oryginału.')) return
    batchJob.start(() => api.dubSubtitlesBatch({ session, segments: origSegs, languages: langs, include_original: includeOrig, settings: settingsPayload() }))
  }

  useEffect(() => {
    if (analyzeJob.state.status === 'done') {
      const r = analyzeJob.state.result as { session?: string; original_segments?: Seg[]; original_url?: string } | null
      if (r?.session) {
        const segs = r.original_segments ?? []
        savedSegsRef.current = JSON.stringify(segs)   // freshly from disk → don't autosave it back
        setSession(r.session); setOrigSegs(segs); setOrigUrl(r.original_url ?? null)
      }
    }
  }, [analyzeJob.state.status, analyzeJob.state.result, setSession, setOrigSegs, setOrigUrl])

  // Autosave hand-edited transcript to the session (debounced) so corrections survive
  // app restarts and re-analysis reuses them instead of the raw Whisper text.
  useEffect(() => {
    if (!session || origSegs.length === 0 || anyRunning) return
    const cur = JSON.stringify(origSegs)
    if (cur === savedSegsRef.current) return
    const t = setTimeout(() => {
      api.dubSaveTranscript({ session, segments: origSegs })
        .then(() => { savedSegsRef.current = cur })
        .catch(() => { /* keep dirty; will retry on next edit */ })
    }, 700)
    return () => clearTimeout(t)
  }, [origSegs, session, anyRunning])

  // Load the project list whenever the backend is online — drives History, the
  // "recent films" lists in the YouTube/file tabs, and the per-session done markers.
  useEffect(() => { if (online) loadProjects() }, [online, sourceMode, session, loadProjects])
  useEffect(() => {
    if (batchJob.state.status === 'done') {
      loadProjects()
      const r = batchJob.state.result as { results?: SubResult[] } | null
      if (r?.results?.length) setSubResults(r.results)
    }
  }, [batchJob.state.status, batchJob.state.result, loadProjects, setSubResults])
  const removeProject = async (id: string) => {
    if (!window.confirm('Usunąć ten projekt napisów? Tej operacji nie można cofnąć.')) return
    try { await api.deleteSubsProject(id) } catch { /* */ }
    if (openProject === id) setOpenProject(null)
    loadProjects()
  }
  // Delete generated subtitle versions for the CURRENT session — removes the SRT/VTT
  // files from disk completely. `language` omitted = wipe every version for this film.
  const removeVersion = async (language?: string) => {
    if (!session) return
    if (!window.confirm(language
      ? `Usunąć napisy „${language}” z dysku? Tej operacji nie można cofnąć.`
      : 'Usunąć WSZYSTKIE wygenerowane napisy tego filmu z dysku? Tej operacji nie można cofnąć.')) return
    try { await api.deleteSubsVersion(session, language) } catch { /* */ }
    // Drop from this run's fresh results too, so it disappears immediately.
    setSubResults((prev) => language ? prev.filter((r) => r.language !== language) : [])
    loadProjects()
  }

  return (
    <div className="studio-grid single">
      <section className="editor">
        <header className="editor-head">
          <div>
            <span className="eyebrow">Generator napisów</span>
            <h2>Napisy AI</h2>
          </div>
        </header>

        <div className="source-tabs">
          <button type="button" className={sourceMode === 'youtube' ? 'source-tab active' : 'source-tab'} onClick={() => setSourceMode('youtube')}>
            <Link2 size={16} /> Link YouTube
          </button>
          <button type="button" className={sourceMode === 'file' ? 'source-tab active' : 'source-tab'} onClick={() => setSourceMode('file')}>
            <FolderOpen size={16} /> Plik lokalny
          </button>
          <button type="button" className={sourceMode === 'history' ? 'source-tab active' : 'source-tab'} onClick={() => setSourceMode('history')}>
            <History size={16} /> Historia projektów{projects.length ? ` (${projects.length})` : ''}
          </button>
          <button type="button" className={sourceMode === 'favorites' ? 'source-tab active' : 'source-tab'} onClick={() => setSourceMode('favorites')}>
            <Heart size={16} /> Ulubione{favs.length ? ` (${favs.length})` : ''}
          </button>
        </div>

        {(sourceMode === 'history' || sourceMode === 'favorites') && (() => {
          const list = sourceMode === 'favorites' ? projects.filter((p) => favs.includes(p.id)) : projects
          return list.length === 0 ? (
            <div className="yt-card"><p className="settings-desc" style={{ margin: 0 }}>{sourceMode === 'favorites'
              ? 'Brak ulubionych. Kliknij serduszko przy projekcie, aby zapisać go tutaj.'
              : 'Brak zapisanych projektów. Wczytaj film z „Link YouTube” lub „Plik lokalny” i zrób transkrypcję — pojawi się tutaj od razu.'}</p></div>
          ) : (
            <div className="dub-tiles">
              {list.map((p) => {
                const isOpen = openProject === p.id
                const srcUrl = p.source_exists && p.source_url ? `${getBase()}${p.source_url}` : ''
                const tracks = (p.versions ?? [])
                  .filter((v): v is { language: string; srt_url?: string; vtt_url: string } =>
                    !!v.vtt_url && v.language !== (p.source_lang ?? '') && v.language !== 'Oryginał')
                  .map((v): SubTrack => ({ language: v.language, vttUrl: `${getBase()}${v.vtt_url}` }))
                return (
                  <div className={`dub-tile subs-tile${isOpen ? ' is-open' : ''}`} key={p.id}>
                    <div className="dub-tile-body">
                      <strong className="dub-tile-title" title={p.title}>{p.title}</strong>
                      <span className="dub-tile-meta">
                        {p.versions.length === 0
                          ? 'sama transkrypcja'
                          : `${p.versions.length} ${p.versions.length === 1 ? 'wersja językowa' : 'wersje językowe'}`}
                        {p.source_exists === false && ' · brak pliku na dysku'}
                      </span>
                    </div>

                    {/* Per-tile source player. Collapsed: a simple video with native
                        controls so the film of THIS project plays right here. Expanded:
                        the full synced preview (caption overlay + language switch + transcript). */}
                    {isOpen ? (
                      <SubtitlePreview
                        videoUrl={srcUrl}
                        original={p.original_segments ?? []}
                        onOriginal={() => { /* history is read-only */ }}
                        originalLabel={`Oryginał${p.source_lang && p.source_lang !== 'Automatyczne wykrywanie' ? ` (${p.source_lang})` : ''}`}
                        tracks={tracks}
                        readOnly
                        sourceMissing={!p.source_exists}
                      />
                    ) : srcUrl ? (
                      <video className="dub-tile-video" src={srcUrl} controls preload="metadata" playsInline />
                    ) : (
                      <div className="dub-tile-video dub-tile-video-empty">
                        Plik źródłowy nie istnieje już na dysku — film usunięto lub przeniesiono. Napisy możesz nadal pobierać.
                      </div>
                    )}

                    {p.versions.length > 0 && (
                      <div className="subs-tile-langs">
                        {p.versions.map((v) => (
                          <div className="subs-result-row" key={v.language}>
                            <span className="subs-result-lang"><Captions size={15} /> {v.language}</span>
                            <div className="subs-result-actions">
                              {v.srt_url && <a className="ghost-btn short-action-btn" href={dlUrl(`${getBase()}${v.srt_url}`)} download><Download size={13} /> SRT</a>}
                              {v.vtt_url && <a className="ghost-btn short-action-btn" href={dlUrl(`${getBase()}${v.vtt_url}`)} download><Download size={13} /> VTT</a>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="dub-tile-actions">
                      <button type="button" className="ghost-btn short-action-btn" onClick={() => setOpenProject(isOpen ? null : p.id)}>
                        {isOpen ? <><X size={14} /> Zamknij podgląd</> : <><Play size={14} /> Otwórz z napisami</>}
                      </button>
                      <button type="button" className="ghost-btn short-action-btn" disabled={anyRunning} title="Wczytaj film z powrotem do edytora i dołóż kolejne języki napisów" onClick={() => reopenProject(p)}>
                        <Languages size={14} /> Dołóż napisy
                      </button>
                      <button type="button" className={favs.includes(p.id) ? 'ghost-btn icon-btn fav-on' : 'ghost-btn icon-btn'} title={favs.includes(p.id) ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'} onClick={() => toggleFav(p.id)}><Heart size={15} /></button>
                      <button type="button" className="ghost-btn icon-btn danger" title="Usuń" onClick={() => removeProject(p.id)}><Trash2 size={15} /></button>
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })()}

        {usingYt && (
          <div className="yt-card">
            <label className="yt-card-label"><Link2 size={15} /> Adres wideo YouTube</label>
            <div className="yt-card-input">
              <input className="yt-input" placeholder="https://www.youtube.com/watch?v=…" value={ytUrl} list="subs-yt-recents"
                onChange={(e) => setYtUrl(e.target.value)} onBlur={() => rememberYt(ytUrl)} />
              {ytUrl.trim() && <button type="button" className="yt-clear" onClick={() => setYtUrl('')}>Wyczyść</button>}
            </div>
            <datalist id="subs-yt-recents">
              {ytRecents.map((u) => <option key={u} value={u} />)}
            </datalist>
            {ytProjects.length > 0 && (
              <div className="yt-downloads">
                <span className="yt-downloads-label">
                  <History size={13} /> Ostatnie filmy — kliknij, by wrócić i dołożyć kolejne napisy:
                </span>
                <div className="yt-downloads-list">
                  {ytProjects.map((p) => (
                    <span className={`yt-download ${session === p.id ? 'active' : ''}`} key={p.id}>
                      <button type="button" className="yt-download-pick" title={p.title} disabled={anyRunning} onClick={() => reopenProject(p)}>
                        <Captions size={12} />
                        <span className="yt-download-title">{p.title}</span>
                        {p.versions.length > 0 && <span className="yt-download-cached">{p.versions.length}</span>}
                      </button>
                      <button type="button" className="yt-download-x" title="Usuń projekt napisów" onClick={() => removeProject(p.id)}>
                        <Trash2 size={11} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="analyze-row">
              <Field label="Jakość pobierania" hint="Maksymalna rozdzielczość pobieranego wideo z YouTube.">
                <Select value={d.yt_quality} options={meta.dub.yt_qualities} onChange={(v) => studio.update('dub', { yt_quality: v })} />
              </Field>
              <Field label="Język filmu (oryginał)" hint="Język mowy w filmie. „Automatyczne wykrywanie” — aplikacja sama rozpozna.">
                <Select value={srcLang} options={meta.dub.source_languages} onChange={setSrc} />
              </Field>
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || anyRunning} onClick={() => analyze()}>
                <WandSparkles size={15} /> Analizuj (transkrypcja)
              </button>
            </div>
          </div>
        )}

        {sourceMode === 'file' && (
          <>
            <DropZone media={media} formats="MP4 · MOV · MKV · WEBM · MP3 · WAV" onPick={pick} compact />
            {fileProjects.length > 0 && (
              <div className="yt-downloads">
                <span className="yt-downloads-label">
                  <History size={13} /> Ostatnie pliki — kliknij, by wrócić i dołożyć kolejne napisy:
                </span>
                <div className="yt-downloads-list">
                  {fileProjects.map((p) => (
                    <span className={`yt-download ${session === p.id ? 'active' : ''}`} key={p.id}>
                      <button type="button" className="yt-download-pick" title={p.title} disabled={anyRunning} onClick={() => reopenProject(p)}>
                        <Captions size={12} />
                        <span className="yt-download-title">{p.title}</span>
                        {p.versions.length > 0 && <span className="yt-download-cached">{p.versions.length}</span>}
                      </button>
                      <button type="button" className="yt-download-x" title="Usuń projekt napisów" onClick={() => removeProject(p.id)}>
                        <Trash2 size={11} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="analyze-row">
              <Field label="Język filmu (oryginał)" hint="Język mowy w pliku. „Automatyczne wykrywanie” — aplikacja sama rozpozna.">
                <Select value={srcLang} options={meta.dub.source_languages} onChange={setSrc} />
              </Field>
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || anyRunning} onClick={() => analyze()}>
                <WandSparkles size={15} /> Analizuj (transkrypcja)
              </button>
            </div>
          </>
        )}

        {sourceMode !== 'history' && analyzeJob.state.status === 'running' && (
          <InlineJobProgress state={analyzeJob.state} label="Transkrypcja (Whisper)" onCancel={analyzeJob.cancel} />
        )}

        {session && sourceMode !== 'history' && sessionSrc === sourceMode && (
          <div className="dub-stage">
            <div className="dub-stage-title with-action">
              <div>
                <strong>1 · Transkrypcja i podgląd</strong>
                <span>Odtwórz film z napisami, popraw ewentualne błędy Whispera, wybierz języki i wygeneruj.</span>
              </div>
              <button type="button" className="ghost-btn short-action-btn" disabled={anyRunning} title="Uruchom Whisper od nowa (pomija zapisaną transkrypcję)" onClick={() => analyze(true)}>
                <WandSparkles size={14} /> Transkrybuj ponownie
              </button>
            </div>
            <SubtitlePreview
              videoUrl={origUrl ? `${getBase()}${origUrl}` : ''}
              original={origSegs}
              onOriginal={setOrigSegs}
              originalLabel={`Oryginał${srcLang && srcLang !== 'Automatyczne wykrywanie' ? ` (${srcLang})` : ''}`}
              tracks={previewTracks}
            />

            <div className="dub-stage-title">
              <strong>2 · Języki napisów</strong>
              <span>Zaznacz jeden lub kilka — napisy powstaną dla każdego (batch). <span className="lang-done-legend">Zielone</span> = już gotowe dla tego filmu (pomijane).</span>
            </div>
            <div className="lang-multi">
              <button type="button"
                className={`lang-pill${includeOrig ? ' on' : ''}${origDone ? ' done' : ''}`}
                title={origDone ? 'Napisy w oryginale są już gotowe. Odznacz, by pominąć przy „Generuj ponownie”.' : undefined}
                onClick={() => setIncludeOrig((v) => !v)}>
                {includeOrig && <Check size={13} />} Oryginał ({srcLang === 'Automatyczne wykrywanie' ? 'auto' : srcLang})
              </button>
              {(meta.dub.translate_target_languages ?? meta.dub.target_languages).map((l) => {
                const done = isLangDone(l)
                return (
                  <button type="button" key={l}
                    className={`lang-pill${langs.includes(l) ? ' on' : ''}${done ? ' done' : ''}`}
                    title={done ? 'Te napisy są już gotowe. Odznacz, by pominąć przy „Generuj ponownie”.' : undefined}
                    onClick={() => toggleLang(l)}>
                    {langs.includes(l) && <Check size={13} />} {l}
                  </button>
                )
              })}
            </div>

            <div className="dub-stage-action">
              {batchJob.state.status === 'running'
                ? <InlineJobProgress state={batchJob.state} label="Generowanie napisów" onCancel={batchJob.cancel} />
                : <>
                    <button type="button" className="primary-btn" disabled={anyRunning || (missingLangs.length === 0 && !needOrig)} onClick={generate}>
                      <Languages size={15} /> {missingLangs.length === 0 && !needOrig
                        ? 'Wszystkie wybrane już gotowe'
                        : `Generuj napisy (${(needOrig ? 1 : 0) + missingLangs.length})`}
                    </button>
                    {generatedVersions.length > 0 && (langs.length > 0 || includeOrig) && (
                      <button type="button" className="ghost-btn" disabled={anyRunning}
                        title="Wygeneruj zaznaczone napisy od nowa (zastępuje gotowe) — użyj po poprawieniu oryginału"
                        onClick={regenerate}>
                        <RotateCcw size={15} /> Generuj ponownie ({(includeOrig ? 1 : 0) + langs.length})
                      </button>
                    )}
                  </>}
            </div>

            {/* All subtitle versions already made for this film — downloadable straight away,
                no regeneration needed. Auto-updates after each batch (loadProjects). */}
            {generatedVersions.length > 0 && (
              <>
                <div className="dub-stage-title with-action">
                  <div><strong>Gotowe napisy dla tego filmu</strong><span>Pobierz od razu — bez generowania od nowa.</span></div>
                  <button type="button" className="ghost-btn short-action-btn danger-btn" disabled={anyRunning}
                    title="Usuń wszystkie wygenerowane napisy tego filmu z dysku" onClick={() => removeVersion()}>
                    <Trash2 size={14} /> Usuń wszystkie
                  </button>
                </div>
                <div className="subs-results">
                  {generatedVersions.map((it) => (
                    <div className="subs-result-row" key={it.language}>
                      <span className="subs-result-lang"><Captions size={15} /> {it.language}</span>
                      <div className="subs-result-actions">
                        {it.srt_url && <a className="primary-btn short-action-btn" href={dlUrl(`${getBase()}${it.srt_url}`)} download><Download size={14} /> SRT</a>}
                        {it.vtt_url && <a className="ghost-btn short-action-btn" href={dlUrl(`${getBase()}${it.vtt_url}`)} download><Download size={14} /> VTT</a>}
                        <button type="button" className="ghost-btn short-action-btn danger-btn" disabled={anyRunning}
                          title={`Usuń napisy „${it.language}” z dysku`} onClick={() => removeVersion(it.language)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {!online && (
          <p className="offline-note">Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>.</p>
        )}
      </section>
    </div>
  )
}
