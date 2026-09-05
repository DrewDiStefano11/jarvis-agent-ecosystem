import { describe, expect, test } from 'vitest'
import rooms from '../public/assets/office-candidate/rooms.json'
import paths from '../public/assets/office-candidate/walk-paths.json'
import walls from '../public/assets/office-candidate/walls.json'
import objects from '../public/assets/office-candidate/objects.json'
import doors from '../public/assets/office-candidate/doors.json'
import lights from '../public/assets/office-candidate/door-lights.json'
import computers from '../public/assets/office-candidate/computers.json'
import positions from '../public/assets/office-candidate/positions.json'
import interactive from '../public/assets/office-candidate/interactive-objects.json'
import { buildFloor1CandidateOverlay, candidateEntityCounts } from '../src/office-reference/office/floor1/candidateReview'

const source = { rooms, 'walk-paths': paths, walls, objects, doors, 'door-lights': lights, computers, positions, 'interactive-objects': interactive }
describe('original office geometry contract', () => {
  test('retains all source categories and their unapproved status', () => {
    const document = buildFloor1CandidateOverlay(source)
    expect(candidateEntityCounts(document)).toEqual({ rooms: 34, 'walk-paths': 167, walls: 82, objects: 178, doors: 47, 'door-lights': 144, computers: 44, positions: 205, 'interactive-objects': 6 })
    expect(document.production).toBe(false)
    expect(document.entities.every(entity => entity.metadata.productionApproved === false)).toBe(true)
    expect(document.entities.filter(entity => entity.type === 'door' && entity.accessState === 'red').every(entity => entity.door?.locked)).toBe(true)
    expect(document.entities.some(entity => entity.type === 'sprite_anchor')).toBe(false)
  })
  test('refuses an accidentally promoted candidate source', () => {
    expect(() => buildFloor1CandidateOverlay({ ...source, rooms: { ...rooms, productionApproved: true } })).toThrow(/approval boundary/)
  })
})
