export const EVIDENCE_LIMIT = 8000
const SECRET_KEY = /password|passwd|secret|token|authorization|credential|cookie|api.?key|private.?key|connection.?string/i

/** Display-only defense. The server remains responsible for credential-safe records. */
export function redactEvidenceText(value: string, limit = 1000) {
  const bounded = value.slice(0, limit)
  const text = bounded
    .replace(/\b(?:Bearer|Basic)\s+[^\s,;"']+/gi, '[redacted authorization]')
    .replace(/\b(?:sk-[\w-]{8,}|gh[pousr]_[\w]{8,}|AKIA[A-Z0-9]{16})\b/g, '[redacted credential]')
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*/g, '[redacted private key]')
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[redacted]@')
    .replace(/\b(password|passwd|secret|token|api[-_]?key|authorization|credential|cookie)["']?\s*[:=]\s*(?:"[^"]*(?:"|$)|'[^']*(?:'|$)|[^\s,;&"']+)/gi, '$1=[redacted]')
  return { text: text + (value.length > limit ? '… [truncated]' : ''), redacted: text !== bounded, truncated: value.length > limit }
}

export function displayedEvidence(payload: unknown) {
  let redacted = false
  let truncated = false
  let nodes = 0
  const seen = new WeakSet<object>()
  const visit = (value: unknown, depth: number): unknown => {
    if (++nodes > 200 || depth > 8) { truncated = true; return '[truncated structure]' }
    if (typeof value === 'string') {
      const safe = redactEvidenceText(value, 2000)
      redacted ||= safe.redacted; truncated ||= safe.truncated
      return safe.text
    }
    if (value === null || typeof value === 'number' || typeof value === 'boolean') return value
    if (typeof value !== 'object') return '[unsupported value]'
    if (seen.has(value)) { truncated = true; return '[circular reference omitted]' }
    seen.add(value)
    if (Array.isArray(value)) {
      const result = value.slice(0, 20).map(item => visit(item, depth + 1))
      if (value.length > 20) { truncated = true; result.push('[truncated array]') }
      return result
    }
    const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>
    let count = 0
    for (const key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue
      if (++count > 40 || nodes >= 200) { truncated = true; result['[truncated fields]'] = true; break }
      const safeKey = redactEvidenceText(key, 128)
      redacted ||= safeKey.redacted; truncated ||= safeKey.truncated
      if (SECRET_KEY.test(key)) { result[safeKey.text] = '[redacted]'; redacted = true }
      else result[safeKey.text] = visit((value as Record<string, unknown>)[key], depth + 1)
    }
    return result
  }
  const serialized = JSON.stringify(visit(payload, 0), null, 2)
  truncated ||= serialized.length > EVIDENCE_LIMIT
  return { text: serialized.slice(0, EVIDENCE_LIMIT), truncated, redacted }
}
