import {
    ResolvedSpriteClip,
    SpriteAssetManifest,
    SpriteDirection,
    SpriteManifest,
    SpriteState,
} from './types';

function findClip(asset: SpriteAssetManifest, state: SpriteState, direction: SpriteDirection) {
    const truthfulFallbacks: readonly SpriteDirection[] = direction === 'none'
        ? ['south', 'east', 'west', 'north']
        : [direction, 'none', 'south', 'east', 'west', 'north'];
    for (const candidate of truthfulFallbacks) {
        const clip = asset.clips.find(item => item.state === state && item.direction === candidate);
        if (clip) return clip;
    }
    return undefined;
}

export function resolveSpriteClip(
    manifest: SpriteManifest,
    assetId: string,
    requestedState: SpriteState,
    requestedDirection: SpriteDirection,
    reducedMotion = false,
): ResolvedSpriteClip | null {
    const asset = manifest.assets.find(item => item.id === assetId);
    if (!asset || asset.availability !== 'available') return null;
    const fallbackChain: SpriteState[] = [];
    const visited = new Set<SpriteState>();
    let state: SpriteState | null = requestedState;
    while (state !== null && !visited.has(state)) {
        visited.add(state);
        fallbackChain.push(state);
        const clip = findClip(asset, state, requestedDirection);
        if (clip) {
            return {
                asset,
                clip,
                requestedState,
                requestedDirection,
                resolvedState: state,
                resolvedDirection: clip.direction,
                fallbackChain,
                staticFrame: reducedMotion ? clip.reducedMotionFallbackFrame : null,
            };
        }
        state = manifest.fallbackGraph[state];
    }
    return null;
}

export function spriteFrameSequence(frames: readonly number[], yoyo: boolean, loop: boolean): number[] {
    if (!yoyo || frames.length <= 1) return [...frames];
    if (!loop) return [...frames, ...frames.slice(0, -1).reverse()];
    if (frames.length <= 2) return [...frames];
    return [...frames, ...frames.slice(1, -1).reverse()];
}

export function frameAtElapsedTime(
    resolved: ResolvedSpriteClip,
    elapsedMs: number,
    speed = 1,
): number {
    if (resolved.staticFrame !== null) return resolved.staticFrame;
    const { clip } = resolved;
    const sequence = spriteFrameSequence(clip.frames, clip.yoyo, clip.loop);
    if (sequence.length === 1) return sequence[0] ?? clip.staticFallbackFrame;
    const frameDuration = 1000 / (clip.framesPerSecond * Math.max(0.1, speed));
    const playbackDuration = sequence.length * frameDuration;
    const cycleDuration = playbackDuration + clip.repeatDelayMs;
    if (!clip.loop && elapsedMs >= playbackDuration) return sequence[sequence.length - 1] ?? clip.staticFallbackFrame;
    const cycleElapsed = clip.loop ? elapsedMs % cycleDuration : elapsedMs;
    if (cycleElapsed >= playbackDuration) return sequence[sequence.length - 1] ?? clip.staticFallbackFrame;
    return sequence[Math.floor(cycleElapsed / frameDuration) % sequence.length] ?? clip.staticFallbackFrame;
}

export function framePosition(asset: SpriteAssetManifest, frame: number) {
    return {
        column: frame % asset.columns,
        row: Math.floor(frame / asset.columns),
        x: -(frame % asset.columns) * asset.frameWidth,
        y: -Math.floor(frame / asset.columns) * asset.frameHeight,
    };
}
