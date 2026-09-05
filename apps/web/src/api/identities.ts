import { ApiError, request } from './client'
import type { IdentityRegistration, RuntimeIdentity } from '../types/runtime'

/** Read every page so a new registration cannot disappear behind the first 100. */
export async function identityPages<T>(path: string): Promise<T[]> {
  const all: T[] = []
  for (let offset = 0; offset < 10_000; offset += 100) {
    const page = await request<T[]>(`${path}${path.includes('?') ? '&' : '?'}limit=100&offset=${offset}`)
    all.push(...page)
    if (page.length < 100) return all
  }
  throw new Error('This registry exceeds the 10,000 identity display limit. Use the identity API to narrow it.')
}

export async function registerIdentity(body: IdentityRegistration): Promise<{ identity: RuntimeIdentity; recovered: boolean }> {
  try {
    const identity = await request<RuntimeIdentity>('/api/identity/agents', { method: 'POST', body: JSON.stringify(body) })
    return { identity, recovered: false }
  } catch (error) {
    // Stable keys are unique in the authoritative database. Resolve an unknown
    // acknowledgement or duplicate-key response without creating another ID.
    if (error instanceof ApiError && error.status < 500 && error.code !== 'DUPLICATE_STABLE_KEY') throw error
    const identities = await identityPages<RuntimeIdentity>('/api/identity/agents').catch(() => null)
    const existing = identities?.find(identity => identity.stable_key === body.stable_key)
    if (existing && existing.display_name === body.display_name && existing.description === body.description
      && existing.agent_type === body.agent_type) return { identity: existing, recovered: true }
    if (existing) throw new Error('That stable key belongs to a different profile. The existing identity was left unchanged.', { cause: error })
    throw error
  }
}
