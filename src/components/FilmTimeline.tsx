// Zoomable full-film timeline for the scene editor. Shows every scene as a numbered
// block (1, 2, 3 …) positioned at its real moment in the film, a moving playhead, and
// time ticks. Wheel (or the +/− buttons) zooms from the whole film all the way down to a
// few seconds; dragging the background pans. Clicking a scene block selects it; clicking
// empty space seeks the player there. Same stable-listener pattern as WaveTrack so drags
// never stall.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'

export type TLScene = { start_time: number; end_time: number }

const DRAG_THRESHOLD = 3
const MIN_SPAN = 1.5 // closest zoom (seconds visible)

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)) }
function fmt(t: number) {
  const v = Math.max(0, t)
  const m = Math.floor(v / 60)
  const s = Math.floor(v % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
function pickTick(span: number) {
  const targets = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
  const want = span / 7
  for (const t of targets) if (t >= want) return t
  return 3600
}

export function FilmTimeline({
  duration, scenes, selected, curT, playing = false, onSeek, onSelectScene, height = 48,
}: {
  duration: number
  scenes: TLScene[]
  selected: number
  curT: number
  playing?: boolean
  onSeek: (t: number) => void
  onSelectScene: (i: number) => void
  height?: number
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [view, setView] = useState<[number, number]>(() => [0, Math.max(1, duration)])

  const viewRef = useRef(view); viewRef.current = view
  const durRef = useRef(duration); durRef.current = duration
  const scenesRef = useRef(scenes); scenesRef.current = scenes
  const selRef = useRef(selected); selRef.current = selected
  const curRef = useRef(curT); curRef.current = curT
  const onSeekRef = useRef(onSeek); onSeekRef.current = onSeek
  const onSelRef = useRef(onSelectScene); onSelRef.current = onSelectScene

  // When the real film duration first arrives (video metadata), open to the full film.
  const lastDurRef = useRef(duration)
  useEffect(() => {
    if (duration <= 0) return
    const [vs, ve] = viewRef.current
    const wasFull = Math.abs(vs) < 0.01 && Math.abs(ve - lastDurRef.current) < 0.5
    if (ve <= 1.0001 || wasFull) setView([0, duration])
    else if (ve > duration) setView([Math.max(0, duration - (ve - vs)), duration])
    lastDurRef.current = duration
  }, [duration])

  // --- draw -----------------------------------------------------------------
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const dpr = window.devicePixelRatio || 1
    const W = wrap.clientWidth, H = height
    if (canvas.width !== Math.floor(W * dpr)) canvas.width = Math.floor(W * dpr)
    if (canvas.height !== Math.floor(H * dpr)) canvas.height = Math.floor(H * dpr)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)
    const [vs, ve] = viewRef.current
    const span = Math.max(0.001, ve - vs)
    const tToX = (t: number) => ((t - vs) / span) * W
    const blockTop = 14, blockH = H - blockTop - 2

    // ticks + labels
    const tick = pickTick(span)
    const first = Math.ceil(vs / tick) * tick
    ctx.font = '10px -apple-system, system-ui, sans-serif'
    ctx.textBaseline = 'top'
    for (let t = first; t <= ve; t += tick) {
      const x = tToX(t)
      ctx.fillStyle = 'rgba(255,255,255,0.10)'
      ctx.fillRect(x, blockTop, 1, blockH)
      ctx.fillStyle = 'rgba(255,255,255,0.45)'
      ctx.fillText(fmt(t), x + 3, 1)
    }

    // scene blocks
    const scn = scenesRef.current
    const sel = selRef.current
    for (let i = 0; i < scn.length; i++) {
      const s = scn[i].start_time, e = scn[i].end_time
      if (e < vs || s > ve) continue
      const x0 = Math.max(0, tToX(s))
      const x1 = Math.min(W, tToX(e))
      const w = Math.max(2, x1 - x0)
      const active = i === sel
      ctx.fillStyle = active ? 'rgba(124,108,255,0.9)' : 'rgba(124,108,255,0.42)'
      const r = 4
      ctx.beginPath()
      ctx.moveTo(x0 + r, blockTop)
      ctx.arcTo(x0 + w, blockTop, x0 + w, blockTop + blockH, r)
      ctx.arcTo(x0 + w, blockTop + blockH, x0, blockTop + blockH, r)
      ctx.arcTo(x0, blockTop + blockH, x0, blockTop, r)
      ctx.arcTo(x0, blockTop, x0 + w, blockTop, r)
      ctx.closePath()
      ctx.fill()
      if (active) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke() }
      // number label (inside if it fits, else just above the block)
      const label = String(i + 1)
      ctx.font = 'bold 11px -apple-system, system-ui, sans-serif'
      ctx.fillStyle = '#fff'
      ctx.textBaseline = 'middle'
      const tw = ctx.measureText(label).width
      if (w > tw + 8) {
        ctx.textAlign = 'center'
        ctx.fillText(label, x0 + w / 2, blockTop + blockH / 2)
      } else {
        ctx.textAlign = 'center'
        ctx.fillText(label, clamp(x0 + w / 2, 6, W - 6), blockTop - 6)
      }
      ctx.textAlign = 'left'
      ctx.textBaseline = 'top'
    }

    // playhead
    const cx = tToX(curRef.current)
    if (cx >= -1 && cx <= W + 1) {
      ctx.fillStyle = '#ff5a7a'
      ctx.fillRect(cx - 1, 0, 2, H)
    }
  }, [height])

  const rafRef = useRef(0)
  const scheduleDraw = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => { rafRef.current = 0; draw() })
  }, [draw])
  useEffect(() => { scheduleDraw() }, [view, scenes, selected, curT, scheduleDraw])
  useEffect(() => {
    const onResize = () => scheduleDraw()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [scheduleDraw])

  // --- interaction (listeners attached once, read from refs) ----------------
  const dragRef = useRef<null | { x0: number; vs0: number; span: number; rectW: number; moved: boolean }>(null)
  const pointerRef = useRef<{ clientX: number } | null>(null)
  const applyRafRef = useRef(0)

  const xToTime = (clientX: number, rectLeft: number, rectW: number) => {
    const [vs, ve] = viewRef.current
    return vs + clamp((clientX - rectLeft) / Math.max(1, rectW), 0, 1) * (ve - vs)
  }
  const sceneAt = (t: number) => {
    const scn = scenesRef.current
    for (let i = 0; i < scn.length; i++) if (t >= scn[i].start_time && t <= scn[i].end_time) return i
    return -1
  }

  const applyPan = useCallback(() => {
    const d = dragRef.current, pos = pointerRef.current
    pointerRef.current = null
    if (!d || !pos) return
    if (Math.abs(pos.clientX - d.x0) > DRAG_THRESHOLD) d.moved = true
    if (!d.moved) return
    const dur = durRef.current
    const delta = -((pos.clientX - d.x0) / Math.max(1, d.rectW)) * d.span
    const nvs = clamp(d.vs0 + delta, 0, Math.max(0, dur - d.span))
    setView([nvs, nvs + d.span])
  }, [])
  const scheduleApply = useCallback(() => {
    if (applyRafRef.current) return
    applyRafRef.current = requestAnimationFrame(() => { applyRafRef.current = 0; applyPan() })
  }, [applyPan])

  useEffect(() => {
    const move = (ev: PointerEvent) => { if (!dragRef.current) return; pointerRef.current = { clientX: ev.clientX }; scheduleApply() }
    const up = (ev: PointerEvent) => {
      const d = dragRef.current
      if (!d) return
      dragRef.current = null; pointerRef.current = null
      if (applyRafRef.current) { cancelAnimationFrame(applyRafRef.current); applyRafRef.current = 0 }
      if (!d.moved) {
        const rect = canvasRef.current!.getBoundingClientRect()
        const t = xToTime(ev.clientX, rect.left, rect.width)
        const i = sceneAt(t)
        if (i >= 0) { onSelRef.current(i) } else { onSeekRef.current(t) }
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  }, [scheduleApply])

  const onDown = (ev: React.PointerEvent) => {
    ev.preventDefault()
    const rect = canvasRef.current!.getBoundingClientRect()
    const [vs, ve] = viewRef.current
    dragRef.current = { x0: ev.clientX, vs0: vs, span: ve - vs, rectW: rect.width, moved: false }
  }

  const zoomAt = (factor: number, anchorFrac = 0.5) => {
    const dur = durRef.current
    const [vs, ve] = viewRef.current
    const span = ve - vs
    const anchor = vs + anchorFrac * span
    const nspan = clamp(span * factor, MIN_SPAN, Math.max(MIN_SPAN, dur))
    let nvs = anchor - anchorFrac * nspan
    nvs = clamp(nvs, 0, Math.max(0, dur - nspan))
    setView([nvs, nvs + nspan])
  }
  // The +/− buttons zoom toward the CURRENT PLAYHEAD and centre on it, so you don't have
  // to zoom-then-pan to find where playback is.
  const zoomToPlayhead = (factor: number) => {
    const dur = durRef.current
    const [vs, ve] = viewRef.current
    const nspan = clamp((ve - vs) * factor, MIN_SPAN, Math.max(MIN_SPAN, dur))
    const cur = clamp(curRef.current, 0, dur)
    const nvs = clamp(cur - nspan / 2, 0, Math.max(0, dur - nspan))
    setView([nvs, nvs + nspan])
  }
  const onWheel = (ev: React.WheelEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect()
    zoomAt(ev.deltaY > 0 ? 1.25 : 1 / 1.25, clamp((ev.clientX - rect.left) / rect.width, 0, 1))
  }

  // While playing AND zoomed in, keep the playhead in view (recentre when it nears an edge).
  useEffect(() => {
    if (!playing) return
    const [vs, ve] = viewRef.current
    const span = ve - vs
    const dur = durRef.current
    if (span >= dur - 0.01) return
    const margin = span * 0.12
    if (curT < vs + margin || curT > ve - margin) {
      const nvs = clamp(curT - span / 2, 0, Math.max(0, dur - span))
      setView([nvs, nvs + span])
    }
  }, [curT, playing])

  return (
    <div className="film-timeline">
      <div className="ft-wrap" ref={wrapRef} style={{ height }}>
        <canvas ref={canvasRef} className="ft-canvas" style={{ width: '100%', height }} onPointerDown={onDown} onWheel={onWheel} />
      </div>
      <div className="ft-ctrls">
        <button type="button" className="ghost-btn icon-btn" title="Cały film" onClick={() => setView([0, Math.max(1, durRef.current)])}><Maximize2 size={13} /></button>
        <button type="button" className="ghost-btn icon-btn" title="Przybliż do odtwarzanego momentu" onClick={() => zoomToPlayhead(1 / 1.6)}><ZoomIn size={13} /></button>
        <button type="button" className="ghost-btn icon-btn" title="Oddal od odtwarzanego momentu" onClick={() => zoomToPlayhead(1.6)}><ZoomOut size={13} /></button>
        <span className="ft-range">{fmt(view[0])}–{fmt(view[1])}</span>
      </div>
    </div>
  )
}
