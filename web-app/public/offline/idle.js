// Shares the signed-in app's session activity and timeout settings across tabs.
export function offlineIdle(user, isPlaying, signOut) {
  if (!user) return () => {}
  const key = 'sh.idle.v1:' + user.sid
  const limitKey = 'sh.idle.limit.v1:' + user.sid
  const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(key)
  let closed = false,
    probe = 0,
    lastWrite = 0
  function expired() {
    const limit = Number(localStorage.getItem(limitKey))
    const last = Number(localStorage.getItem(key))
    return limit > 0 && limit <= 1440 && last > 0 && Date.now() - last >= limit * 60000
  }
  function remember() {
    if (Number(localStorage.getItem(limitKey)) <= 0 || Date.now() - lastWrite < 1000) return
    lastWrite = Date.now()
    localStorage.setItem(key, String(lastWrite))
  }
  function end(remote) {
    if (closed) return
    closed = true
    if (!remote) channel?.postMessage('signed-out')
    signOut(user, remote)
  }
  function check() {
    if (closed) return
    if (isPlaying()) remember()
    else if (expired() && !probe) {
      channel?.postMessage('playing?')
      probe = setTimeout(() => {
        probe = 0
        if (expired() && !isPlaying()) end(false)
      }, 650)
    }
  }
  function activity(e) {
    if (e.isTrusted) {
      if (expired()) check()
      else remember()
    }
  }
  if (channel)
    channel.onmessage = (e) => {
      if (e.data === 'playing?' && isPlaying()) remember()
      if (e.data === 'signed-out') end(true)
    }
  const timer = setInterval(check, 1000)
  for (const event of ['pointerdown', 'keydown', 'wheel']) window.addEventListener(event, activity)
  window.addEventListener('pageshow', check)
  document.addEventListener('visibilitychange', check)
  check()
  return () => {
    closed = true
    clearInterval(timer)
    clearTimeout(probe)
    channel?.close()
    for (const event of ['pointerdown', 'keydown', 'wheel']) window.removeEventListener(event, activity)
    window.removeEventListener('pageshow', check)
    document.removeEventListener('visibilitychange', check)
  }
}
