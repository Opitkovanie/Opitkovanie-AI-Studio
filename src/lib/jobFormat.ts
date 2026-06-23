import { useEffect, useState } from 'react'
import type { JobState } from './useJob'

// Shared job-formatting helpers. Kept OUT of the JobProgress component file so that
// file only exports a component (Fast Refresh / react-refresh/only-export-components).

/** mm:ss (or h:mm:ss) from seconds. */
export function formatClock(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

/** " (czas: M:SS)" once a job finished — for the persistent notice. */
export function timeTag(state: JobState): string {
  if (state.startedAt && state.finishedAt && state.finishedAt > state.startedAt) {
    return ` (czas: ${formatClock((state.finishedAt - state.startedAt) / 1000)})`
  }
  return ''
}

/** Short label of the audio treatment used for a rendered version. */
export function audioModeTag(mode?: string): string {
  if (!mode) return ''
  if (mode === 'Lektor na oryginalnym audio' || String(mode).startsWith('Lektor')) return 'lektor'
  if (mode === 'Oryginalne audio') return ''
  return 'dubbing'
}

/** Ticks every second while `active` so elapsed/ETA stay live; idle otherwise. */
export function useTick(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}
