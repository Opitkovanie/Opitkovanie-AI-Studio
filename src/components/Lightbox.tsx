import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

/** Full-window media preview. Click the image (or the backdrop, or Esc) to close. */
export function Lightbox({ src, kind = 'image', onClose }: { src: string; kind?: 'image' | 'video'; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = '' }
  }, [onClose])

  return createPortal(
    <div className="lightbox" onClick={onClose}>
      <button type="button" className="lightbox-close" onClick={onClose} title="Zamknij (Esc)"><X size={20} /></button>
      {kind === 'video'
        ? <video className="lightbox-media" src={src} controls autoPlay onClick={(e) => e.stopPropagation()} />
        : <img className="lightbox-media" src={src} alt="" onClick={onClose} />}
    </div>,
    document.body,
  )
}
