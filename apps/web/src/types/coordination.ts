export interface Coordination {
  id: string
  taskId: string
  status: 'active' | 'blocked' | 'synthesizing' | 'completing' | 'completed' | 'failed'
  blockedReason: string | null
  nodes: {
    subtaskId: string
    key: string
    assignedAgentId: string
    status: 'pending' | 'claimed' | 'running' | 'retrying' | 'succeeded' | 'blocked' | 'failed'
    attemptCount: number
    retryEligibleAt: string | null
    resultSummary: string | null
    failureDetail: string | null
    provider: string | null
    model: string | null
  }[]
  synthesis: {
    status: 'pending' | 'running' | 'succeeded' | 'failed'
    attemptCount: number
    summary: string | null
    failureDetail: string | null
    retryEligibleAt: string | null
    inputSubtaskIds: string[]
  }
}
