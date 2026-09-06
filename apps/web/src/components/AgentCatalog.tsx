import { useState } from 'react'
import { useAppStore } from '../state/AppStore'

export function AgentCatalog() {
  const { catalog } = useAppStore()
  const [view, setView] = useState('')
  const [reason, setReason] = useState('')
  const choose = (kind: string) => { setView(kind); setReason(''); void catalog.load(kind) }
  const detail = catalog.detail
  return <section className="panel" aria-label="External workforce catalog">
    <h2>Workforce catalog</h2>
    <p>Browse imported specialists and skills. Review an exact revision before activation. Tool requests describe needs and grant no permissions.</p>
    <div className="actions">{([['active', 'Active catalog workforce'], ['agent', 'Agent Catalog'], ['skill', 'Skills'], ['discovery', 'Skill discovery'], ['sources', 'Sources']] as const).map(([kind, label]) =>
      <button key={kind} className="secondary" aria-pressed={view === kind} disabled={catalog.busy} onClick={() => choose(kind)}>{label}</button>)}</div>
    {catalog.error && <p role="alert">{catalog.error}</p>}
    {catalog.busy && <p role="status">Loading catalog…</p>}
    {view === 'sources' && <><p>Most recent 25 pinned imports. Older imports remain available through the paginated sources API.</p>{catalog.sources.map(source => <article key={source.id}>
      <h3>{source.repository}</h3><p><code>{source.commit}</code></p><p>{source.license} · {source.imported_count} definitions · {new Date(source.imported_at).toLocaleString()}</p>
    </article>)}</>}
    {view && view !== 'sources' && <>
      <p>{catalog.page.total} definitions · {catalog.page.offset + 1}–{Math.min(catalog.page.offset + catalog.page.limit, catalog.page.total)}</p>
      {!catalog.busy && !catalog.page.total && <p>No imported definitions. Use the explicit pinned catalog import command.</p>}
      <div className="agent-grid">{catalog.page.items.map(item => <article className="agent-card" key={item.id}>
        <h3>{item.display_name}</h3><p>{item.role}</p><p>{item.capabilities.join(', ') || 'Capability review required'}</p>
        <p>{item.review_status} · {item.trust_status} · {item.enabled ? 'Catalog enabled' : 'Catalog disabled'}</p>
        {item.identity_id && <p>Workforce: {item.lifecycle_state} · {item.operational_status} · {item.runtime_enabled ? 'enabled' : 'disabled'}</p>}
        <p>{item.source_repository} · {item.source_license}</p>
        {item.duplicate_of && <p>Variant of {item.duplicate_of}</p>}
        {item.update_available && <p>Update available; runtime retains its activated revision.</p>}
        <button className="secondary" disabled={catalog.busy} onClick={() => { setReason(''); void catalog.inspect(item.id) }}>Inspect {item.display_name}</button>
      </article>)}</div>
      <div className="actions"><button disabled={catalog.busy || catalog.page.offset === 0} onClick={() => void catalog.load(view, catalog.page.offset - 25)}>Previous catalog page</button>
        <button disabled={catalog.busy || catalog.page.offset + 25 >= catalog.page.total} onClick={() => void catalog.load(view, catalog.page.offset + 25)}>Next catalog page</button></div>
    </>}
    {detail && <article aria-label="Catalog revision review">
      <h3>{detail.display_name} · revision review</h3><p>{detail.description}</p>
      <p>Commit: <code>{detail.source_commit}</code> · Path: <code>{detail.source_path}</code></p>
      <p>SHA-256: <code>{detail.source_hash}</code> · Parser: {detail.parser_version}</p>
      <p>Warnings: {detail.warnings.join(', ') || 'None detected; content remains untrusted'}</p>
      <p>Requested tools: {detail.normalized.requested_tool_classes.join(', ') || 'None detected'}</p>
      <p>Unmapped labels: {detail.normalized.unmapped_tags.join(', ') || 'None'}</p>
      <p>Applicable classes: {detail.normalized.applicable_agent_classes.join(', ') || 'Specialist'}</p>
      <details><summary>Original untrusted definition</summary><pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto' }}>{detail.original_definition}</pre></details>
      <details><summary>Source license and attribution</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{detail.license_text}</pre></details>
      <label>Review reason<input maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <div className="actions"><button disabled={catalog.busy || !reason.trim() || detail.source_license !== 'MIT' || detail.kind === 'discovery'} onClick={() => void catalog.mutate(`/api/catalog/entries/${detail.id}/review`, { revision_id: detail.revision_id, approved: true, reason })}>Approve exact revision</button>
        <button disabled={catalog.busy || !reason.trim()} onClick={() => void catalog.mutate(`/api/catalog/entries/${detail.id}/review`, { revision_id: detail.revision_id, approved: false, reason })}>Reject revision</button>
        {detail.kind === 'agent' && <><button disabled={catalog.busy || !detail.enabled || detail.review_status !== 'approved' || Boolean(detail.duplicate_of)} onClick={() => void catalog.mutate(`/api/catalog/agents/${detail.id}/activate`, { revision_id: detail.revision_id })}>Activate reviewed specialist</button>
          <button disabled={catalog.busy || !detail.enabled} onClick={() => void catalog.mutate(`/api/catalog/agents/${detail.id}/deactivate`)}>Deactivate catalog agent</button></>}
      </div>
    </article>}
  </section>
}
