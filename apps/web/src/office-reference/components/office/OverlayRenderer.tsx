import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { resolveEntityAccessState } from '../../office/access';
import { getSpriteSheetAsset } from '../../office/assets';
import {
    buildPlaybackSequence,
    hasValidSpriteSheetDimensions,
    nextPlaybackIndex,
    shouldRenderMissingSpriteFallback,
    spriteAssetStateAfterRuntimeLoad,
    SpriteAssetState,
    spriteFrameLayout,
    spriteSheetDimensions,
} from '../../office/animation';
import { geometryCenter } from '../../office/geometry';
import { compareEntities } from '../../office/layers';
import { OfficeEntity, OfficeLayer } from '../../office/types';

type Props = Readonly<{
    entities: readonly OfficeEntity[];
    visibleLayers: ReadonlySet<OfficeLayer>;
    debug: boolean;
    reviewMode?: boolean;
    selectedId: string | null;
    hoveredId: string | null;
    showLabels: boolean;
    reducedMotion: boolean;
    onHover: (id: string | null) => void;
    onSelect: (id: string) => void;
}>;

const DEBUG_COLORS: Record<OfficeLayer, string> = {
    paths: '#51d88a',
    rooms: '#5aa9ff',
    restricted: '#ff4d67',
    walls: '#d9e0e7',
    doors: '#ffd166',
    furniture: '#bb8fce',
    computers: '#73d2de',
    lights: '#ffffff',
    effects: '#35c7ff',
    sprites: '#41a8ff',
    labels: '#f7f7f7',
    hitboxes: '#ff8f40',
};

const ACCESS_COLORS = {
    green: '#42d77d',
    blue: '#4f9cff',
    yellow: '#ffd44d',
    red: '#ff4e5f',
} as const;

function pointsAttribute(points: readonly { x: number; y: number }[]): string {
    return points.map(point => `${point.x},${point.y}`).join(' ');
}

function EntityGeometry({
    entity,
    debug,
    reviewMode,
    emphasized,
}: Readonly<{ entity: OfficeEntity; debug: boolean; reviewMode: boolean; emphasized: boolean }>) {
    const accessState = resolveEntityAccessState(entity);
    const color = accessState ? ACCESS_COLORS[accessState] : DEBUG_COLORS[entity.sourceLayer];
    const visible = reviewMode || debug || emphasized || ['access_light', 'effect_zone'].includes(entity.type);
    const fillOpacity = visible ? (entity.type === 'room' || entity.type === 'restricted_zone' || entity.type === 'effect_zone' ? 0.18 : 0.1) : 0;
    const strokeOpacity = visible ? 0.95 : 0;
    const common = {
        fill: color,
        fillOpacity,
        stroke: emphasized ? '#ffffff' : color,
        strokeOpacity,
        strokeWidth: emphasized ? 2.5 : 1,
        vectorEffect: 'non-scaling-stroke' as const,
    };
    const geometry = entity.geometry;
    if (geometry.kind === 'point') {
        return <circle cx={geometry.point.x} cy={geometry.point.y} r={entity.type === 'access_light' ? 38 : 52} {...common} />;
    }
    if (geometry.kind === 'rectangle') {
        return <rect {...geometry.rect} {...common} />;
    }
    if (geometry.kind === 'polygon') {
        return <polygon points={pointsAttribute(geometry.points)} {...common} />;
    }
    return (
        <polyline
            points={pointsAttribute(geometry.points)}
            fill="none"
            stroke={emphasized ? '#ffffff' : color}
            strokeOpacity={visible ? 0.9 : 0}
            strokeWidth={Math.max(geometry.width, emphasized ? 28 : 1)}
            strokeLinecap="round"
            strokeLinejoin="round"
        />
    );
}

function VertexMarkers({ entity }: Readonly<{ entity: OfficeEntity }>) {
    if (entity.geometry.kind !== 'polygon' && entity.geometry.kind !== 'polyline') return null;
    return entity.geometry.points.map((point, index) => (
        <circle key={`${point.x}-${point.y}-${index}`} cx={point.x} cy={point.y} r={22} fill="#ffffff" stroke="#091119" strokeWidth={7} />
    ));
}

function EntityHitArea({ entity }: Readonly<{ entity: OfficeEntity }>) {
    const geometry = entity.geometry;
    const props = { fill: 'transparent', stroke: 'transparent', pointerEvents: 'all' as const };
    if (geometry.kind === 'point') return <circle cx={geometry.point.x} cy={geometry.point.y} r={70} {...props} />;
    if (geometry.kind === 'rectangle') return <rect {...geometry.rect} {...props} />;
    if (geometry.kind === 'polygon') return <polygon points={pointsAttribute(geometry.points)} {...props} />;
    return <polyline points={pointsAttribute(geometry.points)} fill="none" stroke="transparent" strokeWidth={Math.max(geometry.width, 80)} pointerEvents="stroke" />;
}

function SeatPriorityMarker({ entity }: Readonly<{ entity: OfficeEntity }>) {
    if (entity.type !== 'desk' || !entity.seatPriority) return null;
    const point = geometryCenter(entity.geometry);
    const color = entity.seatPriority === 'yellow' ? ACCESS_COLORS.yellow : ACCESS_COLORS.red;
    return (
        <g data-seat-priority={entity.seatPriority} pointerEvents="none">
            <circle cx={point.x} cy={point.y} r={46} fill="#0a1117" stroke={color} strokeWidth={18} />
            <text x={point.x} y={point.y + 18} textAnchor="middle" fill={color} fontSize={54} fontWeight="700">
                {entity.seatPriority === 'yellow' ? 'P' : 'S'}
            </text>
        </g>
    );
}

function SpriteAnchor({ entity, debug, reducedMotion }: Readonly<{ entity: OfficeEntity; debug: boolean; reducedMotion: boolean }>) {
    const [assetState, setAssetState] = useState<SpriteAssetState>('loading');
    const [playbackIndex, setPlaybackIndex] = useState(0);
    const surfaceRef = useRef<HTMLDivElement>(null);
    const [onscreen, setOnscreen] = useState(true);
    const sprite = entity.sprite;
    const playback = useMemo(() => sprite ? buildPlaybackSequence(sprite.animation) : [], [sprite]);


    useEffect(() => {
        const element = surfaceRef.current;
        if (!element || typeof IntersectionObserver === 'undefined') return;
        const observer = new IntersectionObserver(entries => setOnscreen(entries[0]?.isIntersecting ?? true));
        observer.observe(element);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (assetState !== 'ready' || reducedMotion || !onscreen || !sprite?.animation.idle || playback.length < 2) return;
        const timer = window.setTimeout(() => {
            setPlaybackIndex(index => nextPlaybackIndex(index, playback.length, sprite.animation.loop));
        }, sprite.animation.frameDurationMs);
        return () => window.clearTimeout(timer);
    }, [assetState, onscreen, playback.length, playbackIndex, reducedMotion, sprite]);

    if (entity.geometry.kind !== 'point' || !sprite) return null;
    const point = entity.geometry.point;
    const asset = getSpriteSheetAsset(sprite.assetId);
    if (!asset) return debug ? <text x={point.x} y={point.y} className="office-overlay-label">Unknown sprite asset</text> : null;
    const width = sprite.animation.frameWidth * sprite.scale;
    const height = sprite.animation.frameHeight * sprite.scale;
    const frame = playback[playbackIndex] ?? 0;
    const frameLayout = spriteFrameLayout(frame, sprite.animation, sprite.scale);
    const sheetDimensions = spriteSheetDimensions(sprite.animation);

    return (
        <foreignObject x={point.x - width / 2} y={point.y - height} width={width} height={height} pointerEvents="none">
            <div
                ref={surfaceRef}
                className={`office-sprite ${reducedMotion ? '' : 'office-sprite--idle'}`}
                style={{
                    opacity: sprite.opacity,
                    filter: sprite.glow ? `drop-shadow(0 0 22px ${sprite.glow})` : undefined,
                    mixBlendMode: sprite.blendMode,
                }}
            >
                <img
                    src={asset.path}
                    alt=""
                    draggable={false}
                    className="office-sprite__preload"
                    onLoad={event => {
                        setAssetState(spriteAssetStateAfterRuntimeLoad(
                            true,
                            hasValidSpriteSheetDimensions(
                                event.currentTarget.naturalWidth,
                                event.currentTarget.naturalHeight,
                                sprite.animation,
                            ),
                        ));
                    }}
                    onError={() => setAssetState(spriteAssetStateAfterRuntimeLoad(false))}
                />
                {assetState === 'ready' && (
                    <span
                        className="office-sprite__frame"
                        style={{
                            backgroundImage: `url(${asset.path})`,
                            backgroundPosition: `${frameLayout.x}px ${frameLayout.y}px`,
                            backgroundSize: `${sheetDimensions.width * sprite.scale}px ${sheetDimensions.height * sprite.scale}px`,
                        }}
                    />
                )}
                {shouldRenderMissingSpriteFallback(assetState) &&
                    <span className="office-sprite__missing" aria-hidden="true">◇</span>}
            </div>
        </foreignObject>
    );
}

const OverlayEntityView = memo(function OverlayEntityView({
    entity,
    debug,
    reviewMode,
    selected,
    hovered,
    showLabels,
    reducedMotion,
    onHover,
    onSelect,
}: Readonly<{
    entity: OfficeEntity;
    debug: boolean;
    reviewMode: boolean;
    selected: boolean;
    hovered: boolean;
    showLabels: boolean;
    reducedMotion: boolean;
    onHover: (id: string | null) => void;
    onSelect: (id: string) => void;
}>) {
    const emphasized = selected || hovered;
    const interactive = entity.interactive && (entity.sprite?.pointerEvents ?? true);
    const labelPoint = geometryCenter(entity.geometry);
    return (
        <g
            data-entity-id={entity.id}
            data-candidate-category={String(entity.metadata.candidateCategory ?? '')}
            className={interactive ? 'office-entity office-entity--interactive' : 'office-entity'}
            role={interactive ? 'button' : undefined}
            tabIndex={interactive ? 0 : undefined}
            pointerEvents={interactive ? undefined : 'none'}
            aria-label={interactive ? `${entity.name}, ${entity.type.replace('_', ' ')}` : undefined}
            onPointerEnter={() => interactive && onHover(entity.id)}
            onPointerLeave={() => interactive && onHover(null)}
            onClick={event => {
                if (!interactive) return;
                event.stopPropagation();
                onSelect(entity.id);
            }}
            onKeyDown={event => {
                if (interactive && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    onSelect(entity.id);
                }
            }}
        >
            <EntityGeometry entity={entity} debug={debug} reviewMode={reviewMode} emphasized={emphasized} />
            <SeatPriorityMarker entity={entity} />
            {debug && emphasized && <VertexMarkers entity={entity} />}
            {entity.type === 'sprite_anchor' && <SpriteAnchor key={entity.sprite?.assetId} entity={entity} debug={debug} reducedMotion={reducedMotion} />}
            {interactive && <EntityHitArea entity={entity} />}
            {entity.type === 'label_anchor' && showLabels && (
                <text
                    data-production-label={entity.id}
                    x={labelPoint.x}
                    y={labelPoint.y}
                    textAnchor="middle"
                    className="office-production-label"
                >
                    {entity.name}
                </text>
            )}
            {reviewMode && showLabels && entity.type !== 'label_anchor' && (
                <text
                    data-review-label={entity.id}
                    x={labelPoint.x}
                    y={labelPoint.y - 72}
                    textAnchor="middle"
                    className="office-overlay-label office-overlay-label--candidate"
                >
                    {entity.name}
                </text>
            )}
            {debug && showLabels && (
                <text x={labelPoint.x} y={labelPoint.y - (reviewMode ? 140 : 72)} textAnchor="middle" className="office-overlay-label">
                    {entity.id}
                </text>
            )}
        </g>
    );
});

export const OverlayRenderer = memo(function OverlayRenderer({
    entities,
    visibleLayers,
    debug,
    reviewMode = false,
    selectedId,
    hoveredId,
    showLabels,
    reducedMotion,
    onHover,
    onSelect,
}: Props) {
    const sorted = useMemo(
        () => [...entities].filter(entity => entity.enabled && visibleLayers.has(entity.sourceLayer)).sort(compareEntities),
        [entities, visibleLayers],
    );
    return (
        <svg className="office-overlay" width="8192" height="5460" viewBox="0 0 8192 5460" aria-label="Office interaction regions">
            {sorted.map(entity => (
                <OverlayEntityView
                    key={entity.id}
                    entity={entity}
                    debug={debug}
                    reviewMode={reviewMode}
                    selected={selectedId === entity.id}
                    hovered={hoveredId === entity.id}
                    showLabels={showLabels && visibleLayers.has('labels')}
                    reducedMotion={reducedMotion}
                    onHover={onHover}
                    onSelect={onSelect}
                />
            ))}
        </svg>
    );
});
