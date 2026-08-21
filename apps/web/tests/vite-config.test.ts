import { describe, expect, it } from 'vitest'

import { buildEndpoint, buildEndpoints } from '../vite.config'

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
})
