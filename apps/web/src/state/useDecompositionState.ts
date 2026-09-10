import { useEffect, useState } from 'react'
import { request } from '../api/client'
import type { Decomposition } from '../types/decomposition'

export function useDecompositionState(taskId: string | null, lastSync: string | null) {
  const [state, setState] = useState<{ taskId: string | null; record: Decomposition | null; error: string }>({ taskId: null, record: null, error: '' })
  useEffect(() => {
    if (!taskId) return
    let active = true
    request<Decomposition | null>(`/api/tasks/${encodeURIComponent(taskId)}/decomposition`)
      .then(record => { if (active) setState({ taskId, record, error: '' }) })
      .catch(error => { if (active) setState({ taskId, record: null, error: error instanceof Error ? error.message : 'Unable to load planned work' }) })
    return () => { active = false }
  }, [taskId, lastSync])
  return state.taskId === taskId ? state : { taskId, record: null, error: '' }
}
