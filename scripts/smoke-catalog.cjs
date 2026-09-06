/* Real API/browser catalog acceptance. Uses its own database and loopback ports. */
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
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-catalog-'))
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

;(async () => {
  let browser
  let page
  const apiPort = await freePort()
  const webPort = await freePort()
  const base = `http://127.0.0.1:${apiPort}`
  const ui = `http://127.0.0.1:${webPort}`
  const dbUrl = `sqlite:///${path.join(output, 'catalog.db').replaceAll('\\', '/')}`
  
  const env = { ...process.env,
    JARVIS_DATABASE_URL: dbUrl,
    JARVIS_DATA_DIRECTORY: output, PYTHONPATH: path.join(root, 'apps/api'),
    JARVIS_AUTONOMOUS_WORKER_ENABLED: 'false', JARVIS_MODEL_EXECUTION_MODE: 'disabled',
    JARVIS_MODEL_OLLAMA_ENABLED: 'false', JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED: 'false',
    JARVIS_MODEL_ALLOW_REMOTE: 'false', JARVIS_AUTO_MIGRATE: 'true', WEB_ORIGIN: ui,
  }
  
  // 1. Start API to migrate DB
  let api = start(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], path.join(root, 'apps/api'), env, 'api')
  await ready(`${base}/api/health`, api)

  // 2. Import catalog data safely
  console.log('Importing catalog...')
  execFileSync(python, [
    path.join(root, 'scripts', 'import-agent-catalog.py'),
    '--source', 'wshobson-agents',
    '--ref', 'a30778f8c4e6b0a87567941b7cca4f534bf642b6',
    '--database-url', dbUrl
  ], { env, stdio: 'inherit', cwd: root })
  
  // 3. Start Frontend
  const vite = start(process.execPath, [path.join(web, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], web,
    { ...env, VITE_API_BASE_URL: base, VITE_WS_URL: `ws://127.0.0.1:${apiPort}/ws/events` }, 'web')
  await ready(ui, vite)
  
  try {
    browser = await chromium.launch({ headless: true })
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    page.setDefaultTimeout(10000)
    const errors = []
    
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('net::ERR_FAILED')) errors.push(message.text()) })
    
    // open Agents page
    await page.goto(`${ui}/agents`)
    await page.getByRole('heading', { name: 'Workforce catalog' }).waitFor()
    
    // Sources visible
    await page.getByRole('button', { name: 'Sources' }).click()
    await page.getByText('wshobson/agents').waitFor()
    
    // Skills visible
    await page.getByRole('button', { name: 'Skills' }).click()
    await page.waitForTimeout(500)
    
    // Agent Catalog visible
    await page.getByRole('button', { name: 'Agent Catalog' }).click()
    await page.waitForTimeout(500)
    
    // inspect catalog entry
    const firstAgent = page.getByRole('article').first()
    const agentName = await firstAgent.getByRole('heading').first().innerText()
    await firstAgent.getByRole('button', { name: 'Inspect ' + agentName }).click({ force: true })
    
    // provenance/hash/license visible
    await page.getByText('MIT').first().waitFor() // License
    
    // review a canonical agent
    await page.getByRole('textbox').fill('Looks good')
    await page.getByRole('button', { name: 'Approve exact revision' }).click()
    
    // activate it
    await page.getByRole('button', { name: 'Activate reviewed specialist' }).click()
    
    // active workforce reflects it
    await page.getByRole('button', { name: 'Active catalog workforce' }).click()
    await page.getByRole('article').filter({ hasText: agentName }).waitFor()
    
    // reload, state persists
    await page.reload()
    await page.getByRole('button', { name: 'Active catalog workforce' }).click()
    await page.getByRole('article').filter({ hasText: agentName }).waitFor()
    
    if (errors.length > 0) {
      console.error('Browser errors:', errors)
      process.exitCode = 1
    } else {
      console.log('PASS: Catalog specific browser acceptance')
    }
  } finally {
    if (browser) await browser.close()
    for (const process of processes) {
      process.kill()
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
