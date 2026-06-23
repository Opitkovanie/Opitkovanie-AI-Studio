// Live WYSIWYG preview of how subtitles, logo and the text watermark will look
// burned onto a generated short — updates instantly as settings change (font, style,
// size, position, colors, logo, watermark).
import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { api, getBase, logoUrl } from '../lib/api'
import { ensureFontsLoaded, fontFamilyFor } from '../lib/fonts'

export function StyledPreview({
  s,
  media,
  fonts,
  presetFont,
}: {
  s: Record<string, any>
  media?: { url: string } | null
  fonts: string[]
  presetFont?: string
}) {
  useEffect(() => {
    ensureFontsLoaded(fonts)
  }, [fonts])

  const vertical = String(s.aspect_ratio ?? '9:16').includes('9:16')
  const dims = outputDims(s.export_resolution, vertical)
  // 10 words so the full range of the "słowa w bloku" setting (max 10) can be previewed.
  const sample = s.sub_upper
    ? 'TWÓJ VIRALOWY TESTOWY TEKST POKAZUJE DOKŁADNIE WYGLĄD NAPISÓW W PODGLĄDZIE'
    : 'Twój viralowy testowy tekst pokazuje dokładnie wygląd napisów w podglądzie'
  const words = sample.split(' ').slice(0, Math.max(1, Math.min(10, Number(s.sub_words) || 3)))
  const activeAnimation = animationCode(s.sub_animation)
  const cycleSeconds = Math.max(3.2, words.length * 0.75)
  const mode = String(s.sub_mode || 'highlight')
  const xLogo = pct(s.logo_x)
  const yLogo = pct(s.logo_y)
  const xWm = pct(s.wm_x)
  const yWm = pct(s.wm_y)

  const subFont =
    s.custom_font && s.custom_font !== 'Domyślna dla presetu'
      ? fontFamilyFor(s.custom_font)
      : resolveFamily(presetFont, fonts)
  const logoSrc = s.logo_url || logoUrl(s.logo_path)
  const [renderedPreview, setRenderedPreview] = useState('')
  const [previewFailed, setPreviewFailed] = useState(false)
  const previewKey = useMemo(() => JSON.stringify({
    sub_preset: s.sub_preset,
    custom_font: s.custom_font,
    aspect_ratio: s.aspect_ratio,
    export_resolution: s.export_resolution,
    enable_subtitles: s.enable_subtitles,
    sub_bcolor: s.sub_bcolor,
    sub_hcolor: s.sub_hcolor,
    sub_size: s.sub_size,
    sub_hsize: s.sub_hsize,
    sub_out_color: s.sub_out_color,
    sub_out_thick: s.sub_out_thick,
    sub_shad_color: s.sub_shad_color,
    sub_shad_size: s.sub_shad_size,
    sub_bold: s.sub_bold,
    sub_italic: s.sub_italic,
    sub_upper: s.sub_upper,
    sub_punct: s.sub_punct,
    sub_words: s.sub_words,
    sub_mode: s.sub_mode,
    sub_animation: s.sub_animation,
    sub_margin: s.sub_margin,
    sub_autoscale: s.sub_autoscale,
    sub_bg_pad: s.sub_bg_pad,
    enable_logo: s.enable_logo,
    logo_path: s.logo_path,
    logo_scale: s.logo_scale,
    logo_x: s.logo_x,
    logo_y: s.logo_y,
    logo_opacity: s.logo_opacity,
    enable_text: s.enable_text,
    wm_text: s.wm_text,
    wm_font: s.wm_font,
    wm_size: s.wm_size,
    wm_color: s.wm_color,
    wm_opacity: s.wm_opacity,
    wm_x: s.wm_x,
    wm_y: s.wm_y,
    wm_out_color: s.wm_out_color,
    wm_out_thick: s.wm_out_thick,
    wm_shad_color: s.wm_shad_color,
    wm_shad_size: s.wm_shad_size,
    wm_bold: s.wm_bold,
    wm_italic: s.wm_italic,
  }), [s])

  // Signature of subtitle-only style props — when it changes we remount the
  // animated subline (via `key`) so the word animation restarts instantly in the
  // new style instead of waiting for the current sentence cycle to finish.
  const subStyleSig = useMemo(() => JSON.stringify({
    p: s.sub_preset, f: s.custom_font, b: s.sub_bcolor, h: s.sub_hcolor,
    sz: s.sub_size, hsz: s.sub_hsize, oc: s.sub_out_color, ot: s.sub_out_thick,
    sc: s.sub_shad_color, ss: s.sub_shad_size, bd: s.sub_bold, it: s.sub_italic,
    up: s.sub_upper, pu: s.sub_punct, w: s.sub_words, m: s.sub_mode,
    an: s.sub_animation, mg: s.sub_margin, as: s.sub_autoscale, bg: s.sub_bg_pad,
    res: s.export_resolution, ar: s.aspect_ratio,
  }), [s])

  useEffect(() => {
    const needsRenderedPreview = !!s.enable_subtitles || !!s.enable_logo || (!!s.enable_text && !!s.wm_text)
    if (!needsRenderedPreview) {
      setRenderedPreview('')
      return
    }
    // Drop the stale render immediately so the live CSS overlay shows the new
    // style at once; the freshly rendered video swaps back in after the debounce.
    setRenderedPreview('')
    let cancelled = false
    const timer = window.setTimeout(() => {
      api.shortsPreview(s)
        .then((res) => {
          if (cancelled) return
          setRenderedPreview(`${getBase()}${res.url}`)
          setPreviewFailed(false)
        })
        .catch(() => {
          if (cancelled) return
          setRenderedPreview('')
          setPreviewFailed(true)
        })
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [previewKey])
  const sublineStyle = {
    '--preview-base': s.sub_bcolor,
    '--preview-highlight': s.sub_hcolor,
    '--preview-word-cycle': `${cycleSeconds}s`,
    '--preview-base-size': renderSize(s.sub_size, dims.w),
    '--preview-highlight-size': renderSize(s.sub_hsize ?? s.sub_size, dims.w),
    fontFamily: subFont,
    fontSize: renderSize(s.sub_size, dims.w),
    fontWeight: s.sub_bold ? 800 : 600,
    fontStyle: s.sub_italic ? 'italic' : 'normal',
    color: s.sub_bcolor,
    textShadow: outline(s.sub_out_color, s.sub_out_thick, s.sub_shad_color, s.sub_shad_size, dims.w),
  } as CSSProperties

  return (
    <div className="preview-wrap">
      <div className="preview-head">
        <span>{dims.w}x{dims.h}</span>
        <em>{vertical ? '9:16' : '16:9'}</em>
      </div>
      <div
        className={`preview-phone ${vertical ? 'v' : 'h'}`}
        style={{ aspectRatio: `${dims.w} / ${dims.h}` }}
      >
        {renderedPreview ? (
          <video className="preview-media" src={renderedPreview} muted loop autoPlay playsInline />
        ) : media?.url ? (
          <video className="preview-media" src={media.url} muted loop autoPlay playsInline />
        ) : (
          <div className="preview-media placeholder" />
        )}

        {s.enable_logo && (!renderedPreview || previewFailed) && (
          <div
            className="preview-logo"
            style={{
              left: `${clamp(s.logo_x)}%`,
              top: `${clamp(s.logo_y)}%`,
              width: `${Math.max(1, Math.min(100, Number(s.logo_scale) || 45))}%`,
              opacity: (Number(s.logo_opacity) || 100) / 100,
              transform: `translate(-${xLogo}%, -${yLogo}%)`,
            }}
          >
            {logoSrc ? <img src={logoSrc} alt="" /> : 'LOGO'}
          </div>
        )}

        {s.enable_subtitles && (!renderedPreview || previewFailed) && (
          <div key={subStyleSig} className="preview-subs" style={{ bottom: `${marginToPct(s.sub_margin, dims.h)}%` }}>
            <span
              className={`preview-subline anim-${activeAnimation} mode-${mode}`}
              style={sublineStyle}
            >
              {words.map((w, i) => (
                <span
                  key={i}
                  className="preview-word"
                  style={{
                    '--preview-word-delay': `${(i * cycleSeconds) / words.length}s`,
                    padding: mode === 'highlight_box' ? `0 ${renderSize((s.sub_bg_pad || 45) * 0.22, dims.w)}` : 0,
                    borderRadius: mode === 'highlight_box' ? renderSize(4, dims.w) : 0,
                  } as CSSProperties}
                >
                  {w}
                </span>
              ))}
            </span>
          </div>
        )}

        {s.enable_text && s.wm_text && (!renderedPreview || previewFailed) && (
          <div
            className="preview-wm"
            style={{
              left: `${clamp(s.wm_x)}%`,
              top: `${clamp(s.wm_y)}%`,
              fontFamily: fontFamilyFor(s.wm_font),
              fontSize: renderSize(s.wm_size, dims.w),
              fontWeight: s.wm_bold ? 800 : 600,
              fontStyle: s.wm_italic ? 'italic' : 'normal',
              color: s.wm_color,
              opacity: (Number(s.wm_opacity) || 100) / 100,
              textShadow: outline(s.wm_out_color, s.wm_out_thick, s.wm_shad_color, s.wm_shad_size, dims.w),
              transform: `translate(-${xWm}%, -${yWm}%)`,
            }}
          >
            {s.wm_text}
          </div>
        )}
      </div>
      <span className="preview-caption">Podgląd napisów, logo i znaku wodnego</span>
    </div>
  )
}

function outputDims(resolution: string | undefined, vertical: boolean) {
  const text = String(resolution || '1080p')
  const shortEdge = text.includes('4K') || text.includes('2160') ? 2160
    : text.includes('2K') || text.includes('1440') ? 1440
      : text.includes('720') ? 720
        : text.includes('480') ? 480
          : 1080
  const longEdge = Math.round(shortEdge * (16 / 9))
  return vertical ? { w: shortEdge, h: longEdge } : { w: longEdge, h: shortEdge }
}

function pct(v: any) {
  return Math.max(0, Math.min(100, Number(v) || 0))
}

function renderSize(v: any, outW: number) {
  const n = Math.max(1, Number(v) || 30)
  return `${(n / outW) * 100}cqw`
}

function animationCode(value: any) {
  const v = String(value || 'none')
  const map: Record<string, string> = {
    'Brak': 'none',
    'Wyskakiwanie (Spring Pop)': 'spring',
    'Płynne Karaoke': 'karaoke',
    'Trzęsienie (Jiggle)': 'jiggle',
    'Wyłanianie (Blur Reveal)': 'blur_reveal',
    'Nalot (Zoom In)': 'zoom_in',
    'Pulsowanie (Color Pulse)': 'color_pulse',
    'Wjazd 3D (Slide Up)': 'slide_up',
  }
  return map[v] || v
}

function resolveFamily(name: string | undefined, fonts: string[]): string | undefined {
  if (!name) return undefined
  if (['Arial', 'Impact', 'Consolas'].includes(name)) return name
  const norm = (x: string) => x.replace(/\.[^.]+$/, '').replace(/[^a-z0-9]/gi, '').toLowerCase()
  const target = norm(name)
  const file = fonts.find((f) => norm(f) === target) || fonts.find((f) => norm(f).startsWith(target))
  return file ? fontFamilyFor(file) : undefined
}

function clamp(v: any) {
  const n = Number(v) || 0
  return Math.max(0, Math.min(92, n))
}
function marginToPct(v: any, outH: number) {
  const n = Number(v) || 600
  return Math.max(0, Math.min(100, (n / outH) * 100))
}
function outline(color = '#000', thick = 0, shadColor?: string, shadSize = 0, outW = 1080) {
  const t = Math.max(0, Number(thick) || 0)
  const layers: string[] = []
  if (t > 0) {
    for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1], [-1, -1], [1, 1], [-1, 1], [1, -1]]) {
      layers.push(`${dx * (t / outW) * 100}cqw ${dy * (t / outW) * 100}cqw 0 ${color}`)
    }
  }
  if (shadColor && Number(shadSize) > 0) {
    const sz = Number(shadSize)
    const cqw = (sz / outW) * 100
    layers.push(`${cqw}cqw ${cqw}cqw ${cqw}cqw ${shadColor}`)
  }
  return layers.join(', ') || 'none'
}
