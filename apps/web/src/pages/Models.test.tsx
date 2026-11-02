import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Models } from './Models'
import { useAppStore } from '../state/AppStore'

vi.mock('../state/AppStore', () => ({ useAppStore: vi.fn() }))
const providers = {
  setActive: vi.fn(), configureOperator: vi.fn(), saveProvider: vi.fn(), refreshProviders: vi.fn(),
  loading: false, error: null,
  data: { profiles: [], execution_mode: 'disabled', cloud_execution_enabled: false, credential_storage: 'windows_dpapi' },
}

beforeEach(() => {
  vi.clearAllMocks()
  providers.saveProvider.mockResolvedValue(undefined)
  vi.mocked(useAppStore).mockReturnValue({ providers, runtime: { actorId: 'model-operator' } } as unknown as ReturnType<typeof useAppStore>)
})

describe('AI provider configuration', () => {
  it('submits a write-only key, clears the field, and keeps activation separate', async () => {
    render(<Models/>)
    expect(providers.setActive).toHaveBeenCalledWith(true)
    expect(screen.getByText(/Hosted execution: inactive/)).toBeInTheDocument()
    const input = screen.getByLabelText('API key') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'gsk_synthetic_secret_for_form' } })
    fireEvent.change(screen.getByLabelText('Exact model'), { target: { value: 'vendor/model' } })
    fireEvent.change(screen.getByLabelText('code generation'), { target: { value: 'vendor/code' } })
    fireEvent.submit(input.form!)
    expect(input.value).toBe('')
    await waitFor(() => expect(providers.saveProvider).toHaveBeenCalledTimes(1))
    expect(providers.saveProvider).toHaveBeenCalledWith(expect.objectContaining({
      provider_name: 'groq', api_key: 'gsk_synthetic_secret_for_form', enabled: false,
      model_assignments: { code_generation: 'vendor/code' }, expected_revision: 0,
    }))
    expect(document.body.textContent).not.toContain('gsk_synthetic_secret_for_form')
    expect(providers.configureOperator).not.toHaveBeenCalled()
    await screen.findByText(/Provider saved/)
  })

  it('does not echo secret-bearing request errors', async () => {
    providers.saveProvider.mockRejectedValueOnce(new Error('gsk_error_must_remain_private'))
    render(<Models/>)
    fireEvent.submit(screen.getByLabelText('API key').closest('form')!)
    await screen.findByText(/Provider was not saved/)
    expect(document.body.textContent).not.toContain('gsk_error_must_remain_private')
  })
})
