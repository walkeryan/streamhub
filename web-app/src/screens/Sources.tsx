import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import QRCode from 'qrcode'
import { z } from 'zod'
import { api, ApiError, unwrap } from '../api/client'
import { qk, useCreateSource, useDeleteSource, useDisclaimer, useServiceRegistry, useSources } from '../api/hooks'
import type { PlexPin, Source, SourceCreate } from '../api/types'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { Icon } from '../components/Icon'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { DisclaimerGate } from './Gate'

export const SERVICES: Array<{ key: string; name: string; web: string }> = [
  { key: 'netflix', name: 'Netflix', web: 'https://www.netflix.com/' },
  { key: 'hulu', name: 'Hulu', web: 'https://www.hulu.com/' },
  { key: 'disney', name: 'Disney+', web: 'https://www.disneyplus.com/' },
  { key: 'prime', name: 'Prime Video', web: 'https://www.primevideo.com/' },
  { key: 'max', name: 'Max', web: 'https://www.max.com/' },
  { key: 'appletv', name: 'Apple TV+', web: 'https://tv.apple.com/' },
  { key: 'peacock', name: 'Peacock', web: 'https://www.peacocktv.com/' },
  { key: 'paramount', name: 'Paramount+', web: 'https://www.paramountplus.com/' },
  { key: 'youtube', name: 'YouTube', web: 'https://www.youtube.com/' },
  { key: 'tubi', name: 'Tubi', web: 'https://tubitv.com/' },
  { key: 'pluto', name: 'Pluto TV', web: 'https://pluto.tv/' },
]

/** Credentials never appear in the UI: strip userinfo and sensitive query values. */
export function redactUrl(u: string): string {
  try {
    const url = new URL(u)
    url.username = ''; url.password = ''
    for (const k of [...url.searchParams.keys()]) if (/user|pass|token|key|secret|auth/i.test(k)) url.searchParams.set(k, '•••')
    return url.toString()
  } catch { return u.replace(/\/\/[^@/]*@/, '//') }
}

const httpUrl = z.string().trim().url('Enter a full address starting with http:// or https://').refine((v) => /^https?:\/\//i.test(v), 'Address must start with http:// or https://')
const schemas = {
  jellyfin: z.object({ name: z.string().trim().min(1, 'Give the source a name'), server_url: httpUrl, username: z.string().trim().min(1, 'Enter a username'), password: z.string().min(1, 'Enter a password') }),
  xtream: z.object({ name: z.string().trim().min(1, 'Give the source a name'), server_url: httpUrl, username: z.string().trim().min(1, 'Enter a username'), password: z.string().min(1, 'Enter a password'), output: z.enum(['ts', 'm3u8']) }),
  m3u: z.object({ name: z.string().trim().min(1, 'Give the source a name'), url: z.string().trim().optional(), file: z.string().optional(), epg_url: z.string().trim().optional(), refresh_hours: z.coerce.number().int().min(0).max(168) })
    .refine((v) => !!v.url || !!v.file, { message: 'Add a playlist address or choose a file', path: ['url'] })
    .refine((v) => !v.url || /^https?:\/\//i.test(v.url), { message: 'Address must start with http:// or https://', path: ['url'] })
    .refine((v) => !v.epg_url || /^https?:\/\//i.test(v.epg_url), { message: 'Address must start with http:// or https://', path: ['epg_url'] }),
}

type Kind = 'plex' | 'jellyfin' | 'xtream' | 'm3u'

export function Sources() {
  const { data, error, isLoading, refetch } = useSources()
  const registry = useServiceRegistry()
  const create = useCreateSource()
  const del = useDeleteSource()
  const toast = useUi((s) => s.toast)
  const qc = useQueryClient()
  const [adding, setAdding] = useState<Kind | null>(null)
  const [removing, setRemoving] = useState<Source | null>(null)
  const [gate, setGate] = useState<null | (() => void)>(null)
  const profile = useSession((s) => s.profile)
  const setProfile = useSession((s) => s.setProfile)
  const disclaimer = useDisclaimer()
  useRestoreFocus(!!data, 'add:plex')

  const accepted = !!profile && !!disclaimer.data && profile.disclaimer_accepted_version === disclaimer.data.version
  const needsGate = (k: Kind) => (k === 'xtream' || k === 'm3u') && !accepted
  const openAdd = (k: Kind) => (needsGate(k) ? setGate(() => () => setAdding(k)) : setAdding(k))

  const svcSources = (data ?? []).filter((s) => s.type === 'streaming_service')
  const libs = (data ?? []).filter((s) => s.type !== 'streaming_service')
  const webUrl = (key: string) => registry.data?.find((r) => r.key === key)?.web_url ?? SERVICES.find((s) => s.key === key)?.web

  const toggleService = (svc: { key: string; name: string }) => {
    const existing = svcSources.find((s) => s.service_key === svc.key)
    if (existing) del.mutate(existing.id, { onError: (e) => toast(e.message, 'error') })
    else create.mutate({ type: 'streaming_service', name: svc.name, service_key: svc.key, config: {} }, { onError: (e) => toast(e.message, 'error') })
  }
  const sync = async (s: Source) => {
    try { await api.POST('/sources/{id}/sync', { params: { path: { id: s.id } } }); toast(`Syncing ${s.name}…`); void qc.invalidateQueries({ queryKey: qk.sources }) } catch { toast('Could not start the sync.', 'error') }
  }
  const test = async (s: Source) => {
    try { const r = await unwrap<{ ok?: boolean; message?: string }>(api.POST('/sources/{id}/test', { params: { path: { id: s.id } } })); toast(r.message ?? (r.ok ? 'Connection works.' : 'Connection failed.'), r.ok ? 'success' : 'error') }
    catch (e) { toast(e instanceof Error ? e.message : 'Test failed.', 'error') }
  }
  const afterAccepted = () => {
    if (profile && disclaimer.data) setProfile({ ...profile, disclaimer_accepted_version: disclaimer.data.version })
    const next = gate
    setGate(null)
    next?.()
  }

  return (
    <div className="page-pad flex flex-col gap-8" data-testid="sources">
      <h1 className="display">Sources</h1>
      <section aria-label="Connected sources" className="flex flex-col gap-3">
        <h2 className="heading">Connected</h2>
        {error ? <ErrorState error={error} onRetry={() => void refetch()} /> : isLoading ? <Skeleton style={{ height: '5rem' }} /> : libs.length === 0 ? (
          <Empty title="No sources connected" hint="Add Plex, Jellyfin, an IPTV provider or a playlist below. StreamHub only plays what you connect." />
        ) : (
          <FocusGroup focusKey="src:list" className="flex flex-col gap-3">
            {libs.map((s) => (
              <FocusGroup key={s.id} focusKey={`src:${s.id}`} className="panel flex items-center gap-4 flex-wrap" data-testid="source-card">
                <div className="flex-1 min-w-0">
                  <div className="heading truncate">{s.name}</div>
                  <div className="label">{s.type} · <span data-status={s.status}>{s.status === 'ok' ? 'Connected' : s.status === 'syncing' ? 'Syncing' : s.status === 'unauthenticated' ? 'Sign in again' : 'Error'}</span>
                    {s.counts && Object.keys(s.counts).length > 0 && ` · ${Object.entries(s.counts).map(([k, v]) => `${v} ${k}`).join(', ')}`}
                    {typeof s.config_summary?.server_url === 'string' && ` · ${redactUrl(s.config_summary.server_url)}`}
                  </div>
                </div>
                <Button focusKey={`src:${s.id}:sync`} icon="refresh" onEnter={() => void sync(s)}>Sync</Button>
                <Button focusKey={`src:${s.id}:test`} icon="check" onEnter={() => void test(s)}>Test</Button>
                <Button focusKey={`src:${s.id}:rm`} danger icon="trash" onEnter={() => setRemoving(s)}>Remove</Button>
              </FocusGroup>
            ))}
          </FocusGroup>
        )}
      </section>

      <section aria-label="Add a source" className="flex flex-col gap-3">
        <h2 className="heading">Add a source</h2>
        <FocusGroup focusKey="src:add" className="flex gap-4 flex-wrap">
          {([['plex', 'Plex', 'Link with a PIN or QR code'], ['jellyfin', 'Jellyfin', 'Server address and sign-in'], ['xtream', 'Xtream Codes', 'IPTV provider login'], ['m3u', 'M3U playlist', 'Playlist address or file']] as const).map(([k, name, hint]) => (
            <Focusable key={k} focusKey={`add:${k}`} className="panel flex flex-col gap-1" style={{ width: '14rem' }} data-testid={`add-${k}`} onEnter={() => openAdd(k)} label={`Add ${name}`}>
              <Icon name={k === 'm3u' ? 'list' : k === 'plex' ? 'link' : 'source'} />
              <span className="heading">{name}</span><span className="label">{hint}</span>
            </Focusable>
          ))}
        </FocusGroup>
      </section>

      <section aria-label="Streaming services" className="flex flex-col gap-3">
        <h2 className="heading">Streaming services</h2>
        <p className="muted" style={{ maxWidth: '44rem' }}>Turn on the services you subscribe to. Titles you can watch there are labelled “Open in app” and open the service for you.</p>
        <FocusGroup focusKey="src:svc" className="flex gap-3 flex-wrap">
          {SERVICES.map((svc) => {
            const on = svcSources.some((s) => s.service_key === svc.key)
            return (
              <FocusGroup key={svc.key} focusKey={`svc:${svc.key}`} className="panel flex flex-col gap-2" style={{ width: '13rem', padding: '0.75rem' }}>
                <span className="heading">{svc.name}</span>
                <div className="flex gap-2 flex-wrap">
                  <Focusable focusKey={`svc:${svc.key}:toggle`} className="chip fx-flat" aria-pressed={on} role="switch" data-testid={`svc-${svc.key}`} label={`${svc.name} ${on ? 'on' : 'off'}`} onEnter={() => toggleService(svc)}>{on ? 'On' : 'Off'}</Focusable>
                  <Focusable focusKey={`svc:${svc.key}:test`} className="chip fx-flat" label={`Test link for ${svc.name}`} onEnter={() => { const u = webUrl(svc.key); if (u) window.open(u, '_blank', 'noopener') }}>Test link</Focusable>
                </div>
              </FocusGroup>
            )
          })}
        </FocusGroup>
      </section>

      <Modal open={adding === 'plex'} onClose={() => setAdding(null)} title="Link Plex" firstFocusKey="plex:close"><PlexLink onDone={() => setAdding(null)} /></Modal>
      <Modal open={adding === 'jellyfin' || adding === 'xtream' || adding === 'm3u'} onClose={() => setAdding(null)} title={adding === 'jellyfin' ? 'Add Jellyfin' : adding === 'xtream' ? 'Add Xtream Codes' : 'Add M3U playlist'} firstFocusKey="f:name" wide>
        {(adding === 'jellyfin' || adding === 'xtream' || adding === 'm3u') && (
          <SourceForm key={adding} kind={adding} onClose={() => setAdding(null)} onNeedGate={() => setGate(() => () => setAdding(adding))}
            submit={async (body) => { await create.mutateAsync(body) }} />
        )}
      </Modal>
      <Modal open={!!removing} onClose={() => setRemoving(null)} title="Remove this source?" firstFocusKey="rm:cancel">
        <p>{removing?.name} and its synced library will be removed from StreamHub. Your files and accounts are not touched.</p>
        <div className="flex gap-3">
          <Button focusKey="rm:cancel" onEnter={() => setRemoving(null)}>Keep source</Button>
          <Button focusKey="rm:ok" danger icon="trash" onEnter={() => { if (removing) del.mutate(removing.id, { onSuccess: () => toast(`Removed ${removing.name}.`, 'success'), onError: (e) => toast(e.message, 'error') }); setRemoving(null) }}>Remove</Button>
        </div>
      </Modal>
      {gate && <DisclaimerGate overlay onAccepted={afterAccepted} onExit={() => setGate(null)} />}
    </div>
  )
}

function PlexLink({ onDone }: { onDone: () => void }) {
  const [pin, setPin] = useState<PlexPin | null>(null)
  const [qr, setQr] = useState<string>('')
  const [err, setErr] = useState<string | null>(null)
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  useEffect(() => {
    let live = true
    let timer = 0
    ;(async () => {
      try {
        const p = await unwrap<PlexPin>(api.POST('/sources/plex/pin'))
        if (!live) return
        setPin(p)
        setQr(await QRCode.toDataURL(p.link_url, { margin: 1, width: 240 }))
        const poll = async () => {
          if (!live) return
          const { response } = await api.GET('/sources/plex/pin/{pin_id}', { params: { path: { pin_id: p.pin_id } } })
          if (!live) return
          if (response.status === 200) { toast('Plex linked. Your libraries are syncing.', 'success'); void qc.invalidateQueries({ queryKey: qk.sources }); onDone(); return }
          if (response.status !== 428) { setErr('Plex linking failed. Close this and try again.'); return }
          timer = window.setTimeout(() => void poll(), 2000)
        }
        timer = window.setTimeout(() => void poll(), 2000)
      } catch (e) { if (live) setErr(e instanceof Error ? e.message : 'Could not start Plex linking.') }
    })()
    return () => { live = false; window.clearTimeout(timer) }
  }, [qc, toast, onDone])
  return (
    <div className="flex flex-col gap-4 items-start">
      {err ? <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p> : !pin ? <p className="muted">Requesting a code…</p> : (
        <div className="flex gap-6 flex-wrap items-center">
          {qr && <img src={qr} alt="QR code to link Plex" width={160} height={160} style={{ borderRadius: 8, background: '#fff', padding: 4 }} />}
          <div className="flex flex-col gap-2">
            <p>Go to <strong>plex.tv/link</strong> and enter this code, or scan the QR code.</p>
            <p className="display" style={{ letterSpacing: '0.2em' }} data-testid="plex-code">{pin.code}</p>
            <p className="label" aria-live="polite">Waiting for you to approve it in Plex…</p>
          </div>
        </div>
      )}
      <Button focusKey="plex:close" onEnter={onDone}>Cancel</Button>
    </div>
  )
}

function SourceForm({ kind, onClose, submit, onNeedGate }: { kind: 'jellyfin' | 'xtream' | 'm3u'; onClose: () => void; submit: (b: SourceCreate) => Promise<void>; onNeedGate: () => void }) {
  const [v, setV] = useState<Record<string, string>>({ name: '', server_url: '', username: '', password: '', output: 'ts', url: '', epg_url: '', refresh_hours: '12', file: '', fileName: '' })
  const [errs, setErrs] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [formErr, setFormErr] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const toast = useUi((s) => s.toast)
  const set = (k: string) => (val: string) => setV((o) => ({ ...o, [k]: val }))

  const go = async () => {
    setFormErr(null)
    const r = schemas[kind].safeParse(v)
    if (!r.success) {
      setErrs(Object.fromEntries(r.error.issues.map((i) => [String(i.path[0]), i.message])))
      return
    }
    setErrs({})
    const d = r.data as Record<string, unknown>
    const { name, ...config } = d
    if (kind === 'm3u') { Object.keys(config).forEach((k) => (config[k] === '' || config[k] === undefined) && delete config[k]) }
    const body: SourceCreate = { type: kind, name: name as string, config: config as Record<string, unknown> }
    setBusy(true)
    try { await submit(body); toast(`${body.name} added. Syncing…`, 'success'); onClose() }
    catch (e) {
      if (e instanceof ApiError && e.status === 412) { onClose(); onNeedGate() }
      else setFormErr(e instanceof Error ? e.message : 'Could not add the source.')
    } finally { setBusy(false) }
  }
  const field = (k: string, label: string, opts: { type?: string; placeholder?: string; first?: boolean } = {}) => (
    <div className="flex flex-col gap-1">
      <label className="label" htmlFor={`f-${k}`}>{label}</label>
      <FocusableInput focusKey={opts.first ? 'f:name' : `f:${k}`} label={label} type={opts.type} placeholder={opts.placeholder} value={v[k] ?? ''} onChange={set(k)} onSubmit={() => void go()} />
      {errs[k] && <span role="alert" className="label" style={{ color: 'var(--t-danger)' }}>{errs[k]}</span>}
    </div>
  )
  const onFile = async (f: File | undefined) => {
    if (!f) return
    if (f.size > 20 * 1024 * 1024) { toast('That playlist file is larger than 20 MB.', 'error'); return }
    const text = await f.text()
    setV((o) => ({ ...o, file: text, fileName: f.name }))
  }
  return (
    <div className="flex flex-col gap-3">
      {field('name', 'Name', { first: true, placeholder: kind === 'jellyfin' ? 'Living room Jellyfin' : kind === 'xtream' ? 'My IPTV provider' : 'My playlist' })}
      {kind !== 'm3u' && field('server_url', 'Server address', { placeholder: 'http://192.168.1.10:8096' })}
      {kind !== 'm3u' && field('username', 'Username')}
      {kind !== 'm3u' && field('password', 'Password', { type: 'password' })}
      {kind === 'xtream' && (
        <div className="flex flex-col gap-1"><span className="label">Stream format</span>
          <div className="flex gap-2"><Chip focusKey="f:out:ts" pressed={v.output === 'ts'} onEnter={() => set('output')('ts')}>MPEG-TS</Chip><Chip focusKey="f:out:m3u8" pressed={v.output === 'm3u8'} onEnter={() => set('output')('m3u8')}>HLS (m3u8)</Chip></div>
        </div>
      )}
      {kind === 'm3u' && (
        <>
          {field('url', 'Playlist address', { placeholder: 'https://example.com/playlist.m3u' })}
          <div className="flex items-center gap-3">
            <Button focusKey="f:file" icon="upload" onEnter={() => fileInput.current?.click()}>Choose a file</Button>
            <span className="label">{v.fileName || 'or upload a .m3u / .m3u8 file'}</span>
            <input ref={fileInput} type="file" accept=".m3u,.m3u8,audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain" hidden onChange={(e) => void onFile(e.target.files?.[0])} data-testid="m3u-file" />
          </div>
          {field('epg_url', 'Guide (XMLTV) address, optional')}
          {field('refresh_hours', 'Refresh every (hours, 0 = never)')}
        </>
      )}
      {formErr && <p role="alert" style={{ color: 'var(--t-danger)' }}>{formErr}</p>}
      <div className="flex gap-3 mt-1">
        <Button focusKey="f:save" primary disabled={busy} onEnter={() => void go()}>{busy ? 'Adding…' : 'Add source'}</Button>
        <Button focusKey="f:cancel" onEnter={onClose}>Cancel</Button>
      </div>
    </div>
  )
}
