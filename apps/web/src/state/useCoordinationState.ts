import { useEffect, useState } from 'react'
import { request } from '../api/client'
import type { Coordination } from '../types/coordination'

/** Read-only durable projection using the existing AppStore synchronization. */
export function useCoordinationState(taskId: string | null, lastSync: string | null) {
  const [state, setState] = useState<{ taskId: string | null; record: Coordination | null; error: string }>({ taskId: null, record: null, error: '' })
  useEffect(() => {
    if (!taskId) return
    let active = true
    request<Coordination | null>(`/api/tasks/${encodeURIComponent(taskId)}/coordination`)
      .then(record => { if (active) setState({ taskId, record, error: '' }) })
      .catch(error => { if (active) setState({ taskId, record: null, error: error instanceof Error ? error.message : 'Unable to load coordinator' }) })
    return () => { active = false }
  }, [taskId, lastSync])
  return state.taskId === taskId ? state : { taskId, record: null, error: '' }
}
