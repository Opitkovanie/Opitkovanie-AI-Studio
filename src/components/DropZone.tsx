import { useState } from 'react'
import { UploadCloud, FileVideo } from 'lucide-react'
import type { DubcutMediaFile } from '../types/dubcut'

export function DropZone({
  media,
  formats,
  onPick,
  onDropPath,
  compact = false,
}: {
  media: DubcutMediaFile | null
  formats: string
  onPick: () => void
  onDropPath?: (path: string, name: string) => void
  compact?: boolean
}) {
  const [over, setOver] = useState(false)

  if (media) {
    return (
      <div className="dropzone has-media">
        <video src={media.url} controls className="dropzone-video" />
        <div className="dropzone-file">
          <FileVideo size={15} />
          <span>{media.name}</span>
          <button type="button" onClick={onPick}>
            Zmień
          </button>
        </div>
      </div>
    )
  }

  return (
    <button
      type="button"
      className={`dropzone${over ? ' over' : ''}${compact ? ' compact' : ''}`}
      onClick={onPick}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const f = e.dataTransfer.files?.[0] as (File & { path?: string }) | undefined
        if (f && onDropPath) onDropPath(f.path ?? f.name, f.name)
      }}
    >
      <span className="dropzone-icon">
        <UploadCloud size={26} />
      </span>
      <strong>Upuść wideo tutaj</strong>
      <small>{formats}</small>
    </button>
  )
}
