/* Real API/browser task workflow acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-task-workflows-'))
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
    JARVIS_DATABASE_URL: `sqlite:///${path.join(output, 'tasks.db').replaceAll('\\', '/')}`,
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
    await page.getByRole('heading', { name:'Tasks', exact:true }).waitFor()
    await page.locator('.status-connected').waitFor()
    assert.equal(await page.title(), 'Tasks · Jarvis')
    for (const [label, width, height] of [['desktop',1440,1000], ['reference',1586,992], ['widescreen',1920,1080], ['laptop',1024,768], ['tablet',768,1024], ['large-desktop',2560,1440], ['mobile',390,844], ['small-mobile',320,740]]) {
      await page.setViewportSize({ width, height })
      await page.evaluate(() => window.scrollTo(0,0))
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1), `${label} Tasks overflows`)
      await page.screenshot({path:path.join(output,`tasks-${label}.png`),fullPage:true})
    }
    await page.setViewportSize({width:1440,height:1000})
    const views=page.getByRole('group',{name:'Task view',exact:true})
    await views.getByRole('button',{name:'Completed',exact:true}).click()
    await page.getByRole('button',{name:'Open Organize reading list',exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'Open Summarize household manual',exact:true}).count(),0)
    await page.getByText('Result preview for Organize reading list',{exact:true}).click()
    assert.equal(await page.locator('.task-index-result-row pre').isVisible(),true)
    await views.getByRole('button',{name:'All',exact:true}).click()
    await page.getByRole('button',{name:'Filters',exact:true}).click()
    await page.getByLabel('Status',{exact:true}).selectOption('queued')
    assert.equal(await page.locator('.task-index-row').count(),1)
    await page.locator('.task-advanced-filters').getByRole('button',{name:'Clear filters',exact:true}).click()
    await page.getByRole('button',{name:'Filters',exact:true}).click()
    const queued=(await (await page.request.get(`${base}/api/tasks`)).json()).data.find(task=>task.title==='Summarize household manual')
    const planButton=page.getByRole('button',{name:'Plan Summarize household manual',exact:true})
    await planButton.click()
    let drawer=page.getByRole('dialog',{name:'Plan this task',exact:true})
    await drawer.getByRole('heading',{name:'Planning workspace',exact:true}).waitFor()
    assert.equal(await drawer.getByLabel('Task and history',{exact:true}).inputValue(),queued.id)
    assert.equal(await drawer.getByRole('button',{name:'Queue local plan',exact:true}).isDisabled(),true)
    assert.equal(page.url(),`${ui}/tasks`)
    for (const width of [1920,1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:width<768?844:1000})
      await drawer.evaluate(el=>{el.scrollTop=0})
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Planning drawer overflow '+width)
      await page.screenshot({path:path.join(output,`planning-drawer-${width}.png`)})
    }
    await page.setViewportSize({width:1440,height:1000})
    for(let index=0;index<18;index++){await page.keyboard.press('Tab');assert.ok(await page.evaluate(()=>Boolean(document.activeElement?.closest('dialog[open]'))),'Native planning focus must stay inside')}
    await page.keyboard.press('Escape')
    assert.equal(await planButton.evaluate(el=>el===document.activeElement),true,'Native planning focus must return')
    await page.getByRole('button',{name:'Open Seven-day Caribbean trip recommendation',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Seven-day Caribbean trip recommendation',exact:true})
    await drawer.getByRole('heading',{name:'Original request',exact:true}).waitFor()
    await drawer.getByRole('button',{name:'Review 1 pending approval records',exact:true}).click()
    await drawer.getByRole('heading',{name:'Approval inbox',exact:true}).waitFor()
    assert.equal(await drawer.locator('.approval-record').count(),1,'Task inspector must focus approvals for this task')
    assert.equal(page.url(),`${ui}/tasks`)
    await page.screenshot({path:path.join(output,'task-approval.png')})
    await drawer.getByRole('button',{name:'Task summary',exact:true}).click()
    await drawer.getByRole('button',{name:'Plan this task',exact:true}).click()
    await drawer.getByRole('heading',{name:'Planning workspace',exact:true}).waitFor()
    assert.equal(await drawer.getByRole('button',{name:'Queue local plan',exact:true}).isDisabled(),true)
    await page.keyboard.press('Escape')
    await page.getByRole('button',{name:'Open Organize reading list',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Organize reading list',exact:true})
    await drawer.getByRole('heading',{name:'Final result',exact:true}).waitFor()
    for(const width of [1920,1440,1024,768,390,320]){
      await page.setViewportSize({width,height:width<768?844:1000});await drawer.evaluate(el=>{el.scrollTop=0});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Task inspector overflow '+width);await page.screenshot({path:path.join(output,`task-result-${width}.png`)})
    }
    await page.keyboard.press('Escape')
    await page.setViewportSize({width:1440,height:1000})
    await page.getByRole('button',{name:'Plan Summarize household manual',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Plan this task',exact:true})
    await drawer.getByRole('heading',{name:'Planning workspace',exact:true}).waitFor()
    await drawer.getByRole('button',{name:'New task',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'New task',exact:true})
    await drawer.getByLabel('Title',{exact:true}).fill('Task workflow acceptance request')
    await drawer.getByLabel('Description',{exact:true}).fill('Persist this isolated QA request without model execution.')
    const creationRequests=[];let interrupt=true
    await page.route(`${base}/api/tasks`,async route=>{
      if(route.request().method()!=='POST')return route.continue()
      creationRequests.push({body:route.request().postData(),key:route.request().headers()['idempotency-key']})
      if(interrupt){interrupt=false;await route.fetch();return route.abort('failed')}
      return route.continue()
    })
    await drawer.getByRole('button',{name:'Create task',exact:true}).click()
    await drawer.getByRole('alert').waitFor()
    assert.equal(await drawer.getByLabel('Title',{exact:true}).isDisabled(),true)
    await page.keyboard.press('Escape')
    await page.getByRole('button',{name:'New task',exact:true}).click()
    assert.equal(await drawer.getByLabel('Title',{exact:true}).inputValue(),'Task workflow acceptance request','Closing preserves the in-memory uncertain creation')
    assert.equal(await drawer.getByLabel('Title',{exact:true}).isDisabled(),true)
    await drawer.getByRole('button',{name:'Retry creation',exact:true}).click()
    await page.getByText('Task created and queued',{exact:true}).waitFor()
    assert.equal(creationRequests.length,2)
    assert.deepEqual(creationRequests[0],creationRequests[1],'Creation retries must reuse the persisted request and key')
    await page.unroute(`${base}/api/tasks`)
    const all=(await (await page.request.get(`${base}/api/tasks`)).json()).data
    const created=all.filter(task=>task.title==='Task workflow acceptance request')
    assert.equal(created.length,1);assert.equal(created[0].status,'queued')
    await page.getByRole('button',{name:'Open planning for this task',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Plan this task',exact:true})
    await drawer.getByRole('heading',{name:'Planning workspace',exact:true}).waitFor()
    assert.equal(await drawer.getByLabel('Task and history',{exact:true}).inputValue(),created[0].id)
    assert.equal(await drawer.getByRole('button',{name:'Queue local plan',exact:true}).isDisabled(),true)
    await page.keyboard.press('Escape')
    await page.getByRole('button',{name:'Open Research discontinued route',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Research discontinued route',exact:true})
    await drawer.getByRole('button',{name:'Revise task input',exact:true}).click()
    await drawer.getByRole('heading',{name:'Correct task input',exact:true}).waitFor()
    await drawer.getByLabel('Title',{exact:true}).fill('Corrected isolated request')
    await drawer.getByLabel('Description',{exact:true}).fill('Preserve the original failure and create a linked QA follow-up.')
    await drawer.getByRole('button',{name:'Create corrected task',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'Corrected isolated request',exact:true})
    await drawer.getByRole('heading',{name:'Original request',exact:true}).waitFor()
    const corrected=(await (await page.request.get(`${base}/api/tasks`)).json()).data.find(task=>task.title==='Corrected isolated request')
    const original=(await (await page.request.get(`${base}/api/tasks/${corrected.correctionOfTaskId}`)).json()).data
    assert.equal(original.status,'failed');assert.equal(corrected.status,'queued')
    assert.equal(page.url().split('?')[0],`${ui}/tasks`)
    await page.screenshot({path:path.join(output,'corrected-task.png')})
    await page.keyboard.press('Escape')
    await page.getByRole('button',{name:'New task',exact:true}).click()
    drawer=page.getByRole('dialog',{name:'New task',exact:true})
    await page.context().setOffline(true)
    await page.getByText('Data may be stale',{exact:true}).waitFor()
    assert.equal(await drawer.getByRole('button',{name:'Create task',exact:true}).isDisabled(),true)
    await page.screenshot({path:path.join(output,'offline-creation.png')})
    await page.context().setOffline(false)
    await page.getByText('Data may be stale',{exact:true}).waitFor({state:'hidden',timeout:15000})
    await page.keyboard.press('Escape')
    assert.ok(!writes.some(route=>/agent-runtime\/commands|local-planning\/setup|tool-executions/.test(route)),'Inspection and creation must not grant planner permissions or queue inference/tools')
    const relevant=errors.filter(error=>!/net::ERR_INTERNET_DISCONNECTED|WebSocket.*(failed|closed)|net::ERR_NETWORK_CHANGED/.test(error))
    assert.deepEqual(relevant,[],'Unexpected browser errors');assert.deepEqual(failures,[],'HTTP failures')
    console.log(`PASS: Tasks/Planning real-API filters, native focus/Escape, direct creation with lost-ack retry, focused approvals/results, linked correction, stale gates, no inference or permission writes, and eight viewports. Evidence: ${output}`)
  } catch(error){if(page)await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});console.error(`Evidence: ${output}`);throw error}
  finally{if(browser)await browser.close();for(const child of processes.reverse())await stop(child)}
})().catch(error=>{console.error(error);process.exitCode=1})
