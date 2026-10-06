import type { RuntimeIdentity } from '../types/runtime'
import { Status } from './Status'
import '../styles/identity-operations.css'

/** Read-only registry projection. Lifecycle commands remain in IdentityWorkforce. */
export function IdentityOperations({ identities, onInspect }: {
  identities: RuntimeIdentity[]
  onInspect: (identity: RuntimeIdentity) => void
}) {
  return <table className="identity-operations" aria-label="Registered identity operations">
    <thead><tr><th scope="col">Identity</th><th scope="col">Type</th><th scope="col">Lifecycle</th><th scope="col">Availability</th><th scope="col">Operational status</th><th scope="col"><span className="identity-sr-only">Inspect profile</span></th></tr></thead>
    <tbody>{identities.map(identity => <tr key={identity.id}>
      <th scope="row"><strong>{identity.display_name}</strong><code>{identity.stable_key}</code></th>
      <td data-label="Type">{identity.agent_type}</td>
      <td data-label="Lifecycle"><Status value={identity.lifecycle_state}/></td>
      <td data-label="Availability">{identity.is_enabled ? 'Enabled' : 'Disabled'}</td>
      <td data-label="Operational status"><Status value={identity.operational_status}/></td>
      <td className="identity-inspect"><button className="secondary" aria-label={`Inspect identity ${identity.display_name}`} onClick={() => onInspect(identity)}>Inspect</button></td>
    </tr>)}</tbody>
  </table>
}
