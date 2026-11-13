import { queryClient } from './api/queryClient'
import { currentAccessToken } from './api/client'
import { hasAudioConsent } from './player/audioConsent'
import { useSession } from './store/session'
import { useUi } from './store/ui'
import { bridgeOf, shellViewport, titlePlay, type ShellPlay, type StreamHubTVBridge } from './shellProtocol'

/*
 * Shell mode: this web app inside the Android TV app (see shellProtocol.ts for the bridge). Every
 * function here first looks for the bridge; an ordinary browser has none and none of this runs.
 */

export const shellBridge = (): StreamHubTVBridge | null => (typeof window === 'undefined' ? null : bridgeOf(window))
export const inShell = (): boolean => shellBridge() !== null

/** Asks the TV to play. True when the TV took it (its player opens over this page). */
export function handOff(play: ShellPlay): boolean {
  const bridge = shellBridge()
  if (!bridge) return false
  let taken: boolean
  try { taken = bridge.play(JSON.stringify(play)) === true } catch { taken = false }
  if (!taken) useUi.getState().toast('The TV could not start this.', 'error')
  return taken
}

/**
 * A player address (`/play/…`) in shell mode: handed to the TV instead of opening the web player.
 * True when it was taken care of (handed over, or refused with a message); false outside the shell.
 */
export function handOffUrl(to: string): boolean {
  if (!inShell()) return false
  const play = titlePlay(to, hasAudioConsent)
  if (!play) return false
  handOff(play)
  return true
}

/** What Back does in the page, registered by nav/back.tsx (the same handling as Escape and Backspace). */
let back: (() => boolean) | null = null
export function setShellBack(handler: (() => boolean) | null): void { back = handler }

declare global {
  interface Window {
    streamhubShell?: { back: () => boolean; token: (id: string, rejected: string | null) => void; playbackEnded: () => void }
  }
}

/** Called once, before the app renders. Does nothing in an ordinary browser. */
export function installShell(): void {
  const bridge = shellBridge()
  if (!bridge) return
  const root = document.documentElement
  root.dataset.shell = 'android-tv'
  // The layout a 1920×1080 desktop browser gets, whatever CSS width the TV's WebView reports.
  let meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]')
  if (!meta) { meta = document.createElement('meta'); meta.name = 'viewport'; document.head.appendChild(meta) }
  // The narrower of the two: a WebView that reports physical pixels for one of them still gets the right scale.
  const widths = [window.screen?.width, window.innerWidth].filter((w): w is number => typeof w === 'number' && w > 0)
  meta.content = shellViewport(widths.length ? Math.min(...widths) : 0)

  window.streamhubShell = {
    // false only on the home screen with nothing to close: the TV then leaves the app.
    back: () => back?.() ?? false,
    token: (id, rejected) => {
      void currentAccessToken(typeof rejected === 'string' ? rejected : null).then((access) => {
        const profile = useSession.getState().profile
        bridge.deliverToken(String(id), JSON.stringify(access && profile ? { access, profile } : null))
      }, () => bridge.deliverToken(String(id), 'null'))
    },
    // The TV's player closed: progress on tiles and rows is read again; screen and focus are untouched.
    playbackEnded: () => { void queryClient.invalidateQueries() },
  }

  // The TV signs in through its own pairing screen: a page with no session hands back to it.
  const signedOut = () => window.setTimeout(() => {
    if (!useSession.getState().profile) bridge.signedOut(useSession.getState().notice ?? '')
  }, 0)
  if (!useSession.getState().profile) signedOut()
  useSession.subscribe((next, previous) => { if (previous.profile && !next.profile) signedOut() })
}
