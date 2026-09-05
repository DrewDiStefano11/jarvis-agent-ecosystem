import { describe, expect, it } from 'vitest';
import {
    constrainTransform,
    fitTransform,
    markupToSource,
    normalizedToSource,
    officePolygonToScreen,
    officeRectToScreen,
    officeToScreen,
    screenToOffice,
    sourceToNormalized,
    zoomAtScreenPoint,
} from '../coordinates';

describe('office coordinate utilities', () => {
    it('converts screen and office coordinates without round-trip drift', () => {
        const transform = { scale: 1.375, x: -900.25, y: 331.75 };
        const source = { x: 4821.125, y: 2730.5 };
        const roundTrip = screenToOffice(officeToScreen(source, transform), transform);
        expect(roundTrip.x).toBeCloseTo(source.x, 10);
        expect(roundTrip.y).toBeCloseTo(source.y, 10);
    });

    it('keeps the office point beneath the pointer fixed while zooming', () => {
        const pointer = { x: 320, y: 240 };
        const before = { scale: 0.2, x: -100, y: 30 };
        const officePoint = screenToOffice(pointer, before);
        const after = zoomAtScreenPoint(before, pointer, 0.85);
        expect(officeToScreen(officePoint, after)).toEqual(pointer);
    });

    it('creates a centered, aspect-preserving fit transform', () => {
        const transform = fitTransform({ width: 1600, height: 900 });
        expect(transform.scale).toBeCloseTo(900 / 5460);
        expect(transform.y).toBeCloseTo(0);
        expect(transform.x).toBeGreaterThan(0);
    });

    it('converts normalized coordinates in both directions', () => {
        expect(normalizedToSource({ x: 0.5, y: 0.5 })).toEqual({ x: 4096, y: 2730 });
        expect(sourceToNormalized({ x: 4096, y: 2730 })).toEqual({ x: 0.5, y: 0.5 });
    });

    it('converts arbitrary lower-resolution markup coordinates', () => {
        expect(markupToSource({ x: 1024, y: 682.5 }, 2048, 1365)).toEqual({ x: 4096, y: 2730 });
    });

    it('converts rectangles and polygons using the same transform', () => {
        const transform = { scale: 2, x: 10, y: -20 };
        expect(officeRectToScreen({ x: 2, y: 3, width: 4, height: 5 }, transform))
            .toEqual({ x: 14, y: -14, width: 8, height: 10 });
        expect(officePolygonToScreen([{ x: 0, y: 0 }, { x: 5, y: 10 }], transform))
            .toEqual([{ x: 10, y: -20 }, { x: 20, y: 0 }]);
    });

    it('rejects invalid numeric values and dimensions', () => {
        expect(() => screenToOffice({ x: Number.NaN, y: 0 }, { scale: 1, x: 0, y: 0 })).toThrow();
        expect(() => markupToSource({ x: 0, y: 0 }, 0, 100)).toThrow();
        expect(() => zoomAtScreenPoint({ scale: 1, x: 0, y: 0 }, { x: 0, y: 0 }, Infinity)).toThrow();
    });

    it('constrains panning so content cannot become permanently lost', () => {
        const constrained = constrainTransform(
            { scale: 0.2, x: -100000, y: 100000 },
            { width: 1000, height: 700 },
        );
        expect(constrained.x).toBeGreaterThan(-2000);
        expect(constrained.y).toBeLessThan(800);
    });

    it('applies symmetric content-edge constraints when fitted content is smaller than the viewport', () => {
        const viewport = { width: 1200, height: 675 };
        const fitted = fitTransform(viewport);
        const contentWidth = 8192 * fitted.scale;
        const leftLimit = constrainTransform({ ...fitted, x: -100000 }, viewport);
        const rightLimit = constrainTransform({ ...fitted, x: 100000 }, viewport);

        expect(leftLimit.x).toBeCloseTo(72 - contentWidth);
        expect(rightLimit.x).toBeCloseTo(viewport.width - 72);
        expect(leftLimit.x + contentWidth).toBeCloseTo(72);
        expect(rightLimit.x).toBeCloseTo(viewport.width - 72);
    });
});
