import { NavLink } from 'react-router-dom'
import { NavIcon } from './NavIcon'

export const navigationGroups = [
  { label: 'Operations', links: [
    { to: '/', label: 'Overview', icon: 'overview', mobile: true },
    { to: '/tasks', label: 'Tasks', icon: 'tasks', mobile: true },
    { to: '/agents', label: 'Agents', icon: 'agents', mobile: false },
    { to: '/approvals', label: 'Approvals', icon: 'approvals', mobile: true },
    { to: '/runtime', label: 'Planning', icon: 'planning', mobile: true },
  ] },
  { label: 'Workspace', links: [
    { to: '/lab', label: 'Business Lab', icon: 'lab', mobile: false },
    { to: '/office', label: 'Office', icon: 'office', mobile: false },
  ] },
  { label: 'Observe', links: [
    { to: '/audit', label: 'Activity', icon: 'activity', mobile: false },
    { to: '/system', label: 'System', icon: 'system', mobile: false },
  ] },
] as const
export const navigationLinks = navigationGroups.flatMap(group => [...group.links])

export function ShellNavigation({ collapsed, pending }: { collapsed: boolean; pending: number }) {
  return <nav aria-label="Primary">{navigationGroups.map(group => <div className="nav-group" key={group.label}>
    <span className="nav-group-label">{group.label}</span>
    {group.links.map(({ to, label, icon }) => <NavLink key={to} to={to} end={to === '/'} title={collapsed ? label : undefined} aria-label={to === '/approvals' && pending > 0 ? `${label}, ${pending} pending approval records` : label}>
      <NavIcon name={icon}/><span className="nav-label">{label}</span>{to === '/approvals' && pending > 0 && <span className="nav-count" aria-hidden="true">{pending}</span>}
    </NavLink>)}
  </div>)}</nav>
}
