import { describe, expect, it } from 'vitest'

import { buildEndpoint } from '../vite.config'

describe('runtime supervisor build metadata', () => {
  it('preserves explicitly empty endpoints like the bundled client', () => {
    expect(buildEndpoint('', 'http://127.0.0.1:8000')).toBe('')
  })

  it('uses the fallback only when an endpoint is absent', () => {
    expect(buildEndpoint(undefined, 'http://127.0.0.1:8000')).toBe(
      'http://127.0.0.1:8000'
    )
  })
})
