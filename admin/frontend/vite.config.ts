import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'

const SPA_INDEX = '/admin/static/index.html'
// Page routes that should serve the SPA (not proxied to backend)
const SPA_ROUTES = new Set([
  '/admin/', '/admin/dashboard', '/admin/usage', '/admin/sandbox',
  '/admin/soul', '/admin/soul/persona-guide', '/admin/schedule', '/admin/memory', '/admin/affection',
  '/admin/stickers', '/admin/knowledge', '/admin/memos', '/admin/slang', '/admin/groups',
  '/admin/plugins', '/admin/scheduler', '/admin/config', '/admin/system',
  '/admin/logs', '/admin/design-playground',
])
const SPA_ROUTE_PREFIXES = [
  '/admin/plugins/',
]

function isSpaRoute(pathname: string) {
  return pathname === '/admin'
    || SPA_ROUTES.has(pathname)
    || SPA_ROUTE_PREFIXES.some(prefix => pathname.startsWith(prefix))
}

export default defineConfig({
  plugins: [
    vue(),
    UnoCSS(),
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [NaiveUiResolver()],
      dts: 'src/components.d.ts',
    }),
  ],
  base: '/admin/static/',
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8081',
      '/admin': {
        target: 'http://localhost:8081',
        bypass(req) {
          const url = req.url || ''
          const pathname = new URL(url, 'http://localhost').pathname
          // Vite serves its own assets (HMR, source files, static)
          if (pathname.startsWith('/admin/static')) return url
          // SPA page routes — serve index.html (client-side routing)
          if (isSpaRoute(pathname)) return SPA_INDEX
          // Stale HTML routes (login/logout) — still served by backend
        },
      },
    },
  },
})
