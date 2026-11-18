import { FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useUi } from '../store/ui'
import { GlassIconButton } from '../ds/GlassIconButton'

/** A link shown once, with a button to copy it. Where copying is blocked the field takes focus so it can be selected. */
export function LinkToCopy({ scope, url, label }: { scope: string; url: string; label: string }) {
  const toast = useUi((s) => s.toast)
  const link = new URL(url, window.location.origin).href
  const copy = async () => {
    try { await navigator.clipboard.writeText(link); toast('Link copied.', 'success') } catch { toast('Copying is blocked here. Select the link and copy it.', 'error'); void setFocus(`${scope}:link`) }
  }
  return (
    <div className="invite-link">
      <FocusableInput focusKey={`${scope}:link`} label={label} value={link} onChange={() => undefined} readOnly />
      <GlassIconButton focusKey={`${scope}:copy`} icon="copy" label="Copy link" onEnter={() => void copy()} testId={`${scope}-copy`} />
    </div>
  )
}
