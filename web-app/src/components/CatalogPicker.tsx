import { useMemo, useState } from 'react'
import { Button, Modal } from './ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'

export interface CatalogOption { id: string; label: string; count?: number }
const PAGE_SIZE = 40

/** Search and page metadata instead of mounting hundreds of category controls. */
export function CatalogPicker({ scope, label, value, options, onChange }: {
  scope: string; label: string; value: string; options: CatalogOption[]; onChange: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const matches = useMemo(() => options.filter((o) => o.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [options, query])
  const pageIndex = Math.min(page, Math.max(0, Math.ceil(matches.length / PAGE_SIZE) - 1))
  const selected = options.find((o) => o.id === value)
  const close = () => setOpen(false)
  return <>
    <Button focusKey={`${scope}:pick`} onEnter={() => { setQuery(''); setPage(0); setOpen(true) }} label={`${label}: ${selected?.label ?? 'Choose'}`}>
      {label}: {selected?.label ?? 'Choose'}
    </Button>
    <Modal open={open} onClose={close} title={label} firstFocusKey={`${scope}:search`}>
      <FocusableInput focusKey={`${scope}:search`} label={`Search ${label.toLowerCase()}`} placeholder={`Search ${options.length.toLocaleString()} options`} value={query} onChange={(v) => { setQuery(v); setPage(0) }} />
      <p className="label" aria-live="polite">{matches.length.toLocaleString()} matches · Page {pageIndex + 1} of {Math.max(1, Math.ceil(matches.length / PAGE_SIZE))}</p>
      <FocusGroup focusKey={`${scope}:options`} className="flex flex-col gap-2" role="group" label={label}>
        {matches.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE).map((o) => <Button key={o.id} focusKey={`${scope}:option:${o.id}`} pressed={o.id === value} onEnter={() => { onChange(o.id); close() }}>
          {o.label}{o.count !== undefined ? ` (${o.count.toLocaleString()})` : ''}
        </Button>)}
        {!matches.length && <p className="muted">No matching options. Try a shorter name.</p>}
      </FocusGroup>
      <div className="flex gap-2 flex-wrap">
        <Button focusKey={`${scope}:prev`} disabled={pageIndex === 0} onEnter={() => setPage(pageIndex - 1)}>Previous</Button>
        <Button focusKey={`${scope}:next`} disabled={(pageIndex + 1) * PAGE_SIZE >= matches.length} onEnter={() => setPage(pageIndex + 1)}>Next</Button>
        <Button focusKey={`${scope}:close`} onEnter={close}>Done</Button>
      </div>
    </Modal>
  </>
}
