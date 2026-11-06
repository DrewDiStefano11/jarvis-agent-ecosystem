import type { ModelExecution } from './runtime'

export type ReviewDecision = 'accept' | 'request_changes' | 'reject'
export interface ResultReviewItem {
  execution: ModelExecution
  taskTitle: string
  taskUpdatedAt: string
  runVersion: number
  checkpointId: string
}
export interface ReviewCommand {
  command_type: 'review_result'; run_id: string; command_id: string; expected_run_version: number
  timestamp: string; actor_reference: string; execution_id: string; expected_result_hash: string
  expected_checkpoint_id: string; expected_task_updated_at: string; decision: ReviewDecision
  reason: string; revised_request?: string
}
export interface ReviewReceipt {
  id: string; executionId: string; taskId: string; actorId: string; commandId: string
  decision: ReviewDecision; resultHash: string; artifactId: string | null
  correctionTaskId: string | null; reviewedAt: string
}
export interface ReviewedArtifact {
  id: string; taskId: string; name: string; summary: string; createdAt: string
  resultHash: string; review: ReviewReceipt
}
