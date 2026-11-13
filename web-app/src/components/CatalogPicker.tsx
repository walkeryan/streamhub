import { useMemo, useState } from 'react'
import { Button, Modal } from './ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'

export interface CatalogOption { id: string; label: string; count?: number }
const PAGE_SIZE = 40

/** Search and page metadata instead of mounting hundreds of category controls. */
export function CatalogPicker({ scope, label, value, options, selectedLabel, onChange, search = true, testId, className }: {
  scope: string; label: string; value: string; options: CatalogOption[]; selectedLabel?: string; onChange: (id: string) => void
  /** false for a short, ordered list (seasons): no search field, and the list opens on the current choice. */
  search?: boolean; testId?: string
  /** For the button that opens the list, where it sits among controls of another size. */
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const matches = useMemo(() => options.filter((o) => o.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [options, query])
  const pageIndex = Math.min(page, Math.max(0, Math.ceil(matches.length / PAGE_SIZE) - 1))
  const selected = options.find((o) => o.id === value)
  // Scoped facets may omit an active choice when another filter has no matches.
  // Callers may supply a display name; opaque source IDs must never become labels.
  const currentLabel = selected?.label ?? selectedLabel ?? 'Choose'
  const close = () => setOpen(false)
  return <>
    <Button focusKey={`${scope}:pick`} testId={testId} className={className} icon={search ? undefined : 'down'}
      onEnter={() => { setQuery(''); setPage(search || !selected ? 0 : Math.floor(options.indexOf(selected) / PAGE_SIZE)); setOpen(true) }} label={`${label}: ${currentLabel}`}>
      {search ? `${label}: ${currentLabel}` : currentLabel}
    </Button>
    <Modal open={open} onClose={close} title={label} firstFocusKey={search || !selected ? `${scope}:search` : `${scope}:option:${selected.id}`}>
      {search && <FocusableInput focusKey={`${scope}:search`} label={`Search ${label.toLowerCase()}`} placeholder={`Search ${options.length.toLocaleString()} options`} value={query} onChange={(v) => { setQuery(v); setPage(0) }} />}
      {(search || matches.length > PAGE_SIZE) && <p className="label" aria-live="polite">{matches.length.toLocaleString()} matches · Page {pageIndex + 1} of {Math.max(1, Math.ceil(matches.length / PAGE_SIZE))}</p>}
      <FocusGroup focusKey={`${scope}:options`} className="flex flex-col gap-2" role="group" label={label}>
        {matches.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE).map((o) => <Button key={o.id} focusKey={`${scope}:option:${o.id}`} pressed={o.id === value} onEnter={() => { onChange(o.id); close() }}>
          {o.label}{o.count !== undefined ? ` (${o.count.toLocaleString()})` : ''}
        </Button>)}
        {!matches.length && <p className="muted">No matching options. Try a shorter name.</p>}
      </FocusGroup>
      <div className="flex gap-2 flex-wrap">
        {matches.length > PAGE_SIZE && <Button focusKey={`${scope}:prev`} disabled={pageIndex === 0} onEnter={() => setPage(pageIndex - 1)}>Previous</Button>}
        {matches.length > PAGE_SIZE && <Button focusKey={`${scope}:next`} disabled={(pageIndex + 1) * PAGE_SIZE >= matches.length} onEnter={() => setPage(pageIndex + 1)}>Next</Button>}
        <Button focusKey={`${scope}:close`} onEnter={close}>Done</Button>
      </div>
    </Modal>
  </>
}
