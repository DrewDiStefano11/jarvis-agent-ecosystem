import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import type { IdentityRegistration, RuntimeIdentity } from '../types/runtime'
import { Status } from './Status'
import '../styles/workforce.css'

const agentTypes = ['worker', 'specialist', 'reviewer', 'coordinator', 'supervisor', 'monitor', 'system']

function RegistrationForm({ onClose, onComplete }: { onClose: () => void; onComplete: (message: string) => void }) {
  const { runtime } = useAppStore()
  const [pending, setPending] = useState<IdentityRegistration | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    const body = pending ?? {
      stable_key: String(form.get('stable_key')).trim(), display_name: String(form.get('display_name')).trim(),
      description: String(form.get('description')).trim(), agent_type: String(form.get('agent_type')),
    }
    if (!body.display_name) { setError('Enter a display name.'); return }
    setPending(body); setBusy(true); setError('')
    try {
      const { identity, recovered } = await runtime.createIdentity(body)
      onComplete(recovered
        ? `Found the existing registration for ${identity.display_name}. Its current state is ${identity.lifecycle_state}.`
        : `Registered ${identity.display_name} as provisioned. Activate it explicitly when it is ready.`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Identity registration failed') }
    finally { setBusy(false) }
  }
  return <form className="identity-form" onSubmit={event => void submit(event)} aria-label="Register identity">
    <h3>Register an identity</h3>
    <div className="identity-fields">
      <label>Display name<input name="display_name" required maxLength={160} disabled={busy || Boolean(pending)} /></label>
      <label>Stable key<input name="stable_key" required minLength={2} maxLength={80} pattern={'[a-z][a-z0-9]*([._\\-][a-z0-9]+)*'} placeholder="research-assistant" disabled={busy || Boolean(pending)} /><small>Unique lowercase identifier. Retried registration keeps this key.</small></label>
      <label>Agent type<select name="agent_type" defaultValue="worker" disabled={busy || Boolean(pending)}>{agentTypes.map(type => <option key={type}>{type}</option>)}</select></label>
      <label className="identity-description">Description<textarea name="description" aria-label="Description" maxLength={2000} disabled={busy || Boolean(pending)} /></label>
    </div>
    <p className="muted">Type and description identify the agent; they do not configure model instructions. Registration grants no task, tool, or administrative permissions and leaves the identity provisioned.</p>
    {error && <p className="callout danger" role="alert">{error}</p>}
    {pending && error && <p>Retry uses the same registration. Closing this form does not remove any registration already accepted by the API.</p>}
    <div className="actions"><button className="primary" disabled={busy}>{busy ? 'Registering…' : pending ? 'Retry same registration' : 'Register provisioned identity'}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>Close registration</button></div>
  </form>
}

function IdentityCard({ identity, capability }: { identity: RuntimeIdentity; capability?: string }) {
  const { runtime } = useAppStore()
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const retired = identity.lifecycle_state === 'retired'
  const act = async (operation: () => Promise<RuntimeIdentity>, success: string) => {
    if (busy) return
    setBusy(true); setError(''); setMessage('')
    try { await operation(); setMessage(success); setEditing(false) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Identity update failed') }
    finally { setBusy(false) }
  }
  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const displayName = String(form.get('display_name')).trim()
    const description = String(form.get('description')).trim()
    if (!displayName) { setError('Enter a display name.'); return }
    void act(() => runtime.updateIdentity(identity.id, {
      ...(displayName !== identity.display_name ? { display_name: displayName } : {}),
      ...(description !== identity.description ? { description } : {}),
    }), 'Profile saved.')
  }
  return <article className="identity-card" aria-label={`Identity ${identity.display_name}`}>
    <div className="identity-heading"><div><h3>{identity.display_name}</h3><code>{identity.stable_key}</code></div><Status value={identity.lifecycle_state} /></div>
    <p className="muted">{identity.agent_type} · {identity.is_enabled ? 'Enabled' : 'Disabled'} · {identity.operational_status}</p>
    <p className="identity-copy">{identity.description || 'No description provided.'}</p>
    {capability && <p className="callout">Effective capability: {capability}</p>}
    <small className="muted identity-id">{identity.id}</small>
    {identity.lifecycle_state === 'provisioned' && <p>Provisioned; activation is required before selecting this identity for work or office placement.</p>}
    {identity.lifecycle_state === 'suspended' && <p>Suspended; reactivate it explicitly before new work.</p>}
    {!identity.is_enabled && !retired && <p>Disabled identities are unavailable for new work. Enable this identity before activation or use.</p>}
    {retired && <p>Retired identities remain in durable history and cannot be reactivated.</p>}
    {editing ? <form className="identity-form" onSubmit={save} aria-label={`Edit ${identity.display_name}`}>
      <label>Display name<input name="display_name" required maxLength={160} defaultValue={identity.display_name} disabled={busy}/></label>
      <label>Description<textarea name="description" aria-label="Description" maxLength={2000} defaultValue={identity.description} disabled={busy}/></label>
      <div className="actions"><button className="primary" disabled={busy}>Save profile</button><button type="button" className="secondary" disabled={busy} onClick={() => setEditing(false)}>Cancel edit</button></div>
    </form> : <div className="actions">
      <button className="secondary" disabled={busy || retired} onClick={() => { setEditing(true); setError(''); setMessage('') }}>Edit profile</button>
      {!retired && <button className="secondary" disabled={busy} onClick={() => void act(() => runtime.updateIdentity(identity.id, { is_enabled: !identity.is_enabled }), identity.is_enabled ? 'Identity disabled.' : 'Identity enabled.')}>{identity.is_enabled ? 'Disable identity' : 'Enable identity'}</button>}
      {identity.lifecycle_state === 'active'
        ? <button className="secondary" disabled={busy} onClick={() => void act(() => runtime.transitionIdentity(identity.id, 'suspend'), 'Identity suspended.')}>Suspend identity</button>
        : !retired && <button className="primary" disabled={busy || !identity.is_enabled} onClick={() => void act(() => runtime.transitionIdentity(identity.id, 'activate'), 'Identity activated. Its existing permissions still apply.')}>{identity.lifecycle_state === 'suspended' ? 'Reactivate identity' : 'Activate identity'}</button>}
    </div>}
    {identity.lifecycle_state === 'active' && identity.is_enabled && <p><Link to="/runtime">Assign work in Planning</Link> · <Link to="/office">Place in the office</Link></p>}
    {error && <p role="alert" className="callout danger">{error}</p>}
    {message && <p role="status">{message}</p>}
  </article>
}

export function IdentityWorkforce() {
  const { runtime } = useAppStore()
  const { identities, identityError, identityLoading, loadIdentities, capabilities, loadCapabilities,
    capabilityMembers, loadCapabilityMembers } = runtime
  const [creating, setCreating] = useState(false)
  const [search, setSearch] = useState('')
  const [capability, setCapability] = useState('')
  const [capabilityError, setCapabilityError] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => {
    void loadIdentities().catch(() => undefined)
    void loadCapabilities().catch(caught => setCapabilityError(caught instanceof Error ? caught.message : 'Cannot load capability definitions'))
  }, [loadIdentities, loadCapabilities])
  useEffect(() => {
    if (!capability) return
    let active = true
    void loadCapabilityMembers(capability).catch(caught => { if (active) setCapabilityError(caught instanceof Error ? caught.message : 'Cannot load capability assignments') })
    return () => { active = false }
  }, [capability, loadCapabilityMembers])
  const visible = identities.filter(identity => `${identity.display_name} ${identity.stable_key}`.toLowerCase().includes(search.toLowerCase())
    && (!capability || capabilityMembers[capability]?.includes(identity.id)))
  const selectedCapability = capabilities.find(item => item.stable_key === capability)
  return <section className="panel workforce" aria-labelledby="workforce-title">
    <div className="panel-heading"><div><h2 id="workforce-title">Registered identities</h2><p>Durable identities shared with Planning and the office.</p></div><button className="primary" disabled={creating} onClick={() => { setCreating(true); setMessage('') }}>Register identity</button></div>
    <p>{identities.filter(identity => identity.lifecycle_state === 'active' && identity.is_enabled).length} active and enabled · {identities.length} registered</p>
    <p className="muted">Activation makes an identity available for assignment. It grants no execution permissions. Capabilities describe effective assignments; tool and task access are authorized separately.</p>
    {message && <p role="status" className="callout success">{message}</p>}
    {creating && <RegistrationForm onClose={() => setCreating(false)} onComplete={value => { setMessage(value); setCreating(false) }}/>}
    <div className="filters"><label>Find an identity<input value={search} onChange={event => setSearch(event.target.value)} placeholder="Name or stable key"/></label>
      <label>Effective capability<select value={capability} onChange={event => { setCapability(event.target.value); setCapabilityError('') }}><option value="">All identities</option>{capabilities.filter(item => item.is_enabled).map(item => <option key={item.id} value={item.stable_key}>{item.display_name}</option>)}</select></label>
      <button className="secondary" disabled={identityLoading} onClick={() => { void loadIdentities().catch(() => undefined); if (capability) void loadCapabilityMembers(capability).catch(caught => setCapabilityError(caught instanceof Error ? caught.message : 'Cannot load assignments')) }}>{identityLoading ? 'Refreshing…' : 'Refresh identities'}</button></div>
    {identityError && <p role="alert" className="callout danger">{identityError}. Previously loaded identities may be stale.</p>}
    {capabilityError && <p role="alert">{capabilityError}</p>}
    <div className="identity-grid">{visible.map(identity => <IdentityCard key={identity.id} identity={identity} capability={selectedCapability?.display_name}/>)}</div>
    {!identityLoading && !identityError && !visible.length && <p>{identities.length ? 'No identities match this selection.' : 'No identities registered yet. Register one to build your workforce.'}</p>}
  </section>
}
