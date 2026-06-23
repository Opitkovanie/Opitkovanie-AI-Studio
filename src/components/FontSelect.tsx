import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Check } from 'lucide-react'
import { ensureFontsLoaded, fontFamilyFor } from '../lib/fonts'

/** Font picker whose options are rendered in their own typeface (live preview). */
export function FontSelect({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ensureFontsLoaded(options)
  }, [options])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const label = (opt: string) => opt.replace(/\.(ttf|otf)$/i, '')
  const sample = 'Aa Żółć 123'

  return (
    <div className="font-select" ref={ref}>
      <button type="button" className="font-trigger" onClick={() => setOpen((o) => !o)}>
        <span style={{ fontFamily: fontFamilyFor(value) }} className="font-trigger-label">
          {label(value)}
        </span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="font-pop">
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={opt === value ? 'font-opt active' : 'font-opt'}
              onClick={() => {
                onChange(opt)
                setOpen(false)
              }}
            >
              <span className="font-opt-check">{opt === value && <Check size={13} />}</span>
              <span className="font-opt-content">
                <span style={{ fontFamily: fontFamilyFor(opt) }} className="font-opt-label">
                  {sample}
                </span>
                <small>{label(opt)}</small>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
