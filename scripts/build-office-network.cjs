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
const correctedSource = require('./office-door-connectors.cjs')(source)
const compiled = ts.transpileModule(correctedSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const nav = {}
new Function('require', 'exports', compiled)(name => {
  assert.equal(name, '../../constants')
  return { OFFICE_SOURCE_WIDTH: 8192, OFFICE_SOURCE_HEIGHT: 5460 }
}, nav)
const evidence = read('docs/goal-mode/office-registration.json')
assert.equal(evidence.sourceCommit, '7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd')
const categories = { rooms: 'rooms', positions: 'positions', doors: 'doors', computers: 'computers', interactiveObjects: 'interactive-objects', walls: 'walls', objects: 'objects', walkPaths: 'walk-paths' }
const docs = Object.fromEntries(Object.entries(categories).map(([key, file]) => [key, read(`apps/web/public/assets/office-candidate/${file}.json`)]))
const crossing = read('docs/goal-mode/office-crossing-d42.json')
assert.equal(crossing.sourceCommit, evidence.sourceCommit)
assert.equal(hash(JSON.stringify(docs)), crossing.sourceGeometryHash)
const geometryHash = hash(JSON.stringify({ docs, crossing }))
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
const sourcePoint = (segmentId, point) => {
  const segment = graph.walkSegments.find(item => item.id === segmentId)
  assert.ok(segment && [segment.a, segment.b].some(value => value.x === point.x && value.y === point.y), `Reviewed point lost authored source ${segmentId}`)
}
sourcePoint(crossing.door.sourceSegment, crossing.door.thresholdPoint)
sourcePoint(crossing.bridge.sourceSegmentA, crossing.bridge.a)
sourcePoint(crossing.bridge.sourceSegmentB, crossing.bridge.b)
sourcePoint(crossing.waypoint.sourceSegment, crossing.waypoint.point)
const d42 = graph.doors.find(door => door.id === 'D42')
assert.deepEqual(d42.point, crossing.door.originalPanelCenter)
assert.deepEqual(d42.zoneIds, ['ROOM_SECURITY_AND_GOVERNANCE', 'ROOM_MAIN_CONNECTING_WALKWAY'])
assert.equal(d42.permission, 'general')
assert.equal(d42.apertureRadius, 96)
d42.point = crossing.door.thresholdPoint
assert.equal(nav.candidateSegmentHasStaticClearance(graph, crossing.bridge.a, crossing.bridge.b), true)
assert.ok(Math.abs(Math.hypot(crossing.bridge.a.x - crossing.bridge.b.x, crossing.bridge.a.y - crossing.bridge.b.y) - crossing.bridge.length) < 1e-9)
assert.ok(crossing.bridge.length < 12)
graph.walkSegments.push({ id: crossing.bridge.id, pathId: crossing.bridge.id, a: crossing.bridge.a, b: crossing.bridge.b })
const { sourceSegment: _sourceSegment, ...waypoint } = crossing.waypoint
graph.destinations.push({ ...waypoint, id: `waypoint:${waypoint.id}`, kind: 'waypoint', roomIds: [waypoint.roomId], availability: 'available' })
const selected = [
  ['POSITION_022', 'Models desk A'], ['POSITION_028', 'Models desk B'],
  ['POSITION_030', 'Models review desk A'], ['POSITION_032', 'Models review desk B'],
  ['POSITION_120', 'Focus C desk A'], ['POSITION_121', 'Focus C desk B'],
  ['POSITION_020', 'Security desk by west door'],
]
const stations = selected.map(([id, label]) => {
  const agent = graph.agents.find(value => value.positionId === id)
  assert.ok(agent, `Missing collision-checked station ${id}`)
  assert.equal(nav.candidatePointHasStaticClearance(graph, agent.point), true)
  return { id, label, roomId: agent.roomId, roomName: agent.roomName, point: agent.point }
})
assert.equal(nav.candidatePointHasStaticClearance(graph, waypoint.point), true)
stations.push(waypoint)
const route = (originId, destinationId, routeGraph = graph) => {
  const agent = routeGraph.agents.find(value => value.positionId === (originId === waypoint.id ? 'POSITION_020' : originId))
  const currentPoint = originId === waypoint.id ? waypoint.point : agent.point
  return nav.planCandidateRoute(routeGraph, { destinationId: `${destinationId === waypoint.id ? 'waypoint' : 'position'}:${destinationId}`, agent: { id: agent.id, currentPoint, revision: 0 } })
}
const routes = []
for (const [first, second] of [['POSITION_022', 'POSITION_028'], ['POSITION_030', 'POSITION_032'], ['POSITION_120', 'POSITION_121']]) {
  for (const [originId, destinationId] of [[first, second], [second, first]]) {
    const result = route(originId, destinationId)
    assert.equal(result.status, 'valid', `${originId} -> ${destinationId}: ${result.reason}`)
    assert.equal(nav.validateCandidateRouteSegments(graph, result.points, result.crossedDoorIds), null)
    assert.equal(nav.validateCandidateRouteDoorClearance(result.points, result.doorSteps, graph.doors), null)
    // Existing within-room routes remain unchanged.
    assert.deepEqual(result.crossedDoorIds, [])
    routes.push({ id: `${originId}:${destinationId}`, originId, destinationId, points: result.points, doorIds: result.crossedDoorIds, length: result.length })
  }
}
for (const [originId, destinationId] of [['POSITION_020', waypoint.id], [waypoint.id, 'POSITION_020']]) {
  const result = route(originId, destinationId)
  assert.equal(result.status, 'valid', result.reason)
  assert.equal(result.points.length, 51)
  assert.deepEqual(result.crossedDoorIds, ['D42'])
  assert.equal(result.doorSteps[0].permission, 'general')
  assert.equal(result.doorSteps[0].requiredAction, 'automatic_open')
  assert.equal(nav.validateCandidateRouteSegments(graph, result.points, result.crossedDoorIds), null)
  assert.equal(nav.validateCandidateRouteDoorClearance(result.points, result.doorSteps, graph.doors), null)
  assert.notEqual(nav.validateCandidateRouteSegments(graph, result.points, []), null, 'The wall must block an unapproved door crossing')
  routes.push({ id: `${originId}:${destinationId}`, originId, destinationId, points: result.points, doorIds: result.crossedDoorIds, length: result.length })
}
for (const permission of ['blocked', 'restricted', 'reserved', 'manual_review_required', 'malformed']) {
  const denied = { ...graph, doors: graph.doors.map(door => door.id === 'D42' ? { ...door, permission } : door) }
  assert.notEqual(route('POSITION_020', waypoint.id, denied).status, 'valid', `${permission} D42 must deny transit`)
}
const noJoin = { ...graph, walkSegments: graph.walkSegments.filter(segment => segment.id !== crossing.bridge.id) }
assert.notEqual(route('POSITION_020', waypoint.id, noJoin).status, 'valid', 'The authored stroke gap must not be silently traversed')
const panelCenter = { ...graph, doors: graph.doors.map(door => door.id === 'D42' ? { ...door, point: crossing.door.originalPanelCenter } : door) }
assert.notEqual(route('POSITION_020', waypoint.id, panelCenter).status, 'valid', 'An elevated panel center is not the floor threshold')
const negativeCases = [['POSITION_022', 'POSITION_030', 'blocked'], ['POSITION_022', 'POSITION_120', 'restricted'], ['POSITION_115', 'POSITION_117', 'blocked'], ['POSITION_120', 'POSITION_125', 'blocked']]
for (const [from, to, status] of negativeCases) assert.equal(route(from, to).status, status, `${from} -> ${to} must remain ${status}`)
for (const door of graph.doors.filter(value => value.accessMode === 'blocked')) assert.notEqual(nav.accessOutcome(door), 'allowed')
const catalog = { version: 'floor1-live-v2-d42', sourceCommit: evidence.sourceCommit, geometryHash,
  reviewScope: 'Eight verified stations: six original local desks plus Security desk and west walkway. One bidirectional general-access D42 crossing; other cross-room geometry remains unavailable.',
  stations, routes, spriteIds: ['agent-sheet-01', 'agent-sheet-06'] }
const output = path.join(root, 'apps/api/app/office/catalog.json')
const serialized = `${JSON.stringify(catalog, null, 2)}\n`
if (process.argv.includes('--write')) fs.writeFileSync(output, serialized)
else assert.deepEqual(read('apps/api/app/office/catalog.json'), catalog, 'Regenerate and review changed office geometry')
console.log(`PASS: ${stations.length} stations, ${routes.length} original-engine routes, ${negativeCases.length} rejected real routes, ${graph.colliders.length} colliders and ${graph.doors.length} door policies; geometry ${geometryHash}`)
