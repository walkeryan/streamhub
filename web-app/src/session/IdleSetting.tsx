import { useState } from 'react'
import { Button, Chip } from '../components/ui'
import { FocusableInput } from '../nav/Focusable'
import { useIdleMinutes } from './useIdleSignOut'

export function IdleSetting() {
  const pref = useIdleMinutes()
  const [custom, setCustom] = useState('')
  const n = Number(custom)
  const valid = Number.isInteger(n) && n >= 1 && n <= 1440
  return (
    <section className="set-card" aria-labelledby="idle-label">
      <h2 id="idle-label">Idle sign-out</h2>
      <p>Sign out of this device after inactivity. Watching a title keeps the session open.</p>
      <p aria-live="polite">
        {pref.value ? 'After ' + pref.value + (pref.value === 1 ? ' minute' : ' minutes') : 'Off'}
      </p>
      <div className="chip-scroll">
        {[0, 5, 15, 30, 60].map((value) => (
          <Chip
            key={value}
            focusKey={'idle:' + value}
            pressed={pref.value === value}
            onEnter={() => pref.update(value)}
          >
            {value ? value + ' min' : 'Off'}
          </Chip>
        ))}
      </div>
      <FocusableInput
        label="Custom idle timeout in minutes"
        placeholder="1–1440 minutes"
        focusKey="idle:minutes"
        inputMode="numeric"
        value={custom}
        maxLength={4}
        onChange={setCustom}
      />
      <Button
        focusKey="idle:save"
        disabled={!pref.ready || !valid || pref.saving}
        onEnter={() => {
          pref.update(n)
          setCustom('')
        }}
      >
        Set custom timeout
      </Button>
      {pref.error && <p role="alert">{pref.error}</p>}
    </section>
  )
}
