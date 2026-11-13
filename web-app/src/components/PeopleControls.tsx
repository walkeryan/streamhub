import type { ReactNode } from 'react'
import type { components } from '../api/schema'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { MOVIE_RATINGS, PERMISSION_GROUPS, STREAM_CHOICES, TV_RATINGS, type Limits } from '../lib/people'
import { Icon } from './Icon'
import { Button, Chip } from './ui'

type Permission = components['schemas']['Permission']

/** A switch on one line: what it is, what it means and, for a user, where its value comes from.
 *  Without `onChange` it only shows its value, and still takes focus so a remote can read down a list of them. */
export function Toggle(p: { focusKey: string; label: string; hint?: string; on: boolean; onChange?: (on: boolean) => void; note?: string; testId?: string }) {
  return (
    <Focusable focusKey={p.focusKey} className="toggle fx-flat" aria-pressed={p.on} data-testid={p.testId} data={{ readonly: !p.onChange }}
      label={`${p.label}: ${p.on ? 'on' : 'off'}${p.note ? `, ${p.note.toLowerCase()}` : ''}${p.onChange ? '' : ', cannot be changed'}`} onEnter={() => p.onChange?.(!p.on)}>
      <span className="toggle-box" aria-hidden="true">{p.on && <Icon name="check" size={16} />}</span>
      <span className="flex flex-col flex-1 min-w-0">
        <span style={{ fontWeight: 600 }}>{p.label}</span>
        {p.hint && <span className="label">{p.hint}</span>}
      </span>
      {p.note && <span className="badge" data-testid="toggle-note">{p.note}</span>}
    </Focusable>
  )
}

/**
 * The sixteen permissions as switches, grouped. For a role, `held` is the role's set. For a user,
 * `held` is their effective set and `inherited` their role's: a switch that differs from the role is
 * marked Overridden and gets a Reset beside it.
 */
export function PermissionSwitches(p: { scope: string; held: readonly Permission[]; inherited?: readonly Permission[]; onChange?: (id: Permission, on: boolean) => void; onReset?: (id: Permission) => void }) {
  return (
    <div className="flex flex-col gap-4">
      {PERMISSION_GROUPS.map((group) => (
        <section key={group.title} className="flex flex-col gap-2">
          <h3 className="label">{group.title}</h3>
          <FocusGroup focusKey={`${p.scope}:group:${group.title}`} className="toggle-list">
            {group.items.map((item) => {
              const on = p.held.includes(item.id)
              const overridden = !!p.inherited && p.inherited.includes(item.id) !== on
              return (
                <div key={item.id} className="toggle-row" data-permission={item.id} data-overridden={p.inherited ? overridden : undefined}>
                  <Toggle focusKey={`${p.scope}:${item.id}`} label={item.label} hint={item.hint} on={on} note={p.inherited ? (overridden ? 'Overridden' : 'From role') : undefined}
                    onChange={p.onChange && ((value) => p.onChange!(item.id, value))} />
                  {overridden && p.onReset && <Button focusKey={`${p.scope}:${item.id}:reset`} ghost label={`Reset ${item.label} to the role`} onEnter={() => p.onReset!(item.id)}>Reset</Button>}
                </div>
              )
            })}
          </FocusGroup>
        </section>
      ))}
    </div>
  )
}

/**
 * Streams at once, the two rating ceilings and whether unrated titles show. `overridden` and
 * `onReset` are for a user, whose limits sit on top of their role's.
 */
export function LimitsEditor(p: {
  scope: string; limits: Limits; onChange?: (change: Partial<Limits>) => void
  overridden?: Partial<Record<keyof Limits, boolean>>; onReset?: (key: keyof Limits) => void
}) {
  const row = (key: keyof Limits, title: string, chips: ReactNode) => (
    <section className="flex flex-col gap-2" data-limit={key} data-overridden={p.overridden ? !!p.overridden[key] : undefined}>
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="label">{title}</h3>
        {p.overridden && <span className="badge">{p.overridden[key] ? 'Overridden' : 'From role'}</span>}
      </div>
      <FocusGroup focusKey={`${p.scope}:${key}`} className="flex gap-2 flex-wrap items-center" role="group" label={title}>
        {chips}
        {p.overridden?.[key] && p.onReset && <Button focusKey={`${p.scope}:${key}:reset`} ghost label={`Reset ${title.toLowerCase()} to the role`} onEnter={() => p.onReset!(key)}>Reset</Button>}
      </FocusGroup>
    </section>
  )
  const chip = <K extends keyof Limits>(key: K, value: Limits[K], text: string) => (
    <Chip key={String(value)} focusKey={`${p.scope}:${key}:${value ?? 'none'}`} pressed={p.limits[key] === value} onEnter={() => p.onChange?.({ [key]: value } as Partial<Limits>)}>{text}</Chip>
  )
  // A value set elsewhere (say 5 streams) still needs a chip to show as the current one.
  const streams = p.limits.max_streams && !STREAM_CHOICES.includes(p.limits.max_streams) ? [...STREAM_CHOICES, p.limits.max_streams].sort((a, b) => a - b) : STREAM_CHOICES
  return (
    <div className="flex flex-col gap-4">
      {row('max_streams', 'Streams at once', [chip('max_streams', null, 'No limit'), ...streams.map((n) => chip('max_streams', n, String(n)))])}
      {row('rating_ceiling_movie', 'Movies up to', [chip('rating_ceiling_movie', null, 'Any rating'), ...MOVIE_RATINGS.map((r) => chip('rating_ceiling_movie', r, r))])}
      {row('rating_ceiling_tv', 'TV up to', [chip('rating_ceiling_tv', null, 'Any rating'), ...TV_RATINGS.map((r) => chip('rating_ceiling_tv', r, r))])}
      {row('allow_unrated', 'Titles without a rating', [chip('allow_unrated', true, 'Shown'), chip('allow_unrated', false, 'Hidden')])}
    </div>
  )
}
