import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, WandSparkles, Download, Trash2, SlidersHorizontal, ImageIcon, ListMusic, Languages, Wand2, Eraser, AlertTriangle, FileImage, X, RotateCcw, FolderOpen, Clapperboard, Copy } from 'lucide-react'
import { api, getBase, type ImageHistoryItem, type MediaItem } from '../lib/api'
import type { useStudio } from '../lib/useStudio'
import type { ViewId } from '../App'
import { usePersistentState } from '../lib/usePersistentState'
import { InlineJobProgress } from '../components/JobProgress'
import { formatClock } from '../lib/jobFormat'
import { Lightbox } from '../components/Lightbox'
import { useJob } from '../lib/useJob'
import type { JobState } from '../lib/useJob'
import { Field, Section, TextField, TextArea, Toggle, Slider, PillGroup } from '../components/ui'
import { PresetBar } from '../components/PresetBar'

type Studio = ReturnType<typeof useStudio>

/** Persistent "done in M:SS" line so the render time stays visible after the bar is gone. */
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

export function ImageGenerator({ studio, onNavigate }: { studio: Studio; onNavigate: (v: ViewId) => void }) {
  const { config, meta, update, online, imageGenJob, chooseImage } = studio
  const animate = (path: string) => { update('video', { mode: 'Image to video', image_path: path }); onNavigate('video') }
  const applyAsBase = (path: string) => { update('image', { image_to_image: true, image_path: path }); setTab('generator') }
  const im = config.image
  const mm = meta.image
  const set = (patch: Record<string, unknown>) => update('image', patch)

  const [tab, setTab] = usePersistentState<'generator' | 'gallery'>('dubcut.image.tab', 'generator')
  const [status, setStatus] = useState<{ engine_available: boolean; uv_available: boolean } | null>(null)
  const [history, setHistory] = useState<ImageHistoryItem[]>([])
  const [lightbox, setLightbox] = useState<string | null>(null)
  const enhanceJob = useJob()
  const translateJob = useJob()

  const refreshStatus = useCallback(async () => { try { setStatus(await api.videogenStatus()) } catch { setStatus(null) } }, [])
  const refreshHistory = useCallback(async () => { try { setHistory(await api.imageHistory()) } catch { /* */ } }, [])
  useEffect(() => { if (!online) return; refreshStatus(); refreshHistory() }, [online, refreshStatus, refreshHistory])
  useEffect(() => { if (imageGenJob.state.status === 'done') refreshHistory() }, [imageGenJob.state.status, refreshHistory])

  // Apply enhancer / translation result back into the prompt when ready.
  useEffect(() => {
    if (enhanceJob.state.status === 'done') {
      const r = enhanceJob.state.result as { text?: string } | null
      if (r?.text) set({ prompt: r.text })
    }
  }, [enhanceJob.state.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (translateJob.state.status === 'done') {
      const r = translateJob.state.result as { text?: string } | null
      if (r?.text) set({ prompt: r.text })
    }
  }, [translateJob.state.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const generating = imageGenJob.state.status === 'running'
  const helperRunning = enhanceJob.state.status === 'running' || translateJob.state.status === 'running'
  const busy = generating || helperRunning
  const engineAvailable = status ? status.engine_available : true

  const model = String(im.model || mm.models[Object.keys(mm.models)[0]])
  const modelLabel = Object.keys(mm.models).find((k) => mm.models[k] === model) || Object.keys(mm.models)[0]
  const format = String(im.format || 'Square')
  const formatRes = mm.resolutions[format] || mm.resolutions.Square
  const resLabel = String(im.resolution_label || Object.keys(formatRes)[0])
  const prompt = String(im.prompt || '')
  const lowRam = Boolean(im.low_ram)
  const guidanceEnabled = Boolean(im.guidance_enabled)
  const i2i = Boolean(im.image_to_image)
  const basePath = String(im.image_path || '')

  const pickModel = (label: string) => set({ model: mm.models[label], model_label: label })
  const pickFormat = (f: string) => {
    const firstLabel = Object.keys(mm.resolutions[f])[0]
    const [w, h] = mm.resolutions[f][firstLabel]
    set({ format: f, resolution_label: firstLabel, width: w, height: h })
  }
  const pickRes = (label: string) => { const [w, h] = formatRes[label]; set({ resolution_label: label, width: w, height: h }) }

  const seedRandom = im.seed_random !== false
  const seedValue = () => seedRandom ? Math.floor(Math.random() * 1_000_000_000) : Number(im.seed || 42)

  const pickBaseImage = async () => {
    const f = await chooseImage()
    if (f?.path) set({ image_path: f.path })
  }

  const enhance = () => { if (!prompt.trim() || busy) return; enhanceJob.start(() => api.videogenEnhance(prompt.trim(), 'image')) }
  const translate = () => { if (!prompt.trim() || busy) return; translateJob.start(() => api.videogenTranslate(prompt.trim(), 'image')) }
  const clearPrompt = () => set({ prompt: '' })

  const generate = () => {
    if (busy || !prompt.trim()) return
    const settings = {
      ...im, model, width: im.width, height: im.height,
      style_suffix: mm.styles[String(im.style_label || 'Bez stylu')] || '',
      seed: seedValue(),
      image_path: i2i ? basePath : '',
      guidance: guidanceEnabled ? Number(im.guidance ?? 3.5) : null,
    }
    imageGenJob.start(() => api.imageGenerate(settings))
  }
  const deleteItem = async (file: string) => {
    if (!window.confirm(`Usunąć „${file}”?`)) return
    try { await api.deleteImageHistory(file) } catch { /* */ }
    refreshHistory()
  }

  const genResult = imageGenJob.state.result as { items?: MediaItem[] } | null
  const items = (imageGenJob.state.status === 'done' && genResult?.items) ? genResult.items : []
  const mediaUrl = (u: string) => `${getBase()}${u}`
  const dlUrl = (u: string) => mediaUrl(u) + (u.includes('?') ? '&' : '?') + 'dl=1'
  const heavy = Number(im.width) * Number(im.height) >= 1536 * 864
  const ram = studio.sysStats?.ram ?? null
  const preflight = heavy && ram != null && ram >= 75
    ? `Ciężki preset przy zajętości RAM ${ram}% — render może być wolny albo zabraknie pamięci. Rozważ mniejszy rozmiar lub Low RAM.`
    : ram != null && ram >= 90
      ? `RAM zajęty w ${ram}% — zamknij inne aplikacje przed renderem.`
      : ''

  return (
    <div className="music-view">
      <div className="music-tabbar">
        <button type="button" className={tab === 'generator' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('generator')}>
          <ImageIcon size={15} /> Generator
        </button>
        <button type="button" className={tab === 'gallery' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('gallery')}>
          <ListMusic size={15} /> Galeria {history.length > 0 && <em className="music-tab-badge">{history.length}</em>}
        </button>
        <span className="music-tabbar-spacer" />
        <span className={`status-pill ${engineAvailable ? 'ok' : ''}`}>{engineAvailable ? 'Silnik gotowy' : 'Brak silnika'}</span>
      </div>

      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}
      {tab === 'gallery' ? (
        <ImageGallery history={history} mediaUrl={mediaUrl} dlUrl={dlUrl} onDelete={deleteItem} onCreate={() => setTab('generator')} onZoom={setLightbox}
          onReuse={(s) => { set(s); setTab('generator') }} onReveal={(p) => studio.revealPath(p)} onAnimate={animate} onBase={applyAsBase} />
      ) : (
        <div className="studio-grid">
          <section className="editor">
            <header className="editor-head">
              <div>
                <span className="eyebrow">Generator obrazów</span>
                <h2>Obraz <small className="head-engine">FLUX · lokalnie</small></h2>
              </div>
            </header>

            <PresetBar moduleKey="image" value={im} exclude={['prompt', 'negative_prompt', 'image_path', 'seed', 'seed_random', 'image_to_image', 'style_suffix']} onApply={(s) => set(s)} />

            <div className="mediagen-toggle">
              <Toggle checked={i2i} label="Image-to-image (wariacja z obrazu)" hint="Generuj na bazie wgranego obrazu zamiast od zera." onChange={(v) => set({ image_to_image: v })} />
            </div>
            {i2i && (
              <div className="base-image-row" onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; const p = f && window.dubcut?.getPathForFile?.(f); if (p) set({ image_path: p }) }}>
                {basePath ? (
                  <div className="base-image-chip">
                    <FileImage size={14} /> <span>{basePath.split('/').pop()}</span>
                    <button type="button" className="icon-x" onClick={() => set({ image_path: '' })}><X size={13} /></button>
                  </div>
                ) : <span className="settings-desc">Nie wybrano obrazu — wybierz albo przeciągnij plik tutaj.</span>}
                <button type="button" className="ghost-btn" onClick={pickBaseImage}><FileImage size={14} /> Wybierz obraz</button>
                <div className="base-strength">
                  <Slider label="Trzymanie obrazu" value={Number(im.image_strength ?? 0.6)} min={0.1} max={0.95} step={0.05}
                    hint="Wyżej: mocniej trzyma obraz bazowy. Niżej: prompt zmienia więcej." onChange={(v) => set({ image_strength: v })} />
                </div>
              </div>
            )}

            <Field label="Styl" hint="Gotowy dopisek do prompta (np. cinematic photo). Nie zmienia modelu, tylko opis wizualny.">
              <LabelSelect value={String(im.style_label || 'Bez stylu')} options={Object.keys(mm.styles)} onChange={(v) => set({ style_label: v })} />
            </Field>

            <Field label="Prompt" hint="Możesz pisać po polsku — użyj „Tłumacz na EN” albo „Ulepsz prompt”, by lokalna Gemma przygotowała opis.">
              <TextArea value={prompt} rows={6} placeholder="Np. portret kobiety w deszczu, neonowe światła miasta, cinematic, 85mm, realistyczne detale" onChange={(v) => set({ prompt: v })} />
            </Field>

            <div className="prompt-helpers">
              {helperRunning ? (
                <InlineJobProgress state={enhanceJob.state.status === 'running' ? enhanceJob.state : translateJob.state} label="Lokalna Gemma" onCancel={() => { enhanceJob.cancel(); translateJob.cancel() }} />
              ) : (
                <>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={enhance}><Wand2 size={14} /> Ulepsz prompt</button>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={translate}><Languages size={14} /> Tłumacz na EN</button>
                  <button type="button" className="ghost-btn" disabled={!prompt.trim() || busy} onClick={clearPrompt}><Eraser size={14} /> Wyczyść</button>
                </>
              )}
            </div>

            <Field label="Negative prompt" hint="Czego model ma unikać. Najczęściej: blurry, low quality, watermark, text, bad anatomy.">
              <TextArea value={String(im.negative_prompt || '')} rows={2} placeholder="Np. blurry, low quality, bad anatomy, watermark, text" onChange={(v) => set({ negative_prompt: v })} />
            </Field>

            {!generating && preflight && <p className="settings-desc warn preflight-note"><AlertTriangle size={13} /> {preflight}</p>}
            {generating
              ? <InlineJobProgress state={imageGenJob.state} label="Generowanie obrazu" onCancel={imageGenJob.cancel} />
              : <button type="button" className="primary-btn tts-generate-btn" disabled={busy || !prompt.trim() || !engineAvailable} onClick={generate}>
                  <WandSparkles size={15} /> Generuj obraz
                </button>}
            <DoneTime state={imageGenJob.state} />

            {!engineAvailable && <p className="offline-note">Nie znaleziono silnika. Wskaż folder w <b>Ustawienia → Generator obrazów / wideo</b>.</p>}

            {items.length > 0 && (
              <div className={items.length > 1 ? 'mediagen-results multi' : 'mediagen-results'}>
                {items.map((it) => (
                  <div key={it.file_name} className="mediagen-result-card">
                    <button type="button" className="mediagen-result-img" onClick={() => setLightbox(mediaUrl(it.url))} title="Kliknij, by powiększyć">
                      <img src={mediaUrl(it.url)} alt={it.file_name} />
                    </button>
                    <div className="card-action-row">
                      <a className="primary-btn short-action-btn" href={dlUrl(it.url)} download><Download size={15} /> Pobierz</a>
                      {it.path && <button type="button" className="ghost-btn" title="Animuj ten obraz (wideo)" onClick={() => animate(it.path!)}><Clapperboard size={14} /> Animuj</button>}
                      {it.path && <button type="button" className="ghost-btn icon-btn" title="Użyj jako obraz bazowy (image-to-image)" onClick={() => applyAsBase(it.path!)}><Copy size={14} /></button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {!online && <p className="offline-note">Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>.</p>}
          </section>

          <aside className="inspector accordions">
            <Section icon={<SlidersHorizontal size={16} />} title="Ustawienia renderu" id="image-summary">
              <div className="settings-meta">
                <div><dt>Rozmiar</dt><dd>{im.width} × {im.height}</dd></div>
                <div><dt>Kroki</dt><dd>{im.steps}</dd></div>
                <div><dt>Warianty</dt><dd>{im.batch_count}</dd></div>
                <div><dt>Seed</dt><dd>{seedRandom ? 'losowy' : String(im.seed)}</dd></div>
                <div><dt>Model</dt><dd className="mono">{model}</dd></div>
                <div><dt>Low RAM</dt><dd>{lowRam ? 'tak' : 'nie'}</dd></div>
              </div>
              {heavy && <p className="settings-desc warn"><AlertTriangle size={13} /> Ciężki preset — pierwsze generowanie może długo trwać i mocno użyć pamięci.</p>}
            </Section>

            <Section icon={<SlidersHorizontal size={16} />} title="Parametry obrazu" id="image-params">
              <Field label="Model" hint="Publiczne warianty MFLUX Q4 nie wymagają logowania do Hugging Face. Z-Image Turbo to dobry szybki quality.">
                <LabelSelect value={modelLabel} options={Object.keys(mm.models)} onChange={pickModel} />
              </Field>
              <Field label="Format" hint="Square = kwadrat, Wide = poziomy, Vertical = pionowy.">
                <PillGroup value={format} options={Object.keys(mm.resolutions).map((f) => ({ value: f, label: f }))} onChange={pickFormat} />
              </Field>
              <Field label="Rozdzielczość" hint="Większa = więcej detalu, ale wolniej i więcej pamięci. Testuj 512/768/1024.">
                <LabelSelect value={resLabel} options={Object.keys(formatRes)} onChange={pickRes} />
              </Field>
              <Slider label="Kroki" value={Number(im.steps ?? 4)} min={1} max={40} step={1} hint="Schnell zwykle 4. Dev zwykle 16–30." onChange={(v) => set({ steps: v })} />
              <Toggle checked={guidanceEnabled} label="Guidance" hint="Mocniej trzyma prompt. Dla Schnell zwykle zostaw wyłączone." onChange={(v) => set({ guidance_enabled: v })} />
              {guidanceEnabled && (
                <Slider label="Guidance scale" value={Number(im.guidance ?? 3.5)} min={1} max={8} step={0.1} hint="3–4 to bezpieczny start." onChange={(v) => set({ guidance: v })} />
              )}
              <Toggle checked={lowRam} label="Low RAM" hint="Bezpieczniejsze na 24 GB RAM, zwykle kosztem prędkości." onChange={(v) => set({ low_ram: v })} />
              {!lowRam && (
                <Slider label="MLX cache GB" value={Number(im.mlx_cache_limit_gb ?? 12)} min={4} max={24} step={1} hint="Większy cache może przyspieszyć, ale ryzykuje brak pamięci." onChange={(v) => set({ mlx_cache_limit_gb: v })} />
              )}
              <Slider label="Warianty" value={Number(im.batch_count ?? 1)} min={1} max={4} step={1} hint="Ile obrazów naraz (kolejne seedy). 4 = ~4× dłużej." onChange={(v) => set({ batch_count: v })} />
              <Toggle checked={seedRandom} label="Losowy seed" hint="Włączone: każdy render to nowy wariant. Wyłączone: stały seed odtwarza podobny wynik." onChange={(v) => set({ seed_random: v })} />
              {!seedRandom && (
                <Field label="Seed" hint="Numer wariantu — ten sam seed + ustawienia ≈ ten sam obraz.">
                  <TextField value={String(im.seed ?? 42)} placeholder="42" onChange={(v) => set({ seed: Number(v.replace(/[^0-9]/g, '') || 0) })} />
                </Field>
              )}
            </Section>
          </aside>
        </div>
      )}
    </div>
  )
}

function ImageGallery({ history, mediaUrl, dlUrl, onDelete, onCreate, onZoom, onReuse, onReveal, onAnimate, onBase }: {
  history: ImageHistoryItem[]; mediaUrl: (u: string) => string; dlUrl: (u: string) => string; onDelete: (f: string) => void; onCreate: () => void; onZoom: (src: string) => void; onReuse: (s: Record<string, unknown>) => void; onReveal: (p: string) => void; onAnimate: (p: string) => void; onBase: (p: string) => void
}) {
  return (
    <div className="music-gallery">
      <header className="editor-head">
        <div>
          <span className="eyebrow">Galeria obrazów</span>
          <h2>Gotowe obrazy <small className="head-engine">{history.length} {history.length === 1 ? 'obraz' : 'plików'}</small></h2>
        </div>
        <button type="button" className="primary-btn" onClick={onCreate}><WandSparkles size={15} /> Nowy obraz</button>
      </header>
      {history.length === 0 ? (
        <div className="music-gallery-empty">
          <ImageIcon size={34} />
          <p>Brak obrazów. Wygenerowane grafiki pojawią się tutaj — gotowe do podglądu i pobrania.</p>
          <button type="button" className="ghost-btn" onClick={onCreate}><WandSparkles size={15} /> Stwórz pierwszy obraz</button>
        </div>
      ) : (
        <div className="media-gallery-grid">
          {history.map((item) => (
            <div key={item.file_name} className="media-card">
              <button type="button" className="media-card-thumb" onClick={() => onZoom(mediaUrl(item.url))} title="Kliknij, by powiększyć">
                <img src={mediaUrl(item.url)} alt={item.file_name} loading="lazy" />
              </button>
              <div className="media-card-body">
                <span className="media-card-title" title={item.prompt}>{item.prompt || item.file_name}</span>
                <small>{item.created_at} · seed {item.seed}</small>
                <div className="media-card-actions">
                  <a className="primary-btn short-action-btn" href={dlUrl(item.url)} download><Download size={14} /> Pobierz</a>
                  {item.path && <button type="button" className="ghost-btn icon-btn" title="Animuj (wideo)" onClick={() => onAnimate(item.path!)}><Clapperboard size={14} /></button>}
                  {item.path && <button type="button" className="ghost-btn icon-btn" title="Użyj jako obraz bazowy (i2i)" onClick={() => onBase(item.path!)}><Copy size={14} /></button>}
                  {item.settings && Object.keys(item.settings).length > 0 && (
                    <button type="button" className="ghost-btn icon-btn" title="Wczytaj ustawienia tego obrazu" onClick={() => onReuse(item.settings!)}><RotateCcw size={14} /></button>
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
