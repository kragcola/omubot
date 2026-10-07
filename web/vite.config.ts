import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const backendTarget = 'http://127.0.0.1:18081'
const backendPaths = ['/api', '/session', '/logout', '/admin']
const backendProxy = Object.fromEntries(backendPaths.map((path) => [path, { target: backendTarget }]))

export default defineConfig({
  base: '/web/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    // Keep the browser Host/Origin (localhost:5173) so the backend computes
    // the same base URL during local development. Production is same-origin.
    proxy: backendProxy,
  },
  build: {
    outDir: '../src/omubot_new/web_static',
    emptyOutDir: true,
  },
})
