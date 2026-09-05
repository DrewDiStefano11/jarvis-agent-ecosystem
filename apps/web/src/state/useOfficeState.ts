import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, post, request } from '../api/client'
import type { OfficeCommand, OfficeCommandResult, OfficeSnapshot } from '../types/office'

/** Canonical office projection. AppStore's ordered event/resync path owns refresh. */
export function useOfficeState(lastSync: string | null) {
  const [snapshot, setSnapshot] = useState<OfficeSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<{ identityId: string; command: OfficeCommand } | null>(null)
  const generation = useRef(0)
  const active = useRef(false)
  const sending = useRef(false)
  const load = useCallback(async () => {
    active.current = true
    const current = ++generation.current
    try {
      const data = await request<OfficeSnapshot>('/api/office')
      if (current === generation.current) { setSnapshot(data); setSyncError(null) }
    } catch (caught) {
      if (current === generation.current) setSyncError(caught instanceof Error ? caught.message : 'Office synchronization failed')
    }
  }, [])
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (active.current && !cancelled) void load() })
    return () => { cancelled = true; generation.current += 1 }
  }, [lastSync, load])
  const send = useCallback(async (identityId: string, command: OfficeCommand) => {
    if (sending.current) return
    sending.current = true
    setBusy(true)
    setPending({ identityId, command })
    setError(null)
    try {
      await post<OfficeCommandResult>(`/api/office/identities/${encodeURIComponent(identityId)}/commands`, command)
      setPending(null)
      await load()
    } catch (caught) {
      // Unknown acknowledgement retains the exact idempotency key for an explicit retry.
      if (caught instanceof ApiError && caught.status < 500) { setPending(null); await load() }
      setError(caught instanceof Error ? caught.message : 'Office command acknowledgement was lost')
    } finally { sending.current = false; setBusy(false) }
  }, [load])
  const command = useCallback((identityId: string, body: Omit<OfficeCommand, 'commandId'>) =>
    send(identityId, { ...body, commandId: `office-${crypto.randomUUID()}` }), [send])
  const retry = useCallback(async () => { if (pending) await send(pending.identityId, pending.command) }, [pending, send])
  return { snapshot, error: error ?? syncError, busy, pending, load, command, retry }
}
