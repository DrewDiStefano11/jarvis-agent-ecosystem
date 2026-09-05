import { OfficeGeometry, Point, Rect } from './types';

export function geometryBounds(geometry: OfficeGeometry): Rect {
    if (geometry.kind === 'point') {
        return { x: geometry.point.x, y: geometry.point.y, width: 0, height: 0 };
    }
    if (geometry.kind === 'rectangle') return geometry.rect;
    const xs = geometry.points.map(point => point.x);
    const ys = geometry.points.map(point => point.y);
    const padding = geometry.kind === 'polyline' ? geometry.width / 2 : 0;
    return {
        x: Math.min(...xs) - padding,
        y: Math.min(...ys) - padding,
        width: Math.max(...xs) - Math.min(...xs) + padding * 2,
        height: Math.max(...ys) - Math.min(...ys) + padding * 2,
    };
}

export function geometryCenter(geometry: OfficeGeometry): Point {
    const bounds = geometryBounds(geometry);
    return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
}

export function geometrySummary(geometry: OfficeGeometry): string {
    if (geometry.kind === 'point') return `Point (${geometry.point.x}, ${geometry.point.y})`;
    if (geometry.kind === 'rectangle') {
        const { x, y, width, height } = geometry.rect;
        return `Rectangle (${x}, ${y}) ${width} × ${height}`;
    }
    return `${geometry.kind === 'polygon' ? 'Polygon' : 'Polyline'} · ${geometry.points.length} vertices`;
}
