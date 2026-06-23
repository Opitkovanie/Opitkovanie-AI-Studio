import { useEffect, useRef, useState } from 'react'
import { FolderOpen, RefreshCw, Terminal, Info, BookText, Languages, Cpu, AudioLines, CheckCircle2 } from 'lucide-react'
import type { useStudio } from '../lib/useStudio'
import { api } from '../lib/api'
import { Field, TextField, TextArea, Select } from '../components/ui'
import { StorageManager } from '../components/StorageManager'
import { ModelManager } from '../components/ModelManager'
import { ModuleManager } from '../components/ModuleManager'

type Studio = ReturnType<typeof useStudio>

export function Settings({ studio }: { studio: Studio }) {
  const { status, health, online, openRuntime, refresh, config, update, meta, openPath, chooseWorkDir } = studio
  const engines = meta?.dub?.translation_engines?.length
    ? meta.dub.translation_engines
    : [
        { id: 'nllb', label: 'NLLB-200 (lokalny, najlepsza jakość)' },
        { id: 'argos', label: 'Argos (lokalny, lekki CPU)' },
        { id: 'gemini', label: 'Gemini 2.5 Flash (chmura, wymaga klucza)' },
      ]
  const currentEngineId = String(config.app.translation_engine ?? 'nllb')
  const engineLabel = (id: string) => engines.find((e) => e.id === id)?.label ?? id
  // Voice (TTS) engine — global switch used by every module (dubbing, Text→Audio, Shorts).
  const ttsEngines = meta?.dub?.tts_engines?.length
    ? meta.dub.tts_engines
    : [
        { id: 'qwen', label: 'Qwen TTS (10 języków, głosy presetowe)' },
        { id: 'omnivoice', label: 'OmniVoice (jakość studyjna, polski, klonowanie głosu)' },
      ]
  const currentTtsEngine = String(config.app.tts_engine ?? 'qwen')
  const ttsEngineLabel = (id: string) => ttsEngines.find((e) => e.id === id)?.label ?? id
  const omnivoiceReady = !!health?.deps?.omnivoice
  const qwenReady = !!health?.deps?.qwen_tts
  const [installingOmni, setInstallingOmni] = useState(false)
  const installOmnivoice = async () => {
    if (installingOmni) return
    setInstallingOmni(true)
    try {
      await api.omnivoiceInstall()
      // Install (engine venv + ~3 GB model) runs as a background job; poll readiness.
      const started = Date.now()
      const poll = async (): Promise<void> => {
        const st = await api.omnivoiceStatus().catch(() => null)
        if (st?.ready) { await refresh(true); return }
        if (Date.now() - started > 45 * 60 * 1000) return
        await new Promise((r) => setTimeout(r, 4000))
        return poll()
      }
      await poll()
    } finally {
      setInstallingOmni(false)
    }
  }
  const [logs, setLogs] = useState('')
  const [glossaryWrong, setGlossaryWrong] = useState('')
  const [glossaryRight, setGlossaryRight] = useState('')
  const glossaryRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    window.dubcut?.getLogs().then(setLogs)
    const unsub = window.dubcut?.onLog((p) => setLogs((c) => `${c}${p.line}`))
    return () => unsub?.()
  }, [])

  const addGlossaryEntry = () => {
    const wrong = glossaryWrong.trim()
    const right = glossaryRight.trim()
    if (!wrong && !right) return
    const entry = wrong && right && wrong.toLowerCase() !== right.toLowerCase() ? `${wrong} -> ${right}` : (right || wrong)
    const current = String(config.app.glossary ?? '')
    const lines = current.split('\n').map((line) => line.trim()).filter(Boolean)
    const seen = new Set(lines.map((line) => line.toLowerCase()))
    const exists = seen.has(entry.toLowerCase())
    if (!exists) {
      update('app', { glossary: [...lines, entry].join('\n') })
    }
    setGlossaryWrong('')
    setGlossaryRight('')
    // The new line is appended at the BOTTOM of the list; the textarea doesn't
    // auto-scroll, so a long glossary hid the addition below the fold (the user
    // thought "Dodaj wpis" did nothing). Scroll it into view + flash it.
    window.setTimeout(() => {
      const ta = glossaryRef.current
      if (ta) {
        ta.scrollTop = ta.scrollHeight
        if (!exists) {
          ta.classList.add('glossary-flash')
          window.setTimeout(() => ta.classList.remove('glossary-flash'), 700)
        }
      }
    }, 60)
  }

  return (
    <div className="settings-view">
      <header className="editor-head">
        <div>
          <span className="eyebrow">Konfiguracja</span>
          <h2>Ustawienia i silniki</h2>
        </div>
        <button type="button" className="ghost-btn" onClick={() => refresh(true)}>
          <RefreshCw size={15} /> Odśwież
        </button>
      </header>

      {/* Per-module install / readiness */}
      <ModuleManager studio={studio} />

      {/* Translation engine + its API key together */}
      <div className="card settings-card">
        <div className="settings-card-head">
          <Languages size={17} />
          <h3>Silnik tłumaczenia</h3>
        </div>
        <p className="settings-desc">
          Silnik używany do tłumaczenia napisów, metadanych shortów i dubbingu. Domyślnie działa
          w pełni lokalnie — bez kosztów i bez klucza API. Modele pobierają się raz do katalogu
          domowego (NLLB: ~/.cache/huggingface).
        </p>
        <Field label="Aktywny silnik">
          <Select
            value={engineLabel(currentEngineId)}
            options={engines.map((e) => e.label)}
            onChange={(label) => {
              const picked = engines.find((e) => e.label === label)
              if (picked) update('app', { translation_engine: picked.id })
            }}
          />
        </Field>
        {currentEngineId === 'nllb' && !health?.deps?.nllb && (
          <p className="settings-desc warn">
            NLLB nie jest jeszcze zainstalowany — zainstaluj moduł „Wspólne" albo „DubMaster".
            Do czasu instalacji tłumaczenie spróbuje użyć Argos.
          </p>
        )}
        {currentEngineId === 'argos' && !health?.deps?.argos && (
          <p className="settings-desc warn">Argos nie jest jeszcze zainstalowany — uruchom instalację „Wspólne".</p>
        )}
        <Field label="Klucz API Google Gemini" hint="Potrzebny tylko dla trybu Gemini (tłumaczenie chmurowe) oraz dla AI wybierającego momenty w Shortach.">
          <TextField
            value={config.app.gemini_api_key ?? ''}
            type="password"
            placeholder="AIza…"
            onChange={(v) => update('app', { gemini_api_key: v })}
          />
        </Field>
        {currentEngineId === 'gemini' && !String(config.app.gemini_api_key ?? '').trim() && (
          <p className="settings-desc warn">
            Tryb Gemini wymaga klucza API powyżej. Bez klucza tłumaczenie użyje silnika lokalnego.
          </p>
        )}
      </div>

      {/* Voice (TTS) engine — Qwen ↔ OmniVoice, used everywhere */}
      <div className="card settings-card">
        <div className="settings-card-head">
          <AudioLines size={17} />
          <h3>Silnik głosu (TTS)</h3>
        </div>
        <p className="settings-desc">
          Silnik syntezy mowy używany przez wszystkie moduły (dubbing, lektor, Tekst→Audio,
          Shorts). <strong>Qwen TTS</strong> — 10 języków i głosy presetowe (bez polskiego).
          <strong> OmniVoice</strong> — jakość studyjna, klonowanie głosu i polski oraz wiele
          innych języków. Model instaluje się raz w systemie (~3 GB do ~/.cache/huggingface).
        </p>
        <Field label="Aktywny silnik">
          <Select
            value={ttsEngineLabel(currentTtsEngine)}
            options={ttsEngines.map((e) => e.label)}
            onChange={(label) => {
              const picked = ttsEngines.find((e) => e.label === label)
              if (picked) update('app', { tts_engine: picked.id })
            }}
          />
        </Field>
        {/* Status of the ACTIVE engine — both show readiness + a green tick when installed. */}
        {currentTtsEngine === 'qwen' && (
          qwenReady ? (
            <p className="settings-desc" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <CheckCircle2 size={16} color="#22c55e" /> Qwen TTS jest zainstalowany i gotowy.
            </p>
          ) : (
            <p className="settings-desc warn">
              Qwen TTS nie jest jeszcze zainstalowany — zainstaluj moduł „DubMaster" w sekcji powyżej.
            </p>
          )
        )}
        {currentTtsEngine === 'omnivoice' && (
          omnivoiceReady ? (
            <p className="settings-desc" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <CheckCircle2 size={16} color="#22c55e" /> OmniVoice jest zainstalowany i gotowy.
            </p>
          ) : (
            <div className="settings-desc warn" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span>OmniVoice nie jest jeszcze zainstalowany w systemie.</span>
              <button type="button" className="primary-btn" disabled={installingOmni} onClick={installOmnivoice}>
                {installingOmni ? 'Instalowanie… (to może potrwać)' : 'Zainstaluj OmniVoice'}
              </button>
            </div>
          )
        )}
      </div>

      {/* Work folder (data) + per-module cleanup */}
      <StorageManager online={online} openPath={openPath} chooseWorkDir={chooseWorkDir} onSetWorkDir={(p) => update('app', { work_dir: p })} />

      <ModelManager online={online} openPath={openPath} />

      <div className="card settings-card">
        <div className="settings-card-head">
          <BookText size={17} />
          <h3>Słownik poprawek</h3>
        </div>
        <p className="settings-desc">
          Poprawia pisownię nazw własnych w transkrypcji, napisach i tłumaczeniach (np. „Open AI" → „OpenAI").
        </p>
        <div className="glossary-add">
          <Field label="Nazwa albo błędna wersja">
            <TextField value={glossaryWrong} placeholder="np. WattCycle albo Humsieng" onChange={setGlossaryWrong} />
          </Field>
          <Field label="Poprawna wersja">
            <TextField value={glossaryRight} placeholder="np. Humsienk" onChange={setGlossaryRight} />
          </Field>
          <button type="button" className="primary-btn" onClick={addGlossaryEntry}>Dodaj wpis</button>
        </div>
        <Field label="Słownik">
          <TextArea
            innerRef={glossaryRef}
            value={config.app.glossary ?? ''}
            rows={10}
            placeholder={'Humsieng -> Humsienk\nOpen AI -> OpenAI\nAnthropic'}
            onChange={(v) => update('app', { glossary: v })}
          />
        </Field>
      </div>

      {/* Diagnostics: backend info + app (engines) folder + logs */}
      <div className="card settings-card">
        <div className="settings-card-head">
          <Cpu size={17} />
          <h3>Diagnostyka i środowisko</h3>
          <span className={online ? 'status-pill ok' : 'status-pill'} style={{ marginLeft: 'auto' }}>
            {online ? 'Backend online' : 'Backend offline'}
          </span>
        </div>
        {status && (
          <dl className="settings-meta">
            <div><dt>Backend</dt><dd>{status.url}</dd></div>
            <div><dt>Folder aplikacji (ustawienia)</dt><dd className="mono">{status.dataDir}</dd></div>
            {health && <div><dt>Wersja</dt><dd>{health.version}</dd></div>}
          </dl>
        )}
        <div className="settings-actions">
          <button type="button" className="ghost-btn" onClick={() => openRuntime()}>
            <FolderOpen size={15} /> Folder środowiska (silniki)
          </button>
        </div>
        <details className="diag-logs">
          <summary><Terminal size={14} /> Logi instalacji / backendu</summary>
          <pre className="install-log">{logs || 'Brak logów.'}</pre>
        </details>
      </div>

      <div className="card settings-card about">
        <div className="settings-card-head">
          <Info size={17} />
          <h3>Opitkovanie AI Studio</h3>
        </div>
        <p className="settings-desc">
          Lokalne combo AI: dubbing, shorty, napisy, synteza mowy oraz generatory muzyki, obrazu i wideo —
          w jednej natywnej aplikacji. Całe przetwarzanie odbywa się na Twoim komputerze.
        </p>
      </div>
    </div>
  )
}
