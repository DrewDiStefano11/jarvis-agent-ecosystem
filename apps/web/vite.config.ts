import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'

export const buildEndpoint = (value: string | undefined, fallback: string): string =>
  value ?? fallback

const runtimeMetadata = (mode: string): Plugin => {
  const env = loadEnv(mode, '.', 'VITE_')
  const apiBaseUrl = buildEndpoint(env.VITE_API_BASE_URL, 'http://127.0.0.1:8000')
  const webSocketUrl = buildEndpoint(env.VITE_WS_URL, 'ws://127.0.0.1:8000/ws/events')
  return {
    name: 'jarvis-runtime-metadata',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'runtime-supervisor.json',
        source: `${JSON.stringify({ schemaVersion: 1, apiBaseUrl, webSocketUrl }, null, 2)}\n`
      })
    }
  }
}

export default defineConfig(({ mode }) => {
  return {
    plugins: [react(), runtimeMetadata(mode)],
    server: { port: 5173 },
    test: { environment: 'jsdom', setupFiles: './tests/setup.ts', globals: true, css: true }
  }
})
