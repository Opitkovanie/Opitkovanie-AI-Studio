import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Clapperboard, Minus, Pause, Play, Plus, Repeat, RotateCcw, Save, Scissors, Trash2, X } from 'lucide-react'
import { api } from '../lib/api'
import { WaveTrack } from './WaveTrack'
import { FilmTimeline } from './FilmTimeline'

const EXTEND_STEP = 2 // seconds added/removed per nudge button

export type SceneSegment = { start_time: number; end_time: number; text?: string }
export type SceneWord = { word: string; start: number; end: number }

const MIN_LEN = 0.2 // shortest allowed scene (s)

function fmtT(sec: number) {
  const v = Number.isFinite(sec) ? Math.max(0, sec) : 0
  const m = Math.floor(v / 60)
  const s = v % 60
  return `${m}:${s.toFixed(1).padStart(4, '0')}`
}
function clamp(n: number) {
  return Number.isFinite(n) ? Math.max(0, n) : 0
}

export function SceneEditor({
  title, segments, words = [], globalWords = [], sourceUrl, projectId, saving, createMode = false, persistKey, onClose, onMinimize, onSave,
}: {
  title?: string
  segments: SceneSegment[]
  words?: SceneWord[]
  globalWords?: SceneWord[]
  sourceUrl?: string
  projectId?: string
  saving: boolean
  createMode?: boolean
  /** When set (custom-short flow), the in-progress draft is mirrored to localStorage so it
   *  survives unmount (switching modules) / minimise / app reload until the short is created. */
  persistKey?: string
  onClose: () => void
  /** When provided, the header gets a "minimise" button and a backdrop click hides (keeps the
   *  draft) instead of closing — so the user never loses work by accident. */
  onMinimize?: () => void
  onSave: (data: { segments: SceneSegment[]; restore?: boolean }, thenRender: boolean) => void
}) {
  const [draft, setDraft] = useState<SceneSegment[]>(() => {
    if (persistKey) {
      try {
        const raw = localStorage.getItem(persistKey)
        if (raw) { const parsed = JSON.parse(raw); if (Array.isArray(parsed)) return parsed as SceneSegment[] }
      } catch { /* fall through to fresh */ }
    }
    return segments.map((seg) => ({ ...seg }))
  })
  const [selected, setSelected] = useState(0)
  const [vidDuration, setVidDuration] = useState(0)
  const [curT, setCurT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loop, setLoop] = useState(true)
  const [videoError, setVideoError] = useState(false)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const draftRef = useRef(draft); draftRef.current = draft
  // True only when a mouse press STARTED on the modal backdrop itself — guards the
  // backdrop-click-to-close against drags that end over the overlay (waveform resize).
  const overlayPressRef = useRef(false)
  const selectedRef = useRef(selected); selectedRef.current = selected
  const loopRef = useRef(loop); loopRef.current = loop
  // True while a waveform handle/region is being dragged — suspends the scene loop so the
  // playhead follows the dragged edge instead of being snapped back to the scene start.
  const scrubbingRef = useRef(false)
  const durationRef = useRef(0)

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(segments), [draft, segments])

  // Mirror the draft to localStorage (debounced) so an unmount — switching modules in the
  // left rail, minimising, or an app reload — never discards an in-progress custom short.
  useEffect(() => {
    if (!persistKey) return
    const id = window.setTimeout(() => {
      try { localStorage.setItem(persistKey, JSON.stringify(draft)) } catch { /* ignore quota */ }
    }, 250)
    return () => window.clearTimeout(id)
  }, [persistKey, draft])

  const duration = useMemo(() => {
    const lastEnd = Math.max(0, ...draft.map((s) => clamp(s.end_time)), 1)
    return vidDuration > 0 ? vidDuration : lastEnd + 5
  }, [vidDuration, draft])
  durationRef.current = duration

  const setSeg = (idx: number, patch: Partial<SceneSegment>) =>
    setDraft((items) => items.map((seg, i) => (i === idx ? { ...seg, ...patch } : seg)))
  // Extend/shrink a scene edge by N seconds — lets you grow a scene FAR beyond the
  // waveform window that was visible on open (the WaveTrack re-centres to follow).
  const nudgeEdge = (idx: number, edge: 'start' | 'end', delta: number) => {
    setSelected(idx)
    setDraft((items) => items.map((seg, i) => {
      if (i !== idx) return seg
      if (edge === 'start') return { ...seg, start_time: clamp(Math.min(seg.start_time + delta, seg.end_time - MIN_LEN)) }
      return { ...seg, end_time: Math.max(seg.start_time + MIN_LEN, Math.min(seg.end_time + delta, durationRef.current || seg.end_time + delta)) }
    }))
    const seg = draftRef.current[idx]
    if (seg) seek(edge === 'start' ? Math.max(0, seg.start_time + (delta < 0 ? delta : 0)) : Math.min(durationRef.current || seg.end_time, seg.end_time + (delta > 0 ? delta : 0)), false)
  }
  const removeSeg = (idx: number) =>
    setDraft((items) => {
      const next = items.filter((_, i) => i !== idx)
      setSelected((sel) => Math.max(0, Math.min(sel, next.length - 1)))
      return next
    })
  const reset = () => { setDraft(segments.map((s) => ({ ...s }))); setSelected(0) }

  const build = () => ({
    segments: draft
      .map((seg) => ({ ...seg, start_time: clamp(Number(seg.start_time)), end_time: clamp(Number(seg.end_time)) }))
      .filter((seg) => seg.end_time > seg.start_time)
      .sort((a, b) => a.start_time - b.start_time),
  })

  // --- player control -------------------------------------------------------
  const seek = useCallback((t: number, andPlay = false) => {
    const v = videoRef.current
    if (!v) { setCurT(t); return }
    try { v.currentTime = Math.max(0, t) } catch { /* not seekable yet */ }
    setCurT(Math.max(0, t))
    if (andPlay) { v.play().catch(() => {}) }
  }, [])

  const selectScene = useCallback((idx: number, play = true) => {
    // Update the ref SYNCHRONOUSLY, before seeking. The timeupdate loop guard reads
    // selectedRef; if it still held the previous scene when we jump to the new one, it
    // would see currentTime past the OLD scene's end and snap playback back — so a second
    // scene only started after the first finished. Setting the ref first fixes that.
    selectedRef.current = idx
    setSelected(idx)
    const seg = draftRef.current[idx]
    if (seg) seek(seg.start_time, play)
  }, [seek])

  // Clicking a scene card (but not inputs/buttons/the waveform) makes it the looped one.
  const pickScene = (e: React.MouseEvent, idx: number) => {
    if ((e.target as HTMLElement).closest('input, button, .wave-track')) return
    selectScene(idx, true)
  }

  const togglePlay = () => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) v.play().catch(() => {}); else v.pause()
  }

  // Per-scene ▶ button: first press selects + plays that scene in a loop; pressing
  // it again while that same scene is already playing PAUSES (parity with the left
  // transport play/pause). Other scenes' button switches the loop to them.
  const toggleScenePlay = (idx: number) => {
    const v = videoRef.current
    if (!v) return
    if (selectedRef.current === idx && !v.paused) { v.pause(); return }
    selectScene(idx, true)
  }

  const addScene = () => {
    const at = videoRef.current ? videoRef.current.currentTime : curT
    const start = clamp(at)
    const end = Math.min(duration, start + 5)
    setDraft((items) => {
      const next = [...items, { start_time: start, end_time: Math.max(start + MIN_LEN, end), text: '' }]
      setSelected(next.length - 1)
      return next
    })
    seek(start, false)
  }

  // --- timeupdate: loop ONLY the selected scene + drive caption -------------
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onTime = () => {
      const seg = draftRef.current[selectedRef.current]
      if (!scrubbingRef.current && loopRef.current && seg && v.currentTime >= seg.end_time - 0.03) {
        try { v.currentTime = seg.start_time } catch { /* ignore */ }
      }
      setCurT(v.currentTime)
    }
    const onMeta = () => { setVidDuration(v.duration || 0); if (draftRef.current[0]) seek(draftRef.current[0].start_time, false) }
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
  }, [seek])

  // Rolling caption under the player — words active around the playhead. We use the full
  // transcript (globalWords) for complete coverage when extending scenes, but OVERLAY the
  // short's own (possibly subtitle-editor-edited) words on top by timestamp, so corrections
  // made in the subtitle editor are reflected here too.
  const captionWords = useMemo(() => {
    if (!globalWords.length) return words
    if (!words.length) return globalWords
    const edits = new Map(words.map((w) => [`${w.start}_${w.end}`, w.word]))
    return globalWords.map((w) => {
      const e = edits.get(`${w.start}_${w.end}`)
      return e !== undefined && e !== w.word ? { ...w, word: e } : w
    })
  }, [globalWords, words])
  const overlayText = useMemo(() => {
    if (!captionWords.length) {
      const seg = draft[selected]
      return seg?.text || ''
    }
    const active = captionWords.filter((w) => w.start <= curT + 0.12 && w.end >= curT - 1.4)
    if (active.length) return active.map((w) => w.word).join(' ').replace(/\s+/g, ' ').trim()
    const upcoming = captionWords.filter((w) => w.start > curT).slice(0, 6)
    return upcoming.map((w) => w.word).join(' ').trim()
  }, [captionWords, curT, draft, selected])

  // Text that a scene currently covers (updates live as you drag the handles), built
  // from the WHOLE film's words so dragging reveals subtitles before/after the cut.
  const sceneText = (seg: SceneSegment): string => {
    const src = globalWords.length ? globalWords : words
    if (!src.length) return seg.text || ''
    const inRange = src.filter((w) => w.end > seg.start_time + 0.02 && w.start < seg.end_time - 0.02)
    return inRange.map((w) => w.word).join(' ').replace(/\s+/g, ' ').trim() || (seg.text || '')
  }

  useEffect(() => { setVideoError(false) }, [sourceUrl])

  const hasVideo = Boolean(sourceUrl && projectId)
  const fetchPeaks = useCallback(
    (s: number, e: number, b: number) => api.shortsPeaks(projectId || '', s, e, b),
    [projectId],
  )

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => { overlayPressRef.current = e.target === e.currentTarget }}
      onClick={(e) => {
        // Only treat this as a backdrop click when the press BOTH started and ended on
        // the overlay itself. Otherwise a drag that begins on the waveform and releases
        // over the backdrop fires a click on the overlay and wrongly closes the editor
        // (losing the scene the user was resizing).
        if (saving) return
        if (overlayPressRef.current && e.target === e.currentTarget) (onMinimize ?? onClose)()
      }}
    >
      <div className="modal-card editor-card scene-card scene-card-pro" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <Scissors size={18} />
          <h3>{createMode ? 'Nowy short — wytnij z całego filmu' : 'Edytor scen'}{title ? ` — ${title}` : ''}</h3>
          {onMinimize && (
            <button type="button" className="ghost-btn icon-btn editor-min" disabled={saving} onClick={onMinimize} title="Zwiń do paska — wróć do tego shorta w każdej chwili (praca zostaje zapisana)">
              <Minus size={15} />
            </button>
          )}
          <button type="button" className="ghost-btn icon-btn editor-x" disabled={saving} onClick={onClose} title={createMode ? 'Porzuć tego shorta' : 'Zamknij edytor'}>
            <X size={15} />
          </button>
        </header>

        <div className="scene-pro-body">
          {/* LEFT: live preview + transport + full-film overview */}
          <div className="scene-stage">
            <div className="scene-player-wrap">
              {hasVideo ? (
                <video ref={videoRef} src={sourceUrl} className="scene-player" playsInline preload="metadata" onError={() => setVideoError(true)} />
              ) : (
                <div className="scene-player scene-player-empty">Brak podglądu źródła — edytuj czasy poniżej.</div>
              )}
              {hasVideo && videoError && (
                <div className="scene-player-empty scene-player-err">
                  Nie udało się załadować podglądu wideo — oryginalny plik źródłowy mógł zostać usunięty z dysku lub ma nieobsługiwany format. Czasy scen i napisy edytujesz normalnie poniżej.
                </div>
              )}
              {overlayText && !videoError && <div className="scene-caption"><span>{overlayText}</span></div>}
            </div>

            <div className="scene-transport">
              <button type="button" className="ghost-btn icon-btn" onClick={togglePlay} disabled={!hasVideo} title={playing ? 'Pauza' : 'Odtwórz'}>
                {playing ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <button type="button" className={`ghost-btn icon-btn ${loop ? 'is-on' : ''}`} onClick={() => setLoop((v) => !v)} title="Zapętl wybraną scenę">
                <Repeat size={16} />
              </button>
              <span className="scene-clock">{fmtT(curT)} <em>/ {fmtT(duration)}</em></span>
            </div>

            <FilmTimeline
              duration={duration}
              scenes={draft}
              selected={selected}
              curT={curT}
              playing={playing}
              onSeek={(t) => seek(t, false)}
              onSelectScene={(i) => selectScene(i, false)}
            />

            <button type="button" className="ghost-btn scene-add-here" onClick={addScene} disabled={saving}>
              <Plus size={14} /> Wstaw scenę w tym miejscu ({fmtT(curT)})
            </button>
          </div>

          {/* RIGHT: scene list with REAL-audio waveforms + draggable handles */}
          <div className="scene-list">
            {draft.length === 0 && (
              <div className="scene-empty">
                <Scissors size={22} />
                <strong>Brak scen</strong>
                <span>Przewiń film po lewej do wybranego momentu i kliknij „Wstaw scenę w tym miejscu", albo „Dodaj scenę". Zbuduj shorta scena po scenie od zera.</span>
              </div>
            )}
            {draft.map((seg, idx) => (
              <div className={`scene-item ${idx === selected ? 'active' : ''}`} key={idx} onMouseDown={() => setSelected(idx)} onClick={(e) => pickScene(e, idx)}>
                <div className="scene-item-head">
                  <strong>Scena {idx + 1}</strong>
                  <span className="scene-item-range">{fmtT(seg.start_time)} – {fmtT(seg.end_time)} · {(seg.end_time - seg.start_time).toFixed(1)}s</span>
                  <button type="button" className={`ghost-btn icon-btn ${idx === selected ? 'is-on' : ''}`} onClick={() => toggleScenePlay(idx)} disabled={!hasVideo} title={idx === selected && playing ? 'Pauza' : 'Odtwórz scenę w pętli'}>
                    {idx === selected && playing ? <Pause size={13} /> : <Play size={13} />}
                  </button>
                  <button type="button" className="ghost-btn icon-btn" disabled={saving || draft.length <= 1} onClick={() => removeSeg(idx)} title="Usuń scenę">
                    <Trash2 size={13} />
                  </button>
                </div>

                {hasVideo ? (
                  <WaveTrack
                    fetchPeaks={fetchPeaks}
                    duration={duration}
                    start={seg.start_time}
                    end={seg.end_time}
                    playhead={curT}
                    allowRegionDrag={false}
                    onChange={(s, e) => { setSelected(idx); setSeg(idx, { start_time: s, end_time: e }) }}
                    onSeek={(t) => { setSelected(idx); seek(t) }}
                    onScrub={(active) => {
                      scrubbingRef.current = active
                      // Pause while dragging so the frame sits exactly under the pointer
                      // (the playhead then tracks the dragged edge via onSeek).
                      if (active) { selectedRef.current = idx; videoRef.current?.pause() }
                    }}
                  />
                ) : null}

                <div className="scene-extend" title="Rozszerz lub skróć scenę poza widoczne okno waveformu">
                  <span className="scene-extend-lbl">Początek</span>
                  <button type="button" className="ghost-btn xs-btn" disabled={saving} title="Początek 2 s wcześniej (dłuższa scena)" onClick={() => nudgeEdge(idx, 'start', -EXTEND_STEP)}>−2s</button>
                  <button type="button" className="ghost-btn xs-btn" disabled={saving} title="Początek 2 s później (krótsza scena)" onClick={() => nudgeEdge(idx, 'start', EXTEND_STEP)}>+2s</button>
                  <span className="scene-extend-gap" />
                  <span className="scene-extend-lbl">Koniec</span>
                  <button type="button" className="ghost-btn xs-btn" disabled={saving} title="Koniec 2 s wcześniej (krótsza scena)" onClick={() => nudgeEdge(idx, 'end', -EXTEND_STEP)}>−2s</button>
                  <button type="button" className="ghost-btn xs-btn" disabled={saving} title="Koniec 2 s później (dłuższa scena)" onClick={() => nudgeEdge(idx, 'end', EXTEND_STEP)}>+2s</button>
                </div>

                <div className="scene-time-grid">
                  <label>Początek
                    <input className="text-field" type="number" min={0} step={0.1} value={Number(seg.start_time.toFixed(2))}
                      onChange={(e) => setSeg(idx, { start_time: Math.max(0, Math.min(Number(e.target.value), seg.end_time - MIN_LEN)) })} />
                  </label>
                  <label>Koniec
                    <input className="text-field" type="number" min={0} step={0.1} value={Number(seg.end_time.toFixed(2))}
                      onChange={(e) => setSeg(idx, { end_time: Math.max(seg.start_time + MIN_LEN, Number(e.target.value)) })} />
                  </label>
                </div>
                <p className="scene-text">{sceneText(seg)}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="modal-actions scene-actions">
          <button type="button" className="ghost-btn" disabled={saving} onClick={addScene}>
            <Plus size={14} /> Dodaj scenę
          </button>
          <button type="button" className="ghost-btn" disabled={saving || !dirty} onClick={reset}>
            <RotateCcw size={14} /> Cofnij
          </button>
          {!createMode && (
            <button type="button" className="ghost-btn danger-lite" disabled={saving} onClick={() => onSave({ segments, restore: true }, false)}>
              <RotateCcw size={14} /> Przywróć oryginalne cięcia
            </button>
          )}
          <span className="editor-spacer" />
          {onMinimize && (
            <button type="button" className="ghost-btn" disabled={saving} onClick={onMinimize} title="Zwiń — wróć do tego shorta później (praca zostaje zapisana)">
              <Minus size={14} /> Zwiń
            </button>
          )}
          <button type="button" className="ghost-btn" disabled={saving} onClick={onClose}>{createMode ? 'Porzuć' : 'Anuluj'}</button>
          <button type="button" className="ghost-btn" disabled={saving || draft.length === 0} onClick={() => onSave(build(), false)} title={draft.length === 0 ? 'Najpierw dodaj przynajmniej jedną scenę' : undefined}>
            <Save size={14} /> {createMode ? 'Stwórz short' : 'Zapisz'}
          </button>
          <button type="button" className="primary-btn" disabled={saving || draft.length === 0} onClick={() => onSave(build(), true)} title={draft.length === 0 ? 'Najpierw dodaj przynajmniej jedną scenę' : undefined}>
            <Clapperboard size={14} /> {saving ? (createMode ? 'Tworzenie...' : 'Zapisywanie...') : (createMode ? 'Stwórz i renderuj' : 'Zapisz i renderuj')}
          </button>
        </div>
      </div>
    </div>
  )
}
