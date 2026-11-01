import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

async function setup(page:Page) {
  await page.addInitScript(()=>{
    const original=window.fetch.bind(window)
    window.fetch=async(input,init)=>{
      const request=new Request(input,init),url=new URL(request.url)
      if(!url.pathname.startsWith('/api/v1/discover/'))return original(request)
      const calls=JSON.parse(sessionStorage.getItem('discover.calls')??'[]') as string[]
      calls.push(url.search);sessionStorage.setItem('discover.calls',JSON.stringify(calls))
      const state=window as unknown as {discoveryFail?:boolean}
      if(state.discoveryFail)return Response.json({message:'Metadata service unavailable. Retry shortly.'},{status:502})
      const year=url.searchParams.get('year'),page=Number(url.searchParams.get('page')??1)
      const tile={id:'tmdb:movie:11',kind:'movie',title:'Star Wars',year:1977,sources:[],is_favorite:false}
      return Response.json({tiles:year==='1999'?[]:[{...tile,title:page===1?'Star Wars':'The Empire Strikes Back'}],page,page_size:20,total:year==='1999'?null:40,has_more:page===1,coverage_note:'Metadata catalog coverage. Availability is checked separately.'})
    }
  })
  await signIn(page)
  await page.getByTestId('nav-movies').click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await expect(page.getByTestId('tile')).toHaveCount(1)
}
const requests=(page:Page)=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('discover.calls')??'[]') as string[])

test('primary discovery preserves library routes and unavailable metadata without false Play',async({page})=>{
  await setup(page)
  await expect(page).toHaveURL(/\/discover\/movies$/)
  await expect(page.getByTestId('tile')).toContainText('Not available here')
  await expect(page.getByRole('button',{name:'Play',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:'My library',exact:true}).click()
  await expect(page).toHaveURL(/\/browse\/movies$/)
  await expect(page.getByLabel('Search this library',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:/Genre:/})).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('discover')).toBeVisible()
  await expect(page.getByRole('button',{name:'My library',exact:true})).toHaveAttribute('data-focused','true')
  await page.getByRole('button',{name:'Search all titles',exact:true}).click()
  await expect(page).toHaveURL(/\/search(?:\?|$)/)
  await expect(page.getByTestId('search')).toBeVisible()
})

test('keyboard sort picker has a single focus and restores it on Back; filters survive reload',async({page})=>{
  await setup(page)
  const sort=page.getByRole('button',{name:'Sort: Popular',exact:true})
  await expect(sort).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Enter');await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByLabel('Search sort',{exact:true})).toBeFocused()
  await page.keyboard.press('Escape');await expect(sort).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByLabel('Search sort',{exact:true})).toBeFocused()
  await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('button',{name:'Top rated',exact:true})).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Enter')
  await page.getByLabel('Release year',{exact:true}).fill('1977')
  await expect.poll(async()=> (await requests(page)).at(-1)).toContain('year=1977')
  await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('mock.prefs.p2')??'{}')['discoverFilters.v1.movies']?.year)).toBe('1977')
  await page.reload();await expect(page.getByLabel('Release year',{exact:true})).toHaveValue('1977')
  await expect(page.getByRole('button',{name:'Sort: Top rated',exact:true})).toBeVisible()
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('sparse restricted page does not invent totals or disable available next page',async({page})=>{
  await setup(page);await page.getByLabel('Release year',{exact:true}).fill('1999')
  await expect(page.getByText('No visible titles on this page',{exact:true})).toBeVisible()
  await expect(page.getByText('Page 1 · Total unavailable for this profile',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Next page',exact:true}).click()
  await expect(page.getByText('Page 2 · Total unavailable for this profile',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Next page',exact:true})).toHaveAttribute('aria-disabled','true')
})

test('invalid year avoids metadata requests and service failure offers retry',async({page})=>{
  await setup(page)
  const count=(await requests(page)).length
  await page.getByLabel('Release year',{exact:true}).fill('12')
  await expect(page.getByRole('alert')).toContainText('complete release year')
  expect((await requests(page)).length).toBe(count)
  await page.evaluate(()=>{(window as unknown as {discoveryFail:boolean}).discoveryFail=true})
  await page.getByLabel('Release year',{exact:true}).fill('2001')
  await expect(page.getByText(/Metadata service unavailable/)).toBeVisible()
  await page.evaluate(()=>{(window as unknown as {discoveryFail:boolean}).discoveryFail=false})
  await page.getByRole('button',{name:'Try again',exact:true}).click()
  await expect(page.getByTestId('tile')).toHaveCount(1)
})

test('mobile layout fits and source/language scope never hides metadata',async({page})=>{
  await page.setViewportSize({width:390,height:844});await setup(page)
  await page.getByRole('button',{name:'Availability languages: My preferred languages',exact:true}).click()
  await page.getByRole('button',{name:'All languages',exact:true}).click()
  await expect.poll(async()=> (await requests(page)).at(-1)).toContain('language=all')
  await expect(page.getByTestId('tile')).toHaveCount(1)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:'/tmp/streamhub-discover-mobile.png',fullPage:true})
})

test('profile change uses its own saved discovery filters and never reuses the prior profile year',async({page})=>{
  await setup(page);await page.getByLabel('Release year',{exact:true}).fill('1977')
  await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('mock.prefs.p2')??'{}')['discoverFilters.v1.movies']?.year)).toBe('1977')
  await page.evaluate(async()=>{
    const path='/src/store/session.ts';const {useSession}=await import(/* @vite-ignore */ path)
    await fetch('/api/v1/disclaimer/accept',{method:'POST',headers:{authorization:'Bearer mock-access-p1'}})
    const profiles=await (await fetch('/api/v1/profiles')).json()
    const owner=profiles.find((p:{id:string})=>p.id==='p1')
    sessionStorage.setItem('mock.prefs.p1',JSON.stringify({'discoverFilters.v1.movies':{sort:'newest',year:'2005',page:1,source:'',language:'all'}}))
    useSession.getState().setSession('mock-access-p1','mock-refresh-p1',{...owner,disclaimer_accepted_version:'1.0'})
  })
  await expect(page.getByLabel('Release year',{exact:true})).toHaveValue('2005')
  await expect(page.getByRole('button',{name:'Sort: Newest',exact:true})).toBeVisible()
  await expect.poll(async()=> (await requests(page)).at(-1)).toContain('year=2005')
})
