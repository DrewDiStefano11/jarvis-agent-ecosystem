/* Real API/browser task index acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-task-index-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'task-index.db').replaceAll('\\', '/')}`,
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
    await page.goto(`${ui}/tasks`)
    await page.getByRole('heading', { name: 'Tasks', exact: true }).waitFor()
    assert.equal(await page.title(), 'Tasks · Jarvis')
    await page.locator('.status-connected').waitFor()
    const table=page.getByRole('table',{name:'Task record index',exact:true})
    await table.waitFor()
    await page.getByRole('button',{name:'Open Research discontinued route',exact:true}).waitFor()
    for(const [label,width,height] of [['desktop',1440,1000],['widescreen',1920,1080],['laptop',1024,768],['mobile',390,844],['small-mobile',320,740]]) {
      await page.setViewportSize({width,height})
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${label} overflow`)
      await page.screenshot({path:path.join(output,`task-index-${label}.png`),fullPage:true})
    }
    await page.setViewportSize({width:1440,height:1000})
    await page.getByLabel('View',{exact:true}).selectOption('approval')
    await page.getByRole('button',{name:'Open Seven-day Caribbean trip recommendation',exact:true}).waitFor()
    await page.getByLabel('View',{exact:true}).selectOption('completed')
    await page.getByText('Result preview for Organize reading list',{exact:true}).click()
    await page.locator('.task-index-result-row pre').waitFor()
    await page.screenshot({path:path.join(output,'task-index-completed.png'),fullPage:true})
    await page.getByLabel('View',{exact:true}).selectOption('failed')
    assert.equal(await page.getByRole('button',{name:'Open Research discontinued route',exact:true}).count(),1)
    await page.getByLabel('View',{exact:true}).selectOption('all')
    await page.getByLabel('Assigned agent',{exact:true}).selectOption('agent:scout')
    assert.equal(await page.getByRole('button',{name:'Open Organize reading list',exact:true}).count(),0)
    await page.getByLabel('Assigned agent',{exact:true}).selectOption('all')
    // Task creation stays the existing retry-safe form and never queues inference.
    await page.getByRole('button',{name:'+ New task',exact:true}).click()
    await page.getByLabel('Title',{exact:true}).fill('Task index actual API')
    await page.getByLabel('Description',{exact:true}).fill('Inspect bounded records and explicit planning handoff.')
    const created=page.waitForResponse(r=>r.url().endsWith('/api/tasks')&&r.request().method()==='POST')
    await page.getByRole('button',{name:'Create task',exact:true}).click()
    const task=(await (await created).json()).data
    await page.getByRole('button',{name:'Open Task index actual API',exact:true}).waitFor()
    await page.getByRole('searchbox').fill(task.id)
    assert.equal(await page.getByRole('button',{name:/^Open /}).count(),1)
    await page.getByRole('button',{name:'Open Task index actual API',exact:true}).click()
    await page.getByRole('dialog').waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({state:'hidden'})
    await page.getByRole('searchbox').fill('absent')
    await page.getByText('No task records match these filters.',{exact:true}).waitFor()
    await page.getByRole('searchbox').fill('')
    // Correct a real persisted completed seed request via existing query/form flow.
    const records=(await (await page.request.get(`${base}/api/tasks`)).json()).data
    const source=records.find(t=>t.status==='completed')
    await page.goto(`${ui}/tasks?correct=${encodeURIComponent(source.id)}`)
    await page.getByRole('heading',{name:'Correct task input',exact:true}).waitFor()
    await page.getByLabel('Title',{exact:true}).fill('Task index corrected request')
    await page.getByLabel('Description',{exact:true}).fill('A linked correction with original completed evidence preserved.')
    await page.getByRole('button',{name:'Create corrected task',exact:true}).click()
    await page.getByRole('button',{name:'Open Task index corrected request',exact:true}).waitFor()
    await page.getByText(/Corrected follow-up to/).waitFor()
    const original=(await (await page.request.get(`${base}/api/tasks/${source.id}`)).json()).data
    assert.equal(original.result,source.result)
    assert.equal(original.status,'completed')
    await page.context().setOffline(true)
    await page.getByText('Last-known task data may be outdated. Refresh state.',{exact:true}).waitFor()
    await page.getByRole('button',{name:'Refresh state',exact:true}).click()
    await page.getByRole('button',{name:'Open Task index actual API',exact:true}).waitFor()
    await page.screenshot({path:path.join(output,'task-index-offline.png'),fullPage:true})
    await page.context().setOffline(false)
    await page.getByText('Last-known task data may be outdated. Refresh state.',{exact:true}).waitFor({state:'hidden',timeout:15000})
    await page.reload()
    await page.getByRole('button',{name:'Open Task index corrected request',exact:true}).waitFor()
    await page.getByLabel('View',{exact:true}).selectOption('completed')
    await page.getByText('Result preview for Organize reading list',{exact:true}).click()
    await page.setViewportSize({width:320,height:740})
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Result phone overflow')
    await page.screenshot({path:path.join(output,'task-index-result-mobile.png'),fullPage:true})
    assert.deepEqual(errors.filter(error=>!/net::ERR_INTERNET_DISCONNECTED|WebSocket.*(failed|closed)|net::ERR_NETWORK_CHANGED/.test(error)),[])
    assert.deepEqual(failures,[])
    assert(!writes.some(route=>route.includes('/agent-runtime/commands')),'Task creation must not queue inference')
    console.log(`PASS: real task records, five widths, completed result/filter, assignment, creation/details, correction preserving original, offline/HTTP refresh/reconnect and reload. Evidence: ${output}`)
  } catch(error) {
    if(page&&!page.isClosed())await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{})
    throw error
  } finally {
    if(browser)await browser.close()
    for(const child of processes.toReversed())await stop(child)
  }
})().catch(error=>{console.error(error);console.error(`Diagnostics: ${output}`);process.exitCode=1})
