import { AndroidDownload } from '../components/AndroidDownload'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { qk, useChannelGroups, useDisclaimer, useHome, useLanguages, usePatchProfile, useProfiles, useSettings } from '../api/hooks'
import type { Profile } from '../api/types'
import { Button, Chip, ErrorState, Modal } from '../components/ui'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { setTheme } from '../lib/theme'
import { LANG_NAMES } from '../lib/util'
import { themes } from '../tokens'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

const TABS = [
  ['profiles', 'Profiles'], ['appearance', 'Appearance'], ['languages', 'Languages'], ['rows', 'Home rows'],
  ['parental', 'Parental'], ['playback', 'Playback'], ['about', 'About'],
] as const
type Tab = (typeof TABS)[number][0]

export function Settings() {
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.find((t) => t[0] === sp.get('tab'))?.[0] ?? 'profiles') as Tab
  useRestoreFocus(true, `set:tab:${tab}`)
  return (
    <div className="page-pad flex flex-col gap-6" data-testid="settings">
      <h1 className="display">Settings</h1>
      <FocusGroup focusKey="set:tabs" className="flex gap-2 flex-wrap" role="tablist">
        {TABS.map(([id, label]) => <Chip key={id} focusKey={`set:tab:${id}`} pressed={tab === id} onEnter={() => setSp({ tab: id }, { replace: true })} testId={`tab-${id}`}>{label}</Chip>)}
      </FocusGroup>
      <div style={{ maxWidth: '60rem' }}>
        {tab === 'profiles' && <ProfilesTab />}
        {tab === 'appearance' && <AppearanceTab />}
        {tab === 'languages' && <LanguagesTab />}
        {tab === 'rows' && <RowsTab />}
        {tab === 'parental' && <ParentalTab />}
        {tab === 'playback' && <PlaybackTab />}
        {tab === 'about' && <AboutTab />}
      </div>
    </div>
  )
}

function useCurrent(): Profile {
  return useSession((s) => s.profile)!
}
function useSaveProfile() {
  const patch = usePatchProfile()
  const setProfile = useSession((s) => s.setProfile)
  const profile = useCurrent()
  const toast = useUi((s) => s.toast)
  return (p: Partial<Profile>, msg?: string) =>
    patch.mutate({ id: profile.id, patch: { ...profile, ...p } }, {
      onSuccess: () => { setProfile({ ...profile, ...p }); if (msg) toast(msg, 'success') },
      onError: (e) => toast(e.message, 'error'),
    })
}

/* ------------------------------ profiles ------------------------------ */
function ProfilesTab() {
  const { data, error, refetch } = useProfiles()
  const me = useCurrent()
  const clear = useSession((s) => s.clear)
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  const [name, setName] = useState('')
  const [kids, setKids] = useState(false)
  const [pin, setPin] = useState('')
  const [pinErr, setPinErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Profile | null>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: qk.profiles })
  const addProfile = async () => {
    if (!name.trim()) return
    try {
      await ok(api.POST('/profiles', { body: { id: '', name: name.trim(), is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids } }))
      setName(''); setKids(false); refresh(); toast('Profile added.', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : 'Could not add the profile.', 'error') }
  }
  const savePin = async (value: string | null) => {
    if (value !== null && !/^\d{4,8}$/.test(value)) { setPinErr('Use 4 to 8 digits.'); return }
    setPinErr(null)
    try { await ok(api.PUT('/profiles/{id}/pin', { params: { path: { id: me.id } }, body: { pin: value } })); setPin(''); refresh(); toast(value ? 'PIN saved.' : 'PIN removed.', 'success') }
    catch (e) { toast(e instanceof Error ? e.message : 'Could not change the PIN.', 'error') }
  }
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="heading">Signed in as {me.name}</h2>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="prof:switch" icon="user" onEnter={() => { clear(); qc.clear() }} testId="switch-profile">Switch profile</Button>
        </div>
      </section>
      <section className="flex flex-col gap-3">
        <h2 className="heading">Profiles</h2>
        <FocusGroup focusKey="prof:list" className="flex flex-col gap-2">
          {(data ?? []).map((p) => (
            <div key={p.id} className="panel flex items-center gap-3">
              <span className="flex-1">{p.name}{p.id === me.id ? ' (you)' : ''}<span className="label"> {p.is_admin ? ' · Admin' : ''}{p.kids ? ' · Kids' : ''}{p.has_pin ? ' · PIN' : ''}</span></span>
              {me.is_admin && p.id !== me.id && !p.is_admin && <Button focusKey={`prof:del:${p.id}`} danger icon="trash" onEnter={() => setConfirm(p)}>Delete</Button>}
            </div>
          ))}
        </FocusGroup>
      </section>
      {me.is_admin && (
        <section className="flex flex-col gap-3">
          <h2 className="heading">Add a profile</h2>
          <div className="flex gap-3 flex-wrap items-center">
            <div style={{ width: '16rem' }}><FocusableInput focusKey="prof:name" label="Profile name" placeholder="Name" value={name} onChange={setName} onSubmit={() => void addProfile()} /></div>
            <Chip focusKey="prof:kids" pressed={kids} onEnter={() => setKids((k) => !k)}>Kids profile</Chip>
            <Button focusKey="prof:add" primary icon="plus" disabled={!name.trim()} onEnter={() => void addProfile()}>Add profile</Button>
          </div>
        </section>
      )}
      <section className="flex flex-col gap-3">
        <h2 className="heading">PIN for {me.name}</h2>
        <p className="muted">{me.has_pin ? 'A PIN is required to open this profile.' : 'Anyone can open this profile. Add a PIN to lock it.'}</p>
        <div className="flex gap-3 flex-wrap items-center">
          <div style={{ width: '12rem' }}><FocusableInput focusKey="prof:pin" label="New PIN" type="password" inputMode="numeric" maxLength={8} placeholder="4 to 8 digits" value={pin} onChange={setPin} onSubmit={() => void savePin(pin)} /></div>
          <Button focusKey="prof:pin-save" onEnter={() => void savePin(pin)}>Save PIN</Button>
          {me.has_pin && <Button focusKey="prof:pin-clear" danger onEnter={() => void savePin(null)}>Remove PIN</Button>}
        </div>
        {pinErr && <p role="alert" style={{ color: 'var(--t-danger)' }}>{pinErr}</p>}
      </section>
      <Modal open={!!confirm} onClose={() => setConfirm(null)} title={`Delete ${confirm?.name ?? ''}?`} firstFocusKey="del:cancel">
        <p>Their favorites, watchlist and resume positions are deleted too.</p>
        <div className="flex gap-3">
          <Button focusKey="del:cancel" onEnter={() => setConfirm(null)}>Keep profile</Button>
          <Button focusKey="del:ok" danger icon="trash" onEnter={async () => { if (confirm) { await ok(api.DELETE('/profiles/{id}', { params: { path: { id: confirm.id } } })); refresh() } setConfirm(null) }}>Delete</Button>
        </div>
      </Modal>
    </div>
  )
}

/* ------------------------------ appearance ------------------------------ */
function AppearanceTab() {
  const me = useCurrent()
  const save = useSaveProfile()
  const choose = (id: string, name: string) => { setTheme(id); save({ theme_id: id }, `Theme: ${name}`) }
  return (
    <div className="flex flex-col gap-4">
      <h2 className="heading">Theme</h2>
      <p className="muted">Themes apply immediately and are saved to {me.name}'s profile.</p>
      <FocusGroup focusKey="theme:list" className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(15rem, 1fr))', padding: '0.5rem' }}>
        {themes.map((t) => (
          <Focusable key={t.id} focusKey={`theme:${t.id}`} className="theme-card" role="radio" aria-pressed={me.theme_id === t.id} label={`${t.name} theme`} data-testid={`theme-${t.id}`} onEnter={() => choose(t.id, t.name)}>
            <div data-theme={t.id === 'adaptive' ? 'midnight' : t.id} style={{ background: 'var(--t-bg)', color: 'var(--t-text)', borderRadius: 'var(--radius-md)', padding: '0.625rem', display: 'flex', flexDirection: 'column', gap: 8, pointerEvents: 'none' }} aria-hidden="true">
              <div style={{ height: 44, borderRadius: 6, background: `linear-gradient(to top, var(--t-bg), transparent), linear-gradient(120deg, var(--t-surface-alt), var(--t-surface))`, display: 'flex', alignItems: 'flex-end', padding: 6 }}>
                <span style={{ background: t.id === 'adaptive' ? 'hsl(280 70% 60%)' : 'var(--t-accent)', color: 'var(--t-accent-text)', fontSize: 9, fontWeight: 700, padding: '1px 8px', borderRadius: 4 }}>Play</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {[0, 1, 2].map((i) => <i key={i} style={{ width: 28, height: 40, borderRadius: 4, background: 'var(--t-surface-alt)', outline: i === 1 ? '2px solid var(--t-focus-ring)' : 'none', transform: i === 1 ? 'scale(1.08)' : 'none', boxShadow: i === 1 ? '0 0 10px color-mix(in srgb, var(--t-focus-glow) 55%, transparent)' : 'none' }} />)}
              </div>
              <span style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>Muted text sample</span>
            </div>
            <div className="flex items-center justify-between mt-2"><span style={{ fontWeight: 600 }}>{t.name}</span>{me.theme_id === t.id && <span className="badge badge-play">Active</span>}</div>
            {t.adaptive && <span className="label">Accent follows the artwork in focus</span>}
          </Focusable>
        ))}
      </FocusGroup>
    </div>
  )
}

/* ------------------------------ languages ------------------------------ */
function LanguagesTab() {
  const me = useCurrent()
  const langs = useLanguages()
  const save = useSaveProfile()
  const toggle = (code: string) => save({ languages: me.languages.includes(code) ? me.languages.filter((c) => c !== code) : [...me.languages, code] })
  return (
    <div className="flex flex-col gap-3">
      <h2 className="heading">Preferred languages</h2>
      <p className="muted">Live TV, Guide, library browsing and Search default to these languages. Other languages remain available through All languages. Choose none to show every language.</p>
      {langs.error ? <ErrorState error={langs.error} onRetry={() => void langs.refetch()} /> : (
        <FocusGroup focusKey="lang:list" className="flex gap-2 flex-wrap">
          <Chip focusKey="lang:all" pressed={me.languages.length === 0} onEnter={() => save({ languages: [] })}>All languages</Chip>
          {(langs.data ?? []).map((l) => <Chip key={l.code} focusKey={`lang:${l.code}`} pressed={me.languages.includes(l.code)} onEnter={() => toggle(l.code)}>{LANG_NAMES[l.code] ?? l.name} · {l.count}</Chip>)}
        </FocusGroup>
      )}
    </div>
  )
}

/* ------------------------------ home rows ------------------------------ */
function RowsTab() {
  const home = useHome()
  const prefs = useUi((s) => s.homePrefs)
  const setPrefs = useUi((s) => s.setHomePrefs)
  const all = home.data?.rows ?? []
  const pos = (id: string) => { const i = prefs.order.indexOf(id); return i < 0 ? 1000 : i }
  const rows = [...all].sort((a, b) => pos(a.id) - pos(b.id))
  const move = (id: string, d: number) => {
    const ids = rows.map((r) => r.id)
    const i = ids.indexOf(id), j = i + d
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j]!, ids[i]!]
    setPrefs({ ...prefs, order: ids })
  }
  const hide = (id: string) => setPrefs({ ...prefs, hidden: prefs.hidden.includes(id) ? prefs.hidden.filter((h) => h !== id) : [...prefs.hidden, id] })
  return (
    <div className="flex flex-col gap-3">
      <h2 className="heading">Home rows</h2>
      <p className="muted">Reorder or hide rows on Home. This is saved in this browser.</p>
      <FocusGroup focusKey="rows:list" className="flex flex-col gap-2">
        {rows.map((r) => (
          <div key={r.id} className="panel flex items-center gap-2" data-testid="row-pref">
            <span className="flex-1" style={{ opacity: prefs.hidden.includes(r.id) ? 0.5 : 1 }}>{r.title}</span>
            <Button focusKey={`rows:${r.id}:up`} iconOnly icon="up" label={`Move ${r.title} up`} onEnter={() => move(r.id, -1)} />
            <Button focusKey={`rows:${r.id}:down`} iconOnly icon="down" label={`Move ${r.title} down`} onEnter={() => move(r.id, 1)} />
            <Button focusKey={`rows:${r.id}:hide`} pressed={prefs.hidden.includes(r.id)} onEnter={() => hide(r.id)}>{prefs.hidden.includes(r.id) ? 'Hidden' : 'Shown'}</Button>
          </div>
        ))}
        {!home.data && <p className="muted">Loading rows…</p>}
      </FocusGroup>
      <div><Button focusKey="rows:reset" onEnter={() => setPrefs({ order: [], hidden: [] })}>Reset to default</Button></div>
    </div>
  )
}

/* ------------------------------ parental ------------------------------ */
function ParentalTab() {
  const me = useCurrent()
  const save = useSaveProfile()
  const groups = useChannelGroups({ language: 'all', include_hidden: true })
  const [groupSearch, setGroupSearch] = useState('')
  const [groupPage, setGroupPage] = useState(0)
  const matchingGroups = (groups.data ?? []).filter((g) => g.name.toLowerCase().includes(groupSearch.trim().toLowerCase()))
  const groupPageIndex = Math.min(groupPage, Math.max(0, Math.ceil(matchingGroups.length / 40) - 1))
  const qc = useQueryClient()
  const toggleGroup = async (id: string, hidden: boolean) => { await ok(api.PATCH('/channels/groups/{id}', { params: { path: { id } }, body: { hidden } })); void qc.invalidateQueries({ queryKey: ['channel-groups'] }); void qc.invalidateQueries({ queryKey: ['channels'] }) }
  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-2">
        <h2 className="heading">Kids profile</h2>
        <p className="muted">The kids profile label is saved, but rating-based restrictions are not yet enforced. Do not rely on this setting to block content.</p>
        <div><Chip focusKey="par:kids" pressed={me.kids} onEnter={() => save({ kids: !me.kids }, me.kids ? 'Kids label off.' : 'Kids label on.')}>{me.kids ? 'Kids label on' : 'Kids label off'}</Chip></div>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="heading">Hide channel groups</h2>
        <p className="muted">This display preference applies to all profiles. It does not block playback or restrict access to a group.</p>
        <FocusableInput focusKey="par:search" label="Search channel groups" placeholder="Search channel groups" value={groupSearch} onChange={(v) => { setGroupSearch(v); setGroupPage(0) }} />
        <FocusGroup focusKey="par:groups" className="flex gap-2 flex-wrap">
          {matchingGroups.slice(groupPageIndex * 40, (groupPageIndex + 1) * 40).map((g) => <Chip key={g.id} focusKey={`par:g:${g.id}`} pressed={g.hidden} onEnter={() => void toggleGroup(g.id, !g.hidden)}>{g.hidden ? 'Hidden' : 'Shown'} · {g.name}</Chip>)}
          {!matchingGroups.length && <span className="muted">No matching channel groups.</span>}
        </FocusGroup>
        <div className="flex gap-3 items-center flex-wrap"><Button focusKey="par:prev" disabled={!groupPageIndex} onEnter={() => setGroupPage(groupPageIndex - 1)}>Previous</Button><span className="label">Page {groupPageIndex + 1} of {Math.max(1, Math.ceil(matchingGroups.length / 40))}</span><Button focusKey="par:next" disabled={(groupPageIndex + 1) * 40 >= matchingGroups.length} onEnter={() => setGroupPage(groupPageIndex + 1)}>Next</Button></div>
      </section>
    </div>
  )
}

/* ------------------------------ playback / server ------------------------------ */
function PlaybackTab() {
  const { data, error, refetch } = useSettings()
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  const [key, setKey] = useState('')
  const patch = async (b: Record<string, unknown>) => {
    try { await ok(api.PATCH('/settings', { body: b })); void qc.invalidateQueries({ queryKey: qk.settings }) } catch (e) { toast(e instanceof Error ? e.message : 'Could not save.', 'error') }
  }
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <p className="muted">Loading…</p>
  const opt = <T extends string | number>(label: string, field: string, values: T[], cur: T | undefined, fmt: (v: T) => string = String) => (
    <section className="flex flex-col gap-2"><h3 className="label">{label}</h3>
      <FocusGroup focusKey={`pb:${field}`} className="flex gap-2 flex-wrap">{values.map((v) => <Chip key={String(v)} focusKey={`pbo:${field}:${v}`} pressed={cur === v} onEnter={() => void patch({ [field]: v })}>{fmt(v)}</Chip>)}</FocusGroup>
    </section>
  )
  return (
    <div className="flex flex-col gap-5">
      {opt('Stream proxy', 'proxy_mode', ['auto', 'always', 'never'], data.proxy_mode)}
      {opt('Guide refresh', 'epg_refresh_hours', [6, 12, 24, 48], data.epg_refresh_hours, (v) => `Every ${v} h`)}
      {opt('Hardware transcoding', 'transcode_hw', ['none', 'vaapi', 'nvenc', 'qsv'], data.transcode_hw)}
      <section className="flex flex-col gap-2">
        <h3 className="label">TMDB API key {data.tmdb_api_key_set ? '(saved)' : '(not set)'}</h3>
        <div className="flex gap-3 flex-wrap items-center">
          <div style={{ width: '22rem', maxWidth: '100%' }}><FocusableInput focusKey="tmdb:key" label="TMDB API key" type="password" placeholder="Paste a key to replace it" value={key} onChange={setKey} /></div>
          <Button focusKey="tmdb:save" disabled={!key.trim()} onEnter={async () => { await ok(api.PUT('/settings/tmdb_key', { body: { api_key: key.trim() } })); setKey(''); void qc.invalidateQueries({ queryKey: qk.settings }); toast('TMDB key saved.', 'success') }}>Save key</Button>
        </div>
      </section>
    </div>
  )
}

/* ------------------------------ about ------------------------------ */
function AboutTab() {
  const d = useDisclaimer()
  const me = useCurrent()
  return (
    <div className="flex flex-col gap-5" data-testid="about">
      <section className="flex flex-col gap-1"><h2 className="heading">StreamHub 1.0.0</h2><p className="muted">A self-hosted hub for your own media and IPTV sources.</p></section>
      <section className="flex flex-col gap-3 panel">
        <h2 className="heading">Android TV app</h2>
        <p>Debug build d99151e · Android 7 or newer · 29 MB. The webpage may include newer features than this APK.</p>
        <AndroidDownload focusKey="about:android" />
        <p className="muted">If Android reports a signing conflict, this APK cannot update that installation. Keep your existing app and data; do not uninstall to work around it.</p>
      </section>
      <section className="flex flex-col gap-3 panel" data-testid="attribution">
        <div className="flex items-center gap-3"><AttrLogo src="/attribution/tmdb.svg" fallback="TMDB" /><p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p></div>
        <div className="flex items-center gap-3"><AttrLogo src="/attribution/justwatch.svg" fallback="JustWatch" /><p>Availability data by JustWatch.</p></div>
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="heading">Legal notice</h2>
        {d.data ? (
          <div className="panel flex flex-col gap-3">
            {d.data.text.split(/\n{2,}/).map((p, i) => <p key={i}>{p.replace(/\*\*/g, '')}</p>)}
            <p className="label">Version {d.data.version}. {me.disclaimer_accepted_version ? `Accepted by ${me.name}.` : 'Not yet accepted on this profile.'}</p>
          </div>
        ) : <p className="muted">Loading…</p>}
      </section>
    </div>
  )
}
function AttrLogo({ src, fallback }: { src: string; fallback: string }) {
  const [bad, setBad] = useState(false)
  return bad ? <strong style={{ minWidth: '5rem' }}>{fallback}</strong> : <img src={src} alt={fallback} height={28} style={{ height: 28, width: 'auto' }} onError={() => setBad(true)} />
}
