import { useCallback, useEffect, useRef, useState } from 'react'
import { request } from '../api/client'

export const textCapabilities = ['chat', 'text_generation', 'code_generation', 'code_editing', 'reasoning', 'structured_output'] as const
export interface ProviderProfile {
  provider_name: string; integration: 'groq' | 'gemini' | 'openrouter' | 'cloudflare'
  selected_model: string; priority: number; model_assignments: Partial<Record<typeof textCapabilities[number], string>>
  enabled: boolean; revision: number; credential_configured: boolean; credential_evidence_matches: boolean; state: string
  credential_fingerprint?: string | null
}
export interface ProviderConfiguration {
  profiles: ProviderProfile[]; execution_mode: 'disabled' | 'local_only' | 'free_only'
  cloud_execution_enabled: boolean; credential_storage: 'windows_dpapi' | 'unavailable'
}
export interface ProviderMetadata {
  provider_name: string; profile_revision: number; state: string; exact_model_available: boolean | null; models: string[]
}

/** Redacted projection only. Credentials pass directly to fetch and never enter shared state. */
export function useProviderConfigurationState(actorId: string, selectActor: (id: string) => void) {
  const [active, setActive] = useState(false)
  const [projection, setProjection] = useState<{ actorId: string; data: ProviderConfiguration | null; error: string | null; loading: boolean }>({ actorId: '', data: null, error: null, loading: false })
  const generation = useRef(0)
  const [catalog, setCatalog] = useState<{ actorId: string; result: ProviderMetadata } | null>(null)
  const currentActor = useRef(actorId)
  useEffect(() => { currentActor.current = actorId }, [actorId])
  const refreshProviders = useCallback(async () => {
    if (!actorId) return
    const ticket = ++generation.current
    setProjection(previous => ({ actorId, data: previous.actorId === actorId ? previous.data : null, loading: true, error: null }))
    try {
      const data = await request<ProviderConfiguration>('/api/model-configuration/profiles', { headers: { 'X-Jarvis-Actor-Id': actorId } })
      if (ticket === generation.current) setProjection({ actorId, data, error: null, loading: false })
    } catch {
      if (ticket === generation.current) setProjection({ actorId, data: null, error: 'Provider settings could not be loaded. Select an authorized local model operator.', loading: false })
    }
  }, [actorId])
  useEffect(() => {
    let cancelled = false
    if (active) void Promise.resolve().then(() => { if (!cancelled) void refreshProviders() })
    return () => { cancelled = true; generation.current += 1 }
  }, [active, refreshProviders])
  const configureOperator = useCallback(async () => {
    const result = await request<{ actor_id: string }>('/api/model-configuration/setup', {
      method: 'POST', body: JSON.stringify({ confirmation: 'configure_model_operator' }),
    })
    selectActor(result.actor_id)
  }, [selectActor])
  const saveProvider = useCallback(async (body: unknown) => {
    await request<ProviderProfile>('/api/model-configuration/profiles', {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify(body),
    })
    if (currentActor.current === actorId) await refreshProviders()
  }, [actorId, refreshProviders])
  const checkMetadata = useCallback(async (provider: string, revision: number) => {
    const ticket = generation.current
    const result = await request<ProviderMetadata>(`/api/model-configuration/profiles/${encodeURIComponent(provider)}/metadata`, {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify({ expected_revision: revision }),
    })
    if (ticket === generation.current && currentActor.current === actorId) setCatalog({ actorId, result })
  }, [actorId])
  const metadata = catalog?.actorId === actorId && projection.actorId === actorId && projection.data?.profiles.some(
    profile => profile.provider_name === catalog.result.provider_name && profile.revision === catalog.result.profile_revision,
  ) ? catalog.result : null
  return { active, setActive, configureOperator, saveProvider, refreshProviders, checkMetadata, metadata,
    data: projection.actorId === actorId ? projection.data : null,
    error: projection.actorId === actorId ? projection.error : null,
    loading: projection.actorId === actorId && projection.loading }
}
