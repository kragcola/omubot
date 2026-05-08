import presetRemToPx from '@unocss/preset-rem-to-px'
import { defineConfig, presetAttributify, presetIcons, presetWind3 } from 'unocss'

export default defineConfig({
  presets: [
    presetWind3(),
    presetAttributify(),
    presetIcons({
      prefix: ['i-'],
      extraProperties: {
        display: 'inline-block',
        width: '1em',
        height: '1em',
      },
    }),
    presetRemToPx({ baseFontSize: 4 }),
  ],
  shortcuts: [
    ['wh-full', 'w-full h-full'],
    ['f-c-c', 'flex justify-center items-center'],
    ['flex-center', 'flex justify-center items-center'],
    ['flex-col', 'flex flex-col'],
    ['card-border', 'border border-solid border-[var(--om-border)]'],
    ['auto-bg', 'bg-[var(--om-surface)]'],
    ['auto-bg-hover', 'hover:bg-[var(--om-surface-2)]'],
    ['auto-bg-highlight', 'bg-[var(--om-surface-2)]'],
    ['text-highlight', 'rounded-8 px-10 py-4 bg-[var(--om-surface-2)] text-[var(--om-text-2)]'],
    ['card-shadow', 'shadow-[0_16px_40px_rgba(23,42,48,0.12)] dark:shadow-[0_18px_44px_rgba(0,0,0,0.34)]'],
  ],
  theme: {
    colors: {
      primary: 'rgba(var(--primary-color))',
      dark: '#18181c',
      light_border: '#efeff5',
      dark_border: '#2d2d30',
    },
  },
})
