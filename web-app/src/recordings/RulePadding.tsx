import { FocusableInput } from '../nav/Focusable'

export type Padding = { pad_before_s: number | null; pad_after_s: number | null }
export const automaticPadding: Padding = { pad_before_s: null, pad_after_s: null }
const time = (ms: number) => new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export function RulePadding({ value, update, example }: {
  value: Padding
  update: (next: Padding) => void
  example?: { start: string; stop: string; sport: boolean }
}) {
  const before = value.pad_before_s ?? 120
  const after = value.pad_after_s ?? (example?.sport ? 3600 : 300)
  return <fieldset className="rec-padding">
    <legend>Recording window for this rule</legend>
    <div className="lib-chips">{(['pad_before_s', 'pad_after_s'] as const).map(field => {
      const early = field === 'pad_before_s'
      const name = early ? 'Minutes before' : 'Minutes after'
      return <label key={field}><span className="label">{name}</span>
        <FocusableInput focusKey={`rec:padding:${field}`} label={name} inputMode="numeric" maxLength={3}
          placeholder={early ? 'Auto · 2' : example ? `Auto · ${example.sport ? 60 : 5}` : 'Auto'}
          value={value[field] == null ? '' : String(value[field] / 60)}
          onChange={raw => { if (/^\d{0,3}$/.test(raw) && Number(raw) <= 360) update({ ...value, [field]: raw === '' ? null : Number(raw) * 60 }) }} />
      </label>
    })}</div>
    <p className="label" role="status">{example
      ? `Captures ${time(Date.parse(example.start) - before * 1000)} to ${time(Date.parse(example.stop) + after * 1000)}.`
      : `Starts ${before / 60} min before and ends ${value.pad_after_s == null ? '5 min after (60 min for sports)' : `${after / 60} min after`} each programme.`}</p>
  </fieldset>
}
