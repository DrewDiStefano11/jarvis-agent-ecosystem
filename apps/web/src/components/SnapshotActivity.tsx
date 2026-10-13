import { useState } from 'react'
import type { AuditEvent } from '../types/contracts'
import { NavIcon } from './NavIcon'

/** Observed event counts only. Missing dates are never invented as zero-activity days. */
export function SnapshotActivity({ events, onDate }: { events: AuditEvent[]; onDate?: (date: string) => void }) {
  const [range, setRange] = useState('all')
  const [checkedAt, setCheckedAt] = useState(() => Date.now())
  const cutoff = range === 'all' ? -Infinity : checkedAt - Number(range) * 86400000
  const days = new Map<string, number>()
  const seen = new Set<string>()
  for (const event of events) {
    const time = Date.parse(event.timestamp)
    if (seen.has(event.id) || !Number.isFinite(time) || time < cutoff || (range !== 'all' && time > checkedAt)) continue
    seen.add(event.id)
    const day = new Date(time).toISOString().slice(0, 10)
    days.set(day, (days.get(day) ?? 0) + 1)
  }
  const buckets = [...days.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(-14)
  const maximum = Math.max(1, ...buckets.map(([, count]) => count))
  return <section className="snapshot-activity" aria-label="Recorded event activity">
    <div className="mc-heading"><div><h2><NavIcon name="activity"/>Recorded event activity</h2><p className="mc-note">Loaded audit records, including demonstration events</p></div><label>Period<select value={range} onChange={event => { setRange(event.target.value); setCheckedAt(Date.now()) }}><option value="all">Recorded days</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select></label></div>
    {buckets.length ? <><div className="activity-readout"><strong>{buckets.reduce((sum, [, count]) => sum + count, 0)}</strong><span>recorded events across {buckets.length} observed {buckets.length === 1 ? 'day' : 'days'}</span></div><div className="event-plot"><div className="event-scale" aria-hidden="true"><span>{maximum}</span><span>0</span></div><div className={`event-bars${buckets.length > 7 ? ' dense-event-dates' : ''}`}>{buckets.map(([day, count]) => <button className="event-bar-column" key={day} aria-label={`Inspect ${count} recorded events on ${day}`} title={`${count} recorded events on ${day}`} onClick={() => onDate?.(day)}><strong style={{ bottom: `${count / maximum * 100}%` }}>{count}</strong><span className="event-bar" style={{ height: `${count / maximum * 100}%` }}/><span className="event-bar-date">{day.slice(5)}</span></button>)}</div></div><details className="chart-data"><summary>Read chart values</summary><table><caption>Recorded events by UTC date</caption><thead><tr><th>Date</th><th>Events</th></tr></thead><tbody>{buckets.map(([day, count]) => <tr key={day}><td>{day}</td><td>{count}</td></tr>)}</tbody></table><p className="mc-note">Up to 14 observed UTC dates. Missing dates are unknown. This snapshot does not measure execution throughput.</p></details></> : <div className="chart-empty"><NavIcon name="activity"/><strong>No recorded events in this period</strong><p>Historical throughput and utilization measurements are not available.</p></div>}
  </section>
}
