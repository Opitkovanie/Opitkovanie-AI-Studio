import { useState, useEffect, useCallback } from 'react'
import { usePersistentState } from '../lib/usePersistentState'
import {
  Mic2, Languages, SlidersHorizontal, Music2, WandSparkles,
  Clapperboard, FolderOpen, Link2, History, Heart, Download, Captions, Trash2, Clock3,
} from 'lucide-react'
import { api, getBase } from '../lib/api'
import { useJob } from '../lib/useJob'
import type { useStudio } from '../lib/useStudio'
import type { DubcutMediaFile } from '../types/dubcut'
import { DropZone } from '../components/DropZone'
import { InlineJobProgress } from '../components/JobProgress'
import { audioModeTag } from '../lib/jobFormat'
import { VoiceManager } from '../components/VoiceManager'
import { VoiceSamplePreview, OmniVoiceParams, ttsEngineOf } from '../components/VoiceSynth'
import { SegmentEditor, type Seg } from '../components/SegmentEditor'
import { SubtitleEditor, type EditWord } from '../components/SubtitleEditor'
import { Field, PillGroup, Section, Select, Slider, Toggle } from '../components/ui'

type Studio = ReturnType<typeof useStudio>

export function DubMaster({ studio }: { studio: Studio }) {
  const { config, meta, update, chooseVideo, online, refresh } = studio
  const d = config.dub
  const [media, setMedia] = usePersistentState<DubcutMediaFile | null>('dubcut.dub.media', null)
  const [ytUrl, setYtUrl] = usePersistentState('dubcut.dub.ytUrl', '')
  const [sourceMode, setSourceMode] = usePersistentState<'youtube' | 'file' | 'history' | 'favorites'>('dubcut.dub.sourceMode', 'youtube')

  // Staged workflow: analyze (transcribe+translate) → edit → re-translate → render.
  const analyzeJob = useJob('dub.analyze')
  // One-shot "force fresh transcription" — mirrors the Shorts module (same input→transcript
  // flow). Ignores the cached Whisper transcript for the next analyze; resets after firing.
  const [forceTranscribe, setForceTranscribe] = useState(false)
  const translateJob = useJob('dub.translate')
  const renderJob = useJob('dub.render')
  const subtitlesJob = useJob('dub.subtitles')
  const [session, setSession] = usePersistentState<string | null>('dubcut.dub.session', null)
  const [origSegs, setOrigSegs] = usePersistentState<Seg[]>('dubcut.dub.origSegs', [])
  const [origWords, setOrigWords] = usePersistentState<EditWord[]>('dubcut.dub.origWords', [])
  const [transSegs, setTransSegsRaw] = usePersistentState<Seg[]>('dubcut.dub.transSegs', [])
  const [originalUrl, setOriginalUrl] = usePersistentState<string>('dubcut.dub.originalUrl', '')
  // "dirty" = text/language changed since the last successful render, so the
  // Generate buttons should reappear (they hide once a result is ready).
  const [dirty, setDirty] = useState(false)
  const [wordEditorOpen, setWordEditorOpen] = useState(false)
  const [savingWordEdit, setSavingWordEdit] = useState(false)
  const setTransSegs = (s: Seg[]) => { setTransSegsRaw(s); setDirty(true) }

  const set = (patch: Record<string, unknown>) => update('dub', patch)

  // Voice engine is global; the panel adapts so controls match the active model.
  const engine = ttsEngineOf(config)
  const isOmni = engine === 'omnivoice'
  // OmniVoice has no preset speakers, so hide that source when it's the active engine.
  const voiceSourceOptions = isOmni
    ? (meta.dub.voice_sources ?? []).filter((v) => v !== 'Głos presetowy (Qwen)')
    : meta.dub.voice_sources
  // Which voice controls are relevant depends on the chosen source.
  const rawVoiceSource = String(d.voice_source ?? 'Głos z oryginalnego filmu')
  const voiceSource = isOmni && rawVoiceSource === 'Głos presetowy (Qwen)' ? 'Głos z oryginalnego filmu' : rawVoiceSource
  const isOwnSample = voiceSource === 'Sklonowany głos (własna próbka)'
  const isQwenPreset = !isOmni && voiceSource === 'Głos presetowy (Qwen)'
  const isClone = !isQwenPreset // original film or own sample both clone a timbre
  const selectedVoicePath = meta.voices.find((v) => v.id === d.selected_voice_id)?.path

  const pick = async () => {
    const file = await chooseVideo()
    if (file) setMedia(file)
  }

  const usingYt = sourceMode === 'youtube'
  const canRun = usingYt ? !!ytUrl.trim() : !!media
  const anyRunning = analyzeJob.state.status === 'running' || translateJob.state.status === 'running'
    || renderJob.state.status === 'running' || subtitlesJob.state.status === 'running'
  const settingsPayload = () => ({ ...d, input_method: usingYt ? 'Pobierz z YouTube' : 'Lokalny plik' })
  // Force a download with the real, language-suffixed filename (server sets it via dl=1).
  const dlUrl = (u: string) => u + (u.includes('?') ? '&' : '?') + 'dl=1'

  const setTargetLang = (v: string) => { set({ target_lang: v }); setDirty(true) }

  const analyze = () => {
    if (!canRun || anyRunning) return
    setSession(null); setOrigSegs([]); setOrigWords([]); setTransSegsRaw([]); setOriginalUrl(''); setDirty(false)
    renderJob.reset(); translateJob.reset(); subtitlesJob.reset()
    const force = forceTranscribe
    setForceTranscribe(false)  // one-shot, same behaviour as the Shorts module
    analyzeJob.start(() => api.dubAnalyze({ source: usingYt ? ytUrl.trim() : media!.path, settings: settingsPayload(), force }))
  }
  const retranslate = () => {
    if (!session || anyRunning) return
    translateJob.start(() => api.dubTranslate({ session, segments: origSegs, settings: settingsPayload() }))
  }
  const render = () => {
    if (!session || anyRunning || transSegs.length === 0) return
    setDirty(false)
    renderJob.start(() => api.dubRender({ session, segments: transSegs, settings: settingsPayload() }))
  }
  const genSubs = () => {
    if (!session || anyRunning || transSegs.length === 0) return
    setDirty(false)
    subtitlesJob.start(() => api.dubSubtitles({ session, segments: transSegs, settings: settingsPayload() }))
  }
  const genOrigSubs = () => {
    if (!session || anyRunning || origSegs.length === 0) return
    subtitlesJob.start(() => api.dubSubtitles({ session, segments: origSegs, settings: { ...settingsPayload(), target_lang: String(d.source_lang) } }))
  }
  const saveWordEdit = async (data: { segments: any[]; words: EditWord[] }) => {
    if (!session) return
    setSavingWordEdit(true)
    try {
      const nextSegments = data.segments.map((s, i) => ({ id: i, start: s.start_time, end: s.end_time, text: s.text || '' }))
      setOrigSegs(nextSegments)
      setOrigWords(data.words)
      setDirty(true)
      await api.dubSaveTranscript({ session, segments: nextSegments, words: data.words })
      setWordEditorOpen(false)
    } finally {
      setSavingWordEdit(false)
    }
  }

  // Analyze done → load editable transcript (translation is a separate step).
  useEffect(() => {
    if (analyzeJob.state.status === 'done') {
      const r = analyzeJob.state.result as { session?: string; original_segments?: Seg[]; original_words?: EditWord[]; original_url?: string } | null
      if (r?.session) {
        setSession(r.session)
        setOrigSegs(r.original_segments ?? [])
        setOrigWords(r.original_words ?? [])
        setTransSegsRaw([])
        setOriginalUrl(r.original_url ? `${getBase()}${r.original_url}` : '')
        setDirty(false)
      }
    }
  }, [analyzeJob.state.status, analyzeJob.state.result])

  // Re-translate done → refresh translation pane (mark dirty so Generate shows).
  useEffect(() => {
    if (translateJob.state.status === 'done') {
      const r = translateJob.state.result as { translated_segments?: Seg[] } | null
      if (r?.translated_segments) { setTransSegsRaw(r.translated_segments); setDirty(true) }
    }
  }, [translateJob.state.status, translateJob.state.result])

  const isVoiceover = String(d.mix_mode).startsWith('Lektor')
  const isDucking = String(d.mix_mode).toLowerCase().includes('ducking')
  const keepsBackground = !!d.keep_bg

  type DubProject = { id: string; title: string; language: string; mix_mode: string; created_at: number; video_url: string; subtitle_url: string }
  const [dubProjects, setDubProjects] = useState<DubProject[]>([])
  const [dubFavs, setDubFavs] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('dubcut.dubFavorites') || '[]') } catch { return [] }
  })
  const loadProjects = useCallback(() => { api.dubProjects().then(setDubProjects).catch(() => {}) }, [])
  useEffect(() => { if (sourceMode === 'history' || sourceMode === 'favorites') loadProjects() }, [sourceMode, loadProjects])
  useEffect(() => { if (renderJob.state.status === 'done') loadProjects() }, [renderJob.state.status, loadProjects])
  const toggleFav = (id: string) => setDubFavs((prev) => {
    const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    try { localStorage.setItem('dubcut.dubFavorites', JSON.stringify(next)) } catch { /* ignore */ }
    return next
  })
  const removeProject = async (id: string) => {
    if (!window.confirm('Usunąć ten projekt dubbingu? Tej operacji nie można cofnąć.')) return
    try { await api.deleteDubProject(id) } catch { /* ignore */ }
    loadProjects()
  }

  return (
    <div className="studio-grid">
      <section className="editor">
        <header className="editor-head">
          <div>
            <span className="eyebrow">Moduł dubbingu</span>
            <h2>Dubbing Studio</h2>
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
            <History size={16} /> Historia projektów{dubProjects.length ? ` (${dubProjects.length})` : ''}
          </button>
          <button type="button" className={sourceMode === 'favorites' ? 'source-tab active' : 'source-tab'} onClick={() => setSourceMode('favorites')}>
            <Heart size={16} /> Ulubione{dubFavs.length ? ` (${dubFavs.length})` : ''}
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
                onChange={(e) => setYtUrl(e.target.value)}
              />
              {ytUrl.trim() && (
                <button type="button" className="yt-clear" onClick={() => setYtUrl('')}>
                  Wyczyść
                </button>
              )}
            </div>
            <div className="analyze-row">
              <Field label="Jakość pobierania" hint="Maksymalna rozdzielczość pobieranego wideo z YouTube.">
                <Select value={d.yt_quality} options={meta.dub.yt_qualities} onChange={(v) => set({ yt_quality: v })} />
              </Field>
              <Field label="Język filmu (oryginał)" hint="Język mowy w filmie. „Automatyczne wykrywanie” — aplikacja sama rozpozna; wybierz konkretny, jeśli się myli.">
                <Select value={d.source_lang} options={meta.dub.source_languages} onChange={(v) => set({ source_lang: v })} />
              </Field>
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || anyRunning} onClick={analyze}>
                <WandSparkles size={15} /> Analizuj (transkrypcja)
              </button>
            </div>
            <div className="dub-force-row">
              <Toggle checked={forceTranscribe} label="Wymuś ponowną transkrypcję (pełny reset)" hint="Usuwa poprzednią sesję dubbingu i cache transkrypcji tego filmu, po czym tworzy wynik od nowa Whisperem. Użyj po błędzie lub halucynacji." onChange={setForceTranscribe} />
            </div>
          </div>
        )}

        {sourceMode === 'file' && (
          <>
            <DropZone media={media} formats="MP4 · MOV · MKV · WEBM · MP3 · WAV" onPick={pick} compact />
            <div className="analyze-row">
              <Field label="Język filmu (oryginał)" hint="Język mowy w pliku. „Automatyczne wykrywanie” — aplikacja sama rozpozna.">
                <Select value={d.source_lang} options={meta.dub.source_languages} onChange={(v) => set({ source_lang: v })} />
              </Field>
              <button type="button" className="primary-btn analyze-btn" disabled={!canRun || anyRunning} onClick={analyze}>
                <WandSparkles size={15} /> Analizuj (transkrypcja)
              </button>
            </div>
            <div className="dub-force-row">
              <Toggle checked={forceTranscribe} label="Wymuś ponowną transkrypcję (pełny reset)" hint="Usuwa poprzednią sesję dubbingu i cache transkrypcji tego pliku, po czym tworzy wynik od nowa Whisperem. Użyj po błędzie lub halucynacji." onChange={setForceTranscribe} />
            </div>
          </>
        )}

        {(sourceMode === 'history' || sourceMode === 'favorites') && (() => {
          const list = sourceMode === 'favorites' ? dubProjects.filter((p) => dubFavs.includes(p.id)) : dubProjects
          if (list.length === 0) {
            return (
              <div className="yt-card">
                <p className="settings-desc" style={{ margin: 0 }}>
                  {sourceMode === 'history'
                    ? 'Brak zapisanych dubbingów. Wygeneruj pierwszy z „Link YouTube” lub „Plik lokalny”, a pojawi się tutaj.'
                    : 'Brak ulubionych. Kliknij serduszko przy gotowym dubbingu, aby zapisać go tutaj.'}
                </p>
              </div>
            )
          }
          return (
            <div className="dub-tiles">
              {list.map((p) => {
                const vurl = `${getBase()}${p.video_url}`
                return (
                  <div className="dub-tile" key={p.id}>
                    <video src={vurl} controls playsInline preload="metadata" className="dub-tile-video" />
                    <div className="dub-tile-body">
                      <strong className="dub-tile-title" title={p.title}>{p.title}</strong>
                      <span className="dub-tile-meta">{p.language}{p.mix_mode ? ` · ${audioModeTag(p.mix_mode) || 'dubbing'}` : ''}</span>
                    </div>
                    <div className="dub-tile-actions">
                      <a className="ghost-btn icon-btn" href={dlUrl(vurl)} download title="Pobierz wideo"><Download size={15} /></a>
                      {p.subtitle_url && (
                        <a className="ghost-btn icon-btn" href={dlUrl(`${getBase()}${p.subtitle_url}`)} download title="Pobierz napisy (SRT)"><Captions size={15} /></a>
                      )}
                      <button type="button" className={dubFavs.includes(p.id) ? 'ghost-btn icon-btn fav-on' : 'ghost-btn icon-btn'} title={dubFavs.includes(p.id) ? 'Usuń z ulubionych' : 'Dodaj do ulubionych'} onClick={() => toggleFav(p.id)}>
                        <Heart size={15} />
                      </button>
                      <button type="button" className="ghost-btn icon-btn danger" title="Usuń" onClick={() => removeProject(p.id)}>
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })()}

        {/* Staged workflow + progress live only on the working tabs. */}
        {(sourceMode === 'youtube' || sourceMode === 'file') && (
          <>
            {analyzeJob.state.status === 'running' && (
              <InlineJobProgress state={analyzeJob.state} label="Transkrypcja (Whisper)" onCancel={analyzeJob.cancel} />
            )}

            {session && (() => {
              const rr = renderJob.state.result as { url?: string; subtitle_url?: string; language?: string } | null
              const dubbedUrl = (renderJob.state.status === 'done' && rr?.url) ? `${getBase()}${rr.url}` : ''
              const hasResult = renderJob.state.status === 'done' && !!dubbedUrl
              const showGenerate = transSegs.length > 0 && (dirty || !hasResult)
              return (
              <div className="dub-stage">
                {/* Two players side by side: original (always) + dubbed (after render). */}
                <div className="dub-players">
                  <div className="dub-player-col">
                    <span className="dub-player-label">Oryginał</span>
                    {originalUrl
                      ? <video src={originalUrl} controls playsInline preload="metadata" className="dub-player-video" />
                      : <div className="dub-player-ph">Ładowanie oryginału…</div>}
                  </div>
                  <div className="dub-player-col">
                    <span className="dub-player-label">Dubbing{rr?.language ? ` (${rr.language})` : ''}</span>
                    {dubbedUrl ? (
                      <>
                        <video src={dubbedUrl} controls playsInline preload="metadata" className="dub-player-video" />
                        <div className="dub-result-actions">
                          <a className="primary-btn short-action-btn" href={dlUrl(dubbedUrl)} download><Download size={15} /> Wideo</a>
                          {rr?.subtitle_url && <a className="ghost-btn short-action-btn" href={dlUrl(`${getBase()}${rr.subtitle_url}`)} download><Captions size={15} /> SRT</a>}
                        </div>
                      </>
                    ) : <div className="dub-player-ph">Pojawi się po wygenerowaniu dubbingu</div>}
                  </div>
                </div>

                {transSegs.length === 0 ? (
                  <>
                    <div className="dub-stage-title">
                      <strong>1 · Transkrypcja (oryginał)</strong>
                      <span>Popraw ewentualne błędy Whispera, wybierz język docelowy i przetłumacz.</span>
                    </div>
                    <SegmentEditor primary={origSegs} onPrimary={setOrigSegs} />
                    {origWords.length > 0 && (
                      <div className="dub-word-timing-action">
                        <span>Potrzebujesz skorygować moment pojawienia się konkretnego słowa?</span>
                        <button type="button" className="ghost-btn" disabled={anyRunning} onClick={() => setWordEditorOpen(true)}>
                          <Clock3 size={15} /> Edytuj timingi słów
                        </button>
                      </div>
                    )}
                    <div className="dub-stage-action two">
                      <button type="button" className="ghost-btn" disabled={anyRunning || origSegs.length === 0} onClick={genOrigSubs}>
                        <Captions size={15} /> Napisy oryginału (SRT + VTT)
                      </button>
                      {translateJob.state.status === 'running'
                        ? <InlineJobProgress state={translateJob.state} label="Tłumaczenie" onCancel={translateJob.cancel} />
                        : <div className="lang-pick">
                            <Select value={String(d.target_lang)} options={meta.dub.target_languages} onChange={setTargetLang} />
                            <button type="button" className="primary-btn" disabled={anyRunning} onClick={retranslate}>
                              <Languages size={15} /> Przetłumacz
                            </button>
                          </div>}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="dub-stage-title">
                      <strong>2 · Tekst do dubbingu</strong>
                      <span>Po lewej oryginał, po prawej tłumaczenie. Możesz zmienić język i przetłumaczyć ponownie.</span>
                    </div>
                    <SegmentEditor
                      primary={origSegs} onPrimary={setOrigSegs} primaryLabel="Oryginał"
                      secondary={transSegs} onSecondary={setTransSegs} secondaryLabel={`Tłumaczenie (${String(d.target_lang)})`}
                    />
                    <div className="dub-stage-action">
                      {translateJob.state.status === 'running'
                        ? <InlineJobProgress state={translateJob.state} label="Tłumaczenie" onCancel={translateJob.cancel} />
                        : <div className="lang-pick">
                            <Select value={String(d.target_lang)} options={meta.dub.target_languages} onChange={setTargetLang} />
                            <button type="button" className="ghost-btn" disabled={anyRunning} onClick={retranslate}>
                              <Languages size={15} /> Przetłumacz ponownie
                            </button>
                          </div>}
                    </div>
                    {renderJob.state.status === 'running' ? (
                      <InlineJobProgress state={renderJob.state} label="Generowanie dubbingu" onCancel={renderJob.cancel} />
                    ) : subtitlesJob.state.status === 'running' ? (
                      <InlineJobProgress state={subtitlesJob.state} label="Generowanie napisów" onCancel={subtitlesJob.cancel} />
                    ) : showGenerate ? (
                      <div className="dub-stage-action two">
                        <button type="button" className="primary-btn" disabled={anyRunning} onClick={render}>
                          <WandSparkles size={15} /> 3 · Generuj dubbing
                        </button>
                        <button type="button" className="ghost-btn" disabled={anyRunning} onClick={genSubs}>
                          <Captions size={15} /> Generuj same napisy (SRT + VTT)
                        </button>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
              )
            })()}

            {subtitlesJob.state.status === 'done' && (() => {
              const r = subtitlesJob.state.result as { subtitle_url?: string; vtt_url?: string; language?: string } | null
              if (!r?.subtitle_url && !r?.vtt_url) return null
              return (
                <div className="dub-result">
                  <p className="settings-desc" style={{ margin: '0 0 8px' }}>Napisy {r.language} gotowe (standard YouTube).</p>
                  <div className="dub-result-actions">
                    {r.subtitle_url && <a className="primary-btn short-action-btn" href={dlUrl(`${getBase()}${r.subtitle_url}`)} download><Download size={15} /> SRT</a>}
                    {r.vtt_url && <a className="ghost-btn short-action-btn" href={dlUrl(`${getBase()}${r.vtt_url}`)} download><Download size={15} /> VTT</a>}
                  </div>
                </div>
              )
            })()}

            {wordEditorOpen && session && (
              <SubtitleEditor
                title="oryginalnego tekstu dubbingu"
                segments={origSegs.map((s) => ({ start_time: s.start, end_time: s.end, text: s.text }))}
                words={origWords}
                sourceUrl={originalUrl}
                fetchPeaks={(start, end, buckets) => api.dubPeaks(session, start, end, buckets)}
                saving={savingWordEdit}
                onClose={() => !savingWordEdit && setWordEditorOpen(false)}
                onSave={(data) => { void saveWordEdit(data) }}
              />
            )}
          </>
        )}

        {!online && (
          <p className="offline-note">
            Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>, aby aktywować silniki.
          </p>
        )}
      </section>

      <aside className="inspector accordions">
        <Section icon={<Mic2 size={16} />} title="Głos i synteza">
          <p className="settings-desc" style={{ marginTop: 0 }}>
            Aktywny silnik: <strong>{isOmni ? 'OmniVoice' : 'Qwen TTS'}</strong>. Zmienisz go w
            Ustawieniach → „Silnik głosu (TTS)”.
          </p>
          <Field label="Źródło głosu" hint="Skąd wziąć głos lektora. „Z oryginalnego filmu” — kopiuje (klonuje) barwę głosu mówcy z wideo. „Własna próbka” — używa Twojego nagrania głosu. „Baza Qwen” — gotowy, syntetyczny głos modelu, nic nie musisz nagrywać.">
            <Select value={voiceSource} options={voiceSourceOptions} onChange={(v) => set({ voice_source: v })} />
          </Field>

          {/* Own-sample: pick + manage the saved voice samples. */}
          {isOwnSample && (
            <Field label="Próbka głosu" hint="Wybierz zapisaną próbkę albo wgraj własne nagranie głosu do sklonowania. Najlepiej 8–20 sekund czystej mowy, bez muzyki w tle. Użyj ▶, aby odsłuchać.">
              <div className="voice-pick-row">
                <Select
                  value={(meta.voices.find((v) => v.id === (d.selected_voice_id || meta.voices[0]?.id))?.label) ?? ''}
                  options={meta.voices.map((v) => v.label)}
                  onChange={(label) => { const m = meta.voices.find((v) => v.label === label); if (m) set({ selected_voice_id: m.id }) }}
                />
                <VoiceSamplePreview path={selectedVoicePath} />
              </div>
              <VoiceManager
                voices={meta.voices}
                selectedPath={selectedVoicePath}
                onChanged={refresh}
                onDeletedSelected={() => set({ selected_voice_id: '' })}
              />
            </Field>
          )}

          {/* Qwen preset: pick a ready-made voice. */}
          {isQwenPreset && (
            <Field label="Głos presetowy (Qwen)" hint="Wybierz jeden z gotowych głosów modelu (różne barwy męskie i żeńskie). Nic nie nagrywasz — od razu gotowe.">
              <Select value={d.dubbing_qwen_speaker} options={meta.dub.speakers} onChange={(v) => set({ dubbing_qwen_speaker: v })} />
            </Field>
          )}

          {/* Cloning options only matter when we actually clone a timbre. */}
          {isClone && (
            <Field label="Tryb klonowania" hint="„Strict” — stabilniejsza, wierniejsza barwa głosu (bezpieczny wybór). „Expressive” — więcej ekspresji i emocji, ale mniej stabilna barwa.">
              <Select value={d.clone_mode} options={meta.dub.clone_modes} onChange={(v) => set({ clone_mode: v })} />
            </Field>
          )}

          {isOmni ? (
            <OmniVoiceParams d={d as Record<string, unknown>} set={set} showSpeed={false} />
          ) : (
            <Field label="Model TTS" hint="„1.7B” — wyższa jakość i naturalność głosu. „0.6B” — szybszy i lżejszy, dla słabszego sprzętu.">
              <PillGroup
                value={d.tts_model}
                options={meta.dub.tts_models.map((m) => ({ value: m, label: m.split(' ')[0] }))}
                onChange={(v) => set({ tts_model: v })}
              />
            </Field>
          )}

          {/* Reference length + ambient/filtered choice apply when cloning from the film. */}
          {voiceSource === 'Głos z oryginalnego filmu' && (
            <>
              <Field label="Rodzaj głosu z oryginału" hint="„Odfiltrowany głos” — czysty głos mówcy bez pogłosu, muzyki i odgłosów otoczenia (zalecane — najwierniejszy klon). „Oryginalny z ambientem” — głos wraz z tłem i pogłosem oryginału.">
                <Select
                  value={String(d.voice_ref ?? 'filtered') === 'ambient' ? 'Oryginalny z ambientem' : 'Odfiltrowany głos (czysty)'}
                  options={['Odfiltrowany głos (czysty)', 'Oryginalny z ambientem']}
                  onChange={(v) => set({ voice_ref: v.startsWith('Oryginalny') ? 'ambient' : 'filtered' })}
                />
              </Field>
              <Slider label="Długość próbki głosu" value={d.ref_audio_length} min={3} max={30} suffix=" s"
                hint="Ile sekund mowy z filmu nagrać jako wzór głosu do klonowania. 10–15 s zwykle wystarcza — za krótka próbka daje gorsze podobieństwo."
                onChange={(v) => set({ ref_audio_length: v })} />
            </>
          )}
        </Section>

        <Section icon={<SlidersHorizontal size={16} />} title="Miks i synchronizacja">
          <Field label="Tryb miksu głosu" hint="„Czysty dubbing” — kasuje oryginalny głos, zostaje sam głos AI (plus tło, jeśli włączysz). „Lektor” — oryginał gra w tle, a lektor AI czyta na wierzchu (jak w TV). „Lektor z duckingiem” — oryginał dodatkowo przycisza się, gdy mówi lektor.">
            <Select value={d.mix_mode} options={meta.dub.mix_modes} onChange={(v) => set({ mix_mode: v })} />
          </Field>
          {isVoiceover && (
            <Field label="Silnik lektora" hint="„Stabilny lektor systemowy” — nigdy nie zmyśla ani nie ucina słów, najpewniejszy do voice-over. „Qwen” — brzmi naturalniej, ale w trybie lektora rzadziej potrafi uciąć lub dopowiedzieć tekst.">
              <Select value={d.voiceover_tts_engine} options={meta.dub.voiceover_engines} onChange={(v) => set({ voiceover_tts_engine: v })} />
            </Field>
          )}
          <Slider label="Głośność dubbingu" value={d.dub_vol} min={0.1} max={3.0} step={0.05}
            hint="Głośność nowego głosu AI w gotowym filmie. 1 = bez zmian, wyżej = głośniej." onChange={(v) => set({ dub_vol: v })} />
          {keepsBackground && (
            <Slider label="Głośność tła" value={d.bg_music_vol} min={0.0} max={2.0} step={0.05}
              hint="Głośność muzyki i dźwięków tła z oryginału (działa, gdy włączysz „Zachowaj muzykę i tło”). 1 = bez zmian." onChange={(v) => set({ bg_music_vol: v })} />
          )}
          {isVoiceover && (
            <Slider label="Głośność oryginału" value={d.voiceover_original_vol} min={0.0} max={1.5} step={0.05}
              hint="Głośność oryginalnej ścieżki filmu pod lektorem, gdy lektor NIE mówi. 1 = normalnie." onChange={(v) => set({ voiceover_original_vol: v })} />
          )}
          {isDucking && (
            <Slider label="Ściszanie oryginału pod lektorem" value={d.voiceover_duck_amount} min={0.0} max={1.0} step={0.05}
              hint="Jak mocno oryginał przycisza się, GDY mówi lektor (poza tym gra normalnie). Im wyżej, tym ciszej oryginał pod głosem AI: 0 = wcale, 0,8 = mocno, 1 = prawie niesłyszalny." onChange={(v) => set({ voiceover_duck_amount: v })} />
          )}
          <div className="toggle-grid">
            <Toggle checked={!!d.auto_min_tempo} label="AUTO min tempo" hint="Włączone = aplikacja sama decyduje, jak zwolnić głos AI, gdy tłumaczenie jest krótsze niż scena. Wyłącz, by ustawić ręcznie." onChange={(v) => set({ auto_min_tempo: v })} />
            <Toggle checked={!!d.auto_max_tempo} label="AUTO max tempo" hint="Włączone = aplikacja sama dobiera, jak przyspieszyć głos AI, by zmieścił się w czasie sceny, ale wciąż brzmiał naturalnie." onChange={(v) => set({ auto_max_tempo: v })} />
          </div>
          {!d.auto_min_tempo && (
            <Slider label="Minimalne tempo" value={d.sync_min_tempo} min={0.5} max={1.5} step={0.05}
              hint="Najwolniej, jak głos AI może być odtworzony (1 = normalnie). Niżej pomaga, gdy tłumaczenie jest krótsze niż scena." onChange={(v) => set({ sync_min_tempo: v })} />
          )}
          {!d.auto_max_tempo && (
            <Slider label="Maksymalne tempo" value={d.sync_max_tempo} min={1.0} max={2.0} step={0.05}
              hint="Najszybciej, jak głos AI może być odtworzony (1 = normalnie). Wyżej pomaga zmieścić dłuższe tłumaczenie w krótszej scenie." onChange={(v) => set({ sync_max_tempo: v })} />
          )}
          <Slider label="Korekta tonu" value={d.pitch_adj} min={-6} max={6} step={0.5}
            hint="Podnosi (+) lub obniża (−) wysokość głosu AI. 0 = bez zmian. Minus = niższy, poważniejszy głos; plus = wyższy." onChange={(v) => set({ pitch_adj: v })} />
        </Section>

        <Section icon={<Music2 size={16} />} title="Tło i separacja audio">
          <Toggle checked={!!d.keep_bg} label="Zachowaj muzykę i tło"
            hint="Zostawia muzykę i dźwięki tła z oryginału, a usuwa tylko oryginalny głos. Włącz, gdy w filmie gra muzyka — inaczej zniknie razem z głosem." onChange={(v) => set({ keep_bg: v })} />
          {keepsBackground && (
            <>
              <Slider label="Dokładność oddzielania tła" value={d.demucs_shifts} min={1} max={10}
                hint="Jak dokładnie oddzielić głos od muzyki/tła. Więcej = czystszy efekt, ale dłużej trwa. 2 to dobry kompromis." onChange={(v) => set({ demucs_shifts: v })} />
              <Slider label="Głośność dźwięków otoczenia" value={d.ambient_vol} min={0.0} max={2.0} step={0.05}
                hint="Głośność odgłosów otoczenia (oklaski, śmiech, gwar publiczności). 0 = wyciszone." onChange={(v) => set({ ambient_vol: v })} />
              <Toggle checked={!!d.ambient_eq_enabled} label="Oczyść dźwięki otoczenia"
                hint="Delikatnie filtruje kanał otoczenia, żeby brzmiał naturalnie i nie zagłuszał mowy lektora." onChange={(v) => set({ ambient_eq_enabled: v })} />
              {d.ambient_eq_enabled && (
                <>
                  <Slider label="Odcięcie basów (Hz)" value={d.ambient_eq_hp} min={20} max={1000} step={10}
                    hint="Usuwa niskie dudnienie i muzykę z kanału otoczenia. Wyżej = więcej odcięte. Typowo 200 Hz." onChange={(v) => set({ ambient_eq_hp: v })} />
                  <Slider label="Wyrazistość (dB)" value={d.ambient_eq_presence} min={0} max={12} step={0.5}
                    hint="Podbija „powietrze” i klarowność otoczenia (oklaski, szelest). Typowo 4 dB." onChange={(v) => set({ ambient_eq_presence: v })} />
                  <Slider label="Odcięcie pasma mowy (Hz)" value={d.ambient_eq_lpf_speech} min={2000} max={8000} step={100}
                    hint="Wycisza w otoczeniu pasmo ludzkiej mowy, by nie kłóciło się z lektorem. Typowo 3500 Hz." onChange={(v) => set({ ambient_eq_lpf_speech: v })} />
                </>
              )}
            </>
          )}
        </Section>

        <Section icon={<Clapperboard size={16} />} title="Wideo wyjściowe">
          <Field label="Rozdzielczość" hint="Domyślnie Auto — wideo wyjściowe ma taką samą rozdzielczość jak oryginał.">
            <Select value={d.output_resolution} options={meta.dub.output_resolutions} onChange={(v) => set({ output_resolution: v })} />
          </Field>
          <Slider label="Bitrate" value={d.output_bitrate_mbps} min={1} max={50} step={0.5} suffix=" Mb/s"
            hint="Domyślnie 5 Mbps — dobry dla 1080p przy przesyłaniu online." onChange={(v) => set({ output_bitrate_mbps: v })} />
        </Section>

      </aside>
    </div>
  )
}
