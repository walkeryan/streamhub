import { useState, type KeyboardEvent, type ReactNode } from 'react'
import type { Tile } from '../api/types'
import { useMediaActions } from '../api/mediaActions'
import { OverflowMenu, type OverflowItem } from '../ds/OverflowMenu'
import { PHASE_LABEL, tileRequest } from '../lib/requests'
import { setFocus } from '../nav/spatial'
import { Button, Modal } from './ui'

/** The corner button is inside the poster bounds, so geometric navigation skips it. */
export function CardActions({ className, cardKey, menuKey, children }: {
  className: string; cardKey: string; menuKey: string; children: ReactNode
}) {
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = (event.target as HTMLElement).closest('[data-focus-key]')?.getAttribute('data-focus-key')
    const next = current === cardKey && event.key === 'ArrowUp' ? menuKey
      : current === menuKey && event.key === 'ArrowDown' ? cardKey : null
    if (!next) return
    event.preventDefault()
    event.stopPropagation()
    void setFocus(next)
  }
  return <div className={className} onKeyDownCapture={move}>{children}</div>
}

/** One menu beside one real content link. Media state loads only when the viewer opens this menu. */
export function MediaCardActions({ tile, className, cardKey, menuKey, items = [], children }: {
  tile: Tile; className?: string; cardKey: string; menuKey: string; items?: OverflowItem[]; children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const actions = useMediaActions(tile, open)
  const request = tileRequest(tile)
  const mediaItems: OverflowItem[] = actions.ready ? [
    { key: 'save', icon: 'bookmark', label: actions.saved ? 'Remove from My List' : 'Add to My List', pressed: actions.saved,
      keepOpen: true, disabled: actions.busy, onEnter: () => void actions.setSaved(!actions.saved) },
    { key: 'rate-up', icon: 'thumbUp' as const, label: actions.vote === 'up' ? 'Remove thumbs up' : 'Thumbs up', pressed: actions.vote === 'up',
      keepOpen: true, disabled: actions.busy, group: 1, onEnter: () => void actions.rate(actions.vote === 'up' ? null : 'up') },
    { key: 'rate-down', icon: 'thumbDown' as const, label: actions.vote === 'down' ? 'Remove thumbs down' : 'Thumbs down', pressed: actions.vote === 'down',
      keepOpen: true, disabled: actions.busy, group: 1, onEnter: () => void actions.rate(actions.vote === 'down' ? null : 'down') },
    ...(actions.canRemove ? [{ key: 'remove-library', icon: 'trash' as const, label: 'Remove from library', group: 2, disabled: actions.busy, onEnter: () => setConfirmRemove(true) }] : []),
  ] : actions.error
    ? [{ key: 'retry-actions', icon: 'refresh', label: 'Retry loading actions', keepOpen: true, onEnter: actions.retry }]
    : [{ key: 'loading', icon: 'refresh', label: 'Loading actions…', disabled: true, onEnter: () => undefined }]
  const remove = async () => {
    await actions.remove()
    setConfirmRemove(false)
  }
  const available = tile.sources.some(source => source.playable_in_app)
  const status = actions.busy || (open && !actions.ready && !actions.error) ? 'busy'
    : open && actions.error ? 'error' : request ? 'pending'
      : available || actions.saved || actions.vote ? 'active' : undefined
  const statusWords = [
    request && PHASE_LABEL[request.phase], available && 'Available here',
    actions.saved && 'On My List', actions.vote === 'up' && 'Liked', actions.vote === 'down' && 'Disliked',
  ].filter(Boolean).join(' · ')
  return <>
    <CardActions className={`media-card ${className ?? ''}`} cardKey={cardKey} menuKey={`${menuKey}:button`}>
      {children}
      <div className="media-card-menu">
        <OverflowMenu focusKey={`${menuKey}:button`} menuKey={menuKey} title={tile.title} label={`More actions for ${tile.title}`} tip={statusWords || 'More'} icon="menu" status={status} size="sm"
          testId="media-card-actions" items={[...items, ...mediaItems]} onOpenChange={setOpen} />
      </div>
    </CardActions>
    <Modal open={confirmRemove} onClose={() => setConfirmRemove(false)} title={tile.title}
      firstFocusKey={`${menuKey}:remove-cancel`} variant="sheet" testId="remove-library-confirm">
      <p>Remove from the household library?</p>
      <div className="lib-actions">
        <Button focusKey={`${menuKey}:remove-cancel`} ghost disabled={actions.busy} onEnter={() => setConfirmRemove(false)}>Cancel</Button>
        <Button focusKey={`${menuKey}:remove-confirm`} danger disabled={actions.busy} onEnter={() => void remove()}>Remove</Button>
      </div>
    </Modal>
  </>
}
