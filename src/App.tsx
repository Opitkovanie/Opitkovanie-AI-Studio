import { useEffect, useRef, useState } from 'react'
import { LayoutDashboard, Mic2, Scissors, Settings2, Activity, Captions, AudioLines, Music4, ImageIcon, Clapperboard, CheckCircle2, CircleAlert, Download, RefreshCw, X } from 'lucide-react'
import './App.css'
import { useStudio } from './lib/useStudio'
import { usePersistentState } from './lib/usePersistentState'
import { Dashboard } from './views/Dashboard'
import { DubMaster } from './views/DubMaster'
import { Shorts } from './views/Shorts'
import { Subtitles } from './views/Subtitles'
import { TextToAudio } from './views/TextToAudio'
import { MusicGenerator } from './views/MusicGenerator'
import { ImageGenerator } from './views/ImageGenerator'
import { VideoGenerator } from './views/VideoGenerator'
import { Settings } from './views/Settings'
import type { AppUpdateStatus } from './types/dubcut'

export type ViewId = 'home' | 'dub' | 'shorts' | 'subs' | 'tts' | 'music' | 'image' | 'video' | 'settings'

const NAV: { id: ViewId; icon: typeof Mic2; label: string }[] = [
  { id: 'home', icon: LayoutDashboard, label: 'Pulpit' },
  { id: 'dub', icon: Mic2, label: 'Dubbing' },
  { id: 'shorts', icon: Scissors, label: 'Shorts' },
  { id: 'subs', icon: Captions, label: 'Napisy' },
  { id: 'tts', icon: AudioLines, label: 'Tekst→Audio' },
  { id: 'music', icon: Music4, label: 'Muzyka' },
  { id: 'image', icon: ImageIcon, label: 'Obraz' },
  { id: 'video', icon: Clapperboard, label: 'Wideo' },
]

const SETTINGS_NAV: { id: ViewId; icon: typeof Mic2; label: string } = { id: 'settings', icon: Settings2, label: 'Ustawienia' }
const ALL_NAV = [...NAV, SETTINGS_NAV]

function meterClass(v: number): string {
  return v >= 85 ? 'hot' : v >= 60 ? 'warm' : ''
}

function notify(title: string, body: string) {
  try {
    if (typeof Notification === 'undefined') return
    if (Notification.permission === 'granted') new Notification(title, { body })
    else if (Notification.permission !== 'denied') {
      Notification.requestPermission().then((p) => { if (p === 'granted') new Notification(title, { body }) })
    }
  } catch { /* notifications unavailable */ }
}

function formatReleaseDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('pl-PL', { dateStyle: 'long' }).format(date)
}

function releaseSummary(notes: string): string {
  const line = String(notes || '').split('\n')
    .map((item) => item.replace(/^[-*]\s*/, '').trim())
    .find((item) => item && !item.startsWith('#') && !/^aktualizacja$/i.test(item) && !/^po pobraniu/i.test(item))
  return line || 'Opis zmian nie został dodany.'
}

function ReleaseNotes({ notes }: { notes: string }) {
  const lines = String(notes || '').split('\n').map((line) => line.trim()).filter(Boolean)
  if (!lines.length) return <p className="update-notes-empty">Opis zmian dla tego wydania nie został jeszcze dodany.</p>
  return <div className="update-notes">
    {lines.map((line, index) => line.startsWith('## ')
      ? <strong key={`${line}-${index}`}>{line.slice(3)}</strong>
      : <p key={`${line}-${index}`} className={line.startsWith('- ') ? 'update-note-item' : ''}>{line.replace(/^[-*]\s*/, '')}</p>)}
  </div>
}

function App() {
  const studio = useStudio()
  const [view, setView] = usePersistentState<ViewId>('dubcut.view', 'home')
  const [updateOpen, setUpdateOpen] = useState(false)
  const [update, setUpdate] = useState<AppUpdateStatus | null>(null)
  const { online, status } = studio
  const prevJobStatus = useRef<Record<string, string>>({})

  // Native notification when a background render finishes while you're elsewhere.
  useEffect(() => {
    try { if (typeof Notification !== 'undefined' && Notification.permission === 'default') Notification.requestPermission() } catch { /* */ }
  }, [])
  useEffect(() => {
    if (!window.dubcut) return
    void window.dubcut.getUpdateStatus().then(setUpdate)
    return window.dubcut.onUpdateStatus(setUpdate)
  }, [])
  useEffect(() => {
    const watch: { key: string; mview: ViewId; status: string; label: string }[] = [
      { key: 'music', mview: 'music', status: studio.musicGenJob.state.status, label: 'Muzyka' },
      { key: 'image', mview: 'image', status: studio.imageGenJob.state.status, label: 'Obraz' },
      { key: 'video', mview: 'video', status: studio.videoGenJob.state.status, label: 'Wideo' },
      { key: 'shorts', mview: 'shorts', status: studio.shortsJob.state.status, label: 'Shorts' },
    ]
    for (const w of watch) {
      const was = prevJobStatus.current[w.key]
      if (w.status !== was) {
        prevJobStatus.current[w.key] = w.status
        if (was === 'running' && (w.status === 'done' || w.status === 'error') && view !== w.mview) {
          notify('Opitkovanie AI Studio', w.status === 'done' ? `${w.label}: gotowe ✓` : `${w.label}: render nie powiódł się`)
        }
      }
    }
  }, [studio.musicGenJob.state.status, studio.imageGenJob.state.status, studio.videoGenJob.state.status, studio.shortsJob.state.status, view])

  const statusLabel = online
    ? 'Gotowy'
    : status?.installing
      ? 'Instalacja'
      : status?.installed
        ? 'Uśpiony'
        : 'Brak silnika'
  const updateAvailable = update?.status === 'available' || update?.status === 'downloading' || update?.status === 'downloaded'
  const updateCurrent = update?.status === 'current'
  const updateLabel = updateAvailable
    ? `Dostępna v${update?.latestVersion ?? ''}`
    : updateCurrent
      ? 'Aktualna'
      : update?.status === 'checking'
        ? 'Sprawdzam…'
        : 'Aktualizacje'
  const checkUpdates = () => {
    setUpdateOpen(true)
    void window.dubcut?.checkUpdates()
  }

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="top-left">
          <span className={`live-dot ${online ? 'on' : status?.installing ? 'busy' : ''}`} />
          <span className="top-module">{ALL_NAV.find((n) => n.id === view)?.label}</span>
        </div>
        <div className="top-brand">
          <img src="./opitkovanie-logo.png" alt="Opitkovanie AI Studio" />
          <span className="top-brand-text">
            <strong>Opitkovanie AI Studio</strong>
          </span>
        </div>
        <div className="top-right">
          {studio.sysStats && (
            <span className="sys-meters" title="Zużycie zasobów systemu">
              {studio.sysStats.cpu != null && (
                <span className="sys-meter"><b>CPU</b><i className={meterClass(studio.sysStats.cpu)}>{studio.sysStats.cpu}%</i></span>
              )}
              {studio.sysStats.gpu != null && (
                <span className="sys-meter"><b>GPU</b><i className={meterClass(studio.sysStats.gpu)}>{studio.sysStats.gpu}%</i></span>
              )}
              {studio.sysStats.ram != null && (
                <span className="sys-meter" title={studio.sysStats.ram_total_gb ? `${studio.sysStats.ram_used_gb} / ${studio.sysStats.ram_total_gb} GB` : undefined}>
                  <b>RAM</b><i className={meterClass(studio.sysStats.ram)}>{studio.sysStats.ram}%</i>
                </span>
              )}
            </span>
          )}
          <span className={`eq ${online ? 'live' : ''}`} aria-hidden="true" title={online ? 'Backend aktywny' : 'Backend offline'}>
            <i /><i /><i /><i /><i />
          </span>
          <span className="stat">
            <Activity size={13} /> {studio.health ? `v${studio.health.version}` : '—'}
          </span>
          <button
            type="button"
            className={`update-pill ${updateAvailable ? 'available' : updateCurrent ? 'current' : ''}`}
            onClick={() => { setUpdateOpen(true); if (!update || update.status === 'idle' || update.status === 'error') checkUpdates() }}
            title="Informacje o aktualizacjach"
          >
            {updateAvailable ? <Download size={12} /> : update?.status === 'error' ? <CircleAlert size={12} /> : <CheckCircle2 size={12} />}
            {updateLabel}
          </button>
          <span className={`idle-pill ${online ? 'on' : ''}`}>{statusLabel}</span>
        </div>
      </header>

      <div className="body">
        <nav className="icon-rail">
          {NAV.map(({ id, icon: Icon, label }) => (
            <button
              key={id}
              type="button"
              className={view === id ? 'rail-btn active' : 'rail-btn'}
              onClick={() => setView(id)}
              title={label}
            >
              <Icon size={22} />
              <em>{label}</em>
            </button>
          ))}
          <span className="rail-spacer" />
          <span className="rail-sep" />
          <button
            type="button"
            className={view === SETTINGS_NAV.id ? 'rail-btn active' : 'rail-btn'}
            onClick={() => setView(SETTINGS_NAV.id)}
            title={SETTINGS_NAV.label}
          >
            <SETTINGS_NAV.icon size={22} />
            <em>{SETTINGS_NAV.label}</em>
          </button>
        </nav>

        {/* Keep every view mounted (hidden when inactive) so each section
            remembers its state — loaded project, transcript edits, scroll —
            and background jobs keep streaming when you switch sections. */}
        <main className="view-area">
          <div className="view-pane" hidden={view !== 'home'}>
            <Dashboard studio={studio} onNavigate={setView} />
          </div>
          <div className="view-pane" hidden={view !== 'dub'}>
            <DubMaster studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'shorts'}>
            <Shorts studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'subs'}>
            <Subtitles studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'tts'}>
            <TextToAudio studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'music'}>
            <MusicGenerator studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'image'}>
            <ImageGenerator studio={studio} onNavigate={setView} />
          </div>
          <div className="view-pane" hidden={view !== 'video'}>
            <VideoGenerator studio={studio} />
          </div>
          <div className="view-pane" hidden={view !== 'settings'}>
            <Settings studio={studio} />
          </div>
        </main>
      </div>

      {updateOpen && (
        <div className="update-overlay" role="dialog" aria-modal="true" aria-labelledby="update-title" onMouseDown={() => setUpdateOpen(false)}>
          <section className="update-card" onMouseDown={(event) => event.stopPropagation()}>
            <header className="update-head">
              <div>
                <span className="update-eyebrow">OPITKOVANIE AI STUDIO</span>
                <h2 id="update-title">Aktualizacje aplikacji</h2>
              </div>
              <button type="button" className="update-close" onClick={() => setUpdateOpen(false)} aria-label="Zamknij"><X size={18} /></button>
            </header>
            <div className={`update-banner ${updateAvailable ? 'available' : update?.status === 'error' ? 'error' : ''}`}>
              {updateAvailable ? <Download size={20} /> : update?.status === 'error' ? <CircleAlert size={20} /> : <CheckCircle2 size={20} />}
              <div>
                <strong>{updateAvailable ? `Dostępna jest wersja v${update?.latestVersion}` : update?.status === 'checking' ? 'Sprawdzam dostępność aktualizacji…' : update?.status === 'error' ? 'Nie udało się sprawdzić aktualizacji' : 'Masz najnowszą wersję aplikacji.'}</strong>
                {update?.message && <span>{update.message}</span>}
              </div>
            </div>
            <div className="update-versions">
              <div><span>Zainstalowana wersja</span><b>v{update?.currentVersion ?? studio.health?.version ?? '—'}</b></div>
              <div><span>Najnowsza wersja</span><b>{update?.latestVersion ? `v${update.latestVersion}` : '—'}</b></div>
              <div><span>Data wydania</span><b>{formatReleaseDate(update?.releaseDate ?? null)}</b></div>
            </div>
            <div className="update-section">
              <h3>Co nowego w v{update?.latestVersion ?? update?.currentVersion ?? '—'}</h3>
              <ReleaseNotes notes={update?.releaseNotes || ''} />
            </div>
            <div className="update-section">
              <h3>Historia wydań</h3>
              <div className="update-history">
                {update?.history?.length ? update.history.map((release) => (
                  <article key={`${release.version}-${release.publishedAt}`}>
                    <div><b>v{release.version}</b><span>{formatReleaseDate(release.publishedAt)}</span></div>
                    <p>{releaseSummary(release.notes)}</p>
                  </article>
                )) : <p className="update-empty">Historia zostanie pobrana z GitHub Releases podczas sprawdzania aktualizacji.</p>}
              </div>
            </div>
            <footer className="update-actions">
              {updateAvailable && (
                <button type="button" className="update-primary" onClick={() => void window.dubcut?.downloadUpdate()} disabled={update?.status === 'downloading'}>
                  <Download size={15} /> {update?.status === 'downloading' ? `Pobieranie ${update.progress ?? 0}%` : 'Aktualizuj'}
                </button>
              )}
              <button type="button" className="update-secondary" onClick={checkUpdates}><RefreshCw size={14} /> Sprawdź ponownie</button>
              <button type="button" className="update-secondary" onClick={() => setUpdateOpen(false)}>Zamknij</button>
            </footer>
          </section>
        </div>
      )}

      <footer className="bottom-bar">
        <span className={online ? 'foot-stat ok' : 'foot-stat'}>Backend {online ? 'online' : 'offline'}</span>
        <span className="foot-grow" />
      </footer>
    </div>
  )
}

export default App
