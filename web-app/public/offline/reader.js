import { offlineIdle } from './idle.js'
import { owner, purge, list, openFile, checkpoint, reconcile, check, remove } from './store.js'
const video = document.querySelector('video'),
  status = document.querySelector('#status'),
  items = document.querySelector('#items'),
  close = document.querySelector('#close')
let active,
  url,
  timer,
  writing = Promise.resolve(),
  opening = 0
const tell = (e) => {
  status.textContent = e.message || String(e)
}
function save() {
  if (active && !video.hidden && video.readyState) {
    const id = active.id,
      pos = video.currentTime * 1000,
      dur = video.duration * 1000
    writing = writing.then(() => checkpoint(id, pos, dur)).catch(tell)
  }
}
function stop() {
  opening++
  save()
  clearInterval(timer)
  active = null
  video.pause()
  video.removeAttribute('src')
  video.load()
  video.hidden = true
  close.hidden = true
  if (url) URL.revokeObjectURL(url)
  url = null
}
async function play(row) {
  stop()
  await writing
  const seq = opening
  try {
    const file = await openFile(row.id)
    if (seq !== opening) {
      URL.revokeObjectURL(file.url)
      return
    }
    active = file.row
    url = file.url
    video.src = url
    video.hidden = false
    close.hidden = false
    video.onloadedmetadata = () => {
      if (active && active.position > 0 && active.position < video.duration * 950)
        video.currentTime = active.position / 1000
    }
    await video.play()
    status.textContent = row.title
    timer = setInterval(async () => {
      try {
        await check(row)
        save()
      } catch (e) {
        stop()
        tell(e)
        await render()
      }
    }, 15000)
  } catch (e) {
    stop()
    tell(e)
  }
}
function button(label, action) {
  const b = document.createElement('button')
  b.textContent = label
  b.onclick = action
  return b
}
async function render() {
  const rows = await list()
  items.replaceChildren()
  if (!rows.length) {
    status.textContent = 'No downloaded episodes for this session.'
    return
  }
  for (const row of rows) {
    const el = document.createElement('article'),
      title = document.createElement('strong')
    title.textContent = row.title
    el.append(
      title,
      button('Play', () => play(row)),
      button('Remove', async () => {
        if (active?.id === row.id) stop()
        await remove(row.id)
        await render()
      }),
    )
    items.append(el)
  }
}
async function online() {
  try {
    if (active) await check(active)
    await writing
    await reconcile()
  } catch (e) {
    stop()
    tell(e)
  }
  await render()
}
close.onclick = () => {
  stop()
  writing.then(online)
}
video.onended = () => {
  save()
  writing.then(online)
}
let lastSaved = 0
video.ontimeupdate = () => {
  if (Date.now() - lastSaved > 2500) {
    lastSaved = Date.now()
    save()
  }
}
video.onerror = () => {
  if (active) {
    stop()
    tell('This device cannot decode the downloaded episode.')
  }
}
video.onpause = save
window.addEventListener('pagehide', save)
window.addEventListener('online', online)
const initialUser = owner()
const deactivate = offlineIdle(
  initialUser,
  () => !!active && !video.paused && !video.ended && video.readyState >= 2,
  async (user, remote) => {
    if (owner()?.sid !== user.sid) return
    stop()
    items.replaceChildren()
    const current = owner()
    localStorage.removeItem('sh.session.v1')
    localStorage.removeItem('sh.idle.v1:' + user.sid)
    localStorage.removeItem('sh.idle.limit.v1:' + user.sid)
    if (!remote && navigator.onLine)
      void fetch('/api/v1/auth/sessions/' + encodeURIComponent(user.sid), {
        method: 'DELETE',
        headers: { Authorization: 'Bearer ' + current.access },
        keepalive: true,
      }).catch(() => {})
    await purge(user.profile, user.sid)
    tell('Signed out after inactivity. Sign in to continue.')
  },
)
window.addEventListener('storage', (event) => {
  if (event.key === 'sh.session.v1' && owner()?.sid !== initialUser?.sid) {
    deactivate()
    stop()
    items.replaceChildren()
    if (initialUser) void purge(initialUser.profile, initialUser.sid)
    location.reload()
  }
})
document.addEventListener('visibilitychange', () => {
  if (document.hidden) save()
  else online().catch(tell)
})
await online().catch(tell)
