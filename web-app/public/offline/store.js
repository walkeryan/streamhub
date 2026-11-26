const database = 'streamhub-private-downloads-v1'
const prefKey = 'sh.offline.prefs.'
const MiB = 1024 * 1024
export const supported = () =>
  window.isSecureContext && !!navigator.serviceWorker && !!window.indexedDB && !!crypto.subtle && !!navigator.locks
export function owner() {
  try {
    const s = JSON.parse(localStorage.getItem('sh.session.v1'))
    const jwt = JSON.parse(atob(s.access.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return s.profile?.id && jwt.sid ? { profile: s.profile.id, sid: jwt.sid, access: s.access } : null
  } catch {
    return null
  }
}
const same = (a, b) => !!a && !!b && a.profile === b.profile && a.sid === b.sid
export function settings(profile) {
  try {
    const prefs = JSON.parse(localStorage.getItem(prefKey + profile)) || {}
    return {
      enabled: prefs.enabled === true,
      wifi: prefs.wifi !== false,
      mib: [250, 512, 1024, 2048].includes(prefs.mib) ? prefs.mib : 512,
    }
  } catch {
    return { enabled: false, wifi: true, mib: 512 }
  }
}
export const setSettings = (profile, prefs) => localStorage.setItem(prefKey + profile, JSON.stringify(prefs))
const connection = () => navigator.connection || navigator.mozConnection || navigator.webkitConnection
export const onWifi = () => connection()?.type === 'wifi'
const db = new Promise((resolve, reject) => {
  const request = indexedDB.open(database, 1)
  request.onupgradeneeded = () => {
    const store = request.result
    store.createObjectStore('files', { keyPath: 'id' })
    store.createObjectStore('parts', { keyPath: ['grant', 'index'] })
    store.createObjectStore('keys')
  }
  request.onsuccess = () => resolve(request.result)
  request.onerror = () => reject(request.error)
})
async function operation(name, mode, work) {
  const connection = await db
  return new Promise((resolve, reject) => {
    const tx = connection.transaction(name, mode)
    const request = work(tx.objectStore(name))
    tx.oncomplete = () => resolve(request?.result)
    tx.onabort = tx.onerror = () => reject(tx.error || new Error('Device storage failed.'))
  })
}
const files = () => operation('files', 'readonly', (s) => s.getAll())
const write = (value) => operation('files', 'readwrite', (s) => s.put(value))
const owns = (row, user = owner()) => same({ profile: row.profile_id, sid: row.session_id }, user)
export async function remove(id) {
  const row = await operation('files', 'readonly', (s) => s.get(id))
  if (row && owns(row)) await erase(row)
}
async function erase(row) {
  await operation('parts', 'readwrite', (s) =>
    s.delete(IDBKeyRange.bound([row.id, 0], [row.id, Number.MAX_SAFE_INTEGER])),
  )
  await operation('files', 'readwrite', (s) => s.delete(row.id))
}
export async function purge(profile, sid) {
  for (const row of await files()) if (row.profile_id === profile && row.session_id === sid) await erase(row)
  await operation('keys', 'readwrite', (s) => s.delete(profile + '|' + sid))
}
export async function list() {
  const out = []
  for (const row of await files()) {
    if (Date.parse(row.expires_at) <= Date.now()) {
      await erase(row)
      continue
    }
    if (row.ready && owns(row)) out.push(row)
  }
  return out
}
let appFetch = null
export function useAuthenticatedFetch(fn) {
  appFetch = fn
}
async function readerFetch(request, user) {
  const first = await fetch(request.clone())
  if (first.status !== 401) return first
  const problem = await first
    .clone()
    .json()
    .catch(() => ({}))
  if (!['token_expired', 'session_stale'].includes(problem.code)) return first
  const refreshed = await navigator.locks.request('streamhub-offline-refresh', async () => {
    const current = owner()
    if (!same(current, user)) return false
    if (current.access !== user.access) return true
    const original = JSON.parse(localStorage.getItem('sh.session.v1'))
    const response = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: original.refresh }),
    })
    if (!response.ok) return false
    const tokens = await response.json()
    if (!same(user, owner()) || tokens.profile.id !== user.profile) return false
    localStorage.setItem(
      'sh.session.v1',
      JSON.stringify({ profile: tokens.profile, access: tokens.access_token, refresh: tokens.refresh_token }),
    )
    return true
  })
  if (!refreshed) return first
  const retry = new Request(request)
  retry.headers.set('Authorization', 'Bearer ' + owner().access)
  return fetch(retry)
}
async function api(path, opts = {}, user = owner()) {
  if (!user || !same(user, owner())) throw new Error('Sign in again to access these downloads.')
  const request = new Request(new URL('/api/v1/viewing/downloads' + path, location.origin), {
    ...opts,
    cache: 'no-store',
    headers: { Authorization: 'Bearer ' + owner().access },
  })
  const response = appFetch ? await appFetch(request) : await readerFetch(request, user)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw Object.assign(new Error(data.message || 'The library could not complete the download.'), {
      status: response.status,
    })
  }
  if (!same(user, owner())) throw new Error('The active profile changed.')
  return response
}
const revoked = (e) => [401, 403, 404, 409, 410].includes(e.status)
async function keyFor(user, create) {
  const id = user.profile + '|' + user.sid
  let key = await operation('keys', 'readonly', (s) => s.get(id))
  if (!key && create) {
    key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
    await operation('keys', 'readwrite', (s) => s.put(key, id))
  }
  if (!key) throw new Error('This session no longer has its download key.')
  return key
}
export async function save(ref) {
  if (!supported()) throw new Error('Downloads require a secure browser with private storage support.')
  const user = owner()
  if (!user) throw new Error('Sign in to download an episode.')
  return navigator.locks.request('streamhub-offline-writer', async () => {
    function permitted() {
      const prefs = settings(user.profile)
      if (!same(user, owner()) || !prefs.enabled) throw new Error('Downloads are disabled for this session.')
      if (prefs.wifi && !onWifi())
        throw new Error('Waiting for confirmed Wi-Fi. You can change the network preference in Settings.')
      return prefs
    }
    const prefs = permitted()
    const rows = await files()
    for (const row of rows) if (!row.ready || Date.parse(row.expires_at) <= Date.now()) await erase(row)
    const current = await files()
    const existing = current.find((f) => owns(f, user) && f.ref_id === ref)
    if (existing) {
      await check(existing)
      return existing
    }
    // The cap belongs to this device, including the bytes saved by its other profiles.
    const remaining = prefs.mib * MiB - current.reduce((sum, f) => sum + f.size, 0)
    if (remaining <= 0) throw new Error('Download storage is full.')
    const worker = await navigator.serviceWorker.register('/offline-worker.js', { scope: '/' })
    await navigator.serviceWorker.ready
    if (!worker.active && !worker.waiting) throw new Error('Offline reader could not be installed.')
    const grant = await (
      await api('?' + new URLSearchParams({ id: ref, max_bytes: String(remaining) }), { method: 'POST' }, user)
    ).json()
    if (!owns(grant, user) || grant.size > remaining || !document.createElement('video').canPlayType(grant.mime))
      throw new Error('This episode cannot be stored or played on this device.')
    const row = { ...grant, ready: false, position: 0, duration: grant.duration_ms || 0, dirty: false, parts: 0 }
    const key = await keyFor(user, true)
    const controller = new AbortController()
    const networkChanged = () => {
      try {
        permitted()
      } catch {
        controller.abort()
      }
    }
    connection()?.addEventListener?.('change', networkChanged)
    try {
      await write(row)
      const stream = await api('/' + grant.id + '/content', { signal: controller.signal }, user)
      if (!stream.body) throw new Error('The source supplied no file.')
      const reader = stream.body.getReader()
      let total = 0,
        chunks = [],
        buffered = 0
      const flush = async () => {
        if (!buffered) return
        const bytes = new Uint8Array(buffered)
        let offset = 0
        for (const piece of chunks) {
          bytes.set(piece, offset)
          offset += piece.length
        }
        const iv = crypto.getRandomValues(new Uint8Array(12))
        const data = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, bytes)
        await operation('parts', 'readwrite', (s) => s.put({ grant: row.id, index: row.parts++, iv, data }))
        chunks = []
        buffered = 0
      }
      for (;;) {
        permitted()
        const { done, value } = await reader.read()
        if (done) break
        total += value.length
        if (total > grant.size || total > remaining) throw new Error('The source exceeded the download limit.')
        chunks.push(value)
        buffered += value.length
        if (buffered >= MiB) await flush()
      }
      await flush()
      if (total !== grant.size) throw new Error('Download was interrupted. Try again.')
      permitted()
      row.ready = true
      await write(row)
      return row
    } catch (e) {
      controller.abort()
      await erase(row)
      throw e
    } finally {
      connection()?.removeEventListener?.('change', networkChanged)
    }
  })
}
export async function check(row) {
  if (!owns(row) || Date.parse(row.expires_at) <= Date.now()) throw new Error('The offline license has ended.')
  if (navigator.onLine) {
    try {
      await api('/' + row.id)
    } catch (e) {
      if (revoked(e)) await erase(row)
      if (!(e instanceof TypeError)) throw e
    }
  }
}
export async function openFile(id) {
  const row = (await list()).find((f) => f.id === id)
  if (!row) throw new Error('This episode is unavailable for this session.')
  await check(row)
  const user = owner(),
    key = await keyFor(user, false),
    parts = []
  for (let i = 0; i < row.parts; i++) {
    const part = await operation('parts', 'readonly', (s) => s.get([row.id, i]))
    if (!part) throw new Error('The saved file is incomplete.')
    parts.push(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: part.iv }, key, part.data))
  }
  if (!same(user, owner())) throw new Error('The active profile changed.')
  return { row, url: URL.createObjectURL(new Blob(parts, { type: row.mime })) }
}
export async function checkpoint(id, position, duration) {
  if (!Number.isFinite(duration) || duration <= 0) return
  const row = await operation('files', 'readonly', (s) => s.get(id))
  if (!row || !owns(row)) return
  row.position = Math.max(0, Math.floor(position))
  row.duration = Math.floor(duration)
  row.dirty = true
  await write(row)
}
export async function reconcile() {
  if (!navigator.onLine) return
  for (const row of await list()) {
    await check(row)
    if (!row.dirty) continue
    try {
      await api(
        '/' +
          row.id +
          '/progress?' +
          new URLSearchParams({ position_ms: String(row.position), duration_ms: String(row.duration) }),
        { method: 'POST' },
      )
    } catch (e) {
      if (e.status !== 409) throw e
    }
    const latest = await operation('files', 'readonly', (s) => s.get(row.id))
    if (latest && latest.position === row.position) {
      latest.dirty = false
      await write(latest)
    }
  }
}
