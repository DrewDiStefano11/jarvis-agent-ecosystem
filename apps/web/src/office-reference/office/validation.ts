import { isSpriteSheetAssetId } from './assets';
import { OFFICE_SOURCE_HEIGHT, OFFICE_SOURCE_WIDTH } from './constants';
import { LAYER_ORDER } from './layers';
import {
    AccessState,
    OfficeEntity,
    OfficeEntityType,
    OfficeGeometry,
    OfficeLayer,
    OfficeOverlayDocument,
    PathNode,
    Point,
    SeatPriority,
} from './types';

export type ValidationResult =
    | Readonly<{ valid: true; value: OfficeOverlayDocument; errors: readonly [] }>
    | Readonly<{ valid: false; errors: readonly string[] }>;

const ENTITY_TYPES: readonly OfficeEntityType[] = [
    'room', 'walk_path', 'wall', 'door', 'desk', 'computer', 'access_light',
    'effect_zone', 'sprite_anchor', 'restricted_zone', 'interaction_zone', 'label_anchor',
];
const ACCESS_STATES: readonly AccessState[] = ['green', 'blue', 'yellow', 'red'];
const SEAT_PRIORITIES: readonly SeatPriority[] = ['yellow', 'red'];
const BLEND_MODES = ['normal', 'screen', 'multiply'] as const;
const ID_PATTERN = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const CSS_HEX_COLOR_PATTERN = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const TYPE_LAYER: Record<OfficeEntityType, OfficeLayer> = {
    room: 'rooms',
    walk_path: 'paths',
    wall: 'walls',
    door: 'doors',
    desk: 'furniture',
    computer: 'computers',
    access_light: 'lights',
    effect_zone: 'effects',
    sprite_anchor: 'sprites',
    restricted_zone: 'restricted',
    interaction_zone: 'hitboxes',
    label_anchor: 'labels',
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finite(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Overlay glow colors intentionally support deterministic CSS hexadecimal
 * forms only: RGB, RGBA, RRGGBB, and RRGGBBAA.
 */
export function isSupportedCssColor(value: unknown): value is string {
    return typeof value === 'string' && CSS_HEX_COLOR_PATTERN.test(value);
}

function validateStringArray(value: unknown, path: string, errors: string[], required: boolean): void {
    if (value === undefined && !required) return;
    if (!Array.isArray(value) || value.some(item => typeof item !== 'string' || item.trim() === '')) {
        errors.push(`${path} must be an array of nonempty strings.`);
    }
}

function validatePoint(
    point: unknown,
    path: string,
    errors: string[],
    width: number,
    height: number,
    allowOut: boolean,
): point is Point {
    if (!isRecord(point) || !finite(point.x) || !finite(point.y)) {
        errors.push(`${path} must contain finite x and y values.`);
        return false;
    }
    if (!allowOut && (point.x < 0 || point.x > width || point.y < 0 || point.y > height)) {
        errors.push(`${path} is outside the ${width}×${height} source bounds.`);
    }
    return true;
}

function polygonArea(points: readonly Point[]): number {
    let twiceArea = 0;
    for (let index = 0; index < points.length; index += 1) {
        const current = points[index];
        const next = points[(index + 1) % points.length];
        if (!current || !next) throw new Error("Polygon vertex is missing.");
        twiceArea += current.x * next.y - next.x * current.y;
    }
    return Math.abs(twiceArea) / 2;
}

function validateGeometry(
    geometry: unknown,
    path: string,
    errors: string[],
    width: number,
    height: number,
    allowOut: boolean,
): geometry is OfficeGeometry {
    if (!isRecord(geometry) || typeof geometry.kind !== 'string') {
        errors.push(`${path} is malformed.`);
        return false;
    }
    if (geometry.kind === 'point') {
        return validatePoint(geometry.point, `${path}.point`, errors, width, height, allowOut);
    }
    if (geometry.kind === 'rectangle') {
        const rect = geometry.rect;
        if (!isRecord(rect) || !finite(rect.x) || !finite(rect.y) || !finite(rect.width) || !finite(rect.height)) {
            errors.push(`${path}.rect must contain finite x, y, width, and height values.`);
            return false;
        }
        if (rect.width <= 0 || rect.height <= 0) errors.push(`${path}.rect must have positive area.`);
        validatePoint({ x: rect.x, y: rect.y }, `${path}.rect origin`, errors, width, height, allowOut);
        validatePoint({ x: rect.x + rect.width, y: rect.y + rect.height }, `${path}.rect extent`, errors, width, height, allowOut);
        return true;
    }
    if (geometry.kind === 'polygon' || geometry.kind === 'polyline') {
        if (!Array.isArray(geometry.points)) {
            errors.push(`${path}.points must be an array.`);
            return false;
        }
        const minimum = geometry.kind === 'polygon' ? 3 : 2;
        if (geometry.points.length < minimum) errors.push(`${path} requires at least ${minimum} points.`);
        let pointsAreValid = true;
        geometry.points.forEach((point, index) => {
            if (!validatePoint(point, `${path}.points[${index}]`, errors, width, height, allowOut)) pointsAreValid = false;
        });
        for (let index = 1; index < geometry.points.length; index += 1) {
            const previous = geometry.points[index - 1];
            const current = geometry.points[index];
            if (isRecord(previous) && isRecord(current) && previous.x === current.x && previous.y === current.y) {
                errors.push(`${path} contains a zero-length segment at index ${index - 1}.`);
            }
        }
        if (geometry.kind === 'polygon' && pointsAreValid && geometry.points.length >= 3 &&
            polygonArea(geometry.points as Point[]) <= Number.EPSILON) {
            errors.push(`${path} must have nonzero polygon area.`);
        }
        if (geometry.kind === 'polyline' && (!finite(geometry.width) || geometry.width <= 0)) {
            errors.push(`${path}.width must be a positive finite number.`);
        }
        return true;
    }
    errors.push(`${path}.kind is unsupported.`);
    return false;
}

function validateAccessPolicy(value: unknown, path: string, errors: string[]): void {
    if (value === undefined) return;
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return;
    }
    if (!ACCESS_STATES.includes(value.state as AccessState)) errors.push(`${path}.state is invalid.`);
    for (const field of ['memberIds', 'roleIds', 'teamIds', 'rankGroups'] as const) {
        validateStringArray(value[field], `${path}.${field}`, errors, false);
    }
}

function validateDoor(value: unknown, path: string, errors: string[], required: boolean): void {
    if (value === undefined && !required) return;
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return;
    }
    if (!ACCESS_STATES.includes(value.currentState as AccessState) ||
        !ACCESS_STATES.includes(value.defaultState as AccessState)) errors.push(`${path} access state is invalid.`);
    validateStringArray(value.linkedRoomIds, `${path}.linkedRoomIds`, errors, true);
    validateStringArray(value.reservedGroupIds, `${path}.reservedGroupIds`, errors, false);
    if (value.scheduleReference !== undefined &&
        (typeof value.scheduleReference !== 'string' || value.scheduleReference.trim() === '')) {
        errors.push(`${path}.scheduleReference must be a nonempty string.`);
    }
    if (!['open', 'closed'].includes(String(value.visualState))) errors.push(`${path}.visualState is invalid.`);
    if (typeof value.locked !== 'boolean') errors.push(`${path}.locked must be boolean.`);
}

function validatePath(value: unknown, path: string, errors: string[], required: boolean): void {
    if (value === undefined && !required) return;
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return;
    }
    for (const field of ['nodeIds', 'linkedIntersectionIds', 'linkedRoomIds', 'linkedDoorIds'] as const) {
        validateStringArray(value[field], `${path}.${field}`, errors, true);
    }
    validateStringArray(value.restrictedSegmentIds, `${path}.restrictedSegmentIds`, errors, false);
    if (!['one_way', 'bidirectional'].includes(String(value.direction))) errors.push(`${path}.direction is invalid.`);
    if (typeof value.blocked !== 'boolean') errors.push(`${path}.blocked must be boolean.`);
}

function validateSprite(value: unknown, path: string, errors: string[], required: boolean): void {
    if (value === undefined && !required) return;
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return;
    }
    if (typeof value.assetId !== 'string' || value.assetId.trim() === '') {
        errors.push(`${path}.assetId is required.`);
    } else if (!isSpriteSheetAssetId(value.assetId)) {
        errors.push(`${path}.assetId "${value.assetId}" is not a registered sprite-sheet asset.`);
    }
    if (!finite(value.scale) || value.scale <= 0) errors.push(`${path}.scale must be a positive finite number.`);
    if (!finite(value.opacity) || value.opacity < 0 || value.opacity > 1) errors.push(`${path}.opacity must be between 0 and 1.`);
    if (value.glow !== undefined && !isSupportedCssColor(value.glow)) {
        errors.push(`${path}.glow is not a supported color.`);
    }
    if (value.blendMode !== undefined && !BLEND_MODES.includes(value.blendMode as never)) {
        errors.push(`${path}.blendMode is invalid.`);
    }
    if (value.pointerEvents !== undefined && typeof value.pointerEvents !== 'boolean') {
        errors.push(`${path}.pointerEvents must be boolean.`);
    }
    const animation = value.animation;
    if (!isRecord(animation)) {
        errors.push(`${path}.animation must be an object.`);
        return;
    }
    for (const field of ['frameWidth', 'frameHeight', 'frameCount', 'columns', 'frameDurationMs'] as const) {
        if (!finite(animation[field]) || animation[field] <= 0 || !Number.isInteger(animation[field])) {
            errors.push(`${path}.animation.${field} must be a positive integer.`);
        }
    }
    if (!Array.isArray(animation.frameSequence) || animation.frameSequence.length === 0 ||
        animation.frameSequence.some(frame => !Number.isInteger(frame) || frame < 0 ||
            (finite(animation.frameCount) && frame >= animation.frameCount))) {
        errors.push(`${path}.animation.frameSequence contains invalid frames.`);
    }
    for (const field of ['loop', 'pingPong', 'idle'] as const) {
        if (typeof animation[field] !== 'boolean') errors.push(`${path}.animation.${field} must be boolean.`);
    }
}

function validateMetadata(value: unknown, path: string, errors: string[]): void {
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return;
    }
    Object.entries(value).forEach(([key, item]) => {
        if (item !== null && typeof item !== 'string' && typeof item !== 'boolean' && !finite(item)) {
            errors.push(`${path}.${key} must be a string, finite number, boolean, or null.`);
        }
    });
}

function validateEntity(
    value: unknown,
    index: number,
    errors: string[],
    width: number,
    height: number,
): value is OfficeEntity {
    const path = `entities[${index}]`;
    if (!isRecord(value)) {
        errors.push(`${path} must be an object.`);
        return false;
    }
    const typeIsValid = typeof value.type === 'string' && ENTITY_TYPES.includes(value.type as OfficeEntityType);
    const layerIsValid = typeof value.sourceLayer === 'string' && LAYER_ORDER.includes(value.sourceLayer as OfficeLayer);
    if (typeof value.id !== 'string' || !ID_PATTERN.test(value.id)) errors.push(`${path}.id is invalid.`);
    if (!typeIsValid) errors.push(`${path}.type is invalid.`);
    if (typeof value.name !== 'string' || value.name.trim() === '') errors.push(`${path}.name is required.`);
    if (!layerIsValid) errors.push(`${path}.sourceLayer is invalid.`);
    if (typeIsValid && layerIsValid && TYPE_LAYER[value.type as OfficeEntityType] !== value.sourceLayer) {
        errors.push(`${path}.sourceLayer does not match entity type ${String(value.type)}.`);
    }
    if (typeof value.enabled !== 'boolean') errors.push(`${path}.enabled must be boolean.`);
    if (typeof value.interactive !== 'boolean') errors.push(`${path}.interactive must be boolean.`);
    validateMetadata(value.metadata, `${path}.metadata`, errors);
    if (!finite(value.zIndex) || !Number.isInteger(value.zIndex)) errors.push(`${path}.zIndex must be an integer.`);
    validateStringArray(value.tags, `${path}.tags`, errors, false);
    validateStringArray(value.linkedEntityIds, `${path}.linkedEntityIds`, errors, false);
    if (value.parentId !== undefined && (typeof value.parentId !== 'string' || value.parentId.trim() === '')) {
        errors.push(`${path}.parentId must be a nonempty string.`);
    }
    if (value.allowOutOfBounds !== undefined && typeof value.allowOutOfBounds !== 'boolean') {
        errors.push(`${path}.allowOutOfBounds must be boolean.`);
    }
    validateGeometry(value.geometry, `${path}.geometry`, errors, width, height, value.allowOutOfBounds === true);
    if (value.type === 'sprite_anchor' && (!isRecord(value.geometry) || value.geometry.kind !== 'point')) {
        errors.push(`${path}.geometry must use point geometry for sprite anchors.`);
    }
    if (value.accessState !== undefined && !ACCESS_STATES.includes(value.accessState as AccessState)) {
        errors.push(`${path}.accessState is invalid.`);
    }
    if (value.seatPriority !== undefined && !SEAT_PRIORITIES.includes(value.seatPriority as SeatPriority)) {
        errors.push(`${path}.seatPriority is invalid.`);
    }
    if (value.seatPriority !== undefined && value.type !== 'desk') {
        errors.push(`${path}.seatPriority is only valid for desks.`);
    }
    validateAccessPolicy(value.accessPolicy, `${path}.accessPolicy`, errors);
    validateDoor(value.door, `${path}.door`, errors, value.type === 'door');
    validatePath(value.path, `${path}.path`, errors, value.type === 'walk_path');
    validateSprite(value.sprite, `${path}.sprite`, errors, value.type === 'sprite_anchor');
    if (value.type !== 'door' && value.door !== undefined) errors.push(`${path}.door is only valid for door entities.`);
    if (value.type !== 'walk_path' && value.path !== undefined) errors.push(`${path}.path is only valid for walk paths.`);
    if (value.type !== 'sprite_anchor' && value.sprite !== undefined) errors.push(`${path}.sprite is only valid for sprite anchors.`);
    return true;
}

function validatePathNodes(value: unknown, errors: string[], width: number, height: number): value is readonly PathNode[] {
    if (!Array.isArray(value)) {
        errors.push('pathNodes must be an array.');
        return false;
    }
    value.forEach((node, index) => {
        if (!isRecord(node)) {
            errors.push(`pathNodes[${index}] must be an object.`);
            return;
        }
        if (typeof node.id !== 'string' || !ID_PATTERN.test(node.id)) errors.push(`pathNodes[${index}].id is invalid.`);
        validatePoint(node.point, `pathNodes[${index}].point`, errors, width, height, false);
        validateStringArray(node.connectedNodeIds, `pathNodes[${index}].connectedNodeIds`, errors, true);
    });
    return true;
}

function validateTypedReference(
    reference: unknown,
    expectedType: OfficeEntityType,
    path: string,
    entityTypes: ReadonlyMap<string, OfficeEntityType>,
    errors: string[],
): void {
    if (typeof reference !== 'string') return;
    const actualType = entityTypes.get(reference);
    if (!actualType) errors.push(`${path} references unknown entity ${reference}.`);
    else if (actualType !== expectedType) errors.push(`${path} must reference a ${expectedType}, but ${reference} is ${actualType}.`);
}

export function validateOverlayDocument(input: unknown): ValidationResult {
    const errors: string[] = [];
    if (!isRecord(input)) return { valid: false, errors: ['Overlay document must be an object.'] };
    if (input.schemaVersion !== 1) errors.push(`Unsupported schema version: ${String(input.schemaVersion)}.`);
    const source = input.source;
    if (!isRecord(source) || !finite(source.width) || !finite(source.height) || source.width <= 0 || source.height <= 0) {
        errors.push('source must define positive finite width and height.');
    } else if (source.width !== OFFICE_SOURCE_WIDTH || source.height !== OFFICE_SOURCE_HEIGHT) {
        errors.push(`source must match the canonical ${OFFICE_SOURCE_WIDTH}×${OFFICE_SOURCE_HEIGHT} office image.`);
    }
    if (typeof input.production !== 'boolean') errors.push('production must be boolean.');
    const width = isRecord(source) && finite(source.width) ? source.width : 0;
    const height = isRecord(source) && finite(source.height) ? source.height : 0;
    if (!Array.isArray(input.entities)) errors.push('entities must be an array.');
    else input.entities.forEach((entity, index) => validateEntity(entity, index, errors, width, height));
    validatePathNodes(input.pathNodes, errors, width, height);

    if (Array.isArray(input.entities)) {
        const ids = input.entities
            .map(entity => isRecord(entity) ? entity.id : undefined)
            .filter((id): id is string => typeof id === 'string');
        ids.filter((id, index) => ids.indexOf(id) !== index)
            .filter((id, index, duplicates) => duplicates.indexOf(id) === index)
            .forEach(id => errors.push(`Duplicate entity ID: ${id}.`));
        const entityTypes = new Map<string, OfficeEntityType>();
        input.entities.forEach(entity => {
            if (isRecord(entity) && typeof entity.id === 'string' &&
                typeof entity.type === 'string' && ENTITY_TYPES.includes(entity.type as OfficeEntityType)) {
                entityTypes.set(entity.id, entity.type as OfficeEntityType);
            }
        });
        input.entities.forEach((entity, index) => {
            if (!isRecord(entity)) return;
            const genericReferences = [
                ...(Array.isArray(entity.linkedEntityIds) ? entity.linkedEntityIds : []),
                ...(typeof entity.parentId === 'string' ? [entity.parentId] : []),
            ];
            genericReferences.forEach(reference => {
                if (typeof reference === 'string' && !entityTypes.has(reference)) {
                    errors.push(`entities[${index}] references unknown entity ${reference}.`);
                }
            });
            if (isRecord(entity.door) && Array.isArray(entity.door.linkedRoomIds)) {
                entity.door.linkedRoomIds.forEach(reference =>
                    validateTypedReference(reference, 'room', `entities[${index}].door.linkedRoomIds`, entityTypes, errors));
            }
            if (isRecord(entity.path)) {
                if (Array.isArray(entity.path.linkedRoomIds)) {
                    entity.path.linkedRoomIds.forEach(reference =>
                        validateTypedReference(reference, 'room', `entities[${index}].path.linkedRoomIds`, entityTypes, errors));
                }
                if (Array.isArray(entity.path.linkedDoorIds)) {
                    entity.path.linkedDoorIds.forEach(reference =>
                        validateTypedReference(reference, 'door', `entities[${index}].path.linkedDoorIds`, entityTypes, errors));
                }
            }
        });
    }

    if (Array.isArray(input.pathNodes)) {
        const nodeIds = input.pathNodes
            .map(node => isRecord(node) ? node.id : undefined)
            .filter((id): id is string => typeof id === 'string');
        const nodeSet = new Set(nodeIds);
        nodeIds.filter((id, index) => nodeIds.indexOf(id) !== index)
            .filter((id, index, duplicates) => duplicates.indexOf(id) === index)
            .forEach(id => errors.push(`Duplicate path node ID: ${id}.`));
        input.pathNodes.forEach((node, index) => {
            if (!isRecord(node) || !Array.isArray(node.connectedNodeIds)) return;
            node.connectedNodeIds.forEach(reference => {
                if (typeof reference === 'string' && !nodeSet.has(reference)) {
                    errors.push(`pathNodes[${index}] references unknown node ${reference}.`);
                }
            });
        });
        if (Array.isArray(input.entities)) {
            input.entities.forEach((entity, index) => {
                if (!isRecord(entity) || !isRecord(entity.path)) return;
                for (const field of ['nodeIds', 'linkedIntersectionIds'] as const) {
                    if (!Array.isArray(entity.path[field])) continue;
                    entity.path[field].forEach(reference => {
                        if (typeof reference === 'string' && !nodeSet.has(reference)) {
                            errors.push(`entities[${index}].path.${field} references unknown node ${reference}.`);
                        }
                    });
                }
            });
        }
    }
    if (errors.length > 0) return { valid: false, errors };
    return { valid: true, value: input as unknown as OfficeOverlayDocument, errors: [] };
}

export function assertValidOverlayDocument(input: unknown): OfficeOverlayDocument {
    const result = validateOverlayDocument(input);
    if (!result.valid) throw new Error(`Invalid office overlay:\n${result.errors.join('\n')}`);
    return result.value;
}
