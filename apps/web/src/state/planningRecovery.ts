import { API_BASE } from '../api/client'
import type { PlanningSubmission } from '../api/planning'
import type { Task } from '../types/contracts'

// Store retry identifiers and an input digest, never task text or model output.
function apiStorageScope(): string {
  try { return new URL(API_BASE, window.location.origin).origin }
  catch { return 'invalid-api-origin' }
}
const PREFIX = `jarvis:planning:v1:${encodeURIComponent(apiStorageScope())}:`
export interface SavedPlanningSubmission {
  version: 1
  id: string
  timestamp: string
  actorId: string
  targetId: string
  taskId: string
  inputHash: string
  responseFormat?: PlanningSubmission['responseFormat']
}
export interface PlanningRecoveryList { items: SavedPlanningSubmission[]; warning: string }
const identifier = (value: unknown): value is string => typeof value === 'string'
  && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/.test(value)

function parseSaved(value: string): SavedPlanningSubmission | null {
  const item: unknown = JSON.parse(value)
  if (!item || typeof item !== 'object') return null
  const record = item as Record<string, unknown>
  if (record.version !== 1 || !identifier(record.id) || !identifier(record.actorId)
    || !identifier(record.targetId) || !identifier(record.taskId)
    || typeof record.timestamp !== 'string' || !Number.isFinite(Date.parse(record.timestamp))
    || typeof record.inputHash !== 'string' || !/^[a-f0-9]{64}$/.test(record.inputHash)
    || (record.responseFormat !== undefined && record.responseFormat !== 'planning_review_json_v1')) return null
  return { version: 1, id: record.id, timestamp: record.timestamp, actorId: record.actorId,
    targetId: record.targetId, taskId: record.taskId, inputHash: record.inputHash,
    ...(record.responseFormat === undefined ? {} : { responseFormat: record.responseFormat }) }
}

export function readPlanningRecovery(): PlanningRecoveryList {
  const items: SavedPlanningSubmission[] = []
  let warning = ''
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (!key?.startsWith(PREFIX)) continue
      try {
        const record = parseSaved(localStorage.getItem(key) ?? 'null')
        if (record && key === `${PREFIX}${record.id}`) items.push(record)
        else warning = 'A saved submission is unreadable. Inspect durable history before creating replacement work.'
      } catch { warning = 'A saved submission is unreadable. Inspect durable history before creating replacement work.' }
    }
  } catch { warning = 'Browser retry storage is unavailable. Keep this page open while submitting and inspect history after a reload.' }
  return { items: items.sort((a, b) => b.timestamp.localeCompare(a.timestamp)), warning }
}

async function inputHash(task: Task): Promise<string> {
  const input = JSON.stringify([task.id, task.projectId ?? 'jarvis-agent-ecosystem', task.description])
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input))
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

export async function rememberPlanningSubmission(submission: PlanningSubmission): Promise<string> {
  const record: SavedPlanningSubmission = { version: 1, id: submission.id,
    timestamp: submission.timestamp, actorId: submission.actorId, targetId: submission.targetId,
    taskId: submission.task.id, inputHash: await inputHash(submission.task),
    ...(submission.responseFormat === undefined ? {} : { responseFormat: submission.responseFormat }) }
  try { localStorage.setItem(`${PREFIX}${record.id}`, JSON.stringify(record)); return '' }
  catch { return 'Browser retry storage is unavailable. Keep this page open while submitting and inspect history after a reload.' }
}

export function forgetPlanningSubmission(id: string): string {
  try { localStorage.removeItem(`${PREFIX}${id}`); return '' }
  catch { return 'The saved retry ID could not be removed. It is safe to inspect history and replay that same ID; do not create duplicate work.' }
}

export async function restorePlanningSubmission(saved: SavedPlanningSubmission, task: Task): Promise<PlanningSubmission> {
  if (task.id !== saved.taskId || await inputHash(task) !== saved.inputHash) {
    throw new Error('The task input changed. Inspect the original run in history; this submission cannot be replayed with different input.')
  }
  return { id: saved.id, timestamp: saved.timestamp, actorId: saved.actorId, targetId: saved.targetId,
    task: structuredClone(task), ...(saved.responseFormat === undefined ? {} : { responseFormat: saved.responseFormat }) }
}
