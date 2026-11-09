import { request } from './client'
import type { Task } from '../types/contracts'
import type { RuntimeRun } from '../types/runtime'

export interface PlanningSubmission {
  id: string; timestamp: string; task: Task; actorId: string; targetId: string
  budgetProfile?: 'direct-v1'
  responseFormat?: 'planning_review_json_v1' | 'workspace_plan_json_v1'
}
export const newPlanningSubmission = (task: Task, actorId: string, targetId: string, mode: 'planning' | 'workspace' = 'planning'): PlanningSubmission => ({
  id: crypto.randomUUID(), timestamp: new Date().toISOString(), task: structuredClone(task), actorId, targetId,
  ...(mode === 'planning' ? { budgetProfile: 'direct-v1' as const } : {}),
  responseFormat: mode === 'workspace' ? 'workspace_plan_json_v1' : 'planning_review_json_v1',
})

export async function submitPlanning(submission: PlanningSubmission): Promise<{ snapshot: RuntimeRun, contextCategories: string[] }> {
  const { id, timestamp, task, actorId, targetId, responseFormat, budgetProfile } = submission
  if (budgetProfile === 'direct-v1' && responseFormat !== 'planning_review_json_v1') throw new Error('The captured direct budget profile requires its original structured response format.')
  const workspace = responseFormat === 'workspace_plan_json_v1'
  const direct = budgetProfile === 'direct-v1' && !workspace
  const content = task.description
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content))
  const contentHash = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
  const assembly = await request<{ id: string; status: string; manifest: { includedSources: Array<{ sourceType: string }> } }>('/api/context/assemblies', {
    method: 'POST', headers: { 'Idempotency-Key': `planning-context-${id}`, 'X-Jarvis-Actor-Id': actorId },
    body: JSON.stringify({
      ...(direct ? { executionBudget: { maximumRequests: 1, maximumTokens: 7500, maximumAttempts: 1, stages: [{ stage: 'planning', maximumRequests: 1, maximumInputTokens: 5964, maximumOutputTokens: 1536 }] } } : {}),
      taskId: task.id, projectId: task.projectId ?? 'jarvis-agent-ecosystem',
      allowedResultType: 'structured_output', completionCriteria: workspace ? 'Propose a fixed bounded workspace plan with exact file actions and useful report content, for explicit operator authorization.' : 'Produce a bounded local planning review with recommendations, risks and explicit assumptions.',
      toolAvailabilitySummary: workspace
        ? { proposed_tools: ['workspace.list', 'workspace.read', 'workspace.write', 'workspace.report'], required_authorization: ['explicit_operator_review'], prohibited_tools: ['shell', 'browser', 'external_actions'] } satisfies Record<string, string[]>
        : { prohibited_tools: ['shell', 'browser', 'filesystem', 'external_actions'] },
      policy: { maximumContextTokens: direct ? 4096 : 8192, estimatedTokenBudget: direct ? 4096 : 8192, reservedOutputTokens: direct ? 1536 : 2048 },
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
        maximum_permitted_attempts: direct ? 1 : 3, autonomous_execution: { execution_type: workspace ? 'workspace_plan' : 'planning_review',
          context_assembly_id: assembly.id, maximum_provider_requests: direct ? 1 : 2, maximum_repair_calls: direct ? 0 : 1,
          ...(responseFormat ? { response_format: responseFormat } : {}),
          maximum_output_tokens: direct ? 1536 : 2048, maximum_execution_seconds: 300 } },
    }),
  })
  const queued = await request<{ snapshot: RuntimeRun }>('/api/agent-runtime/commands', {
    method: 'POST', headers, body: JSON.stringify({ command_type: 'queue', command_id: `planning-queue-${id}`,
      timestamp, actor_reference: actorId, run_id: created.snapshot.specification.run_id,
      expected_run_version: created.snapshot.version, detail: 'Operator explicitly queued local planning review' }),
  })
  const categories = Array.from(new Set(assembly.manifest.includedSources.map(s => s.sourceType)))
  return { snapshot: queued.snapshot, contextCategories: categories }
}
