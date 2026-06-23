import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, WandSparkles, Download, Trash2, Cpu, MemoryStick, Power, SlidersHorizontal, AlertTriangle, Sparkles, Music4, ListMusic, RotateCcw, FolderOpen } from 'lucide-react'
import { api, getBase, type MusicEngineStatus, type MusicHistoryItem, type MusicTrack } from '../lib/api'
import type { useStudio } from '../lib/useStudio'
import { usePersistentState } from '../lib/usePersistentState'
import { InlineJobProgress } from '../components/JobProgress'
import { Field, Section, TextField, TextArea, Toggle, Slider } from '../components/ui'
import { PresetBar } from '../components/PresetBar'

type Studio = ReturnType<typeof useStudio>

/** Quick style presets — common YouTube / vlog / drone / background use cases. Clicking
 *  appends the descriptor to the prompt (click again to remove), so styles can be mixed. */
const STYLE_PRESETS: { label: string; text: string }[] = [
  { label: 'Vlog / Pop', text: 'upbeat indie pop, bright acoustic guitars, claps, warm vocals, feel-good vlog background' },
  { label: 'Dron / Cinematic', text: 'epic cinematic orchestral, soaring strings, big drums, ambient pads, aerial drone footage score' },
  { label: 'Lo-fi / Chill', text: 'lo-fi hip hop, mellow piano, vinyl crackle, soft drums, relaxed study beat' },
  { label: 'Podkład techniczny', text: 'minimal corporate background, clean synth pads, subtle pulse, neutral tech tutorial underscore' },
  { label: 'Piano emocjonalne', text: 'emotional solo piano, soft reverb, intimate, melancholic, slow tempo' },
  { label: 'Gitara akustyczna', text: 'fingerstyle acoustic guitar, warm, organic, folk, gentle' },
  { label: 'EDM / Energia', text: 'energetic EDM, festival synths, four-on-the-floor, big drop, driving bass' },
  { label: 'Rock', text: 'driving rock, distorted electric guitars, punchy drums, powerful vocal' },
  { label: 'Trap / Hip-hop', text: 'modern trap beat, deep 808 bass, crisp hi-hats, dark moody synths' },
  { label: 'Ambient', text: 'calm ambient, evolving pads, atmospheric, spacious, background texture' },
  { label: 'Korporacyjne', text: 'uplifting corporate, motivational piano, light strings, claps, positive presentation music' },
  { label: 'Synthwave', text: '80s synthwave, neon retro synths, gated reverb drums, nostalgic' },
]

/** Native select that shows a human label while storing a raw value — for the many
 *  ACE-Step option lists (language / BPM / key / vocal type) that have label maps. */
function LabelSelect({
  value, options, labels, onChange,
}: { value: string; options: string[]; labels?: Record<string, string>; onChange: (v: string) => void }) {
  return (
    <div className="select">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o} value={o}>{labels?.[o] ?? o}</option>
        ))}
      </select>
      <ChevronDown size={14} />
    </div>
  )
}

/** Client-side mirror of music_pipeline.estimate_duration — keeps the hint instant. */
function estimateDuration(lyrics: string): { min: number; max: number; suggested: number; words: number } {
  const words = lyrics
    .split('\n')
    .filter((l) => !(l.trim().startsWith('[') && l.trim().endsWith(']')))
    .join(' ')
    .match(/[\p{L}\p{N}'-]+/gu)?.length ?? 0
  if (words === 0) return { min: 60, max: 90, suggested: 75, words: 0 }
  const min = Math.max(30, Math.round((words / 130) * 60) + 16)
  const max = Math.max(min + 15, Math.round((words / 95) * 60) + 24)
  const suggested = Math.max(min, Math.min(600, Math.round((Math.round((words / 130) * 60) + Math.round((words / 95) * 60)) / 2) + 20))
  return { min: Math.min(600, min), max: Math.min(600, max), suggested: Math.min(600, Math.max(10, suggested)), words }
}

const ENGINE_LABEL: Record<MusicEngineStatus['state'], string> = {
  ready: 'Model w pamięci', loading: 'Ładowanie…', stopped: 'Uśpiony',
}

export function MusicGenerator({ studio }: { studio: Studio }) {
  const { config, meta, update, online, musicGenJob, musicLoadJob } = studio
  const m = config.music
  const mm = meta.music
  const set = (patch: Record<string, unknown>) => update('music', patch)

  const [tab, setTab] = usePersistentState<'generator' | 'gallery'>('dubcut.music.tab', 'generator')
  const [engine, setEngine] = useState<MusicEngineStatus | null>(null)
  const [history, setHistory] = useState<MusicHistoryItem[]>([])
  const pollRef = useRef<number | null>(null)

  const refreshEngine = useCallback(async () => {
    try { setEngine(await api.musicStatus()) } catch { setEngine(null) }
  }, [])
  const refreshHistory = useCallback(async () => {
    try { setHistory(await api.musicHistory()) } catch { /* offline */ }
  }, [])

  // Poll engine status while online so the pill + button states stay live.
  useEffect(() => {
    if (!online) return
    refreshEngine()
    refreshHistory()
    pollRef.current = window.setInterval(refreshEngine, 3000)
    return () => { if (pollRef.current) window.clearInterval(pollRef.current) }
  }, [online, refreshEngine, refreshHistory])

  // Refresh history + engine when a generation finishes.
  useEffect(() => {
    if (musicGenJob.state.status === 'done') { refreshHistory(); refreshEngine() }
  }, [musicGenJob.state.status, refreshHistory, refreshEngine])
  useEffect(() => {
    if (musicLoadJob.state.status === 'done') refreshEngine()
  }, [musicLoadJob.state.status, refreshEngine])

  const generating = musicGenJob.state.status === 'running'
  const loading = musicLoadJob.state.status === 'running'
  const busy = generating || loading
  const engineAvailable = engine ? engine.engine_available : true
  const model = String(m.model || mm.default_model)
  const problemReason = mm.problematic_models?.[model]
  const minSteps = 4
  const maxSteps = model.includes('turbo') ? 8 : 64
  const variantOptions = model.startsWith('acestep-v15-xl') ? [1] : mm.variants
  const instrumental = Boolean(m.instrumental)

  const est = estimateDuration(String(m.lyrics || ''))
  const prompt = String(m.prompt || '')

  const togglePreset = (text: string) => {
    const cur = prompt
    if (cur.toLowerCase().includes(text.toLowerCase())) {
      const next = cur
        .replace(text, '')
        .replace(/,\s*,/g, ', ')
        .replace(/^[\s,]+|[\s,]+$/g, '')
        .replace(/\s{2,}/g, ' ')
        .trim()
      set({ prompt: next })
    } else {
      const base = cur.trim().replace(/,\s*$/, '')
      set({ prompt: base ? `${base}, ${text}` : text })
    }
  }

  const generate = () => {
    if (busy) return
    musicGenJob.start(() => api.musicGenerate({ ...m }))
  }
  const loadModel = () => { if (!busy) musicLoadJob.start(() => api.musicLoad(model)) }
  const unloadModel = async () => {
    try { await api.musicUnload() } catch { /* */ }
    refreshEngine()
  }
  const deleteTrack = async (fileName: string) => {
    if (!window.confirm(`Usunąć „${fileName}”?`)) return
    try { await api.deleteMusicHistory(fileName) } catch { /* */ }
    refreshHistory()
  }

  const genResult = musicGenJob.state.result as { tracks?: MusicTrack[] } | null
  const tracks = (musicGenJob.state.status === 'done' && genResult?.tracks) ? genResult.tracks : []
  const audioUrl = (u: string) => `${getBase()}${u}`
  const dlUrl = (u: string) => audioUrl(u) + (u.includes('?') ? '&' : '?') + 'dl=1'

  return (
    <div className="music-view">
      <div className="music-tabbar">
        <button type="button" className={tab === 'generator' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('generator')}>
          <Music4 size={15} /> Generator
        </button>
        <button type="button" className={tab === 'gallery' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('gallery')}>
          <ListMusic size={15} /> Galeria {history.length > 0 && <em className="music-tab-badge">{history.length}</em>}
        </button>
        <span className="music-tabbar-spacer" />
        <span className={`status-pill ${engine?.state === 'ready' ? 'ok' : engine?.state === 'loading' ? 'busy' : ''}`}>
          {engine ? ENGINE_LABEL[engine.state] : '—'}
        </span>
      </div>

      {tab === 'gallery' ? (
        <MusicGallery history={history} audioUrl={audioUrl} dlUrl={dlUrl} onDelete={deleteTrack} onCreate={() => setTab('generator')}
          onReuse={(s) => { set(s); setTab('generator') }} onReveal={(p) => studio.revealPath(p)} />
      ) : (
        <div className="studio-grid">
          <section className="editor">
            <header className="editor-head">
              <div>
                <span className="eyebrow">Generator muzyki</span>
                <h2>Muzyka <small className="head-engine">ACE-Step · lokalnie</small></h2>
              </div>
            </header>

            <PresetBar moduleKey="music" value={m} exclude={['title', 'prompt', 'lyrics', 'seed']} onApply={(s) => set(s)} />

            <Field label="Tytuł" hint="Nazwa widoczna w galerii i w nazwie pliku. Nie wpływa na samo generowanie muzyki.">
              <TextField value={String(m.title ?? '')} placeholder="np. Król z Floriańskiej" onChange={(v) => set({ title: v })} />
            </Field>

            <Field label="Styl muzyczny" hint="Opisz gatunek, nastrój, instrumenty, wokal i brzmienie miksu. Im konkretniej (tempo, energia, typ wokalu, referencje gatunkowe), tym lepiej.">
              <TextArea value={prompt} rows={4} placeholder="np. energetic indie pop, warm live drums, bright guitars, emotional male vocal" onChange={(v) => set({ prompt: v })} />
            </Field>

            <div className="style-presets">
              <span className="style-presets-label"><Sparkles size={12} /> Szybkie style — kliknij, by dodać lub zmiksować:</span>
              <div className="style-presets-row">
                {STYLE_PRESETS.map((p) => {
                  const active = prompt.toLowerCase().includes(p.text.toLowerCase())
                  return (
                    <button key={p.label} type="button" className={active ? 'preset-pill active' : 'preset-pill'} onClick={() => togglePreset(p.text)} title={p.text}>
                      {p.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <Field label="Tekst" hint="Tekst piosenki. Możesz używać sekcji [Verse], [Chorus], [Bridge]. Przy włączonym Instrumentalu tekst jest pomijany.">
              <TextArea value={String(m.lyrics ?? '')} rows={10} placeholder={'[Verse]\n…\n\n[Chorus]\n…'} onChange={(v) => set({ lyrics: v })} />
            </Field>
            {instrumental && (
              <p className="settings-desc info-note"><AlertTriangle size={13} /> Instrumental włączony — tekst zostanie pominięty, powstanie utwór bez wokalu.</p>
            )}

            {est.words > 0 && !instrumental && (
              <div className="music-duration-hint">
                <span>Tekst ma ok. <b>{est.words}</b> słów. Sensowna długość: <b>{est.min}–{est.max}s</b> · propozycja <b>{est.suggested}s</b>.</span>
                <button type="button" className="ghost-btn" disabled={busy} onClick={() => set({ duration: est.suggested })}>
                  Ustaw {est.suggested}s
                </button>
              </div>
            )}

            {generating
              ? <InlineJobProgress state={musicGenJob.state} label="Generowanie utworu" onCancel={musicGenJob.cancel} />
              : <button type="button" className="primary-btn tts-generate-btn" disabled={busy || !!problemReason || !engineAvailable} onClick={generate}>
                  <WandSparkles size={15} /> Generuj muzykę
                </button>}

            {problemReason && <p className="settings-desc warn"><AlertTriangle size={13} /> {problemReason}</p>}
            {!engineAvailable && (
              <p className="offline-note">Nie znaleziono silnika ACE-Step. Wskaż folder w <b>Ustawienia → Music Generator</b>.</p>
            )}

            {tracks.length > 0 && (
              <div className="dub-result">
                {tracks.map((t, i) => (
                  <div key={t.file_name} className="music-track">
                    <div className="music-track-head"><span>{t.file_name}</span></div>
                    <audio src={audioUrl(t.url)} controls style={{ width: '100%' }} />
                    <div className="dub-result-actions">
                      <a className="primary-btn short-action-btn" href={dlUrl(t.url)} download><Download size={15} /> Pobierz {tracks.length > 1 ? `(${i + 1})` : ''}</a>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {!online && (
              <p className="offline-note">Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>.</p>
            )}
          </section>

          <aside className="inspector accordions">
            <Section icon={<Cpu size={16} />} title="Silnik" id="music-engine">
              <p className="settings-desc">
                Model ACE-Step ładuje się do pamięci tylko na czas tworzenia muzyki. Możesz wczytać go
                ręcznie z wyprzedzeniem albo zwolnić pamięć, gdy skończysz.
              </p>
              <div className="settings-meta">
                <div><dt>Stan</dt><dd>{engine ? ENGINE_LABEL[engine.state] : '—'}</dd></div>
                {engine?.loaded_model && <div><dt>Model w pamięci</dt><dd className="mono">{engine.loaded_model}</dd></div>}
              </div>
              {loading
                ? <InlineJobProgress state={musicLoadJob.state} label="Ładowanie modelu" onCancel={musicLoadJob.cancel} />
                : (
                  <div className="settings-actions">
                    <button type="button" className="ghost-btn" disabled={busy || !engineAvailable} onClick={loadModel}>
                      <MemoryStick size={15} /> Wczytaj do pamięci
                    </button>
                    <button type="button" className="ghost-btn danger" disabled={busy || engine?.state === 'stopped'} onClick={unloadModel}>
                      <Power size={15} /> Zwolnij pamięć
                    </button>
                  </div>
                )}
              <Toggle
                checked={Boolean(m.auto_unload)}
                label="Zwolnij pamięć po wygenerowaniu"
                hint="Po każdym utworze model jest usuwany z pamięci, żeby nie obciążać RAM. Wyłącz, jeśli generujesz wiele utworów pod rząd."
                onChange={(v) => set({ auto_unload: v })}
              />
            </Section>

            <Section icon={<SlidersHorizontal size={16} />} title="Parametry muzyki" id="music-params">
              <Field label="Model" hint="acestep-v15-turbo działa najlepiej na tym Macu. Modele XL/SFT bywają problematyczne.">
                <LabelSelect
                  value={model}
                  options={mm.models}
                  labels={Object.fromEntries(mm.models.map((x) => [x, mm.problematic_models?.[x] ? `${x} – problematyczny` : x]))}
                  onChange={(v) => set({ model: v })}
                />
              </Field>
              <Field label="Język wokalu" hint="Preferowany język wymowy. Automatycznie pozwala silnikowi wykryć język z tekstu.">
                <LabelSelect value={String(m.language ?? 'unknown')} options={mm.languages} labels={mm.language_labels} onChange={(v) => set({ language: v })} />
              </Field>
              <Slider label="Długość" value={Number(m.duration ?? 120)} min={10} max={600} step={5} suffix="s"
                hint="Dobierz do długości tekstu. Za krótko: wokal spieszy/ucina frazy; za długo: model dodaje instrumentalne wstawki."
                onChange={(v) => set({ duration: v })} />
              <Field label="Format" hint="MP3 do szybkiego odsłuchu. WAV/FLAC do dalszej produkcji.">
                <LabelSelect value={String(m.audio_format ?? 'mp3')} options={mm.formats} onChange={(v) => set({ audio_format: v })} />
              </Field>
              <Field label="BPM" hint="Tempo utworu. Automatycznie jest najlepsze na start.">
                <LabelSelect value={String(m.bpm_choice ?? 'auto')} options={mm.bpm_options} labels={mm.bpm_labels} onChange={(v) => set({ bpm_choice: v })} />
              </Field>
              <Field label="Tonacja" hint="Tonacja utworu. Automatycznie pozwala modelowi dobrać tonację do stylu i tekstu.">
                <LabelSelect value={String(m.key_scale_choice ?? 'auto')} options={mm.key_scale_options} labels={mm.key_scale_labels} onChange={(v) => set({ key_scale_choice: v })} />
              </Field>
              <Field label="Metrum" hint="Automatycznie albo 4/4 to najlepszy wybór dla większości popu, rocka i hip-hopu.">
                <LabelSelect value={String(m.time_signature_choice ?? 'auto')} options={mm.time_signature_options} labels={mm.time_signature_labels} onChange={(v) => set({ time_signature_choice: v })} />
              </Field>
              <Field label="Typ wokalu" hint="Pomaga wymusić charakter wokalu. To model generatywny, więc nie zawsze posłucha idealnie.">
                <LabelSelect value={String(m.vocal_type ?? 'auto')} options={mm.vocal_types} labels={mm.vocal_type_labels} onChange={(v) => set({ vocal_type: v })} />
              </Field>
              <Field label="Wersje" hint="Ile wariantów wygenerować w jednym zadaniu. Więcej wersji mocniej obciąża RAM.">
                <LabelSelect
                  value={String(m.variant_count ?? 2)}
                  options={variantOptions.map(String)}
                  labels={Object.fromEntries(variantOptions.map((v) => [String(v), v === 1 ? '1 utwór' : `${v} utwory`]))}
                  onChange={(v) => set({ variant_count: Number(v) })}
                />
              </Field>
              <Slider label="Kroki" value={Math.max(minSteps, Math.min(maxSteps, Number(m.inference_steps ?? minSteps)))} min={minSteps} max={maxSteps} step={1}
                hint="Więcej kroków bywa lepsze jakościowo, ale dłużej trwa. Dla Turbo zacznij od 8."
                onChange={(v) => set({ inference_steps: v })} />
              <Slider label="Guidance" value={Number(m.guidance_scale ?? 7)} min={1} max={15} step={0.5}
                hint="Jak mocno model trzyma się opisu. Zbyt wysoko = sztywny albo przesterowany wynik."
                onChange={(v) => set({ guidance_scale: v })} />
              <Field label="Seed" hint="Puste pole = nowy losowy wariant. Wpisz liczbę, by łatwiej powtórzyć podobny wynik.">
                <TextField value={String(m.seed ?? '')} placeholder="losowy" onChange={(v) => set({ seed: v.replace(/[^0-9]/g, '') })} />
              </Field>
              <Toggle checked={instrumental} label="Instrumental (bez wokalu)" hint="Utwór bez wokalu — pole tekstu jest pomijane przy generowaniu." onChange={(v) => set({ instrumental: v })} />
              <Toggle checked={Boolean(m.thinking)} label="LM thinking" hint="Pozwala modelowi językowemu lepiej zaplanować strukturę. Zwykle warto zostawić włączone." onChange={(v) => set({ thinking: v })} />
            </Section>
          </aside>
        </div>
      )}
    </div>
  )
}

function MusicGallery({
  history, audioUrl, dlUrl, onDelete, onCreate, onReuse, onReveal,
}: {
  history: MusicHistoryItem[]
  audioUrl: (u: string) => string
  dlUrl: (u: string) => string
  onDelete: (file: string) => void
  onCreate: () => void
  onReuse: (settings: Record<string, unknown>) => void
  onReveal: (path: string) => void
}) {
  return (
    <div className="music-gallery">
      <header className="editor-head">
        <div>
          <span className="eyebrow">Galeria utworów</span>
          <h2>Gotowe utwory <small className="head-engine">{history.length} {history.length === 1 ? 'utwór' : 'plików'}</small></h2>
        </div>
        <button type="button" className="primary-btn" onClick={onCreate}>
          <WandSparkles size={15} /> Nowy utwór
        </button>
      </header>

      {history.length === 0 ? (
        <div className="music-gallery-empty">
          <Music4 size={34} />
          <p>Brak utworów. Wygenerowane piosenki pojawią się tutaj — gotowe do odsłuchu i pobrania.</p>
          <button type="button" className="ghost-btn" onClick={onCreate}><WandSparkles size={15} /> Stwórz pierwszy utwór</button>
        </div>
      ) : (
        <div className="music-gallery-grid">
          {history.map((item) => (
            <div key={item.file_name} className="music-card">
              <div className="music-card-head">
                <span className="music-card-icon"><Music4 size={16} /></span>
                <div className="music-card-meta">
                  <span className="music-card-title" title={item.file_name}>{item.file_name.replace(/\.[^.]+$/, '')}</span>
                  <small>{item.created_at}</small>
                </div>
                <button type="button" className="ghost-btn icon-btn danger" title="Usuń" onClick={() => onDelete(item.file_name)}>
                  <Trash2 size={14} />
                </button>
              </div>
              {item.prompt && <p className="music-card-prompt" title={item.prompt}>{item.prompt}</p>}
              <audio src={audioUrl(item.url)} controls style={{ width: '100%' }} />
              <div className="card-action-row">
                <a className="primary-btn short-action-btn" href={dlUrl(item.url)} download><Download size={15} /> Pobierz</a>
                {item.settings && Object.keys(item.settings).length > 0 && (
                  <button type="button" className="ghost-btn" title="Wczytaj ustawienia tego utworu" onClick={() => onReuse(item.settings!)}><RotateCcw size={14} /> Ustawienia</button>
                )}
                {item.path && (
                  <button type="button" className="ghost-btn icon-btn" title="Pokaż w Finderze" onClick={() => onReveal(item.path!)}><FolderOpen size={14} /></button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
