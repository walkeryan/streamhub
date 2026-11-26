// Only the offline reader's shipped public assets are cached here. Account and media responses never enter Cache Storage.
const cacheName = 'streamhub-offline-reader-v1'
const shell = [
  '/offline/index.html',
  '/offline/reader.js',
  '/offline/store.js',
  '/offline/reader.css',
  '/offline/idle.js',
]
self.addEventListener('install', (event) =>
  event.waitUntil(
    (async () => {
      const cache = await caches.open(cacheName)
      await cache.addAll(shell)
      await self.skipWaiting()
    })(),
  ),
)
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== location.origin) return
  if (shell.includes(url.pathname))
    event.respondWith(
      caches
        .open(cacheName)
        .then((cache) => cache.match(url.pathname))
        .then((hit) => hit || fetch(request)),
    )
  else if (request.mode === 'navigate' && !url.pathname.startsWith('/api/')) {
    const offline = async () => (await caches.open(cacheName)).match('/offline/index.html')
    event.respondWith(
      (async () => {
        if (!self.navigator.onLine) return offline()
        try {
          const response = await fetch(request)
          return response.status === 502 || response.status === 503 || response.status === 504
            ? offline()
            : response
        } catch {
          return offline()
        }
      })(),
    )
  }
})
