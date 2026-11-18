import type { EpisodeTile, TitleDetail } from '../../api/types'

export type { EpisodeTile, Tile, TitleActivityEntry } from '../../api/types'
export type CastMember = NonNullable<TitleDetail['cast']>[number]
export type Season = EpisodeTile
