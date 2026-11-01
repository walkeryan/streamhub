import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

async function fixture(page: Page, mode: 'admin' | 'restricted' | 'member' | 'disabled' | 'restricted-admin' = 'admin') {
  await page.addInitScript(({ mode }) => {
    const original = window.fetch.bind(window)
    const state = window as unknown as { policyCalls: Array<{path:string;body:Record<string,unknown>}>; approveOwner?:boolean; holdSwitch?:boolean; releaseSwitch?:()=>void; expireOwner?:boolean; holdOwner?:boolean; releaseOwner?:()=>void; mismatchedTarget?:boolean; switchReplyReady?:boolean; ownerReplyReady?:boolean }
    state.policyCalls = []
    let accepted: string | null = null
    let authenticated = !!localStorage.getItem('sh.session.v1')
    const decorate = (p: Record<string, unknown>) => ({ ...p, disclaimer_accepted_version: accepted ?? p.disclaimer_accepted_version, is_admin: p.id === 'p2' ? (mode === 'admin' || mode === 'restricted-admin') && authenticated : p.is_admin, content_restricted: p.id === 'p3' || (p.id === 'p2' && (mode === 'restricted' || mode === 'restricted-admin')), has_password: false })
    const token = async (id: string) => {
      await original('/api/v1/disclaimer/accept', {method:'POST',headers:{'content-type':'application/json',authorization:`Bearer mock-access-${id}`},body:JSON.stringify({version:'1.0'})})
      const profiles = await (await original('/api/v1/profiles')).json() as Array<Record<string, unknown>>
      return { access_token: `mock-access-${id}`, refresh_token: `mock-refresh-${id}`, profile: decorate(profiles.find((p) => p.id === id)!) }
    }
    window.fetch = async (input, init) => {
      const req = new Request(input, init), url = new URL(req.url)
      if (url.pathname === '/api/v1/auth/config') return Response.json({ google_enabled:false, google_required:false, content_policy_enabled:mode !== 'disabled',owner_pin_configured:true })
      if (url.pathname === '/api/v1/disclaimer/accept') { accepted = (await req.clone().json()).version; return original(req) }
      if (url.pathname === '/api/v1/profiles' && req.method === 'GET') { const res = await original(req); return Response.json((await res.json() as Array<Record<string,unknown>>).map(decorate)) }
      if (url.pathname === '/api/v1/auth/login') { const res = await original(req); authenticated=true; const data = await res.json(); return Response.json({ ...data, profile: decorate(data.profile) }, {status:res.status}) }
      if (url.pathname.endsWith('/content-policy')) {
        const body = req.method === 'PUT' ? await req.json() : {}
        state.policyCalls.push({path:url.pathname,body})
        return Response.json({ profile_id: url.pathname.split('/')[4], feature_enabled:true, restricted:false, allowed_movie_codes:[], allowed_tv_codes:[], allow_unknown:false, ...body })
      }
      if (url.pathname === '/api/v1/auth/refresh') { const body=await req.json(); return Response.json(await token(String(body.refresh_token).replace('mock-refresh-',''))) }
      if (url.pathname === '/api/v1/auth/profile-switch') {
        const body = await req.json(); state.policyCalls.push({path:url.pathname,body})
        if (state.holdSwitch) await new Promise<void>((resolve) => {state.releaseSwitch = resolve})
        if(mode==='member' && body.pin !== '1234') return Response.json({code:'unauthorized',message:'Wrong credential'},{status:401})
        const data = await token(state.mismatchedTarget ? 'p1' : body.profile_id); state.switchReplyReady=true; return Response.json(data)
      }
      if(url.pathname==='/api/v1/auth/owner-pin/start')return Response.json({challenge_id:'fixture-challenge',poll_token:'fixture-secret',expires_in:300})
      if(url.pathname==='/api/v1/auth/owner-pin/verify')return Response.json({owner_action_token:'fixture-policy-proof',profile_id:'p3',action:'content_policy',expires_in:120})
      if(url.pathname==='/api/v1/auth/owner-pin/cancel')return new Response(null,{status:204})
      return original(req)
    }
  }, { mode })
  await signIn(page)
}
const remote = async (page:Page,key:string) => {
  if (key === 'prof:switch') await expect(page.getByTestId('tab-profiles')).toHaveAttribute('data-focused','true')
  // Wait for the control to exist, not an arbitrary render delay. Modal entry
  // separately waits for its documented initial Cancel focus before any input.
  await expect.poll(()=>page.evaluate(async(key)=>{const path='/src/nav/spatial.ts';const {doesFocusableExist}=await import(/* @vite-ignore */ path);return doesFocusableExist(key)},key)).toBe(true)
  await page.evaluate(async(key)=>{const path='/src/nav/spatial.ts';const {setFocus}=await import(/* @vite-ignore */ path);setFocus(key)},key)
  await page.keyboard.press('Enter')
  if (key === 'prof:switch') await expect(page.getByRole('button',{name:'Cancel',exact:true})).toHaveAttribute('data-focused','true')
}
const calls = (page: Page) => page.evaluate(() => (window as unknown as {policyCalls:Array<{path:string;body:Record<string,unknown>}>}).policyCalls)

 test('disabled capability exposes no policy editor or authenticated-switch controls', async ({ page }) => {
  await fixture(page,'disabled'); await page.goto('/settings?tab=parental')
  await expect(page.getByText('Server-enforced content controls are not enabled.')).toBeVisible()
  await expect(page.getByTestId('content-policies')).toHaveCount(0)
  expect(await calls(page)).toEqual([])
})

test('owner edits a separate target policy with unknown ratings blocked and can discard changes', async ({ page }) => {
  await fixture(page); await page.goto('/settings?tab=parental')
  await page.getByTestId('content-policies').getByRole('button',{name:'Kids',exact:true}).click()
  await expect(page.getByTestId('policy-editor')).toBeVisible()
  await page.getByRole('button',{name:'Restrictions off',exact:true}).click()
  await remote(page,'policy:movie:G')
  await page.keyboard.press('ArrowRight');await page.keyboard.press('Enter')
  await expect(page.getByRole('button',{name:'PG',exact:true})).toHaveAttribute('aria-pressed','true')
  await page.getByRole('button',{name:'TV-PG',exact:true}).click()
  await page.screenshot({path:'/tmp/streamhub-content-policy.png',fullPage:true})
  await page.getByRole('button',{name:'Discard changes',exact:true}).click()
  await expect(page.getByRole('button',{name:'Restrictions off',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Restrictions off',exact:true}).click()
  await page.getByRole('button',{name:'PG',exact:true}).click()
  await page.getByRole('button',{name:'Save restrictions for Kids',exact:true}).click()
  await page.getByLabel('Owner PIN',{exact:true}).fill('824913');await page.getByRole('button',{name:'Authorize with owner PIN',exact:true}).click()
  await expect.poll(async()=> (await calls(page)).find((c)=>c.body.restricted===true)?.body).toEqual({restricted:true,allowed_movie_codes:['PG'],allowed_tv_codes:[],allow_unknown:false})
  await expect(page.getByTestId('nav-profile')).toContainText('Guest')
})

test('non-admin cannot load policy editor; switching to an unrestricted target requires owner PIN', async ({ page }) => {
  await fixture(page,'member'); await page.goto('/settings?tab=parental')
  await expect(page.getByText('Content restrictions are managed by the owner.')).toBeVisible()
  expect(await calls(page)).toEqual([])
  await page.goto('/settings?tab=profiles'); await page.getByTestId('switch-profile').click()
  await page.getByRole('dialog').getByRole('button',{name:'Ryan',exact:true}).click()
  await expect(page.getByLabel('Owner PIN',{exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Continue as Ryan',exact:true})).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByTestId('switch-profile')).toHaveAttribute('data-focused','true')
})

test('owner-authorized switch needs confirmation, clears old cached data and uses no credentials', async ({ page }) => {
  await fixture(page); await page.goto('/settings?tab=profiles')
  await page.getByTestId('switch-profile').click(); await page.getByRole('dialog').getByRole('button',{name:'Kids',exact:true}).click()
  expect((await calls(page)).filter((c)=>c.path.endsWith('/profile-switch'))).toEqual([])
  await page.getByRole('button',{name:'Continue as Kids',exact:true}).click()
  await expect(page.getByTestId('nav-profile')).toContainText('Kids')
  await expect(page).toHaveURL(/\/$/)
  expect((await calls(page)).find((c)=>c.path.endsWith('/profile-switch'))?.body).toEqual({profile_id:'p3'})
  await page.evaluate(async()=>{ const path='/src/store/ui.ts'; const {useUi}=await import(/* @vite-ignore */ path); if(useUi.getState().lastChannelId!==null) throw new Error('Previous profile channel history leaked') })
})

test('canceling an in-flight switch rejects the late response and restores focus', async ({ page }) => {
  await fixture(page); await page.goto('/settings?tab=profiles')
  await page.evaluate(()=>{(window as unknown as {holdSwitch:boolean}).holdSwitch=true})
  await page.getByTestId('switch-profile').click(); await page.getByRole('dialog').getByRole('button',{name:'Kids',exact:true}).click()
  await page.getByRole('button',{name:'Continue as Kids',exact:true}).click()
  await expect.poll(async()=> (await calls(page)).some((c)=>c.path.endsWith('/profile-switch'))).toBe(true)
  await page.keyboard.press('Escape')
  await page.evaluate(()=>{(window as unknown as {releaseSwitch:()=>void}).releaseSwitch()})
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {switchReplyReady:boolean}).switchReplyReady)).toBe(true)
  await page.evaluate(()=>new Promise(requestAnimationFrame))
  await expect(page.getByTestId('nav-profile')).toContainText('Guest')
  await expect(page.getByTestId('switch-profile')).toHaveAttribute('data-focused','true')
})





test('mismatched target tokens are rejected without replacing the current session', async ({page}) => {
  await fixture(page);await page.goto('/settings?tab=profiles')
  await page.evaluate(()=>{(window as unknown as {mismatchedTarget:boolean}).mismatchedTarget=true})
  await remote(page,'prof:switch');await remote(page,'switch:p3');await remote(page,'switch:confirm')
  await expect(page.getByRole('alert')).toContainText('different profile')
  await expect(page.getByTestId('nav-profile')).toContainText('Guest')
})

test('late switch cannot overwrite a newer login to the same profile', async ({page}) => {
  await fixture(page);await page.goto('/settings?tab=profiles')
  await page.evaluate(()=>{(window as unknown as {holdSwitch:boolean}).holdSwitch=true})
  await remote(page,'prof:switch');await remote(page,'switch:p3');await remote(page,'switch:confirm')
  await expect.poll(async()=> (await calls(page)).some(c=>c.path.endsWith('/profile-switch'))).toBe(true)
  await page.evaluate(async()=>{const path='/src/store/session.ts';const {useSession}=await import(/* @vite-ignore */ path);const s=useSession.getState();s.setSession('newer-access','newer-refresh',s.profile);(window as unknown as {releaseSwitch:()=>void}).releaseSwitch()})
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {switchReplyReady:boolean}).switchReplyReady)).toBe(true)
  await expect(page.getByRole('alert')).toContainText('Sign-in changed')
  await expect(page.getByTestId('nav-profile')).toContainText('Guest')
  expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('sh.session.v1')!).access)).toBe('newer-access')
})


test('a restricted admin cannot expose policy or profile-management escape controls', async ({page}) => {
  await fixture(page,'restricted-admin');await page.goto('/settings?tab=parental')
  await expect(page.getByTestId('content-policies')).toHaveCount(0)
  expect(await calls(page)).toEqual([])
  await page.goto('/settings?tab=profiles')
  await expect(page.getByRole('button',{name:'Add profile',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Delete',exact:true})).toHaveCount(0)
  await expect(page.getByLabel('New PIN')).toHaveCount(0)
  await remote(page,'prof:switch');await remote(page,'switch:p1')
  await expect(page.getByLabel('Owner PIN',{exact:true})).toBeVisible()
})


test('owner profiles cannot be restricted through the editor', async ({page}) => {
  await fixture(page);await page.goto('/settings?tab=parental')
  await page.getByTestId('content-policies').getByRole('button',{name:'Ryan',exact:true}).click()
  await expect(page.getByText('Owner profiles cannot be restricted.')).toBeVisible()
  await expect(page.getByTestId('policy-editor')).toHaveCount(0)
  expect(await calls(page)).toEqual([])
})


test('rapid directional input after initial modal focus selects the intended profile without focus theft', async ({page}) => {
  await fixture(page,'restricted');await page.goto('/settings?tab=profiles')
  await remote(page,'prof:switch')
  await page.evaluate(async()=>{const path='/src/nav/spatial.ts';const {setFocus}=await import(/* @vite-ignore */ path);setFocus('switch:p1')})
  await page.keyboard.press('ArrowRight');await page.keyboard.press('Enter')
  await expect(page.getByRole('heading',{name:'Switch to Kids',exact:true})).toBeVisible()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('button',{name:'Kids',exact:true})).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Escape');await expect(page.getByTestId('switch-profile')).toHaveAttribute('data-focused','true')
})


test('refresh cannot replace a profile or resurrect a superseded login generation', async ({page}) => {
  await fixture(page)
  const safe = await page.evaluate(async()=>{const path='/src/store/session.ts';const {useSession}=await import(/* @vite-ignore */ path);const s=useSession.getState();const wrong=s.refreshSession('wrong','wrong',{...s.profile,id:'p1'},s.refresh,s.generation);s.setSession(s.access,s.refresh,s.profile);const late=s.refreshSession('late','late',s.profile,s.refresh,s.generation);return !wrong && !late && useSession.getState().profile.id==='p2' && useSession.getState().access===s.access})
  expect(safe).toBe(true)
})
