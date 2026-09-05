export type OfficeAsset = Readonly<{
    id: string;
    path: string;
    kind: 'background' | 'sprite-sheet';
    required: boolean;
    pixelated?: boolean;
}>;

/**
 * Resolves a public asset without importing it into Vite's module graph.
 * Vite replaces BASE_URL for each deployment, including relative builds.
 */
export function resolvePublicAssetPath(path: string, baseUrl = import.meta.env.BASE_URL): string {
    const trimmedBase = baseUrl.trim();
    const baseWithoutTrailingSlashes = trimmedBase.replace(/\/+$/, '');
    const normalizedBase = baseWithoutTrailingSlashes === ''
        ? (trimmedBase.startsWith('/') ? '/' : './')
        : `${baseWithoutTrailingSlashes}/`;
    const normalizedPath = path.replace(/^\/+/, '');
    return `${normalizedBase}${normalizedPath}`;
}

export const OFFICE_ASSETS = {
    background: {
        id: 'office-background-8k',
        path: resolvePublicAssetPath('assets/office/office-8192x5460.png'),
        kind: 'background',
        required: true,
    },
    hologram: {
        id: 'central-blue-tube-hologram',
        path: resolvePublicAssetPath('assets/office/sprites/central-blue-tube-hologram.png'),
        kind: 'sprite-sheet',
        required: false,
        pixelated: true,
    },
} as const satisfies Record<string, OfficeAsset>;

export type SpriteSheetAsset = Extract<
    (typeof OFFICE_ASSETS)[keyof typeof OFFICE_ASSETS],
    { readonly kind: 'sprite-sheet' }
>;
export type SpriteSheetAssetId = SpriteSheetAsset['id'];

export const SPRITE_SHEET_ASSET_IDS: ReadonlySet<SpriteSheetAssetId> = new Set(
    Object.values(OFFICE_ASSETS)
        .filter((asset): asset is SpriteSheetAsset => asset.kind === 'sprite-sheet')
        .map(asset => asset.id),
);

export function isSpriteSheetAssetId(value: unknown): value is SpriteSheetAssetId {
    return typeof value === 'string' && SPRITE_SHEET_ASSET_IDS.has(value as SpriteSheetAssetId);
}

export function getSpriteSheetAsset(value: unknown): SpriteSheetAsset | undefined {
    if (!isSpriteSheetAssetId(value)) return undefined;
    return Object.values(OFFICE_ASSETS).find(
        (asset): asset is SpriteSheetAsset => asset.kind === 'sprite-sheet' && asset.id === value,
    );
}
