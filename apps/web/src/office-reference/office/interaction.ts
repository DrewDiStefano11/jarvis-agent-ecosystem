import { constrainTransform } from './coordinates';
import { geometryBounds, geometryCenter } from './geometry';
import { OfficeEntity, OfficeLayer, ViewTransform, ViewportSize } from './types';

export function panTransform(
    transform: ViewTransform,
    deltaX: number,
    deltaY: number,
    viewport: ViewportSize,
    sourceWidth: number,
    sourceHeight: number,
    padding = 72,
): ViewTransform {
    return constrainTransform(
        { ...transform, x: transform.x + deltaX, y: transform.y + deltaY },
        viewport,
        sourceWidth,
        sourceHeight,
        padding,
    );
}

export function focusEntityTransform(
    entity: OfficeEntity,
    transform: ViewTransform,
    viewport: ViewportSize,
    maximumZoom: number,
): ViewTransform {
    const center = geometryCenter(entity.geometry);
    const bounds = geometryBounds(entity.geometry);
    const fitScale = bounds.width > 0 && bounds.height > 0
        ? Math.min(viewport.width / (bounds.width * 1.8), viewport.height / (bounds.height * 1.8))
        : transform.scale * 1.5;
    const scale = Math.min(maximumZoom, Math.max(transform.scale, fitScale));
    return {
        scale,
        x: viewport.width / 2 - center.x * scale,
        y: viewport.height / 2 - center.y * scale,
    };
}

export type FocusRequestResolution = Readonly<{
    request: number;
    transform: ViewTransform;
}>;

export function resolveFocusRequest(
    request: number,
    lastHandledRequest: number,
    entity: OfficeEntity | undefined,
    transform: ViewTransform,
    viewport: ViewportSize,
    maximumZoom: number,
): FocusRequestResolution | null {
    if (request <= 0 || request <= lastHandledRequest || !entity) return null;
    if (!Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) ||
        viewport.width <= 1 || viewport.height <= 1) return null;
    return {
        request,
        transform: focusEntityTransform(entity, transform, viewport, maximumZoom),
    };
}

export function reconcileSelection(selectedId: string | null, entities: readonly OfficeEntity[]): string | null {
    if (!selectedId) return null;
    return entities.some(entity => entity.id === selectedId && entity.enabled) ? selectedId : null;
}

export function toggleLayerVisibility(
    layers: ReadonlySet<OfficeLayer>,
    layer: OfficeLayer,
): ReadonlySet<OfficeLayer> {
    const next = new Set(layers);
    if (next.has(layer)) next.delete(layer);
    else next.add(layer);
    return next;
}
