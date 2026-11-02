import { useEffect, useState, type FormEvent } from 'react'
import { useAppStore } from '../state/AppStore'
import { textCapabilities } from '../state/useProviderConfigurationState'
import '../styles/providers.css'

export function Models() {
  const { providers, runtime } = useAppStore()
  const { setActive } = providers
  const [selection, setSelection] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => { setActive(true); return () => setActive(false) }, [setActive])
  const selected = providers.data?.profiles.find(profile => profile.provider_name === selection)
  const acceptance = providers.acceptance
  const preparedProfile = providers.data?.profiles.find(profile => profile.provider_name === acceptance?.plan.provider_name)
  const canSendAcceptance = Boolean(acceptance && ['prepared', 'approved'].includes(acceptance.status)
    && preparedProfile && !preparedProfile.enabled && preparedProfile.revision === acceptance.plan.profile_revision
    && !providers.data?.cloud_execution_enabled)
  async function setup() {
    setBusy(true); setMessage('')
    try { await providers.configureOperator(); setMessage('Local model operator selected. This identity can configure providers.') }
    catch { setMessage('Operator setup failed. Existing denials and inactive identities are preserved.') }
    finally { setBusy(false) }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    const keyInput = form.elements.namedItem('api_key') as HTMLInputElement
    keyInput.value = ''
    setBusy(true); setMessage('')
    try {
      const evidence = fields.get('allowance') as File | null
      if (evidence && evidence.size > 65536) throw new Error('Oversized evidence')
      const apiKey = fields.get('api_key')?.toString() ?? ''
      await providers.saveProvider({
        provider_name: fields.get('provider_name'), integration: fields.get('integration'),
        selected_model: fields.get('selected_model'), priority: Number(fields.get('priority')),
        model_assignments: Object.fromEntries(textCapabilities.flatMap(capability => {
          const model = fields.get(capability)?.toString().trim()
          return model ? [[capability, model]] : []
        })),
        enabled: fields.get('enabled') === 'on', clear_credential: fields.get('clear_credential') === 'on',
        expected_revision: selected?.revision ?? 0,
        ...(apiKey ? { api_key: apiKey } : {}),
        allowance: evidence?.size ? JSON.parse(await evidence.text()) : null,
      })
      setSelection(fields.get('provider_name')?.toString() ?? '')
      setMessage('Provider saved. Keys are hidden. Activation and live acceptance remain separate; restart services after configuration changes.')
    } catch {
      setMessage('Provider was not saved. Check the current revision, model, protected credential and verified nonbillable allowance, then refresh and retry.')
    } finally { setBusy(false) }
  }
  async function checkMetadata(name: string, revision: number) {
    setBusy(true); setMessage('')
    try { await providers.checkMetadata(name, revision) }
    catch { setMessage('Model metadata could not be checked. Refresh the current profile and verify operator access.') }
    finally { setBusy(false) }
  }
  async function prepareAcceptance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const file = new FormData(form).get('account_evidence') as File | null
    ;(form.elements.namedItem('account_evidence') as HTMLInputElement).value = ''
    setBusy(true); setMessage('')
    try {
      if (!selected || !file?.size || file.size > 32768) throw new Error('Current account evidence is required')
      const raw = await file.text()
      if (/"(?:api_key|access_token|refresh_token|authorization)"\s*:/i.test(raw) || /\bgsk_[A-Za-z0-9]{8,}/.test(raw)) throw new Error('Keep keys out of evidence')
      await providers.prepareGroqAcceptance({ provider_name: selected.provider_name, expected_revision: selected.revision, account_evidence: JSON.parse(raw) })
      setMessage('Acceptance prepared. Review the exact request and approve separately to send it.')
    } catch { setMessage('Acceptance was not prepared. Verify the current Personal Free account evidence, key association, model limits and saved profile revision.') }
    finally { setBusy(false) }
  }
  async function approveAcceptance() {
    if (!acceptance || !canSendAcceptance) return
    if (Date.now() >= Date.parse(acceptance.plan.expires_at)) {
      setMessage('Acceptance expired. Prepare current account evidence and review its new plan before approving.')
      return
    }
    setBusy(true); setMessage('')
    try {
      await providers.approveAndExecuteGroqAcceptance(acceptance.id, acceptance.plan_hash)
      setMessage('Acceptance result recorded. This action does not enable ongoing inference.')
    } catch { setMessage('Acceptance did not complete. Refresh its recorded result before taking further action; uncertain requests are never replayed automatically.') }
    finally { setBusy(false) }
  }
  async function refreshAcceptance() {
    if (!acceptance) return
    setBusy(true)
    try { await providers.refreshGroqAcceptance(acceptance.id) }
    catch { setMessage('Acceptance result could not be refreshed. Verify operator access.') }
    finally { setBusy(false) }
  }
  return <div className="provider-page">
    <header className="page-title"><div><h1>AI providers</h1><p>Configure hosted models without requiring Ollama.</p></div></header>
    <section className="panel" aria-label="Model operator">
      <h2>Local model operator</h2>
      <p>Provider setup uses a dedicated permission. It does not grant task execution or activate inference.</p>
      <button disabled={busy} onClick={() => void setup()}>Configure and select model operator</button>
      <p>Selected identity: {runtime.actorId || 'None'}</p>
      <button className="secondary" disabled={providers.loading || !runtime.actorId} onClick={() => void providers.refreshProviders()}>Refresh providers</button>
      {providers.error && <p role="alert">{providers.error}</p>}
      {message && <p role="status">{message}</p>}
    </section>
    {providers.data && <>
      <section className="panel" aria-label="Execution configuration">
        <h2>Execution configuration</h2>
        <p>Mode: {providers.data.execution_mode}. Hosted execution: {providers.data.cloud_execution_enabled ? 'activated' : 'inactive'}.</p>
        <p>Credential storage: {providers.data.credential_storage === 'windows_dpapi' ? 'Windows user-bound encryption' : 'unavailable on this platform'}.</p>
        <p>Saving settings makes no inference requests. Account quota and a free label do not establish nonbillable eligibility.</p>
      </section>
      <section className="panel" aria-label="Saved providers"><h2>Saved providers</h2>
        {providers.data.profiles.length === 0 && <p>No providers configured.</p>}
        {providers.data.profiles.map(profile => <article key={profile.provider_name}>
          <h3>{profile.provider_name}</h3><p>{profile.selected_model} · {profile.state.replaceAll('_', ' ')} · Credential {profile.credential_configured ? 'stored' : 'missing'}</p>
          {profile.credential_fingerprint && <p>Stored credential SHA-256 (for matching verified evidence): <code>{profile.credential_fingerprint}</code></p>}
          <button className="secondary" onClick={() => { setSelection(profile.provider_name); setMessage('') }}>Edit {profile.provider_name}</button>
          {profile.integration === 'groq' && <button className="secondary" disabled={busy || !profile.credential_configured} onClick={() => void checkMetadata(profile.provider_name, profile.revision)}>Check Groq model access</button>}
          {providers.metadata?.provider_name === profile.provider_name && <p role="status">Model metadata: {providers.metadata.state.replaceAll('_', ' ')}. Selected model available: {providers.metadata.exact_model_available === null ? 'unknown' : providers.metadata.exact_model_available ? 'yes' : 'no'}. This check does not establish nonbillable eligibility or test inference.</p>}
        </article>)}
        <button className="secondary" onClick={() => { setSelection(''); setMessage('') }}>Add provider</button>
      </section>
      <section className="panel" aria-label="Provider settings"><h2>{selected ? `Edit ${selected.provider_name}` : 'Add provider'}</h2>
        <form key={selection} onSubmit={event => void save(event)}>
          <label>Provider name<input name="provider_name" required pattern={'[A-Za-z0-9_\\-]+'} maxLength={120} defaultValue={selected?.provider_name ?? 'groq'} readOnly={Boolean(selected)}/></label>
          <label>Integration<select name="integration" defaultValue={selected?.integration ?? 'groq'}>{['groq', 'gemini', 'openrouter', 'cloudflare'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label>Exact model<input name="selected_model" required maxLength={200} defaultValue={selected?.selected_model ?? 'openai/gpt-oss-120b'}/></label>
          <label>Priority (lower first)<input name="priority" type="number" min={0} max={100} required defaultValue={selected?.priority ?? 50}/></label>
          <label>API key<input name="api_key" type="password" autoComplete="new-password" maxLength={4096} aria-describedby="key-help"/></label>
          <p id="key-help">Leave blank to keep the stored key. The field clears when submitted.</p>
          <label><input name="clear_credential" type="checkbox"/>Remove stored credential</label>
          <fieldset><legend>Models by task category (optional)</legend>{textCapabilities.map(capability => <label key={capability}>{capability.replaceAll('_', ' ')}<input name={capability} maxLength={200} defaultValue={selected?.model_assignments[capability] ?? ''}/></label>)}</fieldset>
          <label>Verified account and model allowance (JSON)<input name="allowance" type="file" accept=".json,application/json"/></label>
          <p>Attach current independently verified nonbillable evidence for every enabled change. Uploaded evidence must contain no API keys.</p>
          <p>For the first Groq request, keep this allowance empty and save the profile disabled. Prepare the one-request test below using account evidence. Remaining quota comes from the provider response.</p>
          <label><input name="enabled" type="checkbox" defaultChecked={selected?.enabled ?? false}/>Enable this profile when separately activated</label>
          <button disabled={busy || providers.data.credential_storage !== 'windows_dpapi'} type="submit">{busy ? 'Saving…' : 'Save provider'}</button>
        </form>
      </section>
      <section className="panel" aria-label="One-request Groq acceptance"><h2>One-request Groq acceptance</h2>
        <p>Preparation validates account evidence. Approval authorizes one fixed request with a $0 paid ceiling and leaves ongoing inference disabled.</p>
        {selected?.integration === 'groq' ? <form onSubmit={event => void prepareAcceptance(event)}>
          <label>Groq account evidence (JSON)<input name="account_evidence" type="file" accept=".json,application/json" required/></label>
          <p>Supply the canonical Personal Free account, saved-key association, exact model, independently verified RPM/RPD/TPM/TPD limits, verification times and evidence references/digest. Unknown remaining quota is omitted from this account document. Keep API keys out of it.</p>
          <button disabled={busy || selected.enabled || !selected.credential_configured || providers.data.cloud_execution_enabled} type="submit">Prepare one-request Groq test</button>
        </form> : <p>Select a saved Groq profile to prepare its acceptance test.</p>}
        {acceptance && <article aria-label="Prepared Groq request">
          <h3>{acceptance.plan.evidence.model}</h3>
          <p>Account: {acceptance.plan.evidence.account_id}. Profile revision: {acceptance.plan.profile_revision}. Status: {acceptance.status.replaceAll('_', ' ')}.</p>
          <p>Limits: {acceptance.plan.evidence.limits.rpm} RPM · {acceptance.plan.evidence.limits.rpd} RPD · {acceptance.plan.evidence.limits.tpm} TPM · {acceptance.plan.evidence.limits.tpd} TPD.</p>
          <p>POST {acceptance.plan.endpoint}. One request. Token reservation: {acceptance.plan.token_reservation}. Paid ceiling: $0. Ongoing activation: off.</p>
          <pre aria-label="Exact Groq acceptance request">{JSON.stringify(acceptance.plan.request_body, null, 2)}</pre>
          <p>Exact plan SHA-256: <code>{acceptance.plan_hash}</code></p><p>Expires: {acceptance.plan.expires_at}.</p>
          <p>Runtime policy SHA-256: <code>{acceptance.plan.policy_digest}</code></p>
          <details><summary>Account evidence used for this plan</summary><pre>{JSON.stringify(acceptance.plan.evidence, null, 2)}</pre></details>
          {['prepared', 'approved'].includes(acceptance.status) && <button disabled={busy || !canSendAcceptance} onClick={() => void approveAcceptance()}>Approve and send exactly one Groq request</button>}
          <button className="secondary" disabled={busy} onClick={() => void refreshAcceptance()}>Refresh recorded acceptance</button>
          {acceptance.result_code && <p role="status">Result: {acceptance.result_code.replaceAll('_', ' ')}. Header tracking initialized: {acceptance.initialized_capacity ? 'yes' : 'no'}.</p>}
          {acceptance.quota_observation && <p>First response observation ({acceptance.quota_observation.received_at}): RPD remaining {acceptance.quota_observation.rpd_remaining}, reset {acceptance.quota_observation.rpd_resets_at}; TPM remaining {acceptance.quota_observation.tpm_remaining}, reset {acceptance.quota_observation.tpm_resets_at}.</p>}
          <p>RPM and TPD track this application's usage. Unused account-wide RPM/TPD is not inferred from headers; Groq enforces usage by other clients.</p>
        </article>}
      </section>
    </>}
  </div>
}
