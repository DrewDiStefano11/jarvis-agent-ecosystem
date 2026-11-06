/* Ordinary browser flow; all inference is the isolated harness HTTP fixture. */
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.SMOKE_WEB, 'node_modules/playwright'))
const assert = (value, message) => { if (!value) throw new Error(message) }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const directory = process.env.SMOKE_ARTIFACT_DIR
fs.mkdirSync(directory, { recursive: true })
;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const read = async endpoint => {
    const response = await fetch(process.env.SMOKE_BASE + endpoint)
    assert(response.ok, 'Read failed: ' + endpoint)
    return (await response.json()).data
  }
  try {
    await page.goto(process.env.SMOKE_UI)
    await page.getByLabel('Describe your goal', { exact: true }).fill('Preserve this draft without planning or execution.')
    await page.getByRole('button', { name: 'Save goal', exact: true }).click()
    await page.getByText('Saved draft', { exact: true }).waitFor()
    const drafts = await read('/api/goals')
    assert(drafts.length === 1 && !drafts[0].planning, 'Default submission acquired planning authority')
    await page.locator('.goal-planning-controls > summary').click()
    await page.getByLabel('Maximum new goals').fill('2')
    await page.getByRole('button', { name: 'Allow bounded planning', exact: true }).click()
    await page.getByText(/0 of 2 goal permissions used/).waitFor()
    assert((await read('/api/tasks/' + drafts[0].taskId)).status === 'queued', 'Existing draft was backfilled')
    await page.getByRole('button', { name: 'New conversation', exact: true }).click()
    const requestText = 'Prepare the Level 1 trapeze prefab quality-control checklist. Include quantities, conductors, mounts, tagging and code addenda; flag missing schedules and field evidence.'
    await page.getByLabel('Describe your goal', { exact: true }).fill(requestText)
    let lost = false
    await page.route('**/api/goals', async route => {
      if (route.request().method() === 'POST' && !lost) { lost = true; await route.fetch(); await route.abort('failed') } else await route.continue()
    })
    await page.getByRole('button', { name: 'Send for bounded planning', exact: true }).click()
    await page.getByText('Submission could not be confirmed', { exact: true }).waitFor()
    await page.reload()
    await page.getByRole('button', { name: 'Retry same request', exact: true }).click()
    await page.locator('.draft-badge').filter({ hasText: 'Awaiting your review' }).waitFor({ timeout: 30000 })
    await page.unroute('**/api/goals')
    await page.getByRole('button', { name: 'Enable local result review', exact: true }).click()
    await page.getByRole('heading', { name: 'Saved planning output', exact: true }).waitFor()
    assert(await page.locator('.home-saved-output .result-recommendations li').count() === 5, 'Home omitted recommendations')
    for (const width of [1920, 1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1100 })
      await page.evaluate(() => window.scrollTo(0, 0))
      await sleep(80)
      assert(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), 'Home overflow at ' + width)
      await page.screenshot({ path: path.join(directory, 'home-planning-' + width + '.png'), fullPage: true })
    }
    await page.setViewportSize({ width: 1440, height: 1100 })
    await page.getByRole('link', { name: 'Review this output in Inbox' }).click()
    const card = page.locator('.result-review-card')
    await card.getByLabel('Review note', { exact: true }).fill('Reviewed the five recommendations and the missing project evidence.')
    await card.getByRole('button', { name: 'Accept result', exact: true }).click()
    await page.getByRole('heading', { name: 'Result accepted', exact: true }).waitFor()
    await page.getByRole('link', { name: 'View accepted output in Library' }).click()
    await page.getByRole('button', { name: 'Preview output' }).click()
    await page.locator('.result-recommendations li').nth(4).waitFor()
    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download JSON' }).click()
    const download = await downloading
    const outputPath = path.join(directory, 'home-accepted-output.json')
    await download.saveAs(outputPath)
    assert(JSON.parse(fs.readFileSync(outputPath, 'utf8')).requiresHumanReview === false, 'Operator review rewrote the original model flag')
    const goals = await read('/api/goals')
    assert(goals.length === 2, 'Lost acknowledgement duplicated the goal')
    const goal = goals.find(item => item.messages[0].content === requestText)
    assert(goal && goal.planning, 'Planned goal scope missing')
    const task = await read('/api/tasks/' + goal.taskId)
    assert(task.status === 'completed' && task.artifactIds.length === 1, 'Accepted task/artifact missing')
    assert((await read('/api/tasks/' + drafts[0].taskId)).status === 'queued', 'Draft execution was implicitly enabled')
    assert(errors.length === 0, 'Browser errors: ' + errors.join('; '))
    const report = { goalId: goal.id, taskId: task.id, runId: goal.planning.runId, status: task.status, untouchedDraft: drafts[0].id, originalModelReviewFlag: false, errors, inference: 'one deterministic HTTP fixture request; no manual worker selection or action API calls' }
    fs.writeFileSync(path.join(directory, 'goal-planning-acceptance.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
