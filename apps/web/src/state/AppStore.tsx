import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { post, request, WS_URL } from '../api/client'
import { useRuntimeState } from './useRuntimeState'
import { useOfficeState } from './useOfficeState'
import { useToolExecutionState } from './useToolExecutionState'
import type {
  Agent,
  Approval,
  Artifact,
  AuditEvent,
  ConnectionStatus,
  Department,
  EventEnvelope,
  Notification,
  Snapshot,
  SystemStatus,
  Task,
} from '../types/contracts'

interface AppState {
  departments: Department[]
  agents: Agent[]
  tasks: Task[]
  approvals: Approval[]
  artifacts: Artifact[]
  auditEvents: AuditEvent[]
  notifications: Notification[]
  system: SystemStatus | null
  connection: ConnectionStatus
  loading: boolean
  error: string | null
  lastSync: string | null
  resyncRequired: boolean
}

interface Store extends AppState {
  runtime: ReturnType<typeof useRuntimeState>
  office: ReturnType<typeof useOfficeState>
  tools: ReturnType<typeof useToolExecutionState>
  refresh: () => Promise<void>
  action: <T>(path: string, body?: unknown) => Promise<T>
  selectAgent: (id: string | null) => void
  selectTask: (id: string | null) => void
  selectedAgentId: string | null
  selectedTaskId: string | null
}

const Context = createContext<Store | null>(null)
const initial: AppState = {
  departments: [],
  agents: [],
  tasks: [],
  approvals: [],
  artifacts: [],
  auditEvents: [],
  notifications: [],
  system: null,
  connection: 'connecting',
  loading: true,
  error: null,
  lastSync: null,
  resyncRequired: false,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseEventEnvelope(raw: unknown): EventEnvelope | null {
  if (!isRecord(raw) || !isRecord(raw.payload)) return null
  if (
    typeof raw.eventId !== 'string' ||
    typeof raw.eventType !== 'string' ||
    typeof raw.sequenceNumber !== 'number' ||
    !Number.isSafeInteger(raw.sequenceNumber) ||
    raw.sequenceNumber < 0 ||
    typeof raw.source !== 'string'
  ) {
    return null
  }
  return raw as unknown as EventEnvelope
}

function parseSnapshot(payload: Record<string, unknown>): {
  snapshot: Snapshot
  system: SystemStatus
} | null {
  const snapshot = payload.snapshot
  const system = payload.system
  if (!isRecord(snapshot) || !isRecord(system)) return null
  const collections = [
    snapshot.departments,
    snapshot.agents,
    snapshot.tasks,
    snapshot.approvals,
    snapshot.artifacts,
    snapshot.auditEvents,
    snapshot.notifications,
  ]
  if (collections.some((value) => !Array.isArray(value))) return null
  return {
    snapshot: snapshot as unknown as Snapshot,
    system: system as unknown as SystemStatus,
  }
}

export function AppStoreProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(initial)
  const runtime = useRuntimeState(state.lastSync)
  const office = useOfficeState(state.lastSync)
  const tools = useToolExecutionState(runtime.actorId, runtime.taskId, state.lastSync)
  const [selectedAgentId, selectAgent] = useState<string | null>(null)
  const [selectedTaskId, selectTask] = useState<string | null>(null)
  const primarySequences = useRef(new Map<string, number>())
  const runtimeSequences = useRef(new Map<string, number>())
  const reconnects = useRef(0)
  const refreshGeneration = useRef(0)

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current
    try {
      const [
        departments,
        agents,
        tasks,
        approvals,
        artifacts,
        auditEvents,
        notifications,
        system,
      ] = await Promise.all([
        request<Department[]>('/api/departments'),
        request<Agent[]>('/api/agents'),
        request<Task[]>('/api/tasks'),
        request<Approval[]>('/api/approvals'),
        request<Artifact[]>('/api/artifacts').catch(() => []),
        request<AuditEvent[]>('/api/audit-events'),
        request<Notification[]>('/api/notifications'),
        request<SystemStatus>('/api/system/status'),
      ])
      if (generation !== refreshGeneration.current) return
      setState((current) => ({
        ...current,
        departments,
        agents,
        tasks,
        approvals,
        artifacts,
        auditEvents,
        notifications,
        system,
        loading: false,
        error: null,
        lastSync: new Date().toISOString(),
        resyncRequired: false,
      }))
    } catch (error) {
      if (generation !== refreshGeneration.current) return
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : 'Unable to synchronize',
        connection: navigator.onLine ? 'error' : 'offline',
      }))
    }
  }, [])

  const action = useCallback(
    async <T,>(path: string, body?: unknown) => {
      const result = await post<T>(path, body)
      await refresh()
      return result
    },
    [refresh],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let closed = false

    const scheduleReconnect = () => {
      if (closed || reconnectTimer !== undefined) return
      reconnects.current += 1
      const delay = Math.min(1000 * reconnects.current, 5000)
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = undefined
        connect()
      }, delay)
    }

    const connect = () => {
      if (
        closed ||
        socket?.readyState === WebSocket.OPEN ||
        socket?.readyState === WebSocket.CONNECTING
      ) {
        return
      }
      primarySequences.current.clear()
      runtimeSequences.current.clear()
      setState((current) => ({
        ...current,
        connection: reconnects.current ? 'reconnecting' : 'connecting',
      }))
      const nextSocket = new WebSocket(WS_URL)
      socket = nextSocket
      nextSocket.onopen = () => {
        if (socket !== nextSocket) return
        reconnects.current = 0
        setState((current) => ({ ...current, connection: 'connected' }))
      }
      nextSocket.onmessage = (message) => {
        if (socket !== nextSocket) return
        let event: EventEnvelope | null = null
        try {
          event = parseEventEnvelope(JSON.parse(String(message.data)))
        } catch {
          // Invalid server data is handled through the same fail-closed resync path.
        }
        if (!event) {
          setState((current) => ({
            ...current,
            error: 'Received an invalid synchronization event.',
            resyncRequired: true,
          }))
          nextSocket.send('resync')
          void refresh()
          return
        }

        const parsedSnapshot =
          event.eventType === 'system.snapshot' ? parseSnapshot(event.payload) : null
        if (event.eventType === 'system.snapshot' && !parsedSnapshot) {
          setState((current) => ({
            ...current,
            error: 'Received an invalid synchronization snapshot.',
            resyncRequired: true,
          }))
          nextSocket.send('resync')
          void refresh()
          return
        }

        const isRuntime = event.source === 'agent_runtime'
        const sessionKey = event.eventSessionId ?? 'legacy'
        const cursors = isRuntime ? runtimeSequences.current : primarySequences.current
        const previous = cursors.get(sessionKey)
        if (previous !== undefined && event.sequenceNumber <= previous) return
        if (previous !== undefined && event.sequenceNumber !== previous + 1) {
          cursors.delete(sessionKey)
          setState((current) => ({ ...current, resyncRequired: true }))
          nextSocket.send('resync')
          void refresh()
          return
        }
        if (isRuntime && previous !== undefined) cursors.delete(sessionKey)
        cursors.set(sessionKey, event.sequenceNumber)
        if (isRuntime) {
          while (runtimeSequences.current.size > 100) {
            const oldest = runtimeSequences.current.keys().next().value
            if (typeof oldest !== 'string') break
            runtimeSequences.current.delete(oldest)
          }
        }

        if (event.eventType === 'system.snapshot') {
          const { snapshot, system } = parsedSnapshot!
          refreshGeneration.current += 1
          setState((current) => ({
            ...current,
            departments: snapshot.departments,
            agents: snapshot.agents,
            tasks: snapshot.tasks,
            approvals: snapshot.approvals,
            artifacts: snapshot.artifacts,
            auditEvents: snapshot.auditEvents,
            notifications: snapshot.notifications,
            system,
            loading: false,
            lastSync: new Date().toISOString(),
            error: null,
            resyncRequired: false,
          }))
        } else {
          void refresh()
        }
      }
      nextSocket.onerror = () => {
        if (socket !== nextSocket) return
        setState((current) => ({ ...current, connection: 'error' }))
      }
      nextSocket.onclose = () => {
        if (socket !== nextSocket) return
        socket = null
        if (closed) return
        setState((current) => ({
          ...current,
          connection: navigator.onLine ? 'reconnecting' : 'offline',
        }))
        scheduleReconnect()
      }
    }

    connect()
    const poll = window.setInterval(() => {
      if (socket?.readyState !== WebSocket.OPEN) void refresh()
    }, 10_000)
    const offline = () =>
      setState((current) => ({ ...current, connection: 'offline' }))
    const online = () => {
      if (socket?.readyState === WebSocket.OPEN) {
        setState((current) => ({ ...current, connection: 'connected' }))
        void refresh()
        return
      }
      connect()
    }
    window.addEventListener('offline', offline)
    window.addEventListener('online', online)
    return () => {
      closed = true
      refreshGeneration.current += 1
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer)
      clearInterval(poll)
      socket?.close()
      window.removeEventListener('offline', offline)
      window.removeEventListener('online', online)
    }
  }, [refresh])

  const value = useMemo(
    () => ({
      ...state,
      runtime,
      office,
      tools,
      refresh,
      action,
      selectAgent,
      selectTask,
      selectedAgentId,
      selectedTaskId,
    }),
    [state, runtime, office, tools, refresh, action, selectedAgentId, selectedTaskId],
  )
  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function useAppStore() {
  const value = useContext(Context)
  if (!value) throw new Error('AppStoreProvider is required')
  return value
}
