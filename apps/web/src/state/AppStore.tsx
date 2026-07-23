import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { post, request, WS_URL } from '../api/client'
import type { Agent, Approval, Artifact, AuditEvent, ConnectionStatus, Department, EventEnvelope, Notification, Snapshot, SystemStatus, Task } from '../types/contracts'

interface AppState {departments:Department[];agents:Agent[];tasks:Task[];approvals:Approval[];artifacts:Artifact[];auditEvents:AuditEvent[];notifications:Notification[];system:SystemStatus|null;connection:ConnectionStatus;loading:boolean;error:string|null;lastSync:string|null;resyncRequired:boolean}
interface Store extends AppState {refresh:()=>Promise<void>;action:<T>(path:string,body?:unknown)=>Promise<T>;selectAgent:(id:string|null)=>void;selectTask:(id:string|null)=>void;selectedAgentId:string|null;selectedTaskId:string|null}
const Context=createContext<Store|null>(null)
const initial:AppState={departments:[],agents:[],tasks:[],approvals:[],artifacts:[],auditEvents:[],notifications:[],system:null,connection:'connecting',loading:true,error:null,lastSync:null,resyncRequired:false}

export function AppStoreProvider({children}:{children:ReactNode}){
  const [state,setState]=useState(initial); const [selectedAgentId,selectAgent]=useState<string|null>(null); const [selectedTaskId,selectTask]=useState<string|null>(null)
  const sequence=useRef(-1); const eventSession=useRef<string|null>(null); const reconnects=useRef(0)
  const refresh=useCallback(async()=>{try{
    const [departments,agents,tasks,approvals,artifacts,auditEvents,notifications,system]=await Promise.all([
      request<Department[]>('/api/departments'),request<Agent[]>('/api/agents'),request<Task[]>('/api/tasks'),request<Approval[]>('/api/approvals'),
      request<Artifact[]>('/api/artifacts').catch(()=>[]),request<AuditEvent[]>('/api/audit-events'),request<Notification[]>('/api/notifications'),request<SystemStatus>('/api/system/status')])
    setState(s=>({...s,departments,agents,tasks,approvals,artifacts,auditEvents,notifications,system,loading:false,error:null,lastSync:new Date().toISOString(),resyncRequired:false}))
  }catch(error){setState(s=>({...s,loading:false,error:error instanceof Error?error.message:'Unable to synchronize',connection:navigator.onLine?'error':'offline'}))}},[])
  const action=useCallback(async<T,>(path:string,body?:unknown)=>{const result=await post<T>(path,body);await refresh();return result},[refresh])
  useEffect(()=>{void refresh()},[refresh])
  useEffect(()=>{let socket:WebSocket|null=null;let timer:number|undefined;let closed=false
    const connect=()=>{if(closed)return;setState(s=>({...s,connection:reconnects.current?'reconnecting':'connecting'}));socket=new WebSocket(WS_URL)
      socket.onopen=()=>{reconnects.current=0;setState(s=>({...s,connection:'connected'}))}
      socket.onmessage=(message)=>{const event=JSON.parse(String(message.data)) as EventEnvelope
        if(event.eventSessionId&&event.eventSessionId!==eventSession.current){eventSession.current=event.eventSessionId;sequence.current=-1}
        if(event.sequenceNumber<=sequence.current)return
        if(sequence.current>=0&&event.sequenceNumber!==sequence.current+1){setState(s=>({...s,resyncRequired:true}));void refresh()}
        sequence.current=event.sequenceNumber
        if(event.eventType==='system.snapshot'){const payload=event.payload as {snapshot:Snapshot;system:SystemStatus};const snap=payload.snapshot;setState(s=>({...s,...snap,system:payload.system,loading:false,lastSync:new Date().toISOString(),error:null}))}
        else void refresh()
      }
      socket.onerror=()=>setState(s=>({...s,connection:'error'}))
      socket.onclose=()=>{if(closed)return;reconnects.current+=1;setState(s=>({...s,connection:navigator.onLine?'reconnecting':'offline'}));timer=window.setTimeout(connect,Math.min(1000*reconnects.current,5000))}
    };connect();const poll=window.setInterval(()=>{if(socket?.readyState!==WebSocket.OPEN)void refresh()},10000)
    const offline=()=>setState(s=>({...s,connection:'offline'}));const online=()=>{reconnects.current=1;connect()};window.addEventListener('offline',offline);window.addEventListener('online',online)
    return()=>{closed=true;if(timer)clearTimeout(timer);clearInterval(poll);socket?.close();window.removeEventListener('offline',offline);window.removeEventListener('online',online)}
  },[refresh])
  const value=useMemo(()=>({...state,refresh,action,selectAgent,selectTask,selectedAgentId,selectedTaskId}),[state,refresh,action,selectedAgentId,selectedTaskId])
  return <Context.Provider value={value}>{children}</Context.Provider>
}
export function useAppStore(){const value=useContext(Context);if(!value)throw new Error('AppStoreProvider is required');return value}
