import { OFFICE_SOURCE_HEIGHT, OFFICE_SOURCE_WIDTH } from './constants';
import { Point, Rect, ViewTransform, ViewportSize } from './types';

function assertFinite(...values: number[]): void {
    if (values.some(value => !Number.isFinite(value))) {
        throw new Error('Coordinate values must be finite numbers.');
    }
}

export function screenToOffice(point: Point, transform: ViewTransform): Point {
    assertFinite(point.x, point.y, transform.x, transform.y, transform.scale);
    if (transform.scale <= 0) throw new Error('Transform scale must be positive.');
    return {
        x: (point.x - transform.x) / transform.scale,
        y: (point.y - transform.y) / transform.scale,
    };
}

export function officeToScreen(point: Point, transform: ViewTransform): Point {
    assertFinite(point.x, point.y, transform.x, transform.y, transform.scale);
    if (transform.scale <= 0) throw new Error('Transform scale must be positive.');
    return {
        x: point.x * transform.scale + transform.x,
        y: point.y * transform.scale + transform.y,
    };
}

export function officeRectToScreen(rect: Rect, transform: ViewTransform): Rect {
    const origin = officeToScreen(rect, transform);
    return {
        x: origin.x,
        y: origin.y,
        width: rect.width * transform.scale,
        height: rect.height * transform.scale,
    };
}

export function officePolygonToScreen(points: readonly Point[], transform: ViewTransform): Point[] {
    return points.map(point => officeToScreen(point, transform));
}

export function normalizedToSource(
    point: Point,
    sourceWidth = OFFICE_SOURCE_WIDTH,
    sourceHeight = OFFICE_SOURCE_HEIGHT,
): Point {
    assertFinite(point.x, point.y, sourceWidth, sourceHeight);
    return { x: point.x * sourceWidth, y: point.y * sourceHeight };
}

export function sourceToNormalized(
    point: Point,
    sourceWidth = OFFICE_SOURCE_WIDTH,
    sourceHeight = OFFICE_SOURCE_HEIGHT,
): Point {
    assertFinite(point.x, point.y, sourceWidth, sourceHeight);
    if (sourceWidth <= 0 || sourceHeight <= 0) throw new Error('Source dimensions must be positive.');
    return { x: point.x / sourceWidth, y: point.y / sourceHeight };
}

export function markupToSource(
    point: Point,
    markupWidth: number,
    markupHeight: number,
    sourceWidth = OFFICE_SOURCE_WIDTH,
    sourceHeight = OFFICE_SOURCE_HEIGHT,
): Point {
    assertFinite(point.x, point.y, markupWidth, markupHeight, sourceWidth, sourceHeight);
    if (markupWidth <= 0 || markupHeight <= 0) throw new Error('Markup dimensions must be positive.');
    return {
        x: point.x * (sourceWidth / markupWidth),
        y: point.y * (sourceHeight / markupHeight),
    };
}

export function fitTransform(
    viewport: ViewportSize,
    sourceWidth = OFFICE_SOURCE_WIDTH,
    sourceHeight = OFFICE_SOURCE_HEIGHT,
): ViewTransform {
    assertFinite(viewport.width, viewport.height, sourceWidth, sourceHeight);
    if (viewport.width <= 0 || viewport.height <= 0 || sourceWidth <= 0 || sourceHeight <= 0) {
        throw new Error('Viewport and source dimensions must be positive.');
    }
    const scale = Math.min(viewport.width / sourceWidth, viewport.height / sourceHeight);
    return {
        scale,
        x: (viewport.width - sourceWidth * scale) / 2,
        y: (viewport.height - sourceHeight * scale) / 2,
    };
}

export function zoomAtScreenPoint(
    transform: ViewTransform,
    screenPoint: Point,
    nextScale: number,
): ViewTransform {
    assertFinite(nextScale);
    if (nextScale <= 0) throw new Error('Zoom scale must be positive.');
    const officePoint = screenToOffice(screenPoint, transform);
    return {
        scale: nextScale,
        x: screenPoint.x - officePoint.x * nextScale,
        y: screenPoint.y - officePoint.y * nextScale,
    };
}

export function constrainTransform(
    transform: ViewTransform,
    viewport: ViewportSize,
    sourceWidth = OFFICE_SOURCE_WIDTH,
    sourceHeight = OFFICE_SOURCE_HEIGHT,
    padding = 72,
): ViewTransform {
    const contentWidth = sourceWidth * transform.scale;
    const contentHeight = sourceHeight * transform.scale;
    const minX = Math.min(padding - contentWidth, viewport.width - padding);
    const maxX = Math.max(padding - contentWidth, viewport.width - padding);
    const minY = Math.min(padding - contentHeight, viewport.height - padding);
    const maxY = Math.max(padding - contentHeight, viewport.height - padding);
    return {
        ...transform,
        x: Math.min(maxX, Math.max(minX, transform.x)),
        y: Math.min(maxY, Math.max(minY, transform.y)),
    };
}
