import { OFFICE_SOURCE_HEIGHT, OFFICE_SOURCE_WIDTH } from '../../constants';
import type { Point } from '../../types';

export type CandidateAccessOutcome = 'allowed' | 'blocked' | 'restricted' | 'reserved' | 'manual-review-required' | 'malformed-door';
export type CandidateRouteStatus = 'valid' | 'blocked' | 'restricted' | 'unreachable' | 'malformed';
export type CandidateDestinationKind = 'position' | 'room' | 'computer' | 'interactive-object' | 'waypoint';

export type CandidateAgentFixture = Readonly<{
    id: string;
    label: string;
    positionId: string;
    roomId: string;
    roomIds: readonly string[];
    roomName: string;
    point: Point;
    accessTier: 'standard' | 'priority';
    spriteAssetId: string;
    provisionalSpriteAssignment: true;
}>;

export type CandidateDestination = Readonly<{
    id: string;
    label: string;
    kind: CandidateDestinationKind;
    point: Point;
    roomId: string;
    roomIds: readonly string[];
    roomName: string;
    accessTier?: 'standard' | 'priority';
    markerPoint?: Point;
    approachPositionId?: string;
    approachAccessTier?: 'standard' | 'priority';
    approachResolution?: 'position-anchor' | 'walk-node' | 'walk-segment';
    approachAnchorId?: string;
    approachDistance?: number;
    availability?: 'available' | 'unavailable';
    unavailableReason?: string;
    roomAnchorResolution?: 'position-anchor' | 'walk-node' | 'walk-segment' | 'polygon-interior';
    roomAnchorSourceId?: string;
    roomAnchorSourceTier?: 'standard' | 'priority';
}>;

export type CandidateRouteRequest = Readonly<{
    destinationId: string;
    agent: Readonly<{
        id: string;
        currentPoint: Point;
        revision: number;
    }>;
}>;

export type RegistrationLandmark = Readonly<{
    id: string;
    markup: Point;
    source: Point;
    residualErrorPixels: number;
}>;

export type MarkupRegistration = Readonly<{
    sourceWidth: 8192;
    sourceHeight: 5460;
    markupWidth: number;
    markupHeight: number;
    scale: number;
    offsetX: number;
    offsetY: number;
    rotationDegrees: 0;
    status: 'unverified' | 'review_required' | 'approved';
    approvalStatus?: 'candidate_unverified' | 'candidate_review_required' | 'candidate_reviewed' | 'approved';
    storedCoordinateSpace?: 'raw_markup' | 'registered_candidate_source';
    productionApproved?: false;
    registrationLandmarks: readonly RegistrationLandmark[];
    maximumResidualErrorPixels: number;
    provenance?: Readonly<{
        generator: string;
        generatedArtifact: string;
        sourceEvidence: readonly string[];
    }>;
}>;

export type CandidateNavigationBuildOptions = Readonly<{
    registration?: MarkupRegistration | null;
    instrumentation?: CandidateGraphBuildInstrumentation;
}>;

export type CandidateGraphVerificationMode = 'reviewed' | 'unverified-sandbox';

export type CandidateDoorPermission = 'general' | 'restricted' | 'reserved' | 'blocked' | 'elevator' | 'manual_review_required' | 'malformed';
export type CandidateDoorPhysicalState = 'closed' | 'opening' | 'open' | 'closing' | 'waiting' | 'unavailable';
export type CandidateDoorStep = Readonly<{ doorId: string; permission: CandidateDoorPermission; initialPhysicalState: CandidateDoorPhysicalState; requiredAction: 'automatic_open' | 'wait_for_open' | 'elevator_call' | 'unavailable' | 'none'; approachPoint: Point; thresholdPoint: Point; exitPoint: Point; approachDistance: number; thresholdDistance: number; exitDistance: number; clearanceReleaseDistance: number; }>;
export type CandidateDoorRuntime = Readonly<{ doorId: string; state: CandidateDoorPhysicalState; stateElapsedMs: number; requestedByAgentId?: string; revision: number }>;
export const CANDIDATE_DOOR_OPEN_MS = 400;
export const CANDIDATE_DOOR_HOLD_MS = 600;
export const CANDIDATE_DOOR_CLOSE_MS = 400;

export type CandidateDoorNode = Readonly<{
    id: string;
    point: Point;
    zones: readonly string[];
    zoneIds: readonly string[];
    accessMode: string;
    permission?: CandidateDoorPermission;
    defaultState?: string;
    currentState?: CandidateDoorPhysicalState;
    openRule?: string;
    closeRule?: string;
    collisionRule?: string;
    elevatorRule?: string;
    manualReviewRequired: boolean;
    apertureRadius: number;
    malformedReason?: string;
}>;

export type CandidateCollider = Readonly<{
    id: string;
    kind: 'wall' | 'object';
    points: readonly Point[];
    closed: boolean;
    thickness: number;
}>;

export type CandidateWalkNode = Readonly<{
    id: string;
    point: Point;
    roomId: string;
    roomIds: readonly string[];
    pathId: string;
}>;

export type CandidateWalkSegment = Readonly<{
    id: string;
    a: Point;
    b: Point;
    pathId: string;
}>;

export type CandidateNavigationGraph = Readonly<{
    verificationMode: CandidateGraphVerificationMode;
    rooms: readonly CandidateRoom[];
    doors: readonly CandidateDoorNode[];
    agents: readonly CandidateAgentFixture[];
    destinations: readonly CandidateDestination[];
    colliders: readonly CandidateCollider[];
    walkNodes: readonly CandidateWalkNode[];
    walkSegments: readonly CandidateWalkSegment[];
    roomDiagnostics: readonly string[];
    nodeCount: number;
    edgeCount: number;
    navigationAvailable: boolean;
    unavailableReason?: string;
}>;

export type CandidateRouteResult = Readonly<{
    status: CandidateRouteStatus;
    reason: string;
    points: readonly Point[];
    crossedDoorIds: readonly string[];
    doorSteps: readonly CandidateDoorStep[];
    nodeSequence: readonly string[];
    cost: number;
    length: number;
    expandedNodeCount: number;
    failureCategory?: string;
}>;

type UnknownRecord = Record<string, unknown>;
type CandidateRoom = Readonly<{ id: string; name: string; polygon: readonly Point[]; center: Point }>;

type CandidateDocuments = Readonly<{
    rooms: unknown;
    positions: unknown;
    doors: unknown;
    computers: unknown;
    interactiveObjects: unknown;
    walls: unknown;
    objects: unknown;
    walkPaths?: unknown;
}>;

type NativePath = Readonly<{ id: string; points: readonly Point[]; thickness: number; closed: boolean }>;
type CandidatePositionRecord = Readonly<{ id: string; point: Point; tier: 'standard' | 'priority'; room: CandidateRoom; roomIds: readonly string[] }>;
type CandidatePositionEvaluation = Readonly<{ position: CandidatePositionRecord; bounded: boolean; collisionFree: boolean; directWalkSupport: boolean; connectorSupported: boolean; nearestWalkNodeId?: string; roomIds: readonly string[] }>;
type CandidateGraphBuildInstrumentation = { positionCollisionEvaluations: number; positionWalkEvaluations: number };

const MAX_ROUTE_POINTS = 160;
const MAX_EXPANDED_NODES = 1_024;
const MAX_SAMPLED_WALK_NODES = 1_600;
const SAFE_REASON_LIMIT = 180;
const SPRITE_SHEET_COUNT = 16;
const DOOR_APERTURE_RADIUS = 96;
export const AGENT_FOOTPRINT_RADIUS = 34;
const CONNECTOR_SEARCH_LIMIT = 18;
const CONNECTOR_MAX_DISTANCE = 420;
const CONNECTOR_INGRESS_DISTANCE = 180;
const WALK_SUPPORT_RADIUS = 260;
const WALK_SAMPLE_INTERVAL = 96;
const MAX_FRAME_DELTA_MS = 100;
export const INTERACTIVE_APPROACH_MAX_DISTANCE = CONNECTOR_MAX_DISTANCE;
const REGISTRATION_RESIDUAL_EPSILON_PX = 0.001;
const ROUTE_PROGRESS_EPSILON = 0.001;

const DEFAULT_CANDIDATE_REGISTRATION: MarkupRegistration = {
    sourceWidth: 8192,
    sourceHeight: 5460,
    markupWidth: 6144,
    markupHeight: 4096,
    scale: 1.3333333333333333,
    offsetX: 0,
    offsetY: -0.6666666666665151,
    rotationDegrees: 0,
    status: 'unverified',
    approvalStatus: 'candidate_unverified',
    storedCoordinateSpace: 'raw_markup',
    productionApproved: false,
    registrationLandmarks: [],
    maximumResidualErrorPixels: Number.POSITIVE_INFINITY,
};

function validateRegistrationShape(registration: MarkupRegistration | null | undefined): string | null {
    if (!registration) return 'Candidate navigation unavailable: Floor 1 markup registration is missing.';
    if (registration.sourceWidth !== 8192 || registration.sourceHeight !== 5460) return 'Candidate navigation unavailable: Floor 1 registration source dimensions are invalid.';
    if (!Number.isFinite(registration.markupWidth) || !Number.isFinite(registration.markupHeight) || registration.markupWidth <= 0 || registration.markupHeight <= 0) return 'Candidate navigation unavailable: Floor 1 registration markup dimensions are invalid.';
    if (!Number.isFinite(registration.scale) || registration.scale <= 0) return 'Candidate navigation unavailable: Floor 1 registration scale is invalid.';
    if (!Number.isFinite(registration.offsetX) || !Number.isFinite(registration.offsetY)) return 'Candidate navigation unavailable: Floor 1 registration offsets are invalid.';
    if (registration.rotationDegrees !== 0) return 'Candidate navigation unavailable: Floor 1 registration rotation is unsupported.';
    if (registration.storedCoordinateSpace !== 'raw_markup' && registration.storedCoordinateSpace !== 'registered_candidate_source') return 'Candidate navigation unavailable: Floor 1 stored coordinate space is unknown.';
    return null;
}

export type CandidateRegistrationResidualResult = Readonly<{
    landmarkResiduals: readonly Readonly<{
        id: string;
        expectedSource: Point;
        actualSource: Point;
        computedResidualPixels: number;
        declaredResidualPixels: number;
    }>[];
    computedMaximumResidualPixels: number;
}>;

export function computeCandidateRegistrationResiduals(registration: MarkupRegistration): CandidateRegistrationResidualResult {
    const landmarkResiduals = registration.registrationLandmarks.map(landmark => {
        const expectedSource = {
            x: landmark.markup.x * registration.scale + registration.offsetX,
            y: landmark.markup.y * registration.scale + registration.offsetY,
        };
        return {
            id: landmark.id,
            expectedSource,
            actualSource: landmark.source,
            computedResidualPixels: Math.hypot(expectedSource.x - landmark.source.x, expectedSource.y - landmark.source.y),
            declaredResidualPixels: landmark.residualErrorPixels,
        };
    });
    return {
        landmarkResiduals,
        computedMaximumResidualPixels: landmarkResiduals.reduce((maximum, residual) => Math.max(maximum, residual.computedResidualPixels), 0),
    };
}

function withinRegistrationResidualEpsilon(a: number, b: number): boolean {
    return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= REGISTRATION_RESIDUAL_EPSILON_PX;
}

function validateDistributedLandmarks(registration: MarkupRegistration): string | null {
    const landmarks = registration.registrationLandmarks;
    if (!Array.isArray(landmarks) || landmarks.length < 4) return 'Candidate navigation unavailable: registration_landmarks_insufficient.';
    const ids = new Set<string>();
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const landmark of landmarks) {
        if (!landmark.id || ids.has(landmark.id)) return 'Candidate navigation unavailable: registration_landmark_invalid.';
        ids.add(landmark.id);
        const markupBounded = Number.isFinite(landmark.markup.x) && Number.isFinite(landmark.markup.y)
            && landmark.markup.x >= 0 && landmark.markup.y >= 0
            && landmark.markup.x <= registration.markupWidth && landmark.markup.y <= registration.markupHeight;
        if (!bounded(landmark.source) || !markupBounded) return 'Candidate navigation unavailable: registration_landmark_invalid.';
        if (!Number.isFinite(landmark.residualErrorPixels) || landmark.residualErrorPixels < 0) return 'Candidate navigation unavailable: registration_residual_invalid.';
        minX = Math.min(minX, landmark.source.x); maxX = Math.max(maxX, landmark.source.x);
        minY = Math.min(minY, landmark.source.y); maxY = Math.max(maxY, landmark.source.y);
    }
    if (maxX - minX < registration.sourceWidth * 0.35 || maxY - minY < registration.sourceHeight * 0.35) return 'Candidate navigation unavailable: registration_landmarks_not_distributed.';
    return null;
}

export function validateCandidateReviewRegistration(registration: MarkupRegistration | null | undefined): string | null {
    const shapeFailure = validateRegistrationShape(registration);
    if (shapeFailure) return shapeFailure;
    if (!registration) return 'Candidate navigation unavailable: Floor 1 markup registration is missing.';
    if (registration.productionApproved !== false) return 'Candidate navigation unavailable: Floor 1 candidate registration crossed the production boundary.';
    if (!registration.provenance?.generator || !registration.provenance.generatedArtifact || registration.provenance.sourceEvidence.length === 0) return 'Candidate navigation unavailable: Floor 1 candidate registration provenance is missing.';
    if (registration.approvalStatus !== 'candidate_reviewed') return 'Candidate navigation unavailable: registration review required.';
    const landmarkFailure = validateDistributedLandmarks(registration);
    if (landmarkFailure) return landmarkFailure;
    if (!Number.isFinite(registration.maximumResidualErrorPixels) || registration.maximumResidualErrorPixels < 0) return 'Candidate navigation unavailable: registration_residual_invalid.';
    const residuals = computeCandidateRegistrationResiduals(registration);
    for (const residual of residuals.landmarkResiduals) {
        if (!Number.isFinite(residual.computedResidualPixels)) return 'Candidate navigation unavailable: registration_residual_invalid.';
        if (!withinRegistrationResidualEpsilon(residual.declaredResidualPixels, residual.computedResidualPixels)) return 'Candidate navigation unavailable: registration_residual_mismatch.';
    }
    if (!withinRegistrationResidualEpsilon(registration.maximumResidualErrorPixels, residuals.computedMaximumResidualPixels)) return 'Candidate navigation unavailable: registration_maximum_residual_mismatch.';
    if (residuals.computedMaximumResidualPixels > 8) return 'Candidate navigation unavailable: registration_residual_exceeds_tolerance.';
    return null;
}

export function validateMarkupRegistration(registration: MarkupRegistration | null | undefined): string | null {
    const shapeFailure = validateRegistrationShape(registration);
    if (shapeFailure) return shapeFailure;
    if (!registration) return 'Candidate navigation unavailable: Floor 1 markup registration is missing.';
    if (registration.status !== 'approved' || registration.approvalStatus !== 'approved') return 'Candidate navigation unavailable: Floor 1 markup registration is not approved.';
    if (!Array.isArray(registration.registrationLandmarks) || registration.registrationLandmarks.length === 0) return 'Candidate navigation unavailable: Floor 1 registration landmark evidence is missing.';
    if (!Number.isFinite(registration.maximumResidualErrorPixels) || registration.maximumResidualErrorPixels < 0) return 'Candidate navigation unavailable: Floor 1 registration residual is invalid.';
    return null;
}

export function transformMarkupPoint(pointValue: Point, registration: MarkupRegistration): Point {
    if (registration.storedCoordinateSpace === 'registered_candidate_source') return pointValue;
    return {
        x: pointValue.x * registration.scale + registration.offsetX,
        y: pointValue.y * registration.scale + registration.offsetY,
    };
}

function transformMarkupPoints(pointValues: readonly Point[], registration: MarkupRegistration): Point[] {
    return pointValues.map(pointValue => transformMarkupPoint(pointValue, registration));
}

function transformMarkupWidth(width: number, registration: MarkupRegistration): number {
    return registration.storedCoordinateSpace === 'registered_candidate_source' ? width : width * registration.scale;
}

function unavailableGraph(reason: string, verificationMode: CandidateGraphVerificationMode): CandidateNavigationGraph {
    return { verificationMode, rooms: [], doors: [], agents: [], destinations: [], colliders: [], walkNodes: [], walkSegments: [], roomDiagnostics: [reason], nodeCount: 0, edgeCount: 0, navigationAvailable: false, unavailableReason: reason };
}

function record(value: unknown, context: string): UnknownRecord {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${context} is malformed.`);
    return value as UnknownRecord;
}

function array(value: unknown, context: string): unknown[] {
    if (!Array.isArray(value)) throw new Error(`${context} must be an array.`);
    return value;
}

function finite(value: unknown, context: string): number {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${context} must be finite.`);
    return value;
}

function text(value: unknown, fallback: string): string {
    return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function point(value: unknown, context: string): Point {
    const item = record(value, context);
    return { x: finite(item.x, `${context}.x`), y: finite(item.y, `${context}.y`) };
}

function points(value: unknown, context: string): Point[] {
    return array(value, context).map((item, index) => point(item, `${context}[${index}]`));
}

function wrapperData(value: unknown, context: string): UnknownRecord {
    const wrapper = record(value, context);
    if (wrapper.productionApproved !== false || wrapper.registrationStatus !== 'candidate-unverified') {
        throw new Error(`${context} is not candidate-only provisional data.`);
    }
    return record(wrapper.data, `${context}.data`);
}

function bounded(pointValue: Point): boolean {
    return Number.isFinite(pointValue.x) && Number.isFinite(pointValue.y)
        && pointValue.x >= 0 && pointValue.y >= 0
        && pointValue.x <= OFFICE_SOURCE_WIDTH && pointValue.y <= OFFICE_SOURCE_HEIGHT;
}

function centroid(polygon: readonly Point[]): Point {
    if (polygon.length === 0) return { x: 0, y: 0 };
    const sum = polygon.reduce((acc, item) => ({ x: acc.x + item.x, y: acc.y + item.y }), { x: 0, y: 0 });
    return { x: sum.x / polygon.length, y: sum.y / polygon.length };
}

export function pointInPolygon(target: Point, polygon: readonly Point[]): boolean {
    if (polygon.length < 3) return false;
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const a = polygon[i];
        const b = polygon[j];
        const intersects = (a.y > target.y) !== (b.y > target.y)
            && target.x < ((b.x - a.x) * (target.y - a.y)) / ((b.y - a.y) || Number.EPSILON) + a.x;
        if (intersects) inside = !inside;
    }
    return inside;
}

function distance(a: Point, b: Point): number {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

function routeLength(pointsIn: readonly Point[]): number {
    return pointsIn.slice(1).reduce((acc, item, index) => acc + distance(pointsIn[index], item), 0);
}

function orientation(a: Point, b: Point, c: Point): number {
    const value = (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y);
    if (Math.abs(value) < 1e-7) return 0;
    return Math.sign(value);
}

function onSegment(a: Point, b: Point, c: Point): boolean {
    return Math.min(a.x, c.x) - 1e-7 <= b.x && b.x <= Math.max(a.x, c.x) + 1e-7
        && Math.min(a.y, c.y) - 1e-7 <= b.y && b.y <= Math.max(a.y, c.y) + 1e-7;
}

export function segmentsIntersect(a: Point, b: Point, c: Point, d: Point): boolean {
    const o1 = orientation(a, b, c);
    const o2 = orientation(a, b, d);
    const o3 = orientation(c, d, a);
    const o4 = orientation(c, d, b);
    if (o1 !== o2 && o3 !== o4) return true;
    return (o1 === 0 && onSegment(a, c, b))
        || (o2 === 0 && onSegment(a, d, b))
        || (o3 === 0 && onSegment(c, a, d))
        || (o4 === 0 && onSegment(c, b, d));
}

function pointSegmentDistance(pointValue: Point, a: Point, b: Point): number {
    const lengthSquared = ((b.x - a.x) ** 2) + ((b.y - a.y) ** 2);
    if (lengthSquared === 0) return distance(pointValue, a);
    const t = Math.max(0, Math.min(1, ((pointValue.x - a.x) * (b.x - a.x) + (pointValue.y - a.y) * (b.y - a.y)) / lengthSquared));
    return distance(pointValue, { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) });
}

function closestPointOnSegment(pointValue: Point, a: Point, b: Point): Point {
    const lengthSquared = ((b.x - a.x) ** 2) + ((b.y - a.y) ** 2);
    if (lengthSquared === 0) return a;
    const t = Math.max(0, Math.min(1, ((pointValue.x - a.x) * (b.x - a.x) + (pointValue.y - a.y) * (b.y - a.y)) / lengthSquared));
    return { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) };
}

function segmentDistance(a: Point, b: Point, c: Point, d: Point): number {
    if (segmentsIntersect(a, b, c, d)) return 0;
    return Math.min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d), pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b));
}

function lineIntersectionPoint(a: Point, b: Point, c: Point, d: Point): Point | null {
    const denominator = (a.x - b.x) * (c.y - d.y) - (a.y - b.y) * (c.x - d.x);
    if (Math.abs(denominator) < 1e-7) return null;
    const x = ((a.x * b.y - a.y * b.x) * (c.x - d.x) - (a.x - b.x) * (c.x * d.y - c.y * d.x)) / denominator;
    const y = ((a.x * b.y - a.y * b.x) * (c.y - d.y) - (a.y - b.y) * (c.x * d.y - c.y * d.x)) / denominator;
    const pointValue = { x, y };
    return onSegment(a, pointValue, b) && onSegment(c, pointValue, d) ? pointValue : null;
}

function colliderIntersections(a: Point, b: Point, collider: CandidateCollider): Point[] {
    const hits: Point[] = [];
    if (collider.closed && (pointInPolygon(a, collider.points) || pointInPolygon(b, collider.points))) hits.push(a);
    const segmentCount = collider.closed ? collider.points.length : collider.points.length - 1;
    for (let i = 0; i < segmentCount; i += 1) {
        const c = collider.points[i];
        const d = collider.points[(i + 1) % collider.points.length];
        const threshold = collider.thickness / 2 + AGENT_FOOTPRINT_RADIUS;
        if (segmentDistance(a, b, c, d) <= threshold) {
            const intersection = lineIntersectionPoint(a, b, c, d);
            if (intersection) {
                hits.push(intersection);
            } else {
                if (pointSegmentDistance(a, c, d) <= threshold) hits.push(a);
                if (pointSegmentDistance(b, c, d) <= threshold) hits.push(b);
                if (pointSegmentDistance(c, a, b) <= threshold) hits.push(closestPointOnSegment(c, a, b));
                if (pointSegmentDistance(d, a, b) <= threshold) hits.push(closestPointOnSegment(d, a, b));
            }
        }
    }
    return hits;
}

function nativePathsFromRecord(source: UnknownRecord, context: string, registration: MarkupRegistration): NativePath[] {
    const native = record(source.nativeGeometry, `${context}.nativeGeometry`);
    const sourceId = text(source.id, context);
    const style = source.style && typeof source.style === 'object' ? record(source.style, `${context}.style`) : {};
    const rawWidth = typeof style.width === 'number' && Number.isFinite(style.width) ? style.width * 16 / 9 : 10;
    const thickness = Math.max(8, Math.min(96, transformMarkupWidth(rawWidth, registration)));
    if (native.kind === 'polygon') return [{ id: sourceId, points: transformMarkupPoints(points(native.points, `${context}.points`), registration), thickness, closed: true }];
    if (native.kind === 'rectangle') {
        const rect = record(native.rect, `${context}.rect`);
        const x1 = finite(rect.x1, `${context}.rect.x1`);
        const x2 = finite(rect.x2, `${context}.rect.x2`);
        const y1 = finite(rect.y1, `${context}.rect.y1`);
        const y2 = finite(rect.y2, `${context}.rect.y2`);
        const rectPoints = transformMarkupPoints([{ x: x1, y: y1 }, { x: x2, y: y1 }, { x: x2, y: y2 }, { x: x1, y: y2 }], registration);
        rectPoints.forEach((item, index) => { if (!bounded(item)) throw new Error(`${context}.rect[${index}] is out of bounds.`); });
        return [{ id: sourceId, points: rectPoints, thickness, closed: true }];
    }
    if (native.kind === 'ink') {
        return array(native.paths, `${context}.paths`).map((pathValue, index) => {
            const pathPoints = transformMarkupPoints(points(pathValue, `${context}.paths[${index}]`), registration).filter((item, pointIndex, all) => pointIndex === 0 || distance(item, all[pointIndex - 1]) > 0.001);
            if (pathPoints.length < 2) throw new Error(`${context}.paths[${index}] must contain at least two bounded points.`);
            const closed = pathPoints.length >= 3 && distance(pathPoints[0], pathPoints[pathPoints.length - 1]) <= Math.max(4, thickness);
            return { id: `${sourceId}:path:${String(index + 1).padStart(2, '0')}`, points: pathPoints, thickness, closed };
        });
    }
    return [];
}

function roomMembershipsForPoint(rooms: readonly CandidateRoom[], value: Point): readonly CandidateRoom[] {
    return rooms.filter(room => pointInPolygon(value, room.polygon)).sort((a, b) => a.id.localeCompare(b.id));
}


function membershipIds(rooms: readonly CandidateRoom[], value: Point): readonly string[] {
    return roomMembershipsForPoint(rooms, value).map(room => room.id);
}

function normalizeZone(value: string): string {
    return value.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
}

function makeZoneResolver(rooms: readonly CandidateRoom[]) {
    const byName = new Map<string, CandidateRoom[]>();
    rooms.forEach(room => {
        const key = normalizeZone(room.name);
        byName.set(key, [...(byName.get(key) ?? []), room]);
    });
    const diagnostics: string[] = [];
    const resolve = (zoneName: string): string => {
        const key = normalizeZone(zoneName);
        const exact = byName.get(key) ?? [];
        if (exact.length === 1) return exact[0].id;
        if (exact.length > 1) {
            diagnostics.push(`Ambiguous room name "${zoneName}" maps to ${exact.map(room => room.id).join(', ')}.`);
            return `ambiguous:${key}`;
        }
        const partial = rooms.filter(room => key.includes(normalizeZone(room.name)) || normalizeZone(room.name).includes(key));
        if (partial.length === 1) return partial[0].id;
        if (partial.length > 1) {
            diagnostics.push(`Ambiguous door zone "${zoneName}" maps to ${partial.map(room => room.id).join(', ')}.`);
            return `ambiguous:${key}`;
        }
        diagnostics.push(`Door zone "${zoneName}" has no stable room ID mapping; retaining provisional zone ID.`);
        return `zone:${key || 'unresolved'}`;
    };
    return { resolve, diagnostics };
}

export function accessOutcome(door: CandidateDoorNode): CandidateAccessOutcome {
    if (door.malformedReason || !door.id || door.zoneIds.length < 2 || door.zoneIds.some(zone => zone.startsWith('ambiguous:'))) return 'malformed-door';
    const permission = door.permission ?? (door.manualReviewRequired ? 'manual_review_required' : door.accessMode === 'open' ? 'general' : door.accessMode === 'restricted' ? 'restricted' : door.accessMode === 'event' ? 'reserved' : door.accessMode === 'elevator' ? 'elevator' : door.accessMode === 'blocked' ? 'blocked' : 'malformed');
    if (permission === 'manual_review_required') return 'manual-review-required';
    if (permission === 'general') return 'allowed';
    if (permission === 'elevator') return 'blocked';
    if (permission === 'blocked') return 'blocked';
    if (permission === 'restricted') return 'restricted';
    if (permission === 'reserved') return 'reserved';
    return 'malformed-door';
}

function safeReason(reason: string): string {
    return reason.length <= SAFE_REASON_LIMIT ? reason : `${reason.slice(0, SAFE_REASON_LIMIT - 1)}…`;
}

function destinationSort(a: CandidateDestination, b: CandidateDestination): number {
    const kindOrder: Record<CandidateDestinationKind, number> = { room: 0, computer: 1, 'interactive-object': 2, position: 3, waypoint: 4 };
    return kindOrder[a.kind] - kindOrder[b.kind]
        || (a.accessTier ?? '').localeCompare(b.accessTier ?? '')
        || a.label.localeCompare(b.label)
        || a.id.localeCompare(b.id);
}


export function isCandidateAgentSpawnEligible(evaluation: CandidatePositionEvaluation): boolean {
    return evaluation.bounded && evaluation.collisionFree && evaluation.connectorSupported && evaluation.roomIds.length > 0;
}

function selectCandidateAgentPositions(evaluations: Iterable<CandidatePositionEvaluation>): CandidatePositionRecord[] {
    const eligible = [...evaluations].filter(isCandidateAgentSpawnEligible).map(evaluation => evaluation.position);
    const selected: CandidatePositionRecord[] = [];
    for (const item of eligible.filter(position => position.tier === 'priority').sort((a, b) => a.id.localeCompare(b.id))) {
        if (selected.length >= 4) break;
        if (selected.every(existing => distance(existing.point, item.point) >= 38)) selected.push(item);
    }
    for (const item of eligible.filter(position => position.tier === 'standard').sort((a, b) => a.id.localeCompare(b.id))) {
        if (selected.length >= 32) break;
        if (selected.every(existing => distance(existing.point, item.point) >= 38)) selected.push(item);
    }
    for (const item of eligible.filter(position => position.tier === 'priority').sort((a, b) => a.id.localeCompare(b.id))) {
        if (selected.length >= 40) break;
        if (!selected.includes(item) && selected.every(existing => distance(existing.point, item.point) >= 38)) selected.push(item);
    }
    return selected;
}

function buildCandidateAgents(selectedPositions: readonly CandidatePositionRecord[]): CandidateAgentFixture[] {
    return selectedPositions.map((item, index): CandidateAgentFixture => ({
        id: `floor1-review-agent-${String(index + 1).padStart(2, '0')}`,
        label: `Review agent ${String(index + 1).padStart(2, '0')}`,
        positionId: item.id,
        roomId: item.room.id,
        roomIds: item.roomIds,
        roomName: item.room.name,
        point: item.point,
        accessTier: item.tier,
        spriteAssetId: `agent-sheet-${String((index % SPRITE_SHEET_COUNT) + 1).padStart(2, '0')}`,
        provisionalSpriteAssignment: true,
    }));
}

export function validateCandidateSandboxRegistration(registration: MarkupRegistration | null | undefined): string | null {
    const shapeFailure = validateRegistrationShape(registration);
    if (shapeFailure) return shapeFailure;
    if (!registration) return 'Candidate sandbox unavailable: Floor 1 markup registration is missing.';
    if (registration.productionApproved !== false) return 'Candidate sandbox unavailable: registration crossed the production boundary.';
    if (registration.status !== 'unverified' && registration.status !== 'review_required') return 'Candidate sandbox unavailable: registration is not provisional.';
    if (!['candidate_unverified', 'candidate_review_required', 'candidate_reviewed'].includes(registration.approvalStatus ?? '')) return 'Candidate sandbox unavailable: provisional approval status is invalid.';
    if (!registration.provenance?.generator || !registration.provenance.generatedArtifact || registration.provenance.sourceEvidence.length === 0) return 'Candidate sandbox unavailable: registration provenance is missing.';
    return null;
}

export function buildCandidateSandboxGraph(documents: CandidateDocuments, registration: MarkupRegistration): CandidateNavigationGraph {
    const registrationFailure = validateCandidateSandboxRegistration(registration);
    if (registrationFailure) return unavailableGraph(registrationFailure, 'unverified-sandbox');
    return constructCandidateNavigationGraph(documents, registration, {}, 'unverified-sandbox');
}

export function buildCandidateNavigationGraph(documents: CandidateDocuments, options: CandidateNavigationBuildOptions = {}): CandidateNavigationGraph {
    const registration = options.registration ?? DEFAULT_CANDIDATE_REGISTRATION;
    const registrationFailure = validateCandidateReviewRegistration(registration);
    if (registrationFailure) return unavailableGraph(registrationFailure, 'reviewed');
    return constructCandidateNavigationGraph(documents, registration, options, 'reviewed');
}

function constructCandidateNavigationGraph(
    documents: CandidateDocuments,
    registration: MarkupRegistration,
    options: CandidateNavigationBuildOptions,
    verificationMode: CandidateGraphVerificationMode,
): CandidateNavigationGraph {
    const roomData = wrapperData(documents.rooms, 'rooms');
    const positionData = wrapperData(documents.positions, 'positions');
    const doorData = wrapperData(documents.doors, 'doors');
    const computerData = wrapperData(documents.computers, 'computers');
    const interactiveData = wrapperData(documents.interactiveObjects, 'interactive-objects');
    const wallData = wrapperData(documents.walls, 'walls');
    const objectData = wrapperData(documents.objects, 'objects');
    const walkPathData = documents.walkPaths ? wrapperData(documents.walkPaths, 'walk-paths') : { records: [] };

    const rooms: CandidateRoom[] = array(roomData.rooms, 'rooms').map((value, index) => {
        const item = record(value, `room[${index}]`);
        const polygon = transformMarkupPoints(points(item.pdfPolygon, `room[${index}].pdfPolygon`), registration);
        return { id: text(item.id, `ROOM_${index + 1}`), name: text(item.canonicalName, `Room ${index + 1}`), polygon, center: centroid(polygon) };
    }).sort((a, b) => a.id.localeCompare(b.id));
    const zoneResolver = makeZoneResolver(rooms);

    const positions = array(positionData.positions, 'positions').map((value, index) => {
        const item = record(value, `position[${index}]`);
        const candidatePoint = transformMarkupPoint(point(item.pdfAnchor, `position[${index}].pdfAnchor`), registration);
        const memberships = roomMembershipsForPoint(rooms, candidatePoint);
        const room = memberships[0];
        if (!room) return null;
        return { id: text(item.id, `POSITION_${String(index + 1).padStart(3, '0')}`), point: candidatePoint, tier: item.accessTier === 'priority' ? 'priority' as const : 'standard' as const, room, roomIds: memberships.map(itemRoom => itemRoom.id) };
    }).filter((item): item is NonNullable<typeof item> => item !== null && bounded(item.point));

    const positionDestinations = positions.map((item): CandidateDestination => ({ id: `position:${item.id}`, label: `${item.id} (${item.tier})`, kind: 'position', point: item.point, roomId: item.room.id, roomIds: item.roomIds, roomName: item.room.name, accessTier: item.tier }));
    const computerRecords = array(computerData.records, 'computers.records');
    const interactiveRecords = array(interactiveData.interactiveObjects, 'interactiveObjects');

    const doors = array(doorData.doors, 'doors').map((value, index): CandidateDoorNode => {
        const item = record(value, `door[${index}]`);
        const facts = record(item.authoredFacts, `door[${index}].authoredFacts`);
        const polygon = transformMarkupPoints(points(item.pdfPolygon, `door[${index}].pdfPolygon`), registration);
        const zones = [text(facts.zone_a, ''), text(facts.zone_b, '')].filter(Boolean);
        const zoneIds = zones.map(zoneResolver.resolve);
        const accessMode = text(item.csvAccessMode, text(facts.access_mode, ''));
        const manualReviewRequired = item.manualReviewRequired === true || text(facts.manual_review_required, 'no') === 'yes';
        const permission: CandidateDoorPermission = manualReviewRequired ? 'manual_review_required'
            : accessMode === 'open' ? 'general'
                : accessMode === 'restricted' ? 'restricted'
                    : accessMode === 'event' ? 'reserved'
                        : accessMode === 'blocked' ? 'blocked'
                            : accessMode === 'elevator' ? 'elevator'
                                : 'malformed';
        const defaultState = text(item.csvDefaultState, text(facts.default_door_state, 'closed'));
        return {
            id: text(item.id, `D${String(index + 1).padStart(2, '0')}`),
            point: centroid(polygon),
            zones,
            zoneIds,
            accessMode,
            permission,
            defaultState,
            currentState: defaultState.includes('open') ? 'open' : defaultState.includes('unavailable') ? 'unavailable' : 'closed',
            openRule: text(facts.door_open_rule, ''),
            closeRule: text(facts.door_close_rule, ''),
            collisionRule: text(facts.collision_and_pathfinding_rule, ''),
            elevatorRule: text(facts.elevator_rule, ''),
            manualReviewRequired,
            apertureRadius: DOOR_APERTURE_RADIUS,
            malformedReason: polygon.length < 3 || zoneIds.length < 2 ? 'Malformed doorway geometry or zone association.' : undefined,
        };
    }).sort((a, b) => a.id.localeCompare(b.id));

    const colliders: CandidateCollider[] = [];
    for (const [kind, data] of [['wall', wallData], ['object', objectData]] as const) {
        array(data.records, `${kind}.records`).forEach((value, index) => {
            const item = record(value, `${kind}[${index}]`);
            nativePathsFromRecord(item, `${kind}[${index}]`, registration).forEach(path => {
                if (path.points.length >= 2) colliders.push({ id: `${kind}:${path.id}`, kind, points: path.points, closed: path.closed, thickness: path.thickness });
            });
        });
    }

    const walkNodes: CandidateWalkNode[] = [];
    const walkSegments: CandidateWalkSegment[] = [];
    array(walkPathData.records, 'walk-paths.records').forEach((value, index) => {
        const item = record(value, `walk-path[${index}]`);
        nativePathsFromRecord(item, `walk-path[${index}]`, registration).forEach(path => {
            for (let segmentIndex = 1; segmentIndex < path.points.length; segmentIndex += 1) {
                walkSegments.push({ id: `walk-segment:${path.id}:${String(segmentIndex).padStart(3, '0')}`, a: path.points[segmentIndex - 1], b: path.points[segmentIndex], pathId: path.id });
            }
            const step = Math.max(1, Math.ceil(path.points.length / 10));
            path.points.forEach((pathPoint, pointIndex) => {
                if ((pointIndex === 0 || pointIndex === path.points.length - 1 || pointIndex % step === 0) && walkNodes.length < MAX_SAMPLED_WALK_NODES) {
                    const memberships = roomMembershipsForPoint(rooms, pathPoint);
                    const room = memberships[0];
                    if (room) walkNodes.push({ id: `walk:${path.id}:${String(pointIndex).padStart(3, '0')}`, point: pathPoint, roomId: room.id, roomIds: memberships.map(itemRoom => itemRoom.id), pathId: path.id });
                }
            });
        });
    });



    const positionsByRoomId = new Map<string, CandidatePositionRecord[]>();
    for (const position of positions) {
        for (const roomId of position.roomIds) positionsByRoomId.set(roomId, [...(positionsByRoomId.get(roomId) ?? []), position]);
    }
    const walkNodesByRoomId = new Map<string, CandidateWalkNode[]>();
    for (const node of walkNodes) {
        for (const roomId of node.roomIds) walkNodesByRoomId.set(roomId, [...(walkNodesByRoomId.get(roomId) ?? []), node]);
    }

    const graphForApproach = {
        verificationMode,
        rooms,
        doors,
        agents: [],
        destinations: positionDestinations,
        colliders,
        walkNodes,
        walkSegments,
        roomDiagnostics: zoneResolver.diagnostics,
        nodeCount: 0,
        edgeCount: 0,
        navigationAvailable: true,
    } satisfies CandidateNavigationGraph;

    const positionEvaluations = new Map<string, CandidatePositionEvaluation>();
    for (const position of positions) {
        if (options.instrumentation) options.instrumentation.positionCollisionEvaluations += 1;
        const collisionFree = bounded(position.point) && !colliders.some(collider => pointOverlapsColliderFootprint(position.point, collider));
        if (options.instrumentation) options.instrumentation.positionWalkEvaluations += 1;
        const directWalkSupport = isWalkSupported(graphForApproach, position.point);
        const nearestWalk = directWalkSupport ? null : nearestWalkPoint(graphForApproach, position.point, position.roomIds);
        positionEvaluations.set(position.id, {
            position,
            bounded: bounded(position.point),
            collisionFree,
            directWalkSupport,
            connectorSupported: directWalkSupport || nearestWalk !== null,
            nearestWalkNodeId: nearestWalk ? pointKey(nearestWalk) : undefined,
            roomIds: position.roomIds,
        });
    }

    const safeSelectedPositions = selectCandidateAgentPositions(positionEvaluations.values());
    const agents = buildCandidateAgents(safeSelectedPositions);

    const resolvePositionApproachAnchor = (markerPoint: Point, markerRoomIds: readonly string[], minimumSeparation = AGENT_FOOTPRINT_RADIUS) => {
        const membershipSet = new Set(markerRoomIds);
        const candidatePositions = [...membershipSet]
            .flatMap(roomId => positionsByRoomId.get(roomId) ?? [])
            .filter((position, index, all) => all.findIndex(item => item.id === position.id) === index);
        return candidatePositions
            .map(position => positionEvaluations.get(position.id))
            .filter((evaluation): evaluation is CandidatePositionEvaluation => !!evaluation)
            .filter(evaluation => evaluation.collisionFree && evaluation.connectorSupported && distance(evaluation.position.point, markerPoint) > minimumSeparation)
            .sort((a, b) => (a.position.tier === 'standard' ? 0 : 1) - (b.position.tier === 'standard' ? 0 : 1)
                || (a.directWalkSupport === b.directWalkSupport ? 0 : a.directWalkSupport ? -1 : 1)
                || distance(a.position.point, markerPoint) - distance(b.position.point, markerPoint)
                || a.position.id.localeCompare(b.position.id))[0] ?? null;
    };

    const resolveRoomAnchor = (room: CandidateRoom) => {
        const position = resolvePositionApproachAnchor(room.center, [room.id], 0);
        if (position && pointInPolygon(position.position.point, room.polygon)) return {
            point: position.position.point,
            resolution: 'position-anchor' as const,
            sourceId: position.position.id,
            sourceTier: position.position.tier,
        };
        const walkNode = (walkNodesByRoomId.get(room.id) ?? [])
            .filter(node => pointInPolygon(node.point, room.polygon) && !colliders.some(collider => pointOverlapsColliderFootprint(node.point, collider)))
            .sort((a, b) => distance(a.point, room.center) - distance(b.point, room.center) || a.id.localeCompare(b.id))[0];
        if (walkNode) return { point: walkNode.point, resolution: 'walk-node' as const, sourceId: walkNode.id, sourceTier: undefined };
        return null;
    };

    const roomDestinations = rooms.map((room): CandidateDestination => {
        const anchor = resolveRoomAnchor(room);
        return {
            id: `room:${room.id}`,
            label: room.name,
            kind: 'room',
            point: anchor?.point ?? room.center,
            roomId: room.id,
            roomIds: [room.id],
            roomName: room.name,
            availability: anchor ? 'available' : 'unavailable',
            unavailableReason: anchor ? undefined : 'No safe candidate room destination anchor is available.',
            roomAnchorResolution: anchor?.resolution,
            roomAnchorSourceId: anchor?.sourceId,
            roomAnchorSourceTier: anchor?.sourceTier,
        };
    });

    const resolveInteractiveApproachAnchor = (markerPoint: Point, markerRooms: readonly CandidateRoom[]) => {
        if (markerRooms.length === 0) return null;
        const markerRoomIds = markerRooms.map(room => room.id);
        const markerRoomSet = new Set(markerRoomIds);
        const insideMarkerRoom = (pointValue: Point) => markerRooms.some(room => pointInPolygon(pointValue, room.polygon));
        const doorClearanceSafe = (pointValue: Point) => !doors.some(door => candidateAgentOccupiesDoor(pointValue, door));
        const positionCandidates = markerRoomIds
            .flatMap(roomId => positionsByRoomId.get(roomId) ?? [])
            .filter((position, index, all) => all.findIndex(item => item.id === position.id) === index)
            .map(position => positionEvaluations.get(position.id))
            .filter((evaluation): evaluation is CandidatePositionEvaluation => !!evaluation)
            .filter(evaluation => evaluation.bounded && evaluation.collisionFree && evaluation.connectorSupported)
            .filter(evaluation => evaluation.position.roomIds.some(roomId => markerRoomSet.has(roomId)))
            .filter(evaluation => distance(markerPoint, evaluation.position.point) <= INTERACTIVE_APPROACH_MAX_DISTANCE)
            .filter(evaluation => insideMarkerRoom(evaluation.position.point) && doorClearanceSafe(evaluation.position.point))
            .sort((a, b) => distance(a.position.point, markerPoint) - distance(b.position.point, markerPoint)
                || (a.position.tier === 'standard' ? 0 : 1) - (b.position.tier === 'standard' ? 0 : 1)
                || (a.directWalkSupport === b.directWalkSupport ? 0 : a.directWalkSupport ? -1 : 1)
                || a.position.id.localeCompare(b.position.id));
        const position = positionCandidates[0];
        if (position) return {
            point: position.position.point,
            room: position.position.room,
            roomIds: position.position.roomIds,
            accessTier: position.position.tier,
            approachPositionId: position.position.id,
            approachAccessTier: position.position.tier,
            approachResolution: 'position-anchor' as const,
            approachAnchorId: position.position.id,
            approachDistance: distance(markerPoint, position.position.point),
        };
        const walkNode = walkNodes
            .filter(node => node.roomIds.some(roomId => markerRoomSet.has(roomId)))
            .filter(node => distance(markerPoint, node.point) <= INTERACTIVE_APPROACH_MAX_DISTANCE)
            .filter(node => insideMarkerRoom(node.point) && !colliders.some(collider => pointOverlapsColliderFootprint(node.point, collider)) && doorClearanceSafe(node.point))
            .filter(node => isWalkSupported(graphForApproach, node.point))
            .sort((a, b) => distance(a.point, markerPoint) - distance(b.point, markerPoint) || a.id.localeCompare(b.id))[0];
        if (walkNode) {
            const room = markerRooms.find(item => walkNode.roomIds.includes(item.id)) ?? markerRooms[0];
            return { point: walkNode.point, room, roomIds: walkNode.roomIds.filter(roomId => markerRoomSet.has(roomId)), approachResolution: 'walk-node' as const, approachAnchorId: walkNode.id, approachDistance: distance(markerPoint, walkNode.point) };
        }
        const walkSegment = walkSegments
            .map(segment => ({ segment, point: closestPointOnSegment(markerPoint, segment.a, segment.b) }))
            .filter(candidate => distance(markerPoint, candidate.point) <= INTERACTIVE_APPROACH_MAX_DISTANCE)
            .filter(candidate => insideMarkerRoom(candidate.point) && membershipIds(rooms, candidate.point).some(roomId => markerRoomSet.has(roomId)))
            .filter(candidate => !colliders.some(collider => pointOverlapsColliderFootprint(candidate.point, collider)) && doorClearanceSafe(candidate.point))
            .filter(candidate => isWalkSupported(graphForApproach, candidate.point))
            .sort((a, b) => distance(a.point, markerPoint) - distance(b.point, markerPoint) || a.segment.id.localeCompare(b.segment.id))[0];
        if (walkSegment) {
            const segmentRoomIds = membershipIds(rooms, walkSegment.point).filter(roomId => markerRoomSet.has(roomId));
            const room = markerRooms.find(item => segmentRoomIds.includes(item.id)) ?? markerRooms[0];
            return { point: walkSegment.point, room, roomIds: segmentRoomIds, approachResolution: 'walk-segment' as const, approachAnchorId: walkSegment.segment.id, approachDistance: distance(markerPoint, walkSegment.point) };
        }
        return null;
    };

    const interactiveDestinations = interactiveRecords.map((value, index): CandidateDestination => {
        const item = record(value, `interactive[${index}]`);
        const polygon = transformMarkupPoints(points(item.pdfPolygon, `interactive[${index}].pdfPolygon`), registration);
        const markerPoint = centroid(polygon);
        const markerMemberships = roomMembershipsForPoint(rooms, markerPoint);
        const polygonMemberships = polygon.flatMap(pointValue => roomMembershipsForPoint(rooms, pointValue));
        const memberships = (markerMemberships.length > 0 ? markerMemberships : polygonMemberships)
            .filter((room, roomIndex, allRooms) => allRooms.findIndex(itemRoom => itemRoom.id === room.id) === roomIndex);
        const approach = resolveInteractiveApproachAnchor(markerPoint, memberships);
        const room = approach?.room ?? memberships[0] ?? { id: 'candidate-zone-unresolved', name: 'Unresolved candidate zone', polygon: [], center: markerPoint };
        return {
            id: `interactive:${text(item.id, `INTERACTIVE_${index + 1}`)}`,
            label: text(item.name, `Interactive object ${index + 1}`),
            kind: 'interactive-object',
            point: approach?.point ?? markerPoint,
            roomId: room.id,
            roomIds: approach?.roomIds ?? memberships.map(itemRoom => itemRoom.id),
            roomName: room.name,
            accessTier: approach?.accessTier,
            markerPoint,
            approachPositionId: approach?.approachPositionId,
            approachAccessTier: approach?.approachAccessTier,
            approachResolution: approach?.approachResolution,
            approachAnchorId: approach?.approachAnchorId,
            approachDistance: approach?.approachDistance,
            availability: approach ? 'available' : 'unavailable',
            unavailableReason: approach ? undefined : 'No local candidate interactive-object approach anchor is available.',
        };
    }).filter(item => bounded(item.point));

    const computerDestinations = computerRecords.map((value, index): CandidateDestination | null => {
        const item = record(value, `computer[${index}]`);
        const native = nativePathsFromRecord(item, `computer[${index}]`, registration)[0];
        if (!native) return null;
        const markerPoint = centroid(native.points);
        const memberships = roomMembershipsForPoint(rooms, markerPoint);
        const approach = resolvePositionApproachAnchor(markerPoint, memberships.map(itemRoom => itemRoom.id));
        if (!approach) return null;
        return {
            id: `computer:${text(item.id, `COMPUTER_${index + 1}`)}`,
            label: `Computer ${String(index + 1).padStart(3, '0')}`,
            kind: 'computer',
            point: approach.position.point,
            roomId: approach.position.room.id,
            roomIds: approach.position.roomIds,
            roomName: approach.position.room.name,
            accessTier: approach.position.tier === 'priority' ? 'priority' : 'standard',
            markerPoint,
            approachPositionId: approach.position.id,
            approachAccessTier: approach.position.tier,
            approachResolution: 'position-anchor',
            availability: 'available',
        };
    }).filter((item): item is CandidateDestination => item !== null && bounded(item.point));

    return {
        verificationMode,
        rooms,
        doors,
        agents,
        destinations: [...positionDestinations, ...computerDestinations, ...roomDestinations, ...interactiveDestinations].sort(destinationSort),
        colliders: colliders.sort((a, b) => a.id.localeCompare(b.id)),
        walkNodes: walkNodes.sort((a, b) => a.id.localeCompare(b.id)),
        walkSegments: walkSegments.sort((a, b) => a.id.localeCompare(b.id)),
        roomDiagnostics: zoneResolver.diagnostics,
        nodeCount: rooms.length + doors.length + positionDestinations.length + computerDestinations.length + interactiveDestinations.length + walkNodes.length,
        edgeCount: doors.length + walkSegments.length,
        navigationAvailable: true,
    };
}

function destinationById(graph: CandidateNavigationGraph, destinationId: string): CandidateDestination | null {
    return graph.destinations.find(destination => destination.id === destinationId) ?? null;
}

function destinationRequiredAccessTier(destination: CandidateDestination): 'standard' | 'priority' | 'malformed' | undefined {
    if (destination.kind === 'room') return undefined;
    if (destination.kind === 'position') return destination.accessTier;
    if ((destination.kind === 'computer' || destination.kind === 'interactive-object') && destination.approachResolution === 'position-anchor') {
        if (!destination.approachPositionId || !destination.approachAccessTier) return 'malformed';
        if (destination.accessTier && destination.accessTier !== destination.approachAccessTier) return 'malformed';
        return destination.approachAccessTier;
    }
    if ((destination.kind === 'computer' || destination.kind === 'interactive-object') && destination.approachResolution && destination.approachResolution !== 'position-anchor') {
        if (destination.approachPositionId || destination.approachAccessTier) return 'malformed';
    }
    return undefined;
}

function routeFailure(status: CandidateRouteStatus, reason: string, failureCategory: string, expandedNodeCount = 0, crossedDoorIds: readonly string[] = []): CandidateRouteResult {
    return { status, reason: safeReason(reason), points: [], crossedDoorIds, doorSteps: [], nodeSequence: [], cost: 0, length: 0, expandedNodeCount, failureCategory };
}

function validatePoint(graph: CandidateNavigationGraph, value: Point, label: string): CandidateRouteResult | null {
    if (!bounded(value)) return routeFailure('malformed', `${label} is outside bounded Floor 1 candidate coordinates.`, 'bounds');
    const collider = graph.colliders.find(item => pointOverlapsColliderFootprint(value, item));
    if (collider) return routeFailure('blocked', `${label} footprint overlaps candidate ${collider.kind} collision geometry (${collider.id}).`, 'collision');
    return null;
}

function pointOverlapsColliderFootprint(pointValue: Point, collider: CandidateCollider): boolean {
    if (collider.closed && pointInPolygon(pointValue, collider.points)) return true;
    const segmentCount = collider.closed ? collider.points.length : collider.points.length - 1;
    for (let index = 0; index < segmentCount; index += 1) {
        const a = collider.points[index];
        const b = collider.points[(index + 1) % collider.points.length];
        if (pointSegmentDistance(pointValue, a, b) <= collider.thickness / 2 + AGENT_FOOTPRINT_RADIUS) return true;
    }
    return false;
}

function doorForHit(graph: CandidateNavigationGraph, pointValue: Point, crossedDoorIds: readonly string[]): CandidateDoorNode | null {
    return crossedDoorIds.map(id => graph.doors.find(door => door.id === id) ?? null)
        .find((door): door is CandidateDoorNode => door !== null && door.apertureRadius > AGENT_FOOTPRINT_RADIUS && distance(pointValue, door.point) <= door.apertureRadius - AGENT_FOOTPRINT_RADIUS) ?? null;
}

function isDoorAperturePoint(graph: CandidateNavigationGraph, pointValue: Point, crossedDoorIds: readonly string[]): boolean {
    return !!doorForHit(graph, pointValue, crossedDoorIds);
}

type IndexedCollider = Readonly<{ collider: CandidateCollider; minX: number; minY: number; maxX: number; maxY: number }>;
const STATIC_COLLIDER_INDEX = new WeakMap<object, readonly IndexedCollider[]>();

function staticColliderIndex(graph: CandidateNavigationGraph): readonly IndexedCollider[] {
    const cached = STATIC_COLLIDER_INDEX.get(graph);
    if (cached) return cached;
    const indexed = graph.colliders.map(collider => {
        const margin = collider.thickness / 2 + AGENT_FOOTPRINT_RADIUS;
        return {
            collider,
            minX: Math.min(...collider.points.map(point => point.x)) - margin,
            minY: Math.min(...collider.points.map(point => point.y)) - margin,
            maxX: Math.max(...collider.points.map(point => point.x)) + margin,
            maxY: Math.max(...collider.points.map(point => point.y)) + margin,
        };
    });
    STATIC_COLLIDER_INDEX.set(graph, indexed);
    return indexed;
}

export function candidatePointHasStaticClearance(
    graph: CandidateNavigationGraph,
    pointValue: Point,
    crossedDoorIds: readonly string[] = [],
): boolean {
    if (!bounded(pointValue)) return false;
    return staticColliderIndex(graph)
        .filter(item => pointValue.x >= item.minX && pointValue.x <= item.maxX && pointValue.y >= item.minY && pointValue.y <= item.maxY)
        .every(({ collider }) => {
        if (!pointOverlapsColliderFootprint(pointValue, collider)) return true;
        return collider.kind === 'wall' && isDoorAperturePoint(graph, pointValue, crossedDoorIds);
    });
}

export function candidateSegmentHasStaticClearance(
    graph: CandidateNavigationGraph,
    a: Point,
    b: Point,
    crossedDoorIds: readonly string[] = [],
): boolean {
    if (!bounded(a) || !bounded(b)) return false;
    const minX = Math.min(a.x, b.x);
    const minY = Math.min(a.y, b.y);
    const maxX = Math.max(a.x, b.x);
    const maxY = Math.max(a.y, b.y);
    return staticColliderIndex(graph)
        .filter(item => item.maxX >= minX && item.minX <= maxX && item.maxY >= minY && item.minY <= maxY)
        .every(({ collider }) => {
        const hits = colliderIntersections(a, b, collider);
        if (hits.length === 0) return true;
        return collider.kind === 'wall' && hits.every(hit => isDoorAperturePoint(graph, hit, crossedDoorIds));
    });
}

function isWalkSupported(graph: CandidateNavigationGraph, pointValue: Point): boolean {
    if (graph.walkSegments.length === 0 && graph.walkNodes.length === 0) return true;
    return graph.walkSegments.some(segment => pointSegmentDistance(pointValue, segment.a, segment.b) <= WALK_SUPPORT_RADIUS)
        || graph.walkNodes.some(node => distance(pointValue, node.point) <= WALK_SUPPORT_RADIUS);
}

type SegmentKind = 'start_connector' | 'walk_network' | 'doorway_transition' | 'destination_connector';

function segmentHasWalkSupport(graph: CandidateNavigationGraph, a: Point, b: Point, crossedDoorIds: readonly string[], kind: SegmentKind): boolean {
    if (graph.walkSegments.length === 0) return true;
    const length = distance(a, b);
    if ((kind === 'start_connector' || kind === 'destination_connector') && length > CONNECTOR_MAX_DISTANCE) return false;
    const sampleCount = Math.max(2, Math.ceil(length / WALK_SAMPLE_INTERVAL));
    let supportedSeen = false;
    for (let index = 0; index <= sampleCount; index += 1) {
        const ratio = index / sampleCount;
        const sample = { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio };
        if (isDoorAperturePoint(graph, sample, crossedDoorIds)) { supportedSeen = true; continue; }
        const supported = isWalkSupported(graph, sample);
        if (supported) { supportedSeen = true; continue; }
        if (kind === 'start_connector' && distance(sample, a) <= CONNECTOR_INGRESS_DISTANCE && !supportedSeen) continue;
        if (kind === 'destination_connector' && distance(sample, b) <= CONNECTOR_INGRESS_DISTANCE) continue;
        return false;
    }
    return kind === 'walk_network' || kind === 'doorway_transition' || supportedSeen;
}

function segmentKind(index: number, routePoints: readonly Point[], crossedDoorIds: readonly string[], graph: CandidateNavigationGraph): SegmentKind {
    const a = routePoints[index - 1];
    const b = routePoints[index];
    const nearDoor = crossedDoorIds.some(doorId => {
        const door = graph.doors.find(item => item.id === doorId);
        return door ? distance(a, door.point) <= door.apertureRadius || distance(b, door.point) <= door.apertureRadius : false;
    });
    if (nearDoor) return 'doorway_transition';
    if (index === 1) return 'start_connector';
    if (index === routePoints.length - 1) return 'destination_connector';
    return 'walk_network';
}

export function validateCandidateRouteSegments(graph: CandidateNavigationGraph, routePoints: readonly Point[], crossedDoorIds: readonly string[]): CandidateRouteResult | null {
    for (let index = 1; index < routePoints.length; index += 1) {
        const a = routePoints[index - 1];
        const b = routePoints[index];
        if (!bounded(a) || !bounded(b)) return routeFailure('malformed', 'Route includes out-of-bounds coordinates.', 'bounds', index, crossedDoorIds);
        const kind = segmentKind(index, routePoints, crossedDoorIds, graph);
        for (const collider of graph.colliders) {
            const hits = colliderIntersections(a, b, collider);
            if (hits.length === 0) continue;
            if (collider.kind === 'object') return routeFailure('blocked', `Route segment intersects candidate object collision geometry (${collider.id}).`, 'collision', index, crossedDoorIds);
            const invalidWallHit = hits.find(hit => !doorForHit(graph, hit, crossedDoorIds));
            if (invalidWallHit) return routeFailure('blocked', `Route segment intersects candidate wall collision geometry outside a validated doorway aperture (${collider.id}).`, 'collision', index, crossedDoorIds);
            const uniqueDoors = new Set(hits.map(hit => doorForHit(graph, hit, crossedDoorIds)?.id).filter(Boolean));
            if (uniqueDoors.size > 1) return routeFailure('blocked', 'Route segment crosses multiple wall apertures without intermediate doorway nodes.', 'door-aperture', index, crossedDoorIds);
        }
        if (!segmentHasWalkSupport(graph, a, b, crossedDoorIds, kind)) {
            const category = kind === 'start_connector' ? 'start_connector_unsupported'
                : kind === 'destination_connector' ? 'destination_connector_unsupported'
                    : 'route_leaves_walkable_geometry';
            return routeFailure('blocked', `Route ${kind.replace(/_/g, ' ')} leaves candidate walk-path geometry.`, category, index, crossedDoorIds);
        }
    }
    return null;
}

function nearestWalkPoint(graph: CandidateNavigationGraph, source: Point, roomIds: readonly string[]): Point | null {
    const roomSet = new Set(roomIds);
    const candidates = graph.walkNodes
        .filter(node => node.roomIds.some(roomId => roomSet.has(roomId)))
        .sort((a, b) => distance(a.point, source) - distance(b.point, source) || a.id.localeCompare(b.id))
        .slice(0, CONNECTOR_SEARCH_LIMIT);
    return candidates.find(node => distance(node.point, source) <= CONNECTOR_MAX_DISTANCE)?.point ?? null;
}

function buildTopology(graph: CandidateNavigationGraph) {
    const adjacency = new Map<string, Array<{ to: string; door: CandidateDoorNode }>>();
    const denied: CandidateDoorNode[] = [];
    for (const door of graph.doors) {
        const outcome = accessOutcome(door);
        if (outcome !== 'allowed') { denied.push(door); continue; }
        const [a, b] = door.zoneIds;
        if (!a || !b) continue;
        adjacency.set(a, [...(adjacency.get(a) ?? []), { to: b, door }]);
        adjacency.set(b, [...(adjacency.get(b) ?? []), { to: a, door }]);
    }
    adjacency.forEach(edges => edges.sort((a, b) => a.door.id.localeCompare(b.door.id) || a.to.localeCompare(b.to)));
    return { adjacency, denied };
}

function findDoorPaths(graph: CandidateNavigationGraph, startRoomIds: readonly string[], destRoomIds: readonly string[]) {
    const { adjacency, denied } = buildTopology(graph);
    const destSet = new Set(destRoomIds);
    const queue: Array<{ roomId: string; path: CandidateDoorNode[]; nodes: string[] }> = startRoomIds
        .slice()
        .sort((a, b) => a.localeCompare(b))
        .map(roomId => ({ roomId, path: [], nodes: [roomId] }));
    const paths: Array<{ path: CandidateDoorNode[]; nodes: string[] }> = [];
    let expanded = 0;
    while (queue.length > 0 && expanded < MAX_EXPANDED_NODES && paths.length < 32) {
        const current = queue.shift();
        if (!current) break;
        expanded += 1;
        if (destSet.has(current.roomId)) {
            paths.push({ path: current.path, nodes: current.nodes });
            continue;
        }
        for (const edge of adjacency.get(current.roomId) ?? []) {
            if (current.nodes.includes(edge.to)) continue;
            queue.push({ roomId: edge.to, path: [...current.path, edge.door], nodes: [...current.nodes, edge.to] });
        }
        queue.sort((a, b) => a.path.map(door => door.id).join(',').localeCompare(b.path.map(door => door.id).join(',')) || a.roomId.localeCompare(b.roomId));
    }
    return { paths, expanded, denied };
}

function roomName(graph: CandidateNavigationGraph, roomId: string): string {
    return graph.rooms.find(room => room.id === roomId)?.name ?? roomId.replace(/^zone:/, '');
}

function pointKey(pointValue: Point): string {
    return `${Math.round(pointValue.x * 100) / 100},${Math.round(pointValue.y * 100) / 100}`;
}

function appendPath(pointsOut: Point[], path: readonly Point[]): void {
    for (const pointValue of path.slice(1)) {
        if (distance(pointsOut[pointsOut.length - 1], pointValue) > 0.001) pointsOut.push(pointValue);
    }
}

type CandidateWalkEndpointConnector = Readonly<{
    nodeId: string;
    point: Point;
    connectorPoints: readonly Point[];
    connectorDistance: number;
    roomIds: readonly string[];
}>;

type CandidateWalkNetwork = Readonly<{
    nodes: ReadonlyMap<string, Point>;
    edges: ReadonlyMap<string, readonly Readonly<{ to: string; length: number; edgeId: string }>[] >;
}>;

function buildRelevantWalkNetwork(graph: CandidateNavigationGraph, roomIds: readonly string[]): CandidateWalkNetwork {
    const roomSet = new Set(roomIds);
    const relevant = graph.walkSegments.filter(segment => {
        const aRooms = membershipIds(graph.rooms, segment.a);
        const bRooms = membershipIds(graph.rooms, segment.b);
        return aRooms.some(roomId => roomSet.has(roomId)) || bRooms.some(roomId => roomSet.has(roomId));
    });
    const nodes = new Map<string, Point>();
    const mutableEdges = new Map<string, Array<{ to: string; length: number; edgeId: string }>>();
    const addNode = (pointValue: Point) => { const key = pointKey(pointValue); if (!nodes.has(key)) nodes.set(key, pointValue); return key; };
    const addEdge = (a: string, b: string, length: number, edgeId: string) => {
        mutableEdges.set(a, [...(mutableEdges.get(a) ?? []), { to: b, length, edgeId }]);
        mutableEdges.set(b, [...(mutableEdges.get(b) ?? []), { to: a, length, edgeId }]);
    };
    for (const segment of relevant) {
        const a = addNode(segment.a);
        const b = addNode(segment.b);
        addEdge(a, b, distance(segment.a, segment.b), segment.id);
    }
    const edges = new Map<string, readonly Readonly<{ to: string; length: number; edgeId: string }>[]>();
    mutableEdges.forEach((items, id) => {
        edges.set(id, items.sort((a, b) => a.to.localeCompare(b.to) || a.edgeId.localeCompare(b.edgeId)));
    });
    return { nodes, edges };
}

function connectorCandidateValid(graph: CandidateNavigationGraph, from: Point, to: Point, allowedRoomIds: readonly string[]): boolean {
    if (!bounded(from) || !bounded(to) || distance(from, to) > CONNECTOR_MAX_DISTANCE) return false;
    const roomSet = new Set(allowedRoomIds);
    const fromRooms = membershipIds(graph.rooms, from);
    const toRooms = membershipIds(graph.rooms, to);
    if (fromRooms.length > 0 && !fromRooms.some(roomId => roomSet.has(roomId))) return false;
    if (toRooms.length > 0 && !toRooms.some(roomId => roomSet.has(roomId))) return false;
    return validateCandidateRouteSegments(graph, [from, to], []) === null;
}

function candidateWalkEndpointConnectorsForNetwork(
    graph: CandidateNavigationGraph,
    network: CandidateWalkNetwork,
    pointValue: Point,
    allowedRoomIds: readonly string[],
): readonly CandidateWalkEndpointConnector[] {
    const roomSet = new Set(allowedRoomIds);
    const candidates = [...network.nodes.entries()]
        .map(([nodeId, nodePoint]) => ({
            nodeId,
            point: nodePoint,
            connectorPoints: [pointValue, nodePoint],
            connectorDistance: distance(pointValue, nodePoint),
            roomIds: membershipIds(graph.rooms, nodePoint).filter(roomId => roomSet.has(roomId)),
        }))
        .filter(candidate => candidate.connectorDistance <= CONNECTOR_MAX_DISTANCE && candidate.roomIds.length > 0)
        .sort((a, b) => a.connectorDistance - b.connectorDistance || a.nodeId.localeCompare(b.nodeId));
    const valid: CandidateWalkEndpointConnector[] = [];
    for (const candidate of candidates) {
        if (connectorCandidateValid(graph, pointValue, candidate.point, allowedRoomIds)) valid.push(candidate);
        if (valid.length >= CONNECTOR_SEARCH_LIMIT) break;
    }
    return valid;
}

export function candidateWalkEndpointConnectors(
    graph: CandidateNavigationGraph,
    pointValue: Point,
    allowedRoomIds: readonly string[],
): readonly CandidateWalkEndpointConnector[] {
    return candidateWalkEndpointConnectorsForNetwork(graph, buildRelevantWalkNetwork(graph, allowedRoomIds), pointValue, allowedRoomIds);
}

type CandidateWalkSearchContext = Readonly<{ allowedRoomIds: readonly string[]; allowedDoorIds: readonly string[] }>;

function candidateWalkEdgeTraversable(graph: CandidateNavigationGraph, from: Point, to: Point, context: CandidateWalkSearchContext, cache: Map<string, boolean>): boolean {
    const key = `${pointKey(from)}>${pointKey(to)}|${context.allowedDoorIds.slice().sort((a, b) => a.localeCompare(b)).join(',')}`;
    const cached = cache.get(key);
    if (cached !== undefined) return cached;
    const traversable = validateCandidateRouteSegments(graph, [from, to], context.allowedDoorIds) === null;
    cache.set(key, traversable);
    return traversable;
}

function shortestWalkNodePath(graph: CandidateNavigationGraph, network: CandidateWalkNetwork, startId: string, endId: string, context: CandidateWalkSearchContext, edgeCollisionCache: Map<string, boolean>): Readonly<{ nodeIds: readonly string[]; cost: number; expanded: number }> | null {
    const queue = [{ id: startId, cost: 0, path: [startId] }];
    const best = new Map([[startId, 0]]);
    let expanded = 0;
    while (queue.length > 0 && expanded < MAX_EXPANDED_NODES) {
        queue.sort((a, b) => a.cost - b.cost || a.id.localeCompare(b.id) || a.path.join('|').localeCompare(b.path.join('|')));
        const current = queue.shift();
        if (!current) break;
        expanded += 1;
        if (current.id === endId) return { nodeIds: current.path, cost: current.cost, expanded };
        for (const edge of network.edges.get(current.id) ?? []) {
            if (current.path.includes(edge.to)) continue;
            const fromPoint = network.nodes.get(current.id);
            const toPoint = network.nodes.get(edge.to);
            if (!fromPoint || !toPoint || !candidateWalkEdgeTraversable(graph, fromPoint, toPoint, context, edgeCollisionCache)) continue;
            const cost = current.cost + edge.length;
            if (cost >= (best.get(edge.to) ?? Number.POSITIVE_INFINITY)) continue;
            best.set(edge.to, cost);
            queue.push({ id: edge.to, cost, path: [...current.path, edge.to] });
        }
    }
    return null;
}

function connectedWalkPath(graph: CandidateNavigationGraph, from: Point, to: Point, roomIds: readonly string[], allowedDoorIds: readonly string[] = []): Point[] | null {
    if (graph.walkSegments.length === 0) return [from, to];
    const network = buildRelevantWalkNetwork(graph, roomIds);
    if (network.nodes.size === 0) return null;
    const startCandidates = candidateWalkEndpointConnectorsForNetwork(graph, network, from, roomIds);
    const endCandidates = candidateWalkEndpointConnectorsForNetwork(graph, network, to, roomIds);
    if (startCandidates.length === 0 || endCandidates.length === 0) return null;
    const edgeCollisionCache = new Map<string, boolean>();
    let best: Readonly<{
        points: readonly Point[];
        cost: number;
        length: number;
        start: CandidateWalkEndpointConnector;
        end: CandidateWalkEndpointConnector;
        nodeSequence: string;
    }> | null = null;
    for (const start of startCandidates) {
        for (const end of endCandidates) {
            const walkPath = shortestWalkNodePath(graph, network, start.nodeId, end.nodeId, { allowedRoomIds: roomIds, allowedDoorIds }, edgeCollisionCache);
            if (!walkPath) continue;
            const nodePoints = walkPath.nodeIds.map(id => network.nodes.get(id)!).filter(Boolean);
            const points = [from, ...nodePoints, to].filter((pointItem, index, all) => index === 0 || distance(pointItem, all[index - 1]) > 0.001);
            const length = routeLength(points);
            const cost = start.connectorDistance + walkPath.cost + end.connectorDistance;
            const candidate = { points, cost, length, start, end, nodeSequence: walkPath.nodeIds.join('|') };
            if (!best
                || candidate.cost < best.cost
                || (candidate.cost === best.cost && candidate.length < best.length)
                || (candidate.cost === best.cost && candidate.length === best.length && candidate.start.connectorDistance < best.start.connectorDistance)
                || (candidate.cost === best.cost && candidate.length === best.length && candidate.start.connectorDistance === best.start.connectorDistance && candidate.end.connectorDistance < best.end.connectorDistance)
                || (candidate.cost === best.cost && candidate.length === best.length && candidate.start.connectorDistance === best.start.connectorDistance && candidate.end.connectorDistance === best.end.connectorDistance && candidate.start.nodeId.localeCompare(best.start.nodeId) < 0)
                || (candidate.cost === best.cost && candidate.length === best.length && candidate.start.connectorDistance === best.start.connectorDistance && candidate.end.connectorDistance === best.end.connectorDistance && candidate.start.nodeId === best.start.nodeId && candidate.end.nodeId.localeCompare(best.end.nodeId) < 0)
                || (candidate.cost === best.cost && candidate.length === best.length && candidate.start.connectorDistance === best.start.connectorDistance && candidate.end.connectorDistance === best.end.connectorDistance && candidate.start.nodeId === best.start.nodeId && candidate.end.nodeId === best.end.nodeId && candidate.nodeSequence.localeCompare(best.nodeSequence) < 0)) {
                best = candidate;
            }
        }
    }
    return best ? [...best.points] : null;
}

export function validateCandidateRouteDoorClearance(
    routePoints: readonly Point[],
    doorSteps: readonly CandidateDoorStep[],
    doors: readonly CandidateDoorNode[],
): string | null {
    if (doorSteps.length === 0) return null;
    const length = routeLength(routePoints);
    const finalStep = doorSteps[doorSteps.length - 1];
    if (finalStep.clearanceReleaseDistance > length + ROUTE_PROGRESS_EPSILON) return 'final_door_clearance_unreachable';
    const finalDoor = doors.find(door => door.id === finalStep.doorId);
    const endpoint = routePoints[routePoints.length - 1];
    if (finalDoor && endpoint && candidateAgentOccupiesDoor(endpoint, finalDoor)) return 'destination_inside_door_clearance';
    return null;
}

function routeForDoorPath(
    graph: CandidateNavigationGraph,
    start: Point,
    destination: CandidateDestination,
    startRoomIds: readonly string[],
    destRoomIds: readonly string[],
    doorPath: readonly CandidateDoorNode[],
    roomNodes: readonly string[],
    expandedNodeCount: number,
): CandidateRouteResult {
    const crossedDoorIds: string[] = [];
    const doorSteps: CandidateDoorStep[] = [];
    const pointsOut: Point[] = [start];
    const nodeSequence = ['point:start', ...(roomNodes.length > 0 ? [roomNodes[0]] : startRoomIds)];
    let currentPoint = start;
    let currentRooms = startRoomIds;
    for (const door of doorPath) {
        if (door.apertureRadius <= AGENT_FOOTPRINT_RADIUS) {
            return routeFailure('blocked', `${door.id} doorway aperture cannot fit the candidate agent footprint.`, 'collision', expandedNodeCount, [door.id]);
        }
        const pathToDoor = connectedWalkPath(graph, currentPoint, door.point, currentRooms, [door.id]);
        if (!pathToDoor) return routeFailure('blocked', 'Walk network is disconnected before a doorway transition.', 'walk_network_disconnected', expandedNodeCount, crossedDoorIds);
        appendPath(pointsOut, pathToDoor);
        const thresholdDistance = routeLength(pointsOut);
        const approachPoint = pointsOut.length > 1 ? pointsOut[pointsOut.length - 2] : currentPoint;
        const approachDistance = Math.max(0, thresholdDistance - distance(approachPoint, door.point));
        doorSteps.push({
            doorId: door.id,
            permission: door.permission ?? 'general',
            initialPhysicalState: door.currentState ?? 'closed',
            requiredAction: (door.permission ?? 'general') === 'elevator' ? 'elevator_call' : (door.currentState ?? 'closed') === 'open' ? 'none' : 'automatic_open',
            approachPoint,
            thresholdPoint: door.point,
            exitPoint: door.point,
            approachDistance,
            thresholdDistance,
            exitDistance: thresholdDistance,
            clearanceReleaseDistance: thresholdDistance + AGENT_FOOTPRINT_RADIUS * 2,
        });
        crossedDoorIds.push(door.id);
        nodeSequence.push(`door:${door.id}`);
        const nextRoom = roomNodes[crossedDoorIds.length];
        if (nextRoom && nodeSequence[nodeSequence.length - 1] !== nextRoom) nodeSequence.push(nextRoom);
        currentPoint = door.point;
        currentRooms = nextRoom ? [nextRoom] : currentRooms;
        if (pointsOut.length > MAX_ROUTE_POINTS) return routeFailure('malformed', 'Route exceeded the candidate route point limit.', 'limits', expandedNodeCount, crossedDoorIds);
    }
    const pathToDestination = connectedWalkPath(graph, currentPoint, destination.point, destRoomIds, crossedDoorIds.slice(-1));
    if (!pathToDestination) return routeFailure('blocked', 'Walk network is disconnected before the destination.', 'walk_network_disconnected', expandedNodeCount, crossedDoorIds);
    appendPath(pointsOut, pathToDestination);
    for (const roomId of destRoomIds) { if (nodeSequence[nodeSequence.length - 1] !== roomId) nodeSequence.push(roomId); }
    nodeSequence.push(`destination:${destination.id}`);
    const compact = pointsOut.filter((item, index, all) => index === 0 || distance(item, all[index - 1]) > 0.001);
    const segmentFailure = validateCandidateRouteSegments(graph, compact, crossedDoorIds);
    if (segmentFailure) return { ...segmentFailure, expandedNodeCount: Math.max(segmentFailure.expandedNodeCount, expandedNodeCount), crossedDoorIds, doorSteps };
    const clearanceFailure = validateCandidateRouteDoorClearance(compact, doorSteps, graph.doors);
    if (clearanceFailure) return routeFailure('blocked', 'Candidate route endpoint does not clear the final doorway.', clearanceFailure, expandedNodeCount, crossedDoorIds);
    const length = routeLength(compact);
    return {
        status: 'valid',
        reason: crossedDoorIds.length > 0 ? `Candidate route allowed through ${crossedDoorIds.join(', ')}.` : 'Candidate same-room route is valid.',
        points: compact,
        crossedDoorIds,
        doorSteps,
        nodeSequence,
        cost: Math.round(length),
        length: Math.round(length),
        expandedNodeCount,
    };
}

export function planCandidateRoute(graph: CandidateNavigationGraph, request: CandidateRouteRequest): CandidateRouteResult {
    if (!graph.navigationAvailable) return routeFailure('malformed', graph.unavailableReason ?? 'Candidate navigation unavailable.', 'registration_unavailable');
    const { destinationId } = request;
    if (!request.agent?.id) return routeFailure('malformed', 'Route planning requires a candidate agent identity.', 'agent_context_missing');
    const agent = graph.agents.find(item => item.id === request.agent.id);
    if (!agent) return routeFailure('malformed', 'Route planning agent identity is not part of the candidate graph.', 'agent_context_unknown');
    const start = request.agent.currentPoint;
    const startValidation = validatePoint(graph, start, 'Route start');
    if (startValidation) return startValidation;
    const destination = destinationById(graph, destinationId);
    if (!destination) return routeFailure('malformed', 'Destination could not be resolved to a candidate review point.', 'destination');
    if (destination.availability === 'unavailable') return routeFailure('blocked', destination.unavailableReason ?? 'Destination is unavailable for candidate navigation.', 'destination_unavailable');
    const requiredAccessTier = destinationRequiredAccessTier(destination);
    if (requiredAccessTier === 'malformed') return routeFailure('malformed', 'Destination access metadata is inconsistent for candidate navigation.', 'destination_access_metadata_invalid');
    if (requiredAccessTier === 'priority' && agent.accessTier !== 'priority') {
        return routeFailure('restricted', `Destination ${destination.label} requires a priority review agent.`, 'destination_access_restricted');
    }
    const destinationValidation = validatePoint(graph, destination.point, 'Route destination');
    if (destinationValidation) return destinationValidation;

    const startRoomIds = membershipIds(graph.rooms, start);
    if (startRoomIds.length === 0) return routeFailure('malformed', 'Route start has no candidate room polygon membership.', 'start_room_membership_unresolved');
    const actualDestinationRoomIds = membershipIds(graph.rooms, destination.point);
    const destRoomIds = destination.roomIds.filter(roomId => actualDestinationRoomIds.includes(roomId));
    if (destRoomIds.length === 0) return routeFailure('malformed', 'Route destination has no candidate room polygon membership.', 'destination_room_membership_unresolved');
    const overlappingMembership = startRoomIds.some(roomId => destRoomIds.includes(roomId));
    const doorSearch = overlappingMembership
        ? { paths: [{ path: [] as CandidateDoorNode[], nodes: startRoomIds.filter(roomId => destRoomIds.includes(roomId)) }], expanded: 1, denied: [] as CandidateDoorNode[] }
        : findDoorPaths(graph, startRoomIds, destRoomIds);
    if (doorSearch.paths.length === 0) {
        const relevantDenied = doorSearch.denied.find(door => door.zoneIds.some(roomId => startRoomIds.includes(roomId)) || door.zoneIds.some(roomId => destRoomIds.includes(roomId)));
        if (relevantDenied) {
            const outcome = accessOutcome(relevantDenied);
            return routeFailure(outcome === 'restricted' ? 'restricted' : 'blocked', `${relevantDenied.id} is ${outcome}; candidate search continued but no allowed alternate route reached ${roomName(graph, destRoomIds[0] ?? 'unresolved')}.`, outcome, doorSearch.expanded, [relevantDenied.id]);
        }
        return routeFailure('unreachable', `No allowed candidate door path connects ${roomName(graph, startRoomIds[0] ?? 'unresolved')} to ${roomName(graph, destRoomIds[0] ?? 'unresolved')}.`, 'disconnected', doorSearch.expanded);
    }
    let firstFailure: CandidateRouteResult | null = null;
    for (const candidate of doorSearch.paths) {
        const result = routeForDoorPath(graph, start, destination, startRoomIds, destRoomIds, candidate.path, candidate.nodes, doorSearch.expanded);
        if (result.status === 'valid') return result;
        if (!firstFailure) firstFailure = result;
    }
    return firstFailure ?? routeFailure('unreachable', 'No geometrically valid candidate door path reached the destination.', 'disconnected', doorSearch.expanded);
}

export function interpolateRoute(pointsIn: readonly Point[], distanceAlongRoute: number): Point {
    if (pointsIn.length === 0) return { x: 0, y: 0 };
    let remaining = Math.max(0, distanceAlongRoute);
    for (let index = 1; index < pointsIn.length; index += 1) {
        const start = pointsIn[index - 1];
        const end = pointsIn[index];
        const segmentLength = distance(start, end);
        if (remaining <= segmentLength) {
            const ratio = segmentLength === 0 ? 0 : remaining / segmentLength;
            return { x: start.x + (end.x - start.x) * ratio, y: start.y + (end.y - start.y) * ratio };
        }
        remaining -= segmentLength;
    }
    return pointsIn[pointsIn.length - 1];
}

export function candidateAgentOccupiesDoor(agentPoint: Point, door: CandidateDoorNode): boolean {
    return door.permission !== 'elevator' && distance(agentPoint, door.point) <= door.apertureRadius + AGENT_FOOTPRINT_RADIUS;
}

export function isCandidateAdvancingStatus(status: string): boolean { return status === 'walking' || status === 'crossing_door'; }
export function isCandidatePausableStatus(status: string): boolean { return status === 'walking' || status === 'waiting_for_door' || status === 'crossing_door' || status === 'canceling_clearance'; }

export function activeCandidateDoorStep<T extends { status: string; route: CandidateRouteResult | null; progress: number }>(agent: T): Readonly<{ index: number; step: CandidateDoorStep; phase: 'before_trigger' | 'approaching' | 'waiting' | 'crossing' | 'cleared' }> | null {
    if (!agent.route) return null;
    const index = agent.route.doorSteps.findIndex(step => agent.progress <= step.clearanceReleaseDistance);
    if (index < 0) return null;
    const step = agent.route.doorSteps[index]!;
    const phase = agent.progress < step.approachDistance ? 'before_trigger' : agent.status === 'waiting_for_door' ? 'waiting' : agent.progress < step.thresholdDistance ? 'approaching' : agent.progress <= step.clearanceReleaseDistance ? 'crossing' : 'cleared';
    return { index, step, phase };
}

export function activeCandidateDoorRequestIds<T extends { id?: string; status: string; route: CandidateRouteResult | null; progress: number; point: Point }>(agents: readonly T[], doors: readonly CandidateDoorNode[] = []): string[] {
    const ids = new Set<string>();
    for (const agent of agents) {
        for (const door of doors) {
            if (accessOutcome(door) === 'allowed' && candidateAgentOccupiesDoor(agent.point, door)) ids.add(door.id);
        }
        if (!agent.route || !['walking', 'waiting_for_door', 'crossing_door', 'paused'].includes(agent.status)) continue;
        const active = activeCandidateDoorStep(agent);
        if (active && active.step.permission === 'general' && agent.progress + AGENT_FOOTPRINT_RADIUS >= active.step.approachDistance && agent.progress <= active.step.clearanceReleaseDistance) ids.add(active.step.doorId);
    }
    return [...ids].sort((a, b) => a.localeCompare(b));
}

export function candidateDoorRuntimeNeedsTick(runtime: CandidateDoorRuntime, retained: boolean): boolean {
    return runtime.state === 'opening' || runtime.state === 'closing' || (runtime.state === 'open' && !retained);
}

export function advanceCandidateDoorRuntimes(
    runtimes: Readonly<Record<string, CandidateDoorRuntime>>,
    requestingDoorIds: readonly string[],
    deltaMs: number,
): Readonly<Record<string, CandidateDoorRuntime>> {
    const requests = new Set(requestingDoorIds);
    let changed = false;
    const next: Record<string, CandidateDoorRuntime> = { ...runtimes };
    for (const doorId of Object.keys(next).sort()) {
        const runtime = next[doorId];
        const requested = requests.has(doorId);
        let state = runtime.state;
        let elapsed = requested && state === 'open' ? 0 : runtime.stateElapsedMs + Math.max(0, deltaMs);
        if (requested && state === 'closed') { state = 'opening'; elapsed = 0; }
        if (state === 'opening' && elapsed >= CANDIDATE_DOOR_OPEN_MS) { state = 'open'; elapsed = 0; }
        if (!requested && state === 'open' && elapsed >= CANDIDATE_DOOR_HOLD_MS) { state = 'closing'; elapsed = 0; }
        if (state === 'closing' && requested) { state = 'open'; elapsed = 0; }
        if (state === 'closing' && elapsed >= CANDIDATE_DOOR_CLOSE_MS) { state = 'closed'; elapsed = 0; }
        if (state !== runtime.state || elapsed !== runtime.stateElapsedMs) {
            next[doorId] = { ...runtime, state, stateElapsedMs: elapsed, revision: runtime.revision + 1 };
            changed = true;
        }
    }
    return changed ? next : runtimes;
}

export function advanceCandidateAgents<T extends { id?: string; status: string; route: CandidateRouteResult | null; progress: number; point: Point }>(
    agents: readonly T[],
    deltaMs: number,
    speedPxPerSecond: number,
    doorRuntimes: Readonly<Record<string, CandidateDoorRuntime>> = {},
): readonly T[] {
    const clampedDelta = Math.min(MAX_FRAME_DELTA_MS, Math.max(0, deltaMs));
    if (clampedDelta === 0 || agents.every(agent => !['walking', 'crossing_door'].includes(agent.status))) return agents;
    let changed = false;
    const next = agents.map(agent => {
        if (!['walking', 'crossing_door'].includes(agent.status) || !agent.route || agent.route.status !== 'valid') return agent;
        const length = routeLength(agent.route.points);
        let nextProgress = Math.min(length, agent.progress + (speedPxPerSecond * clampedDelta) / 1000);
        for (const step of agent.route.doorSteps) {
            if (agent.progress > step.clearanceReleaseDistance) continue;
            const runtime = doorRuntimes[step.doorId];
            const open = runtime?.state === 'open';
            if (!open && nextProgress >= step.approachDistance) {
                nextProgress = step.approachDistance;
                const waitingAgent = { ...agent, point: interpolateRoute(agent.route.points, nextProgress), progress: nextProgress, status: 'waiting_for_door' };
                changed = true;
                return waitingAgent as T;
            }
            if (open && agent.progress < step.clearanceReleaseDistance && nextProgress > step.thresholdDistance) {
                const reachedRouteEnd = nextProgress >= length - ROUTE_PROGRESS_EPSILON;
                const crossingAgent = { ...agent, point: interpolateRoute(agent.route.points, nextProgress), progress: nextProgress, status: reachedRouteEnd ? 'arrived' : nextProgress >= step.clearanceReleaseDistance ? 'walking' : 'crossing_door' };
                changed = true;
                return crossingAgent as T;
            }
            break;
        }
        const nextAgent = {
            ...agent,
            point: interpolateRoute(agent.route.points, nextProgress),
            progress: nextProgress,
            status: nextProgress >= length ? 'arrived' : 'walking',
        };
        changed = true;
        return nextAgent as T;
    });
    return changed ? next : agents;
}
