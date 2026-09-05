import {
    AccessState,
    OfficeEntity,
    OfficeGeometry,
    OfficeLayer,
    OfficeOverlayDocument,
    Point,
} from '../types';
import { assertValidOverlayDocument } from '../validation';

export const FLOOR1_CANDIDATE_LABEL = 'Floor 1 candidate — not production approved';

export const FLOOR1_CANDIDATE_CATEGORIES = [
    'rooms',
    'walk-paths',
    'walls',
    'objects',
    'doors',
    'door-lights',
    'computers',
    'positions',
    'interactive-objects',
] as const;

export type Floor1CandidateCategory = (typeof FLOOR1_CANDIDATE_CATEGORIES)[number];
export type CandidateDocuments = Readonly<Record<Floor1CandidateCategory, unknown>>;

export const FLOOR1_CANDIDATE_LAYER_CONTROLS: ReadonlyArray<Readonly<{
    category: Floor1CandidateCategory;
    label: string;
    layer: OfficeLayer;
}>> = [
    { category: 'rooms', label: 'Rooms', layer: 'rooms' },
    { category: 'walk-paths', label: 'Walk paths', layer: 'paths' },
    { category: 'walls', label: 'Walls', layer: 'walls' },
    { category: 'objects', label: 'Objects', layer: 'restricted' },
    { category: 'doors', label: 'Doors', layer: 'doors' },
    { category: 'door-lights', label: 'Door lights', layer: 'lights' },
    { category: 'computers', label: 'Computers', layer: 'computers' },
    { category: 'positions', label: 'Positions', layer: 'furniture' },
    { category: 'interactive-objects', label: 'Interactive objects', layer: 'hitboxes' },
] as const;

export const FLOOR1_CANDIDATE_LAYERS = new Set<OfficeLayer>(
    FLOOR1_CANDIDATE_LAYER_CONTROLS.map(control => control.layer),
);

type UnknownRecord = Record<string, unknown>;

type CandidateEntityOptions = Readonly<{
    category: Floor1CandidateCategory;
    id: string;
    name: string;
    type: OfficeEntity['type'];
    layer: OfficeLayer;
    geometry: OfficeGeometry;
    metadata?: Readonly<Record<string, string | number | boolean | null>>;
    accessState?: AccessState;
    seatPriority?: 'yellow' | 'red';
    door?: OfficeEntity['door'];
    path?: OfficeEntity['path'];
}>;

function record(value: unknown, context: string): UnknownRecord {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`Floor 1 candidate ${context} is malformed.`);
    }
    return value as UnknownRecord;
}

function array(value: unknown, context: string): unknown[] {
    if (!Array.isArray(value)) throw new Error(`Floor 1 candidate ${context} must be an array.`);
    return value;
}

function finite(value: unknown, context: string): number {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        throw new Error(`Floor 1 candidate ${context} must be finite.`);
    }
    return value;
}

function point(value: unknown, context: string): Point {
    const item = record(value, context);
    return { x: finite(item.x, `${context}.x`), y: finite(item.y, `${context}.y`) };
}

function points(value: unknown, context: string): Point[] {
    const result = array(value, context).map((item, index) => point(item, `${context}[${index}]`));
    return result.filter((item, index) => index === 0
        || item.x !== result[index - 1]?.x
        || item.y !== result[index - 1]?.y);
}

function identifier(value: unknown): string {
    const normalized = String(value).trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
    if (!normalized) throw new Error('Floor 1 candidate entity ID is empty.');
    return normalized;
}

function text(value: unknown, fallback: string): string {
    return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function candidateEntity(options: CandidateEntityOptions): OfficeEntity {
    return {
        id: `floor1-candidate.${options.category}.${identifier(options.id)}`,
        type: options.type,
        name: options.name,
        geometry: options.geometry,
        sourceLayer: options.layer,
        enabled: true,
        interactive: true,
        allowOutOfBounds: true,
        metadata: {
            candidateCategory: options.category,
            productionApproved: false,
            reviewStatus: 'candidate-unverified',
            ...options.metadata,
        },
        zIndex: 0,
        accessState: options.accessState,
        accessPolicy: options.accessState ? { state: options.accessState } : undefined,
        seatPriority: options.seatPriority,
        door: options.door,
        path: options.path,
    };
}

function polygonEntity(
    category: Floor1CandidateCategory,
    source: UnknownRecord,
    type: OfficeEntity['type'],
    layer: OfficeLayer,
    displayName: string,
): OfficeEntity {
    const sourceId = text(source.id, `${category}-unknown`);
    return candidateEntity({
        category,
        id: sourceId,
        name: displayName,
        type,
        layer,
        geometry: { kind: 'polygon', points: points(source.pdfPolygon, `${sourceId}.pdfPolygon`) },
        metadata: { sourceRecordId: sourceId },
    });
}

function nativeGeometryEntities(
    category: 'walk-paths' | 'walls' | 'objects' | 'door-lights' | 'computers',
    source: UnknownRecord,
    index: number,
): OfficeEntity[] {
    const sourceId = text(source.id, `${category}-${index + 1}`);
    const native = record(source.nativeGeometry, `${sourceId}.nativeGeometry`);
    const config = {
        'walk-paths': { type: 'walk_path', layer: 'paths', prefix: 'WALK_PATH' },
        walls: { type: 'wall', layer: 'walls', prefix: 'WALL' },
        objects: { type: 'restricted_zone', layer: 'restricted', prefix: 'OBJECT' },
        'door-lights': { type: 'access_light', layer: 'lights', prefix: 'DOOR_LIGHT' },
        computers: { type: 'computer', layer: 'computers', prefix: 'COMPUTER' },
    }[category] as Readonly<{ type: OfficeEntity['type']; layer: OfficeLayer; prefix: string }>;
    const displayId = `${config.prefix}_${String(index + 1).padStart(3, '0')}`;
    const metadata = {
        sourceRecordId: sourceId,
        annotationId: text(source.annotationId, 'unknown'),
        pdfObjectId: typeof source.pdfObjectId === 'number' ? source.pdfObjectId : -1,
    };
    const common = {
        category,
        name: displayId,
        type: config.type,
        layer: config.layer,
        metadata,
        path: config.type === 'walk_path' ? {
            nodeIds: [],
            linkedIntersectionIds: [],
            linkedRoomIds: [],
            linkedDoorIds: [],
            direction: 'bidirectional' as const,
            blocked: false,
        } : undefined,
    };
    if (native.kind === 'polygon') {
        return [candidateEntity({
            ...common,
            id: sourceId,
            geometry: { kind: 'polygon', points: points(native.points, `${sourceId}.points`) },
        })];
    }
    if (native.kind === 'rectangle') {
        const bounds = record(native.rect, `${sourceId}.rect`);
        const x1 = finite(bounds.x1, `${sourceId}.rect.x1`);
        const x2 = finite(bounds.x2, `${sourceId}.rect.x2`);
        const y1 = finite(bounds.y1, `${sourceId}.rect.y1`);
        const y2 = finite(bounds.y2, `${sourceId}.rect.y2`);
        return [candidateEntity({
            ...common,
            id: sourceId,
            geometry: {
                kind: 'rectangle',
                rect: {
                    x: Math.min(x1, x2),
                    y: Math.min(y1, y2),
                    width: Math.abs(x2 - x1),
                    height: Math.abs(y2 - y1),
                },
            },
        })];
    }
    if (native.kind === 'ink') {
        const style = record(source.style, `${sourceId}.style`);
        const width = Math.max(8, typeof style.width === 'number' ? style.width * 16 / 9 : 8);
        return array(native.paths, `${sourceId}.paths`).map((path, pathIndex, pathsValue) => candidateEntity({
            ...common,
            id: pathsValue.length === 1 ? sourceId : `${sourceId}.path-${pathIndex + 1}`,
            name: pathsValue.length === 1 ? displayId : `${displayId} path ${pathIndex + 1}/${pathsValue.length}`,
            geometry: { kind: 'polyline', points: points(path, `${sourceId}.paths[${pathIndex}]`), width },
        }));
    }
    throw new Error(`Floor 1 candidate ${sourceId} has unsupported geometry kind "${String(native.kind)}".`);
}

function provisionalData(category: Floor1CandidateCategory, value: unknown): UnknownRecord {
    const wrapper = record(value, category);
    if (wrapper.registrationStatus !== 'candidate-unverified' || wrapper.productionApproved !== false) {
        throw new Error(`Floor 1 candidate ${category} crossed the production approval boundary.`);
    }
    return record(wrapper.data, `${category}.data`);
}

export function buildFloor1CandidateOverlay(documents: CandidateDocuments): OfficeOverlayDocument {
    const data = Object.fromEntries(FLOOR1_CANDIDATE_CATEGORIES.map(category => [
        category,
        provisionalData(category, documents[category]),
    ])) as Record<Floor1CandidateCategory, UnknownRecord>;
    const entities: OfficeEntity[] = [];

    for (const value of array(data.rooms.rooms, 'rooms.data.rooms')) {
        const room = record(value, 'room');
        const sourceId = text(room.id, 'ROOM_UNKNOWN');
        entities.push(polygonEntity('rooms', room, 'room', 'rooms', `${text(room.canonicalName, sourceId)} (${sourceId})`));
    }
    for (const category of ['walk-paths', 'walls', 'objects', 'door-lights', 'computers'] as const) {
        array(data[category].records, `${category}.data.records`).forEach((value, index) => {
            entities.push(...nativeGeometryEntities(category, record(value, `${category}[${index}]`), index));
        });
    }
    for (const value of array(data.doors.doors, 'doors.data.doors')) {
        const door = record(value, 'door');
        const sourceId = text(door.id, 'D_UNKNOWN');
        const facts = record(door.authoredFacts, `${sourceId}.authoredFacts`);
        const mode = text(door.csvAccessMode, 'blocked');
        const accessStates: Readonly<Record<string, AccessState>> = {
            open: 'green',
            blocked: 'red',
            restricted: 'blue',
            event: 'yellow',
            elevator: 'green',
        };
        const state = accessStates[mode] ?? 'red';
        const entity = polygonEntity('doors', door, 'door', 'doors', `${sourceId} — ${text(facts.location_name, 'candidate door')}`);
        entities.push({
            ...entity,
            accessState: state,
            accessPolicy: { state },
            door: {
                currentState: state,
                defaultState: state,
                linkedRoomIds: [],
                locked: state === 'red',
                visualState: 'closed',
            },
        });
    }
    array(data.positions.positions, 'positions.data.positions').forEach((value, index) => {
        const position = record(value, `positions[${index}]`);
        const sourceId = text(position.id, `POSITION_${String(index + 1).padStart(3, '0')}`);
        entities.push(candidateEntity({
            category: 'positions',
            id: sourceId,
            name: sourceId,
            type: 'desk',
            layer: 'furniture',
            geometry: { kind: 'point', point: point(position.pdfAnchor, `${sourceId}.pdfAnchor`) },
            metadata: {
                sourceRecordId: sourceId,
                poseCandidate: text(position.poseCandidate, 'unresolved'),
            },
            seatPriority: position.accessTier === 'priority' ? 'yellow' : 'red',
        }));
    });
    for (const value of array(data['interactive-objects'].interactiveObjects, 'interactive-objects.data.interactiveObjects')) {
        const item = record(value, 'interactive object');
        const sourceId = text(item.id, 'INTERACTIVE_UNKNOWN');
        entities.push(polygonEntity(
            'interactive-objects',
            item,
            'interaction_zone',
            'hitboxes',
            `${text(item.name, sourceId)} (${sourceId})`,
        ));
    }

    return assertValidOverlayDocument({
        schemaVersion: 1,
        source: { width: 8192, height: 5460 },
        production: false,
        entities,
        pathNodes: [],
    });
}

export function candidateEntityCounts(document: OfficeOverlayDocument): Record<Floor1CandidateCategory, number> {
    const counts = Object.fromEntries(FLOOR1_CANDIDATE_CATEGORIES.map(category => [category, 0])) as Record<Floor1CandidateCategory, number>;
    for (const entity of document.entities) {
        const category = entity.metadata.candidateCategory;
        if (typeof category === 'string' && FLOOR1_CANDIDATE_CATEGORIES.includes(category as Floor1CandidateCategory)) {
            counts[category as Floor1CandidateCategory] += 1;
        }
    }
    return counts;
}

export async function loadFloor1CandidateOverlay(
    fetcher: typeof fetch = fetch,
): Promise<OfficeOverlayDocument> {
    const entries = await Promise.all(FLOOR1_CANDIDATE_CATEGORIES.map(async category => {
        let response: Response;
        try {
            response = await fetcher(`${import.meta.env.BASE_URL}assets/office-candidate/${category}.json`);
        } catch (error) {
            const detail = error instanceof Error ? error.message : 'request failed';
            throw new Error(`Floor 1 candidate ${category} request failed: ${detail}`, { cause: error });
        }
        if (!response.ok) throw new Error(`Floor 1 candidate ${category} failed to load (${response.status}).`);
        try {
            return [category, await response.json()] as const;
        } catch {
            throw new Error(`Floor 1 candidate ${category} is malformed JSON.`);
        }
    }));
    try {
        return buildFloor1CandidateOverlay(Object.fromEntries(entries) as CandidateDocuments);
    } catch (error) {
        const detail = error instanceof Error ? error.message : 'overlay validation failed';
        throw new Error(`Floor 1 candidate overlay validation failed: ${detail}`, { cause: error });
    }
}
