import { request } from './client'
import type { Task } from '../types/contracts'
import type { RuntimeRun } from '../types/runtime'

export interface PlanningSubmission {
  id: string; timestamp: string; task: Task; actorId: string; targetId: string
  responseFormat?: 'planning_review_json_v1' | 'workspace_plan_json_v1'
}
export const newPlanningSubmission = (task: Task, actorId: string, targetId: string, mode: 'planning' | 'workspace' = 'planning'): PlanningSubmission => ({
  id: crypto.randomUUID(), timestamp: new Date().toISOString(), task: structuredClone(task), actorId, targetId,
  responseFormat: mode === 'workspace' ? 'workspace_plan_json_v1' : 'planning_review_json_v1',
})

/** Repeating the same submission resumes its durable commands without creating new work. */
export async function submitPlanning(submission: PlanningSubmission): Promise<RuntimeRun> {
  const { id, timestamp, task, actorId, targetId, responseFormat } = submission
  const workspace = responseFormat === 'workspace_plan_json_v1'
  const content = task.description
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content))
  const contentHash = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
  const assembly = await request<{ id: string; status: string }>('/api/context/assemblies', {
    method: 'POST', headers: { 'Idempotency-Key': `planning-context-${id}` },
    body: JSON.stringify({
      taskId: task.id, projectId: task.projectId ?? 'jarvis-agent-ecosystem',
      allowedResultType: 'structured_output', completionCriteria: workspace ? 'Propose a fixed bounded workspace plan with exact file actions and useful report content, for explicit operator authorization.' : 'Produce a bounded local planning review with recommendations, risks and explicit assumptions.',
      toolAvailabilitySummary: workspace
        ? { proposed_tools: ['workspace.list', 'workspace.read', 'workspace.write', 'workspace.report'], execution_requires_separate_operator_authorization: true, prohibited_tools: ['shell', 'browser', 'external_actions'] }
        : { prohibited_tools: ['shell', 'browser', 'filesystem', 'external_actions'] },
      policy: { maximumContextTokens: 8192, estimatedTokenBudget: 8192, reservedOutputTokens: 2048 },
      sources: [{ sourceId: `request-${id}`, sourceType: 'manual_note', trustLevel: 'operator_instruction',
        title: 'Operator supplied task context', content, contentHash,
        metadata: { projectId: task.projectId ?? 'jarvis-agent-ecosystem', approved: true, truncationAllowed: false } }],
    }),
  })
  if (assembly.status !== 'completed') throw new Error(`Context ${assembly.id} requires review. No execution was queued.`)
  const headers = { 'X-Jarvis-Actor-Id': actorId }
  const created = await request<{ snapshot: RuntimeRun }>('/api/agent-runtime/commands', {
    method: 'POST', headers, body: JSON.stringify({ command_type: 'create', command_id: `planning-create-${id}`,
      timestamp, actor_reference: actorId,
      specification: { run_id: `run-${id}`, task_id: task.id, agent_id: targetId,
        requested_operation: workspace ? 'Workspace action plan' : 'Local planning review', created_at: timestamp, idempotency_key: `planning-${id}`,
        maximum_permitted_attempts: 3, autonomous_execution: { execution_type: workspace ? 'workspace_plan' : 'planning_review',
          context_assembly_id: assembly.id, maximum_provider_requests: 2, maximum_repair_calls: 1,
          ...(responseFormat ? { response_format: responseFormat } : {}),
          maximum_output_tokens: 2048, maximum_execution_seconds: 300 } },
    }),
  })
  const queued = await request<{ snapshot: RuntimeRun }>('/api/agent-runtime/commands', {
    method: 'POST', headers, body: JSON.stringify({ command_type: 'queue', command_id: `planning-queue-${id}`,
      timestamp, actor_reference: actorId, run_id: created.snapshot.specification.run_id,
      expected_run_version: created.snapshot.version, detail: 'Operator explicitly queued local planning review' }),
  })
  return queued.snapshot
}
