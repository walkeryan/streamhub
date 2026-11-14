import { useAuthCapabilities } from '../api/contentPolicy'
import { useCan, useMe } from '../api/me'
import { absent } from '../api/client'
import { people, usePeopleChange, useSessions, type AuthSession } from '../api/people'
import { DEVICE_GONE, DeviceRow } from '../components/DeviceRow'
import { deviceName } from '../lib/people'
import { OwnerPinSettings } from '../components/OwnerPinSettings'
import { SignInMethods } from '../components/SignInMethods'
import { ProfileSwitch } from '../components/ProfileSwitch'
import { ContentPolicySettings } from '../components/ContentPolicySettings'
import { AndroidDownload } from '../components/AndroidDownload'
import { inShell } from '../shell'
import { StreamRecovery } from '../components/StreamRecovery'
import { RequestManagerSettings } from '../components/RequestManagerSettings'
import { HomeShelvesSettings } from '../components/HomeShelvesSettings'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { qk, useChannelGroups, useDisclaimer, useHome, useLanguages, usePatchProfile, useSettings, useTvRelease } from '../api/hooks'
import type { Profile } from '../api/types'
import { Button, Chip, ErrorState, Modal } from '../components/ui'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { setTheme } from '../lib/theme'
import { LANG_NAMES } from '../lib/util'
import { themes } from '../tokens'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

const TABS = [
  ['profiles', 'Profile'], ['appearance', 'Appearance'], ['languages', 'Languages'], ['rows', 'Home rows'],
  ['parental', 'Parental'], ['playback', 'Playback'], ['about', 'About'], ['devices', 'Devices'],
] as const
type Tab = (typeof TABS)[number][0]

export function Settings() {
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.find((t) => t[0] === sp.get('tab'))?.[0] ?? 'profiles') as Tab
  useRestoreFocus(true, `set:tab:${tab}`)
  // Signed-in devices are the server's users-and-sessions model; a server without it has none to list.
  const sessions = useMe().data !== null
  return (
    <div className="page-pad flex flex-col gap-6" data-testid="settings">
      <h1 className="display">Settings</h1>
      <FocusGroup focusKey="set:tabs" className="flex gap-2 flex-wrap" role="tablist">
        {TABS.filter(([id]) => id !== 'devices' || sessions).map(([id, label]) => <Chip key={id} focusKey={`set:tab:${id}`} pressed={tab === id} onEnter={() => setSp({ tab: id }, { replace: true })} testId={`tab-${id}`}>{label}</Chip>)}
      </FocusGroup>
      <div style={{ maxWidth: '60rem' }}>
        {tab === 'profiles' && <ProfilesTab />}
        {tab === 'appearance' && <AppearanceTab />}
        {tab === 'languages' && <LanguagesTab />}
        {tab === 'rows' && <RowsTab />}
        {tab === 'parental' && <ParentalTab />}
        {tab === 'playback' && <PlaybackTab />}
        {tab === 'about' && <AboutTab />}
        {tab === 'devices' && sessions && <DevicesTab />}
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

/* ------------------------------ profile ------------------------------ */
/** The signed-in person: who they are, switching, and their own PIN. Everyone else is managed on the Users screen. */
function ProfilesTab() {
  const capabilities=useAuthCapabilities()
  const me = useCurrent()
  // A restricted profile never manages others, whatever its permissions say.
  const manages = useCan('users') && !me.content_restricted
  const owner = useCan('system.manage')
  const qc = useQueryClient()
  const go = useGo()
  const toast = useUi((s) => s.toast)
  const [pin, setPin] = useState('')
  const [currentPin, setCurrentPin] = useState('')
  const [pinErr, setPinErr] = useState<string | null>(null)
  const savePin = async (value: string | null) => {
    if (value !== null && !/^\d{4,8}$/.test(value)) { setPinErr('Use 4 to 8 digits.'); return }
    setPinErr(null)
    // A PIN that exists is only changed or removed by someone who knows it (or who has just signed in).
    const body = { pin: value, ...(me.has_pin && currentPin ? { current_pin: currentPin } : {}) }
    try { await ok(api.PUT('/profiles/{id}/pin', { params: { path: { id: me.id } }, body })); setPin(''); setCurrentPin(''); void qc.invalidateQueries({ queryKey: qk.profiles }); toast(value ? 'PIN saved.' : 'PIN removed.', 'success') }
    catch (e) {
      const code = (e as { code?: string }).code
      if (code === 'current_pin_required') setPinErr('Enter your current PIN to change or remove it.')
      else if (code === 'pin_invalid') setPinErr('That is not your current PIN.')
      else toast(e instanceof Error ? e.message : 'Could not change the PIN.', 'error')
    }
  }
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h2 className="heading">Signed in as {me.name}</h2>
        <p className="muted">Theme and languages, on the tabs beside this one, are saved for {me.name} and nobody else.</p>
        <div className="flex gap-3 flex-wrap">
          <ProfileSwitch />
        </div>
      </section>
      {manages && (
        <section className="flex flex-col gap-3" data-testid="profile-users">
          <h2 className="heading">Other people</h2>
          <p className="muted">Who can use this StreamHub, what each of them may watch, their PINs and their devices are on the Users screen.</p>
          <div><Button focusKey="prof:users" icon="users" onEnter={() => go('/users')} testId="open-users">Open Users</Button></div>
        </section>
      )}
      <SignInMethods />
      <OwnerPinSettings />
      {!me.content_restricted && (!capabilities.data?.content_policy_enabled || owner) && <section className="flex flex-col gap-3">
        <h2 className="heading">PIN for {me.name}</h2>
        <p className="muted">{me.has_pin ? 'A PIN is required to open this profile.' : 'Anyone can open this profile. Add a PIN to lock it.'}</p>
        <div className="flex gap-3 flex-wrap items-center">
          {me.has_pin && <div style={{ width: '12rem' }}><FocusableInput focusKey="prof:pin-current" label="Current PIN" type="password" inputMode="numeric" maxLength={8} placeholder="Current PIN" value={currentPin} onChange={setCurrentPin} onSubmit={() => void setFocus('prof:pin')} /></div>}
          <div style={{ width: '12rem' }}><FocusableInput focusKey="prof:pin" label="New PIN" type="password" inputMode="numeric" maxLength={8} placeholder="4 to 8 digits" value={pin} onChange={setPin} onSubmit={() => void savePin(pin)} /></div>
          <Button focusKey="prof:pin-save" onEnter={() => void savePin(pin)}>Save PIN</Button>
          {me.has_pin && <Button focusKey="prof:pin-clear" danger onEnter={() => void savePin(null)}>Remove PIN</Button>}
        </div>
        {pinErr && <p role="alert" style={{ color: 'var(--t-danger)' }}>{pinErr}</p>}
      </section>}
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
  // Which shelves Home has is the server's, for everyone; their order and visibility here is this browser's own.
  const admin = useCan('settings.admin')
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
    <div className="flex flex-col gap-8">
    {admin && <HomeShelvesSettings />}
    <section className="flex flex-col gap-3">
      <h2 className="heading">{admin ? 'Home rows in this browser' : 'Home rows'}</h2>
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
    </section>
    </div>
  )
}

/* ------------------------------ parental ------------------------------ */
function ParentalTab() {
  const capabilities=useAuthCapabilities()
  const me = useCurrent()
  const save = useSaveProfile()
  const groups = useChannelGroups({ language: 'all', include_hidden: true })
  const [groupSearch, setGroupSearch] = useState('')
  const [groupPage, setGroupPage] = useState(0)
  const matchingGroups = (groups.data ?? []).filter((g) => g.path.toLowerCase().includes(groupSearch.trim().toLowerCase()))
  // A hidden parent hides its children whatever their own setting: showing them again is the parent's switch.
  const hiddenParents = new Set((groups.data ?? []).filter((g) => !g.parent && g.hidden).map((g) => `${g.source_id}|${g.name}`))
  const groupPageIndex = Math.min(groupPage, Math.max(0, Math.ceil(matchingGroups.length / 40) - 1))
  const qc = useQueryClient()
  const toggleGroup = async (id: string, hidden: boolean) => { await ok(api.PATCH('/channels/groups/{id}', { params: { path: { id } }, body: { hidden } })); void qc.invalidateQueries({ queryKey: ['channel-groups'] }); void qc.invalidateQueries({ queryKey: ['channels'] }) }
  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-2">
        <ContentPolicySettings />
        {capabilities.data&&!capabilities.data.content_policy_enabled&&<>
        <h2 className="heading">Kids profile label</h2>
        <p className="muted">This label alone does not block content. Server-enforced restrictions, when enabled, are managed separately by the owner.</p>
        <div><Chip focusKey="par:kids" pressed={me.kids} onEnter={() => save({ kids: !me.kids }, me.kids ? 'Kids label off.' : 'Kids label on.')}>{me.kids ? 'Kids label on' : 'Kids label off'}</Chip></div></>}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="heading">Hide channel groups</h2>
        <p className="muted">This display preference applies to all profiles. It does not block playback or restrict access to a group.</p>
        <FocusableInput focusKey="par:search" label="Search channel groups" placeholder="Search channel groups" value={groupSearch} onChange={(v) => { setGroupSearch(v); setGroupPage(0) }} />
        <FocusGroup focusKey="par:groups" className="flex gap-2 flex-wrap">
          {matchingGroups.slice(groupPageIndex * 40, (groupPageIndex + 1) * 40).map((g) => g.parent && hiddenParents.has(`${g.source_id}|${g.parent}`)
            ? <span key={g.id} className="chip" style={{ opacity: 0.6 }} data-testid="group-hidden-with-parent">Hidden with {g.parent} · {g.name}</span>
            : <Chip key={g.id} focusKey={`par:g:${g.id}`} pressed={g.hidden} onEnter={() => void toggleGroup(g.id, !g.hidden)}>{g.hidden ? 'Hidden' : 'Shown'} · {g.path}</Chip>)}
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
  const admin = useCan('settings.admin')
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
      <StreamRecovery />
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
      {admin && <RequestManagerSettings configured={!!data.request_manager_configured} />}
    </div>
  )
}

/* ------------------------------ devices ------------------------------ */
function DevicesTab() {
  const { data, error, refetch } = useSessions()
  const toast = useUi((s) => s.toast)
  const signOut = useSession((s) => s.clear)
  const qc = useQueryClient()
  const [renaming, setRenaming] = useState<AuthSession | null>(null)
  const [label, setLabel] = useState('')
  const rename = usePeopleChange((v: { sid: string; label: string }) => people.patchSession(v.sid, { label: v.label }))
  const save = () => {
    const s = renaming
    if (!s || !label.trim()) return
    rename.mutate({ sid: s.sid, label: label.trim() }, {
      onSuccess: () => { toast('Device renamed.', 'success'); setRenaming(null) },
      onError: (e) => { toast(absent(e) ? DEVICE_GONE : e.message, 'error'); setRenaming(null); if (absent(e)) void refetch() },
    })
  }
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  return (
    <div className="flex flex-col gap-3" data-testid="devices">
      <h2 className="heading">Where you are signed in</h2>
      <p className="muted">Revoke signs a device out; it has to sign in again to be used.</p>
      {!data ? <p className="muted">Loading…</p> : (
        <FocusGroup focusKey="dev:list" className="flex flex-col gap-2">
          {data.map((s) => <DeviceRow key={s.sid} session={s} scope="dev" rename={() => { setLabel(s.label ?? ''); setRenaming(s) }}
            // Revoking this very device is signing out of it.
            onRevoked={(gone) => { if (gone.current) { signOut(); qc.clear() } }} />)}
        </FocusGroup>
      )}
      <Modal open={!!renaming} onClose={() => setRenaming(null)} title={`Rename ${renaming ? deviceName(renaming.label, renaming.kind, renaming.user_agent) : ''}`} firstFocusKey="devname:input">
        <FocusableInput focusKey="devname:input" label="Device name" placeholder="Living room TV" maxLength={64} value={label} onChange={setLabel} onSubmit={save} />
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="devname:save" primary disabled={!label.trim() || rename.isPending} onEnter={save} testId="device-rename-save">Save name</Button>
          <Button focusKey="devname:cancel" onEnter={() => setRenaming(null)}>Cancel</Button>
        </div>
      </Modal>
    </div>
  )
}

/* ------------------------------ about ------------------------------ */
function AboutTab() {
  const d = useDisclaimer()
  const releaseQuery = useTvRelease()
  const release = releaseQuery.data
  const me = useCurrent()
  return (
    <div className="flex flex-col gap-5" data-testid="about">
      <section className="flex flex-col gap-1"><h2 className="heading">StreamHub 1.0.0</h2><p className="muted">A self-hosted hub for your own media and IPTV sources.</p></section>
      {/* Inside the Android TV app the TV updates itself (TV settings on its error screen). */}
      {!inShell() && <section className="flex flex-col gap-3 panel">
        <h2 className="heading">Android TV app</h2>
        {release ? <>
          <p>Version {release.version_name} · Android 7 or newer. The TV shows this website full screen and plays video natively; once installed it updates itself from this server.</p>
          <AndroidDownload focusKey="about:android" release={release} />
          <p className="muted">If Android reports a signing conflict, this APK cannot update that installation. Keep your existing app and data; do not uninstall to work around it.</p>
        </> : <p className="muted" data-testid="android-unpublished">{releaseQuery.isPending ? 'Checking for the TV app…' : 'No TV app is published on this server.'}</p>}
      </section>}
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
