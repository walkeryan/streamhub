import { useEffect, useState } from 'react'
import { authenticatedFetch } from '../api/client'
import { useMe } from '../api/me'
import { inShell } from '../shell'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { Button } from './ui'

type Preferences = { enabled: boolean; wifi: boolean; mib: number }
interface OfflineStore {
  useAuthenticatedFetch: (fn: typeof authenticatedFetch) => void
  settings: (profile: string) => Preferences
  setSettings: (profile: string, prefs: Preferences) => void
  onWifi: () => boolean
  save: (id: string) => Promise<unknown>
  purge: (profile: string, sid: string) => Promise<void>
  reconcile: () => Promise<void>
}
const script = '/offline/store.js'
const supported = () =>
  !inShell() &&
  window.isSecureContext &&
  !!navigator.serviceWorker &&
  !!window.indexedDB &&
  !!window.crypto?.subtle &&
  !!navigator.locks
const openStore = async (): Promise<OfflineStore> => {
  const store: OfflineStore = await import(/* @vite-ignore */ script)
  store.useAuthenticatedFetch(authenticatedFetch)
  return store
}
const defaults = { enabled: false, wifi: true, mib: 512 }
function sessionId(token: string | null) {
  try {
    return JSON.parse(atob(token!.split('.')[1]!.replace(/-/g, '+').replace(/_/g, '/'))).sid as string
  } catch {
    return null
  }
}

export function useOfflineSession() {
  useEffect(() => {
    if (!supported()) return
    const reconcile = () => {
      const profile = useSession.getState().profile?.id
      if (profile && localStorage.getItem('sh.offline.prefs.' + profile))
        void openStore()
          .then((s) => s.reconcile())
          .catch(() => {})
    }
    const off = useSession.subscribe((next, old) => {
      const previous = sessionId(old.access)
      if (previous && old.profile && (!next.profile || previous !== sessionId(next.access))) {
        if (localStorage.getItem('sh.offline.prefs.' + old.profile.id))
          void openStore()
            .then((s) => s.purge(old.profile!.id, previous))
            .catch(() => {})
      }
    })
    reconcile()
    window.addEventListener('online', reconcile)
    return () => {
      off()
      window.removeEventListener('online', reconcile)
    }
  }, [])
}

export function OfflineSettings() {
  const profile = useSession((s) => s.profile?.id)
  const me = useMe()
  const [prefs, setPrefs] = useState(defaults)
  const [store, setStore] = useState<OfflineStore>()
  useEffect(() => {
    if (!profile || !supported() || me.data?.shared_device) return
    let active = true
    void openStore()
      .then((s) => {
        if (active) {
          setStore(s)
          setPrefs(s.settings(profile))
        }
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [profile, me.data?.shared_device])
  if (!profile || me.data?.shared_device || !store) return null
  function change(update: Partial<Preferences>) {
    const value = { ...prefs, ...update }
    store!.setSettings(profile!, value)
    setPrefs(value)
  }
  return (
    <section className="acct-section" aria-label="Offline episodes">
      <h3 className="acct-kicker">Offline episodes</h3>
      <p>
        Save the next episode from a library that allows downloads. Copies stay available on this device for up to
        24 hours.
      </p>
      <Button pressed={prefs.enabled} onEnter={() => change({ enabled: !prefs.enabled })}>
        {prefs.enabled ? 'Next episode downloads on' : 'Enable next episode downloads'}
      </Button>
      {prefs.enabled && (
        <>
          <Button pressed={prefs.wifi} onEnter={() => change({ wifi: !prefs.wifi })}>
            {prefs.wifi ? 'Wi-Fi only' : 'Any network'}
          </Button>
          <label>
            Device storage limit{' '}
            <select
              aria-label="Download storage limit"
              value={prefs.mib}
              onChange={(e) => change({ mib: Number(e.target.value) })}
            >
              {[250, 512, 1024, 2048].map((mib) => (
                <option key={mib} value={mib}>
                  {mib} MiB
                </option>
              ))}
            </select>
          </label>
          {prefs.wifi && !store.onWifi() && (
            <p>Waiting for confirmed Wi-Fi. Choose Any network to allow downloads on this connection.</p>
          )}
        </>
      )}
      <Button
        onEnter={() => {
          window.location.assign('/offline/index.html')
        }}
      >
        Open downloads
      </Button>
    </section>
  )
}

export function OfflineEpisodeAction({ id }: { id: string }) {
  const profile = useSession((s) => s.profile?.id)
  const me = useMe()
  const [busy, setBusy] = useState(false)
  const toast = useUi((s) => s.toast)
  if (!profile || !supported() || me.data?.shared_device) return null
  async function save() {
    setBusy(true)
    try {
      const store = await openStore()
      if (!store.settings(profile!).enabled) throw new Error('Enable offline episodes in Playback settings first.')
      await store.save(id)
      toast('Episode saved for offline viewing.', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Episode download failed.', 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button disabled={busy} onEnter={() => void save()}>
      {busy ? 'Saving episode…' : 'Download episode'}
    </Button>
  )
}

export function useDownloadNextEpisode(id: string | null | undefined) {
  const profile = useSession((s) => s.profile?.id)
  const toast = useUi((s) => s.toast)
  useEffect(() => {
    if (!id || !profile || !supported() || !localStorage.getItem('sh.offline.prefs.' + profile)) return
    let active = true
    const download = async () => {
      const store = await openStore()
      const prefs = store.settings(profile)
      if (!active || !navigator.onLine || !prefs.enabled || (prefs.wifi && !store.onWifi())) return
      try {
        await store.save(id)
      } catch (e) {
        if (active) toast(e instanceof Error ? e.message : 'Next episode download failed.', 'error')
      }
    }
    const online = () => {
      void download().catch(() => {})
    }
    online()
    const connection = (navigator as Navigator & { connection?: EventTarget }).connection
    connection?.addEventListener('change', online)
    window.addEventListener('online', online)
    return () => {
      active = false
      connection?.removeEventListener('change', online)
      window.removeEventListener('online', online)
    }
  }, [id, profile, toast])
}
