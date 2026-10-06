import { describe, expect, test } from 'vitest'
import { displayedEvidence, EVIDENCE_LIMIT, redactEvidenceText } from '../src/components/activity-evidence'

describe('bounded displayed evidence',()=>{
 test('redacts sensitive fields at any nesting and recognized free-text credentials',()=>{
  const result=displayedEvidence({safe:'visible',nested:{api_key:'example-sensitive-value',authorization:'private-auth',leaseToken:'lease-value'},message:'Bearer bearer-value https://user:password@host.test/path?token=query-value sk-testcredential123456'})
  expect(result.redacted).toBe(true);expect(result.text).toContain('visible');for(const secret of ['example-sensitive-value','private-auth','lease-value','bearer-value','user:password','query-value','sk-testcredential123456']) expect(result.text).not.toContain(secret)
 })
 test('depth, node, array and text bounds never emit unlimited evidence',()=>{
  const input:Record<string,unknown>={long:'x'.repeat(20000),list:Array.from({length:500},(_,i)=>({i,text:'y'.repeat(500)}))};let nested=input;for(let i=0;i<20;i++){nested.next={};nested=nested.next as typeof input}
  const result=displayedEvidence(input);expect(result.truncated).toBe(true);expect(result.text.length).toBeLessThanOrEqual(EVIDENCE_LIMIT);expect(result.text).toContain('truncated')
 })
 test('cycles and malicious HTML remain bounded inert text',()=>{
  const payload:Record<string,unknown>={html:'<script>alert(1)</script>'};payload.self=payload
  const result=displayedEvidence(payload);expect(result.truncated).toBe(true);expect(result.text).toContain('circular');expect(result.text).toContain('<script>')
 })
 test('many fields and reserved object keys are safely bounded',()=>{
  const payload=JSON.parse('{"__proto__":{"password":"secret-value"}}') as Record<string,unknown>
  Object.assign(payload,Object.fromEntries(Array.from({length:10000},(_,i)=>[`field${i}`,i])))
  const result=displayedEvidence(payload);expect(result.text).not.toContain('secret-value');expect(result.text.length).toBeLessThanOrEqual(EVIDENCE_LIMIT);expect(result.truncated).toBe(true)
 })
 test('summary redaction and explicit text truncation',()=>{
  expect(redactEvidenceText('cookie=session-value Basic aW52YWxpZA==').text).not.toContain('session-value');expect(redactEvidenceText('long'.repeat(400)).truncated).toBe(true)
 })
 test('credentials inside JSON or quoted prose are redacted before display or copy',()=>{
  const result=displayedEvidence({message:'{"password":"secret with spaces","token":"nested-token"}',reason:"api_key='quoted-secret'"})
  for(const value of ['secret with spaces','nested-token','quoted-secret']) expect(result.text).not.toContain(value)
  expect(result.redacted).toBe(true)
 })
})

test('quoted secrets crossing summary and evidence truncation boundaries stay redacted', () => {
 for (const quote of ['"', "'"]) {
  for (const limit of [1000, 2000]) {
   const source = 'x'.repeat(limit - 30) + ` {${quote}password${quote}:${quote}boundary-secret extra text extending beyond the limit${quote}}`
   const summary = redactEvidenceText(source, limit)
   expect(summary.redacted).toBe(true)
   expect(summary.truncated).toBe(true)
   expect(summary.text).not.toContain('boundary-secret')
   expect(summary.text).toContain('[redacted]')
   if (limit === 2000) {
    const evidence = displayedEvidence({message: source})
    expect(evidence.redacted).toBe(true)
    expect(evidence.truncated).toBe(true)
    expect(evidence.text).not.toContain('boundary-secret')
   }
  }
 }
})
