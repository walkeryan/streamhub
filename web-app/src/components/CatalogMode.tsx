import { useGo } from '../nav/back'
import { FocusGroup } from '../nav/Focusable'
import { Chip } from './ui'

export function CatalogMode({section,mode}:{section:string;mode:'discover'|'library'}) {
  const go=useGo()
  const kind=section==='shows'||section==='series'?'shows':'movies'
  return <FocusGroup focusKey="catalog:mode" className="flex gap-2" label="Catalog view">
    <Chip focusKey="catalog:discover" pressed={mode==='discover'} onEnter={()=>{if(mode!=='discover')go(`/discover/${kind}`)}}>Discover</Chip>
    <Chip focusKey="catalog:library" pressed={mode==='library'} onEnter={()=>{if(mode!=='library')go(`/browse/${kind}`)}}>My library</Chip>
  </FocusGroup>
}
