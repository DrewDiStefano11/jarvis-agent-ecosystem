export const SPRITE_STATES = [
    'idle',
    'walking',
    'working',
    'sitting',
    'typing',
    'talking',
    'thinking',
    'reviewing',
    'waiting',
    'blocked',
    'error',
    'offline',
] as const;

export const SPRITE_DIRECTIONS = ['north', 'south', 'east', 'west', 'none'] as const;

export type SpriteState = (typeof SPRITE_STATES)[number];
export type SpriteDirection = (typeof SPRITE_DIRECTIONS)[number];

export type SpriteClip = Readonly<{
    id: string;
    state: SpriteState;
    direction: SpriteDirection;
    frames: readonly number[];
    framesPerSecond: number;
    loop: boolean;
    repeatDelayMs: number;
    yoyo: boolean;
    reducedMotionFallbackFrame: number;
    staticFallbackFrame: number;
}>;

export type SpriteAssetManifest = Readonly<{
    id: string;
    sourceAssetReference: string;
    generatedAssetUrl: string;
    sourceChecksum: string;
    generatedChecksum: string;
    frameWidth: number;
    frameHeight: number;
    frameCount: number;
    rows: number;
    columns: number;
    anchor: Readonly<{ x: number; y: number }>;
    visualScale: number;
    pixelArt: boolean;
    availability: 'available' | 'blocked';
    approval: 'approved' | 'provisional';
    blockingReason: string | null;
    runtimeCapability: 'limited-cardinal-idle-walk' | 'complete-office-activities';
    frameIntegrity: Readonly<{
        method: string;
        inspectedFrameCount: number;
        maximumBottomAnchorDeviationPixels: number | null;
    }>;
    agentProfileCompatibility: readonly string[];
    classification: 'agent' | 'hologram' | 'effect';
    authoredDirections: readonly SpriteDirection[];
    horizontalFlipDirections: readonly SpriteDirection[];
    clips: readonly SpriteClip[];
}>;

export type BlockedSpriteAsset = Readonly<{
    id: string;
    sourceAssetReference: string;
    availability: 'blocked';
    approval: 'provisional';
    blockingReason: string;
    runtimeCapability: 'quarantined-fallback-only';
}>;

export type SpriteManifest = Readonly<{
    schemaVersion: 1;
    generatedBy: string;
    fallbackGraph: Readonly<Record<SpriteState, SpriteState | null>>;
    assets: readonly SpriteAssetManifest[];
    blockedAssets: readonly BlockedSpriteAsset[];
}>;

export type SpriteManifestValidationIssue = Readonly<{
    path: string;
    message: string;
}>;

export type SpriteManifestIntegrity = Readonly<{
    sourceChecksums?: Readonly<Record<string, string>>;
    generatedChecksums?: Readonly<Record<string, string>>;
    availableGeneratedUrls?: ReadonlySet<string>;
    productionMode?: boolean;
}>;

export type ResolvedSpriteClip = Readonly<{
    asset: SpriteAssetManifest;
    clip: SpriteClip;
    requestedState: SpriteState;
    requestedDirection: SpriteDirection;
    resolvedState: SpriteState;
    resolvedDirection: SpriteDirection;
    fallbackChain: readonly SpriteState[];
    staticFrame: number | null;
}>;
