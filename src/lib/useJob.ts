import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamJob, type JobEvent } from './api'

export type JobState = {
  id: string | null
  status: 'idle' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number
  message: string
  log: JobEvent[]
  result: unknown
  error: string | null
  startedAt: number | null
  finishedAt: number | null
}

const INITIAL: JobState = {
  id: null,
  status: 'idle',
  progress: 0,
  message: '',
  log: [],
  result: null,
  error: null,
  startedAt: null,
  finishedAt: null,
}

function applyEvent(s: JobState, e: JobEvent): JobState {
  const next = { ...s, log: [...s.log, e].slice(-400) }
  if (e.type === 'progress') {
    if (typeof e.value === 'number') next.progress = e.value
    if (e.message) next.message = e.message
  } else if (e.type === 'log' && e.message) {
    next.message = e.message
  } else if (e.type === 'done') {
    next.status = 'done'
    next.progress = 1
    next.result = e.result
    next.finishedAt = Date.now()
  } else if (e.type === 'error') {
    next.status = 'error'
    next.error = e.message ?? 'Błąd'
    next.message = next.error
    next.finishedAt = Date.now()
  } else if (e.type === 'cancelled') {
    next.status = 'cancelled'
    next.message = 'Przerwano'
    next.finishedAt = Date.now()
  }
  return next
}

// `kind` ties the job to a persisted slot so it survives view switches AND full
// reloads: the work runs on the backend regardless of the UI, and we reconnect to
// its live SSE stream. The job only ends when it finishes or is cancelled — never
// because the user navigated away.
export function useJob(kind?: string) {
  const [state, setState] = useState<JobState>(INITIAL)
  const unsub = useRef<(() => void) | null>(null)
  const lastSeq = useRef(0)
  const storageKey = kind ? `dubcut.job.${kind}` : null

  const clearPersisted = useCallback(() => {
    if (storageKey) try { localStorage.removeItem(storageKey) } catch { /* ignore */ }
  }, [storageKey])

  const subscribe = useCallback((jobId: string) => {
    unsub.current?.()
    lastSeq.current = 0
    unsub.current = streamJob(jobId, (e) => {
      // Stream replays the whole log by seq on (re)connect — dedupe so a reconnect
      // or a stray double-subscribe can't append the same event twice.
      const seq = typeof e.seq === 'number' ? e.seq : null
      if (seq !== null) {
        if (seq <= lastSeq.current) return
        lastSeq.current = seq
      }
      setState((s) => applyEvent(s, e))
      if (e.type === 'done' || e.type === 'error' || e.type === 'cancelled' || e.type === 'end') {
        if (e.type !== 'end') clearPersisted()
      }
    })
  }, [clearPersisted])

  const start = useCallback(async (starter: () => Promise<{ job_id: string }>) => {
    setState({ ...INITIAL, status: 'running', message: 'Uruchamianie…', startedAt: Date.now() })
    try {
      const { job_id } = await starter()
      setState((s) => ({ ...s, id: job_id }))
      if (storageKey) try { localStorage.setItem(storageKey, job_id) } catch { /* ignore */ }
      subscribe(job_id)
    } catch (err) {
      clearPersisted()
      setState((s) => ({
        ...s,
        status: 'error',
        error: err instanceof Error ? err.message : 'Nie udało się uruchomić zadania',
      }))
    }
  }, [storageKey, subscribe, clearPersisted])

  // On mount, reattach to a still-running job started in a previous mount/session.
  useEffect(() => {
    if (!storageKey) return
    let saved: string | null = null
    try { saved = localStorage.getItem(storageKey) } catch { /* ignore */ }
    if (!saved) return
    let cancelled = false
    api.jobStatus(saved)
      .then((j: any) => {
        if (cancelled) return
        if (j?.status === 'running') {
          setState({ ...INITIAL, id: saved, status: 'running', message: 'Wznawianie…', startedAt: Date.now() })
          subscribe(saved!)
        } else {
          clearPersisted()
        }
      })
      .catch(() => clearPersisted())
    return () => { cancelled = true }
  }, [storageKey, subscribe, clearPersisted])

  const cancel = useCallback(() => {
    if (state.id) api.cancelJob(state.id).catch(() => {})
    setState((s) => s.status === 'running'
      ? { ...s, status: 'cancelled', message: 'Przerwano', finishedAt: Date.now() }
      : s)
    clearPersisted()
  }, [state.id, clearPersisted])

  const reset = useCallback(() => {
    unsub.current?.()
    clearPersisted()
    setState(INITIAL)
  }, [clearPersisted])

  return { state, start, cancel, reset }
}
