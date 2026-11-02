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
export interface GroqAcceptance {
  id: string; plan_hash: string; status: 'prepared' | 'approved' | 'dispatch_started' | 'succeeded' | 'failed' | 'uncertain'
  initialized_capacity: boolean; result_code: string | null
  plan: { provider_name: string; profile_revision: number; expires_at: string; endpoint: string; maximum_requests: 1; paid_ceiling_usd: 0; ongoing_activation: false; token_reservation: number; policy_digest: string; request_body: Record<string, unknown>; evidence: { account_id: string; model: string; limits: { rpm: number; rpd: number; tpm: number; tpd: number } } }
  quota_observation: { received_at: string; rpd_remaining: number; rpd_resets_at: string; tpm_remaining: number; tpm_resets_at: string } | null
}

/** Redacted projection only. Credentials pass directly to fetch and never enter shared state. */
export function useProviderConfigurationState(actorId: string, selectActor: (id: string) => void) {
  const [active, setActive] = useState(false)
  const [projection, setProjection] = useState<{ actorId: string; data: ProviderConfiguration | null; error: string | null; loading: boolean }>({ actorId: '', data: null, error: null, loading: false })
  const generation = useRef(0)
  const [catalog, setCatalog] = useState<{ actorId: string; result: ProviderMetadata } | null>(null)
  const [acceptanceProjection, setAcceptanceProjection] = useState<{ actorId: string; result: GroqAcceptance } | null>(null)
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
  const prepareGroqAcceptance = useCallback(async (body: unknown) => {
    const result = await request<GroqAcceptance>('/api/model-configuration/groq/acceptance/prepare', {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify(body),
    })
    if (currentActor.current === actorId) setAcceptanceProjection({ actorId, result })
  }, [actorId])
  const refreshGroqAcceptance = useCallback(async (identifier: string) => {
    const result = await request<GroqAcceptance>(`/api/model-configuration/groq/acceptance/${encodeURIComponent(identifier)}`, {
      headers: { 'X-Jarvis-Actor-Id': actorId },
    })
    if (currentActor.current === actorId) setAcceptanceProjection({ actorId, result })
  }, [actorId])
  const approveAndExecuteGroqAcceptance = useCallback(async (identifier: string, hash: string) => {
    const current = acceptanceProjection?.actorId === actorId ? acceptanceProjection.result : null
    if (!current || current.id !== identifier || current.plan_hash !== hash || !['prepared', 'approved'].includes(current.status)) throw new Error('Review the current acceptance plan first')
    const base = `/api/model-configuration/groq/acceptance/${encodeURIComponent(identifier)}`
    if (current.status === 'prepared') {
      const approved = await request<GroqAcceptance>(`${base}/approve`, {
        method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify({ expected_plan_hash: hash, confirmation: 'approve_exactly_one_groq_request' }),
      })
      if (currentActor.current !== actorId) throw new Error('Operator changed')
      setAcceptanceProjection({ actorId, result: approved })
    }
    if (currentActor.current !== actorId) throw new Error('Operator changed')
    const result = await request<GroqAcceptance>(`${base}/execute`, {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify({ expected_plan_hash: hash }),
    })
    if (currentActor.current === actorId) setAcceptanceProjection({ actorId, result })
  }, [acceptanceProjection, actorId])
  const metadata = catalog?.actorId === actorId && projection.actorId === actorId && projection.data?.profiles.some(
    profile => profile.provider_name === catalog.result.provider_name && profile.revision === catalog.result.profile_revision,
  ) ? catalog.result : null
  return { active, setActive, configureOperator, saveProvider, refreshProviders, checkMetadata, metadata,
    prepareGroqAcceptance, refreshGroqAcceptance, approveAndExecuteGroqAcceptance,
    acceptance: acceptanceProjection?.actorId === actorId ? acceptanceProjection.result : null,
    data: projection.actorId === actorId ? projection.data : null,
    error: projection.actorId === actorId ? projection.error : null,
    loading: projection.actorId === actorId && projection.loading }
}
