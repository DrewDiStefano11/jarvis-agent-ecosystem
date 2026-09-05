/* Narrow correction to the pinned prototype: an authorized doorway must remain
 * available to the endpoint connectors as well as the walk-network edges.
 * Geometry, access decisions, collision checks and limits remain authoritative.
 */
const assert = require('node:assert/strict')

module.exports = function preserveDoorConnectorScope(source) {
  const replacements = [
    ['function connectorCandidateValid(graph: CandidateNavigationGraph, from: Point, to: Point, allowedRoomIds: readonly string[]): boolean {', 'function connectorCandidateValid(graph: CandidateNavigationGraph, from: Point, to: Point, allowedRoomIds: readonly string[], allowedDoorIds: readonly string[] = []): boolean {'],
    ['return validateCandidateRouteSegments(graph, [from, to], []) === null;', 'return validateCandidateRouteSegments(graph, [from, to], allowedDoorIds) === null;'],
    ['function candidateWalkEndpointConnectorsForNetwork(\n    graph: CandidateNavigationGraph,\n    network: CandidateWalkNetwork,\n    pointValue: Point,\n    allowedRoomIds: readonly string[],\n)', 'function candidateWalkEndpointConnectorsForNetwork(\n    graph: CandidateNavigationGraph,\n    network: CandidateWalkNetwork,\n    pointValue: Point,\n    allowedRoomIds: readonly string[],\n    allowedDoorIds: readonly string[] = [],\n)'],
    ['connectorCandidateValid(graph, pointValue, candidate.point, allowedRoomIds)', 'connectorCandidateValid(graph, pointValue, candidate.point, allowedRoomIds, allowedDoorIds)'],
    ['candidateWalkEndpointConnectorsForNetwork(graph, network, from, roomIds)', 'candidateWalkEndpointConnectorsForNetwork(graph, network, from, roomIds, allowedDoorIds)'],
    ['candidateWalkEndpointConnectorsForNetwork(graph, network, to, roomIds)', 'candidateWalkEndpointConnectorsForNetwork(graph, network, to, roomIds, allowedDoorIds)'],
  ]
  let corrected = source.replaceAll('\r\n', '\n')
  for (const [before, after] of replacements) {
    assert.equal(corrected.split(before).length, 2, 'Pinned connector source changed; review the bounded fix')
    corrected = corrected.replace(before, after)
  }
  return corrected
}
