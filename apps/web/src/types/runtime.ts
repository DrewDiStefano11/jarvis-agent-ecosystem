export interface RuntimeIdentity {
  id: string
  display_name: string
  stable_key: string
  lifecycle_state: string
  operational_status: string
  is_enabled: boolean
  agent_type: string
}
export interface RuntimeRun {
  specification: { run_id: string; task_id: string; agent_id: string; requested_operation: string; autonomous_execution: { execution_type: string; context_assembly_id: string } | null }
  state: string
  version: number
  attempt_count: number
  status_detail: string | null
  active_attempt_id: string | null
}
export interface RuntimePage { items: RuntimeRun[]; next_offset: number | null; total_count: number }
export interface PlanningResult {
  summary: string
  analysis: string
  recommendations: { title: string; description: string; priority: string }[]
  risks: { title: string; description: string; severity: string; mitigation: string }[]
  assumptions: string[]
  missingInformation: string[]
  requiresHumanReview: boolean
}
export interface ModelExecution {
  executionId: string; runtimeRunId: string; runtimeAttemptId: string; taskId: string; targetAgentId: string
  workerId: string; stage: string; provider: string | null; model: string | null
  result: PlanningResult | null; failureCode: string | null; requestCount: number
  createdAt: string; completedAt: string | null
}
export interface AutonomousStatus {
  enabled: boolean; modelExecutionMode: string; status: string; reasonCode: string | null
  activeExecutionCount: number; queuedEligibleRuntimeCount: number; completedExecutionCount: number
  failedExecutionCount: number; reviewRequiredCount: number; providerReady: boolean
  lastWorkerHeartbeat: string | null; lastSuccessfulExecutionAt: string | null
}
