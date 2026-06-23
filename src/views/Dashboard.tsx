import { useEffect, useState } from 'react'
import { Mic2, Scissors, Download, ArrowRight, Sparkles, Captions, AudioLines, Music4, ImageIcon, Clapperboard, Activity, HardDrive, CheckCircle2, AlertTriangle, Loader2, XCircle, CircleSlash } from 'lucide-react'
import type { useStudio } from '../lib/useStudio'
import type { ViewId } from '../App'
import { api, type JobSummary } from '../lib/api'

type Studio = ReturnType<typeof useStudio>

const JOB_LABELS: Record<string, string> = {
  'shorts.analyze': 'Analiza shortów (AI)',
  'shorts.render': 'Renderowanie shorta',
  'shorts.render-version': 'Przegenerowanie wersji',
  'shorts.render-dub': 'Dubbing shorta',
  'shorts.translate': 'Tłumaczenie napisów',
  'shorts.translate-version-subs': 'Napisy w innym języku',
  'shorts.translate-version-subs-batch': 'Napisy wsadowe (wiele języków)',
  'shorts.prepare-manual': 'Przygotowanie własnego shorta',
  'dub.run': 'Dubbing wideo',
  'dub.subtitles': 'Generowanie napisów',
  'music.generate': 'Generowanie muzyki',
  'image.generate': 'Generowanie obrazu',
  'video.generate': 'Generowanie wideo',
  'tts.generate': 'Synteza mowy',
}
const jobLabel = (kind: string) => JOB_LABELS[kind] ?? kind

function fmtClock(sec: number) {
  if (!Number.isFinite(sec) || sec < 0) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function HealthPanel({ studio }: { studio: Studio }) {
  const sys = studio.health?.system
  if (!studio.online || !sys) return null
  const warnings = sys.warnings ?? []
  return (
    <div className={`dash-status-card ${warnings.length ? 'warn' : 'ok'}`}>
      <div className="dash-status-head">
        {warnings.length ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
        <h3>Stan systemu</h3>
        <span className="dash-status-pill">
          <HardDrive size={13} /> {sys.disk_free_gb != null ? `${sys.disk_free_gb} GB wolne` : 'dysk: ?'}
          {sys.disk_name ? ` · ${sys.disk_name}` : ''}
        </span>
      </div>
      {warnings.length === 0 ? (
        <p className="dash-status-ok">FFmpeg, dysk roboczy i wolne miejsce — wszystko w porządku.</p>
      ) : (
        <ul className="dash-status-warns">
          {warnings.map((w, i) => <li key={i}><AlertTriangle size={13} /> {w}</li>)}
        </ul>
      )}
      {sys.data_path && (
        <p className="dash-status-path" title={sys.data_path}>
          Projekty: <code>{sys.data_path}</code>
          {' · '}{sys.data_disk_mounted ? 'dysk podłączony' : 'dysk NIEpodłączony'}
        </p>
      )}
    </div>
  )
}

function JobsPanel({ online }: { online: boolean }) {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!online) { setJobs([]); return }
    let alive = true
    const tick = () => {
      setNow(Date.now() / 1000)
      api.jobsList().then((j) => { if (alive) setJobs(j) }).catch(() => {})
    }
    tick()
    const t = window.setInterval(tick, 2000)
    return () => { alive = false; window.clearInterval(t) }
  }, [online])
  if (!online || jobs.length === 0) return null
  const running = jobs.filter((j) => j.status === 'running')
  const recent = jobs.filter((j) => j.status !== 'running').slice(0, 6)
  const icon = (s: string) => s === 'running' ? <Loader2 size={14} className="spin" />
    : s === 'done' ? <CheckCircle2 size={14} />
    : s === 'error' ? <XCircle size={14} />
    : <CircleSlash size={14} />
  const eta = (j: JobSummary) => {
    if (j.status !== 'running' || j.progress <= 0.02) return ''
    const elapsed = now - j.created
    const remain = elapsed * (1 - j.progress) / j.progress
    return `~${fmtClock(remain)}`
  }
  return (
    <div className="dash-jobs-card">
      <div className="dash-status-head">
        <Activity size={17} />
        <h3>Zadania{running.length ? ` · ${running.length} w toku` : ''}</h3>
      </div>
      <div className="dash-jobs-list">
        {running.map((j) => (
          <div key={j.id} className="dash-job running">
            <span className="dash-job-ico">{icon(j.status)}</span>
            <span className="dash-job-name">{jobLabel(j.kind)}</span>
            <span className="dash-job-bar"><em style={{ width: `${Math.round(j.progress * 100)}%` }} /></span>
            <span className="dash-job-meta">{Math.round(j.progress * 100)}% {eta(j) && `· ${eta(j)}`}</span>
          </div>
        ))}
        {recent.map((j) => (
          <div key={j.id} className={`dash-job ${j.status}`}>
            <span className="dash-job-ico">{icon(j.status)}</span>
            <span className="dash-job-name">{jobLabel(j.kind)}</span>
            <span className="dash-job-meta">
              {j.status === 'done' ? 'gotowe' : j.status === 'error' ? 'błąd' : 'anulowano'}
              {j.finished ? ` · ${fmtClock((j.finished - j.created))}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function Dashboard({ studio, onNavigate }: { studio: Studio; onNavigate: (v: ViewId) => void }) {
  const { online, status } = studio
  return (
    <div className="dashboard">
      <HealthPanel studio={studio} />
      <JobsPanel online={online} />
      <div className="dash-hero">
        <span className="eyebrow">
          <Sparkles size={13} /> Studio lokalne AI
        </span>
        <h1>Witaj w Opitkovanie AI Studio</h1>
        <p>Dubbing, shorty, napisy, synteza mowy i generator muzyki w jednym miejscu. Prywatnie, lokalnie, bez limitów.</p>
        {!online && (
          <button type="button" className="primary-btn lg" onClick={() => onNavigate('settings')}>
            <Download size={16} /> {status?.installed ? 'Uruchom silniki' : 'Rozpocznij instalację'}
          </button>
        )}
      </div>

      <div className="dash-cards">
        <button type="button" className="dash-card dub" onClick={() => onNavigate('dub')}>
          <span className="dash-icon">
            <Mic2 size={22} />
          </span>
          <h3>DubMaster</h3>
          <p>Profesjonalny dubbing z klonowaniem głosu, separacją tła i synchronizacją tempa.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card cut" onClick={() => onNavigate('shorts')}>
          <span className="dash-icon">
            <Scissors size={22} />
          </span>
          <h3>AI ViralCutter</h3>
          <p>AI wybiera najlepsze momenty, dodaje animowane napisy, kadruje pod 9:16 i eksportuje.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card subs" onClick={() => onNavigate('subs')}>
          <span className="dash-icon">
            <Captions size={22} />
          </span>
          <h3>Napisy AI</h3>
          <p>Transkrypcja Whisperem i tłumaczenie napisów na wiele języków naraz — pobierz SRT i VTT.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card tts" onClick={() => onNavigate('tts')}>
          <span className="dash-icon">
            <AudioLines size={22} />
          </span>
          <h3>Tekst → Audio</h3>
          <p>Zamień dowolny tekst w mowę głosem Qwen lub sklonowanym z próbki — z opcją tłumaczenia.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card music" onClick={() => onNavigate('music')}>
          <span className="dash-icon">
            <Music4 size={22} />
          </span>
          <h3>Music Generator</h3>
          <p>Twórz oryginalne utwory z tekstem i stylem — lokalnie na silniku ACE-Step, z gotową galerią do pobrania.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card image" onClick={() => onNavigate('image')}>
          <span className="dash-icon">
            <ImageIcon size={22} />
          </span>
          <h3>Generator obrazów</h3>
          <p>Text-to-image i image-to-image lokalnie na FLUX/MFLUX — style, warianty i galeria gotowych grafik.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>

        <button type="button" className="dash-card video" onClick={() => onNavigate('video')}>
          <span className="dash-icon">
            <Clapperboard size={22} />
          </span>
          <h3>Generator wideo</h3>
          <p>Text-to-video i image-to-video lokalnie na LTX 2.3 MLX — z opcjonalnym audio i galerią klipów.</p>
          <span className="dash-go">
            Otwórz <ArrowRight size={15} />
          </span>
        </button>
      </div>
    </div>
  )
}
