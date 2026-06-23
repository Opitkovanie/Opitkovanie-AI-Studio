import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, WandSparkles, Download, Trash2, SlidersHorizontal, Clapperboard, ListMusic, Languages, Wand2, Eraser, AlertTriangle, FileImage, X, Volume2, Maximize2, RotateCcw, FolderOpen } from 'lucide-react'
import { api, getBase, type VideoHistoryItem, type MediaItem } from '../lib/api'
import type { useStudio } from '../lib/useStudio'
import { usePersistentState } from '../lib/usePersistentState'
import { InlineJobProgress } from '../components/JobProgress'
import { formatClock } from '../lib/jobFormat'
import { Lightbox } from '../components/Lightbox'
import { useJob } from '../lib/useJob'
import type { JobState } from '../lib/useJob'
import { Field, Section, TextField, TextArea, Toggle, Slider, PillGroup } from '../components/ui'
import { PresetBar } from '../components/PresetBar'

type Studio = ReturnType<typeof useStudio>

function DoneTime({ state }: { state: JobState }) {
  if (state.status !== 'done' || !state.startedAt || !state.finishedAt) return null
  return <p className="gen-done">✓ Gotowe w {formatClock((state.finishedAt - state.startedAt) / 1000)}</p>
}

function LabelSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <div className="select">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <ChevronDown size={14} />
    </div>
  )
}

function framesForDuration(duration: number, fps: number): number {
  const target = Math.max(9, Math.round(duration * fps))
  const k = Math.max(1, Math.round((target - 1) / 8))
  return 8 * k + 1
}

export function VideoGenerator({ studio }: { studio: Studio }) {
  const { config, meta, update, online, videoGenJob, chooseImage } = studio
  const v = config.video
  const mm = meta.video
  const set = (patch: Record<string, unknown>) => update('video', patch)

  const [tab, setTab] = usePersistentState<'generator' | 'gallery'>('dubcut.video.tab', 'generator')
  const [status, setStatus] = useState<{ engine_available: boolean; ltx_models_ok: boolean } | null>(null)
  const [history, setHistory] = useState<VideoHistoryItem[]>([])
  const [lightbox, setLightbox] = useState<string | null>(null)
  const enhanceJob = useJob()
  const translateJob = useJob()

  const refreshStatus = useCallback(async () => { try { setStatus(await api.videogenStatus()) } catch { setStatus(null) } }, [])
  const refreshHistory = useCallback(async () => { try { setHistory(await api.videoHistory()) } catch { /* */ } }, [])
  useEffect(() => { if (!online) return; refreshStatus(); refreshHistory() }, [online, refreshStatus, refreshHistory])
  useEffect(() => { if (videoGenJob.state.status === 'done') refreshHistory() }, [videoGenJob.state.status, refreshHistory])

  useEffect(() => {
    if (enhanceJob.state.status === 'done') { const r = enhanceJob.state.result as { text?: string } | null; if (r?.text) set({ prompt: r.text }) }
  }, [enhanceJob.state.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (translateJob.state.status === 'done') { const r = translateJob.state.result as { text?: string } | null; if (r?.text) set({ prompt: r.text }) }
  }, [translateJob.state.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const generating = videoGenJob.state.status === 'running'
  const helperRunning = enhanceJob.state.status === 'running' || translateJob.state.status === 'running'
  const busy = generating || helperRunning
  const engineAvailable = status ? status.engine_available : true

  const modelLabel = String(v.model_label || Object.keys(mm.models)[0])
  const model = mm.models[modelLabel] || ''
  const resLabel = String(v.resolution_label || Object.keys(mm.resolutions)[0])
  const durLabel = String(v.duration_label || '4 s')
  const fps = Number(v.fps ?? 24)
  const prompt = String(v.prompt || '')
  const audioEnabled = Boolean(v.audio_enabled)
  const i2v = String(v.mode || 'Text to video') === 'Image to video'
  const basePath = String(v.image_path || '')

  const frames = framesForDuration(Number(v.duration ?? 4), fps)
  const realSeconds = (frames / fps).toFixed(2)

  const seedRandom = v.seed_random !== false
  const seedValue = () => seedRandom ? Math.floor(Math.random() * 2_147_483_647) : Number(v.seed || 42)

  const pickRes = (label: string) => { const [w, h] = mm.resolutions[label]; set({ resolution_label: label, width: w, height: h }) }
  const pickDur = (label: string) => set({ duration_label: label, duration: mm.durations[label] })
  const pickBaseImage = async () => { const f = await chooseImage(); if (f?.path) set({ image_path: f.path }) }

  const enhance = () => { if (!prompt.trim() || busy) return; enhanceJob.start(() => api.videogenEnhance(prompt.trim(), 'video')) }
  const translate = () => { if (!prompt.trim() || busy) return; translateJob.start(() => api.videogenTranslate(prompt.trim(), 'video')) }

  const generate = () => {
    if (busy || !prompt.trim()) return
    if (i2v && !basePath) { window.alert('Tryb image-to-video wymaga obrazu bazowego.'); return }
    const settings = {
      ...v, model, frames, fps, seed: seedValue(),
      image_path: i2v ? basePath : '',
    }
    videoGenJob.start(() => api.videoGenerate(settings))
  }
  const deleteItem = async (file: string) => {
    if (!window.confirm(`Usunąć „${file}”?`)) return
    try { await api.deleteVideoHistory(file) } catch { /* */ }
    refreshHistory()
  }

  const genResult = videoGenJob.state.result as { items?: MediaItem[] } | null
  const items = (videoGenJob.state.status === 'done' && genResult?.items) ? genResult.items : []
  const mediaUrl = (u: string) => `${getBase()}${u}`
  const dlUrl = (u: string) => mediaUrl(u) + (u.includes('?') ? '&' : '?') + 'dl=1'
  const heavy = Number(v.width) * Number(v.height) >= 1280 * 720
  const ram = studio.sysStats?.ram ?? null
  const preflight = heavy && ram != null && ram >= 70
    ? `Ciężki preset (${v.width}×${v.height}) przy zajętości RAM ${ram}% — render może być bardzo wolny albo zabraknie pamięci. Rozważ niższą rozdzielczość.`
    : ram != null && ram >= 90
      ? `RAM zajęty w ${ram}% — zamknij inne aplikacje przed renderem.`
      : ''

  return (
    <div className="music-view">
      <div className="music-tabbar">
        <button type="button" className={tab === 'generator' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('generator')}>
          <Clapperboard size={15} /> Generator
        </button>
        <button type="button" className={tab === 'gallery' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('gallery')}>
          <ListMusic size={15} /> Galeria {history.length > 0 && <em className="music-tab-badge">{history.length}</em>}
        </button>
        <span className="music-tabbar-spacer" />
        <span className={`status-pill ${engineAvailable ? 'ok' : ''}`}>{engineAvailable ? 'Silnik gotowy' : 'Brak silnika'}</span>
      </div>

      {lightbox && <Lightbox src={lightbox} kind="video" onClose={() => setLightbox(null)} />}
      {tab === 'gallery' ? (
        <VideoGallery history={history} mediaUrl={mediaUrl} dlUrl={dlUrl} onDelete={deleteItem} onCreate={() => setTab('generator')} onZoom={setLightbox}
          onReuse={(s) => { set(s); setTab('generator') }} onReveal={(p) => studio.revealPath(p)} />
      ) : (
        <div className="studio-grid">
          <section className="editor">
            <header className="editor-head">
              <div>
                <span className="eyebrow">Generator wideo</span>
                <h2>Wideo <small className="head-engine">LTX 2.3 · lokalnie</small></h2>
              </div>
            </header>

            <PresetBar moduleKey="video" value={v} exclude={['prompt', 'image_path', 'seed', 'seed_random', 'sound_prompt', 'spoken_text', 'mode']} onApply={(s) => set(s)} />

            <Field label="Tryb" hint="Text-to-video tworzy klip od zera. Image-to-video animuje wgrany obraz bazowy.">
              <PillGroup value={String(v.mode || 'Text to video')}
                options={[{ value: 'Text to video', label: 'Text → video' }, { value: 'Image to video', label: 'Image → video' }]}
                onChange={(mode) => set({ mode })} />
            </Field>

            {i2v && (
              <div className="base-image-row" onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; const p = f && window.dubcut?.getPathForFile?.(f); if (p) set({ image_path: p }) }}>
                {basePath ? (
                  <div className="base-image-chip">
                    <FileImage size={14} /> <span>{basePath.split('/').pop()}</span>
                    <button type="button" className="icon-x" onClick={() => set({ image_path: '' })}><X size={13} /></button>
                  </div>
                ) : <span className="settings-desc">Nie wybrano obrazu — wybierz albo przeciągnij plik tutaj.</span>}
                <button type="button" className="ghost-btn" onClick={pickBaseImage}><FileImage size={14} /> Wybierz obraz</button>
              </div>
            )}

            <Field label="Prompt" hint="Opisz scenę i ruch kamery. Możesz pisać po polsku — użyj „Tłumacz na EN” albo „Ulepsz prompt”.">
              <TextArea value={prompt} rows={6} placeholder="Np. cinematic close-up of a glass espresso cup on a desk, morning light, slow push-in camera, realistic motion" onChange={(v2) => set({ prompt: v2 })} />
            </Field>

            <div className="prompt-helpers">
              {helperRunning ? (
                <InlineJobProgress state={enhanceJob.state.status === 'running' ? enhanceJob.state : translateJob.state} label="Lokalna Gemma" onCancel={() => { enhanceJob.cancel(); translateJob.cancel() }} />
              ) : (
                <>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={enhance}><Wand2 size={14} /> Ulepsz prompt</button>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={translate}><Languages size={14} /> Tłumacz na EN</button>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={() => set({ prompt: '' })}><Eraser size={14} /> Wyczyść</button>
                </>
              )}
            </div>

            <Section icon={<Volume2 size={16} />} title="Audio" id="video-audio">
              <Toggle checked={audioEnabled} label="Generuj z audio" hint="LTX może wygenerować ścieżkę dźwiękową. Wyłącz, by dostać sam obraz (audio usuwane po renderze)." onChange={(val) => set({ audio_enabled: val })} />
              {audioEnabled && (
                <>
                  <Field label="Opis dźwięku" hint="Co ma być słychać w tle, np. quiet room tone, soft footsteps.">
                    <TextArea value={String(v.sound_prompt || '')} rows={2} placeholder="Np. quiet room tone, soft footsteps, subtle camera handling noise" onChange={(val) => set({ sound_prompt: val })} />
                  </Field>
                  <Field label="Co osoba ma powiedzieć" hint="Dokładny dialog w LTX bywa niepewny. Do precyzyjnej mowy lepszy będzie osobny moduł Tekst→Audio.">
                    <TextField value={String(v.spoken_text || '')} placeholder="Np. Welcome to the future of local video generation." onChange={(val) => set({ spoken_text: val })} />
                  </Field>
                </>
              )}
            </Section>

            {!generating && preflight && <p className="settings-desc warn preflight-note"><AlertTriangle size={13} /> {preflight}</p>}
            {generating
              ? <InlineJobProgress state={videoGenJob.state} label="Generowanie wideo" onCancel={videoGenJob.cancel} />
              : <button type="button" className="primary-btn tts-generate-btn" disabled={busy || !prompt.trim() || !engineAvailable} onClick={generate}>
                  <WandSparkles size={15} /> Generuj wideo
                </button>}
            <DoneTime state={videoGenJob.state} />

            {!engineAvailable && <p className="offline-note">Nie znaleziono silnika. Wskaż folder w <b>Ustawienia → Generator obrazów / wideo</b>.</p>}

            {items.length > 0 && (
              <div className="dub-result">
                {items.map((it) => (
                  <div key={it.file_name} className="music-track">
                    <div className="music-track-head"><span>{it.file_name}</span></div>
                    <video src={mediaUrl(it.url)} controls style={{ width: '100%', borderRadius: 10, background: '#000' }} />
                    <div className="dub-result-actions">
                      <a className="primary-btn short-action-btn" href={dlUrl(it.url)} download><Download size={15} /> Pobierz wideo</a>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {!online && <p className="offline-note">Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>.</p>}
          </section>

          <aside className="inspector accordions">
            <Section icon={<SlidersHorizontal size={16} />} title="Ustawienia renderu" id="video-summary">
              <div className="settings-meta">
                <div><dt>Rozmiar</dt><dd>{v.width} × {v.height}</dd></div>
                <div><dt>Klatki</dt><dd>{frames}</dd></div>
                <div><dt>FPS</dt><dd>{fps}</dd></div>
                <div><dt>Realny czas</dt><dd>{realSeconds} s</dd></div>
                <div><dt>Kroki</dt><dd>{v.steps}</dd></div>
                <div><dt>Seed</dt><dd>{seedRandom ? 'losowy' : String(v.seed)}</dd></div>
              </div>
              {heavy && <p className="settings-desc warn"><AlertTriangle size={13} /> 720p+ może być wolne i pamięciożerne na 24 GB RAM. Renderuj niżej i upscale’uj.</p>}
            </Section>

            <Section icon={<SlidersHorizontal size={16} />} title="Parametry wideo" id="video-params">
              <Field label="Model" hint="Q4 jest stabilny dla 24 GB RAM. Q8 daje lepszą jakość, ale używaj krótkich klipów i niskiej rozdzielczości.">
                <LabelSelect value={modelLabel} options={Object.keys(mm.models)} onChange={(label) => set({ model_label: label, model: mm.models[label] })} />
              </Field>
              <Field label="Rozdzielczość" hint="Na 24 GB RAM zacznij od 512×320 lub 704×480. Presety HD/Full HD są ciężkie.">
                <LabelSelect value={resLabel} options={Object.keys(mm.resolutions)} onChange={pickRes} />
              </Field>
              <Field label="Czas video" hint="LTX wymaga klatek 8k+1 — aplikacja dobiera najbliższą poprawną wartość.">
                <LabelSelect value={durLabel} options={Object.keys(mm.durations)} onChange={pickDur} />
              </Field>
              <Field label="FPS" hint="24 FPS jest najbezpieczniejsze. Niższy FPS = dłuższy klip przy mniejszej liczbie klatek.">
                <LabelSelect value={String(fps)} options={mm.fps.map(String)} onChange={(val) => set({ fps: Number(val) })} />
              </Field>
              <Slider label="Kroki" value={Number(v.steps ?? 8)} min={4} max={24} step={1} hint="Więcej kroków = lepsza jakość, ale wolniej. Dla Q4 zacznij od 8." onChange={(val) => set({ steps: val })} />
              <Toggle checked={seedRandom} label="Losowy seed" hint="Włączone: każdy render to nowy wariant. Wyłączone: stały seed odtwarza wariant." onChange={(val) => set({ seed_random: val })} />
              {!seedRandom && (
                <Field label="Seed" hint="Numer wariantu — ten sam seed + ustawienia ≈ ten sam klip.">
                  <TextField value={String(v.seed ?? 42)} placeholder="42" onChange={(val) => set({ seed: Number(val.replace(/[^0-9]/g, '') || 0) })} />
                </Field>
              )}
            </Section>
          </aside>
        </div>
      )}
    </div>
  )
}

function VideoGallery({ history, mediaUrl, dlUrl, onDelete, onCreate, onZoom, onReuse, onReveal }: {
  history: VideoHistoryItem[]; mediaUrl: (u: string) => string; dlUrl: (u: string) => string; onDelete: (f: string) => void; onCreate: () => void; onZoom: (src: string) => void; onReuse: (s: Record<string, unknown>) => void; onReveal: (p: string) => void
}) {
  return (
    <div className="music-gallery">
      <header className="editor-head">
        <div>
          <span className="eyebrow">Galeria wideo</span>
          <h2>Gotowe klipy <small className="head-engine">{history.length} {history.length === 1 ? 'klip' : 'plików'}</small></h2>
        </div>
        <button type="button" className="primary-btn" onClick={onCreate}><WandSparkles size={15} /> Nowy klip</button>
      </header>
      {history.length === 0 ? (
        <div className="music-gallery-empty">
          <Clapperboard size={34} />
          <p>Brak klipów. Wygenerowane wideo pojawią się tutaj — gotowe do odtworzenia i pobrania.</p>
          <button type="button" className="ghost-btn" onClick={onCreate}><WandSparkles size={15} /> Stwórz pierwszy klip</button>
        </div>
      ) : (
        <div className="media-gallery-grid">
          {history.map((item) => (
            <div key={item.file_name} className="media-card">
              <div className="media-card-thumb">
                <video src={mediaUrl(item.url)} controls preload="metadata" />
                <button type="button" className="media-zoom-btn" title="Powiększ" onClick={() => onZoom(mediaUrl(item.url))}><Maximize2 size={14} /></button>
              </div>
              <div className="media-card-body">
                <span className="media-card-title" title={item.prompt}>{item.prompt || item.file_name}</span>
                <small>{item.created_at} · {item.mode} · seed {item.seed}</small>
                <div className="media-card-actions">
                  <a className="primary-btn short-action-btn" href={dlUrl(item.url)} download><Download size={14} /> Pobierz</a>
                  {item.settings && Object.keys(item.settings).length > 0 && (
                    <button type="button" className="ghost-btn icon-btn" title="Wczytaj ustawienia tego klipu" onClick={() => onReuse(item.settings!)}><RotateCcw size={14} /></button>
                  )}
                  {item.path && (
                    <button type="button" className="ghost-btn icon-btn" title="Pokaż w Finderze" onClick={() => onReveal(item.path!)}><FolderOpen size={14} /></button>
                  )}
                  <button type="button" className="ghost-btn icon-btn danger" title="Usuń" onClick={() => onDelete(item.file_name)}><Trash2 size={14} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
