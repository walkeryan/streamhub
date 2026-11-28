import { useEffect } from 'react'
import { useSession } from '../store/session'
import { useProfilePreference } from '../api/profilePreferences'
import { queryClient } from '../api/queryClient'
import { nativeCastAvailable, receiverPlayback } from '../player/castNative'
import { useCastController } from '../cast/controller'
import { inShell, shellBridge } from '../shell'
import { useWho } from '../store/who'
import { idleExpired, idleSessionId } from './idleClock'

const validMinutes = (v: unknown): number | null =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= 1440 ? v : null
export const useIdleMinutes = () => useProfilePreference<number>('idleSignOutMinutes.v1', 0, validMinutes)
let playing = false
export function reportPlayerActivity(active: boolean) {
  playing = active
  window.dispatchEvent(new Event('sh:playback-activity'))
}

export function useIdleSignOut(sharedDevice: boolean | undefined) {
  const { value: minutes, ready } = useIdleMinutes()
  const sid = useSession((s) => idleSessionId(s.access))
  const picked = useWho((s) => s.picked)
  useEffect(() => {
    if (!sid || sharedDevice === undefined || (sharedDevice && !picked)) return
    const limitKey = 'sh.idle.limit.v1:' + sid
    const limit = ready ? minutes : (validMinutes(Number(localStorage.getItem(limitKey))) ?? 0)
    if (ready) localStorage.setItem(limitKey, String(limit))
    const key = 'sh.idle.v1:' + sid
    if (!limit) {
      localStorage.removeItem(key)
      return
    }
    if (sharedDevice) localStorage.setItem(key, String(Date.now()))
    let last = Number(localStorage.getItem(key)) || Date.now()
    let lastWrite = 0
    let ended = false
    let previousPlayback = false
    let probe: ReturnType<typeof setTimeout> | undefined
    const channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(key) : null
    let castPlayingAt = 0
    const unwatch = useCastController.subscribe((next, previous) => {
      if (next.session !== previous.session)
        castPlayingAt = next.session?.status === 'connected' && next.session.playback.playing ? Date.now() : 0
    })
    const isPlaying = () =>
      playing ||
      (castPlayingAt > 0 && Date.now() - castPlayingAt < 15_000) ||
      (nativeCastAvailable() && receiverPlayback().playing)
    const remember = (now: number) => {
      last = Math.max(last, now)
      if (now - lastWrite >= 1000) {
        localStorage.setItem(key, String(Math.max(last, Number(localStorage.getItem(key)) || 0)))
        lastWrite = now
      }
    }
    const signOut = (remote = false) => {
      const current = useSession.getState()
      if (ended || idleSessionId(current.access) !== sid) return
      ended = true
      if (!remote && current.access) {
        channel?.postMessage('signed-out')
        void fetch('/api/v1/auth/sessions/' + encodeURIComponent(sid), {
          method: 'DELETE',
          headers: { Authorization: 'Bearer ' + current.access },
          keepalive: true,
        }).catch(() => undefined)
      }
      localStorage.removeItem(key)
      localStorage.removeItem(limitKey)
      queryClient.clear()
      current.end('Signed out after inactivity. Sign in to continue.')
    }
    const returnToPicker = (remote = false) => {
      const current = useSession.getState()
      if (ended || idleSessionId(current.access) !== sid) return
      ended = true
      if (!remote) channel?.postMessage('profile-picker')
      // The paired session is the authority for the real shared-device picker and its PIN
      // checks. Keep it, and give the next chosen profile a fresh idle window.
      remember(Date.now())
      useWho.getState().reset()
    }
    // The limit is the profile's and follows it to every device. A TV that is one person's own is not a
    // browser left open: signing it out would unpair it, so there the limit only starts again. A shared TV
    // still returns to the picker.
    const expire = () => sharedDevice ? returnToPicker() : inShell() ? remember(Date.now()) : signOut()
    function expired(now: number) {
      last = Math.max(last, Number(localStorage.getItem(key)) || 0)
      return idleExpired(last, limit, now)
    }
    function check() {
      if (ended) return
      const now = Date.now()
      const active = isPlaying()
      if (active || previousPlayback) remember(now)
      previousPlayback = active
      if (!expired(now) || probe !== undefined) return
      channel?.postMessage('playing?')
      probe = setTimeout(() => {
        probe = undefined
        if (!ended && !isPlaying() && expired(Date.now())) expire()
      }, 650)
    }
    function activity(e: Event) {
      if (!e.isTrusted || ended) return
      const now = Date.now()
      if (expired(now)) check()
      else remember(now)
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'sh.session.v1' && e.newValue === null) signOut(true)
    }
    if (channel)
      channel.onmessage = (e) => {
        if (e.data === 'signed-out') signOut(true)
        if (e.data === 'profile-picker' && sharedDevice) returnToPicker(true)
        if (e.data === 'playing?' && isPlaying()) remember(Date.now())
      }
    const nativeInput = () => {
      if (nativeCastAvailable()) {
        const bridge = shellBridge() as { castState?: () => string } | null
        try {
          const at = JSON.parse(bridge?.castState?.() ?? '{}').interaction_at
          if (typeof at === 'number' && at > last && at <= Date.now() && !idleExpired(last, limit, at))
            remember(at)
        } catch {
          /* The player may close while it is being read. */
        }
      }
      check()
    }
    if (!localStorage.getItem(key)) remember(last)
    nativeInput()
    const events = ['pointerdown', 'keydown', 'input', 'wheel', 'touchstart']
    for (const name of events) window.addEventListener(name, activity, { capture: true, passive: true })
    window.addEventListener('sh:playback-activity', check)
    window.addEventListener('storage', onStorage)
    window.addEventListener('pageshow', nativeInput)
    document.addEventListener('visibilitychange', nativeInput)
    const timer = setInterval(nativeInput, 1500)
    return () => {
      unwatch()
      clearInterval(timer)
      clearTimeout(probe)
      channel?.close()
      for (const name of events) window.removeEventListener(name, activity, true)
      window.removeEventListener('sh:playback-activity', check)
      window.removeEventListener('storage', onStorage)
      window.removeEventListener('pageshow', nativeInput)
      document.removeEventListener('visibilitychange', nativeInput)
    }
  }, [sid, ready, minutes, picked, sharedDevice])
}
