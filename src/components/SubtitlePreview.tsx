// Subtitle preview + transcript editor for the Napisy view.
//
// A source-video player sits on top with a burned-in caption overlay synced to the
// playhead. A language switcher lets you flip between the original transcript and any
// generated/translated subtitle track to check that the wording fits the picture.
//
// Below the player is the transcript: when "Oryginał" is selected each line is an
// editable textarea (fix Whisper mistakes here — the translations are regenerated from
// the corrected original), and every line is clickable to jump the film to that moment.
// When a translated language is selected the lines are read-only (still click-to-seek),
// since translations are derived from the original, not edited directly.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Play, Pause, Languages, Pencil } from 'lucide-react'
import type { Seg } from './SegmentEditor'

export type SubTrack = { language: string; vttUrl: string }
type Cue = { start: number; end: number; text: string }

const ORIG = '__orig__'

function fmtT(sec: number) {
  const v = Number.isFinite(sec) ? Math.max(0, sec) : 0
  const m = Math.floor(v / 60)
  const s = Math.floor(v % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

// Parse a WebVTT (or close-enough SRT) blob into cues. Tolerates `.` or `,` decimals
// and optional hours; ignores the WEBVTT header, NOTE blocks and numeric cue ids.
function parseVtt(raw: string): Cue[] {
  const cues: Cue[] = []
  const toSec = (t: string): number => {
    const m = t.trim().replace(',', '.').match(/(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)/)
    if (!m) return NaN
    const h = m[1] ? parseInt(m[1], 10) : 0
    return h * 3600 + parseInt(m[2], 10) * 60 + parseFloat(m[3])
  }
  const blocks = raw.replace(/\r/g, '').split(/\n\s*\n/)
  for (const block of blocks) {
    const lines = block.split('\n').filter((l) => l.trim() !== '')
    const tl = lines.find((l) => l.includes('-->'))
    if (!tl) continue
    const [a, b] = tl.split('-->')
    const start = toSec(a)
    const end = toSec((b || '').trim().split(/\s+/)[0] || '')
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue
    const text = lines.slice(lines.indexOf(tl) + 1).join('\n').trim()
    if (text) cues.push({ start, end, text })
  }
  return cues
}

export function SubtitlePreview({
  videoUrl, original, onOriginal, originalLabel, tracks, readOnly = false, sourceMissing = false,
}: {
  videoUrl: string
  original: Seg[]
  onOriginal: (s: Seg[]) => void
  originalLabel: string
  tracks: SubTrack[]
  readOnly?: boolean
  sourceMissing?: boolean
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const rowsRef = useRef<HTMLDivElement | null>(null)
  const rowElsRef = useRef<Map<string, HTMLDivElement>>(new Map())
  const [curT, setCurT] = useState(0)
  const [dur, setDur] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [sel, setSel] = useState<string>(ORIG)
  const [videoError, setVideoError] = useState(false)
  const [cueCache, setCueCache] = useState<Record<string, Cue[]>>({})
  const [loading, setLoading] = useState(false)

  useEffect(() => { setVideoError(false) }, [videoUrl])
  // If the selected translated track disappears (e.g. project reset), fall back to original.
  useEffect(() => {
    if (sel !== ORIG && !tracks.some((t) => t.language === sel)) setSel(ORIG)
  }, [tracks, sel])

  // Lazily fetch + parse the VTT for the selected translated track.
  useEffect(() => {
    if (sel === ORIG) return
    const t = tracks.find((x) => x.language === sel)
    if (!t || cueCache[t.vttUrl]) return
    let cancelled = false
    setLoading(true)
    fetch(t.vttUrl)
      .then((r) => r.text())
      .then((txt) => { if (!cancelled) setCueCache((c) => ({ ...c, [t.vttUrl]: parseVtt(txt) })) })
      .catch(() => { if (!cancelled) setCueCache((c) => ({ ...c, [t.vttUrl]: [] })) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [sel, tracks, cueCache])

  const isOrig = sel === ORIG
  const editing = isOrig && !readOnly
  const cues: Cue[] = useMemo(() => {
    if (isOrig) return original.map((s) => ({ start: s.start, end: s.end, text: s.text }))
    const t = tracks.find((x) => x.language === sel)
    return (t && cueCache[t.vttUrl]) || []
  }, [isOrig, sel, original, tracks, cueCache])

  // --- player ---------------------------------------------------------------
  const seek = useCallback((t: number, andPlay = false) => {
    const v = videoRef.current
    if (!v) { setCurT(t); return }
    try { v.currentTime = Math.max(0, t) } catch { /* not seekable yet */ }
    setCurT(Math.max(0, t))
    if (andPlay) v.play().catch(() => {})
  }, [])

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return
    if (v.paused) v.play().catch(() => {}); else v.pause()
  }

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onTime = () => setCurT(v.currentTime)
    const onMeta = () => setDur(v.duration || 0)
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    v.addEventListener('timeupdate', onTime)
    v.addEventListener('loadedmetadata', onMeta)
    v.addEventListener('play', onPlay)
    v.addEventListener('pause', onPause)
    return () => {
      v.removeEventListener('timeupdate', onTime)
      v.removeEventListener('loadedmetadata', onMeta)
      v.removeEventListener('play', onPlay)
      v.removeEventListener('pause', onPause)
    }
  }, [])

  // Active cue = the LAST one whose window contains the current time. Cues are sorted by
  // start, so when the previous line's end touches this line's start (boundary, within the
  // ±0.05s tolerance) the later cue wins — otherwise clicking a line highlighted the one above.
  const activeIdx = useMemo(() => {
    let best = -1
    for (let i = 0; i < cues.length; i++) {
      if (curT >= cues[i].start - 0.05 && curT <= cues[i].end + 0.05) best = i
    }
    return best
  }, [cues, curT])

  const overlay = useMemo(() => (activeIdx >= 0 ? cues[activeIdx].text : ''), [cues, activeIdx])

  const activeRowKey = editing
    ? (activeIdx >= 0 && original[activeIdx] ? `orig-${original[activeIdx].id}` : '')
    : (activeIdx >= 0 ? `cue-${activeIdx}` : '')

  const bindRow = useCallback((key: string) => (el: HTMLDivElement | null) => {
    if (el) rowElsRef.current.set(key, el)
    else rowElsRef.current.delete(key)
  }, [])

  useLayoutEffect(() => {
    if (!playing || !activeRowKey) return
    const list = rowsRef.current
    const row = rowElsRef.current.get(activeRowKey)
    if (!list || !row) return
    const focused = document.activeElement
    if (focused instanceof HTMLTextAreaElement && list.contains(focused)) return
    const listRect = list.getBoundingClientRect()
    const rowRect = row.getBoundingClientRect()
    const delta = (rowRect.top - listRect.top) + (rowRect.height / 2) - (list.clientHeight / 2)
    if (Math.abs(delta) < 2) return
    const maxTop = Math.max(0, list.scrollHeight - list.clientHeight)
    list.scrollTop = Math.max(0, Math.min(maxTop, list.scrollTop + delta))
  }, [activeRowKey, playing])

  const total = dur > 0 ? dur : Math.max(1, ...cues.map((c) => c.end))
  const editLine = (id: number, text: string) =>
    onOriginal(original.map((s) => (s.id === id ? { ...s, text } : s)))

  return (
    <div className="sub-prev">
      <div className="sub-prev-main">
        <div className="scene-player-wrap">
          <video ref={videoRef} src={videoUrl || undefined} className="scene-player" playsInline preload="metadata" onError={() => setVideoError(true)} />
          {(sourceMissing || !videoUrl) ? (
            <div className="scene-player-empty scene-player-err">
              Plik źródłowy nie istnieje już na dysku — film usunięto lub przeniesiono. Napisy obok możesz nadal przeglądać i pobierać.
            </div>
          ) : videoError && (
            <div className="scene-player-empty scene-player-err">
              Nie udało się załadować podglądu wideo — plik źródłowy mógł zostać usunięty lub ma nieobsługiwany format. Napisy obok edytujesz i pobierasz normalnie.
            </div>
          )}
          {overlay && !videoError && videoUrl && !sourceMissing && <div className="scene-caption"><span>{overlay}</span></div>}
        </div>

        <div className="scene-transport">
          <button type="button" className="ghost-btn icon-btn" onClick={togglePlay} title={playing ? 'Pauza' : 'Odtwórz'}>
            {playing ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <span className="scene-clock">{fmtT(curT)} <em>/ {fmtT(total)}</em></span>
          {loading && <span className="sub-prev-loading">Wczytywanie napisów…</span>}
        </div>
        <input className="scene-scrub" type="range" min={0} max={Math.max(total, 1)} step={0.05}
          value={Math.min(curT, total)} onChange={(e) => seek(Number(e.target.value), false)} />

        <div className="sub-prev-langs">
          <Languages size={14} className="sub-prev-langs-ic" />
          <button type="button" className={isOrig ? 'lang-pill on' : 'lang-pill'} onClick={() => setSel(ORIG)}>
            {originalLabel}
          </button>
          {tracks.map((t) => (
            <button type="button" key={t.language} className={sel === t.language ? 'lang-pill on' : 'lang-pill'} onClick={() => setSel(t.language)}>
              {t.language}
            </button>
          ))}
        </div>
      </div>

      <div className="sub-prev-side">
      <div className="sub-prev-list" ref={rowsRef}>
        {editing ? (
          original.map((s) => (
            <div
              className={`sub-prev-row${activeIdx >= 0 && original[activeIdx]?.id === s.id ? ' active' : ''}`}
              key={s.id}
              ref={bindRow(`orig-${s.id}`)}
            >
              <button type="button" className="sub-prev-time" onClick={() => seek(s.start, false)} title="Przewiń film tutaj">
                {fmtT(s.start)}
              </button>
              <textarea className="seg-text" rows={1} value={s.text}
                onFocus={() => seek(s.start, false)}
                onChange={(e) => editLine(s.id, e.target.value)} />
            </div>
          ))
        ) : cues.length ? (
          cues.map((c, i) => (
            <div
              className={`sub-prev-row${i === activeIdx ? ' active' : ''}`}
              key={i}
              ref={bindRow(`cue-${i}`)}
              onClick={() => seek(c.start, false)}
            >
              <button type="button" className="sub-prev-time" onClick={(e) => { e.stopPropagation(); seek(c.start, false) }} title="Przewiń film tutaj">
                {fmtT(c.start)}
              </button>
              <div className="sub-prev-ro">{c.text}</div>
            </div>
          ))
        ) : (
          <p className="sub-hint">{loading ? 'Wczytywanie napisów…' : 'Brak napisów dla tego języka.'}</p>
        )}
      </div>
      <p className="sub-prev-foot">
        {editing
          ? <><Pencil size={12} /> Popraw tu błędy Whispera — kliknij znacznik czasu, by sprawdzić fragment na filmie. Tłumaczenia powstają z poprawionego oryginału.</>
          : readOnly && isOrig
            ? <>Kliknij znacznik czasu, by przewinąć film. Przełącz język, by sprawdzić wybrane napisy.</>
            : <>Podgląd tłumaczenia. Aby je zmienić, popraw oryginał i wygeneruj napisy ponownie.</>}
      </p>
      </div>
    </div>
  )
}
