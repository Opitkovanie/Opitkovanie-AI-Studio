import { useState } from 'react'
import { Save, Trash2, Bookmark, ChevronDown } from 'lucide-react'

type Preset = { name: string; settings: Record<string, any> }

function loadPresets(key: string): Preset[] {
  try { return JSON.parse(localStorage.getItem(key) || '[]') } catch { return [] }
}
function savePresets(key: string, p: Preset[]) {
  try { localStorage.setItem(key, JSON.stringify(p)) } catch { /* ignore */ }
}

/** Named settings recipes per module. Saves the current config (minus transient keys like
 *  prompt/seed/base image) under a name and re-applies it with one click. */
export function PresetBar({ moduleKey, value, exclude = [], onApply }: {
  moduleKey: string
  value: Record<string, any>
  exclude?: string[]
  onApply: (s: Record<string, unknown>) => void
}) {
  const storeKey = `dubcut.presets.${moduleKey}`
  const [presets, setPresets] = useState<Preset[]>(() => loadPresets(storeKey))
  const [sel, setSel] = useState('')

  const apply = (name: string) => {
    setSel(name)
    const p = presets.find((x) => x.name === name)
    if (p) onApply(p.settings)
  }
  const saveCurrent = () => {
    const name = window.prompt('Nazwa presetu (recepty ustawień):')?.trim()
    if (!name) return
    const settings = Object.fromEntries(Object.entries(value).filter(([k]) => !exclude.includes(k)))
    const next = [...presets.filter((p) => p.name !== name), { name, settings }].sort((a, b) => a.name.localeCompare(b.name))
    setPresets(next); savePresets(storeKey, next); setSel(name)
  }
  const removeCurrent = () => {
    if (!sel || !window.confirm(`Usunąć preset „${sel}”?`)) return
    const next = presets.filter((p) => p.name !== sel)
    setPresets(next); savePresets(storeKey, next); setSel('')
  }

  return (
    <div className="preset-bar">
      <Bookmark size={14} />
      <span className="preset-bar-label">Preset</span>
      <div className="select preset-select">
        <select value={sel} onChange={(e) => apply(e.target.value)}>
          <option value="">{presets.length ? '— wybierz receptę —' : '— brak presetów —'}</option>
          {presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
        <ChevronDown size={14} />
      </div>
      <button type="button" className="ghost-btn" onClick={saveCurrent} title="Zapisz bieżące ustawienia jako preset"><Save size={13} /> Zapisz</button>
      {sel && <button type="button" className="ghost-btn icon-btn danger" onClick={removeCurrent} title="Usuń ten preset"><Trash2 size={13} /></button>}
    </div>
  )
}
