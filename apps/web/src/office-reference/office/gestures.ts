import { screenToOffice } from './coordinates';
import { Point, ViewTransform } from './types';

export const PAN_SELECTION_THRESHOLD = 6;

export type PanGesture = Readonly<{
    pointerId: number;
    start: Point;
    last: Point;
    distance: number;
}>;

export type PinchGesture = Readonly<{
    initialMidpoint: Point;
    initialOfficeAnchor: Point;
    initialDistance: number;
    initialTransform: ViewTransform;
}>;

export function beginPanGesture(pointerId: number, point: Point): PanGesture {
    return { pointerId, start: point, last: point, distance: 0 };
}

export function updatePanGesture(
    gesture: PanGesture,
    point: Point,
): Readonly<{ gesture: PanGesture; delta: Point }> {
    const delta = { x: point.x - gesture.last.x, y: point.y - gesture.last.y };
    return {
        delta,
        gesture: {
            ...gesture,
            last: point,
            distance: gesture.distance + Math.hypot(delta.x, delta.y),
        },
    };
}

export function shouldSuppressSelection(gesture: PanGesture | null): boolean {
    return (gesture?.distance ?? 0) >= PAN_SELECTION_THRESHOLD;
}

export function midpoint(a: Point, b: Point): Point {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

export function beginPinchGesture(a: Point, b: Point, transform: ViewTransform): PinchGesture {
    const initialMidpoint = midpoint(a, b);
    return {
        initialMidpoint,
        initialOfficeAnchor: screenToOffice(initialMidpoint, transform),
        initialDistance: Math.max(Math.hypot(a.x - b.x, a.y - b.y), Number.EPSILON),
        initialTransform: transform,
    };
}

export function updatePinchGesture(
    gesture: PinchGesture,
    a: Point,
    b: Point,
    minimumZoom: number,
    maximumZoom: number,
): ViewTransform {
    const currentDistance = Math.hypot(a.x - b.x, a.y - b.y);
    const currentMidpoint = midpoint(a, b);
    const scale = Math.min(
        maximumZoom,
        Math.max(minimumZoom, gesture.initialTransform.scale * currentDistance / gesture.initialDistance),
    );
    return {
        scale,
        x: currentMidpoint.x - gesture.initialOfficeAnchor.x * scale,
        y: currentMidpoint.y - gesture.initialOfficeAnchor.y * scale,
    };
}

export function remainingPointer(
    pointers: ReadonlyMap<number, Point>,
): Readonly<{ pointerId: number; point: Point }> | null {
    const entry = pointers.entries().next();
    if (entry.done) return null;
    return { pointerId: entry.value[0], point: entry.value[1] };
}

export function resumePanGesture(pointers: ReadonlyMap<number, Point>): PanGesture | null {
    const remaining = remainingPointer(pointers);
    return remaining ? beginPanGesture(remaining.pointerId, remaining.point) : null;
}
