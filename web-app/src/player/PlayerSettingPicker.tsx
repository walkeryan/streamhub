import { useEffect } from 'react'
import { Icon } from '../components/Icon'
import { Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'

export type PlayerSetting = 'speed' | 'quality' | 'aspect' | 'sleep'

type Value = string | number | null
type Choice<T> = { value: T; label: string; detail?: string | null }

export function PlayerSettingPicker<T extends Value>({ id, title, value, choices, open, onOpen, onChange, note }: {
  id: PlayerSetting
  title: string
  value: T
  choices: readonly Choice<T>[]
  open: PlayerSetting | null
  onOpen: (setting: PlayerSetting | null) => void
  onChange: (value: T, label: string) => void
  note?: string
}) {
  const expanded = open === id
  const rowKey = `pm:${id}`
  const selected = choices.find(choice => choice.value === value)
  useEffect(() => {
    if (!expanded) return
    const frame = requestAnimationFrame(() => void setFocus(`${rowKey}:${value}`))
    return () => cancelAnimationFrame(frame)
  }, [expanded, rowKey, value])

  return (
    <div className="pm-picker">
      <Focusable focusKey={rowKey} className="pm-row" data-testid={`setting-${id}`}
        label={`${title}, ${selected?.label ?? ''}`} aria={{ expanded, controls: expanded ? `pm-options-${id}` : undefined }}
        onEnter={() => onOpen(expanded ? null : id)}>
        <span>{title}</span><span className="pm-value">{selected?.label}</span>
        <Icon name={expanded ? 'up' : 'chevD'} size={16} />
      </Focusable>
      {expanded && <div className="pm-choices" role="group" aria-label={title} id={`pm-options-${id}`}>
        {choices.map(choice => (
          <Focusable key={String(choice.value)} focusKey={`${rowKey}:${choice.value}`} className="pm-choice"
            aria-pressed={choice.value === value} data-testid={`player-${id}`} onEnter={() => {
              void setFocus(rowKey)
              onOpen(null)
              onChange(choice.value, choice.label)
            }}>
            <span className="pm-choice-label">{choice.label}</span>{choice.detail && <small>{choice.detail}</small>}
            {choice.value === value && <Icon name="check" size={16} />}
          </Focusable>
        ))}
        {note && <p className="pm-note">{note}</p>}
      </div>}
    </div>
  )
}
