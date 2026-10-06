/* Real API/browser Activity acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-activity-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'activity.db').replaceAll('\\', '/')}`,
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
    // Real operator-created records, deliberately isolated from any model dispatch.
    let task
    for(let i=0;i<52;i++) {
      const response=await page.request.post(`${base}/api/tasks`,{data:{title:`Activity acceptance ${i}`,description:'Bounded audit display acceptance through actual task creation.'}})
      assert.equal(response.status(),201)
      task=(await response.json()).data
    }
    await page.goto(`${ui}/audit`)
    await page.getByRole('heading',{name:'Activity',exact:true}).waitFor()
    await page.locator('.status-connected').waitFor()
    assert.equal(await page.title(),'Audit · Jarvis') // shell title remains independent until #75
    assert.equal(await page.locator('.activity-list > li').count(),50)
    await page.getByRole('button',{name:'Load 50 more',exact:true}).click()
    assert.equal(await page.locator('.activity-list > li').count(),53)
    const summary=page.getByText('Created task: Activity acceptance 51',{exact:true})
    await summary.click()
    await page.getByLabel('Displayed event evidence',{exact:true}).waitFor()
    assert.equal(await page.getByText('Provenance not supplied by this record',{exact:true}).count(),1)
    await page.context().grantPermissions(['clipboard-read','clipboard-write'])
    await page.getByRole('button',{name:'Copy displayed evidence',exact:true}).click()
    await page.getByText('Displayed evidence copied.',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),await page.getByLabel('Displayed event evidence',{exact:true}).textContent())
    for(const [label,width,height] of [['desktop',1440,1000],['widescreen',1920,1080],['laptop',1024,768],['mobile',390,844],['small-mobile',320,740]]) {
      await page.setViewportSize({width,height})
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${label} overflow`)
      await page.evaluate(()=>window.scrollTo(0,0))
      await page.screenshot({path:path.join(output,`activity-${label}.png`),fullPage:false})
    }
    await page.setViewportSize({width:1440,height:1000})
    await page.getByRole('button',{name:'Open task',exact:true}).click()
    await page.getByRole('dialog').waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({state:'hidden'})
    await page.getByLabel('Actor',{exact:true}).selectOption('system')
    assert.equal(await page.locator('.activity-list > li').count(),50)
    await page.getByLabel('Category',{exact:true}).selectOption('task')
    await page.getByRole('searchbox').fill('Activity acceptance 51')
    assert.equal(await page.locator('.activity-list > li').count(),1)
    if (!await page.getByLabel('Displayed event evidence',{exact:true}).isVisible()) await page.getByText('Created task: Activity acceptance 51',{exact:true}).click()
    await page.getByLabel('Displayed event evidence',{exact:true}).waitFor()
    await page.setViewportSize({width:320,height:740})
    await page.screenshot({path:path.join(output,'activity-focused-mobile.png'),fullPage:true})
    await page.setViewportSize({width:1440,height:1000})
    await page.getByRole('searchbox').fill('absent')
    await page.getByText('No activity matches these filters.',{exact:true}).waitFor()
    await page.getByRole('searchbox').fill('')
    await page.getByLabel('Actor',{exact:true}).selectOption('all')
    await page.getByLabel('Category',{exact:true}).selectOption('all')
    await page.context().setOffline(true)
    await page.getByText(/Last-known activity may be outdated/).waitFor()
    await page.getByRole('button',{name:'Refresh state',exact:true}).click()
    assert.equal(await page.locator('.activity-list > li').count(),50)
    await page.screenshot({path:path.join(output,'activity-offline.png'),fullPage:true})
    await page.context().setOffline(false)
    await page.getByText(/Last-known activity may be outdated/).waitFor({state:'hidden',timeout:15000})
    const decision=await page.request.post(`${base}/api/approvals/approval-pending/approve`,{data:{decisionNote:'Activity event acceptance'}})
    assert.equal(decision.status(),200)
    await page.getByText('Approval approved',{exact:true}).waitFor()
    await page.getByText('Approval approved',{exact:true}).click()
    await page.getByLabel('Displayed event evidence',{exact:true}).waitFor()
    await page.getByRole('button',{name:'Refresh state',exact:true}).click()
    await page.reload()
    await page.locator('.status-connected').waitFor()
    await page.getByText('Approval approved',{exact:true}).waitFor()
    const persisted=await page.request.get(`${base}/api/tasks/${task.id}`)
    assert.equal((await persisted.json()).data.title,'Activity acceptance 51')
    assert.deepEqual(errors.filter(error=>!/net::ERR_INTERNET_DISCONNECTED|WebSocket.*(failed|closed)|net::ERR_NETWORK_CHANGED/.test(error)),[])
    assert.deepEqual(failures,[])
    assert(!writes.some(route=>route.includes('/agent-runtime/commands')),'No inference dispatch')
    console.log(`PASS: actual durable audit, 50-row bound/load more, sequence/provenance, clipboard, five widths, task drawer, filters, offline/HTTP reconciliation, live approval and reload. Evidence: ${output}`)
  } catch(error) {
    if(page&&!page.isClosed()) await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{})
    throw error
  } finally {
    if(browser) await browser.close()
    for(const child of processes.toReversed()) await stop(child)
  }
})().catch(error=>{console.error(error);console.error(`Diagnostics: ${output}`);process.exitCode=1})
