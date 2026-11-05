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
  it('shows a prepared request without sending until its explicit one-request approval', async () => {
    const approve=vi.fn().mockResolvedValue(undefined)
    const profile={provider_name:'groq',integration:'groq',selected_model:'openai/gpt-oss-120b',priority:50,model_assignments:{},enabled:false,revision:2,credential_configured:true,credential_evidence_matches:false,state:'evidence_required'}
    const acceptance={id:'acceptance-1',plan_hash:'a'.repeat(64),status:'prepared',initialized_capacity:false,result_code:null,
      plan:{provider_name:'groq',profile_revision:2,expires_at:'2099-01-01T00:00:00Z',endpoint:'https://api.groq.com/openai/v1/chat/completions',maximum_requests:1,paid_ceiling_usd:0,ongoing_activation:false,token_reservation:8000,
        evidence:{account_id:'fixture-account',model:profile.selected_model,limits:{rpm:30,rpd:1000,tpm:8000,tpd:200000}},request_body:{model:profile.selected_model,max_completion_tokens:512}}}
    vi.mocked(useAppStore).mockReturnValue({providers:{...providers,data:{...providers.data,profiles:[profile]},acceptance,
      approveAndExecuteGroqAcceptance:approve,refreshGroqAcceptance:vi.fn()},runtime:{actorId:'model-operator'}} as unknown as ReturnType<typeof useAppStore>)
    render(<Models/>)
    expect(approve).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button',{name:'Approve and send exactly one Groq request'}))
    await waitFor(()=>expect(approve).toHaveBeenCalledWith('acceptance-1','a'.repeat(64)))
    expect(providers.saveProvider).not.toHaveBeenCalled()
    expect(screen.getByText(/Hosted execution: inactive/)).toBeInTheDocument()
  })
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
