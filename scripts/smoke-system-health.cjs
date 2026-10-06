/* Real API/browser System health acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-system-health-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'system-health.db').replaceAll('\\', '/')}`,
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
    await page.goto(`${ui}/system`)
    await page.getByRole('heading', { name:'System', exact:true }).waitFor()
    await page.locator('.status-connected').waitFor()
    const controls = page.getByRole('region', { name:'System controls', exact:true })
    const health = page.getByRole('region', { name:'Health snapshot', exact:true })
    assert.equal(await page.title(), 'System · Jarvis')
    assert.equal(await page.getByText('fixture 18%', { exact:true }).count(), 0)
    assert.equal(await page.getByRole('button', { name:'Reset demo', exact:true }).isVisible(), false)
    for (const [label,width,height] of [['desktop',1440,1000],['widescreen',1920,1080],['laptop',1024,768],['mobile',390,844],['small-mobile',320,740]]) {
      await page.setViewportSize({ width,height })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label} overflow`)
      await page.evaluate(() => window.scrollTo(0,0))
      await page.screenshot({ path:path.join(output, `system-${label}.png`), fullPage:true })
    }
    await page.setViewportSize({ width:1440,height:1000 })
    const before = writes.length
    page.once('dialog', dialog => dialog.dismiss())
    await controls.getByRole('button', { name:'Emergency stop', exact:true }).click()
    assert.equal(writes.length, before)
    page.once('dialog', dialog => dialog.accept())
    await controls.getByRole('button', { name:'Emergency stop', exact:true }).click()
    await page.getByText('Emergency stop is active. Review interrupted work before resuming.', { exact:true }).waitFor()
    await page.getByText('Demonstration controls', { exact:true }).click()
    assert.equal(await page.getByRole('button', { name:'Reset demo', exact:true }).isEnabled(), false)
    await page.screenshot({ path:path.join(output,'system-emergency.png'),fullPage:true })
    await page.context().setOffline(true)
    await page.getByText(/Last-known data may be outdated/).waitFor()
    await page.getByRole('button', { name:'Refresh state', exact:true }).click()
    await health.getByText('Last-known backend status: healthy', { exact:true }).waitFor()
    await page.getByText('Emergency stop is active. Review interrupted work before resuming.', { exact:true }).waitFor()
    await page.screenshot({ path:path.join(output,'system-offline.png'),fullPage:true })
    await page.context().setOffline(false)
    await page.getByText(/Last-known data may be outdated/).waitFor({ state:'hidden', timeout:15000 })
    page.once('dialog', dialog => dialog.dismiss())
    const beforeResume = writes.length
    await controls.getByRole('button', { name:'Resume system', exact:true }).click()
    assert.equal(writes.length, beforeResume)
    page.once('dialog', dialog => dialog.accept())
    await controls.getByRole('button', { name:'Resume system', exact:true }).click()
    await controls.getByRole('button', { name:'Emergency stop', exact:true }).waitFor()
    const beforeReset = writes.length
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name:'Reset demo', exact:true }).click()
    assert.equal(writes.length, beforeReset)
    // A destructive reset can be accepted before its acknowledgement is lost.
    // Shared uncertainty must disable a second reset until post-failure refresh.
    await page.route(`${base}/api/simulator/reset`, async route => { await route.fetch(); await route.abort('failed') })
    const resetBeforeLost = writes.filter(route => route === '/api/simulator/reset').length
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name:'Reset demo', exact:true }).click()
    await page.getByText(/Request outcome could not be confirmed/).waitFor()
    assert.equal(await page.getByRole('button', { name:'Reset demo', exact:true }).isEnabled(), false)
    await page.getByRole('button', { name:'Reset demo', exact:true }).evaluate(button => button.click())
    assert.equal(writes.filter(route => route === '/api/simulator/reset').length, resetBeforeLost + 1)
    await page.unroute(`${base}/api/simulator/reset`)
    await page.getByRole('button', { name:'Refresh state', exact:true }).click()
    await page.getByText(/Last-known data may be outdated/).waitFor({ state:'hidden' })
    assert.equal(await page.getByRole('button', { name:'Reset demo', exact:true }).isEnabled(), true)
    // Accept server write but drop the response: do not invent acknowledgement or replay.
    let lost = false
    await page.route(`${base}/api/system/emergency-stop`, async route => {
      if (!lost) { lost = true; await route.fetch(); await route.abort('failed') }
      else await route.continue()
    })
    const countBeforeLost = writes.filter(route => route === '/api/system/emergency-stop').length
    page.once('dialog', dialog => dialog.accept())
    await controls.getByRole('button', { name:'Emergency stop', exact:true }).click()
    await page.getByText(/Request outcome could not be confirmed/).waitFor()
    assert.equal(writes.filter(route => route === '/api/system/emergency-stop').length, countBeforeLost + 1)
    await page.unroute(`${base}/api/system/emergency-stop`)
    await page.getByRole('button', { name:'Refresh state', exact:true }).click()
    await controls.getByRole('button', { name:'Resume system', exact:true }).waitFor()
    assert.equal(await page.getByRole('button', { name:'Reset demo', exact:true }).isEnabled(), false)
    await page.getByText('Technical details', { exact:true }).click()
    await page.getByText('Event session', { exact:true }).waitFor()
    await page.setViewportSize({ width:320,height:740 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Technical details overflow')
    await page.screenshot({ path:path.join(output,'system-technical-mobile.png'),fullPage:true })
    assert.deepEqual(errors.filter(error => !/net::ERR_INTERNET_DISCONNECTED|WebSocket.*(failed|closed)|net::ERR_NETWORK_CHANGED/.test(error)), [])
    assert.deepEqual(failures, [])
    assert(!writes.some(route => route.includes('/agent-runtime/commands')), 'No inference queued')
    console.log(`PASS: actual API health, five widths, confirmations, emergency/reset separation, offline preservation/HTTP refresh, lost-ack uncertainty without replay, and technical mobile details. Evidence: ${output}`)
  } catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path:path.join(output,'failure.png'),fullPage:true }).catch(() => {})
    throw error
  } finally {
    if (browser) await browser.close()
    for (const child of processes.toReversed()) await stop(child)
  }
})().catch(error => { console.error(error); console.error(`Diagnostics: ${output}`); process.exitCode = 1 })
