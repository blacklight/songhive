import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

const allowedHosts = process.env.VITE_ALLOWED_HOSTS
  ? process.env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim()).filter(Boolean)
  : []

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    host: true,
    allowedHosts,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        autoRewrite: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/manifest.webmanifest': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../songhive/static',
    emptyOutDir: true,
  },
})
