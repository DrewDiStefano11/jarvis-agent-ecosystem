/* Real isolated API/worker + browser review; provider is the harness fixture. */
const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const { chromium } = require(path.join(process.env.SMOKE_WEB, 'node_modules/playwright'))
const base = process.env.SMOKE_BASE
const ui = process.env.SMOKE_UI
const directory = process.env.SMOKE_ARTIFACT_DIR
fs.mkdirSync(directory, { recursive: true })
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const assert = (value, message) => { if (!value) throw new Error(message) }

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const get = async endpoint => { const response = await fetch(base + endpoint); if (!response.ok) throw new Error(await response.text()); return (await response.json()).data }
  const report = { tasks: [], screenshots: [], errors, inference: 'deterministic HTTP fixture only' }
  try {
    await page.goto(ui + '/inbox')
    await page.getByRole('button', { name: 'Enable local result review', exact: true }).click()
    await page.getByRole('heading', { name: 'You’re up to date' }).waitFor()
    for (const name of ['Accept', 'Changes', 'Reject']) {
      const task = await page.evaluate(async ({ base, name }) => {
        const response = await fetch(base + '/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: name + ' trapeze QC checklist', description: 'Prepare a Level 1 trapeze prefab quality-control checklist. Flag missing project schedules and field evidence.', priority: 'medium' }) })
        if (!response.ok) throw new Error(await response.text())
        return (await response.json()).data
      }, { base, name })
      execFileSync(process.env.SMOKE_PYTHON, ['-m', 'app.autonomous_worker.setup', '--task-id', task.id], { cwd: process.env.SMOKE_API, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] })
      await page.evaluate(async ({ task, actor }) => {
        const { newPlanningSubmission, submitPlanning } = await import('/src/api/planning.ts')
        await submitPlanning(newPlanningSubmission(task, actor, actor))
      }, { task, actor: process.env.SMOKE_ACTOR })
      const deadline = Date.now() + 30000
      while ((await get('/api/tasks/' + task.id)).status !== 'under_review') { assert(Date.now() < deadline, 'Worker did not pause for review'); await sleep(100) }
      await page.getByRole('button', { name: 'Refresh results', exact: true }).click()
      const card = page.getByRole('article', { name: 'Review ' + task.title, exact: true })
      await card.waitFor()
      assert(await card.locator('.result-recommendations li').count() === 5, 'Full five recommendations were not rendered')
      await card.getByText('No project schedules or field documents were supplied.', { exact: true }).waitFor()
      await card.getByLabel('Review note', { exact: true }).fill('Reviewed all five recommendations, assumptions and missing evidence.')
      if (name === 'Accept') {
        for (const width of [1920, 1440, 1024, 768, 390, 320]) {
          await page.setViewportSize({ width, height: 1100 })
          await page.evaluate(() => window.scrollTo(0, 0))
          await sleep(100)
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
          assert(!overflow, 'Inbox overflow at ' + width)
          const file = path.join(directory, `inbox-${width}.png`)
          await page.screenshot({ path: file, fullPage: true }); report.screenshots.push(file)
        }
        await page.setViewportSize({ width: 1440, height: 1100 })
        let lost = false
        await page.route('**/api/results/executions/*/review', async route => {
          if (!lost) { lost = true; await route.fetch(); await route.abort('failed') } else await route.continue()
        })
        await card.getByRole('button', { name: 'Accept result', exact: true }).click()
        await page.getByRole('heading', { name: 'Decision acknowledgement pending' }).waitFor()
        await page.reload()
        await page.getByRole('button', { name: 'Retry captured decision' }).click()
        await page.getByRole('heading', { name: 'Result accepted', exact: true }).waitFor()
        await page.unroute('**/api/results/executions/*/review')
        await page.getByRole('link', { name: 'View accepted output in Library' }).click()
        await page.getByRole('button', { name: 'Preview output' }).click()
        await page.locator('.result-recommendations li').nth(4).waitFor()
        const downloaded = page.waitForEvent('download')
        await page.getByRole('button', { name: 'Download JSON' }).click()
        const download = await downloaded
        await download.saveAs(path.join(directory, 'accepted-output.json'))
        assert(JSON.parse(fs.readFileSync(path.join(directory, 'accepted-output.json'), 'utf8')).requiresHumanReview === true, 'Original model evidence was overwritten')
        for (const width of [1440, 390, 320]) {
          await page.setViewportSize({ width, height: 1100 })
          await page.evaluate(() => window.scrollTo(0, 0))
          assert(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), 'Library overflow at ' + width)
          await page.screenshot({ path: path.join(directory, `library-${width}.png`), fullPage: true })
        }
        await page.setViewportSize({ width: 1440, height: 1100 })
        await page.goto(ui + '/inbox')
      } else if (name === 'Changes') {
        await card.getByRole('button', { name: 'Request changes', exact: true }).click()
        await card.getByLabel('Full revised request', { exact: true }).fill('Revise the Level 1 trapeze checklist using the approved project schedule and include field sign-off evidence.')
        await card.getByRole('button', { name: 'Create correction draft' }).click()
        await page.getByRole('heading', { name: 'Correction draft created', exact: true }).waitFor()
      } else {
        await card.getByRole('button', { name: 'Reject result', exact: true }).click()
        await page.getByRole('heading', { name: 'Result rejected', exact: true }).waitFor()
      }
      const final = await get('/api/tasks/' + task.id)
      assert(final.status === ({ Accept: 'completed', Changes: 'revision_requested', Reject: 'cancelled' })[name], 'Wrong authoritative task state')
      report.tasks.push({ id: task.id, status: final.status, result: final.result })
    }
    const tasks = await get('/api/tasks')
    const correction = tasks.filter(task => task.correctionOfTaskId === report.tasks[1].id)
    assert(correction.length === 1 && correction[0].status === 'queued', 'Correction draft duplicated or executed')
    assert(errors.length === 0, 'Browser page errors: ' + errors.join('; '))
    fs.writeFileSync(path.join(directory, 'result-review-acceptance.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
