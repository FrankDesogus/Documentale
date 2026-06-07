/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './*/templates/**/*.html',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#f0f7ff',
          100: '#e0eeff',
          200: '#b9d9ff',
          300: '#7cbfff',
          400: '#3a9ef5',
          500: '#1a7fd4',
          600: '#1362a8',
          700: '#0f4a87',
          800: '#0d3a6b',
          900: '#0f2840',
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
}
