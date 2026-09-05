import { useEffect, useState } from 'react'
import type { OfficeSnapshot } from '../types/office'
import { officePosition } from '../state/officeMotion'
import spriteData from '../office-reference/office/sprites/live-manifest.json'
import type { SpriteManifest } from '../office-reference/office/sprites/types'
import { frameAtElapsedTime, framePosition, resolveSpriteClip } from '../office-reference/office/sprites/resolver'

const manifest = spriteData as SpriteManifest

export function LiveOfficeLayer({ snapshot, selectedId, onSelect }: { snapshot: OfficeSnapshot; selectedId: string | null; onSelect: (id: string) => void }) {
  const [clock, setClock] = useState({ source: snapshot.serverTime, elapsed: 0 })
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const elapsed = clock.source === snapshot.serverTime ? clock.elapsed : 0
  const moving = snapshot.placements.some(item => item.movementState === 'moving') && !snapshot.emergencyStop
  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const changed = (event: MediaQueryListEvent) => setReducedMotion(event.matches)
    preference.addEventListener('change', changed)
    return () => preference.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    if (!moving) return
    const start = performance.now()
    // A local presentation clock never changes placement or runtime state.
    const timer = window.setInterval(() => setClock({ source: snapshot.serverTime, elapsed: performance.now() - start }), reducedMotion ? 250 : 50)
    return () => clearInterval(timer)
  }, [moving, reducedMotion, snapshot.serverTime])
  const now = Date.parse(snapshot.serverTime) + elapsed
  return <div className="live-office-layer">
    <svg className="live-office-routes" viewBox="0 0 8192 5460" aria-hidden="true">{snapshot.placements.filter(item => item.motion).map(item => <polyline key={item.identityId} points={item.motion!.points.map(point => `${point.x},${point.y}`).join(' ')} stroke={item.movementState === 'stopped' ? '#ffbb65' : '#6de5be'}/>)}</svg>
    {snapshot.catalog.stations.map(station => <button type="button" key={station.id} className="live-office-station" style={{ left: station.point.x, top: station.point.y }} aria-label={`Inspect ${station.label}`} onClick={() => onSelect(station.id)}>{station.label}</button>)}
    {snapshot.placements.map(placement => {
      const position = snapshot.emergencyStop ? { point: placement.position, direction: 'south' as const } : officePosition(placement, now)
      const inactive = !placement.enabled || placement.lifecycleState !== 'active'
      const walking = placement.movementState === 'moving' && !snapshot.emergencyStop && !inactive && placement.motion !== null && now < Date.parse(placement.motion.startedAt) + placement.motion.durationMs
      const resolved = resolveSpriteClip(manifest, placement.spriteId, inactive ? 'offline' : walking ? 'walking' : 'idle', position.direction, reducedMotion)
      const frame = resolved ? framePosition(resolved.asset, frameAtElapsedTime(resolved, elapsed)) : null
      return <button type="button" key={placement.identityId} data-testid={`office-agent-${placement.identityId}`} data-x={position.point.x.toFixed(2)} data-y={position.point.y.toFixed(2)} data-movement={placement.movementState} className={`live-office-agent ${selectedId === `identity:${placement.identityId}` ? 'selected' : ''} ${inactive ? 'inactive' : ''}`} style={{ left: position.point.x, top: position.point.y }} aria-label={`Select ${placement.displayName}, ${placement.activity}, ${placement.movementState}`} onClick={() => onSelect(`identity:${placement.identityId}`)}>
        {resolved && frame ? <span className="live-office-sprite" style={{ width: resolved.asset.frameWidth, height: resolved.asset.frameHeight, backgroundImage: `url(/${resolved.asset.generatedAssetUrl.replace(/^\/+/, '')})`, backgroundPosition: `${frame.x}px ${frame.y}px`, transform: `scale(${resolved.asset.visualScale})` }}/> : <span>?</span>}
        <span className="live-office-name">{placement.displayName}<small>{inactive ? placement.enabled ? placement.lifecycleState : 'disabled' : snapshot.emergencyStop ? 'Emergency stop' : `${placement.activity} · ${placement.movementState}`}</small></span>
      </button>
    })}
  </div>
}
