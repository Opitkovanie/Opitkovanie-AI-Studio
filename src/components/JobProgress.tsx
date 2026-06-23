import { Loader2, CheckCircle2, XCircle, Ban } from 'lucide-react'
import type { JobState } from '../lib/useJob'
import { formatClock, useTick } from '../lib/jobFormat'

/** Inline progress bar with live elapsed time + ETA. Shared by Shorts and Dubbing. */
export function InlineJobProgress({ state, label, onCancel }: { state: JobState; label: string; onCancel: () => void }) {
  const running = state.status === 'running'
  const done = state.status === 'done'
  const errored = state.status === 'error'
  const cancelled = state.status === 'cancelled'
  const percent = done ? 100 : Math.round(state.progress * 100)
  const now = useTick(running)
  // Elapsed freezes at completion (uses finishedAt) so it stops counting.
  const elapsed = state.startedAt ? ((running ? now : state.finishedAt ?? now) - state.startedAt) / 1000 : 0
  const eta = running && state.progress > 0.03 && elapsed > 1
    ? (elapsed / state.progress) * (1 - state.progress)
    : null
  return (
    <div className="inline-job">
      <div className="inline-job-head">
        <span>
          {running && <Loader2 size={14} className="spin" />}
          {done && <CheckCircle2 size={14} className="ok" />}
          {errored && <XCircle size={14} className="err" />}
          {cancelled && <Ban size={14} className="warn" />}
          {' '}{running ? `${label}: ${percent}%` : (state.message || label)}
        </span>
        {state.startedAt != null && (running || done) && (
          <span className="inline-job-time">
            ⏱ {formatClock(elapsed)}{eta != null ? ` · pozostało ~${formatClock(eta)}` : ''}
          </span>
        )}
        {running && (
          <button type="button" className="ghost-btn danger inline-cancel" onClick={onCancel}>Przerwij</button>
        )}
      </div>
      <div className="run-progress">
        <em style={{ width: `${percent}%` }} />
      </div>
      {running && <p>{state.message || label}</p>}
      {errored && <p className="err">{state.error || 'Błąd'}</p>}
    </div>
  )
}
