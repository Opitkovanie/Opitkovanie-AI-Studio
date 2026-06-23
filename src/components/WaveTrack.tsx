// Real-audio waveform region editor (canvas), modeled on OmniVoice Studio's AudioTrimmer.
// The visible window (`view`) is independent of the selected region [start,end], so dragging
// the pink handles VISIBLY moves them across a STABLE waveform (the old version recomputed
// the view from start/end every render, so handles never appeared to move). Peaks are the
// ACTUAL audio, fetched per window from the backend. Background drag pans the whole row.
import { useCallback, useEffect, useRef, useState } from 'react'

type PeaksFetcher = (start: number, end: number, buckets: number) => Promise<{ start: number; end: number; peaks: number[] }>

const MIN_LEN = 0.15
const EDGE_PX = 11
const DRAG_THRESHOLD = 3

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)) }

export function WaveTrack({
  fetchPeaks, duration, start, end, onChange, onSeek, onScrub, playhead, height = 64, allowRegionDrag = true,
}: {
  fetchPeaks: PeaksFetcher
  duration: number
  start: number
  end: number
  onChange: (start: number, end: number) => void
  onSeek?: (t: number) => void
  /** Fires true while a handle/region is being dragged, false on release — lets the parent
   *  suspend its loop so the playhead follows the dragged edge instead of snapping away. */
  onScrub?: (active: boolean) => void
  playhead?: number
  height?: number
  /** When false, dragging inside the selected range will not move the whole range.
   *  Scene cuts should be changed only by their handles, not by accidental region drags. */
  allowRegionDrag?: boolean
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [view, setView] = useState<[number, number]>(() => {
    const len = Math.max(MIN_LEN, end - start)
    const pad = Math.max(3, len * 0.6)
    return [Math.max(0, start - pad), Math.min(duration || end + pad, end + pad)]
  })
  const [peaks, setPeaks] = useState<number[]>([])

  // Refs mirror state for the window-attached drag handlers (no stale closures).
  const viewRef = useRef(view); viewRef.current = view
  const startRef = useRef(start); startRef.current = start
  const endRef = useRef(end); endRef.current = end
  const durRef = useRef(duration); durRef.current = duration
  const peaksRef = useRef(peaks); peaksRef.current = peaks
  const playheadRef = useRef(playhead); playheadRef.current = playhead
  // Callback refs so the (once-attached) drag listeners always call the LATEST
  // onChange/onSeek without being torn down when the parent passes new inline fns.
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange
  const onSeekRef = useRef(onSeek); onSeekRef.current = onSeek
  const onScrubRef = useRef(onScrub); onScrubRef.current = onScrub
  const allowRegionDragRef = useRef(allowRegionDrag); allowRegionDragRef.current = allowRegionDrag
  const suppressAutoPanRef = useRef(false)

  // Keep the region visible: if a numeric edit pushed an edge outside the window, pan it.
  useEffect(() => {
    if (suppressAutoPanRef.current) return
    const [vs, ve] = viewRef.current
    const span = ve - vs
    if (start < vs) setView([Math.max(0, start - span * 0.1), Math.max(0, start - span * 0.1) + span])
    else if (end > ve) {
      const nve = Math.min(duration, end + span * 0.1)
      setView([nve - span, nve])
    }
  }, [start, end, duration])

  // --- fetch real peaks for the current window (debounced + cached) ---------
  const cacheRef = useRef<Map<string, number[]>>(new Map())
  useEffect(() => {
    const [vs, ve] = view
    const w = wrapRef.current
    const buckets = clamp(Math.round((w?.clientWidth || 600)), 120, 1600)
    const key = `${vs.toFixed(2)}-${ve.toFixed(2)}-${buckets}`
    const cached = cacheRef.current.get(key)
    if (cached) { setPeaks(cached); return }
    let cancelled = false
    const t = window.setTimeout(async () => {
      try {
        const r = await fetchPeaks(vs, ve, buckets)
        if (cancelled) return
        cacheRef.current.set(key, r.peaks)
        if (cacheRef.current.size > 40) cacheRef.current.delete(cacheRef.current.keys().next().value as string)
        setPeaks(r.peaks)
      } catch { /* keep previous peaks */ }
    }, 110)
    return () => { cancelled = true; window.clearTimeout(t) }
  }, [view, fetchPeaks])

  // --- draw ----------------------------------------------------------------
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const dpr = window.devicePixelRatio || 1
    const cssW = wrap.clientWidth, cssH = height
    if (canvas.width !== Math.floor(cssW * dpr)) canvas.width = Math.floor(cssW * dpr)
    if (canvas.height !== Math.floor(cssH * dpr)) canvas.height = Math.floor(cssH * dpr)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)
    const [vs, ve] = viewRef.current
    const span = Math.max(0.001, ve - vs)
    const s = startRef.current, e = endRef.current
    const mid = cssH / 2
    const p = peaksRef.current
    const n = p.length
    const sx = ((s - vs) / span) * cssW
    const ex = ((e - vs) / span) * cssW
    // region background
    ctx.fillStyle = 'rgba(124,108,255,0.13)'
    ctx.fillRect(sx, 0, Math.max(0, ex - sx), cssH)
    // bars
    const barW = Math.max(1, cssW / Math.max(1, n))
    for (let i = 0; i < n; i++) {
      const x = (i / n) * cssW
      const t = vs + (i / n) * span
      const inside = t >= s && t <= e
      const amp = Math.max(0.02, p[i]) * (mid - 2)
      ctx.fillStyle = inside ? '#9d7bff' : 'rgba(255,255,255,0.16)'
      ctx.fillRect(x, mid - amp, Math.max(0.7, barW - 0.6), amp * 2)
    }
    // handles
    ctx.fillStyle = '#ff5a7a'
    ctx.fillRect(sx - 1.5, 0, 3, cssH)
    ctx.fillRect(ex - 1.5, 0, 3, cssH)
    // grab knobs
    ctx.fillStyle = '#fff'
    ctx.fillRect(sx - 1, mid - 7, 2, 14)
    ctx.fillRect(ex - 1, mid - 7, 2, 14)
    // playhead (current video time) — a bright vertical line so you see WHERE in
    // this scene's audio the player is, mirroring the left preview.
    const ph = playheadRef.current
    if (ph != null && Number.isFinite(ph) && ph >= vs && ph <= ve) {
      const px = ((ph - vs) / span) * cssW
      ctx.fillStyle = '#22e3c4'
      ctx.fillRect(px - 1, 0, 2, cssH)
      ctx.beginPath()
      ctx.arc(px, 5, 3.2, 0, Math.PI * 2)
      ctx.fill()
    }
  }, [height])

  const rafRef = useRef(0)
  const scheduleDraw = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => { rafRef.current = 0; draw() })
  }, [draw])

  useEffect(() => { scheduleDraw() }, [peaks, view, start, end, playhead, scheduleDraw])
  useEffect(() => {
    const onResize = () => scheduleDraw()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [scheduleDraw])

  // --- pointer interaction --------------------------------------------------
  // Drag state + latest pointer position live in refs so the window listeners
  // (attached ONCE below) never need to be re-bound when start/end/onChange
  // change. The actual mutation is applied in a requestAnimationFrame, exactly
  // like OmniVoice's AudioTrimmer — this is what makes dragging buttery and
  // stops the old "moves 0.1s then freezes" stall (which was the cleanup effect
  // tearing down the listeners on every parent re-render).
  const dragRef = useRef<null | {
    mode: 'start' | 'end' | 'region' | 'pan' | 'seek'
    rectLeft: number; rectW: number
    offset: number; regionLen: number
    panStartX: number; panViewStart: number; panSpan: number
    moved: boolean
  }>(null)
  const pointerRef = useRef<{ clientX: number } | null>(null)
  const applyRafRef = useRef(0)

  const xToTime = (clientX: number, rectLeft: number, rectW: number) => {
    const [vs, ve] = viewRef.current
    return vs + clamp((clientX - rectLeft) / Math.max(1, rectW), 0, 1) * (ve - vs)
  }

  const applyPointer = useCallback(() => {
    const d = dragRef.current
    const pos = pointerRef.current
    pointerRef.current = null
    if (!d || !pos) return
    if (Math.abs(pos.clientX - d.panStartX) > DRAG_THRESHOLD) d.moved = true
    const dur = durRef.current
    if (d.mode === 'pan') {
      const span = d.panSpan
      const delta = -((pos.clientX - d.panStartX) / Math.max(1, d.rectW)) * span
      const nvs = clamp(d.panViewStart + delta, 0, Math.max(0, dur - span))
      setView([nvs, nvs + span])
      return
    }
    if (d.mode === 'seek') return
    const t = xToTime(pos.clientX, d.rectLeft, d.rectW)
    if (d.mode === 'start') {
      const ns = clamp(t, 0, endRef.current - MIN_LEN)
      onChangeRef.current(ns, endRef.current); onSeekRef.current?.(ns)
    } else if (d.mode === 'end') {
      const ne = clamp(t, startRef.current + MIN_LEN, dur)
      onChangeRef.current(startRef.current, ne); onSeekRef.current?.(ne)
    } else if (d.mode === 'region') {
      const len = d.regionLen
      const ns = clamp(t - d.offset, 0, Math.max(0, dur - len))
      onChangeRef.current(ns, ns + len); onSeekRef.current?.(ns)
    }
  }, [])

  const scheduleApply = useCallback(() => {
    if (applyRafRef.current) return
    applyRafRef.current = requestAnimationFrame(() => { applyRafRef.current = 0; applyPointer() })
  }, [applyPointer])

  // Window listeners attached ONCE. They read everything from refs, so changing
  // start/end/onChange never re-binds them (the fix for the drag stall).
  useEffect(() => {
    const move = (ev: PointerEvent) => {
      if (!dragRef.current) return
      pointerRef.current = { clientX: ev.clientX }
      scheduleApply()
    }
    const up = (ev: PointerEvent) => {
      const d = dragRef.current
      if (!d) return
      dragRef.current = null
      pointerRef.current = null
      if (applyRafRef.current) { cancelAnimationFrame(applyRafRef.current); applyRafRef.current = 0 }
      if (d.mode !== 'pan' && d.mode !== 'seek') onScrubRef.current?.(false)
      window.setTimeout(() => { suppressAutoPanRef.current = false }, 180)
      // a click (no drag) on the waveform = seek the video there
      if (!d.moved && onSeekRef.current) onSeekRef.current(xToTime(ev.clientX, d.rectLeft, d.rectW))
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [scheduleApply])

  const onDown = (ev: React.PointerEvent) => {
    const canvas = canvasRef.current
    if (!canvas) return
    ev.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const [vs, ve] = viewRef.current
    const span = ve - vs
    const sx = ((startRef.current - vs) / span) * rect.width
    const ex = ((endRef.current - vs) / span) * rect.width
    const x = ev.clientX - rect.left
    let mode: 'start' | 'end' | 'region' | 'pan' | 'seek'
    if (Math.abs(x - sx) <= EDGE_PX) mode = 'start'
    else if (Math.abs(x - ex) <= EDGE_PX) mode = 'end'
    else if (x > sx && x < ex) mode = allowRegionDragRef.current ? 'region' : 'seek'
    else mode = 'pan'
    suppressAutoPanRef.current = true
    const t = xToTime(ev.clientX, rect.left, rect.width)
    dragRef.current = {
      mode, rectLeft: rect.left, rectW: rect.width,
      offset: t - startRef.current, regionLen: endRef.current - startRef.current,
      panStartX: ev.clientX, panViewStart: vs, panSpan: span,
      moved: false,
    }
    // Dragging an edge/region should make the playhead track the pointer — tell the parent
    // to suspend its scene loop so it doesn't snap the playhead back to the scene start.
    if (mode !== 'pan' && mode !== 'seek') {
      onScrubRef.current?.(true)
      if (mode === 'end') onSeekRef.current?.(endRef.current)
      else if (mode === 'start') onSeekRef.current?.(startRef.current)
    }
  }

  // Wheel = zoom the waveform around the cursor. Attached as a NATIVE, non-passive
  // listener (React's onWheel is passive, so preventDefault is ignored there) — otherwise
  // the wheel zooms AND scrolls the page/scene-list at the same time. stopPropagation keeps
  // the gesture local to the waveform; everywhere else the wheel scrolls the list normally.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheelNative = (ev: WheelEvent) => {
      ev.preventDefault()
      ev.stopPropagation()
      const dur = durRef.current
      const [vs, ve] = viewRef.current
      const span = ve - vs
      const factor = ev.deltaY > 0 ? 1.2 : 1 / 1.2
      const rect = canvas.getBoundingClientRect()
      const xFrac = clamp((ev.clientX - rect.left) / rect.width, 0, 1)
      const anchor = vs + xFrac * span
      const nspan = clamp(span * factor, 1, Math.max(1, dur))
      let nvs = anchor - xFrac * nspan
      nvs = clamp(nvs, 0, Math.max(0, dur - nspan))
      setView([nvs, nvs + nspan])
    }
    canvas.addEventListener('wheel', onWheelNative, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheelNative)
  }, [])

  return (
    <div className="wave-track" ref={wrapRef} style={{ height }}>
      <canvas
        ref={canvasRef}
        className="wave-canvas"
        style={{ width: '100%', height }}
        onPointerDown={onDown}
      />
    </div>
  )
}
