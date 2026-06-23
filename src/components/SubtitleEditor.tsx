// Word-level subtitle editor. Each word keeps its Whisper start/end timestamp, so
// correcting the text NEVER shifts subtitle↔speech timing — the render uses the exact
// same word boundaries that were detected in the audio. Segment text is rebuilt from
// its words on save.
//
// A source-video player sits on top: focus (click) a word and the film jumps to it and
// shows it as a caption; an audio-preview track under the player holds a movable /
// resizable window (±5 s around the word by default) that loops so you can hear exactly
// the snippet you are correcting — or play the whole film.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Captions, X, Clapperboard, Save, RotateCcw, Play, Pause, Repeat } from 'lucide-react'
import { api } from '../lib/api'
import { WaveTrack } from './WaveTrack'
import { FilmTimeline } from './FilmTimeline'

export type EditWord = { word: string; start: number; end: number }
export type EditSegment = { start_time: number; end_time: number; text?: string }

const PAD_DEFAULT = 5 // ±seconds the preview window opens around a focused word
const TIME_NUDGE = 0.1

function fmtT(sec: number) {
  const v = Number.isFinite(sec) ? Math.max(0, sec) : 0
  const m = Math.floor(v / 60)
  const s = (v % 60)
  return `${m}:${s.toFixed(1).padStart(4, '0')}`
}
function fmtEditT(sec: number) {
  const v = Number.isFinite(sec) ? Math.max(0, sec) : 0
  const m = Math.floor(v / 60)
  return `${m}:${(v % 60).toFixed(2).padStart(5, '0')}`
}
function parseEditT(value: string): number | null {
  const text = value.trim().replace(',', '.')
  if (!text) return null
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text)
  const match = text.match(/^(\d+):(\d{1,2}(?:\.\d+)?)$/)
  if (!match) return null
  const seconds = Number(match[2])
  return seconds < 60 ? Number(match[1]) * 60 + seconds : null
}
function wordWidth(word: string) {
  const len = Math.max(4, word.length + 3)
  return `min(100%, calc(${len}ch + 18px))`
}

export function SubtitleEditor({
  title, segments, words, sourceUrl, projectId, fetchPeaks: peakFetcher, saving, onClose, onSave,
}: {
  title?: string
  segments: EditSegment[]
  words: EditWord[]
  sourceUrl?: string
  projectId?: string
  fetchPeaks?: (start: number, end: number, buckets: number) => Promise<{ start: number; end: number; peaks: number[] }>
  saving: boolean
  onClose: () => void
  onSave: (data: { segments: EditSegment[]; words: EditWord[] }, thenRender: boolean) => void
}) {
  const [draft, setDraft] = useState<EditWord[]>(() => words.map((w) => ({ ...w })))
  const [focused, setFocused] = useState<number | null>(null)
  const [win, setWin] = useState<{ start: number; end: number }>({ start: 0, end: 0 })
  const [vidDuration, setVidDuration] = useState(0)
  const [curT, setCurT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loopFrag, setLoopFrag] = useState(true)
  const [videoError, setVideoError] = useState(false)
  const [timeText, setTimeText] = useState({ start: '', end: '' })

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const draftRef = useRef(draft); draftRef.current = draft
  const winRef = useRef(win); winRef.current = win
  const loopRef = useRef(loopFrag); loopRef.current = loopFrag
  const focusedRef = useRef(focused); focusedRef.current = focused
  const durationRef = useRef(0)

  useEffect(() => { setVideoError(false) }, [sourceUrl])

  const hasVideo = Boolean(sourceUrl)
  const duration = useMemo(() => {
    const lastEnd = Math.max(0, ...words.map((w) => w.end), 1)
    return vidDuration > 0 ? vidDuration : lastEnd + 5
  }, [vidDuration, words])
  durationRef.current = duration

  // Assign each word to a segment by its (stable) timestamp.
  const groups = useMemo(() => {
    const assigned = new Set<number>()
    const g = segments.map((seg) => {
      const idxs: number[] = []
      words.forEach((w, i) => {
        if (assigned.has(i)) return
        if (w.start >= seg.start_time - 0.5 && w.end <= seg.end_time + 0.5) { idxs.push(i); assigned.add(i) }
      })
      return { seg, idxs }
    })
    const leftover = words.map((_, i) => i).filter((i) => !assigned.has(i))
    if (leftover.length) g.push({ seg: { start_time: 0, end_time: 0, text: '' }, idxs: leftover })
    return g
  }, [segments, words])

  const dirty = useMemo(() => draft.some((w, i) => (
    w.word !== words[i]?.word || w.start !== words[i]?.start || w.end !== words[i]?.end
  )), [draft, words])

  const setWord = (i: number, value: string) => setDraft((d) => d.map((w, k) => (k === i ? { ...w, word: value } : w)))
  const setWordTime = (i: number, key: 'start' | 'end', value: number) => {
    if (!Number.isFinite(value)) return
    setDraft((current) => {
      const next = current.map((word) => ({ ...word }))
      const word = next[i]
      if (!word) return current
      const previousEnd = i > 0 ? next[i - 1].end : 0
      if (key === 'start') {
        word.start = Math.max(previousEnd, value)
        word.end = Math.max(word.end, word.start + 0.04)
      } else {
        word.end = Math.max(word.start + 0.04, value)
      }
      // Whisper occasionally supplies a zero-length word between two words that
      // share a boundary. Preserve the user's edit and push following boundaries
      // forward just enough to keep every word non-overlapping.
      for (let index = i + 1; index < next.length; index++) {
        const previous = next[index - 1]
        const following = next[index]
        const oldDuration = Math.max(0.04, following.end - following.start)
        if (following.start < previous.end) {
          following.start = previous.end
          following.end = following.start + oldDuration
        } else {
          following.end = Math.max(following.end, following.start + 0.04)
        }
      }
      return next.map((item) => ({ ...item, start: Number(item.start.toFixed(3)), end: Number(item.end.toFixed(3)) }))
    })
  }
  const nudgeWordTime = (i: number, key: 'start' | 'end', delta: number) => {
    const current = draftRef.current[i]
    if (!current) return
    const requested = (key === 'start' ? current.start : current.end) + delta
    const previousEnd = i > 0 ? draftRef.current[i - 1].end : 0
    const start = key === 'start' ? Math.max(previousEnd, requested) : current.start
    const end = key === 'start' ? Math.max(current.end, start + 0.04) : Math.max(current.start + 0.04, requested)
    setWordTime(i, key, requested)
    // Buttons used to change the internal draft while the controlled text input
    // kept showing its old value. Update both visible values in the same click.
    setTimeText({ start: fmtEditT(start), end: fmtEditT(end) })
  }
  const reset = () => setDraft(words.map((w) => ({ ...w })))

  const build = () => {
    const segs = groups
      .filter((grp) => grp.seg.end_time > 0 || grp.idxs.length === 0)
      .map((grp) => ({ ...grp.seg, text: grp.idxs.map((i) => draft[i].word).join(' ').trim() }))
      .filter((seg) => seg.end_time > 0)
    return { segments: segs.length ? segs : segments, words: draft }
  }

  // --- player ---------------------------------------------------------------
  const seek = useCallback((t: number, andPlay = false) => {
    const v = videoRef.current
    if (!v) { setCurT(t); return }
    try { v.currentTime = Math.max(0, t) } catch { /* not seekable yet */ }
    setCurT(Math.max(0, t))
    if (andPlay) v.play().catch(() => {})
  }, [])

  const focusWord = useCallback((i: number, play = false) => {
    setFocused(i)
    const w = draftRef.current[i]
    if (!w) return
    const start = Math.max(0, w.start - PAD_DEFAULT)
    const end = Math.min(durationRef.current, w.end + PAD_DEFAULT)
    setWin({ start, end })
    setTimeText({ start: fmtEditT(w.start), end: fmtEditT(w.end) })
    seek(w.start, play)
  }, [seek])

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return
    if (v.paused) {
      // start at the window beginning so the snippet plays from the top
      if (loopRef.current && focusedRef.current != null && (v.currentTime < winRef.current.start || v.currentTime >= winRef.current.end)) {
        try { v.currentTime = winRef.current.start } catch { /* ignore */ }
      }
      v.play().catch(() => {})
    } else v.pause()
  }

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onTime = () => {
      const w = winRef.current
      if (loopRef.current && focusedRef.current != null && w.end > w.start) {
        if (v.currentTime >= w.end - 0.03) { try { v.currentTime = w.start } catch { /* ignore */ } }
      }
      setCurT(v.currentTime)
    }
    const onMeta = () => setVidDuration(v.duration || 0)
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

  // rolling caption — words active around the playhead, focused word emphasised
  const overlay = useMemo(() => {
    const active = draft.filter((w) => w.start <= curT + 0.12 && w.end >= curT - 1.2)
    if (active.length) return active.map((w) => w.word).join(' ').replace(/\s+/g, ' ').trim()
    const f = focused != null ? draft[focused] : null
    return f ? f.word : ''
  }, [draft, curT, focused])

  // Segment the playhead currently sits in — highlighted on the film timeline.
  const activeSegIdx = useMemo(() => {
    for (let i = 0; i < segments.length; i++) {
      if (curT >= segments[i].start_time && curT < segments[i].end_time) return i
    }
    return -1
  }, [segments, curT])

  // --- preview-window waveform (real audio) --------------------------------
  const focusedW = focused != null ? draft[focused] : null
  const previousWord = focused != null && focused > 0 ? draft[focused - 1] : null
  const nextWord = focused != null && focused < draft.length - 1 ? draft[focused + 1] : null
  const fetchPeaks = useCallback(
    (s: number, e: number, b: number) => peakFetcher ? peakFetcher(s, e, b) : api.shortsPeaks(projectId || '', s, e, b),
    [peakFetcher, projectId],
  )

  return (
    <div className="modal-overlay" onClick={() => !saving && onClose()}>
      <div className={`modal-card editor-card ${hasVideo ? 'sub-card-pro' : ''}`} onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Captions size={18} />
          <h3>Edytor napisów{title ? ` — ${title}` : ''}</h3>
          <button type="button" className="ghost-btn icon-btn editor-x" disabled={saving} onClick={onClose} title="Zamknij edytor">
            <X size={15} />
          </button>
        </header>
        <p className="modal-desc">
          Kliknij słowo, by zobaczyć je na filmie i odsłuchać fragment wokół niego. Możesz poprawić tekst oraz
          jego timing; sąsiednie granice pozostają bez nakładania.
        </p>

        {hasVideo && (
          <div className="sub-stage">
            <div className="scene-player-wrap">
              <video ref={videoRef} src={sourceUrl} className="scene-player" playsInline preload="metadata" onError={() => setVideoError(true)} />
              {videoError && (
                <div className="scene-player-empty scene-player-err">
                  Nie udało się załadować podglądu wideo — oryginalny plik źródłowy mógł zostać usunięty z dysku lub ma nieobsługiwany format. Tekst napisów edytujesz normalnie poniżej (czasy słów zachowane).
                </div>
              )}
              {overlay && !videoError && <div className="scene-caption"><span>{overlay}</span></div>}
            </div>
            <div className="scene-transport">
              <button type="button" className="ghost-btn icon-btn" onClick={togglePlay} title={playing ? 'Pauza' : 'Odtwórz'}>
                {playing ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <button type="button" className={`ghost-btn icon-btn ${loopFrag ? 'is-on' : ''}`} onClick={() => setLoopFrag((v) => !v)} title="Zapętl fragment wokół słowa">
                <Repeat size={16} />
              </button>
              <span className="scene-clock">{fmtT(curT)} <em>/ {fmtT(duration)}</em></span>
              {focusedW && <span className="sub-focus-label">„{focusedW.word}” · okno {fmtT(win.start)}–{fmtT(win.end)}</span>}
            </div>
            <input className="scene-scrub" type="range" min={0} max={Math.max(duration, 1)} step={0.05}
              value={Math.min(curT, duration)} onChange={(e) => seek(Number(e.target.value), false)} />
            <FilmTimeline
              duration={duration}
              scenes={segments}
              selected={activeSegIdx}
              curT={curT}
              playing={playing}
              onSeek={(t) => seek(t, false)}
              onSelectScene={(i) => { const s = segments[i]; if (s) seek(s.start_time, false) }}
            />
            <WaveTrack
              fetchPeaks={fetchPeaks}
              duration={duration}
              start={win.start}
              end={win.end}
              playhead={curT}
              onChange={(s, e) => setWin({ start: s, end: e })}
              onSeek={(t) => seek(t, false)}
              height={48}
            />
            {focusedW && (
              <div className="word-timing-panel">
                <div className="word-timing-head">
                  <strong>Timing słowa „{focusedW.word || '—'}”</strong>
                  <span>bez nakładania na sąsiednie słowa</span>
                </div>
                <div className="word-timing-grid">
                  <label>Start (m:ss.xx)
                    <div className="word-timing-input"><button type="button" onClick={() => focused != null && nudgeWordTime(focused, 'start', -TIME_NUDGE)} aria-label="Odejmij 0,1 sekundy">−</button><input type="text" inputMode="decimal" value={timeText.start || fmtEditT(focusedW.start)}
                      onChange={(e) => { const value = e.target.value; setTimeText((t) => ({ ...t, start: value })); const parsed = parseEditT(value); if (focused != null && parsed != null) setWordTime(focused, 'start', parsed) }}
                      onBlur={() => setTimeText((t) => ({ ...t, start: fmtEditT(draftRef.current[focused ?? 0]?.start ?? focusedW.start) }))} /><button type="button" onClick={() => focused != null && nudgeWordTime(focused, 'start', TIME_NUDGE)} aria-label="Dodaj 0,1 sekundy">+</button></div>
                  </label>
                  <label>Koniec (m:ss.xx)
                    <div className="word-timing-input"><button type="button" onClick={() => focused != null && nudgeWordTime(focused, 'end', -TIME_NUDGE)} aria-label="Odejmij 0,1 sekundy">−</button><input type="text" inputMode="decimal" value={timeText.end || fmtEditT(focusedW.end)}
                      onChange={(e) => { const value = e.target.value; setTimeText((t) => ({ ...t, end: value })); const parsed = parseEditT(value); if (focused != null && parsed != null) setWordTime(focused, 'end', parsed) }}
                      onBlur={() => setTimeText((t) => ({ ...t, end: fmtEditT(draftRef.current[focused ?? 0]?.end ?? focusedW.end) }))} /><button type="button" onClick={() => focused != null && nudgeWordTime(focused, 'end', TIME_NUDGE)} aria-label="Dodaj 0,1 sekundy">+</button></div>
                  </label>
                  <div className="word-timing-neighbour">Poprzednie kończy się: <b>{previousWord ? fmtT(previousWord.end) : 'początek filmu'}</b></div>
                  <div className="word-timing-neighbour">Następne zaczyna się: <b>{nextWord ? fmtT(nextWord.start) : 'koniec filmu'}</b></div>
                </div>
              </div>
            )}
            {!focusedW && <p className="sub-hint">Kliknij dowolne słowo poniżej, aby ustawić podgląd.</p>}
          </div>
        )}

        <div className="editor-segments">
          {groups.map((grp, gi) => (
            <div className="editor-seg" key={gi}>
              <div className="editor-seg-time">
                {grp.seg.end_time > 0 ? `${fmtT(grp.seg.start_time)} – ${fmtT(grp.seg.end_time)}` : 'Pozostałe słowa'}
              </div>
              <div className="editor-words">
                {grp.idxs.map((i) => (
                  <input
                    key={i}
                    className={`editor-word ${draft[i].word !== words[i]?.word ? 'edited' : ''} ${focused === i ? 'focused' : ''}`}
                    value={draft[i].word}
                    title={`${fmtT(draft[i].start)} – ${fmtT(draft[i].end)}`}
                    style={{ width: wordWidth(draft[i].word) }}
                    onFocus={() => focusWord(i)}
                    onClick={() => focusWord(i)}
                    onChange={(e) => setWord(i, e.target.value)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="modal-actions">
          <button type="button" className="ghost-btn" disabled={saving || !dirty} onClick={reset}>
            <RotateCcw size={14} /> Cofnij zmiany
          </button>
          <span className="editor-spacer" />
          <button type="button" className="ghost-btn" disabled={saving} onClick={onClose}>Anuluj</button>
          <button type="button" className="ghost-btn" disabled={saving} onClick={() => onSave(build(), false)}>
            <Save size={14} /> Zapisz
          </button>
          <button type="button" className="primary-btn" disabled={saving} onClick={() => onSave(build(), true)}>
            <Clapperboard size={14} /> {saving ? 'Zapisywanie…' : 'Zapisz i renderuj'}
          </button>
        </div>
      </div>
    </div>
  )
}
