import type { components, paths } from './schema'

type S = components['schemas']
export type Profile = S['Profile']
export type Source = S['Source']
export type SourceCreate = S['SourceCreate']
export type Tile = S['Tile']
export type Row = S['Row']
export type TitleDetail = S['TitleDetail']
export type Channel = S['Channel']
export type ChannelGroup = S['ChannelGroup']
export type Programme = S['Programme']
export type SourceBadge = S['SourceBadge']
export type PlaybackDescriptor = S['PlaybackDescriptor']
export type ProgressReport = S['ProgressReport']
export type Settings = S['Settings']
export type Disclaimer = S['Disclaimer']
export type AuthTokens = S['AuthTokens']
export type PlexPin = S['PlexPin']
export type MediaKind = S['MediaKind']
export type Theme = S['Theme']
export type { paths }

export type SearchResult = paths['/search']['get']['responses']['200']['content']['application/json']
export type HomeData = paths['/home']['get']['responses']['200']['content']['application/json']
export type BrowsePage = paths['/browse/{section}']['get']['responses']['200']['content']['application/json']
export type ChannelsResult = paths['/channels']['get']['responses']['200']['content']['application/json']
export type EpgData = paths['/epg']['get']['responses']['200']['content']['application/json']
export type LanguageCount = { code: string; name: string; count: number }
