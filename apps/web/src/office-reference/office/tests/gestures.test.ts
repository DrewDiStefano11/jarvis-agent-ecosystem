import { describe, expect, it } from 'vitest';
import { officeToScreen } from '../coordinates';
import {
    beginPanGesture,
    beginPinchGesture,
    remainingPointer,
    resumePanGesture,
    shouldSuppressSelection,
    updatePanGesture,
    updatePinchGesture,
} from '../gestures';

describe('pointer gesture helpers', () => {
    it('suppresses selection only after a drag crosses the pan threshold', () => {
        const click = updatePanGesture(beginPanGesture(1, { x: 10, y: 10 }), { x: 13, y: 12 }).gesture;
        expect(shouldSuppressSelection(click)).toBe(false);
        const drag = updatePanGesture(click, { x: 20, y: 12 }).gesture;
        expect(shouldSuppressSelection(drag)).toBe(true);
    });

    it('preserves normal click selection when the pointer does not move', () => {
        expect(shouldSuppressSelection(beginPanGesture(1, { x: 20, y: 30 }))).toBe(false);
    });

    it('translates a two-finger gesture without changing scale', () => {
        const initial = { scale: 0.5, x: -100, y: 50 };
        const pinch = beginPinchGesture({ x: 100, y: 100 }, { x: 200, y: 100 }, initial);
        const next = updatePinchGesture(pinch, { x: 140, y: 125 }, { x: 240, y: 125 }, 0.04, 8);
        expect(next.scale).toBe(initial.scale);
        expect(next.x).toBe(initial.x + 40);
        expect(next.y).toBe(initial.y + 25);
    });

    it('maps the initial office anchor to the current midpoint during translation and scaling', () => {
        const initial = { scale: 1, x: 20, y: -10 };
        const pinch = beginPinchGesture({ x: 100, y: 100 }, { x: 200, y: 100 }, initial);
        const next = updatePinchGesture(pinch, { x: 100, y: 150 }, { x: 300, y: 150 }, 0.04, 8);
        expect(next.scale).toBe(2);
        expect(officeToScreen(pinch.initialOfficeAnchor, next)).toEqual({ x: 200, y: 150 });
    });

    it('returns the remaining pointer so one-finger pan can continue after a pinch', () => {
        const pointers = new Map([[7, { x: 320, y: 240 }]]);
        expect(remainingPointer(pointers)).toEqual({ pointerId: 7, point: { x: 320, y: 240 } });
        expect(resumePanGesture(pointers)).toEqual({
            pointerId: 7,
            start: { x: 320, y: 240 },
            last: { x: 320, y: 240 },
            distance: 0,
        });
        expect(remainingPointer(new Map())).toBeNull();
        expect(resumePanGesture(new Map())).toBeNull();
    });
});
