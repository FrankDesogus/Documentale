/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
    './*/templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        elt: {
          navy: {
            950: '#050B24',
            900: '#081331',
            800: '#102143',
          },
          blue: {
            600: '#0B5EA8',
          },
          cyan: {
            500: '#10B8D4',
            300: '#67E3EE',
          },
          slate: {
            400: '#94A3B8',
          },
          surface: {
            50: '#F6F8FC',
            100: '#EEF3F8',
          },
          border: {
            light: '#DCE4EF',
          },
          text: {
            dark: '#081331',
            muted: '#64748B',
          },
          white: '#F8FAFC',
        },
        brand: {
          50:  '#EAFBFF',
          100: '#CFF7FE',
          200: '#A6EEF8',
          300: '#67E3EE',
          400: '#2DCCE0',
          500: '#10B8D4',
          600: '#0B5EA8',
          700: '#164D86',
          800: '#102143',
          900: '#081331',
        },
      },
      opacity: {
        '3': '0.03',
        '7': '0.07',
        '12': '0.12',
        '13': '0.13',
        '14': '0.14',
        '18': '0.18',
        '65': '0.65',
        '85': '0.85',
        '88': '0.88',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
}
