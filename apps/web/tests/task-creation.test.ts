import { webcrypto } from 'node:crypto'
import { beforeEach, expect, test, vi } from 'vitest'
import { acknowledgeTaskCreation, prepareTaskCreation, submitTaskCreation, type TaskCreateInput } from '../src/state/taskCreation'

const input: TaskCreateInput = { title: 'Corrected private request', description: 'Revised operator input remains on the server.', priority: 'high', correctionOfTaskId: 'original-task' }
beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); vi.stubGlobal('crypto', webcrypto) })

test('uncertain creation replays the exact input and key after a reload, then permits intentional new work after acknowledgement', async () => {
  const attempt = await prepareTaskCreation(input)
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError('Network interrupted')).mockResolvedValue({ ok: true, json: async () => ({ data: { id: 'one-corrected-task' } }) })
  vi.stubGlobal('fetch', fetch)
  await expect(submitTaskCreation(attempt)).rejects.toThrow('Network interrupted')
  const restored = await prepareTaskCreation(structuredClone(input))
  expect(restored.key).toBe(attempt.key)
  expect(await submitTaskCreation(restored)).toEqual({ id: 'one-corrected-task' })
  expect(fetch.mock.calls[0]).toEqual(fetch.mock.calls[1])
  expect(localStorage.getItem(attempt.storageKey)).not.toContain(input.description)
  expect(attempt.storageKey).not.toContain(input.title)
  expect(acknowledgeTaskCreation(restored)).toBe('')
  expect((await prepareTaskCreation(input)).key).not.toBe(attempt.key)
})

test('changed input and a different correction source use distinct creation identities', async () => {
  const attempt = await prepareTaskCreation(input)
  expect((await prepareTaskCreation({ ...input, description: 'Different revised input' })).key).not.toBe(attempt.key)
  expect((await prepareTaskCreation({ ...input, correctionOfTaskId: 'another-task' })).key).not.toBe(attempt.key)
})

test('storage denial is visible, and malformed retry metadata cannot silently create duplicate work', async () => {
  const attempt = await prepareTaskCreation(input)
  localStorage.setItem(attempt.storageKey, 'broken')
  await expect(prepareTaskCreation(input)).rejects.toThrow('retry key is unreadable')
  localStorage.clear()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError') })
  expect((await prepareTaskCreation(input)).warning).toContain('retry storage is unavailable')
})
