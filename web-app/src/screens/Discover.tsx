import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router'
import { z } from 'zod'
import { api, unwrap } from '../api/client'
import type { paths } from '../api/types'
import { useProfilePreference } from '../api/profilePreferences'
import { useSources } from '../api/hooks'
import { useSession } from '../store/session'
import { useRestoreFocus } from '../nav/back'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { MediaTile } from '../components/Tile'
import { CatalogPicker } from '../components/CatalogPicker'
import { CatalogMode } from '../components/CatalogMode'
import { Button, Empty, ErrorState, GlassIconButton, Icon, PageHeader, Skeleton } from '../ds'

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
  const query=useQuery({
    queryKey:['discover',profile?.id,profile?.content_restricted,section,page,sort,year,source,preferred],
    enabled:saved.ready&&validYear,staleTime:30_000,
    queryFn:({signal})=>unwrap<DiscoveryPage>(api.GET('/discover/{section}',{signal,params:{path:{section},query:{page,sort,year:year?+year:undefined,source_id:source||undefined,language:preferred}}})),
  })
  const update=(change:Partial<Filters>)=>saved.update(current=>({...current,...change,page:1}))
  useRestoreFocus(saved.ready,'discover:sort:pick')
  const libs=(sources.data??[]).filter(s=>s.enabled&&s.type!=='streaming_service')
  const heading=section==='movies'?'Movies':'Series'
  return <div className="page-pad lib-page" data-testid="discover">
    <PageHeader title={heading} className="lib-head"><CatalogMode section={section} mode="discover" /></PageHeader>
    <p className="muted lib-intro">Discover titles from the metadata catalog, including titles unavailable in your sources. Open a title to review availability and audio.</p>
    {!saved.ready?(saved.error?<div className="ds-empty" role="alert">
      <span className="ds-empty-icon ds-empty-icon-alert" aria-hidden="true"><Icon name="alert"/></span>
      <p className="muted">{saved.error}</p>
      <FocusGroup className="ds-empty-actions"><Button focusKey="discover:prefs-retry" primary icon="refresh" onEnter={()=>void saved.retry()}>Retry saved filters</Button></FocusGroup>
    </div>:<><p className="sr-only" role="status">Loading saved discovery filters…</p><FocusGroup className="ds-empty-actions"><Button focusKey="discover:prefs-retry" ghost onEnter={()=>void saved.retry()}>Retry saved filters</Button></FocusGroup><div className="grid-tiles" aria-hidden="true">{Array.from({length:12},(_,i)=><Skeleton key={i} className="ds-skel-tile" style={{aspectRatio:'2/3'}}/>)}</div></>):<>
      <div className="discover-tools">
        <FocusGroup focusKey="discover:filters" label="Discovery filters" className="lib-toolbar">
          <CatalogPicker scope="discover:sort" label="Sort" value={sort} options={[{id:'popular',label:'Popular'},{id:'rating',label:'Top rated'},{id:'newest',label:'Newest'}]} onChange={value=>update({sort:value as Filters['sort']})}/>
          <div className="discover-year"><FocusableInput focusKey="discover:year" label="Release year" placeholder="Any year" inputMode="numeric" value={year} onChange={value=>update({year:value.replace(/\D/g,'').slice(0,4)})}/></div>
          <GlassIconButton focusKey="discover:reset" icon="refresh" label="Reset discovery filters" tip="Reset filters" onEnter={()=>saved.update(defaults)}/>
        </FocusGroup>
        <FocusGroup focusKey="discover:availability" label="Availability preferences" className="lib-toolbar">
          <CatalogPicker scope="discover:language" label="Availability languages" value={language} options={[{id:'mine',label:'My preferred languages'},{id:'all',label:'All languages'}]} onChange={value=>update({language:value as Filters['language']})}/>
          {!!libs.length&&<CatalogPicker scope="discover:source" label="Availability source" value={source} options={[{id:'',label:'All sources'},...libs.map(s=>({id:s.id,label:s.name}))]} onChange={value=>update({source:value})}/>}
        </FocusGroup>
      </div>
      <p className="label lib-note-line">Language and source preferences affect availability, not which metadata titles are discovered. Audio must be checked before playback.</p>
      {saved.error&&<p className="label" role="alert">{saved.error}</p>}
      {!validYear?<p className="lib-alert" role="alert">Enter a complete release year from 1888 to 2100, or clear the year.</p>:query.error?<ErrorState error={query.error} onRetry={()=>void query.refetch()}/>:!query.data?<div className="grid-tiles" aria-hidden="true">{Array.from({length:12},(_,i)=><Skeleton key={i} className="ds-skel-tile" style={{aspectRatio:'2/3'}}/>)}</div>:<>
        <p className="label lib-note-line" role="status">{query.data.coverage_note}</p>
        {query.data.tiles.length?<FocusGroup focusKey="discover:grid" className="grid-tiles">{query.data.tiles.map(tile=><MediaTile key={tile.id} tile={tile} scope="discover" titleAlways/>)}</FocusGroup>:<Empty icon="film" title="No visible titles on this page" hint="Metadata coverage and this profile’s restrictions may leave a page empty. Try another page or change the filters."/>}
      </>}
      <FocusGroup focusKey="discover:pages" label="Discovery pages" className="lib-pager">
        <GlassIconButton focusKey="discover:previous" icon="chevL" label="Previous page" tipAt="above" disabled={page<=1||query.isFetching||!validYear} onEnter={()=>saved.update(current=>({...current,page:current.page-1}))}/>
        <span className="label lib-pager-text">Page {page} · {query.data?query.data.total===null?'Total unavailable for this profile':`${query.data.total.toLocaleString()} metadata titles`:'Loading metadata'}</span>
        <GlassIconButton focusKey="discover:next" icon="chevR" label="Next page" tipAt="above" disabled={!query.data?.has_more||page>=500||query.isFetching||!validYear} onEnter={()=>saved.update(current=>({...current,page:current.page+1}))}/>
      </FocusGroup>
    </>}
  </div>
}
