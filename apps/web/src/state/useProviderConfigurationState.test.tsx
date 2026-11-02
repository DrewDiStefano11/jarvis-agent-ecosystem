import { act, renderHook, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { useProviderConfigurationState, type ProviderConfiguration } from './useProviderConfigurationState'

vi.mock('../api/client', () => ({ request: vi.fn() }))

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
