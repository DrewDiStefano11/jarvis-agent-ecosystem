/* Invoked by smoke-workspace-tools.py with an isolated API, worker and workspace. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(path.join(process.env.SMOKE_WEB, 'node_modules/playwright'))
const base = process.env.SMOKE_BASE
const output = process.env.SMOKE_ARTIFACT_DIR
const headers = { 'X-Jarvis-Actor-Id': process.env.SMOKE_ACTOR }
const timeout = Number(process.env.SMOKE_TIMEOUT_MS)
const expectedSteps = [
  ['workspace.list', 'inputs'], ['workspace.read', 'inputs/brief.txt'], ['workspace.report', 'reports/plan.md'],
]
const marker = 'WORKSPACE_ACCEPTANCE_20260905'
const title = `Workspace acceptance (${process.env.SMOKE_INFERENCE})`
const receiptFile = path.join(output, 'receipt.json')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  page.setDefaultTimeout(15000)
  const errors = []
  const failures = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })
  const read = async route => {
    const response = await page.request.get(`${base}${route}`, { headers })
    assert.equal(response.status(), 200, `${route}: ${await response.text()}`)
    return (await response.json()).data
  }
  const completed = async (route, pick = value => value) => {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      const value = pick(await read(route))
      if (value?.stage === 'completed') return value
      if (value && ['failed', 'paused', 'human_review_required'].includes(value.stage)) {
        fs.writeFileSync(path.join(output, 'incomplete-execution.json'), JSON.stringify(value, null, 2))
        throw Error(`Execution stopped: ${value.stage} (${value.failureCode}); see incomplete-execution.json`)
      }
      await new Promise(resolve => setTimeout(resolve, 500))
    }
    throw Error(`No durable completion before timeout: ${route}`)
  }
  const verifyArtifact = async receipt => {
    const run = await read(`/api/tool-executions/${receipt.run.executionId}`)
    assert.equal(run.stage, 'completed')
    assert.equal(run.sourceExecutionId, receipt.model.executionId)
    assert.equal(run.planHash, receipt.model.resultHash)
    assert.deepEqual(run.steps.map(step => [step.tool, step.path]), expectedSteps)
    assert.ok(run.steps.every(step => step.status === 'completed'))
    assert.ok(run.steps.every(step => !step.failureCode))
    assert.deepEqual(run.steps[0].observation.entries, ['brief.txt'])
    assert.equal(run.steps[1].observation.content, `${marker}: Three local projects need a concise status report.\n`)
    assert.equal(run.artifacts.length, 1)
    const artifact = await read(`/api/tool-artifacts/${run.artifacts[0].artifactId}`)
    assert.equal(artifact.content, receipt.model.result.steps[2].content)
    assert.equal(artifact.relativePath, 'reports/plan.md')
    assert.equal(artifact.executionId, run.executionId)
    assert.equal(artifact.taskId, run.taskId)
    assert.equal(artifact.byteCount, Buffer.byteLength(artifact.content))
    const source = await read(`/api/tasks/${receipt.task.id}`)
    const child = await read(`/api/tasks/${run.taskId}`)
    assert.equal(source.status, 'completed')
    assert.equal(child.status, 'completed')
    return { run, artifact, source, child }
  }
  try {
    if (process.argv[2] === 'verify') {
      const receipt = JSON.parse(fs.readFileSync(receiptFile, 'utf8'))
      const persisted = await verifyArtifact(receipt)
      // Immutable completed records must survive an API process restart exactly.
      assert.deepEqual(persisted.run, receipt.run)
      assert.deepEqual(persisted.artifact, receipt.artifact)
      const models = await read(`/api/model-executions?taskId=${receipt.task.id}`)
      assert.deepEqual(models, [receipt.model])
      const tools = await read(`/api/tool-executions?taskId=${receipt.task.id}`)
      assert.equal(tools.length, 1)
      await page.goto(`${process.env.SMOKE_UI}/lab`)
      await page.getByRole('heading', { name: title, exact: true }).waitFor()
      await page.getByRole('article').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).getByRole('link', { name: 'Open objective workspace', exact: true }).click()
      await page.getByLabel('Act as local identity').selectOption(process.env.SMOKE_ACTOR)
      await page.getByRole('button', { name: 'Read reports/plan.md', exact: true }).click()
      await page.locator('pre:visible').filter({ hasText: receipt.artifact.content }).waitFor()
      await page.screenshot({ path: path.join(output, 'workspace-after-restart.png'), fullPage: true })
      console.log('PASS: source plan, child execution, steps and artifact remain exact after API restart; Business Lab report reopens.')
    } else {
      await page.goto(`${process.env.SMOKE_UI}/lab`)
      await page.getByRole('heading', { name: 'Business Lab', exact: true }).waitFor()
      assert.equal(await page.title(), 'Business Lab · Jarvis')
      await page.getByRole('button', { name: 'New objective', exact: true }).click()
      await page.getByLabel('Title', { exact: true }).fill(title)
      await page.getByLabel('Description', { exact: true }).fill(
        `Prepare a workspace action plan from these supplied facts: ${marker}: Three local projects need a concise status report. ` +
        'Return exactly three fixed steps: workspace.list path inputs; workspace.read path inputs/brief.txt; workspace.report path reports/plan.md. ' +
        `The report content must include ${marker} and a concise project status note based only on these supplied facts. ` +
        'Use no other tools or paths. No replacements or expectedContentHash. Do not claim to know file contents before reading them. ' +
        'Return workspace_plan_json_v1 with the required planning review fields, requiresHumanReview false and these three steps. ' +
        'The operator will separately review and authorize the exact file content before execution.')
      const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/tasks' && response.request().method() === 'POST')
      await page.getByRole('button', { name: 'Create task', exact: true }).click()
      const task = (await (await created).json()).data
      assert.equal(task.projectId, 'business-lab')
      assert.equal(task.status, 'queued')
      await page.getByRole('link', { name: 'Open objective workspace', exact: true }).click()
      await page.getByRole('heading', { name: 'Planning workspace', exact: true }).waitFor()
      assert.equal(await page.getByLabel('Plan type').inputValue(), 'workspace')
      assert.equal(await page.getByLabel('Task and history').inputValue(), task.id)
      await page.getByRole('button', { name: 'Prepare local planner for this task', exact: true }).click()
      await page.getByText('Local planner prepared for this task. Queue the plan when ready.', { exact: true }).waitFor()
      assert.equal(await page.getByLabel('Act as local identity').inputValue(), process.env.SMOKE_ACTOR)
      await page.getByRole('button', { name: 'Queue local plan', exact: true }).click()
      await completed(`/api/model-executions?taskId=${task.id}`, value => value[0])
      await page.getByRole('heading', { name: 'Review workspace actions', exact: true }).waitFor()
      const models = await read(`/api/model-executions?taskId=${task.id}`)
      assert.equal(models.length, 1)
      const model = models[0]
      fs.writeFileSync(path.join(output, 'model-proposal.json'), JSON.stringify(model, null, 2))
      assert.equal(model.stage, 'completed')
      assert.equal(model.model, process.env.SMOKE_MODEL)
      assert.ok(model.resultHash)
      // Review the real persisted plan before automating the operator's approval.
      assert.deepEqual(model.result.steps.map(step => [step.tool, step.path]), expectedSteps)
      assert.ok(model.result.steps.every(step => !step.expectedContentHash))
      assert.ok(model.result.steps[2].content.includes(marker))
      assert.ok(Buffer.byteLength(model.result.steps[2].content) < 65536)
      assert.deepEqual(await read(`/api/tool-executions?taskId=${task.id}`), [])
      for (const content of await page.getByText('Inspect exact file content', { exact: true }).all()) await content.click()
      await page.screenshot({ path: path.join(output, 'workspace-plan-review.png'), fullPage: true })
      await page.getByLabel('Authorized workspace').selectOption('lab')
      const authorize = page.getByRole('button', { name: 'Authorize workspace execution', exact: true })
      assert.equal(await authorize.isEnabled(), false)
      await page.getByRole('checkbox', { name: 'I reviewed the exact actions, file contents and workspace scope.', exact: true }).check()
      const accepted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/tool-executions/authorize' && response.request().method() === 'POST')
      await authorize.click()
      const authorization = await accepted
      assert.ok(authorization.ok(), await authorization.text())
      const run = (await authorization.json()).data
      await completed(`/api/tool-executions/${run.executionId}`)
      await page.getByRole('button', { name: 'Read reports/plan.md', exact: true }).waitFor()
      const receipt = { task, model, run }
      const persisted = await verifyArtifact(receipt)
      Object.assign(receipt, persisted)
      await page.getByRole('button', { name: 'Read reports/plan.md', exact: true }).click()
      await page.locator('pre:visible').filter({ hasText: receipt.artifact.content }).last().waitFor()
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
      await page.screenshot({ path: path.join(output, 'workspace-artifact-desktop.png'), fullPage: true })
      await page.setViewportSize({ width: 390, height: 844 })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Mobile document overflows horizontally')
      await page.screenshot({ path: path.join(output, 'workspace-artifact-mobile.png'), fullPage: true })
      fs.writeFileSync(receiptFile, JSON.stringify(receipt, null, 2))
      console.log(`PASS: Business Lab objective → ${process.env.SMOKE_INFERENCE} model proposal → exact UI review → authorized real list/read/report → durable artifact (${receipt.artifact.contentHash}).`)
    }
    assert.deepEqual(errors, [], 'Browser console/page errors')
    assert.deepEqual(failures, [], 'HTTP errors')
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'workspace-failure.png'), fullPage: true }).catch(() => {})
    fs.writeFileSync(path.join(output, 'browser-errors.json'), JSON.stringify({ errors, failures }, null, 2))
    throw error
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
