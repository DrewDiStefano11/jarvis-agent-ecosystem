import { useCallback, useRef, useState } from 'react'
import { request } from '../api/client'
import type { CatalogDetail, CatalogPage, CatalogSource } from '../types/catalog'

export function useCatalogState(onIdentityChange: () => Promise<void>) {
  const [page, setPage] = useState<CatalogPage>({ items: [], total: 0, offset: 0, limit: 25 })
  const [sources, setSources] = useState<CatalogSource[]>([])
  const [detail, setDetail] = useState<CatalogDetail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const selection = useRef('agent')
  const load = useCallback(async (kind = 'agent', offset = 0) => {
    const current = ++generation.current
    selection.current = kind
    setBusy(true); setError(''); setDetail(null)
    try {
      if (kind === 'sources') {
        const rows = await request<CatalogSource[]>(`/api/catalog/sources?offset=${offset}&limit=25`)
        if (current === generation.current) setSources(rows)
      } else {
        const result = await request<CatalogPage>(`/api/catalog/entries?kind=${kind === 'active' ? 'agent' : kind}&active_only=${kind === 'active'}&offset=${offset}&limit=25`)
        if (current === generation.current) setPage(result)
      }
    } catch (caught) { if (current === generation.current) setError(caught instanceof Error ? caught.message : 'Catalog request failed') }
    finally { if (current === generation.current) setBusy(false) }
  }, [])
  const inspect = useCallback(async (id: string) => {
    const current = ++generation.current
    setBusy(true); setError('')
    try { const value = await request<CatalogDetail>(`/api/catalog/entries/${id}`); if (current === generation.current) setDetail(value) }
    catch (caught) { if (current === generation.current) setError(caught instanceof Error ? caught.message : 'Catalog detail failed') }
    finally { if (current === generation.current) setBusy(false) }
  }, [])
  const mutate = useCallback(async (path: string, body?: unknown) => {
    setBusy(true); setError('')
    try {
      await request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
      await onIdentityChange()
      await load(selection.current)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Catalog operation failed') }
    finally { setBusy(false) }
  }, [load, onIdentityChange])
  return { page, sources, detail, error, busy, load, inspect, mutate }
}
