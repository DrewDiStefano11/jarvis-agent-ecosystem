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
          <label>Exact model<input name="selected_model" required maxLength={200} defaultValue={selected?.selected_model ?? ''}/></label>
          <label>Priority (lower first)<input name="priority" type="number" min={0} max={100} required defaultValue={selected?.priority ?? 50}/></label>
          <label>API key<input name="api_key" type="password" autoComplete="new-password" maxLength={4096} aria-describedby="key-help"/></label>
          <p id="key-help">Leave blank to keep the stored key. The field clears when submitted.</p>
          <label><input name="clear_credential" type="checkbox"/>Remove stored credential</label>
          <fieldset><legend>Models by task category (optional)</legend>{textCapabilities.map(capability => <label key={capability}>{capability.replaceAll('_', ' ')}<input name={capability} maxLength={200} defaultValue={selected?.model_assignments[capability] ?? ''}/></label>)}</fieldset>
          <label>Verified account and model allowance (JSON)<input name="allowance" type="file" accept=".json,application/json"/></label>
          <p>Attach current independently verified nonbillable evidence for every enabled change. Uploaded evidence must contain no API keys.</p>
          <label><input name="enabled" type="checkbox" defaultChecked={selected?.enabled ?? false}/>Enable this profile when separately activated</label>
          <button disabled={busy || providers.data.credential_storage !== 'windows_dpapi'} type="submit">{busy ? 'Saving…' : 'Save provider'}</button>
        </form>
      </section>
    </>}
  </div>
}
