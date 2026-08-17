const SCOPE = self.registration.scope
const CACHE_NAMESPACE = `jarvis-shell:${SCOPE}`
const CACHE = `${CACHE_NAMESPACE}:v2`
const SHELL = [
  SCOPE,
  new URL('manifest.webmanifest', SCOPE).href,
  new URL('jarvis-icon.svg', SCOPE).href,
]

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(`${CACHE_NAMESPACE}:`) && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  const cacheableDestinations = new Set([
    'document',
    'font',
    'image',
    'manifest',
    'script',
    'style',
  ])
  if (
    request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    (request.mode !== 'navigate' && !cacheableDestinations.has(request.destination))
  ) {
    return
  }

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone()
          void caches.open(CACHE).then((cache) => cache.put(request, copy))
        }
        return response
      })
      .catch(async () => {
        const cached = await caches.match(request)
        if (cached) return cached
        if (request.mode === 'navigate') {
          const shell = await caches.match(SCOPE)
          if (shell) return shell
        }
        return Response.error()
      }),
  )
})
