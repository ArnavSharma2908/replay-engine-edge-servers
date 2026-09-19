/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#0D0B08',
          900: '#141109',
          800: '#1C170F',
          700: '#2A2317',
          600: '#3A3121',
          500: '#5C5138'
        },
        signal: {
          500: '#FFB000',
          400: '#FFC23D',
          300: '#FFD470'
        },
        origin: {
          500: '#4A90D9',
          400: '#6FA8E0'
        },
        parchment: '#F5EFE6'
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace']
      }
    }
  },
  plugins: []
}
