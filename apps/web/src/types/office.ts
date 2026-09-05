export interface OfficePoint { x: number; y: number }
export interface OfficeStation { id: string; label: string; roomId: string; roomName: string; point: OfficePoint }
export interface OfficeRoute { id: string; originId: string; destinationId: string; points: OfficePoint[]; doorIds: string[]; length: number }
export interface OfficeCatalog { version: string; sourceCommit: string; geometryHash: string; reviewScope: string; stations: OfficeStation[]; routes: OfficeRoute[]; spriteIds: string[] }
export interface OfficeMotion { originId: string; destinationId: string; points: OfficePoint[]; doorIds: string[]; startedAt: string; durationMs: number; stoppedAt: string | null }
export interface OfficePlacement { identityId: string; displayName: string; lifecycleState: string; enabled: boolean; stationId: string; spriteId: string; position: OfficePoint; motion: OfficeMotion | null; movementState: 'idle' | 'moving' | 'stopped'; activity: 'idle' | 'queued' | 'working' | 'waiting' | 'failed' | 'completed'; version: number; updatedAt: string }
export interface OfficeSnapshot { serverTime: string; catalog: OfficeCatalog; placements: OfficePlacement[]; placementVersions: Record<string, number>; emergencyStop: boolean }
export interface OfficeCommand { commandId: string; action: 'assign' | 'move' | 'stop' | 'release'; expectedVersion: number; stationId?: string; spriteId?: string }
export interface OfficeCommandResult { commandId: string; identityId: string; version: number; action: OfficeCommand['action'] }
