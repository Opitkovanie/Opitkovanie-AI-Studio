import { useCallback, useEffect, useState } from 'react'
import { FolderOpen, Trash2, RefreshCw, HardDrive, FolderCog, Shield } from 'lucide-react'
import { api, type StorageUsage } from '../lib/api'

function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}

export function StorageManager({ online, openPath, chooseWorkDir, onSetWorkDir }: {
  online: boolean
  openPath: (p: string) => Promise<boolean>
  chooseWorkDir: () => Promise<{ path: string } | null>
  onSetWorkDir: (path: string) => void
}) {
  const [usage, setUsage] = useState<StorageUsage | null>(null)
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    try { setUsage(await api.storageUsage()) } catch { setUsage(null) }
  }, [])
  useEffect(() => { if (online) refresh() }, [online, refresh])

  const cleanup = async (key: string, label: string) => {
    if (!window.confirm(`Usunąć wszystkie pliki z folderu „${label}”? Aplikacja odtworzy go w razie potrzeby. Tej operacji nie można cofnąć.`)) return
    setBusy(key)
    try {
      const r = await api.storageCleanup(key)
      await refresh()
      window.alert(`Zwolniono ${fmtBytes(r.freed_bytes)}.`)
    } catch { /* */ } finally { setBusy('') }
  }

  const changeWorkDir = async () => {
    const r = await chooseWorkDir()
    if (r?.path) { onSetWorkDir(r.path); setTimeout(refresh, 600) }
  }

  const total = usage?.total_bytes ?? 0

  return (
    <div className="card settings-card">
      <div className="settings-card-head">
        <HardDrive size={17} />
        <h3>Pliki i pamięć</h3>
        <span className="status-pill">{fmtBytes(total)}</span>
        <button type="button" className="ghost-btn icon-btn" title="Odśwież" style={{ marginLeft: 'auto' }} onClick={refresh}>
          <RefreshCw size={14} />
        </button>
      </div>
      <p className="settings-desc">
        Wszystkie pliki tymczasowe (pobrane, wygenerowane) trzymane są w jednym folderze roboczym,
        podzielone na moduły. Możesz wskazać własny folder (np. na dysku zewnętrznym), a żeby wyczyścić
        moduł — usuń cały jego folder; aplikacja odtworzy go w razie potrzeby.
      </p>

      {usage && (
        <>
          <div className="workdir-row">
            <FolderCog size={15} />
            <div className="workdir-info">
              <span className="workdir-label">Folder roboczy</span>
              <span className="workdir-path mono" title={usage.data_dir}>{usage.data_dir}</span>
            </div>
            <button type="button" className="ghost-btn" onClick={changeWorkDir}><FolderCog size={14} /> Zmień</button>
            <button type="button" className="ghost-btn" onClick={() => openPath(usage.data_dir)}><FolderOpen size={14} /> Otwórz</button>
          </div>
          {usage.config_dir && (
            <p className="settings-desc subtle"><Shield size={12} /> Ustawienia, słownik i zaimportowane loga/próbki głosu zostają osobno (przeżywają aktualizacje): <span className="mono">{usage.config_dir}</span></p>
          )}
        </>
      )}

      {!usage ? (
        <p className="settings-desc">{online ? 'Wczytywanie…' : 'Uruchom backend, aby zobaczyć zużycie dysku.'}</p>
      ) : (
        <div className="storage-list">
          {usage.categories.map((c) => {
            const pct = total > 0 ? Math.round((c.bytes / total) * 100) : 0
            return (
              <div className="storage-row" key={c.key}>
                <div className="storage-row-info">
                  <span className="storage-label">{c.label}</span>
                  <span className="storage-meta">{c.count > 0 ? `${c.count} plików · ` : ''}{fmtBytes(c.bytes)}{total > 0 ? ` · ${pct}% całości` : ''}</span>
                  <div className="storage-bar"><em style={{ width: `${pct}%` }} /></div>
                </div>
                <div className="storage-row-actions">
                  <button type="button" className="ghost-btn" onClick={() => openPath(c.path)} title="Otwórz w Finderze">
                    <FolderOpen size={14} /> Otwórz
                  </button>
                  <button type="button" className="ghost-btn danger" disabled={busy === c.key || c.bytes === 0}
                    onClick={() => cleanup(c.key, c.label)}>
                    <Trash2 size={14} /> {busy === c.key ? 'Czyszczę…' : 'Wyczyść'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
