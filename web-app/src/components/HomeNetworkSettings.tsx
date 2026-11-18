import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { qk, useSettings } from '../api/hooks'
import { FocusGroup } from '../nav/Focusable'
import { useUi } from '../store/ui'
import { Chip } from './ui'
import { GlassPanel } from '../ds/States'
import { SectionHeader } from '../ds/Section'

/**
 * Settings (administrators): whether the household's own internet address counts as home. Needed
 * when the TVs open this StreamHub by its public name through a tunnel: the hub then sees the home
 * router's internet address, and profiles kept to the home network would be refused at home.
 */
export function HomeNetworkSettings() {
  const { data } = useSettings()
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  // Null for anyone who does not manage the server: the section is theirs alone.
  if (data?.home_public_address == null) return null
  const set = async (on: boolean) => {
    try { await ok(api.PATCH('/settings', { body: { home_public_address: on } })); void qc.invalidateQueries({ queryKey: qk.settings }) }
    catch (e) { toast(e instanceof Error ? e.message : 'Could not save.', 'error') }
  }
  const on = data.home_public_address
  return (
    <GlassPanel as="section" className="acct-group" testId="home-network">
      <SectionHeader title="Home network" />
      <p className="acct-note">Profiles kept to the home network work on devices at home. If your TVs open StreamHub by its web address rather than a home address, count your home's internet address as home too. Leave it off if your internet address is shared with neighbours.</p>
      <FocusGroup focusKey="homenet:choice" className="acct-chips" role="group" label="Count our internet address as home">
        <Chip focusKey="homenet:on" pressed={on} onEnter={() => void set(true)} testId="home-public-on">Count our internet address as home</Chip>
        <Chip focusKey="homenet:off" pressed={!on} onEnter={() => void set(false)} testId="home-public-off">Home addresses only</Chip>
      </FocusGroup>
      {on && <p className="acct-small" role="status" data-testid="home-public-seen">{data.home_public_address_seen ? `Your internet address right now: ${data.home_public_address_seen}` : 'Looking up your internet address…'}</p>}
    </GlassPanel>
  )
}
