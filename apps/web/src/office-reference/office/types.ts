export type Point = Readonly<{ x: number; y: number }>;
export type Rect = Readonly<{ x: number; y: number; width: number; height: number }>;

export type OfficeEntityType =
    | 'room'
    | 'walk_path'
    | 'wall'
    | 'door'
    | 'desk'
    | 'computer'
    | 'access_light'
    | 'effect_zone'
    | 'sprite_anchor'
    | 'restricted_zone'
    | 'interaction_zone'
    | 'label_anchor';

export type OfficeLayer =
    | 'paths'
    | 'rooms'
    | 'restricted'
    | 'walls'
    | 'doors'
    | 'furniture'
    | 'computers'
    | 'lights'
    | 'effects'
    | 'sprites'
    | 'labels'
    | 'hitboxes';

export type AccessState = 'green' | 'blue' | 'yellow' | 'red';
export type SeatPriority = 'yellow' | 'red';
export type AccessPolicy = Readonly<{
    state: AccessState;
    memberIds?: readonly string[];
    roleIds?: readonly string[];
    teamIds?: readonly string[];
    rankGroups?: readonly string[];
}>;

export type PointGeometry = Readonly<{ kind: 'point'; point: Point }>;
export type RectangleGeometry = Readonly<{ kind: 'rectangle'; rect: Rect }>;
export type PolygonGeometry = Readonly<{ kind: 'polygon'; points: readonly Point[] }>;
export type PolylineGeometry = Readonly<{
    kind: 'polyline';
    points: readonly Point[];
    width: number;
}>;
export type OfficeGeometry =
    | PointGeometry
    | RectangleGeometry
    | PolygonGeometry
    | PolylineGeometry;

export type AnimationDefinition = Readonly<{
    frameWidth: number;
    frameHeight: number;
    frameCount: number;
    columns: number;
    frameSequence: readonly number[];
    frameDurationMs: number;
    loop: boolean;
    pingPong: boolean;
    idle: boolean;
}>;

export type DoorState = Readonly<{
    currentState: AccessState;
    defaultState: AccessState;
    linkedRoomIds: readonly string[];
    reservedGroupIds?: readonly string[];
    scheduleReference?: string;
    locked: boolean;
    visualState: 'open' | 'closed';
}>;

export type PathDefinition = Readonly<{
    nodeIds: readonly string[];
    linkedIntersectionIds: readonly string[];
    linkedRoomIds: readonly string[];
    linkedDoorIds: readonly string[];
    direction: 'one_way' | 'bidirectional';
    blocked: boolean;
    restrictedSegmentIds?: readonly string[];
}>;

export type SpriteDefinition = Readonly<{
    assetId: string;
    animation: AnimationDefinition;
    scale: number;
    opacity: number;
    glow?: string;
    blendMode?: 'normal' | 'screen' | 'multiply';
    pointerEvents?: boolean;
}>;

export type OfficeEntity = Readonly<{
    id: string;
    type: OfficeEntityType;
    name: string;
    geometry: OfficeGeometry;
    sourceLayer: OfficeLayer;
    enabled: boolean;
    interactive: boolean;
    metadata: Readonly<Record<string, string | number | boolean | null>>;
    zIndex: number;
    tags?: readonly string[];
    accessPolicy?: AccessPolicy;
    parentId?: string;
    linkedEntityIds?: readonly string[];
    allowOutOfBounds?: boolean;
    accessState?: AccessState;
    seatPriority?: SeatPriority;
    door?: DoorState;
    path?: PathDefinition;
    sprite?: SpriteDefinition;
}>;

export type PathNode = Readonly<{
    id: string;
    point: Point;
    connectedNodeIds: readonly string[];
}>;

export type OfficeOverlayDocument = Readonly<{
    schemaVersion: 1;
    source: Readonly<{ width: number; height: number }>;
    production: boolean;
    entities: readonly OfficeEntity[];
    pathNodes: readonly PathNode[];
}>;

export type ViewTransform = Readonly<{ scale: number; x: number; y: number }>;
export type ViewportSize = Readonly<{ width: number; height: number }>;
