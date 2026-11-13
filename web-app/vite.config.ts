import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // ws: the watch-room socket lives under /api too
    proxy: { '/api': { target: 'http://localhost:8787', changeOrigin: true, ws: true } },
  },
  preview: { port: 4173 },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 700,
  },
})
