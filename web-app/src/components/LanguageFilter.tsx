import { useLanguages } from '../api/hooks'
import { useSession } from '../store/session'
import { LANG_NAMES } from '../lib/util'
import { Chip } from './ui'
import { CatalogPicker } from './CatalogPicker'
import type { LanguageCount } from '../api/types'

export function LanguageFilter({ scope, value, onChange, languages }: {
  scope: string; value: string; onChange: (value: string) => void; languages?: LanguageCount[]
}) {
  const all = useLanguages()
  const preferred = useSession((s) => s.profile?.languages)
  const list = languages ?? all.data ?? []
  const options = list.map((l) => ({ id: l.code, label: LANG_NAMES[l.code] ?? l.name, count: l.count }))
  return <div className="flex gap-2 flex-wrap" role="group" aria-label="Language">
    {(preferred?.length ?? 0) > 0 && <Chip focusKey={`${scope}:lang:mine`} pressed={value === 'mine'} onEnter={() => onChange('mine')}>My languages</Chip>}
    <Chip focusKey={`${scope}:lang:eng`} pressed={value === 'eng'} onEnter={() => onChange('eng')}>English</Chip>
    <Chip focusKey={`${scope}:lang:all`} pressed={value === 'all'} onEnter={() => onChange('all')}>All languages</Chip>
    <CatalogPicker scope={`${scope}:language`} label="Other languages" value={value} options={options} onChange={onChange} />
  </div>
}
