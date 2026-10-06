/* Real API/browser command acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-command-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'command.db').replaceAll('\\', '/')}`,
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
    let networkFaultExpected = false
    const failures = []
    const writes = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error' && !(networkFaultExpected && /net::ERR_(?:FAILED|INTERNET_DISCONNECTED)/.test(message.text()))) errors.push(message.text()) })
    page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`) })
    page.on('request', request => { if (['POST', 'PATCH'].includes(request.method())) writes.push(new URL(request.url()).pathname) })
    const registered = await page.request.post(`${base}/api/identity/agents`, { data: { stable_key: 'command-researcher', display_name: 'Command researcher', description: 'Actual registered identity fixture', agent_type: 'worker' } })
    assert.equal(registered.status(), 201)
    const fixtureIdentity = (await (await page.request.get(`${base}/api/identity/agents`)).json()).data.find(row => row.stable_key === 'command-researcher')
    assert.ok((await page.request.post(`${base}/api/identity/agents/${fixtureIdentity.id}/activate`)).ok())
    await page.goto(`${ui}/tasks`)
    await page.getByRole('heading', { name: 'Tasks', exact: true }).waitFor()
    const command = page.getByRole('dialog', { name: 'Talk to Jarvis', exact: true })
    const launch = page.getByRole('button', { name: 'Talk to Jarvis', exact: true })
    await page.keyboard.press('Control+k')
    await command.waitFor()
    assert.equal(await command.getByLabel('Find records and pages').evaluate(el => el === document.activeElement), true)
    for (let index=0;index<45;index++) {
      await page.keyboard.press('Tab')
      assert.equal(await command.evaluate(el => el.contains(document.activeElement)), true, 'Focus escaped native dialog')
    }
    await page.keyboard.press('Escape')
    await launch.click()
    await page.keyboard.press('Escape')
    assert.equal(await launch.evaluate(el => el === document.activeElement), true, 'Launcher focus not restored')
    await launch.click()
    await command.getByRole('button', { name: /Command researcher/ }).waitFor()
    await command.getByRole('button', { name: /Command researcher/ }).click()
    await page.getByRole('heading', { name: 'Planning workspace', exact: true }).waitFor()
    assert.equal(await page.getByLabel('Act as local identity').inputValue(), fixtureIdentity.id)
    await launch.click()
    await command.getByLabel('Find records and pages').fill('zz-no-record-zz')
    await command.getByText('No loaded records or pages match this search.', { exact: true }).waitFor()
    await command.getByLabel('Find records and pages').fill('Archive')
    await command.getByRole('button', { name: /Archive.*simulated agent/ }).click()
    await page.getByRole('dialog', { name: 'Archive', exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await launch.click()
    await command.getByLabel('Find records and pages').fill('')
    await command.getByRole('region', { name: 'Approvals', exact: true }).getByRole('button').first().click()
    await page.getByRole('heading', { name: 'Approval inbox', exact: true }).waitFor()
    await launch.click()
    await command.getByLabel('Find records and pages').fill('Organize reading list')
    await command.getByRole('button', { name: /Organize reading list/ }).click()
    await page.getByRole('dialog', { name: 'Organize reading list', exact: true }).waitFor()
    await page.keyboard.press('Control+k')
    assert.equal(await command.count(), 0, 'Command opened over another detail dialog')
    await page.keyboard.press('Escape')
    for (const width of [1440,1920,1024,390,320]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.ok(await page.locator('.content').evaluate((el, minimum) => parseFloat(getComputedStyle(el).paddingBottom) >= minimum, width <= 760 ? 160 : 100), 'Launcher clearance overridden by shell')
      if (width <= 760) {
        const mobile = page.getByRole('navigation', { name: 'Mobile primary' })
        await mobile.getByRole('button', { name: 'More navigation' }).click()
        for (const link of await mobile.locator('a').all()) {
          assert.ok(await link.evaluate(el => {
            const box = el.getBoundingClientRect()
            return el.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2))
          }), 'Launcher obstructed expanded navigation')
        }
        await mobile.getByRole('button', { name: 'Close more navigation' }).click()
      }
      await launch.click()
      await command.getByLabel('Find records and pages').fill('')
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Page overflow')
      assert.ok(await command.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'Command overflow')
      await page.screenshot({ path: path.join(output, `lookup-${width}.png`), fullPage: false })
      await command.getByRole('button', { name: 'New request', exact: true }).click()
      await command.getByLabel('Title', { exact: true }).fill('Actual API command request')
      await command.getByLabel('Description', { exact: true }).fill('Preserve this draft and create exactly one queued request.')
      await page.screenshot({ path: path.join(output, `request-${width}.png`), fullPage: false })
      await page.keyboard.press('Escape')
      await launch.click()
      await command.getByLabel('Title', { exact: true }).waitFor()
      assert.equal(await command.getByLabel('Title', { exact: true }).inputValue(), 'Actual API command request')
      await command.getByRole('button', { name: 'Find records', exact: true }).click()
      await page.keyboard.press('Escape')
    }
    await launch.click()
    await command.getByRole('button', { name: 'New request', exact: true }).click()
    await command.getByRole('button', { name: 'Create task', exact: true }).click()
    await command.getByRole('heading', { name: 'Task created and queued', exact: true }).waitFor()
    const taskRows = (await (await page.request.get(`${base}/api/tasks`)).json()).data
    const created = taskRows.filter(task => task.title === 'Actual API command request')
    assert.equal(created.length, 1)
    assert.equal(created[0].status, 'queued')
    await command.getByRole('button', { name: 'Open planning for this task', exact: true }).click()
    await page.getByRole('heading', { name: 'Planning workspace', exact: true }).waitFor()
    assert.equal(await command.count(), 0)
    // Last-known identities remain visible when their refresh fails.
    networkFaultExpected = true
    await page.route(`${base}/api/identity/agents*`, route => route.abort('failed'))
    await launch.click()
    await command.getByRole('button', { name: 'Find records', exact: true }).click()
    await command.getByLabel('Find records and pages').fill('Command researcher')
    await command.getByText(/Registered identity lookup is unavailable:/).waitFor()
    await command.getByRole('button', { name: /Command researcher/ }).waitFor()
    await page.unroute(`${base}/api/identity/agents*`)
    await page.keyboard.press('Escape')
    await launch.click()
    await command.getByRole('button', { name: /Command researcher/ }).waitFor()
    // HTTP refresh failure and offline state retain lookup and block new creation.
    await page.context().setOffline(true)
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await command.getByText(/Last-known Hub records may be outdated/).waitFor()
    await command.getByRole('button', { name: 'Refresh state', exact: true }).click()
    await command.getByRole('button', { name: 'New request', exact: true }).click()
    await command.getByRole('button', { name: 'Start another request', exact: true }).click()
    assert.equal(await command.getByRole('button', { name: 'Create task', exact: true }).isDisabled(), true)
    await page.context().setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await command.getByRole('button', { name: 'Create task', exact: true }).waitFor({ state: 'visible' })
    await page.waitForFunction(() => !document.querySelector('.jarvis-command-dialog fieldset').disabled)
    // Accepted POST, lost response, close and reload, retry with the original persisted key.
    await command.getByLabel('Title', { exact: true }).fill('Recovered command request')
    await command.getByLabel('Description', { exact: true }).fill('Recover the same stored task after an accepted response is lost.')
    let lost = false
    const keys = []
    await page.route(`${base}/api/tasks`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      keys.push(route.request().headers()['idempotency-key'])
      if (!lost) { lost = true; await route.fetch(); await route.abort('failed') } else await route.continue()
    })
    await command.getByRole('button', { name: 'Create task', exact: true }).click()
    await command.getByRole('button', { name: 'Retry creation', exact: true }).waitFor()
    await command.getByText(/outcome may be uncertain/).waitFor()
    await page.keyboard.press('Escape')
    await launch.click()
    await command.getByRole('button', { name: 'Retry creation', exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await page.reload()
    await page.getByRole('heading', { name: 'Planning workspace', exact: true }).waitFor()
    await launch.click()
    await command.getByRole('button', { name: 'New request', exact: true }).click()
    await command.getByLabel('Title', { exact: true }).fill('Recovered command request')
    await command.getByLabel('Description', { exact: true }).fill('Recover the same stored task after an accepted response is lost.')
    await command.getByRole('button', { name: 'Create task', exact: true }).click()
    await command.getByRole('heading', { name: 'Task created and queued', exact: true }).waitFor()
    assert.equal(keys.length, 2)
    assert.equal(keys[0], keys[1], 'Retry did not retain persisted idempotency key')
    await page.unroute(`${base}/api/tasks`)
    networkFaultExpected = false
    const recovered = (await (await page.request.get(`${base}/api/tasks`)).json()).data.filter(task => task.title === 'Recovered command request')
    assert.equal(recovered.length, 1)
    assert.equal(recovered[0].status, 'queued')
    // Closing while a POST is in flight does not cancel or hide eventual acknowledgement.
    await command.getByRole('button', { name: 'Start another request', exact: true }).click()
    await command.getByLabel('Title', { exact: true }).fill('Closed while creating')
    await command.getByLabel('Description', { exact: true }).fill('The existing form remains mounted until this acknowledgement arrives.')
    let finish
    let accepted
    const acceptedPromise = new Promise(resolve => { accepted = resolve })
    await page.route(`${base}/api/tasks`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      const response = await route.fetch()
      accepted()
      await new Promise(resolve => { finish = resolve })
      await route.fulfill({ response })
    })
    await command.getByRole('button', { name: 'Create task', exact: true }).click()
    await acceptedPromise
    await page.keyboard.press('Escape')
    finish()
    await launch.click()
    await command.getByRole('heading', { name: 'Task created and queued', exact: true }).waitFor()
    await command.getByRole('status').getByText('Closed while creating', { exact: true }).waitFor()
    await page.unroute(`${base}/api/tasks`)
    assert.equal(writes.filter(route => route === '/api/tasks').length, 4)
    assert.ok(writes.every(route => route === '/api/tasks'), 'Unexpected command authority')
    await page.keyboard.press('Escape')
    await page.setViewportSize({ width: 1774, height: 887 })
    await page.screenshot({ path: path.join(output, 'launcher-desktop.png'), fullPage: false })
    await launch.click()
    await command.getByRole('button', { name: 'Find records', exact: true }).click()
    await command.getByLabel('Find records and pages').fill('')
    await page.screenshot({ path: path.join(output, 'lookup-concept-size.png'), fullPage: false })
    await command.getByRole('button', { name: 'New request', exact: true }).click()
    await command.getByRole('button', { name: 'Start another request', exact: true }).click()
    await page.setViewportSize({ width: 320, height: 500 })
    await command.getByRole('button', { name: 'Create task', exact: true }).scrollIntoViewIfNeeded()
    assert.ok(await command.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'Short phone modal overflow')
    await page.screenshot({ path: path.join(output, 'request-short-phone.png'), fullPage: false })
    assert.deepEqual(errors, [], 'Browser errors')
    assert.deepEqual(failures, [], 'HTTP failures')
    console.log(`PASS actual identity/task/approval lookup, native focus trap/restore, five widths, stale/offline/reconnect, persisted lost-ack retry, close during pending creation and explicit Planning. Evidence: ${output}`)
  } catch (error) {
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {})
    throw error
  } finally {
    if (browser) await browser.close()
    for (const child of processes.toReversed()) await stop(child)
  }
})().catch(error => { console.error(error); console.error(`Diagnostics: ${output}`); process.exitCode = 1 })
