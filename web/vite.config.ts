import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base is set from BASE_PATH so the same build works on GitHub Pages
// (served from /<repo>/) and on Cloudflare Pages or a custom domain (served from /).
export default defineConfig({
  plugins: [react()],
  base: process.env.BASE_PATH || '/',
  build: { outDir: 'dist', sourcemap: false },
})
