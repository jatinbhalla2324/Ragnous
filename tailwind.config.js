/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        /* ── Surfaces: white canvas, warm-neutral chrome ── */
        canvas: '#FFFFFF',
        surface: '#F9F9F9',
        'surface-2': '#F4F4F4',
        'surface-3': '#ECECEC',
        line: '#E8E8E8',
        'line-strong': '#D9D9D9',

        /* ── Text ── */
        ink: '#0D0D0D',
        'ink-2': '#4A4A4A',
        'ink-3': '#8E8E8E',
        'ink-4': '#B4B4B4',

        /* ── Emphasis: black is the only accent this design has ── */
        brand: '#0D0D0D',
        'brand-soft': '#F4F4F4',
        'brand-line': '#D9D9D9',

        /* ── Semantic, told apart by weight rather than hue ── */
        ok: '#0D0D0D',
        'ok-soft': '#F4F4F4',
        warn: '#5D5D5D',
        'warn-soft': '#F4F4F4',
        danger: '#0D0D0D',
        'danger-soft': '#F0F0F0',

        /* ── Back-compat aliases ── */
        signal: '#0D0D0D',
        'signal-hover': '#262626',
        fog: '#8E8E8E',
        mist: '#0D0D0D',
      },
      fontFamily: {
        sans: ['Inter', 'Noto Sans Devanagari', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        display: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        '4xl': '1.75rem',
      },
      boxShadow: {
        card: '0 1px 2px rgba(13,13,13,0.04), 0 1px 3px rgba(13,13,13,0.03)',
        raised: '0 2px 6px rgba(13,13,13,0.05), 0 8px 24px -12px rgba(13,13,13,0.12)',
        composer: '0 2px 6px rgba(13,13,13,0.04), 0 10px 32px -16px rgba(13,13,13,0.22)',
        pop: '0 8px 28px -8px rgba(13,13,13,0.18), 0 2px 6px rgba(13,13,13,0.06)',
      },
      keyframes: {
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        popIn: {
          '0%': { opacity: '0', transform: 'translateY(4px) scale(0.98)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        dotPulse: {
          '0%, 100%': { opacity: '0.25', transform: 'scale(0.7)' },
          '50%': { opacity: '1', transform: 'scale(1)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '160% 0' },
          '100%': { backgroundPosition: '-60% 0' },
        },
      },
      animation: {
        fadeUp: 'fadeUp 0.32s cubic-bezier(0.22, 1, 0.36, 1) both',
        fadeIn: 'fadeIn 0.25s ease-out both',
        popIn: 'popIn 0.16s cubic-bezier(0.22, 1, 0.36, 1) both',
        shimmer: 'shimmer 1.9s linear infinite',
      },
    },
  },
  plugins: [],
};
