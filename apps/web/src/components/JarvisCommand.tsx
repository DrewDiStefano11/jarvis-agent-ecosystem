import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { TaskCreateForm } from './TaskCreateForm'
import '../styles/command.css'

type Icon = 'search' | 'identity' | 'task' | 'agent' | 'approval' | 'page' | 'close' | 'next'
function CommandIcon({ kind }: { kind: Icon }) {
  const paths: Record<Icon, React.ReactNode> = {
    search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>,
    identity: <><circle cx="12" cy="7" r="3"/><path d="M5 21v-3a7 7 0 0 1 14 0v3"/></>,
    task: <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"/></>,
    agent: <><path d="m12 3 8 5v9l-8 5-8-5V8zM4 8l8 5 8-5M12 13v9"/></>,
    approval: <path d="m12 3 8 3v6c0 5-4 8-8 10-4-2-8-5-8-10V6z"/>,
    page: <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>, next: <path d="m9 5 7 7-7 7"/>,
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[kind]}</svg>
}

const pages = [['/','Overview','Home dashboard'],['/tasks','Tasks','Goals and completed task records'],['/agents','Agents','Registered workforce and simulated agents'],['/approvals','Approvals','Approval inbox'],['/audit','Activity','Audit events'],['/runtime','Planning','Local planning and authorized runtime evidence'],['/lab','Business Lab','Projects'],['/office','Office','Visual office'],['/system','System','Health and system controls']] as const
type Result = { id: string; title: string; evidence: string; destination: string; icon: Icon; search: string; open: () => void }
export function JarvisCommand() {
  const store = useAppStore()
  const navigate = useNavigate()
  const dialog = useRef<HTMLDialogElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const [open,setOpen] = useState(false)
  const [engaged,setEngaged] = useState(false)
  const [mode,setMode] = useState('find')
  const [query,setQuery] = useState('')
  const [created,setCreated] = useState<{ id: string; title: string; warning: string } | null>(null)
  const [formGeneration,setFormGeneration] = useState(0)
  const [refreshing,setRefreshing] = useState(false)
  const { loadIdentities } = store.runtime
  const stale = Boolean(store.error || store.resyncRequired || !store.system || store.connection !== 'connected')
  const close = () => { dialog.current?.close(); setOpen(false) }
  useEffect(() => {
    if (open) {
      dialog.current?.showModal()
      searchInput.current?.focus()
      void loadIdentities().catch(() => { /* Shared identity state exposes the error and preserves last-known data. */ })
    } else if (dialog.current?.open) dialog.current.close()
  }, [open,loadIdentities])
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k' || event.altKey || event.repeat || event.isComposing) return
      const otherDialog = document.querySelector('[aria-modal="true"]:not(.jarvis-command-dialog), dialog[open]:not(.jarvis-command-dialog)')
      if (store.selectedTaskId || store.selectedAgentId || otherDialog) return
      event.preventDefault()
      setEngaged(true)
      setOpen(current => !current)
    }
    window.addEventListener('keydown',shortcut)
    return () => window.removeEventListener('keydown',shortcut)
  }, [store.selectedTaskId,store.selectedAgentId])
  const visit = (path: string) => { close(); navigate(path) }
  const groups: { name: string; items: Result[] }[] = [
    { name:'Registered identities', items:store.runtime.identities.map(identity => ({ id:identity.id, title:identity.display_name, evidence:`${identity.lifecycle_state} · registered identity`, destination:identity.lifecycle_state==='active' && identity.is_enabled?'Open in Planning':'Open workforce', icon:'identity', search:[identity.display_name,identity.id,identity.stable_key,identity.description,identity.lifecycle_state].join(' '), open:()=>{ if(identity.lifecycle_state==='active' && identity.is_enabled) { store.runtime.selectActor(identity.id); visit('/runtime') } else visit('/agents') } })) },
    { name:'Task records', items:store.tasks.map(task => ({ id:task.id, title:task.title, evidence:`${task.status.replaceAll('_',' ')} · task record`, destination:'Inspect task', icon:'task', search:[task.id,task.title,task.request,task.result ?? '',task.statusMessage,task.parentTaskId ?? ''].join(' '), open:()=>{close();store.selectTask(task.id)} })) },
    { name:'Simulated agents', items:store.agents.map(agent => ({ id:agent.id, title:agent.name, evidence:`${agent.status} · simulated agent`, destination:'Inspect simulated agent', icon:'agent', search:[agent.id,agent.name,agent.role,...agent.capabilities].join(' '), open:()=>{close();store.selectAgent(agent.id)} })) },
    { name:'Approvals', items:store.approvals.map(approval => ({ id:approval.id, title:approval.title, evidence:`${approval.status} · approval record`, destination:'Open approval inbox', icon:'approval', search:[approval.id,approval.title,approval.taskId,approval.description,approval.status].join(' '), open:()=>visit('/approvals') })) },
    { name:'Pages', items:pages.map(([path,title,description]) => ({ id:path,title,evidence:description,destination:'Open page',icon:'page',search:`${title} ${description}`,open:()=>visit(path) })) },
  ]
  const needle = query.trim().toLowerCase()
  const matched = groups.map(group=>({...group,items:group.items.filter(item=>item.search.toLowerCase().includes(needle))}))
  const count = matched.reduce((sum,group)=>sum+group.items.length,0)
  const shown = matched.reduce((sum,group)=>sum+Math.min(8,group.items.length),0)
  return <>
    <button className="jarvis-command-launcher" aria-label="Talk to Jarvis" aria-haspopup="dialog" aria-expanded={open} onClick={()=>{setEngaged(true);setOpen(true)}} disabled={Boolean(store.selectedAgentId || store.selectedTaskId)}><CommandIcon kind="search"/><span>Talk to Jarvis</span><kbd>Ctrl K</kbd></button>
    <dialog ref={dialog} className="jarvis-command-dialog" aria-labelledby="jarvis-command-title" aria-modal="true" onCancel={event=>{event.preventDefault();close()}} onClose={()=>{if(!dialog.current?.open)setOpen(false)}} onKeyDown={event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return}
      if(event.key!=='Tab')return
      const controls=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:enabled,input:enabled,select:enabled,textarea:enabled,a[href],[tabindex]:not([tabindex="-1"])')).filter(element=>!element.closest('[hidden]')).sort((a,b)=>a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1)
      const first=controls[0],last=controls.at(-1)
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
    }}>
      {engaged && <>
      <header><h2 id="jarvis-command-title">Talk to Jarvis</h2><button aria-label="Close Talk to Jarvis" onClick={close}><CommandIcon kind="close"/></button></header>
      <div className="jarvis-command-modes" role="group" aria-label="Command mode"><button aria-pressed={mode==='find'} onClick={()=>{setMode('find');requestAnimationFrame(()=>searchInput.current?.focus())}}>Find records</button><button aria-pressed={mode==='create'} onClick={()=>setMode('create')}>New request</button></div>
      {stale && <button disabled={refreshing} onClick={async()=>{setRefreshing(true);try{await store.refresh()}finally{setRefreshing(false)}}}>{refreshing?'Refreshing…':'Refresh state'}</button>}
      <section hidden={mode!=='find'}>
        <label className="jarvis-command-search">Find records and pages<input ref={searchInput} type="search" placeholder="Search loaded records, pages, agents…" value={query} onChange={event=>setQuery(event.target.value)}/></label>
        <p className="jarvis-command-scope">Search loaded records. Task records include demonstrations and operator requests.</p>
        {stale && <p role="status">Last-known Hub records may be outdated. Refresh state before creating work.</p>}
        {store.runtime.identityLoading && <p role="status">Refreshing registered identities…</p>}
        {store.runtime.identityError && <p role="alert">Registered identity lookup is unavailable: {store.runtime.identityError}</p>}
        {matched.filter(group=>group.items.length>0).map(group=><section key={group.name} className="jarvis-command-group" aria-label={group.name}><h3>{group.name}<span>{group.items.length} matching</span></h3>{group.items.slice(0,8).map(item=><button key={item.id} className="jarvis-command-result" onClick={item.open}><span className="jarvis-command-icon"><CommandIcon kind={item.icon}/></span><span><strong>{item.title}</strong><small>{item.evidence}</small><small>{item.id}</small></span><span className="jarvis-command-destination">{item.destination}</span><CommandIcon kind="next"/></button>)}{group.items.length>8 && <p>Showing 8 of {group.items.length}. Refine the search.</p>}</section>)}
        {!count && <p>No loaded records or pages match this search.</p>}<p className="jarvis-command-scope" role="status">Showing {shown} of {count} matching loaded records and pages.</p>
      </section>
      <section hidden={mode!=='create'}>
        {stale && <p role="status">New requests are unavailable while Hub state is stale. Refresh state and reconnect.</p>}
        {created ? <div role="status"><h3>Task created and queued</h3><p>{created.title}</p><p>{created.id}</p>{created.warning && <p>{created.warning}</p>}<button onClick={()=>{store.runtime.setTaskId(created.id);visit('/runtime')}}>Open planning for this task</button><button onClick={()=>{setCreated(null);setFormGeneration(value=>value+1)}}>Start another request</button></div> : <fieldset disabled={stale}><TaskCreateForm key={formGeneration} onCreated={(task,warning)=>setCreated({id:task.id,title:task.title,warning})}/></fieldset>}
      </section>
      </>}
    </dialog>
  </>
}
