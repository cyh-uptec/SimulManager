import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 시 backend(8100)로 프록시 — 운영은 FastAPI가 dist/를 직접 서빙
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8100',
      '/ws': { target: 'ws://localhost:8100', ws: true },
    },
  },
})
