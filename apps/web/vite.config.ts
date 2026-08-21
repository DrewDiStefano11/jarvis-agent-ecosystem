import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'

export const buildEndpoint = (value: string | undefined, fallback: string): string =>
  value ?? fallback

export const buildEndpoints = (env: Record<string, string | undefined>) => {
  const host = env.API_HOST ?? '127.0.0.1'
  const port = env.API_PORT ?? '8000'
  const urlHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
  return {
    apiBaseUrl: buildEndpoint(env.VITE_API_BASE_URL, `http://${urlHost}:${port}`),
    webSocketUrl: buildEndpoint(env.VITE_WS_URL, `ws://${urlHost}:${port}/ws/events`)
  }
}

const runtimeMetadata = (mode: string): Plugin => {
  const env = {
    ...loadEnv(mode, '../..', ''),
    ...loadEnv(mode, '../api', ''),
    ...loadEnv(mode, '.', '')
  }
  const { apiBaseUrl, webSocketUrl } = buildEndpoints(env)
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
