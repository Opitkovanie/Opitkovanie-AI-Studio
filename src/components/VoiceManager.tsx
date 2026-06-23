import { useEffect, useRef, useState } from 'react'
import { Check, Pause, Play, Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'

export type VoiceSample = { id: string; label: string; path: string }

function VoiceManagerRow({ voice, selected, playing, onRename, onDelete, onTogglePlay }: {
  voice: VoiceSample
  selected: boolean
  playing: boolean
  onRename: (path: string, label: string) => void
  onDelete: (path: string) => void
  onTogglePlay: (path: string) => void
}) {
  const [name, setName] = useState(voice.label)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setName(voice.label) }, [voice.label])
  const dirty = name.trim().length > 0 && name.trim() !== voice.label
  const save = async () => {
    if (!dirty || busy) return
    setBusy(true)
    await onRename(voice.path, name.trim())
    setBusy(false)
  }
  const remove = () => {
    if (busy) return
    if (window.confirm(`Usunąć próbkę głosu „${voice.label}"? Tej operacji nie można cofnąć.`)) {
      setBusy(true)
      onDelete(voice.path)
    }
  }
  return (
    <div className={selected ? 'voice-row selected' : 'voice-row'}>
      <button type="button" className={`ghost-btn icon-btn ${playing ? 'is-on' : ''}`} title={playing ? 'Zatrzymaj' : 'Odsłuchaj próbkę'} onClick={() => onTogglePlay(voice.path)}>
        {playing ? <Pause size={15} /> : <Play size={15} />}
      </button>
      <input
        className="text-field"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save() }}
        title={voice.path}
      />
      <button type="button" className="ghost-btn icon-btn" disabled={!dirty || busy} title="Zapisz nazwę" onClick={save}>
        <Check size={15} />
      </button>
      <button type="button" className="ghost-btn icon-btn danger" disabled={busy} title="Usuń próbkę" onClick={remove}>
        <Trash2 size={15} />
      </button>
    </div>
  )
}

/** Expandable list of voice samples with audio preview + inline rename + delete + add.
 *  The library is SHARED — Shorts and DubMaster both read the same `meta.voices`, so a
 *  sample added or renamed here shows up in both modules. */
export function VoiceManager({ voices, selectedPath, onChanged, onDeletedSelected }: {
  voices: VoiceSample[]
  selectedPath?: string
  onChanged: () => void
  onDeletedSelected?: (path: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [playingPath, setPlayingPath] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  // One shared <audio> element drives previews; clicking another row swaps the src.
  useEffect(() => {
    if (!audioRef.current) audioRef.current = new Audio()
    const a = audioRef.current
    const onEnded = () => setPlayingPath(null)
    a.addEventListener('ended', onEnded)
    a.addEventListener('pause', onEnded)
    return () => { a.removeEventListener('ended', onEnded); a.removeEventListener('pause', onEnded); a.pause() }
  }, [])

  const togglePlay = (path: string) => {
    const a = audioRef.current
    if (!a) return
    if (playingPath === path) { a.pause(); setPlayingPath(null); return }
    a.src = api.voiceSampleAudioUrl(path)
    a.play().then(() => setPlayingPath(path)).catch(() => setPlayingPath(null))
  }

  const rename = async (path: string, label: string) => {
    try { await api.renameVoiceSample(path, label) } catch { /* ignore */ }
    onChanged()
  }
  const remove = async (path: string) => {
    if (playingPath === path) { audioRef.current?.pause(); setPlayingPath(null) }
    try { await api.deleteVoiceSample(path) } catch { /* ignore */ }
    if (path === selectedPath) onDeletedSelected?.(path)
    onChanged()
  }
  const addFromFile = async () => {
    if (adding) return
    setAdding(true)
    try {
      const picked = await (window.dubcut?.chooseVoiceSample?.() ?? Promise.resolve(null))
      if (picked?.path) {
        const label = (picked.name || '').replace(/\.[^.]+$/, '') || undefined
        await api.addVoiceSample(picked.path, label)
        onChanged()
      }
    } catch { /* ignore */ } finally {
      setAdding(false)
    }
  }

  return (
    <>
      <button type="button" className="manage-voices-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? 'Zamknij zarządzanie' : `Zarządzaj próbkami (${voices.length})`}
      </button>
      {open && (
        <div className="voice-manager">
          <button type="button" className="ghost-btn voice-add-btn" disabled={adding} onClick={addFromFile} title="Wczytaj plik audio/wideo do wspólnej biblioteki głosów (Shorts + DubMaster)">
            <Plus size={14} /> {adding ? 'Dodawanie…' : 'Dodaj próbkę z pliku'}
          </button>
          {voices.length === 0 && <p className="settings-desc">Brak zapisanych próbek głosu.</p>}
          {voices.map((voice) => (
            <VoiceManagerRow
              key={voice.path}
              voice={voice}
              selected={selectedPath === voice.path}
              playing={playingPath === voice.path}
              onRename={rename}
              onDelete={remove}
              onTogglePlay={togglePlay}
            />
          ))}
        </div>
      )}
    </>
  )
}
