import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', '**/._*', 'vendor', 'assets']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // The settings layer mirrors two dynamic Python config schemas; loose typing is intentional.
      '@typescript-eslint/no-explicit-any': 'off',
      // Mount-time data fetch inside an effect is a deliberate, supported pattern here.
      'react-hooks/set-state-in-effect': 'off',
    },
  },
  {
    // The canvas waveform/timeline editors mirror the LATEST props into refs during
    // render so their ONCE-attached pointer listeners (and the rAF draw loop) always
    // read fresh values WITHOUT being torn down on every parent re-render. This is the
    // deliberate fix for the drag-stall bug (see memory dubcut-scene-editor) — the refs
    // are only ever read inside event handlers / requestAnimationFrame, never in the JSX
    // render path, so the react-hooks/refs error is a false positive here.
    files: [
      'src/components/WaveTrack.tsx',
      'src/components/FilmTimeline.tsx',
      'src/components/SceneEditor.tsx',
      'src/components/SubtitleEditor.tsx',
    ],
    rules: {
      'react-hooks/refs': 'off',
    },
  },
])
