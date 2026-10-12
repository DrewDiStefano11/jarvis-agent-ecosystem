import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'
import { resolve } from 'node:path'

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

const runtimeMetadata = ({ apiBaseUrl, webSocketUrl }: ReturnType<typeof buildEndpoints>): Plugin => {
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

export const runtimeBuildConfig = (mode: string, webDirectory = process.cwd()) => {
  const endpoints = buildEndpoints({
    ...loadEnv(mode, resolve(webDirectory, '../..'), ''),
    ...loadEnv(mode, resolve(webDirectory, '../api'), ''),
    ...loadEnv(mode, webDirectory, '')
  })
  return {
    plugins: [react(), runtimeMetadata(endpoints)],
    define: {
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(endpoints.apiBaseUrl),
      'import.meta.env.VITE_WS_URL': JSON.stringify(endpoints.webSocketUrl)
    }
  }
}

// Opt-in development proxy for same-origin previews (for example a sandboxed
// browser that cannot reach the loopback API directly). Pair it with
// VITE_API_BASE_URL= (empty) and VITE_WS_URL=/ws/events. Never used in builds.
export const devProxy = (target: string | undefined) =>
  target
    ? {
        '/api': { target, changeOrigin: true },
        '/ws': { target: target.replace(/^http/, 'ws'), ws: true, changeOrigin: true }
      }
    : undefined

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    ...runtimeBuildConfig(mode),
    server: { port: 5173, proxy: devProxy(env.JARVIS_DEV_PROXY_TARGET) },
    test: { environment: 'jsdom', setupFiles: './tests/setup.ts', globals: true, css: true }
  }
})
