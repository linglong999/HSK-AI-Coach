import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    // 开发期把 /api/* 代理到 FastAPI 后端（生产由后端双壳托管 web/dist）
    proxy: {
      '/api': { target: 'http://127.0.0.1:8612', changeOrigin: true },
    },
  },
})