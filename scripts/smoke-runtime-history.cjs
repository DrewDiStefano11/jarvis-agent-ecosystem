/* Real authorized runtime history/browser acceptance. Uses its own database and loopback ports. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const { spawn, execFileSync } = require('node:child_process')
const root = path.resolve(__dirname, '..')
const web = path.join(root, 'apps/web')
const { chromium } = require(path.join(web, 'node_modules/playwright'))
const python = process.env.JARVIS_SMOKE_PYTHON || path.join(root, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-runtime-history-'))
const processes = []
const freePort = () => new Promise(resolve => {
  const server = net.createServer()
  server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)) })
})
function start(executable, args, cwd, env, name) {
  const log = fs.openSync(path.join(output, `${name}.log`), 'a')
  const child = spawn(executable, args, { cwd, env, windowsHide: true, stdio: ['ignore', log, log] })
  fs.closeSync(log)
  child.on('error', error => { child.startError = error })
  processes.push(child)
  return child
}
async function ready(url, child) {
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    if (child.startError) throw child.startError
    if (child.exitCode !== null) throw Error(`Service exited with ${child.exitCode}; see ${output}`)
    try { if ((await fetch(url)).ok) return } catch { /* still starting */ }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  throw Error(`Service did not become ready: ${url}; see ${output}`)
}
async function stop(child) {
  if (child.exitCode !== null || child.startError) return
  // Stop only this harness's still-running child tree (Windows venv redirectors included).
  const done = new Promise(resolve => child.once('exit', resolve))
  if (process.platform === 'win32') execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
  else child.kill('SIGTERM')
  await done
}

;(async () => {
  let browser
  let page
  const apiPort = await freePort()
  const webPort = await freePort()
  const base = `http://127.0.0.1:${apiPort}`
  const ui = `http://127.0.0.1:${webPort}`
  // Set isolation BEFORE launching any process that imports app.main.
  const env = { ...process.env,
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'runtime-history.db').replaceAll('\\', '/')}`,
    JARVIS_DATA_DIRECTORY: output, PYTHONPATH: path.join(root, 'apps/api'),
    JARVIS_AUTONOMOUS_WORKER_ENABLED: 'false', JARVIS_MODEL_EXECUTION_MODE: 'disabled',
    JARVIS_MODEL_OLLAMA_ENABLED: 'false', JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED: 'false',
    JARVIS_MODEL_ALLOW_REMOTE: 'false', JARVIS_AUTO_MIGRATE: 'true', WEB_ORIGIN: ui,
  }
  const startApi = () => start(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], path.join(root, 'apps/api'), env, 'api')
  let api = startApi()
  try {
    await ready(`${base}/api/health`, api)
    const vite = start(process.execPath, [path.join(web, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], web,
      { ...env, VITE_API_BASE_URL: base, VITE_WS_URL: `ws://127.0.0.1:${apiPort}/ws/events` }, 'web')
    await ready(ui, vite)
    browser = await chromium.launch({ headless: true })
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    page.setDefaultTimeout(10000)
    let revocation = false
    let deniedReads = 0
    const errors = []
    const failures = []
    const writes = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('net::ERR_FAILED') && !(revocation && message.text().includes('403'))) errors.push(message.text()) })
    page.on('response', response => { if (revocation && response.status() === 403 && new URL(response.url()).pathname === '/api/agent-runtime/runs') deniedReads++; else if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })
    page.on('request', request => { if (['POST', 'PATCH'].includes(request.method())) writes.push(new URL(request.url()).pathname) })
    const request = async (url, body, actorId) => {
      const response = await fetch(base + url, { headers: { 'Content-Type': 'application/json', ...(actorId ? { 'X-Jarvis-Actor-Id': actorId } : {}) }, ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }) })
      const result = await response.json()
      assert.equal(response.ok, true, JSON.stringify(result))
      return result.data
    }
    const task = await request('/api/tasks', { title: 'Runtime history acceptance', description: 'Inspect actual authorized native snapshots in an isolated database.' })
    const setup = JSON.parse(execFileSync(python, ['-m', 'app.autonomous_worker.setup', '--task-id', task.id, '--actor-key', 'history-operator'], { cwd: path.join(root, 'apps/api'), env, encoding: 'utf8' }))
    const actorId = setup.actorId
    for (let index = 0; index < 205; index++) {
      const timestamp = new Date(Date.now() + index).toISOString()
      await request('/api/agent-runtime/commands', { command_type: 'create', command_id: `history-create-${index}`, timestamp, actor_reference: actorId,
        specification: { run_id: `run-history-${String(index).padStart(3, '0')}`, task_id: task.id, agent_id: actorId, requested_operation: `Inspect native record ${index}`, created_at: timestamp, idempotency_key: `history-${index}`, maximum_permitted_attempts: 3 } }, actorId)
    }
    const first = await request('/api/agent-runtime/runs?limit=50', undefined, actorId)
    assert.equal(first.items.length, 50); assert.equal(first.total_count, 50); assert.equal(first.next_offset, 50)
    await page.goto(`${ui}/runtime`)
    await page.getByRole('heading', { name: 'Planning workspace', exact: true }).waitFor()
    await page.getByLabel('Act as local identity').selectOption(actorId)
    const history = page.getByRole('region', { name: 'Runtime history' })
    await history.getByText('50 matching of 50 loaded runs', { exact: true }).waitFor()
    for (let count = 1; count <= 3; count++) {
      await history.getByRole('button', { name: 'Load next page' }).click()
      await history.getByText(`${50 * (count + 1)} matching of ${50 * (count + 1)} loaded runs`, { exact: true }).waitFor()
    }
    assert.equal(await history.getByRole('button', { name: 'Load next page' }).count(), 0)
    await history.getByText(/History is bounded to four pages/).waitFor()
    await history.getByLabel('Search runs').fill('record 199')
    await history.getByText('1 matching of 200 loaded runs', { exact: true }).waitFor()
    await history.locator('summary').click()
    await history.getByText('Event sequence', { exact: true }).waitFor()
    await history.getByLabel('Search runs').fill('')
    await history.getByRole('button', { name: 'Refresh runtime' }).click()
    await history.getByText('50 matching of 50 loaded runs', { exact: true }).waitFor()
    for (const width of [1920, 1536, 1440, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 })
      await history.evaluate(element => window.scrollTo(0, element.getBoundingClientRect().top + scrollY - 80))
      assert.equal(await history.evaluate(element => element.scrollWidth <= element.clientWidth && element.getBoundingClientRect().right <= innerWidth), true, `History overflow at ${width}`)
      // The merged-main mobile navigation has a pre-existing 320px overflow.
      // Its replacement is isolated in Mission Control shell PR #75.
      await page.screenshot({ path: path.join(output, `history-${width}.png`) })
    }
    await history.getByRole('button', { name: 'Finished', exact: true }).click()
    await history.getByText('No loaded runs match these filters.', { exact: true }).waitFor()
    await history.getByRole('button', { name: 'All', exact: true }).click()
    await history.getByRole('button', { name: 'Open task', exact: true }).first().click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('button', { name: /Close/ }).click()
    revocation = true
    await request(`/api/identity/agents/${actorId}/suspend`, {})
    await history.getByRole('button', { name: 'Refresh runtime' }).click()
    await history.getByRole('alert').waitFor()
    assert.equal(await history.getByRole('table').count(), 0)
    assert.equal(await history.getByRole('button', { name: 'Load next page' }).count(), 0)
    assert.ok(deniedReads > 0, 'Actual inactive-identity read was denied')
    await page.screenshot({ path: path.join(output, 'history-revoked.png') })
    assert.deepEqual(errors, []); assert.deepEqual(failures, []); assert.deepEqual(writes, [])
    console.log(`PASS authorized runtime history: ${output}`)
  } catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    if (browser) await browser.close()
    for (const child of processes.toReversed()) await stop(child)
  }
})().catch(error => { console.error(error); console.error(`Diagnostics: ${output}`); process.exitCode = 1 })
