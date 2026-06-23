import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode, Ref } from 'react'
import { ChevronDown, HelpCircle } from 'lucide-react'

/** Small info icon with a hover tooltip — mirrors the Streamlit `help=` bubbles.
 *
 * The bubble is rendered into a body portal with `position: fixed` and clamped to
 * the viewport, so it can never be clipped by a scrollable inspector panel or run
 * off the edge of the screen (which the old absolutely-positioned version did). */
export function Hint({ text }: { text?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 })

  const place = useCallback(() => {
    const icon = ref.current?.getBoundingClientRect()
    const tip = tipRef.current?.getBoundingClientRect()
    if (!icon) return
    const margin = 10
    const tw = tip?.width ?? 260
    const th = tip?.height ?? 80
    // Prefer above the icon; flip below if there isn't room.
    let top = icon.top - th - 8
    if (top < margin) top = icon.bottom + 8
    // Centre horizontally on the icon, then clamp to the viewport.
    let left = icon.left + icon.width / 2 - tw / 2
    left = Math.max(margin, Math.min(left, window.innerWidth - tw - margin))
    setPos({ left, top })
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    place()
    const onScroll = () => setOpen(false)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll, true)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll, true)
    }
  }, [open, place])

  if (!text) return null
  return (
    <span
      ref={ref}
      className="hint"
      tabIndex={0}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <HelpCircle size={13} />
      {open && createPortal(
        <div ref={tipRef} className="hint-tip-fixed" style={{ left: pos.left, top: pos.top }}>
          {text}
        </div>,
        document.body,
      )}
    </span>
  )
}

export function SectionTitle({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="section-title">
      {icon}
      <span>{title}</span>
      {hint && <small>{hint}</small>}
    </div>
  )
}

/** Collapsible inspector section — collapsed by default, remembers what the user opens. */
export function Section({
  icon,
  title,
  badge,
  id,
  children,
}: {
  icon?: ReactNode
  title: string
  badge?: string | number
  id?: string
  children: ReactNode
}) {
  const key = `dc.section.${id ?? title}`
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(key) === '1'
    } catch {
      return false
    }
  })
  const toggle = () => {
    setOpen((o) => {
      const next = !o
      try {
        localStorage.setItem(key, next ? '1' : '0')
      } catch {
        /* ignore */
      }
      return next
    })
  }
  return (
    <div className={open ? 'acc open' : 'acc'}>
      <button type="button" className="acc-head" onClick={toggle}>
        <span className="acc-icon">{icon}</span>
        <span className="acc-title">{title}</span>
        {badge !== undefined && <em className="acc-badge">{badge}</em>}
        <ChevronDown size={16} className="acc-chevron" />
      </button>
      <div className="acc-body">
        <div className="acc-inner">{children}</div>
      </div>
    </div>
  )
}

export function Field({
  label,
  badge,
  hint,
  children,
}: {
  label: string
  badge?: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {badge && <em className="field-badge">{badge}</em>}
        <Hint text={hint} />
      </span>
      {children}
    </label>
  )
}

export function Select({
  value,
  options,
  onChange,
}: {
  value: string
  options: string[]
  onChange: (v: string) => void
}) {
  return (
    <div className="select">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o, idx) => (
          <option key={`${o}-${idx}`} value={o}>
            {o}
          </option>
        ))}
      </select>
      <ChevronDown size={14} />
    </div>
  )
}

export function PillGroup({
  value,
  options,
  onChange,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <div className="pill-group">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? 'pill active' : 'pill'}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  hint?: string
}) {
  return (
    <div className="toggle-row">
      <button type="button" className="toggle-btn" onClick={() => onChange(!checked)}>
        <span className={checked ? 'switch on' : 'switch'}>
          <i />
        </span>
        <span>{label}</span>
      </button>
      <Hint text={hint} />
    </div>
  )
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  suffix = '',
  hint,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  suffix?: string
  hint?: string
  onChange: (v: number) => void
}) {
  const pct = ((value - min) / (max - min)) * 100
  return (
    <div className="slider">
      <div className="slider-head">
        <span>
          {label}
          <Hint text={hint} />
        </span>
        <b>
          {value}
          {suffix}
        </b>
      </div>
      <div className="slider-track">
        <em style={{ width: `${pct}%` }} />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </div>
    </div>
  )
}

export function TextField({
  value,
  placeholder,
  type = 'text',
  onChange,
}: {
  value: string
  placeholder?: string
  type?: string
  onChange: (v: string) => void
}) {
  return (
    <input
      className="text-field"
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export function TextArea({
  value,
  placeholder,
  rows = 4,
  onChange,
  innerRef,
  resizeKey,
}: {
  value: string
  placeholder?: string
  rows?: number
  onChange: (v: string) => void
  innerRef?: Ref<HTMLTextAreaElement>
  /** When set, the user-dragged height is remembered across sessions under this key. */
  resizeKey?: string
}) {
  const localRef = useRef<HTMLTextAreaElement | null>(null)
  const attach = (el: HTMLTextAreaElement | null) => {
    localRef.current = el
    if (typeof innerRef === 'function') innerRef(el)
    else if (innerRef && 'current' in innerRef) (innerRef as { current: HTMLTextAreaElement | null }).current = el
  }
  useLayoutEffect(() => {
    if (!resizeKey || !localRef.current) return
    try {
      const h = localStorage.getItem(`dubcut.ta.${resizeKey}`)
      if (h) localRef.current.style.height = h
    } catch { /* */ }
  }, [resizeKey])
  const persist = () => {
    if (!resizeKey || !localRef.current) return
    try { localStorage.setItem(`dubcut.ta.${resizeKey}`, localRef.current.style.height) } catch { /* */ }
  }
  return (
    <textarea
      ref={attach}
      className="text-area"
      rows={rows}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onMouseUp={resizeKey ? persist : undefined}
    />
  )
}

export function ColorField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="color-field">
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)} />
      <span>{value.toUpperCase()}</span>
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>
}
