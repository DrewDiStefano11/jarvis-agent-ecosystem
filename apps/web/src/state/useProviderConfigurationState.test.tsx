import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { useProviderConfigurationState, type ProviderConfiguration } from './useProviderConfigurationState'

vi.mock('../api/client', () => ({ request: vi.fn() }))
beforeEach(() => vi.clearAllMocks())

it('does not execute under an old operator after approval resolves', async () => {
  const prepared={id:'acceptance-1',plan_hash:'a'.repeat(64),status:'prepared',plan:{provider_name:'groq',profile_revision:2},initialized_capacity:false}
  let resolveApproval!:(value:unknown)=>void
  vi.mocked(request).mockImplementation(path=>path.endsWith('/approve') ? new Promise(resolve=>{resolveApproval=resolve}) : Promise.resolve(prepared))
  const hook=renderHook(({actor})=>useProviderConfigurationState(actor,vi.fn()),{initialProps:{actor:'operator-a'}})
  await act(async()=>{await hook.result.current.prepareGroqAcceptance({})})
  let pending!:Promise<void>
  act(()=>{pending=hook.result.current.approveAndExecuteGroqAcceptance('acceptance-1','a'.repeat(64)).catch(()=>undefined)})
  hook.rerender({actor:'operator-b'})
  expect(hook.result.current.acceptance).toBeNull()
  await act(async()=>{resolveApproval({...prepared,status:'approved'});await pending})
  expect(vi.mocked(request).mock.calls.some(call=>call[0].endsWith('/execute'))).toBe(false)
})

it('prepares without inference and sends only after a separate explicit approval action', async () => {
  vi.clearAllMocks()
  const prepared = { id: 'acceptance-1', plan_hash: 'a'.repeat(64), status: 'prepared', plan: { provider_name: 'groq', profile_revision: 2 }, initialized_capacity: false }
  vi.mocked(request).mockImplementation(async path => path.endsWith('/approve') ? { ...prepared, status: 'approved' } : path.endsWith('/execute') ? { ...prepared, status: 'succeeded' } : prepared)
  const hook = renderHook(() => useProviderConfigurationState('operator-a', vi.fn()))
  await act(async () => { await hook.result.current.prepareGroqAcceptance({ provider_name: 'groq', expected_revision: 2, account_evidence: {} }) })
  expect(vi.mocked(request).mock.calls.map(call => call[0])).toEqual(['/api/model-configuration/groq/acceptance/prepare'])
  await act(async () => { await hook.result.current.approveAndExecuteGroqAcceptance('acceptance-1', 'a'.repeat(64)) })
  expect(vi.mocked(request).mock.calls.map(call => call[0])).toEqual([
    '/api/model-configuration/groq/acceptance/prepare', '/api/model-configuration/groq/acceptance/acceptance-1/approve', '/api/model-configuration/groq/acceptance/acceptance-1/execute',
  ])
  expect(vi.mocked(request).mock.calls[1]?.[1]?.body).toBe(JSON.stringify({ expected_plan_hash: 'a'.repeat(64), confirmation: 'approve_exactly_one_groq_request' }))
  expect(hook.result.current.acceptance?.status).toBe('succeeded')
})

it('does not fetch off-page or expose another identity’s saved provider projection', async () => {
  const data: ProviderConfiguration = { profiles: [], execution_mode: 'disabled', cloud_execution_enabled: false, credential_storage: 'windows_dpapi' }
  const pending: Array<(value: ProviderConfiguration) => void> = []
  vi.mocked(request).mockImplementation(() => new Promise(resolve => { pending.push(resolve as (value: ProviderConfiguration) => void) }))
  const selectActor = vi.fn()
  const hook = renderHook(({ actor }) => useProviderConfigurationState(actor, selectActor), { initialProps: { actor: 'operator-a' } })
  expect(request).not.toHaveBeenCalled()
  act(() => hook.result.current.setActive(true))
  await waitFor(() => expect(pending).toHaveLength(1))
  await act(async () => { pending[0]!(data) })
  expect(hook.result.current.data).toEqual(data)
  hook.rerender({ actor: 'operator-b' })
  expect(hook.result.current.data).toBeNull()
  await waitFor(() => expect(pending).toHaveLength(2))
  expect(hook.result.current.data).toBeNull()
  hook.unmount()
  await act(async () => { pending[1]!(data) })
})
