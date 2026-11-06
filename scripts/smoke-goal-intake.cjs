/* Actual loopback API + browser acceptance for durable draft intake. No inference. */
const { spawn, execFileSync } = require('node:child_process')
const { createRequire } = require('node:module')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const assert = require('node:assert/strict')
const root = path.resolve(__dirname, '..')
const { chromium } = createRequire(path.join(root, 'apps/web/package.json'))('playwright')
const args = process.argv.slice(2)
const python = args[args.indexOf('--python') + 1]
const evidence = path.resolve(args[args.indexOf('--evidence-dir') + 1] || '')
if (!args.includes('--python') || !args.includes('--evidence-dir')) throw new Error('Pass --python and --evidence-dir explicitly')
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function port() { const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); const value = server.address().port; await new Promise(resolve => server.close(resolve)); return value }
async function ready(url) { for (let i = 0; i < 160; i++) { try { if ((await fetch(url)).ok) return } catch {} await delay(250) } throw new Error(`Service did not become ready: ${url}`) }
async function stop(child) { if (!child || child.exitCode !== null) return; child.kill(); await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(5000)]) }
async function main() {
  await fs.mkdir(evidence, { recursive: true })
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'jarvis-goal-intake-'))
  const apiPort = await port(), webPort = await port()
  const apiUrl = `http://127.0.0.1:${apiPort}`, webUrl = `http://127.0.0.1:${webPort}`
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('JARVIS_') && !key.startsWith('VITE_')))
  Object.assign(env, { PYTHONPATH: path.join(root, 'apps/api'), JARVIS_DATABASE_URL: `sqlite:///${path.join(temporary, 'intake.db').replaceAll('\\', '/')}`, JARVIS_DATA_DIRECTORY: temporary, JARVIS_AUTONOMOUS_WORKER_ENABLED: 'false', JARVIS_MODEL_VERIFIED_CLOUD_ENABLED: 'false', JARVIS_MODEL_EXECUTION_MODE: 'disabled', WEB_ORIGIN: webUrl, API_HOST: '127.0.0.1', API_PORT: String(apiPort), VITE_API_BASE_URL: apiUrl, VITE_WS_URL: `ws://127.0.0.1:${apiPort}/ws/events` })
  const nativeCounts = () => JSON.parse(execFileSync(python, ['-c', `import json,sqlite3,sys;from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True) as db:
 tables=['model_executions','agent_runtime_runs','agent_runtime_attempts','tool_executions','context_assemblies','groq_acceptance_runs','groq_rate_states','free_ai_quotas']
 print(json.dumps({table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}))`, path.join(temporary, 'intake.db')], { env, windowsHide: true, encoding: 'utf8' }))
  let api, web, browser
  const logs = []
  const launchApi = () => { const child = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], { cwd: path.join(root, 'apps/api'), env, windowsHide: true }); child.stderr.on('data', data => logs.push(String(data))); return child }
  try {
    api = launchApi()
    await ready(`${apiUrl}/api/health`)
    const initialNativeCounts = nativeCounts()
    assert.ok(Object.values(initialNativeCounts).every(count => count === 0))
    web = spawn(process.execPath, [path.join(root, 'apps/web/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], { cwd: path.join(root, 'apps/web'), env, windowsHide: true })
    web.stderr.on('data', data => logs.push(String(data)))
    await ready(webUrl)
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const pageErrors = []; const consoleErrors = []; page.on('pageerror', error => pageErrors.push(error.message)); page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    await page.goto(webUrl)
    await page.getByRole('heading', { name: 'What would you like to accomplish?' }).waitFor()
    assert.match(await page.title(), /^Home/)
    const before = (await (await fetch(`${apiUrl}/api/tasks`)).json()).data.length
    const original = 'Review my Level 1 trapeze schedules and prepare a discrepancy report. Preserve assumptions for human review.'
    await page.getByLabel('Describe your goal').fill(original)
    await page.getByRole('button', { name: 'Save goal', exact: true }).click()
    await page.getByRole('heading', { name: 'Request preserved' }).waitFor()
    const first = (await (await fetch(`${apiUrl}/api/goals`)).json()).data[0]
    assert.equal(first.messages[0].content, original)
    await page.getByRole('button', { name: 'Open linked task' }).click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('button', { name: 'Close task details' }).click()
    for (const width of [1920, 1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1920 ? 1080 : 1000 })
      await page.evaluate(() => scrollTo(0, 0))
      await page.screenshot({ path: path.join(evidence, `home-${width}.png`), fullPage: true })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Horizontal overflow at ${width}`)
    }
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.reload()
    await page.getByRole('button', { name: new RegExp('Review my Level 1') }).click()
    await page.getByRole('heading', { name: 'Request preserved' }).waitFor()
    assert.deepEqual(consoleErrors, [], 'Console errors before intentional network interruptions')
    // Lose one acknowledgement after the real server has committed the request.
    const secondText = 'Retain this request after an interrupted acknowledgement.'
    await page.getByRole('button', { name: 'New conversation' }).click()
    await page.getByLabel('Describe your goal').fill(secondText)
    await page.route('**/api/goals', async route => { if (route.request().method() === 'POST') { await route.fetch(); await route.abort('failed') } else await route.continue() })
    await page.getByRole('button', { name: 'Save goal', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: 'Submission could not be confirmed' }).waitFor()
    await page.unroute('**/api/goals')
    await page.reload()
    await page.getByRole('button', { name: 'Retry same request' }).waitFor()
    await page.getByRole('button', { name: 'Retry same request' }).click()
    await page.getByRole('heading', { name: 'Request preserved' }).waitFor()
    assert.equal((await (await fetch(`${apiUrl}/api/tasks`)).json()).data.length, before + 2)
    await stop(api); api = launchApi(); await ready(`${apiUrl}/api/health`)
    await page.reload()
    await page.getByRole('button', { name: new RegExp('Retain this request') }).click()
    await page.getByRole('heading', { name: 'Request preserved' }).waitFor()
    assert.equal((await (await fetch(`${apiUrl}/api/goals`)).json()).data.length, 2)
    const status = (await (await fetch(`${apiUrl}/api/system/status`)).json()).data
    assert.equal(status.autonomousWorker.enabled, false)
    assert.deepEqual(pageErrors, [])
    const finalNativeCounts = nativeCounts()
    assert.deepEqual(finalNativeCounts, initialNativeCounts)
    await delay(1000)
    assert.deepEqual(nativeCounts(), finalNativeCounts)
    await fs.writeFile(path.join(evidence, 'goal-intake-acceptance.json'), JSON.stringify({ outcome: 'PASS', source: 'working tree; record commit separately', originalRequestPreserved: true, createdNativeTasks: 2, duplicateTasks: 0, nativeExecutionCounts: finalNativeCounts, idleObservationMs: 1000, zeroNativeDispatchesMeasured: true, operatorInteractions: { firstSubmission: 2, openTaskAndClose: 2, historyRestoration: 1, explicitRetryAfterReload: 1 }, execution: 'disabled; draft intake only', lostAcknowledgementRecovered: true, apiRestartRecovered: true, pageErrors, widths: [1920,1440,1024,768,390,320], database: temporary }, null, 2))
    console.log('PASS: actual API/browser intake, lost acknowledgement/reload replay, restart, task link, six widths; no inference')
  } finally { await browser?.close(); await stop(web); await stop(api); await fs.writeFile(path.join(evidence, 'process.log'), logs.join('')) }
}
main().catch(error => { console.error(error); process.exitCode = 1 })

