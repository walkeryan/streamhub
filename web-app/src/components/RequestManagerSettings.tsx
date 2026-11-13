import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import { qk, useRequestManager } from '../api/hooks'
import type { RequestManagerSettings as Stored } from '../api/types'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useUi } from '../store/ui'
import { Button } from './ui'

/** Admin only: where approved requests are sent. The API key is write-only; the server never returns it. */
export function RequestManagerSettings({ configured }: { configured: boolean }) {
  const stored = useRequestManager(true)
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  // null = untouched, so the saved URL shows until the admin types
  const [url, setUrl] = useState<string | null>(null)
  const [key, setKey] = useState('')
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const shownUrl = url ?? stored.data?.url ?? ''

  const refresh = () => {
    for (const queryKey of [qk.settings, ['request-manager'], ['title'], ['search'], ['discover'], qk.home]) void qc.invalidateQueries({ queryKey })
  }
  const save = async (next: { url: string | null; api_key?: string }) => {
    setBusy(true)
    setResult(null)
    try {
      const saved = await unwrap<Stored>(api.PUT('/settings/request_manager', { body: next }))
      setUrl(null)
      setKey('')
      refresh()
      toast(saved.url ? (saved.api_key_set ? 'Request manager saved.' : 'Saved. Enter the API key for this server.') : 'Request manager removed.', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : 'Could not save.', 'error') } finally { setBusy(false) }
  }
  const test = async () => {
    setBusy(true)
    setResult(null)
    try { setResult(await unwrap<{ ok: boolean; message: string }>(api.POST('/settings/request_manager/test'))) }
    catch (e) { setResult({ ok: false, message: e instanceof Error ? e.message : 'Could not run the test.' }) } finally { setBusy(false) }
  }

  return (
    <section className="flex flex-col gap-2" data-testid="request-manager">
      <h3 className="label">Request manager {configured ? '(connected)' : stored.data?.url ? '(API key missing)' : '(not set)'}</h3>
      <p className="muted">Approved requests are sent to a Seerr-compatible server (Overseerr, Jellyseerr or Seerr). StreamHub keeps the approval.</p>
      <FocusGroup focusKey="reqmgr" className="flex gap-3 flex-wrap items-center">
        <div style={{ width: '22rem', maxWidth: '100%' }}><FocusableInput focusKey="reqmgr:url" label="Request manager URL" inputMode="url" placeholder="http://requests.example.test:5055" value={shownUrl} onChange={setUrl} /></div>
        <div style={{ width: '18rem', maxWidth: '100%' }}><FocusableInput focusKey="reqmgr:key" label="Request manager API key" type="password" placeholder={stored.data?.api_key_set ? 'Saved. Paste a key to replace it' : 'API key'} value={key} onChange={setKey} /></div>
        <Button focusKey="reqmgr:save" primary disabled={busy || !shownUrl.trim() || (url === null && !key.trim())} onEnter={() => void save({ url: shownUrl.trim(), ...(key.trim() ? { api_key: key.trim() } : {}) })}>Save</Button>
        <Button focusKey="reqmgr:test" disabled={busy || !configured} onEnter={() => void test()} testId="request-manager-test">Test connection</Button>
        {stored.data?.url && <Button focusKey="reqmgr:remove" danger disabled={busy} onEnter={() => void save({ url: null })}>Remove</Button>}
      </FocusGroup>
      {result && <p role="status" data-testid="request-manager-result" style={{ color: result.ok ? undefined : 'var(--t-danger)' }}>{result.message}</p>}
    </section>
  )
}
