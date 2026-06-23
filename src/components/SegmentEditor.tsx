export type Seg = { id: number; start: number; end: number; text: string }

function ts(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

/** Compact transcript editor. One row per segment: timecode + text.
 *  When `secondary` is provided, shows two aligned columns (original | translation). */
export function SegmentEditor({
  primary, onPrimary, primaryLabel,
  secondary, onSecondary, secondaryLabel,
}: {
  primary: Seg[]
  onPrimary: (s: Seg[]) => void
  primaryLabel?: string
  secondary?: Seg[]
  onSecondary?: (s: Seg[]) => void
  secondaryLabel?: string
}) {
  const dual = !!secondary
  const editP = (id: number, text: string) => onPrimary(primary.map((s) => (s.id === id ? { ...s, text } : s)))
  const editS = (idx: number, text: string) =>
    onSecondary && secondary && onSecondary(secondary.map((s, j) => (j === idx ? { ...s, text } : s)))
  return (
    <div className={dual ? 'seg-editor dual' : 'seg-editor'}>
      {dual && (primaryLabel || secondaryLabel) && (
        <div className="seg-colhead">
          <span className="seg-time" />
          <span>{primaryLabel}</span>
          <span>{secondaryLabel}</span>
        </div>
      )}
      <div className="seg-list">
        {primary.map((s, i) => (
          <div className="seg-row" key={s.id}>
            <span className="seg-time">{ts(s.start)}</span>
            <textarea className="seg-text" rows={dual ? 2 : 1} value={s.text} onChange={(e) => editP(s.id, e.target.value)} />
            {dual && (
              <textarea className="seg-text" rows={2} value={secondary![i]?.text ?? ''} onChange={(e) => editS(i, e.target.value)} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
