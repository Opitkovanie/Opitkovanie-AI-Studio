import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Boxes, Mic2, AudioLines, Captions, Scissors, Music4, Clapperboard, Download, Trash2, CheckCircle2, XCircle, FolderOpen, Terminal, Copy, Check, Loader2 } from 'lucide-react'
import type { useStudio } from '../lib/useStudio'
import type { HealthDeps } from '../lib/api'
import { Field, TextField } from './ui'

// One-time system setup a fresh Mac needs before the app can install its Python deps.
// The app deliberately does NOT install Python/ffmpeg itself — it reuses the system
// ones — so on a brand-new Mac the user runs these once in Terminal.
//
// They MUST be run one at a time: the Homebrew installer (step 1) is interactive
// (asks for the Mac password + a RETURN), so pasting both lines together lets step 2
// get swallowed by that prompt. Step 2 uses brew's absolute path because brew isn't
// on the new shell's PATH yet (Homebrew only adds it to future sessions).
const FRESH_MAC_STEPS: { label: string; cmd: string }[] = [
  {
    label: '1) Zainstaluj Homebrew (zapyta o hasło Maca i o naciśnięcie Return):',
    cmd: '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
  },
  {
    label: '2) Gdy Homebrew skończy, zainstaluj Pythona i ffmpeg:',
    cmd: '/opt/homebrew/bin/brew install python@3.11 ffmpeg',
  },
]

type Studio = ReturnType<typeof useStudio>
type Dep = { key: keyof HealthDeps; label: string; optional?: boolean }
type InstallTarget = 'common' | 'shorts' | 'dubmaster' | 'all' | 'music' | 'videogen'

type ModuleDef = {
  key: string
  label: string
  desc: string
  icon: ReactNode
  deps: Dep[]
  install?: InstallTarget                              // own installable bundle
  installVia?: { target: InstallTarget; note: string } // rides on another bundle
  // Optional advanced override: point at an existing engine folder instead of
  // letting the app install it (so a pre-existing copy isn't re-downloaded).
  external?: { settingKey: 'ace_dir' | 'videogen_dir'; placeholder: string }
}

const MODULES: ModuleDef[] = [
  {
    key: 'common',
    label: 'Wspólne (podstawa)',
    desc: 'Wymagane przez wszystkie moduły: Python, narzędzia pobierania, transkrypcja i tłumaczenie.',
    icon: <Boxes size={16} />,
    install: 'common',
    deps: [
      // Required = what the `common` bundle (requirements-common.txt) + a system FFmpeg
      // actually provide, so the module CAN reach "Gotowy" after installing just common.
      { key: 'python', label: 'Python 3.11+' },
      { key: 'ffmpeg', label: 'FFmpeg (audio / wideo)' },
      { key: 'yt_dlp', label: 'yt-dlp (pobieranie z YouTube)' },
      { key: 'whisper', label: 'Whisper (transkrypcja mowy)' },
      // Translation engines are OPTIONAL here: common ships Gemini (google-genai) + Argos;
      // NLLB rides on the DubMaster bundle (torch + transformers); uv ships with the
      // music/obraz/wideo engines (listed there). At least one engine is enough to translate.
      { key: 'gemini', label: 'Gemini (tłumaczenie chmurowe)', optional: true },
      { key: 'argos', label: 'Argos (lokalne, lekkie CPU)', optional: true },
      { key: 'nllb', label: 'NLLB-200 (lokalne, z pakietem Dubbing)', optional: true },
    ],
  },
  {
    key: 'dub',
    label: 'Dubbing (DubMaster)',
    desc: 'Profesjonalny dubbing: separacja tła, klonowanie i synteza głosu, synchronizacja tempa.',
    icon: <Mic2 size={16} />,
    install: 'dubmaster',
    deps: [
      { key: 'torch', label: 'PyTorch' },
      { key: 'torchaudio', label: 'Torchaudio' },
      { key: 'torchcodec', label: 'TorchCodec' },
      { key: 'demucs', label: 'Demucs (separacja audio)' },
      { key: 'transformers', label: 'Transformers' },
      { key: 'accelerate', label: 'Accelerate' },
      { key: 'sentencepiece', label: 'SentencePiece' },
      { key: 'soundfile', label: 'SoundFile' },
      { key: 'qwen_tts', label: 'Qwen TTS (głos)' },
    ],
  },
  {
    key: 'tts',
    label: 'Tekst → Audio',
    desc: 'Synteza mowy z dowolnego tekstu (Qwen TTS). Korzysta z pakietu DubMaster.',
    icon: <AudioLines size={16} />,
    installVia: { target: 'dubmaster', note: 'Część pakietu DubMaster' },
    deps: [
      { key: 'qwen_tts', label: 'Qwen TTS (synteza mowy)' },
      { key: 'transformers', label: 'Transformers (model głosu)' },
      { key: 'soundfile', label: 'SoundFile (zapis audio)' },
    ],
  },
  {
    key: 'subs',
    label: 'Napisy',
    desc: 'Transkrypcja Whisperem i tłumaczenie napisów na wiele języków (SRT/VTT).',
    icon: <Captions size={16} />,
    installVia: { target: 'dubmaster', note: 'Wspólne + DubMaster (NLLB)' },
    deps: [
      { key: 'whisper', label: 'Whisper (transkrypcja)' },
      { key: 'nllb', label: 'NLLB-200 (tłumaczenie lokalne)' },
      { key: 'ffmpeg', label: 'FFmpeg (ekstrakcja audio)' },
    ],
  },
  {
    key: 'shorts',
    label: 'Shorty (AI ViralCutter)',
    desc: 'AI wybiera viralowe momenty, kadruje pod 9:16, śledzi twarz i dodaje napisy.',
    icon: <Scissors size={16} />,
    install: 'shorts',
    deps: [
      { key: 'opencv', label: 'OpenCV (wideo)' },
      { key: 'ultralytics', label: 'YOLO (śledzenie twarzy)' },
      { key: 'pillow', label: 'Pillow (grafika)' },
      { key: 'whisper', label: 'Whisper (transkrypcja)' },
      { key: 'gemini', label: 'Gemini (wybór momentów)' },
    ],
  },
  {
    key: 'music',
    label: 'Muzyka (ACE-Step)',
    desc: 'Generator muzyki. Na macOS aplikacja sama pobiera silnik ACE-Step i modele (~50 GB) z Hugging Face (na Windows niewspierane).',
    icon: <Music4 size={16} />,
    install: 'music',
    external: { settingKey: 'ace_dir', placeholder: 'np. /Volumes/Extreme Pro/Music Generator/vendor/ACE-Step-1.5' },
    deps: [
      { key: 'ace_step', label: 'Silnik ACE-Step' },
      { key: 'uv', label: 'uv (uruchamianie silnika)' },
    ],
  },
  {
    key: 'videogen',
    label: 'Obraz · Wideo (FLUX / LTX)',
    desc: 'Generator obrazów i wideo. Na macOS aplikacja sama pobiera silnik LTX/MFLUX i modele (~45 GB) z Hugging Face (na Windows niewspierane).',
    icon: <Clapperboard size={16} />,
    install: 'videogen',
    external: { settingKey: 'videogen_dir', placeholder: 'np. /Volumes/Extreme Pro/VideoGenerator' },
    deps: [
      { key: 'videogen', label: 'Silnik VideoGenerator' },
      { key: 'uv', label: 'uv (uruchamianie MLX)' },
    ],
  },
]

function readiness(deps: Dep[], health: Studio['health']): 'ready' | 'partial' | 'none' {
  const required = deps.filter((d) => !d.optional)
  const okCount = required.filter((d) => health?.deps?.[d.key]).length
  if (!health) return 'none'
  if (okCount === required.length) return 'ready'
  if (okCount === 0) return 'none'
  return 'partial'
}

export function ModuleManager({ studio }: { studio: Studio }) {
  const { health, status, install, uninstall, update, openPath, config, logs, online } = studio
  const installing = status?.installing
  const envReady = !!status?.installed
  const [copiedStep, setCopiedStep] = useState<number | null>(null)
  const logRef = useRef<HTMLPreElement | null>(null)

  // Auto-scroll the live install log to the newest line.
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs])

  const copyStep = async (i: number, cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd)
      setCopiedStep(i)
      window.setTimeout(() => setCopiedStep((c) => (c === i ? null : c)), 1800)
    } catch { /* clipboard blocked — user can still select the text */ }
  }

  const confirmUninstall = (target: InstallTarget, label: string) => {
    if (!window.confirm(`Odinstalować pakiety: ${label}? Współdzielone biblioteki (numpy, torch…) zostają.`)) return
    uninstall(target)
  }
  // Show the live install console while installing or whenever there's output to read.
  const showConsole = installing || (logs?.trim().length ?? 0) > 0

  return (
    <div className="card settings-card">
      <div className="settings-card-head">
        <Boxes size={17} />
        <h3>Silniki i moduły</h3>
        <span className={installing ? 'status-pill busy' : status?.installed ? 'status-pill ok' : 'status-pill'}>
          {installing ? 'Instalacja…' : status?.installed ? 'Środowisko gotowe' : 'Wymaga instalacji'}
        </span>
        <button type="button" className="primary-btn module-install-all" style={{ marginLeft: 'auto' }} disabled={installing} onClick={() => install('all')}>
          <Download size={14} /> Zainstaluj wszystko
        </button>
      </div>
      <p className="settings-desc">
        Każdy moduł ma swoje zależności i własne przyciski instalacji. Zacznij od <b>Wspólnych</b> (podstawa),
        potem doinstaluj tylko te sekcje, których używasz. Generatory muzyki/obrazu/wideo aplikacja pobiera
        i konfiguruje automatycznie (silnik + modele z Hugging Face) — nie musisz nic wskazywać ręcznie.
      </p>

      {/* Fresh-Mac one-time setup. The app reuses the system Python + ffmpeg and cannot
          install them itself, so on a brand-new Mac (no backend yet) we show the exact
          one-time Terminal commands up front instead of failing silently. */}
      {!envReady && !installing && (
        <div className="setup-required">
          <div className="setup-required-head">
            <Terminal size={16} />
            <strong>Pierwsze uruchomienie — jednorazowa konfiguracja systemu</strong>
          </div>
          <p>
            Aplikacja korzysta z systemowego <b>Pythona</b> i <b>ffmpeg</b> (nie instaluje ich za Ciebie).
            Na nowym Macu wykonaj <b>po kolei</b> dwie komendy w aplikacji <b>Terminal</b> (jednorazowo) — najpierw
            pierwszą, poczekaj aż się skończy, dopiero potem drugą. Następnie wróć tu i kliknij <b>„Zainstaluj wszystko"</b>.
          </p>
          {FRESH_MAC_STEPS.map((step, i) => (
            <div className="setup-step" key={i}>
              <span className="setup-step-label">{step.label}</span>
              <div className="setup-cmd">
                <pre>{step.cmd}</pre>
                <button type="button" className="ghost-btn icon-btn" title="Kopiuj komendę" onClick={() => copyStep(i, step.cmd)}>
                  {copiedStep === i ? <Check size={15} /> : <Copy size={15} />}
                </button>
              </div>
            </div>
          ))}
          <p className="setup-hint">
            Kopiuj i wklejaj każdą komendę osobno (pierwsza zapyta o hasło Maca i o naciśnięcie Return).
            Masz już Pythona 3.11+ i ffmpeg? Pomiń ten krok i od razu kliknij „Zainstaluj". Po instalacji
            ciężkie modele (PyTorch itp.) dociągną się automatycznie — to może chwilę potrwać. Ten panel zniknie,
            gdy środowisko będzie gotowe.
          </p>
        </div>
      )}

      {/* Live install log — so clicking "Zainstaluj" always shows what's happening
          (progress, errors, or the system-tools message) instead of nothing. */}
      {showConsole && (
        <div className={`install-console${installing ? ' is-running' : ''}`}>
          <div className="install-console-head">
            {installing ? <Loader2 size={14} className="spin" /> : <Terminal size={14} />}
            <span>{installing ? 'Instalacja w toku…' : online ? 'Log ostatniej instalacji' : 'Log instalacji'}</span>
          </div>
          <pre className="install-log" ref={logRef}>{logs?.trim() || 'Uruchamiam…'}</pre>
        </div>
      )}

      <div className="module-list">
        {MODULES.map((m) => {
          const state = readiness(m.deps, health)
          return (
            <div className="module-card" key={m.key}>
              <div className="module-card-head">
                <span className="module-icon">{m.icon}</span>
                <div className="module-title">
                  <span className="module-name">{m.label}</span>
                  <small>{m.desc}</small>
                </div>
                <span className={`status-pill ${state === 'ready' ? 'ok' : state === 'partial' ? 'busy' : ''}`}>
                  {state === 'ready' ? 'Gotowy' : state === 'partial' ? 'Częściowo' : 'Brak'}
                </span>
              </div>

              <div className="module-deps">
                {m.deps.map((d) => {
                  const ok = health?.deps?.[d.key]
                  return (
                    <span className={`module-dep ${ok ? 'ok' : d.optional ? 'opt' : 'miss'}`} key={d.key}>
                      {ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {d.label}{d.optional && !ok ? ' (opcj.)' : ''}
                    </span>
                  )
                })}
              </div>

              {m.install ? (
                <div className="module-actions">
                  <button type="button" className="ghost-btn" disabled={installing} onClick={() => install(m.install!)}>
                    <Download size={14} /> {state === 'ready' ? 'Przeinstaluj / napraw' : 'Zainstaluj'}
                  </button>
                  <button type="button" className="ghost-btn danger" disabled={installing} onClick={() => confirmUninstall(m.install!, m.label)}>
                    <Trash2 size={14} /> Odinstaluj
                  </button>
                </div>
              ) : m.installVia ? (
                <div className="module-actions">
                  <button type="button" className="ghost-btn" disabled={installing} onClick={() => install(m.installVia!.target)}>
                    <Download size={14} /> Zainstaluj
                  </button>
                  <button type="button" className="ghost-btn danger" disabled={installing} onClick={() => confirmUninstall(m.installVia!.target, `${m.label} (${m.installVia!.note})`)}>
                    <Trash2 size={14} /> Odinstaluj
                  </button>
                  <span className="module-via">{m.installVia.note}</span>
                </div>
              ) : null}

              {m.external && (
                <details className="module-external-advanced">
                  <summary>Zaawansowane: użyj istniejącego folderu silnika</summary>
                  <div className="module-external">
                    <Field label="Folder silnika (opcjonalnie)" hint="Zostaw puste, aby aplikacja zainstalowała i wykrywała silnik automatycznie. Wskaż folder tylko, jeśli masz już gotową kopię i nie chcesz pobierać jej ponownie.">
                      <TextField value={config.app[m.external.settingKey] ?? ''} placeholder={m.external.placeholder} onChange={(v) => update('app', { [m.external!.settingKey]: v })} />
                    </Field>
                    {config.app[m.external.settingKey] && (
                      <button type="button" className="ghost-btn" onClick={() => openPath(String(config.app[m.external!.settingKey]))}><FolderOpen size={14} /> Otwórz</button>
                    )}
                  </div>
                </details>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
