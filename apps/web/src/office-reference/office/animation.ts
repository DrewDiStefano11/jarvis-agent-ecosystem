import { AnimationDefinition } from './types';

export type SpriteAssetState = 'loading' | 'ready' | 'missing';

export function spriteAssetStateAfterRuntimeLoad(
    loaded: boolean,
    dimensionsAreValid = false,
): SpriteAssetState {
    return loaded && dimensionsAreValid ? 'ready' : 'missing';
}

export function shouldRenderMissingSpriteFallback(state: SpriteAssetState): boolean {
    return state === 'missing';
}

export type SpriteFrameLayout = Readonly<{
    column: number;
    row: number;
    x: number;
    y: number;
}>;

export function spriteSheetDimensions(animation: AnimationDefinition): Readonly<{ width: number; height: number; rows: number }> {
    const rows = Math.ceil(animation.frameCount / animation.columns);
    return {
        width: animation.columns * animation.frameWidth,
        height: rows * animation.frameHeight,
        rows,
    };
}

export function spriteFrameLayout(
    frame: number,
    animation: AnimationDefinition,
    scale = 1,
): SpriteFrameLayout {
    const column = frame % animation.columns;
    const row = Math.floor(frame / animation.columns);
    return {
        column,
        row,
        x: -column * animation.frameWidth * scale,
        y: -row * animation.frameHeight * scale,
    };
}

export function hasValidSpriteSheetDimensions(
    naturalWidth: number,
    naturalHeight: number,
    animation: AnimationDefinition,
): boolean {
    const expected = spriteSheetDimensions(animation);
    return naturalWidth === expected.width && naturalHeight === expected.height;
}

export function buildPlaybackSequence(animation: AnimationDefinition): readonly number[] {
    const forward = [...animation.frameSequence];
    if (!animation.pingPong || forward.length < 3) return forward;
    return [...forward, ...forward.slice(1, -1).reverse()];
}

export function nextPlaybackIndex(
    currentIndex: number,
    sequenceLength: number,
    loop: boolean,
): number {
    if (sequenceLength <= 0) return 0;
    const next = currentIndex + 1;
    if (next < sequenceLength) return next;
    return loop ? 0 : sequenceLength - 1;
}
