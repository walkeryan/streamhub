import { useState, type ReactNode } from 'react'
import { refreshMe } from '../api/client'
import { useCan, useMe } from '../api/me'
import { people } from '../api/people'
import { useUi } from '../store/ui'
import { AskSecretDialog, ChooseSecretDialog, kindOf } from './SignInSecret'

/**
 * Adult titles, for a user who holds watch.adult and nobody else. They stay out of every listing
 * until that user unlocks them on this session with their own PIN (the server keeps it open for
 * four hours), asked for the way they chose it (number PIN, arrow pattern or password); someone with
 * none yet chooses one first. `offered` is false for everyone without
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
  const kind = kindOf(me?.user)
  const dialog = offered && me ? <>
    {kind && <AskSecretDialog open={asking === 'pin'} kind={kind} arrows={me.user.pin_arrows} onClose={() => setAsking(null)} submitLabel="Unlock" onSubmit={unlock}
      title={{ pin: 'Your PIN', arrows: 'Your arrow pattern', password: 'Your password' }}
      hint={{
        pin: 'Enter your PIN to show adult titles on this device.',
        arrows: 'Press your arrow pattern to show adult titles on this device.',
        password: 'Enter your password to show adult titles on this device.',
      }}
      explain={(code) => (code === 'pin_setup_required' ? 'You have no PIN yet. Close this and choose one first.' : undefined)} />}
    {/* Adult titles open with the person's own secret; someone without one picks how they will sign in, then it unlocks. */}
    <ChooseSecretDialog open={asking === 'setup'} onClose={() => setAsking(null)} name={me.user.display_name} scope="adult" current={null}
      note="Adult titles open with it, and you do not have one yet. The same one protects you on shared devices."
      onSave={async (pin, k) => { await people.setPin(me.user.id, pin, k); await unlock(pin) }} />
  </> : null
  return { offered, unlocked: offered && !!me?.adult_unlocked, ask: () => setAsking(me?.user.has_pin ? 'pin' : 'setup'), lock, dialog }
}
