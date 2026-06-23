import { useCallback, useEffect, useState } from 'react'
import { AudioLines, Languages, ArrowRight, WandSparkles, Download, Trash2, FolderOpen, ListMusic } from 'lucide-react'
import { api, getBase, type TtsHistoryItem } from '../lib/api'
import { useJob } from '../lib/useJob'
import type { useStudio } from '../lib/useStudio'
import { InlineJobProgress } from '../components/JobProgress'
import { VoiceManager } from '../components/VoiceManager'
import { VoiceSamplePreview, OmniVoiceParams, ttsEngineOf } from '../components/VoiceSynth'
import { Field, Section, Select, TextArea, PillGroup } from '../components/ui'
import { usePersistentState } from '../lib/usePersistentState'

type Studio = ReturnType<typeof useStudio>

export function TextToAudio({ studio }: { studio: Studio }) {
  const { config, meta, update, refresh, online } = studio
  const d = config.dub
  const set = (patch: Record<string, unknown>) => update('dub', patch)
  // The voice engine is a global setting; the synthesis panel adapts to it so the
  // controls always match the active model (Qwen presets vs OmniVoice knobs).
  const engine = ttsEngineOf(config)
  const isOmni = engine === 'omnivoice'
  const builtInLabel = isOmni ? 'Głos wbudowany (OmniVoice)' : 'Głos presetowy (Qwen)'
  const ttsVoiceSources = [builtInLabel, 'Sklonowany głos (własna próbka)']

  const [tab, setTab] = usePersistentState<'generator' | 'gallery'>('dubcut.tts.tab', 'generator')
  const [srcText, setSrcText] = usePersistentState('dubcut.tts.srcText', '')
  const [finalText, setFinalText] = usePersistentState('dubcut.tts.finalText', '')
  const [lang, setLang] = useState<string>(() => {
    try { return localStorage.getItem('dubcut.tts.lang') || 'Angielski' } catch { return 'Angielski' }
  })
  const setAudioLang = (v: string) => { setLang(v); try { localStorage.setItem('dubcut.tts.lang', v) } catch { /* */ } }

  const translateJob = useJob('tts.translate')
  const genJob = useJob('tts.generate')
  const anyRunning = translateJob.state.status === 'running' || genJob.state.status === 'running'
  const [history, setHistory] = useState<TtsHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [deletingId, setDeletingId] = useState('')

  const rawSource = String(d.voice_source ?? builtInLabel)
  const isOwnSample = rawSource === 'Sklonowany głos (własna próbka)'
  // Any non-clone source maps to the active engine's built-in option (Qwen preset or
  // OmniVoice built-in voice), so switching engines never leaves a stale label.
  const voiceSource = isOwnSample ? rawSource : builtInLabel
  const selectedVoicePath = meta.voices.find((v) => v.id === d.selected_voice_id)?.path
  const voiceLabel = (id: string) => meta.voices.find((v) => v.id === id)?.label ?? id
  const pickVoice = (label: string) => { const m = meta.voices.find((v) => v.label === label); if (m) set({ selected_voice_id: m.id }) }
  // Backend already returns languages the active TTS + translation engines support,
  // most-popular first — use it as-is so the list adapts to the chosen engines.
  const langOptions = meta.dub.target_languages

  const ttsSettings = () => ({
    language: lang,
    target_lang: lang,
    translation_model: d.translation_model,
    voice_source: voiceSource,
    dubbing_qwen_speaker: d.dubbing_qwen_speaker,
    selected_voice_id: d.selected_voice_id,
    voiceover_style: d.voiceover_style,
    tts_model: d.tts_model,
    tts_engine: engine,
    omnivoice_num_step: d.omnivoice_num_step ?? 32,
    omnivoice_guidance_scale: d.omnivoice_guidance_scale ?? 2.0,
    omnivoice_speed: d.omnivoice_speed ?? 1.0,
    omnivoice_class_temperature: d.omnivoice_class_temperature ?? 0.0,
    omnivoice_gender: d.omnivoice_gender ?? '',
    omnivoice_age: d.omnivoice_age ?? '',
    omnivoice_pitch: d.omnivoice_pitch ?? '',
    omnivoice_whisper: d.omnivoice_whisper ?? false,
  })

  const translate = () => {
    if (!srcText.trim() || anyRunning) return
    translateJob.start(() => api.ttsTranslate({ text: srcText, target_lang: lang, settings: ttsSettings() }))
  }
  const useRaw = () => setFinalText(srcText)
  const generate = () => {
    const text = (finalText || srcText).trim()
    if (!text || anyRunning) return
    genJob.start(() => api.ttsGenerate({ text, settings: ttsSettings() }))
  }

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      setHistory(await api.ttsHistory())
    } catch {
      setHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const deleteHistory = async (id: string) => {
    if (!window.confirm('Usunąć tę generację audio z dysku? Pliki MP3/WAV zostaną skasowane.')) return
    setDeletingId(id)
    try {
      await api.deleteTtsHistory(id)
      await loadHistory()
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      window.alert(`Nie udało się usunąć tego audio z dysku.\n\n${message}`)
    } finally {
      setDeletingId('')
    }
  }

  useEffect(() => {
    if (translateJob.state.status === 'done') {
      const r = translateJob.state.result as { text?: string } | null
      if (r?.text) setFinalText(r.text)
    }
  }, [translateJob.state.status, translateJob.state.result])

  useEffect(() => { loadHistory() }, [loadHistory])
  useEffect(() => {
    if (genJob.state.status === 'done') loadHistory()
  }, [genJob.state.status, loadHistory])

  const genResult = genJob.state.result as { url?: string; mp3_url?: string; wav_url?: string; language?: string } | null
  const mp3Url = (genJob.state.status === 'done' && (genResult?.mp3_url || genResult?.url)) ? `${getBase()}${genResult?.mp3_url || genResult?.url}` : ''
  const wavUrl = (genJob.state.status === 'done' && genResult?.wav_url) ? `${getBase()}${genResult.wav_url}` : ''
  const dlUrl = (u: string) => u + (u.includes('?') ? '&' : '?') + 'dl=1'
  const audioUrl = mp3Url || wavUrl
  const absUrl = (u?: string) => (u ? `${getBase()}${u}` : '')

  return (
    <div className="music-view">
      <div className="music-tabbar">
        <button type="button" className={tab === 'generator' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('generator')}>
          <AudioLines size={15} /> Generator
        </button>
        <button type="button" className={tab === 'gallery' ? 'music-tab active' : 'music-tab'} onClick={() => setTab('gallery')}>
          <ListMusic size={15} /> Galeria {history.length > 0 && <em className="music-tab-badge">{history.length}</em>}
        </button>
        <span className="music-tabbar-spacer" />
        <span className={`status-pill ${online ? 'ok' : ''}`}>{online ? 'Gotowy' : 'Backend offline'}</span>
      </div>

      {tab === 'gallery' ? (
        <TtsGallery
          history={history}
          loading={historyLoading}
          generating={genJob.state.status === 'running'}
          genPct={Math.round((genJob.state.progress ?? 0) * 100)}
          deletingId={deletingId}
          audioUrl={absUrl}
          dlUrl={(u) => dlUrl(absUrl(u))}
          onDelete={deleteHistory}
          onCreate={() => setTab('generator')}
          onReveal={(p) => studio.revealPath(p)}
        />
      ) : (
        <div className="studio-grid">
          <section className="editor">
            <header className="editor-head">
              <div>
                <span className="eyebrow">Generator mowy</span>
                <h2>Tekst → Audio</h2>
              </div>
            </header>

            <Field label="Tekst źródłowy" hint="Wpisz tekst do syntezy mowy. Możesz go najpierw przetłumaczyć albo użyć bez tłumaczenia. Pole możesz rozciągnąć myszką (dolny róg) — rozmiar zostanie zapamiętany.">
              <TextArea value={srcText} rows={16} resizeKey="tts.src" placeholder="Wpisz tekst, np. „I love you” albo polski tekst do tłumaczenia…" onChange={setSrcText} />
              {srcText.trim() && (
                <div className="ta-actions">
                  <button type="button" className="ghost-btn icon-btn danger" title="Wyczyść tekst" onClick={() => setSrcText('')}>
                    <Trash2 size={14} /> Wyczyść
                  </button>
                </div>
              )}
            </Field>

            <div className="tts-translate-row">
              {translateJob.state.status === 'running'
                ? <InlineJobProgress state={translateJob.state} label="Tłumaczenie" onCancel={translateJob.cancel} />
                : <>
                    <div className="lang-pick">
                      <button type="button" className="primary-btn" disabled={!srcText.trim() || anyRunning} onClick={translate}>
                        <Languages size={15} /> Przetłumacz
                      </button>
                      <Select value={lang} options={langOptions} onChange={setAudioLang} />
                    </div>
                    <button type="button" className="ghost-btn" disabled={!srcText.trim() || anyRunning} onClick={useRaw}>
                      <ArrowRight size={15} /> Użyj bez tłumaczenia
                    </button>
                  </>}
            </div>

            <Field label={`Tekst do wygenerowania audio (${lang})`} hint="Tekst, który wypowie głos. Możesz go jeszcze dopracować przed generowaniem.">
              <TextArea value={finalText} rows={12} resizeKey="tts.final" placeholder="Tutaj pojawi się tekst po tłumaczeniu albo tekst użyty bez tłumaczenia." onChange={setFinalText} />
              {finalText.trim() && (
                <div className="ta-actions">
                  <button type="button" className="ghost-btn icon-btn danger" title="Wyczyść tekst" onClick={() => setFinalText('')}>
                    <Trash2 size={14} /> Wyczyść
                  </button>
                </div>
              )}
            </Field>

            {genJob.state.status === 'running'
              ? <InlineJobProgress state={genJob.state} label={`Synteza mowy (${isOmni ? 'OmniVoice' : 'Qwen TTS'})`} onCancel={genJob.cancel} />
              : <button type="button" className="primary-btn tts-generate-btn" disabled={!(finalText || srcText).trim() || anyRunning} onClick={generate}>
                  <WandSparkles size={15} /> Generuj audio z tekstu
                </button>}

            {audioUrl && (
              <div className="dub-result">
                <audio src={audioUrl} controls style={{ width: '100%', marginTop: 12 }} />
                <div className="dub-result-actions">
                  {mp3Url && <a className="primary-btn short-action-btn" href={dlUrl(mp3Url)} download><Download size={15} /> Pobierz MP3</a>}
                  {wavUrl && <a className="ghost-btn short-action-btn" href={dlUrl(wavUrl)} download><Download size={15} /> Pobierz WAV</a>}
                </div>
              </div>
            )}

            {!online && (
              <p className="offline-note">Backend nie jest jeszcze uruchomiony — przejdź do <b>Ustawienia → Zainstaluj</b>.</p>
            )}
          </section>

          <aside className="inspector accordions">
            <Section icon={<AudioLines size={16} />} title="Głos i synteza">
              <p className="settings-desc" style={{ marginTop: 0 }}>
                Aktywny silnik: <strong>{isOmni ? 'OmniVoice' : 'Qwen TTS'}</strong>. Zmienisz go w
                Ustawieniach → „Silnik głosu (TTS)”.
              </p>
              <Field label="Źródło głosu" hint={isOmni ? '„Głos wbudowany” — naturalny głos modelu (możesz nim sterować polem „Styl głosu”). „Własna próbka” — klonuje Twoje nagranie głosu.' : '„Głos presetowy” — gotowy syntetyczny głos modelu. „Własna próbka” — klonuje Twoje nagranie głosu.'}>
                <Select value={voiceSource} options={ttsVoiceSources} onChange={(v) => set({ voice_source: v })} />
              </Field>
              {isOwnSample ? (
                <Field label="Próbka głosu" hint="Wybierz zapisaną próbkę albo wgraj własne nagranie (8–20 s czystej mowy). Użyj ▶, aby odsłuchać wybraną próbkę.">
                  <div className="voice-pick-row">
                    <Select value={voiceLabel(d.selected_voice_id || meta.voices[0]?.id || '')} options={meta.voices.map((v) => v.label)} onChange={pickVoice} />
                    <VoiceSamplePreview path={selectedVoicePath} />
                  </div>
                  <VoiceManager voices={meta.voices} selectedPath={selectedVoicePath} onChanged={refresh} onDeletedSelected={() => set({ selected_voice_id: '' })} />
                </Field>
              ) : isOmni ? (
                <p className="settings-desc">OmniVoice użyje swojego naturalnego głosu wbudowanego. Aby nadać mu charakter, opisz go w polu „Styl głosu” poniżej.</p>
              ) : (
                <Field label="Głos presetowy (Qwen)" hint="Gotowy głos modelu — różne barwy. Nic nie nagrywasz.">
                  <Select value={d.dubbing_qwen_speaker} options={meta.dub.speakers} onChange={(v) => set({ dubbing_qwen_speaker: v })} />
                </Field>
              )}
              {isOmni ? (
                <OmniVoiceParams d={d as Record<string, unknown>} set={set} />
              ) : (
                <Field label="Model TTS" hint="„1.7B” — wyższa jakość. „0.6B” — szybszy, lżejszy.">
                  <PillGroup value={d.tts_model} options={meta.dub.tts_models.map((m) => ({ value: m, label: m.split(' ')[0] }))} onChange={(v) => set({ tts_model: v })} />
                </Field>
              )}
              {!isOmni && (
                <Field label="Styl głosu (opcjonalnie)" hint="Krótka instrukcja brzmienia, np. „spokojny, ciepły”. Zostaw puste dla neutralnego.">
                  <TextArea value={d.voiceover_style ?? ''} rows={2} placeholder="np. spokojny, ciepły lektor" onChange={(v) => set({ voiceover_style: v })} />
                </Field>
              )}
            </Section>
          </aside>
        </div>
      )}
    </div>
  )
}

function TtsGallery({
  history, loading, generating = false, genPct = 0, deletingId, audioUrl, dlUrl, onDelete, onCreate, onReveal,
}: {
  history: TtsHistoryItem[]
  loading: boolean
  generating?: boolean
  genPct?: number
  deletingId: string
  audioUrl: (u?: string) => string
  dlUrl: (u: string) => string
  onDelete: (id: string) => void
  onCreate: () => void
  onReveal: (path: string) => void
}) {
  return (
    <div className="music-gallery">
      <header className="editor-head">
        <div>
          <span className="eyebrow">Galeria audio</span>
          <h2>Gotowe nagrania <small className="head-engine">{history.length} {history.length === 1 ? 'plik' : 'plików'}</small></h2>
        </div>
        <button type="button" className="primary-btn" onClick={onCreate}>
          <WandSparkles size={15} /> Nowe audio
        </button>
      </header>

      {history.length === 0 && !generating ? (
        <div className="music-gallery-empty">
          <AudioLines size={34} />
          <p>{loading ? 'Wczytuję zapisane nagrania...' : 'Brak zapisanych nagrań. Wygenerowane audio pojawi się tutaj do odsłuchu, pobrania i kasowania.'}</p>
          <button type="button" className="ghost-btn" onClick={onCreate}><WandSparkles size={15} /> Stwórz pierwsze audio</button>
        </div>
      ) : (
        <div className="music-gallery-grid tts-gallery-grid">
          {generating && (
            <div className="music-card tts-audio-card tts-pending-card">
              <div className="music-card-head">
                <span className="music-card-icon spin"><AudioLines size={16} /></span>
                <div className="music-card-meta">
                  <span className="music-card-title">Generowanie audio… {genPct}%</span>
                  <small>Plik pojawi się tutaj automatycznie po zakończeniu.</small>
                </div>
              </div>
              <div className="tts-pending-bar"><span style={{ width: `${genPct}%` }} /></div>
            </div>
          )}
          {history.map((item, idx) => {
            const playUrl = audioUrl(item.mp3_url || item.wav_url)
            return (
              <div key={`${item.id || item.path || item.title}-${idx}`} className="music-card tts-audio-card">
                <div className="music-card-head">
                  <span className="music-card-icon"><AudioLines size={16} /></span>
                  <div className="music-card-meta">
                    <span className="music-card-title" title={item.title}>{item.title}</span>
                    <small>{item.created_at}{item.language ? ` · ${item.language}` : ''}</small>
                  </div>
                  <button type="button" className="ghost-btn icon-btn danger" disabled={deletingId === item.id} title="Usuń z dysku" onClick={() => onDelete(item.id)}>
                    <Trash2 size={14} />
                  </button>
                </div>
                {item.text && <p className="music-card-prompt tts-card-text" title={item.text}>{item.text}</p>}
                {playUrl && <audio src={playUrl} controls />}
                <div className="card-action-row">
                  {item.mp3_url && <a className="primary-btn short-action-btn" href={dlUrl(item.mp3_url)} download><Download size={15} /> MP3</a>}
                  {item.wav_url && <a className="ghost-btn short-action-btn" href={dlUrl(item.wav_url)} download><Download size={15} /> WAV</a>}
                  {item.path && (
                    <button type="button" className="ghost-btn icon-btn" title="Pokaż w Finderze" onClick={() => onReveal(item.path!)}><FolderOpen size={14} /></button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
