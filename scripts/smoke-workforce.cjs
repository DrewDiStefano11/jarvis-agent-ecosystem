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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-workforce-'))
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
    await page.goto(`${ui}/agents`)
    await page.getByRole('heading', { name: 'Registered identities', exact: true }).waitFor()
    assert.equal(await page.title(), 'Agents · Jarvis')
    const register = async (name, key) => {
      await page.getByRole('button', { name: 'Register identity', exact: true }).click()
      const form = page.getByRole('form', { name: 'Register identity', exact: true })
      await form.getByLabel('Display name', { exact: true }).fill(name)
      await form.getByLabel('Stable key', { exact: false }).fill(key)
      await form.getByLabel('Description', { exact: true }).fill('Registered through the actual local identity API.')
      await form.getByRole('button', { name: 'Register provisioned identity', exact: true }).click()
      const card = page.getByRole('article', { name: `Identity ${name}`, exact: true })
      await card.waitFor()
      return card
    }
    let card = await register('Browser researcher', 'browser-researcher')
    await card.locator('.status-provisioned').waitFor()
    assert.equal(await card.getByRole('link', { name: 'Assign work in Planning' }).count(), 0)
    await card.getByRole('button', { name: 'Activate identity', exact: true }).click()
    await card.locator('.status-active').waitFor()
    await card.getByRole('button', { name: 'Edit profile', exact: true }).click()
    await card.getByLabel('Display name', { exact: true }).fill('Senior researcher')
    await card.getByLabel('Description', { exact: true }).fill('This profile remains after API restart.')
    await card.getByRole('button', { name: 'Save profile', exact: true }).click()
    card = page.getByRole('article', { name: 'Identity Senior researcher', exact: true })
    await card.getByText('Profile saved.', { exact: true }).waitFor()
    await card.getByRole('button', { name: 'Suspend identity', exact: true }).click()
    await card.locator('.status-suspended').waitFor()
    await card.getByRole('button', { name: 'Reactivate identity', exact: true }).click()
    await card.locator('.status-active').waitFor()
    await card.getByRole('button', { name: 'Disable identity', exact: true }).click()
    await card.getByRole('button', { name: 'Enable identity', exact: true }).waitFor()
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true })
    await nav.getByRole('link', { name: 'Planning', exact: true }).click()
    assert.equal(await page.getByLabel('Target agent').getByRole('option', { name: 'Senior researcher' }).count(), 0)
    await nav.getByRole('link', { name: 'Agents', exact: true }).click()
    await card.getByRole('button', { name: 'Enable identity', exact: true }).click()
    await card.getByRole('link', { name: 'Assign work in Planning', exact: true }).click()
    await page.getByLabel('Target agent').selectOption({ label: 'Senior researcher' })
    assert.ok(await page.getByLabel('Target agent').inputValue())
    await nav.getByRole('link', { name: 'Agents', exact: true }).click()
    // The API accepts the registration, but the browser loses its acknowledgement.
    let lost = false
    await page.route(`${base}/api/identity/agents`, async route => {
      if (route.request().method() === 'POST' && !lost) {
        lost = true; await route.fetch(); await route.abort('failed')
      } else await route.continue()
    })
    await register('Recovered researcher', 'recovered-researcher')
    await page.getByText(/Found the existing registration for Recovered researcher/).waitFor()
    await page.unroute(`${base}/api/identity/agents`)
    const identities = (await (await page.request.get(`${base}/api/identity/agents`)).json()).data
    assert.equal(identities.length, 2)
    assert.equal(writes.filter(route => route === '/api/identity/agents').length, 2)
    assert.ok(writes.every(route => /^\/api\/identity\/agents(?:\/[^/]+(?:\/(?:activate|suspend))?)?$/.test(route)), 'Workforce controls unexpectedly wrote outside identity management')
    const identity = identities.find(row => row.stable_key === 'browser-researcher')
    const permission = await page.request.post(`${base}/api/identity/permissions/evaluate`, { data: { actor_agent_id: identity.id, permission_key: 'runtime.create' } })
    assert.equal(permission.status(), 200)
    assert.equal((await permission.json()).data.allowed, false)
    await page.screenshot({ path: path.join(output, 'workforce-desktop.png'), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.evaluate(() => window.scrollTo(0, 0))
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Mobile page overflows horizontally')
    await page.screenshot({ path: path.join(output, 'workforce-mobile.png'), fullPage: true })
    assert.deepEqual(errors, [], 'Browser console/page errors')
    assert.deepEqual(failures, [], 'HTTP errors')
    await page.close()
    await stop(api)
    api = startApi()
    await ready(`${base}/api/health`, api)
    const reopened = await browser.newPage()
    await reopened.goto(`${ui}/agents`)
    await reopened.getByRole('article', { name: 'Identity Senior researcher', exact: true }).getByText('This profile remains after API restart.', { exact: true }).waitFor()
    const persisted = (await (await reopened.request.get(`${base}/api/identity/agents/${identity.id}`)).json()).data
    assert.equal(persisted.lifecycle_state, 'active')
    assert.equal(persisted.is_enabled, true)
    console.log(`PASS: actual API registration, activation, profile edits, suspend/reactivate, enablement, Planning target sharing, lost-ack recovery, no permission grants, API restart, desktop/mobile. Evidence: ${output}`)
  } catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    if (browser) await browser.close()
    for (const child of processes.toReversed()) await stop(child)
  }
})().catch(error => { console.error(error); console.error(`Diagnostics: ${output}`); process.exitCode = 1 })
