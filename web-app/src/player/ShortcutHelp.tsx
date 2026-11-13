import { Button, Modal } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { SHORTCUT_HELP } from './shortcuts'

/** The keyboard shortcuts, as the key handler has them (shortcuts.ts). A live channel and a film differ. */
export function ShortcutHelp({ open, live, onClose }: { open: boolean; live: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Keyboard shortcuts" firstFocusKey="keys:close" wide testId="player-shortcuts">
      <div className="keys">
        {SHORTCUT_HELP.map((group) => (
          <section key={group.title}>
            <h3 className="label">{group.title}</h3>
            {group.lines.filter((line) => !line.only || (line.only === 'live') === live).map((line) => (
              <div key={line.does} className="keys-line">
                <span>{line.keys.map((key) => (key === '…' ? <span key={key} aria-hidden="true">…</span> : <kbd key={key} className="kbd">{key}</kbd>))}</span>
                <span>{line.does}</span>
              </div>
            ))}
          </section>
        ))}
      </div>
      <p className="muted label">A remote needs none of these: the arrows, OK and Back reach every control.</p>
      <FocusGroup focusKey="keys" className="flex gap-3">
        <Button focusKey="keys:close" primary onEnter={onClose}>Close</Button>
      </FocusGroup>
    </Modal>
  )
}
