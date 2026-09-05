import { OfficeEntity, OfficeEntityType, OfficeLayer } from './types';

export const LAYER_ORDER: readonly OfficeLayer[] = [
    'paths',
    'rooms',
    'restricted',
    'walls',
    'doors',
    'furniture',
    'computers',
    'lights',
    'effects',
    'sprites',
    'labels',
    'hitboxes',
] as const;

const TYPE_PRECEDENCE: Record<OfficeEntityType, number> = {
    room: 1,
    walk_path: 2,
    restricted_zone: 3,
    wall: 4,
    door: 5,
    desk: 6,
    computer: 7,
    access_light: 8,
    effect_zone: 9,
    sprite_anchor: 10,
    interaction_zone: 11,
    label_anchor: 12,
};

export function compareEntities(a: OfficeEntity, b: OfficeEntity): number {
    const layerDifference = LAYER_ORDER.indexOf(a.sourceLayer) - LAYER_ORDER.indexOf(b.sourceLayer);
    if (layerDifference !== 0) return layerDifference;
    if (a.zIndex !== b.zIndex) return a.zIndex - b.zIndex;
    const typeDifference = TYPE_PRECEDENCE[a.type] - TYPE_PRECEDENCE[b.type];
    return typeDifference || a.id.localeCompare(b.id);
}

export function pickTopEntity(entities: readonly OfficeEntity[]): OfficeEntity | undefined {
    const candidates = [...entities].filter(entity => entity.enabled && entity.interactive).sort(compareEntities);
    return candidates[candidates.length - 1];
}
