/* Real isolated API + worker + browser; only model transport is a deterministic fixture. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const { spawn, execFileSync } = require('node:child_process')
const { pathToFileURL } = require('node:url')
const root = path.resolve(__dirname, '..')
const web = path.join(root, 'apps/web')
const apiDir = path.join(root, 'apps/api')
const python = process.env.JARVIS_SMOKE_PYTHON || path.join(apiDir, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const { chromium } = require(path.join(web, 'node_modules/playwright'))
const ts = require(path.join(web, 'node_modules/typescript'))
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-decomposition-'))
const children = []
const calls = []
const freePort = () => new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)) }) })
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
function start(command, args, cwd, env, name) {
  const log = fs.openSync(path.join(output, name + '.log'), 'a')
  const child = spawn(command, args, { cwd, env, windowsHide: true, stdio: ['ignore', log, log] })
  fs.closeSync(log); children.push(child)
  child.on('error', error => { child.startError = error })
  return child
}
async function ready(url, child) {
  for (let i = 0; i < 300; i++) {
    if (child.startError) throw child.startError
    if (child.exitCode !== null) throw Error(`Process exited: ${url}; logs: ${output}`)
    try { if ((await fetch(url)).ok) return } catch { /* startup */ }
    await pause(100)
  }
  throw Error(`Startup timeout: ${url}; logs: ${output}`)
}
async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise(resolve => child.once('exit', resolve))
  child.kill(); await exited
}
const subtask = (key, capability, dependsOn = []) => ({ key, title: `Produce ${key} evidence`, description: 'Compare concrete alternatives and include source provenance.', requiredCapabilities: [capability], dependsOn,
  preferredAgentId: null, deliverable: 'A structured comparison with source references', outputType: key === 'prototype' ? 'code_patch' : 'analysis', completionCriteria: ['Include five observable comparisons or passing validation cases.'] })
const model = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json')
  if (req.method === 'GET') { res.end(JSON.stringify({ models: [{ name: 'fixture-model' }] })); return }
  let body = ''; for await (const chunk of req) body += chunk
  const payload = JSON.parse(body)
  const purpose = payload.format?.title
  calls.push({ purpose, payload })
  let result
  if (purpose === 'RequiredCapabilitiesResult') result = { required: ['research.market', 'business.financial-analysis', 'software.backend'], optional: [], reasoning_summary: 'Multidisciplinary acceptance objective' }
  else if (purpose === 'DecompositionProposal') {
    const blocked = JSON.stringify(payload.messages).includes('BLOCKED ACCEPTANCE')
    result = { objectiveSummary: 'Assess an AI clipping business and produce a bounded prototype.', subtasks: [subtask('research', 'research.market'), subtask('finance', 'business.financial-analysis', ['research']), subtask('prototype', 'software.backend', ['research'])] }
    if (blocked) result.subtasks.push(subtask('security', 'software.security', ['prototype']))
  } else result = { schemaVersion: '1.0', summary: 'The validated specialist plan is ready for future coordination.', analysis: 'This is planning acceptance; no specialist work has executed.', recommendations: [{ title: 'Inspect the work plan', description: 'Review deliverables and dependency inputs.', priority: 'high' }], risks: [{ title: 'Planned work only', description: 'Execution is deferred.', severity: 'low', mitigation: 'Coordinate in milestone 63.' }], assumptions: ['Deterministic transport fixture'], missingInformation: [], requiresHumanReview: false }
  res.end(JSON.stringify({ model: 'fixture-model', message: { role: 'assistant', content: JSON.stringify(result) }, done: true, done_reason: 'stop', prompt_eval_count: 20, eval_count: 40 }))
})

;(async () => {
  let browser
  await new Promise(resolve => model.listen(0, '127.0.0.1', resolve))
  try {
    const apiPort = await freePort(), webPort = await freePort()
    const base = `http://127.0.0.1:${apiPort}`, ui = `http://127.0.0.1:${webPort}`
    const db = `sqlite:///${path.join(output, 'acceptance.db').replaceAll('\\', '/')}`
    const env = { ...process.env, JARVIS_DATABASE_URL: db, JARVIS_DATA_DIRECTORY: output,
      JARVIS_AUTONOMOUS_WORKER_ENABLED: 'false', JARVIS_MODEL_EXECUTION_MODE: 'disabled', JARVIS_MODEL_OLLAMA_ENABLED: 'false', JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED: 'false', JARVIS_MODEL_ALLOW_REMOTE: 'false', JARVIS_TOOL_EXECUTION_ENABLED: 'false', JARVIS_AUTO_MIGRATE: 'true', WEB_ORIGIN: ui }
    execFileSync(python, [path.join(root, 'scripts/decomposition-fixture.py'), '--database-url', db], { cwd: apiDir, env, stdio: 'pipe' })
    Object.assign(env, { JARVIS_MODEL_EXECUTION_MODE: 'local_only', JARVIS_MODEL_OLLAMA_ENABLED: 'true', JARVIS_MODEL_OLLAMA_BASE_URL: `http://127.0.0.1:${model.address().port}`, JARVIS_MODEL_OLLAMA_MODEL: 'fixture-model', JARVIS_MODEL_PROVIDER_PRIORITY: 'ollama', JARVIS_AUTONOMOUS_WORKER_ACTOR_ID: 'jarvis', JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID: 'decomposition-smoke', JARVIS_AUTONOMOUS_WORKER_POLL_INTERVAL_MS: '100' })
    let api = start(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], apiDir, env, 'api')
    await ready(base + '/api/health', api)
    const request = async (url, data) => { const response = await fetch(base + url, { headers: { 'Content-Type': 'application/json', 'X-Jarvis-Actor-Id': 'jarvis' }, ...(data === undefined ? {} : { method: 'POST', body: JSON.stringify(data) }) }); const result = await response.json(); if (!response.ok) throw Error(JSON.stringify(result)); return result.data }
    // Use the actual frontend request construction, with only its base URL bound.
    for (const name of ['client', 'planning']) {
      const source = fs.readFileSync(path.join(web, 'src/api', name + '.ts'), 'utf8').replaceAll('import.meta.env.VITE_API_BASE_URL', JSON.stringify(base)).replaceAll('import.meta.env.VITE_WS_URL', 'undefined').replace("'./client'", "'./client.mjs'")
      fs.writeFileSync(path.join(output, name + '.mjs'), ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText)
    }
    const { newPlanningSubmission, submitPlanning } = await import(pathToFileURL(path.join(output, 'planning.mjs')))
    const task = await request('/api/tasks', { title: 'AI clipping business viability', description: 'Research sports and entertainment markets, evaluate economics and create a bounded prototype.' })
    execFileSync(python, ['-m', 'app.autonomous_worker.setup', '--task-id', task.id, '--actor-key', 'jarvis'], { cwd: apiDir, env, stdio: 'pipe' })
    const submission = newPlanningSubmission(task, 'jarvis', 'jarvis')
    await submitPlanning(submission)
    const graph = await request(`/api/tasks/${task.id}/decomposition`)
    assert.equal(graph.status, 'ready'); assert.equal(graph.subtasks.length, 3)
    assert.equal(new Set(graph.subtasks.map(n => n.assignedAgentId)).size, 3)
    assert.deepEqual(graph.subtasks.map(n => n.key), ['research', 'finance', 'prototype'])
    assert.equal((await request(`/api/tasks/${task.id}/decomposition`, {})).id, graph.id)
    await submitPlanning(submission)
    const worker = start(python, ['-m', 'app.autonomous_worker'], apiDir, { ...env, JARVIS_AUTONOMOUS_WORKER_ENABLED: 'true' }, 'worker')
    let executions = []
    for (let i = 0; i < 300; i++) {
      executions = await request(`/api/model-executions?taskId=${task.id}`)
      if (executions.some(e => e.stage === 'completed')) break
      if (executions.some(e => ['failed', 'human_review_required'].includes(e.stage))) throw Error(JSON.stringify(executions))
      await pause(100)
    }
    assert.equal(executions.length, 1); assert.equal(executions[0].stage, 'completed')
    await stop(worker)
    const blockedTask = await request('/api/tasks', { title: 'BLOCKED ACCEPTANCE', description: 'Evaluate the same business with security coverage.' })
    await request('/api/context/assemblies', { taskId: blockedTask.id, projectId: blockedTask.projectId ?? 'jarvis-agent-ecosystem', completionCriteria: 'Return a bounded specialist plan.' })
    const blocked = await request(`/api/tasks/${blockedTask.id}/decomposition`)
    assert.equal(blocked.status, 'needs_team_reselection')
    assert.deepEqual(blocked.issues[0].requiredCapabilities, ['software.security'])
    await stop(api)
    api = start(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], apiDir, env, 'api-restart')
    await ready(base + '/api/health', api)
    assert.deepEqual(await request(`/api/tasks/${task.id}/decomposition`), graph)
    const frontend = start('node', [path.join(web, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], web, { ...env, VITE_API_BASE_URL: base, VITE_WS_URL: `ws://127.0.0.1:${apiPort}/ws/events` }, 'web')
    await ready(ui, frontend)
    browser = await chromium.launch()
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    await page.goto(ui + '/tasks')
    await page.getByRole('button', { name: 'Open AI clipping business viability' }).click()
    const panel = page.getByRole('region', { name: 'Planned work' })
    await panel.getByText('Produce prototype evidence · blocked').waitFor()
    assert.match(await panel.innerText(), /Owner:.*Backend Engineer/i)
    assert.match(await panel.innerText(), /Depends on: Produce research evidence/)
    await page.screenshot({ path: path.join(output, 'planned-work.png'), fullPage: true })
    await page.reload()
    await page.getByRole('button', { name: 'Open AI clipping business viability' }).click()
    await page.getByText('Version 1 · ready', { exact: true }).waitFor()
    assert.equal(calls.filter(c => c.purpose === 'RequiredCapabilitiesResult').length, 2)
    assert.equal(calls.filter(c => c.purpose === 'DecompositionProposal').length, 2)
    assert.equal(calls.length, 5)
    assert(!JSON.stringify(calls).includes('EXTERNAL_PROMPT_NOT_FOR_DECOMPOSITION'))
    const plan = calls.find(c => !['RequiredCapabilitiesResult', 'DecompositionProposal'].includes(c.purpose))
    assert(JSON.stringify(plan).includes('VALIDATED PLANNED WORK'))
    console.log(JSON.stringify({ result: 'PASS', taskId: task.id, decompositionId: graph.id, subtasks: 3, owners: 3, restart: 'PASS', browserReload: 'PASS', blockedCase: blocked.status, calls: calls.map(c => c.purpose), evidence: output }))
  } finally {
    if (browser) await browser.close()
    for (const child of children.reverse()) await stop(child)
    await new Promise(resolve => model.close(resolve))
  }
})().catch(error => { console.error(error, `Evidence: ${output}`); process.exitCode = 1 })
