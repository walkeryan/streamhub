import { useState, type ReactNode } from 'react'
import { refreshMe } from '../api/client'
import { useCan, useMe } from '../api/me'
import { people } from '../api/people'
import { useUi } from '../store/ui'
import { PinDialog } from './PinPad'

/**
 * Adult titles, for a user who holds watch.adult and nobody else. They stay out of every listing
 * until that user unlocks them on this session with their own PIN (the server keeps it open for
 * four hours); someone with no PIN yet chooses one first. `offered` is false for everyone without
 * the permission, and against a server that has no /auth/me to say so.
 */
export function useAdultUnlock(): { offered: boolean; unlocked: boolean; ask: () => void; lock: () => void; dialog: ReactNode } {
  const me = useMe().data
  const offered = useCan('watch.adult') && !!me
  const toast = useUi((s) => s.toast)
  const [asking, setAsking] = useState<'pin' | 'setup' | null>(null)
  const unlock = async (pin: string) => {
    await people.adultUnlock(pin)
    refreshMe()
    setAsking(null)
    toast('Adult titles are shown on this device for four hours, or until you lock them.', 'success')
  }
  const lock = () => {
    void people.adultLock().then(() => { refreshMe(); toast('Adult titles are locked.', 'success') }, (e: unknown) => toast(e instanceof Error ? e.message : 'Could not lock.', 'error'))
  }
  const dialog = offered && me ? <>
    <PinDialog open={asking === 'pin'} onClose={() => setAsking(null)} title="Your PIN" hint="Enter your PIN to show adult titles on this device." submitLabel="Unlock" onSubmit={unlock}
      explain={(code) => (code === 'pin_setup_required' ? 'You have no PIN yet. Close this and choose one first.' : undefined)} />
    <PinDialog open={asking === 'setup'} onClose={() => setAsking(null)} title="Choose a PIN first" confirm submitLabel="Save PIN and unlock"
      hint="Adult titles open with your own PIN, and you do not have one yet. Choose four to eight digits. The same PIN protects you on shared devices."
      onSubmit={async (pin) => { await people.setPin(me.user.id, pin); await unlock(pin) }} />
  </> : null
  return { offered, unlocked: offered && !!me?.adult_unlocked, ask: () => setAsking(me?.user.has_pin ? 'pin' : 'setup'), lock, dialog }
}
