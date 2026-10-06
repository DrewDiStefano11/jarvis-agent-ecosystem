import { JarvisCommand } from './components/JarvisCommand'
import { lazy, Suspense, useLayoutEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { AgentDetails, TaskDetails } from './components/Details'
import { NavIcon } from './components/NavIcon'
import { Status } from './components/Status'
import { useAppStore } from './state/AppStore'
import { Dashboard } from './pages/Dashboard'
import { Tasks } from './pages/Tasks'
import { Agents } from './pages/Agents'
import { Approvals } from './pages/Approvals'
import { Audit } from './pages/Audit'
import { System } from './pages/System'
import { Runtime } from './pages/Runtime'
import { BusinessLab } from './pages/BusinessLab'

const Office = lazy(() => import('./pages/Office').then(module => ({ default: module.Office })))
const links = [
  { to: '/', label: 'Overview', icon: 'overview', mobile: true },
  { to: '/tasks', label: 'Tasks', icon: 'tasks', mobile: true },
  { to: '/agents', label: 'Agents', icon: 'agents' },
  { to: '/approvals', label: 'Approvals', icon: 'approvals', mobile: true },
  { to: '/audit', label: 'Activity', icon: 'activity' },
  { to: '/runtime', label: 'Planning', icon: 'planning', mobile: true },
  { to: '/lab', label: 'Business Lab', icon: 'lab' },
  { to: '/office', label: 'Office', icon: 'office' },
  { to: '/system', label: 'System', icon: 'system' },
] as const
const routeTitles: Record<string, string> = Object.fromEntries(links.map(link => [link.to, link.label]))
function RouteTitle() {
  const { pathname } = useLocation()
  useLayoutEffect(() => { document.title = `${routeTitles[pathname] ?? 'Not found'} · Jarvis` }, [pathname])
  return null
}
function NotFound() {
  return <section className="empty"><h1>Page not found</h1><p>The requested Jarvis view does not exist.</p><Link className="primary" to="/">Return to overview</Link></section>
}
export default function App() {
  const [controlError, setControlError] = useState('')
  const [controlBusy, setControlBusy] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const [more, setMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const { loading, error, system, connection, lastSync, resyncRequired, approvals, action, refresh, selectedAgentId, selectedTaskId } = useAppStore()
  const pending = approvals.filter(approval => approval.status === 'pending').length
  const stale = Boolean(error || resyncRequired || connection !== 'connected')
  if (loading) return <main className="center-state" aria-live="polite"><div className="loader"/><h1>Synchronizing Jarvis</h1><p>Loading the latest Hub state…</p></main>
  const control = async () => {
    const resume = system?.emergencyStop
    if (!window.confirm(resume ? 'Resume the system? Existing backend authorization and recovery rules still apply.' : 'Stop the system? This interrupts active work and office movement. Review recovery before resuming.')) return
    setControlBusy(true)
    setControlError('')
    try { await action(resume ? '/api/system/resume' : '/api/system/emergency-stop') }
    catch (caught) { setControlError(caught instanceof Error ? caught.message : 'System control failed') }
    finally { setControlBusy(false) }
  }
  return <div className={`shell mission-shell${collapsed ? ' sidebar-collapsed' : ''}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <div className="brand"><div><strong>JARVIS</strong><small>Mission Control</small></div><button className="collapse-button" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}><NavIcon name="collapse"/></button></div>
      <nav aria-label="Primary">{links.map(({ to, label, icon }) => <NavLink key={to} to={to} end={to === '/'} title={collapsed ? label : undefined} aria-label={to === '/approvals' && pending > 0 ? `${label}, ${pending} pending approval records` : label}><NavIcon name={icon}/><span className="nav-label">{label}</span>{to === '/approvals' && pending > 0 && <span className="nav-count" aria-hidden="true">{pending}</span>}</NavLink>)}</nav>
      <div className="sidebar-foot"><strong>Local AI Hub</strong><small>Local execution is opt-in.</small></div>
    </aside>
    <div className="workspace"><RouteTitle/>
      <header className="topbar"><div className="system-summary"><Status value={connection}/><span className="desktop-only">{stale ? 'Last-known state' : 'Event stream connected'}</span></div><div className="top-actions"><button className="emergency" disabled={controlBusy || !system} onClick={() => void control()}>{controlBusy ? 'Awaiting acknowledgement…' : system?.emergencyStop ? 'Resume system' : 'Emergency stop'}</button></div></header>
      <main id="main-content" className="content" tabIndex={-1}>
        {stale && <div className="sync-banner" role="status"><div><strong>Data may be stale</strong><p>{error ?? (resyncRequired ? 'Reconciling an event sequence gap.' : 'The event stream is disconnected; HTTP refresh remains available.')} Last synchronized: {lastSync ? new Date(lastSync).toLocaleString() : 'never'}.</p></div><button className="secondary" disabled={refreshing} onClick={async () => { setRefreshing(true); try { await refresh() } finally { setRefreshing(false) } }}>{refreshing ? 'Refreshing…' : 'Refresh state'}</button></div>}
        {controlError && <p role="alert">{controlError}</p>}
        <Routes><Route path="/" element={<Dashboard/>}/><Route path="/tasks" element={<Tasks/>}/><Route path="/agents" element={<Agents/>}/><Route path="/approvals" element={<Approvals/>}/><Route path="/audit" element={<Audit/>}/><Route path="/runtime" element={<Runtime/>}/><Route path="/lab" element={<BusinessLab/>}/><Route path="/office" element={<Suspense fallback={<p>Loading office…</p>}><Office/></Suspense>}/><Route path="/system" element={<System/>}/><Route path="*" element={<NotFound/>}/></Routes>
      </main>
      <nav className={`mobile-nav${more ? ' mobile-nav-expanded' : ''}`} aria-label="Mobile primary">{links.filter(link => 'mobile' in link && link.mobile).map(link => <NavLink key={link.to} to={link.to} end={link.to === '/'} className="mobile-main-link" aria-label={link.to === '/approvals' && pending > 0 ? `${link.label}, ${pending} pending approval records` : link.label} onClick={() => setMore(false)}><NavIcon name={link.icon}/><small>{link.label}</small></NavLink>)}<button className="mobile-more" aria-expanded={more} aria-label={more ? 'Close more navigation' : 'More navigation'} onClick={() => setMore(value => !value)}><span aria-hidden="true">{more ? '×' : '•••'}</span><small>More</small></button>{more && links.filter(link => !('mobile' in link && link.mobile)).map(link => <NavLink key={link.to} to={link.to} className="mobile-extra-link" onClick={() => setMore(false)}><NavIcon name={link.icon}/><small>{link.label}</small></NavLink>)}</nav>
    </div><JarvisCommand/>{selectedAgentId && <AgentDetails/>}{selectedTaskId && <TaskDetails/>}
  </div>
}
