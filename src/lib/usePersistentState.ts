import { useEffect, useRef, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'

/**
 * useState that survives view switches AND full app reloads by mirroring the
 * value to localStorage. Used for in-progress work (transcripts, translations,
 * typed text, source selections) so the user never loses what they started.
 */
export function usePersistentState<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw !== null ? (JSON.parse(raw) as T) : initial
    } catch {
      return initial
    }
  })
  // Debounce writes a touch so rapid edits (typing) don't thrash localStorage.
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* ignore quota */ }
    }, 250)
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [key, value])
  return [value, setValue]
}
