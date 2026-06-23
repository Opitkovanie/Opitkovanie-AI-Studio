import { useEffect, useRef, useState } from 'react'
import { Play, Pause } from 'lucide-react'
import { api } from '../lib/api'
import { Slider, Select, Field, Toggle } from './ui'

/** Small play/stop button to audition a single voice sample by its file path.
 *  Used right next to the voice-sample picker so the user can preview the chosen
 *  sample without opening the full "Zarządzaj próbkami" panel. */
export function VoiceSamplePreview({ path, title }: { path?: string; title?: string }) {
  const [playing, setPlaying] = useState(false)
  const ref = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    return () => { ref.current?.pause(); ref.current = null }
  }, [])
  // Stop playback if the selected sample changes underneath us.
  useEffect(() => { ref.current?.pause(); setPlaying(false) }, [path])

  const toggle = () => {
    if (!path) return
    if (!ref.current) ref.current = new Audio()
    const a = ref.current
    if (playing) { a.pause(); setPlaying(false); return }
    a.src = api.voiceSampleAudioUrl(path)
    a.onended = () => setPlaying(false)
    a.play().then(() => setPlaying(true)).catch(() => setPlaying(false))
  }

  return (
    <button
      type="button"
      className={`ghost-btn icon-btn voice-preview-btn ${playing ? 'is-on' : ''}`}
      disabled={!path}
      title={title ?? (playing ? 'Zatrzymaj' : 'Odsłuchaj próbkę')}
      onClick={toggle}
    >
      {playing ? <Pause size={15} /> : <Play size={15} />}
    </button>
  )
}

// OmniVoice voice-attribute tags it actually understands (a fixed vocabulary — free
// text is NOT supported and would error). Polish label ↔ OmniVoice token.
const OV_GENDER = [
  { label: 'Automatyczna', token: '' },
  { label: 'Męski', token: 'male' },
  { label: 'Żeński', token: 'female' },
]
const OV_AGE = [
  { label: 'Automatyczny', token: '' },
  { label: 'Dziecko', token: 'child' },
  { label: 'Nastolatek', token: 'teenager' },
  { label: 'Młody dorosły', token: 'young adult' },
  { label: 'Średni wiek', token: 'middle-aged' },
  { label: 'Starszy', token: 'elderly' },
]
const OV_PITCH = [
  { label: 'Automatyczna', token: '' },
  { label: 'Bardzo niska', token: 'very low pitch' },
  { label: 'Niska', token: 'low pitch' },
  { label: 'Średnia', token: 'moderate pitch' },
  { label: 'Wysoka', token: 'high pitch' },
  { label: 'Bardzo wysoka', token: 'very high pitch' },
]
const labelForToken = (opts: { label: string; token: string }[], token: unknown) =>
  opts.find((o) => o.token === String(token ?? ''))?.label ?? opts[0].label
const tokenForLabel = (opts: { label: string; token: string }[], label: string) =>
  opts.find((o) => o.label === label)?.token ?? ''

/** OmniVoice-specific synthesis controls (shown when the OmniVoice engine is active),
 *  shared by every module so dubbing, Shorts and Text→Audio stay identical. */
export function OmniVoiceParams({ d, set, showSpeed = true }: {
  d: Record<string, unknown>
  set: (patch: Record<string, unknown>) => void
  /** Hide the tempo slider in modules that already have min/max sync-tempo (dubbing). */
  showSpeed?: boolean
}) {
  const numStep = Number(d.omnivoice_num_step ?? 32)
  const guidance = Number(d.omnivoice_guidance_scale ?? 2.0)
  const speed = Number(d.omnivoice_speed ?? 1.0)
  const expr = Number(d.omnivoice_class_temperature ?? 0.0)
  return (
    <>
      <Slider
        label="Jakość (liczba kroków)"
        value={numStep}
        min={8}
        max={64}
        step={1}
        hint="Ile kroków dekodowania wykonuje model. Więcej = wyższa jakość i stabilność, ale wolniej. Domyślnie 32. Do szybkich podglądów spróbuj 16–24."
        onChange={(v) => set({ omnivoice_num_step: v })}
      />
      <Slider
        label="Wierność głosu"
        value={guidance}
        min={1}
        max={4}
        step={0.1}
        suffix="×"
        hint="Jak mocno model trzyma się głosu odniesienia i wyrazistej dykcji. Wyżej = wierniej i czyściej, niżej = więcej naturalnej swobody. Zalecane 2.0."
        onChange={(v) => set({ omnivoice_guidance_scale: v })}
      />
      {showSpeed && (
        <Slider
          label="Tempo mowy"
          value={speed}
          min={0.5}
          max={1.8}
          step={0.05}
          suffix="×"
          hint="Tempo wypowiedzi. 1.0 = naturalne, mniej = wolniej, więcej = szybciej."
          onChange={(v) => set({ omnivoice_speed: v })}
        />
      )}
      <Slider
        label="Ekspresja / emocje"
        value={expr}
        min={0}
        max={1}
        step={0.05}
        hint="Im wyżej, tym żywsza i bardziej emocjonalna intonacja (model losuje więcej wariacji). 0 = najbardziej stabilnie, ale płasko/bez emocji. Spróbuj 0.4–0.7 dla naturalnego, żywego brzmienia. Za wysoko może brzmieć niestabilnie."
        onChange={(v) => set({ omnivoice_class_temperature: v })}
      />
      <Field label="Płeć głosu" hint="Wymusza barwę głosu. „Automatyczna” — model decyduje sam (przy klonowaniu zwykle zostaw Automatyczną — barwa pochodzi z próbki).">
        <Select value={labelForToken(OV_GENDER, d.omnivoice_gender)} options={OV_GENDER.map((o) => o.label)} onChange={(l) => set({ omnivoice_gender: tokenForLabel(OV_GENDER, l) })} />
      </Field>
      <Field label="Wiek głosu" hint="Sugerowany wiek mówcy. „Automatyczny” = bez wymuszania.">
        <Select value={labelForToken(OV_AGE, d.omnivoice_age)} options={OV_AGE.map((o) => o.label)} onChange={(l) => set({ omnivoice_age: tokenForLabel(OV_AGE, l) })} />
      </Field>
      <Field label="Wysokość głosu" hint="Ogólny rejestr głosu (od bardzo niskiego do bardzo wysokiego). „Automatyczna” = bez wymuszania.">
        <Select value={labelForToken(OV_PITCH, d.omnivoice_pitch)} options={OV_PITCH.map((o) => o.label)} onChange={(l) => set({ omnivoice_pitch: tokenForLabel(OV_PITCH, l) })} />
      </Field>
      <Toggle checked={!!d.omnivoice_whisper} label="Szept" hint="Wymusza mówienie szeptem." onChange={(v) => set({ omnivoice_whisper: v })} />
    </>
  )
}

/** Reads the globally-selected voice engine from the app config. */
export function ttsEngineOf(config: { app?: Record<string, unknown> } | undefined): 'qwen' | 'omnivoice' {
  return String(config?.app?.tts_engine ?? 'qwen').toLowerCase() === 'omnivoice' ? 'omnivoice' : 'qwen'
}
