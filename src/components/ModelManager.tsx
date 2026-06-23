import { useCallback, useEffect, useState } from 'react'
import { FolderOpen, Trash2, RefreshCw, Boxes } from 'lucide-react'
import { api, type ModelList } from '../lib/api'

function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / Math.pow(1024, i)).toFixed(i <= 1 ? 0 : 1)} ${u[i]}`
}

export function ModelManager({ online, openPath }: { online: boolean; openPath: (p: string) => Promise<boolean> }) {
  const [data, setData] = useState<ModelList | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try { setData(await api.modelsList()) } catch { setData(null) } finally { setLoading(false) }
  }, [])
  // Models are scanned with `du` (slow on 100+ GB) — only on demand, not on mount.
  useEffect(() => { if (!online) setData(null) }, [online])

  const del = async (key: string, label: string, path: string) => {
    if (!window.confirm(`Usunąć „${label}”? Trzeba będzie pobrać go ponownie przy następnym użyciu.`)) return
    setBusy(key)
    try {
      const r = await api.deleteModel(path)
      await refresh()
      window.alert(`Zwolniono ${fmtBytes(r.freed_bytes)}.`)
    } catch { window.alert('Tej pozycji nie można usunąć.') } finally { setBusy('') }
  }

  const total = data?.total_bytes ?? 0

  return (
    <div className="card settings-card">
      <div className="settings-card-head">
        <Boxes size={17} />
        <h3>Modele AI</h3>
        {data && <span className="status-pill">{fmtBytes(data.total_bytes)}</span>}
        <button type="button" className="ghost-btn icon-btn" title="Przeskanuj" style={{ marginLeft: 'auto' }} disabled={!online || loading} onClick={refresh}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      </div>
      <p className="settings-desc">
        Modele (~100+ GB) instalowane są w systemie (silniki w folderze aplikacji, wagi w pamięci
        podręcznej Hugging Face). Skanowanie liczy realne rozmiary na dysku, więc odpala się tylko na
        żądanie. Każdą pozycję możesz usunąć, aby zwolnić miejsce — pobierze się ponownie przy następnym
        użyciu (pozycje „wymagany" są potrzebne do działania modułu).
      </p>
      {!data ? (
        <button type="button" className="ghost-btn" disabled={!online || loading} onClick={refresh}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> {loading ? 'Skanowanie…' : 'Przeskanuj modele'}
        </button>
      ) : data.models.length === 0 ? (
        <p className="settings-desc">Nie znaleziono lokalnych modeli.</p>
      ) : (
        <div className="storage-list">
          {data.models.map((m) => {
            const pct = total > 0 ? Math.round((m.bytes / total) * 100) : 0
            return (
            <div className="storage-row" key={m.key}>
              <div className="storage-row-info">
                <span className="storage-label">{m.label}</span>
                {m.sublabel && m.sublabel !== m.label && <span className="storage-sublabel">{m.sublabel}</span>}
                <span className="storage-meta">{fmtBytes(m.bytes)}{total > 0 ? ` · ${pct}% całości` : ''}{m.required ? ' · wymagany' : ''}</span>
                <div className="storage-bar"><em style={{ width: `${pct}%` }} /></div>
              </div>
              <div className="storage-row-actions">
                <button type="button" className="ghost-btn" onClick={() => openPath(m.path)} title="Otwórz w Finderze"><FolderOpen size={14} /> Otwórz</button>
                {m.deletable && (
                  <button type="button" className="ghost-btn danger" disabled={busy === m.key} onClick={() => del(m.key, m.label, m.path)}>
                    <Trash2 size={14} /> {busy === m.key ? 'Usuwam…' : 'Usuń'}
                  </button>
                )}
              </div>
            </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
