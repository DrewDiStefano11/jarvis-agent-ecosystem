/* Real API/browser workforce acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-mission-control-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'workforce.db').replaceAll('\\', '/')}`,
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
    const errors = []
    const failures = []
    const writes = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('net::ERR_FAILED')) errors.push(message.text()) })
    page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })
    page.on('request', request => { if (['POST', 'PATCH'].includes(request.method())) writes.push(new URL(request.url()).pathname) })
    await page.goto(ui)
    await page.getByRole('heading', { name: 'Mission Control', exact: true }).waitFor()
    await page.locator('.status-connected').waitFor()
    assert.equal(await page.title(), 'Overview · Jarvis')
    assert.equal(await page.getByText('Select an identity to inspect runtime work', { exact: true }).count(), 1)
    assert.equal(await page.getByText('fixture 18%', { exact: true }).isVisible(), false)
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true })
    for (const [label, width, height] of [['desktop',1440,1000], ['widescreen',1920,1080], ['laptop',1024,768], ['mobile',390,844], ['small-mobile',320,740]]) {
      await page.setViewportSize({ width, height })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label} overflows`)
      await page.screenshot({ path:path.join(output, `overview-${label}.png`), fullPage:true })
    }
    await page.getByRole('button', { name:'More navigation', exact:true }).click()
    const mobile = page.getByRole('navigation', { name:'Mobile primary', exact:true })
    await page.keyboard.press('Tab')
    assert.equal(await mobile.getByRole('link', { name:'Agents', exact:true }).evaluate(element => element === document.activeElement), true)
    await mobile.getByRole('link', { name:'System', exact:true }).click()
    await page.getByRole('heading', { name:'System', exact:true }).waitFor()
    assert.equal(await page.getByRole('button', { name:'More navigation', exact:true }).getAttribute('aria-expanded'), 'false')
    await page.getByRole('button', { name:'More navigation', exact:true }).click()
    await mobile.getByRole('link', { name:'Office', exact:true }).click()
    await page.getByRole('heading', { name:'Operations floor', exact:true }).waitFor()
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Office overflows on mobile')
    await mobile.getByRole('link', { name:'Overview', exact:true }).click()
    await page.setViewportSize({ width:1440, height:1000 })
    await page.getByRole('button', { name:'Collapse sidebar', exact:true }).click()
    assert.equal(await nav.getByRole('link', { name:'Office', exact:true }).getAttribute('title'), 'Office')
    await page.getByRole('button', { name:'Expand sidebar', exact:true }).click()
    await page.keyboard.press('Tab')
    await page.getByRole('link', { name:'+ New task', exact:true }).click()
    await page.getByLabel('Title', { exact:true }).fill('Mission Control acceptance task')
    await page.getByLabel('Description', { exact:true }).fill('Verify a durable operator request without queueing execution.')
    const accepted = page.waitForResponse(response => response.url().endsWith('/api/tasks') && response.request().method() === 'POST')
    await page.getByRole('button', { name:'Create task', exact:true }).click()
    const task = (await (await accepted).json()).data
    assert.ok(task.id)
    await page.getByText('Task created and queued.', { exact:false }).waitFor()
    await page.goBack()
    await page.getByLabel('Title', { exact:true }).waitFor()
    await page.goForward()
    await page.getByLabel('Title', { exact:true }).waitFor({ state:'hidden' })
    await nav.getByRole('link', { name:'Overview', exact:true }).click()
    await page.getByRole('button', { name:'Inspect Mission Control acceptance task', exact:true }).click()
    await page.getByRole('dialog', { name:'Mission Control acceptance task', exact:true }).waitFor()
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('dialog').count(), 0)
    assert.equal(await page.getByRole('button', { name:'Inspect Mission Control acceptance task', exact:true }).evaluate(element => element === document.activeElement), true)
    await page.getByRole('link', { name:/Review .* pending approval records/ }).click()
    await page.getByRole('heading', { name:'Approval inbox', exact:true }).waitFor()
    await nav.getByRole('link', { name:'Overview', exact:true }).click()
    page.once('dialog', dialog => dialog.dismiss())
    const beforeStop = writes.length
    await page.getByRole('button', { name:'Emergency stop', exact:true }).click()
    assert.equal(writes.length, beforeStop, 'Declining confirmation must not write')
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name:'Emergency stop', exact:true }).click()
    await page.getByRole('link', { name:/Emergency stop is active/ }).waitFor()
    await page.screenshot({ path:path.join(output,'emergency-attention.png'),fullPage:true })
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name:'Resume system', exact:true }).click()
    await page.getByRole('button', { name:'Emergency stop', exact:true }).waitFor()
    // Disconnect the browser from the real API: retain useful data, disclose stale state,
    // then reconnect through the existing AppStore socket/HTTP fallback.
    await page.context().setOffline(true)
    await page.getByText('Data may be stale', { exact:true }).waitFor()
    await page.getByRole('button', { name:'Inspect Mission Control acceptance task', exact:true }).waitFor()
    await page.screenshot({ path:path.join(output,'offline-last-known.png'),fullPage:true })
    await page.context().setOffline(false)

    await page.getByText('Data may be stale', { exact:true }).waitFor({ state:'hidden', timeout:15000 })
    const persisted = (await (await page.request.get(`${base}/api/tasks/${task.id}`)).json()).data
    assert.equal(persisted.status, 'queued')
    assert.ok(!writes.some(route => route.includes('/agent-runtime/commands')), 'Overview must not queue inference')
    const relevant = errors.filter(error => !/net::ERR_INTERNET_DISCONNECTED|WebSocket.*(failed|closed)|net::ERR_NETWORK_CHANGED/.test(error))
    assert.deepEqual(relevant, [], 'Unexpected browser errors')
    assert.deepEqual(failures, [], 'HTTP failures')
    console.log(`PASS: Mission Control real-API task creation/details, sidebar/mobile navigation, approvals, confirmed system controls, offline/reconnect, and five viewports. Evidence: ${output}`)
  } catch (error) {
    if (page) await page.screenshot({ path:path.join(output,'failure.png'),fullPage:true }).catch(() => {})
    console.error(`Evidence: ${output}`)
    throw error
  } finally {
    if (browser) await browser.close()
    for (const child of processes.reverse()) await stop(child)
  }
})().catch(error => { console.error(error); process.exitCode=1 })
