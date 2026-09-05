/* Local browser golden path. Invoked by smoke-local-planning.py, never production. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.SMOKE_WEB, 'node_modules/playwright'))

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  page.setDefaultTimeout(15000)
  const errors = []
  const failures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })
  const output = process.env.SMOKE_ARTIFACT_DIR
  fs.mkdirSync(output, { recursive: true })
  try {
    await page.goto(process.env.SMOKE_UI)
    await page.getByRole('heading', { name: 'Good evening, operator.' }).waitFor()
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true })
    await nav.getByRole('link', { name: 'Tasks', exact: true }).click()
    await page.getByRole('button', { name: '+ New task' }).click()
    await page.getByLabel('Title', { exact: true }).fill('Browser golden path')
    await page.getByLabel('Description', { exact: true }).fill('Create a bounded plan to verify the local Hub.')
    const created = page.waitForResponse(response => response.url().endsWith('/api/tasks') && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Create task', exact: true }).click()
    const task = (await (await created).json()).data
    assert.ok(task.id)
    await nav.getByRole('link', { name: 'Planning', exact: true }).click()
    await page.getByLabel('Task and history').selectOption(task.id)
    await page.getByRole('button', { name: 'Prepare local planner for this task', exact: true }).click()
    await page.getByText('Local planner prepared for this task. Queue the plan when ready.', { exact: true }).waitFor()
    assert.equal(await page.getByLabel('Act as local identity').inputValue(), process.env.SMOKE_ACTOR)
    // Runtime creation commits, but its acknowledgement is unreadable. The task
    // stays eligible: reload/recovery must replay creation before queueing once.
    let droppedAcknowledgement = false
    const commands = []
    await page.route('**/api/agent-runtime/commands', async route => {
      const command = route.request().postDataJSON()
      commands.push(command)
      if (command.command_type === 'create' && !droppedAcknowledgement) {
        droppedAcknowledgement = true
        const response = await route.fetch()
        assert.equal(response.status(), 200)
        await route.fulfill({ response, body: '{interrupted acknowledgement' })
      } else await route.continue()
    })
    await page.getByRole('button', { name: 'Queue local plan', exact: true }).click()
    await page.getByRole('button', { name: 'Retry same submission', exact: true }).waitFor()
    assert.equal(commands.length, 1)
    await page.reload()
    await page.getByRole('button', { name: 'Recover submission', exact: true }).waitFor()
    await page.setViewportSize({ width: 390, height: 844 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Saved planning form overflows on mobile')
    await page.screenshot({ path: path.join(output, 'planning-recovery-mobile.png'), fullPage: true })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('button', { name: 'Recover submission', exact: true }).click()
    await page.getByText(/Saved submission recovered/).waitFor()
    assert.equal(commands.length, 1, 'Restoring a saved form must not queue automatically')
    assert.equal(await page.getByLabel('Act as local identity').inputValue(), process.env.SMOKE_ACTOR)
    assert.equal(await page.getByLabel('Task and history').inputValue(), task.id)
    await page.getByRole('button', { name: 'Retry same submission', exact: true }).click()
    await page.getByRole('button', { name: 'Queue local plan', exact: true }).waitFor()
    assert.equal(commands.length, 3)
    assert.deepEqual(commands[0], commands[1], 'Reload must preserve the exact original create command')
    assert.equal(commands[2].command_type, 'queue')
    await page.getByRole('heading', { name: 'Deterministic transport fixture completed.', exact: true }).waitFor({ timeout: 30000 })
    const modelHistory = await page.request.get(`${process.env.SMOKE_BASE}/api/model-executions?taskId=${encodeURIComponent(task.id)}`, { headers: { 'X-Jarvis-Actor-Id': process.env.SMOKE_ACTOR } })
    assert.equal((await modelHistory.json()).data.length, 1, 'Acknowledgement replay must not duplicate inference')
    assert.equal(await page.getByRole('button', { name: 'Recover submission', exact: true }).count(), 0)
    await page.screenshot({ path: path.join(output, 'planning-completed.png'), fullPage: true })
    const persisted = await page.request.get(`${process.env.SMOKE_BASE}/api/tasks/${task.id}`)
    const originalCompleted = (await persisted.json()).data
    assert.equal(originalCompleted.status, 'completed')
    // Correct a completed request through the ordinary UI. Task creation also
    // survives an unreadable acknowledgement and a reload with identical input.
    await page.getByRole('link', { name: 'Revise task input', exact: true }).click()
    await page.getByRole('heading', { name: 'Correct task input', exact: true }).waitFor()
    const correctionInput = { title: 'Browser corrected plan', description: 'Revise the Hub validation plan with explicit restart acceptance.' }
    const fillCorrection = async () => {
      await page.getByLabel('Title', { exact: true }).fill(correctionInput.title)
      await page.getByLabel('Description', { exact: true }).fill(correctionInput.description)
    }
    await fillCorrection()
    const creations = []
    let correctedTask
    await page.route('**/api/tasks', async route => {
      if (route.request().method() !== 'POST') return route.continue()
      creations.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] })
      const response = await route.fetch()
      assert.equal(response.status(), 201)
      correctedTask = (await response.json()).data
      await route.fulfill(creations.length === 1 ? { response, body: '{interrupted creation acknowledgement' } : { response })
    })
    await page.getByRole('button', { name: 'Create corrected task', exact: true }).click()
    await page.getByRole('button', { name: 'Retry creation', exact: true }).waitFor()
    await page.reload()
    await page.getByRole('heading', { name: 'Correct task input', exact: true }).waitFor()
    await fillCorrection()
    await page.getByRole('button', { name: 'Create corrected task', exact: true }).click()
    await page.getByRole('link', { name: 'Open planning for this task', exact: true }).waitFor()
    assert.equal(creations.length, 2)
    assert.deepEqual(creations[0], creations[1], 'Creation retry must use the same input and idempotency key')
    assert.equal(correctedTask.correctionOfTaskId, task.id)
    const allTasks = (await (await page.request.get(`${process.env.SMOKE_BASE}/api/tasks`)).json()).data
    assert.equal(allTasks.filter(item => item.correctionOfTaskId === task.id).length, 1)
    assert.deepEqual((await (await page.request.get(`${process.env.SMOKE_BASE}/api/tasks/${task.id}`)).json()).data, originalCompleted)
    await page.getByRole('link', { name: 'Open planning for this task', exact: true }).click()
    await page.getByLabel('Task and history').selectOption(correctedTask.id)
    await page.getByRole('button', { name: 'Prepare local planner for this task', exact: true }).click()
    await page.getByText('Local planner prepared for this task. Queue the plan when ready.', { exact: true }).waitFor()
    await page.getByRole('button', { name: 'Queue local plan', exact: true }).click()
    await page.getByRole('heading', { name: 'Deterministic transport fixture completed.', exact: true }).waitFor({ timeout: 30000 })
    const correctionHistory = await page.request.get(`${process.env.SMOKE_BASE}/api/model-executions?taskId=${encodeURIComponent(correctedTask.id)}`, { headers: { 'X-Jarvis-Actor-Id': process.env.SMOKE_ACTOR } })
    assert.equal((await correctionHistory.json()).data.length, 1)
    await page.screenshot({ path: path.join(output, 'planning-correction-completed.png'), fullPage: true })
    await nav.getByRole('link', { name: 'Office', exact: true }).click()
    await page.getByRole('heading', { name: 'Operations floor', exact: true }).waitFor()
    await page.getByLabel('Office identity').selectOption(process.env.SMOKE_ACTOR)
    assert.equal(await page.getByLabel('Office identity').inputValue(), process.env.SMOKE_ACTOR)
    await page.waitForFunction(() => {
      const image = document.querySelector('.office-background')
      return image?.complete && image.naturalWidth === 8192 && image.naturalHeight === 5460
    })
    assert.equal(await page.title(), 'Office · Jarvis')
    const before = await page.locator('.office-surface').getAttribute('style')
    await page.getByRole('button', { name: 'Zoom in', exact: true }).click()
    assert.notEqual(await page.locator('.office-surface').getAttribute('style'), before)
    await page.getByRole('button', { name: 'Fit office', exact: true }).click()
    await page.screenshot({ path: path.join(output, 'office-desktop.png'), fullPage: true })
    await page.getByLabel('Inspect candidate geometry').check()
    await page.waitForFunction(() => document.querySelector('select')?.options.length > 1)
    await page.getByLabel('Inspect region').selectOption({ index: 1 })
    const beforeFocus = await page.locator('.office-surface').getAttribute('style')
    await page.getByRole('button', { name: 'Focus selected region', exact: true }).click()
    await page.waitForFunction(previous => document.querySelector('.office-surface')?.getAttribute('style') !== previous, beforeFocus)
    await page.getByRole('heading', { name: 'Unverified floor registration', exact: true }).waitFor()
    await page.locator('.office-viewport-shell--candidate').waitFor()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: path.join(output, 'office-candidate-review.png'), fullPage: true })
    await page.getByLabel('Inspect candidate geometry').uncheck()
    await page.setViewportSize({ width: 390, height: 844 })
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.getByRole('button', { name: 'Fit office', exact: true }).click()
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Mobile document overflows horizontally')
    await page.screenshot({ path: path.join(output, 'office-mobile.png'), fullPage: true })
    assert.deepEqual(errors, [], 'Browser console/page errors')
    assert.deepEqual(failures, [], 'HTTP failures')
    console.log('PASS: browser submission/correction, lost-acknowledgement reload/recovery, one worker fixture result per task, preserved source, office camera/regions, desktop/mobile layout')
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
    console.error({ errors, failures })
    throw error
  } finally {
    await browser.close()
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
