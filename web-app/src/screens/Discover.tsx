import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { z } from 'zod'
import { api, unwrap } from '../api/client'
import type { paths } from '../api/types'
import { useProfilePreference } from '../api/profilePreferences'
import { useSources } from '../api/hooks'
import { useSession } from '../store/session'
import { useGo, useRestoreFocus } from '../nav/back'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { MediaTile } from '../components/Tile'
import { CatalogPicker } from '../components/CatalogPicker'
import { CatalogMode } from '../components/CatalogMode'
import { Button, ErrorState, Empty, Skeleton } from '../components/ui'

type DiscoveryPage=paths['/discover/{section}']['get']['responses']['200']['content']['application/json']
const schema=z.object({sort:z.enum(['popular','rating','newest']),year:z.string().regex(/^\d{0,4}$/),page:z.number().int().min(1).max(500),source:z.string().max(256),language:z.enum(['mine','all'])})
type Filters=z.infer<typeof schema>
const validate=(raw:unknown)=>{const result=schema.safeParse(raw);return result.success?result.data:null}
const defaults:Filters={sort:'popular',year:'',page:1,source:'',language:'mine'}

export function Discover() {
  const section=useParams().section==='shows'?'shows':'movies'
  const profile=useSession(s=>s.profile)
  const saved=useProfilePreference(`discoverFilters.v1.${section}`,defaults,validate)
  const {sort,year,page,source,language}=saved.value
  const validYear=year===''||(/^\d{4}$/.test(year)&&+year>=1888&&+year<=2100)
  const preferred=language==='mine'?profile?.languages.join(',')||'all':'all'
  const sources=useSources()
  const go=useGo()
  const query=useQuery({
    queryKey:['discover',profile?.id,profile?.content_restricted,section,page,sort,year,source,preferred],
    enabled:saved.ready&&validYear,staleTime:30_000,
    queryFn:({signal})=>unwrap<DiscoveryPage>(api.GET('/discover/{section}',{signal,params:{path:{section},query:{page,sort,year:year?+year:undefined,source_id:source||undefined,language:preferred}}})),
  })
  const update=(change:Partial<Filters>)=>saved.update(current=>({...current,...change,page:1}))
  useRestoreFocus(saved.ready,'discover:sort:pick')
  const libs=(sources.data??[]).filter(s=>s.enabled&&s.type!=='streaming_service')
  return <div className="page-pad" data-testid="discover">
    <div className="flex gap-3 flex-wrap items-baseline mb-3"><h1 className="display">{section==='movies'?'Movies':'Series'}</h1><CatalogMode section={section} mode="discover" /></div>
    <p className="label mb-3">Discover titles from the metadata catalog, including titles unavailable in your sources. Open a title to review availability and audio.</p>
    {!saved.ready?<>{saved.error?<p role="alert">{saved.error}</p>:<p role="status">Loading saved discovery filters…</p>}<Button focusKey="discover:prefs-retry" onEnter={()=>void saved.retry()}>Retry saved filters</Button></>:<>
      <FocusGroup focusKey="discover:filters" label="Discovery filters" className="flex flex-wrap gap-3 items-center mb-3">
        <CatalogPicker scope="discover:sort" label="Sort" value={sort} options={[{id:'popular',label:'Popular'},{id:'rating',label:'Top rated'},{id:'newest',label:'Newest'}]} onChange={value=>update({sort:value as Filters['sort']})}/>
        <div style={{width:'min(12rem,100%)'}}><FocusableInput focusKey="discover:year" label="Release year" placeholder="Any year" inputMode="numeric" value={year} onChange={value=>update({year:value.replace(/\D/g,'').slice(0,4)})}/></div>
        <Button focusKey="discover:search" onEnter={()=>go('/search')}>Search all titles</Button>
        <Button focusKey="discover:reset" ghost onEnter={()=>saved.update(defaults)}>Reset discovery filters</Button>
      </FocusGroup>
      <FocusGroup focusKey="discover:availability" label="Availability preferences" className="flex flex-wrap gap-3 items-center mb-3">
        <CatalogPicker scope="discover:language" label="Availability languages" value={language} options={[{id:'mine',label:'My preferred languages'},{id:'all',label:'All languages'}]} onChange={value=>update({language:value as Filters['language']})}/>
        {!!libs.length&&<CatalogPicker scope="discover:source" label="Availability source" value={source} options={[{id:'',label:'All sources'},...libs.map(s=>({id:s.id,label:s.name}))]} onChange={value=>update({source:value})}/>}
      </FocusGroup>
      <p className="label mb-3">Language and source preferences affect availability, not which metadata titles are discovered. Audio must be checked before playback.</p>
      {saved.error&&<p role="alert">{saved.error}</p>}
      {!validYear?<p role="alert">Enter a complete release year from 1888 to 2100, or clear the year.</p>:query.error?<ErrorState error={query.error} onRetry={()=>void query.refetch()}/>:!query.data?<div className="grid-tiles">{Array.from({length:12},(_,i)=><Skeleton key={i} style={{aspectRatio:'2/3'}}/>)}</div>:<>
        <p className="label mb-3" role="status">{query.data.coverage_note}</p>
        {query.data.tiles.length?<FocusGroup focusKey="discover:grid" className="grid-tiles">{query.data.tiles.map(tile=><MediaTile key={tile.id} tile={tile} scope="discover" titleAlways/>)}</FocusGroup>:<Empty title="No visible titles on this page" hint="Metadata coverage and this profile’s restrictions may leave a page empty. Try another page or change the filters."/>}
      </>}
      <FocusGroup focusKey="discover:pages" label="Discovery pages" className="flex flex-wrap gap-3 items-center py-4">
        <Button focusKey="discover:previous" disabled={page<=1||query.isFetching||!validYear} onEnter={()=>saved.update(current=>({...current,page:current.page-1}))}>Previous page</Button>
        <span className="label">Page {page} · {query.data?query.data.total===null?'Total unavailable for this profile':`${query.data.total.toLocaleString()} metadata titles`:'Loading metadata'}</span>
        <Button focusKey="discover:next" disabled={!query.data?.has_more||page>=500||query.isFetching||!validYear} onEnter={()=>saved.update(current=>({...current,page:current.page+1}))}>Next page</Button>
      </FocusGroup>
    </>}
  </div>
}
