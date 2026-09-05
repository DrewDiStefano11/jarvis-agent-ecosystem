import type { OfficePlacement, OfficePoint } from '../types/office'

/** Presentation only: position is derived from the server's persisted path and clock. */
export function officePosition(placement: OfficePlacement, now: number): { point: OfficePoint; direction: 'east' | 'west' | 'north' | 'south' } {
  const motion = placement.motion
  if (!motion || !placement.enabled || placement.lifecycleState !== 'active') return { point: placement.position, direction: 'south' }
  const points = motion.points
  const progress = Math.max(0, Math.min(1, ((motion.stoppedAt ? Date.parse(motion.stoppedAt) : now) - Date.parse(motion.startedAt)) / motion.durationMs))
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index]!.x, point.y - points[index]!.y))
  let remaining = lengths.reduce((sum, length) => sum + length, 0) * progress
  for (let index = 0; index < lengths.length; index += 1) {
    const a = points[index]!, b = points[index + 1]!, length = lengths[index]!
    if (remaining <= length || index === lengths.length - 1) {
      const part = length ? Math.min(1, remaining / length) : 0
      const direction = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y) ? b.x >= a.x ? 'east' : 'west' : b.y >= a.y ? 'south' : 'north'
      return { point: { x: a.x + (b.x - a.x) * part, y: a.y + (b.y - a.y) * part }, direction }
    }
    remaining -= length
  }
  return { point: placement.position, direction: 'south' }
}
