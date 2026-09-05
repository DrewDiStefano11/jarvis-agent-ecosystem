import { API_BASE, request } from '../api/client'
import type { Task } from '../types/contracts'

export interface TaskCreateInput {
  title: string
  description: string
  priority: Task['priority']
  correctionOfTaskId?: string
  projectId?: string
}
export interface TaskCreationAttempt { input: TaskCreateInput; key: string; storageKey: string; warning: string }

/** Keep only a request fingerprint and retry key in browser storage. */
export async function prepareTaskCreation(input: TaskCreateInput): Promise<TaskCreationAttempt> {
  const frozen = structuredClone(input)
  const fields = [input.title, input.description, input.priority, input.correctionOfTaskId ?? null]
  if (input.projectId !== undefined) fields.push(input.projectId)
  const serialized = JSON.stringify(fields)
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(serialized))
  const hash = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
  const origin = new URL(API_BASE, window.location.origin).origin
  const storageKey = `jarvis:task-create:v1:${encodeURIComponent(origin)}:${hash}`
  let key = `task-create-${crypto.randomUUID()}`
  let warning = ''
  let invalid = false
  try {
    const existing = localStorage.getItem(storageKey)
    invalid = Boolean(existing && !/^task-create-[a-f0-9-]{36}$/.test(existing))
    if (existing && !invalid) key = existing
    if (!invalid) localStorage.setItem(storageKey, key)
  } catch { warning = 'Browser retry storage is unavailable. Keep this form open until creation is acknowledged.' }
  if (invalid) throw new Error('The saved creation retry key is unreadable. Inspect task history before creating replacement work.')
  return { input: frozen, key, storageKey, warning }
}

export const submitTaskCreation = (attempt: TaskCreationAttempt) => request<Task>('/api/tasks', {
  method: 'POST', headers: { 'Idempotency-Key': attempt.key }, body: JSON.stringify(attempt.input),
})

export function acknowledgeTaskCreation(attempt: TaskCreationAttempt): string {
  try {
    if (localStorage.getItem(attempt.storageKey) === attempt.key) localStorage.removeItem(attempt.storageKey)
    return ''
  } catch { return 'Task created, but its browser retry key could not be cleared. Inspect the existing task before making identical work.' }
}
