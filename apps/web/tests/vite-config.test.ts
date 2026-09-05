import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { build } from 'vite'

import { buildEndpoint, buildEndpoints, runtimeBuildConfig } from '../vite.config'

afterEach(() => vi.unstubAllEnvs())

describe('runtime supervisor build metadata', () => {
  it('preserves explicitly empty endpoints like the bundled client', () => {
    expect(buildEndpoint('', 'http://127.0.0.1:8000')).toBe('')
  })

  it('uses the fallback only when an endpoint is absent', () => {
    expect(buildEndpoint(undefined, 'http://127.0.0.1:8000')).toBe(
      'http://127.0.0.1:8000'
    )
  })

  it('derives build metadata from the supervised API bind', () => {
    expect(buildEndpoints({ API_HOST: 'localhost', API_PORT: '8123' })).toEqual({
      apiBaseUrl: 'http://localhost:8123',
      webSocketUrl: 'ws://localhost:8123/ws/events'
    })
  })

  it('keeps explicit Vite endpoints above derived bind values', () => {
    expect(
      buildEndpoints({
        API_HOST: 'localhost',
        API_PORT: '8123',
        VITE_API_BASE_URL: 'http://127.0.0.1:9000',
        VITE_WS_URL: 'ws://127.0.0.1:9000/ws/events'
      })
    ).toEqual({
      apiBaseUrl: 'http://127.0.0.1:9000',
      webSocketUrl: 'ws://127.0.0.1:9000/ws/events'
    })
  })

  it.each([
    { source: 'repository', directory: '.', env: 'API_HOST=localhost\nAPI_PORT=8123', host: 'localhost', port: 8123 },
    { source: 'API', directory: 'apps/api', env: 'VITE_API_BASE_URL=http://127.0.0.1:8234\nVITE_WS_URL=ws://127.0.0.1:8234/ws/events', host: '127.0.0.1', port: 8234 },
    { source: 'web', directory: 'apps/web', env: 'API_HOST=localhost\nAPI_PORT=8345', host: 'localhost', port: 8345 }
  ])('compiles $source settings into both browser code and metadata', async ({ directory, env, host, port }) => {
    for (const key of ['API_HOST', 'API_PORT', 'VITE_API_BASE_URL', 'VITE_WS_URL']) vi.stubEnv(key, undefined)
    const repository = await mkdtemp(join(tmpdir(), 'jarvis-build-endpoints-'))
    const webDirectory = join(repository, 'apps/web')
    if (!resolve(repository).startsWith(`${resolve(tmpdir())}${sep}jarvis-build-endpoints-`)) {
      throw new Error('Refusing to remove a directory outside the endpoint fixture')
    }
    try {
      await mkdir(webDirectory, { recursive: true })
      await mkdir(join(repository, 'apps/api'), { recursive: true })
      await writeFile(join(repository, directory, '.env'), `${env}\nPRIVATE_TEST_VALUE=must-stay-private\n`)
      const result = await build({
        ...runtimeBuildConfig('production', webDirectory),
        configFile: false,
        root: webDirectory,
        logLevel: 'silent',
        build: {
          write: false,
          minify: false,
          lib: { entry: resolve('src/api/client.ts'), formats: ['es'], fileName: 'client' }
        }
      })
      const bundle = Array.isArray(result) ? result[0] : result
      if (!bundle || !('output' in bundle)) throw new Error('Expected a completed browser build')
      const code = bundle.output.filter(item => item.type === 'chunk').map(item => item.code).join('\n')
      const metadata = bundle.output.find(item => item.type === 'asset' && item.fileName === 'runtime-supervisor.json')
      if (!metadata || metadata.type !== 'asset') throw new Error('Expected supervisor metadata')
      const expected = { apiBaseUrl: `http://${host}:${port}`, webSocketUrl: `ws://${host}:${port}/ws/events` }
      expect(JSON.parse(String(metadata.source))).toEqual({ schemaVersion: 1, ...expected })
      expect(code).toContain(JSON.stringify(expected.apiBaseUrl))
      expect(code).toContain(JSON.stringify(expected.webSocketUrl))
      expect(code).not.toContain('http://127.0.0.1:8000')
      expect(code).not.toContain('must-stay-private')
    } finally {
      await rm(repository, { recursive: true, force: true })
    }
  })
})
