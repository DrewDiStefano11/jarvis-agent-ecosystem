/* Derive and verify the deliberately limited live network with the pinned engine.
 * No candidate wrapper or global production approval is changed by this script.
 * Run: node scripts/build-office-network.cjs [--write]
 */
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const assert = require('node:assert/strict')
const root = path.resolve(__dirname, '..')
const ts = require(path.join(root, 'apps/web/node_modules/typescript'))
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'))
const hash = value => crypto.createHash('sha256').update(value).digest('hex')
const source = fs.readFileSync(path.join(__dirname, 'vendor/office-navigation.ts'), 'utf8')
assert.equal(hash(source.replaceAll('\r\n', '\n')), 'abff5ee7af915d83eb246363ad23c3cce80ae402251e4b8bcd8c1f6eedbc660f', 'Pinned original navigation engine changed; inspect provenance before regenerating')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const nav = {}
new Function('require', 'exports', compiled)(name => {
  assert.equal(name, '../../constants')
  return { OFFICE_SOURCE_WIDTH: 8192, OFFICE_SOURCE_HEIGHT: 5460 }
}, nav)
const evidence = read('docs/goal-mode/office-registration.json')
assert.equal(evidence.sourceCommit, '7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd')
const categories = { rooms: 'rooms', positions: 'positions', doors: 'doors', computers: 'computers', interactiveObjects: 'interactive-objects', walls: 'walls', objects: 'objects', walkPaths: 'walk-paths' }
const docs = Object.fromEntries(Object.entries(categories).map(([key, file]) => [key, read(`apps/web/public/assets/office-candidate/${file}.json`)]))
const geometryHash = hash(JSON.stringify(docs))
const registration = {
  sourceWidth: 8192, sourceHeight: 5460, markupWidth: 6144, markupHeight: 4096,
  scale: evidence.candidate.scale, offsetX: evidence.candidate.offsetX, offsetY: evidence.candidate.offsetY,
  rotationDegrees: 0, status: 'review_required', approvalStatus: 'candidate_reviewed',
  storedCoordinateSpace: 'registered_candidate_source', productionApproved: false,
  registrationLandmarks: evidence.landmarks,
  maximumResidualErrorPixels: evidence.candidate.maximumResidualErrorPixels,
  provenance: { generator: 'scripts/build-office-network.cjs', generatedArtifact: 'apps/api/app/office/catalog.json', sourceEvidence: ['docs/goal-mode/office-registration.json', 'docs/goal-mode/live-office.md'] },
}
assert.equal(nav.validateCandidateReviewRegistration(registration), null)
const graph = nav.buildCandidateNavigationGraph(docs, { registration })
assert.equal(graph.navigationAvailable, true)
assert.equal(graph.colliders.length, 260)
assert.equal(graph.doors.length, 47)
const selected = [
  ['POSITION_022', 'Models desk A'], ['POSITION_028', 'Models desk B'],
  ['POSITION_030', 'Models review desk A'], ['POSITION_032', 'Models review desk B'],
  ['POSITION_120', 'Focus C desk A'], ['POSITION_121', 'Focus C desk B'],
]
const stations = selected.map(([id, label]) => {
  const agent = graph.agents.find(value => value.positionId === id)
  assert.ok(agent, `Missing collision-checked station ${id}`)
  assert.equal(nav.candidatePointHasStaticClearance(graph, agent.point), true)
  return { id, label, roomId: agent.roomId, roomName: agent.roomName, point: agent.point }
})
const route = (originId, destinationId) => {
  const agent = graph.agents.find(value => value.positionId === originId)
  return nav.planCandidateRoute(graph, { destinationId: `position:${destinationId}`, agent: { id: agent.id, currentPoint: agent.point, revision: 0 } })
}
const routes = []
for (const [first, second] of [['POSITION_022', 'POSITION_028'], ['POSITION_030', 'POSITION_032'], ['POSITION_120', 'POSITION_121']]) {
  for (const [originId, destinationId] of [[first, second], [second, first]]) {
    const result = route(originId, destinationId)
    assert.equal(result.status, 'valid', `${originId} -> ${destinationId}: ${result.reason}`)
    assert.equal(nav.validateCandidateRouteSegments(graph, result.points, result.crossedDoorIds), null)
    assert.equal(nav.validateCandidateRouteDoorClearance(result.points, result.doorSteps, graph.doors), null)
    // This first reviewed subset does not cross a door. Door routes remain blocked.
    assert.deepEqual(result.crossedDoorIds, [])
    routes.push({ id: `${originId}:${destinationId}`, originId, destinationId, points: result.points, doorIds: result.crossedDoorIds, length: result.length })
  }
}
const negativeCases = [['POSITION_022', 'POSITION_030', 'blocked'], ['POSITION_022', 'POSITION_120', 'restricted'], ['POSITION_115', 'POSITION_117', 'blocked'], ['POSITION_120', 'POSITION_125', 'blocked']]
for (const [from, to, status] of negativeCases) assert.equal(route(from, to).status, status, `${from} -> ${to} must remain ${status}`)
for (const door of graph.doors.filter(value => value.accessMode === 'blocked')) assert.notEqual(nav.accessOutcome(door), 'allowed')
const catalog = { version: 'floor1-live-v1', sourceCommit: evidence.sourceCommit, geometryHash,
  reviewScope: 'Six reviewed desk stations and six within-room routes. Other candidate geometry and all door crossings remain unavailable.',
  stations, routes, spriteIds: ['agent-sheet-01', 'agent-sheet-06'] }
const output = path.join(root, 'apps/api/app/office/catalog.json')
const serialized = `${JSON.stringify(catalog, null, 2)}\n`
if (process.argv.includes('--write')) fs.writeFileSync(output, serialized)
else assert.deepEqual(read('apps/api/app/office/catalog.json'), catalog, 'Regenerate and review changed office geometry')
console.log(`PASS: ${stations.length} stations, ${routes.length} original-engine routes, ${negativeCases.length} rejected real routes, ${graph.colliders.length} colliders and ${graph.doors.length} door policies; geometry ${geometryHash}`)
