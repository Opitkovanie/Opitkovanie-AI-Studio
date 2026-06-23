import { getBase } from './api'

const SYSTEM = ['Arial', 'Impact', 'Consolas']
let injected = false

/** CSS font-family to use for a given font option (handles system + file fonts). */
export function fontFamilyFor(option?: string): string | undefined {
  if (!option || option === 'Domyślna dla presetu') return undefined
  if (SYSTEM.includes(option) && !/\.(ttf|otf)$/i.test(option)) return option
  return `dcfont_${slug(option)}`
}

function slug(name: string): string {
  return name.replace(/\.[^.]+$/, '').replace(/[^a-z0-9]/gi, '_')
}

/** Inject @font-face rules for every file-based font once, served by the backend. */
export function ensureFontsLoaded(fonts: string[]): void {
  if (injected || !fonts.length) return
  const files = fonts.filter((f) => /\.(ttf|otf)$/i.test(f))
  if (!files.length) return
  const css = files
    .map((file) => {
      const fmt = file.toLowerCase().endsWith('.otf') ? 'opentype' : 'truetype'
      const staticUrl = `fonts/${encodeURIComponent(file)}`
      const apiUrl = `${getBase()}/api/fonts/${encodeURIComponent(file)}`
      return `@font-face{font-family:'dcfont_${slug(file)}';src:url('${staticUrl}') format('${fmt}'),url('${apiUrl}') format('${fmt}');font-display:swap;}`
    })
    .join('\n')
  const style = document.createElement('style')
  style.id = 'dcfont-faces'
  style.textContent = css
  document.head.appendChild(style)
  injected = true
}
