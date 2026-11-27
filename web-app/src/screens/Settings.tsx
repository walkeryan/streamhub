import { OfflineSettings } from '../components/OfflineEpisodes'
import { useAuthCapabilities } from '../api/contentPolicy'
import { useCan, useMe } from '../api/me'
import { absent } from '../api/client'
import { people, usePeopleChange, useSessions, withKind, type AuthSession, type PinKind } from '../api/people'
import { DEVICE_GONE, DeviceRow } from '../components/DeviceRow'
import { deviceName } from '../lib/people'
import { OwnerPinSettings } from '../components/OwnerPinSettings'
import { SignInMethods } from '../components/SignInMethods'
import { ProfileSwitch } from '../components/ProfileSwitch'
import { ContentPolicySettings } from '../components/ContentPolicySettings'
import { AndroidDownload } from '../components/AndroidDownload'
import { SecretControls } from '../components/SignInSecret'
import { CanvasPlaybackOptions } from '../player/CanvasPlaybackOptions'
import { inShell } from '../shell'
import { StreamRecovery } from '../components/StreamRecovery'
import { RequestManagerSettings } from '../components/RequestManagerSettings'
import { HomeShelvesSettings } from '../components/HomeShelvesSettings'
import { HomeNetworkSettings } from '../components/HomeNetworkSettings'
import { useState } from 'react'
import { IdleSetting } from '../session/IdleSetting'
import { ViewingAppearance } from '../components/ViewingAppearance'
import { useHomeRows } from '../api/viewingPreferences'
import { useProfilePreference } from '../api/profilePreferences'
import { useOutletContext, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok, refreshMe } from '../api/client'
import { qk, useChannelGroups, useDisclaimer, useHome, useLanguages, usePatchProfile, useSettings, useTvRelease } from '../api/hooks'
import type { Profile } from '../api/types'
import { TabPanels, Button, Chip, ErrorState, Modal } from '../components/ui'
import { Avatar } from '../components/Avatar'
import { GlassIconButton, GlassPanel, PageHeader, SectionHeader } from '../ds'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { setTheme } from '../lib/theme'
import { LANG_NAMES } from '../lib/util'
import { themes } from '../tokens'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { CastSettingsButton } from '../cast/CastPanel'

const TABS = [
  ['profiles', 'Profile'], ['appearance', 'Appearance'], ['languages', 'Languages'], ['rows', 'Home rows'],
  ['parental', 'Parental'], ['playback', 'Playback'], ['about', 'About'], ['devices', 'Devices'],
] as const
type Tab = (typeof TABS)[number][0]

export function Settings() {
  const { openShortcuts } = useOutletContext<{ openShortcuts: () => void }>()
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.find((t) => t[0] === sp.get('tab'))?.[0] ?? 'profiles') as Tab
  useRestoreFocus(true, `set:tab:${tab}`)
  // Signed-in devices are the server's users-and-sessions model; a server without it has none to list.
  const sessions = useMe().data !== null
  const tabs = TABS.filter(([id]) => id !== 'devices' || sessions)
  return (
    <div className="page-pad acct-page" data-testid="settings">
      <PageHeader title="Settings"><Button focusKey="set:shortcuts" ghost icon="list" onEnter={openShortcuts}>Controls and shortcuts</Button></PageHeader>
      {/* The tabs scroll sideways on a phone rather than wrapping into a wall of chips. */}
      <FocusGroup focusKey="set:tabs" className="chip-scroll" role="tablist" label="Settings">
        {tabs.map(([id, label]) => <Chip tab key={id} focusKey={`set:tab:${id}`} pressed={tab === id} onEnter={() => setSp({ tab: id }, { replace: true })} testId={`tab-${id}`}>{label}</Chip>)}
      </FocusGroup>
      <TabPanels tabs={tabs.map(([id]) => `set:tab:${id}`)} active={`set:tab:${tab}`} className="acct-body">
        {tab === 'profiles' && <><ProfilesTab /><IdleSetting /></>}
        {tab === 'appearance' && <><AppearanceTab /><ViewingAppearance /></>}
        {tab === 'languages' && <LanguagesTab />}
        {tab === 'rows' && <RowsTab />}
        {tab === 'parental' && <ParentalTab />}
        {tab === 'playback' && <PlaybackTab />}
        {tab === 'about' && <AboutTab />}
        {tab === 'devices' && sessions && <DevicesTab />}
      </TabPanels>
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
  const setProfile = useSession((s) => s.setProfile)
  const qc = useQueryClient()
  const go = useGo()
  // A secret that exists is only changed or removed by someone who knows it (or who has just signed in):
  // the hub answers current_pin_required, and SecretControls asks for the current one the way it was chosen.
  const putPin = async (value: string | null, kind: PinKind | null, current?: string) => {
    const body = { ...(value ? withKind({ pin: value }, kind ?? undefined) : { pin: null }), ...(current ? { current_pin: current } : {}) }
    await ok(api.PUT('/profiles/{id}/pin', { params: { path: { id: me.id } }, body }))
    // The profile this tab shows is the session's own copy: it says the new way at once, as the hub now does.
    setProfile({ ...me, has_pin: !!value, pin_kind: value ? kind ?? 'pin' : null, pin_arrows: value && kind === 'arrows' ? value.length : null })
    void qc.invalidateQueries({ queryKey: qk.profiles })
    refreshMe()
  }
  return (
    <>
      <GlassPanel as="section" className="acct-me">
        <span className="ud-face"><Avatar name={me.name} size={3.5} /></span>
        <div className="acct-me-who">
          <h2 className="acct-me-name">Signed in as {me.name}</h2>
          <p className="acct-note">Theme and languages, on the tabs beside this one, are saved for {me.name} and nobody else.</p>
        </div>
        <div className="acct-actions">
          <ProfileSwitch />
        </div>
      </GlassPanel>
      <GlassPanel as="section" className="acct-group" testId="profile-tv-controls">
        <SectionHeader title={inShell() ? 'Phone control for this TV' : 'Watch on a TV'} />
        <p className="acct-note">{inShell()
          ? 'Allow a phone using this profile to request control of this TV. You approve each new connection.'
          : 'Choose a TV using this profile, control its playback, or connect a new TV.'}</p>
        <div className="acct-actions"><CastSettingsButton /></div>
      </GlassPanel>
      {manages && (
        <GlassPanel as="section" className="acct-group" testId="profile-users">
          <SectionHeader title="Other people" />
          <p className="acct-note">Who can use this StreamHub, what each of them may watch, their PINs and their devices are on the Users screen.</p>
          {/* Under its words, where Down from the profile above lands; a place to go keeps its name. */}
          <div><Button focusKey="prof:users" icon="users" onEnter={() => go('/users')} testId="open-users">Open Users</Button></div>
        </GlassPanel>
      )}
      <SignInMethods />
      <OwnerPinSettings />
      {!me.content_restricted && (!capabilities.data?.content_policy_enabled || owner) && <GlassPanel as="section" className="acct-group" testId="profile-secret">
        <SectionHeader title={`How ${me.name} signs in on shared devices`} />
        <p className="acct-note">One way of your choosing: a number PIN, a password or an arrow pattern. It is asked for to open this profile on "Who's watching?", and to show adult titles.</p>
        <FocusGroup focusKey="prof:secret" className="acct-list">
          <SecretControls scope="prof" name={me.name} who={me} testId="prof-pin"
            noneHint="Anyone can open this profile. Choose a sign-in to lock it."
            setHint={me.pin_kind === 'arrows' || (!me.pin_kind && me.pin_arrows) ? 'Typed as a PIN anywhere else, the pattern is its digits: ↑ 2, ← 4, → 6, ↓ 8.' : 'Required to open this profile.'}
            save={putPin} />
        </FocusGroup>
      </GlassPanel>}
    </>
  )
}

/* ------------------------------ appearance ------------------------------ */
function AppearanceTab() {
  const me = useCurrent()
  const save = useSaveProfile()
  const choose = (id: string, name: string) => { setTheme(id); save({ theme_id: id }, `Theme: ${name}`) }
  return (
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Theme" />
      <p className="acct-note">Themes apply immediately and are saved to {me.name}'s profile.</p>
      <FocusGroup focusKey="theme:list" className="theme-grid" role="radiogroup" label="Theme">
        {themes.map((t) => (
          <Focusable key={t.id} focusKey={`theme:${t.id}`} className="theme-card" role="radio" aria={{ checked: me.theme_id === t.id }} label={`${t.name} theme`} data-testid={`theme-${t.id}`} onEnter={() => choose(t.id, t.name)}>
            <div data-theme={t.id === 'adaptive' ? 'midnight' : t.id} className="theme-preview" aria-hidden="true">
              <div style={{ height: 44, borderRadius: 6, background: `linear-gradient(to top, var(--t-bg), transparent), linear-gradient(120deg, var(--t-surface-alt), var(--t-surface))`, display: 'flex', alignItems: 'flex-end', padding: 6 }}>
                <span style={{ background: t.id === 'adaptive' ? 'hsl(280 70% 60%)' : 'var(--t-accent)', color: 'var(--t-accent-text)', fontSize: 9, fontWeight: 700, padding: '1px 8px', borderRadius: 4 }}>Play</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {[0, 1, 2].map((i) => <i key={i} style={{ width: 28, height: 40, borderRadius: 4, background: 'var(--t-surface-alt)', outline: i === 1 ? '2px solid var(--t-focus-ring)' : 'none', transform: i === 1 ? 'scale(1.08)' : 'none', boxShadow: i === 1 ? '0 0 10px color-mix(in srgb, var(--t-focus-glow) 55%, transparent)' : 'none' }} />)}
              </div>
              <span style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>Muted text sample</span>
            </div>
            <div className="theme-name"><span>{t.name}</span>{me.theme_id === t.id && <span className="badge badge-play">Active</span>}</div>
            {t.adaptive && <span className="acct-small">Accent follows the artwork in focus</span>}
          </Focusable>
        ))}
      </FocusGroup>
    </GlassPanel>
  )
}

/* ------------------------------ languages ------------------------------ */
function LanguagesTab() {
  const me = useCurrent()
  const langs = useLanguages()
  const save = useSaveProfile()
  const toggle = (code: string) => save({ languages: me.languages.includes(code) ? me.languages.filter((c) => c !== code) : [...me.languages, code] })
  return (
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Preferred languages" />
      <p className="acct-note">Live TV, Guide, library browsing and Search default to these languages. Other languages remain available through All languages. Choose none to show every language.</p>
      {langs.error ? <ErrorState error={langs.error} onRetry={() => void langs.refetch()} /> : (
        <FocusGroup focusKey="lang:list" className="acct-chips">
          <Chip focusKey="lang:all" pressed={me.languages.length === 0} onEnter={() => save({ languages: [] })}>All languages</Chip>
          {(langs.data ?? []).map((l) => <Chip key={l.code} focusKey={`lang:${l.code}`} pressed={me.languages.includes(l.code)} onEnter={() => toggle(l.code)}>{LANG_NAMES[l.code] ?? l.name} · {l.count}</Chip>)}
        </FocusGroup>
      )}
    </GlassPanel>
  )
}

/* ------------------------------ home rows ------------------------------ */
function RowsTab() {
  const admin = useCan('settings.admin')
  const home = useHome()
  const { value: prefs, update: setPrefs, ready, error, retry } = useHomeRows()
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
    <>
    {admin && <HomeShelvesSettings />}
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Home rows"
        action={<GlassIconButton focusKey="rows:reset" disabled={!ready} icon="refresh" label="Reset to default" size="sm" onEnter={() => setPrefs({ order: [], hidden: [] })} />} />
      <p className="acct-note">Reorder or hide rows on every device that uses this profile.</p>
      {error && <p role="alert">{error} <Button onEnter={() => void retry()}>Retry</Button></p>}
      <FocusGroup focusKey="rows:list" className="acct-list">
        {ready && rows.map((r) => (
          <div key={r.id} className="acct-row acct-order" data-testid="row-pref" data-hidden={prefs.hidden.includes(r.id)}>
            <span className="acct-row-main"><span className="acct-row-title"><strong>{r.title}</strong></span></span>
            <span className="acct-row-end">
              <GlassIconButton focusKey={`rows:${r.id}:up`} icon="up" size="sm" label={`Move ${r.title} up`} tip="Move up" onEnter={() => move(r.id, -1)} />
              <GlassIconButton focusKey={`rows:${r.id}:down`} icon="down" size="sm" label={`Move ${r.title} down`} tip="Move down" onEnter={() => move(r.id, 1)} />
              <Button focusKey={`rows:${r.id}:hide`} className="btn-chip" pressed={prefs.hidden.includes(r.id)} onEnter={() => hide(r.id)}>{prefs.hidden.includes(r.id) ? 'Hidden' : 'Shown'}</Button>
            </span>
          </div>
        ))}
        {!home.data && <p className="acct-note">Loading rows…</p>}
      </FocusGroup>
    </GlassPanel>
    </>
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
    <>
      <GlassPanel as="section" className="acct-group">
        <ContentPolicySettings />
        {capabilities.data&&!capabilities.data.content_policy_enabled&&<>
        <SectionHeader title="Kids profile label" />
        <p className="acct-note">This label alone does not block content. Server-enforced restrictions, when enabled, are managed separately by the owner.</p>
        <div><Chip focusKey="par:kids" pressed={me.kids} onEnter={() => save({ kids: !me.kids }, me.kids ? 'Kids label off.' : 'Kids label on.')}>{me.kids ? 'Kids label on' : 'Kids label off'}</Chip></div></>}
      </GlassPanel>
      <GlassPanel as="section" className="acct-group">
        <SectionHeader title="Hide channel groups" />
        <p className="acct-note">This display preference applies to all profiles. It does not block playback or restrict access to a group.</p>
        <FocusableInput focusKey="par:search" label="Search channel groups" placeholder="Search channel groups" value={groupSearch} onChange={(v) => { setGroupSearch(v); setGroupPage(0) }} />
        <FocusGroup focusKey="par:groups" className="acct-chips">
          {matchingGroups.slice(groupPageIndex * 40, (groupPageIndex + 1) * 40).map((g) => g.parent && hiddenParents.has(`${g.source_id}|${g.parent}`)
            ? <span key={g.id} className="chip" style={{ opacity: 0.6 }} data-testid="group-hidden-with-parent">Hidden with {g.parent} · {g.name}</span>
            : <Chip key={g.id} focusKey={`par:g:${g.id}`} pressed={g.hidden} onEnter={() => void toggleGroup(g.id, !g.hidden)}>{g.hidden ? 'Hidden' : 'Shown'} · {g.path}</Chip>)}
          {!matchingGroups.length && <span className="acct-note">No matching channel groups.</span>}
        </FocusGroup>
        <div className="acct-pager">
          <GlassIconButton focusKey="par:prev" icon="chevL" size="sm" label="Previous" disabled={!groupPageIndex} onEnter={() => setGroupPage(groupPageIndex - 1)} />
          <span className="acct-small">Page {groupPageIndex + 1} of {Math.max(1, Math.ceil(matchingGroups.length / 40))}</span>
          <GlassIconButton focusKey="par:next" icon="chevR" size="sm" label="Next" disabled={(groupPageIndex + 1) * 40 >= matchingGroups.length} onEnter={() => setGroupPage(groupPageIndex + 1)} />
        </div>
      </GlassPanel>
    </>
  )
}

/* ------------------------------ playback / server ------------------------------ */
/** This profile's player preference, read by the web and the TV players alike (`autoplay_next`, missing = on). */
function AutoplaySetting() {
  const pref = useProfilePreference<boolean>('autoplay_next', true, (raw) => (typeof raw === 'boolean' ? raw : null))
  return (
    <section className="acct-field" data-testid="autoplay-setting">
      <h3 className="acct-kicker">Autoplay next episode</h3>
      <FocusGroup focusKey="pb:autoplay" className="acct-chips">
        <Chip focusKey="pbo:autoplay:on" pressed={pref.value} onEnter={() => pref.update(true)} testId="autoplay-on">On</Chip>
        <Chip focusKey="pbo:autoplay:off" pressed={!pref.value} onEnter={() => pref.update(false)} testId="autoplay-off">Off</Chip>
      </FocusGroup>
      <p className="acct-small">{pref.error && pref.ready ? 'Could not save. Try again.' : pref.value
        ? 'At the end of an episode the next one starts after a 10-second countdown.'
        : 'At the end of an episode, the next one waits for you to choose Play next.'}</p>
    </section>
  )
}

/** Whether Back shrinks a playing film or episode to the mini player (`back_minimises`, missing = off); read by both players. */
function BackMinimisesSetting() {
  const pref = useProfilePreference<boolean>('back_minimises', false, (raw) => (typeof raw === 'boolean' ? raw : null))
  return (
    <section className="acct-field" data-testid="back-minimises-setting">
      <h3 className="acct-kicker">Back shrinks to mini player</h3>
      <FocusGroup focusKey="pb:backmini" className="acct-chips">
        <Chip focusKey="pbo:backmini:on" pressed={pref.value} onEnter={() => pref.update(true)} testId="back-minimises-on">On</Chip>
        <Chip focusKey="pbo:backmini:off" pressed={!pref.value} onEnter={() => pref.update(false)} testId="back-minimises-off">Off</Chip>
      </FocusGroup>
      <p className="acct-small">{pref.error && pref.ready ? 'Could not save. Try again.' : pref.value
        ? 'Back keeps a playing film or episode going in the mini player while you browse.'
        : 'Back closes the player. The minimise button still shrinks it to the mini player.'}</p>
    </section>
  )
}

function CanvasPlaybackSetting() {
  return (
    <section className="acct-field" aria-label="Canvas streaming">
      <h3 className="acct-kicker">Canvas streaming</h3>
      <FocusGroup focusKey="pb:canvas" className="acct-chips">
        <CanvasPlaybackOptions scope="pbo:canvas" />
      </FocusGroup>
      <p className="acct-small">Draw video frames on a canvas in this browser. Subtitles use the standard video display.</p>
    </section>
  )
}

function PlaybackTab() {
  return (
    <>
      <GlassPanel as="section" className="acct-group">
        <SectionHeader title="Player" />
        {!inShell() && <CanvasPlaybackSetting />}
        <AutoplaySetting />
        <OfflineSettings />
        <BackMinimisesSetting />
      </GlassPanel>
      <ServerPlaybackSettings />
    </>
  )
}

function ServerPlaybackSettings() {
  const { data, error, refetch } = useSettings()
  const admin = useCan('settings.admin')
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  const [key, setKey] = useState('')
  const patch = async (b: Record<string, unknown>) => {
    try { await ok(api.PATCH('/settings', { body: b })); void qc.invalidateQueries({ queryKey: qk.settings }) } catch (e) { toast(e instanceof Error ? e.message : 'Could not save.', 'error') }
  }
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data) return <p className="acct-note">Loading…</p>
  const opt = <T extends string | number>(label: string, field: string, values: T[], cur: T | undefined, fmt: (v: T) => string = String) => (
    <section className="acct-field"><h3 className="acct-kicker">{label}</h3>
      <FocusGroup focusKey={`pb:${field}`} className="acct-chips">{values.map((v) => <Chip key={String(v)} focusKey={`pbo:${field}:${v}`} pressed={cur === v} onEnter={() => void patch({ [field]: v })}>{fmt(v)}</Chip>)}</FocusGroup>
    </section>
  )
  return (
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Server" />
      <StreamRecovery />
      {opt('Stream proxy', 'proxy_mode', ['auto', 'always', 'never'], data.proxy_mode)}
      {opt('Guide refresh', 'epg_refresh_hours', [6, 12, 24, 48], data.epg_refresh_hours, (v) => `Every ${v} h`)}
      {opt('Hardware transcoding', 'transcode_hw', ['none', 'vaapi', 'nvenc', 'qsv'], data.transcode_hw)}
      <section className="acct-field">
        <h3 className="acct-kicker">TMDB API key {data.tmdb_api_key_set ? '(saved)' : '(not set)'}</h3>
        <div className="acct-actions">
          <div style={{ width: '22rem', maxWidth: '100%' }}><FocusableInput focusKey="tmdb:key" label="TMDB API key" type="password" placeholder="Paste a key to replace it" value={key} onChange={setKey} /></div>
          <Button focusKey="tmdb:save" disabled={!key.trim()} onEnter={async () => { await ok(api.PUT('/settings/tmdb_key', { body: { api_key: key.trim() } })); setKey(''); void qc.invalidateQueries({ queryKey: qk.settings }); toast('TMDB key saved.', 'success') }}>Save key</Button>
        </div>
      </section>
      {admin && <RequestManagerSettings configured={!!data.request_manager_configured} />}
    </GlassPanel>
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
    <>
    <GlassPanel as="section" className="acct-group" testId="devices">
      <SectionHeader title="Where you are signed in" meta={data ? `${data.length}` : undefined} />
      <p className="acct-note">Revoke signs a device out; it has to sign in again to be used.</p>
      {!data ? <p className="acct-note">Loading…</p> : (
        <FocusGroup focusKey="dev:list" className="acct-list">
          {data.map((s) => <DeviceRow key={s.sid} session={s} scope="dev" rename={() => { setLabel(s.label ?? ''); setRenaming(s) }}
            // Revoking this very device is signing out of it.
            onRevoked={(gone) => { if (gone.current) { signOut(); qc.clear() } }} />)}
        </FocusGroup>
      )}
      <Modal open={!!renaming} onClose={() => setRenaming(null)} title={`Rename ${renaming ? deviceName(renaming.label, renaming.kind, renaming.user_agent) : ''}`} firstFocusKey="devname:input" variant="sheet">
        <FocusableInput focusKey="devname:input" label="Device name" placeholder="Living room TV" maxLength={64} value={label} onChange={setLabel} onSubmit={save} />
        <div className="acct-actions">
          <Button focusKey="devname:save" primary disabled={!label.trim() || rename.isPending} onEnter={save} testId="device-rename-save">Save name</Button>
          <Button focusKey="devname:cancel" onEnter={() => setRenaming(null)}>Cancel</Button>
        </div>
      </Modal>
    </GlassPanel>
    <HomeNetworkSettings />
    </>
  )
}

/* ------------------------------ about ------------------------------ */
function AboutTab() {
  const d = useDisclaimer()
  const releaseQuery = useTvRelease()
  const release = releaseQuery.data
  const me = useCurrent()
  return (
    <div className="acct-body" data-testid="about">
      <GlassPanel as="section" className="acct-group">
        <SectionHeader title="StreamHub" meta="1.0.0" />
        <p className="acct-note">A self-hosted hub for your own media and IPTV sources.</p>
      </GlassPanel>
      {/* Inside the Android TV app the TV updates itself (TV settings on its error screen). */}
      {!inShell() && <GlassPanel as="section" className="acct-group">
        <SectionHeader title="Android TV app" meta={release ? `Version ${release.version_name}` : undefined} />
        {release ? <>
          <p>Version {release.version_name} · Android 7 or newer. The TV shows this website full screen and plays video natively; once installed it updates itself from this server.</p>
          <div><AndroidDownload focusKey="about:android" release={release} /></div>
          <p className="acct-small">If Android reports a signing conflict, this APK cannot update that installation. Keep your existing app and data; do not uninstall to work around it.</p>
        </> : <p className="acct-note" data-testid="android-unpublished">{releaseQuery.isPending ? 'Checking for the TV app…' : 'No TV app is published on this server.'}</p>}
      </GlassPanel>}
      <GlassPanel as="section" className="acct-group" testId="attribution">
        <SectionHeader title="Data sources" />
        <div className="attr-row"><AttrLogo src="/attribution/tmdb.svg" fallback="TMDB" /><p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p></div>
        <div className="attr-row"><AttrLogo src="/attribution/justwatch.svg" fallback="JustWatch" /><p>Availability data by JustWatch.</p></div>
      </GlassPanel>
      <GlassPanel as="section" className="acct-group">
        <SectionHeader title="Legal notice" meta={d.data ? `Version ${d.data.version}` : undefined} />
        {d.data ? (
          <div className="legal-text">
            {d.data.text.split(/\n{2,}/).map((p, i) => <p key={i}>{p.replace(/\*\*/g, '')}</p>)}
            <p className="acct-small">Version {d.data.version}. {me.disclaimer_accepted_version ? `Accepted by ${me.name}.` : 'Not yet accepted on this profile.'}</p>
          </div>
        ) : <p className="acct-note">Loading…</p>}
      </GlassPanel>
    </div>
  )
}
function AttrLogo({ src, fallback }: { src: string; fallback: string }) {
  const [bad, setBad] = useState(false)
  return bad ? <strong style={{ minWidth: '5rem' }}>{fallback}</strong> : <img src={src} alt={fallback} height={28} style={{ height: 28, width: 'auto' }} onError={() => setBad(true)} />
}
