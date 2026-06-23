import { useEffect, useRef } from 'react'
import { CheckCircle2, XCircle, Loader2, Ban } from 'lucide-react'
import type { JobState } from '../lib/useJob'
import { useTick, formatClock } from '../lib/jobFormat'

const LEVEL_CLASS: Record<string, string> = {
  success: 'ok',
  error: 'err',
  warning: 'warn',
  step: 'step',
  progress: 'prog',
}

export function RunConsole({ state, onCancel }: { state: JobState; onCancel: () => void }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [state.log.length])

  const running = state.status === 'running'
  const now = useTick(running)
  const elapsed = state.startedAt ? ((running ? now : state.finishedAt ?? now) - state.startedAt) / 1000 : 0
  const eta = running && state.progress > 0.03 && elapsed > 1
    ? (elapsed / state.progress) * (1 - state.progress)
    : null
  const percent = Math.round(state.progress * 100)

  return (
    <div className="run-console">
      <div className="run-head">
        <div className="run-status">
          {state.status === 'running' && <Loader2 size={16} className="spin" />}
          {state.status === 'done' && <CheckCircle2 size={16} className="ok" />}
          {state.status === 'error' && <XCircle size={16} className="err" />}
          {state.status === 'cancelled' && <Ban size={16} className="warn" />}
          <span>{running ? `${state.message || statusLabel(state.status)} · ${percent}%` : (state.message || statusLabel(state.status))}</span>
        </div>
        <div className="run-head-right">
          {state.startedAt != null && (state.status === 'running' || state.status === 'done') && (
            <span className="inline-job-time">
              ⏱ {formatClock(elapsed)}{eta != null ? ` · pozostało ~${formatClock(eta)}` : ''}
            </span>
          )}
          {state.status === 'running' && (
            <button type="button" className="ghost-btn danger" onClick={onCancel}>
              Przerwij
            </button>
          )}
        </div>
      </div>
      <div className="run-progress">
        <em style={{ width: `${percent}%` }} />
      </div>
      <div className="run-log">
        {state.log.length === 0 && <p className="run-empty">Brak logów. Uruchom proces, aby zobaczyć postęp.</p>}
        {state.log
          .filter((e) => e.message && e.type !== 'error')
          .map((e, i) => (
            <p key={i} className={`run-line ${LEVEL_CLASS[e.level ?? ''] ?? ''}`}>
              {e.message}
            </p>
          ))}
        {state.error && <p className="run-line err">{state.error}</p>}
        <div ref={endRef} />
      </div>
    </div>
  )
}

function statusLabel(s: JobState['status']) {
  switch (s) {
    case 'running':
      return 'Przetwarzanie…'
    case 'done':
      return 'Gotowe'
    case 'error':
      return 'Błąd'
    case 'cancelled':
      return 'Przerwano'
    default:
      return 'Gotowe do pracy'
  }
}
