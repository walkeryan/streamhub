import { expect, test, type Page } from '@playwright/test'

const FIXTURE_PIN='824913'
async function boot(page:Page,mode:'login'|'setup'|'restricted'|'admin'|'member'='login') {
  await page.addInitScript(({mode})=>{
    const profiles=[{id:'p1',name:'Owner',is_admin:true,theme_id:'midnight',languages:['eng'],has_pin:false,has_password:false,kids:false,content_restricted:false,disclaimer_accepted_version:'1.0'},{id:'p3',name:'Kids',is_admin:false,theme_id:'midnight',languages:['eng'],has_pin:true,has_password:false,kids:true,content_restricted:true,disclaimer_accepted_version:'1.0'},{id:'p2',name:'Guest',is_admin:false,theme_id:'midnight',languages:['eng'],has_pin:false,has_password:false,kids:false,content_restricted:false,disclaimer_accepted_version:'1.0'}]
    const s=window as unknown as {pinFixture:{calls:Array<{path:string;body:Record<string,unknown>;bearer:string;proof?:string|null}>;localApproval:boolean;recoveryApproval:boolean;rateOnce:boolean;expireOnce:boolean;holdStart:boolean;releaseStart?:()=>void;holdVerify:boolean;releaseVerify?:()=>void;wrongTarget:boolean;wrongAction:boolean;replyReady:boolean}}
    s.pinFixture={calls:[],localApproval:false,recoveryApproval:false,rateOnce:false,expireOnce:false,holdStart:false,holdVerify:false,wrongTarget:false,wrongAction:false,replyReady:false}
    const f=s.pinFixture
    sessionStorage.setItem('mock.accepted',JSON.stringify(['p1','p3','p2']))
    if(mode==='admin'||mode==='restricted'||mode==='member') {const p=profiles[mode==='admin'?0:mode==='restricted'?1:2];localStorage.setItem('sh.session.v1',JSON.stringify({access:`mock-access-${p.id}`,refresh:`mock-refresh-${p.id}`,profile:p}))}else localStorage.removeItem('sh.session.v1')
    const original=window.fetch.bind(window)
    let configured=mode!=='setup',sequence=0
    const challenges=new Map<string,{profile_id:string;action:string}>()
    const tokens=(id:string)=>({access_token:`mock-access-${id}`,refresh_token:`mock-refresh-${id}`,profile:profiles.find(p=>p.id===id)!})
    window.fetch=async(input,init)=>{
      const req=new Request(input,init),url=new URL(req.url)
      if(url.pathname==='/api/v1/auth/config')return Response.json({google_enabled:true,google_required:true,content_policy_enabled:true,owner_pin_configured:configured,owner_pin_setup_required:!configured})
      if(url.pathname==='/api/v1/auth/google/complete')return Response.json({owner_pin_required:true,pending_token:'fixture-pending-capability',profile:profiles[0],setup_required:!configured})
      if(url.pathname==='/api/v1/profiles'&&req.method==='GET')return Response.json(profiles)
      if(url.pathname==='/api/v1/auth/refresh') {const body=await req.json();return Response.json(tokens(String(body.refresh_token).replace('mock-refresh-','')))}
      if(url.pathname.includes('/content-policy')) {
        const body=req.method==='PUT'?await req.json():{}
        f.calls.push({path:url.pathname,body,bearer:req.headers.get('authorization')??'',proof:req.headers.get('x-owner-action')})
        if(req.method==='PUT'&&req.headers.get('x-owner-action')!=='fixture-action-p3')return Response.json({code:'owner_pin_required',message:'Owner action required'},{status:403})
        return Response.json({profile_id:'p3',feature_enabled:true,restricted:false,allowed_movie_codes:[],allowed_tv_codes:[],allow_unknown:false,...body})
      }
      if(url.pathname.startsWith('/api/v1/auth/owner-pin/')) {
        const method=url.pathname.split('/').at(-1)!,body=req.method==='POST'?await req.json():{}
        const redacted={...body};for(const key of ['pin','poll_token','grant_secret'])if(key in redacted)redacted[key]='[redacted]'
        f.calls.push({path:method,body:redacted,bearer:req.headers.get('authorization')??''})
        if(method==='setup-status')return Response.json({configured,local_approval_required:!f.localApproval,setup_authorized:f.localApproval,setup_request_id:'LOCAL-REQ-FIXTURE',...(f.localApproval?{grant_id:'fixture-grant',grant_secret:'fixture-grant-secret'}:{})})
        if(method==='recovery-status')return Response.json({configured:true,local_approval_required:!f.recoveryApproval,recovery_authorized:f.recoveryApproval,recovery_request_id:'RECOVERY-REQ-FIXTURE',...(f.recoveryApproval?{grant_id:'fixture-recovery',grant_secret:'fixture-recovery-secret'}:{})})
        if(method==='setup'||method==='recover') {
          if(!(method==='setup'?f.localApproval:f.recoveryApproval))return Response.json({message:'Local approval required'},{status:403})
          configured=true;return Response.json(tokens('p1'))
        }
        if(method==='start'){if(f.holdStart)await new Promise<void>(resolve=>{f.releaseStart=resolve});const id=`challenge-${++sequence}`;challenges.set(id,body);return Response.json({challenge_id:id,poll_token:'fixture-challenge-secret',expires_in:300})}
        if(method==='cancel')return new Response(null,{status:204})
        if(method==='verify') {
          if(f.rateOnce){f.rateOnce=false;return Response.json({code:'rate_limited',message:'Too many owner PIN attempts.'},{status:429,headers:{'Retry-After':'3'}})}
          if(f.expireOnce){f.expireOnce=false;return Response.json({code:'owner_pin_expired',message:'Owner PIN challenge expired.'},{status:410})}
          if(body.pin!=='824913')return Response.json({code:'owner_pin_invalid',message:'Owner PIN was not accepted.'},{status:403})
          if(f.holdVerify)await new Promise<void>((resolve,reject)=>{f.releaseVerify=resolve;req.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})})
          const target=challenges.get(body.challenge_id)!
          f.replyReady=true
          if(target.action==='admin')return Response.json({owner_action_token:'fixture-action-p3',profile_id:f.wrongAction?'p1':target.profile_id,action:'content_policy',expires_in:120})
          return Response.json(tokens(f.wrongTarget?'p3':target.profile_id))
        }
      }
      return original(req)
    }
  },{mode})
  await page.goto(mode==='login'||mode==='setup'?'/?google_complete=1':mode==='admin'?'/settings?tab=parental':'/settings?tab=profiles')
}
const calls=(page:Page)=>page.evaluate(()=>(window as unknown as {pinFixture:{calls:Array<{path:string;body:Record<string,unknown>;bearer:string;proof?:string|null}>}}).pinFixture.calls)
const enterPin=async(page:Page,pin=FIXTURE_PIN)=>{await page.getByLabel('Owner PIN',{exact:true}).fill(pin);await page.getByRole('button',{name:'Authorize with owner PIN',exact:true}).click()}
async function restrictedDialog(page:Page){await boot(page,'restricted');await page.getByTestId('switch-profile').click();await page.getByRole('dialog').getByRole('button',{name:'Owner',exact:true}).click();await expect(page.getByLabel('Owner PIN',{exact:true})).toBeVisible()}

test('retained Google identity cannot become an owner session before private PIN verification',async({page})=>{
  await boot(page)
  await expect(page.getByTestId('pending-owner-login')).toBeVisible()
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
  await expect(page.getByTestId('home')).toHaveCount(0)
  await expect(page.getByLabel('Owner PIN',{exact:true})).toHaveAttribute('autocomplete','current-password')
  await enterPin(page)
  await expect(page.getByTestId('nav-profile')).toContainText('Owner')
  expect((await calls(page)).find(c=>c.path==='start')?.body).toEqual({profile_id:'p1',action:'login'})
  expect((await calls(page)).find(c=>c.path==='verify')?.bearer).toBe('Bearer fixture-pending-capability')
  const stored=await page.evaluate(()=>JSON.stringify(localStorage)+JSON.stringify(sessionStorage))
  for(const secret of [FIXTURE_PIN,'fixture-pending-capability','fixture-challenge-secret'])expect(stored).not.toContain(secret)
})

test('initial setup cannot accept a PIN until the exact local VM approval arrives',async({page})=>{
  await boot(page,'setup');await expect(page.getByText('LOCAL-REQ-FIXTURE')).toBeVisible()
  await expect(page.getByLabel('New owner PIN',{exact:true})).toHaveCount(0)
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
  await page.evaluate(()=>{(window as unknown as {pinFixture:{localApproval:boolean}}).pinFixture.localApproval=true})
  await page.getByRole('button',{name:'Check local approval',exact:true}).click()
  await page.getByLabel('New owner PIN',{exact:true}).fill(FIXTURE_PIN)
  await page.getByLabel('Confirm new owner PIN',{exact:true}).fill('000000')
  await expect(page.getByRole('button',{name:'Set owner PIN',exact:true})).toHaveAttribute('aria-disabled','true')
  await page.getByLabel('Confirm new owner PIN',{exact:true}).fill(FIXTURE_PIN)
  await expect(page.getByLabel('New owner PIN',{exact:true})).toHaveAttribute('autocomplete','new-password')
  await page.getByRole('button',{name:'Set owner PIN',exact:true}).click()
  await expect(page.getByTestId('nav-profile')).toContainText('Owner')
  expect((await calls(page)).filter(c=>c.path==='setup')).toHaveLength(1)
  expect(await page.evaluate(()=>JSON.stringify(localStorage)+JSON.stringify(sessionStorage))).not.toMatch(/824913|fixture-grant-secret|fixture-pending-capability/)
})

test('forgotten PIN recovery uses separate locally approved recovery, never SSO reset',async({page})=>{
  await boot(page);await page.getByRole('button',{name:'Owner PIN setup or recovery',exact:true}).click()
  await expect(page.getByText('RECOVERY-REQ-FIXTURE')).toBeVisible()
  await expect(page.getByText(/existing PIN keeps working/)).toBeVisible()
  await expect(page.getByLabel('New owner PIN',{exact:true})).toHaveCount(0)
  expect((await calls(page)).some(c=>['setup','recover'].includes(c.path))).toBe(false)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{recoveryApproval:boolean}}).pinFixture.recoveryApproval=true})
  await page.getByRole('button',{name:'Check local approval',exact:true}).click()
  await page.getByLabel('New owner PIN',{exact:true}).fill(FIXTURE_PIN);await page.getByLabel('Confirm new owner PIN',{exact:true}).fill(FIXTURE_PIN)
  await page.getByRole('button',{name:'Replace owner PIN',exact:true}).click()
  await expect(page.getByTestId('nav-profile')).toContainText('Owner')
  expect((await calls(page)).filter(c=>c.path==='recover')).toHaveLength(1)
  expect((await calls(page)).filter(c=>c.path==='setup')).toHaveLength(0)
})

test('wrong PIN is submitted once, cleared, and Cancel preserves the restricted profile',async({page})=>{
  await restrictedDialog(page);await enterPin(page,'000000')
  await expect(page.getByRole('alert')).toContainText('not accepted')
  await expect(page.getByLabel('Owner PIN',{exact:true})).toHaveValue('')
  expect((await calls(page)).filter(c=>c.path==='verify')).toHaveLength(1)
  await page.keyboard.press('Escape');await expect(page.getByTestId('nav-profile')).toContainText('Kids')
  await expect(page.getByTestId('switch-profile')).toHaveAttribute('data-focused','true')
  await expect.poll(async()=> (await calls(page)).some(c=>c.path==='cancel')).toBe(true)
})

test('429 Retry-After blocks repeats until cooldown and challenge expiry requires a fresh challenge',async({page})=>{
  await boot(page);await page.evaluate(()=>{(window as unknown as {pinFixture:{rateOnce:boolean}}).pinFixture.rateOnce=true})
  await page.clock.install();await page.clock.pauseAt(new Date(Date.now()+60000))
  await enterPin(page);await expect(page.getByText(/Try again in 3 seconds/)).toBeVisible()
  await page.getByLabel('Owner PIN',{exact:true}).fill(FIXTURE_PIN)
  await page.getByLabel('Owner PIN',{exact:true}).press('Enter');await page.getByLabel('Owner PIN',{exact:true}).press('Enter')
  expect((await calls(page)).filter(c=>c.path==='verify')).toHaveLength(1)
  await page.clock.runFor(3000)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{expireOnce:boolean}}).pinFixture.expireOnce=true})
  await page.getByRole('button',{name:'Authorize with owner PIN',exact:true}).click();await expect(page.getByRole('alert')).toContainText('expired')
  await enterPin(page)
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('sh.session.v1')??'null')?.profile?.id)).toBe('p1')
  // Cooldown assertions are complete; let unrelated MSW source-loading timers run.
  await page.clock.resume()
  await expect(page.getByTestId('nav-profile')).toContainText('Owner')
  expect((await calls(page)).filter(c=>c.path==='start')).toHaveLength(2)
})

test('repeated Select sends one verification and Back cannot cancel a committing session switch',async({page})=>{
  await restrictedDialog(page);await page.evaluate(()=>{(window as unknown as {pinFixture:{holdVerify:boolean}}).pinFixture.holdVerify=true})
  await page.getByLabel('Owner PIN',{exact:true}).fill(FIXTURE_PIN)
  await page.getByLabel('Owner PIN',{exact:true}).press('Enter');await page.getByLabel('Owner PIN',{exact:true}).press('Enter')
  await expect.poll(async()=> (await calls(page)).filter(c=>c.path==='verify').length).toBe(1)
  await expect(page.getByText(/Finishing authorization/)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.evaluate(()=>{(window as unknown as {pinFixture:{releaseVerify:()=>void}}).pinFixture.releaseVerify()})
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {pinFixture:{replyReady:boolean}}).pinFixture.replyReady)).toBe(true)
  await expect(page.getByTestId('nav-profile')).toContainText('Owner')
})

test('each policy save needs a target-bound action proof; wrong-target proof causes no write',async({page})=>{
  await boot(page,'admin');await page.getByTestId('content-policies').getByRole('button',{name:'Kids',exact:true}).click()
  await page.getByRole('button',{name:'Restrictions off',exact:true}).click();await page.getByRole('button',{name:'PG',exact:true}).click()
  await page.getByRole('button',{name:'Save restrictions for Kids',exact:true}).click()
  expect((await calls(page)).filter(c=>c.proof)).toHaveLength(0)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{wrongAction:boolean}}).pinFixture.wrongAction=true})
  await enterPin(page);await expect(page.getByRole('alert')).toContainText('does not match')
  expect((await calls(page)).filter(c=>c.proof)).toHaveLength(0)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{wrongAction:boolean}}).pinFixture.wrongAction=false})
  await enterPin(page)
  await expect.poll(async()=> (await calls(page)).filter(c=>c.proof).length).toBe(1)
  const saved=(await calls(page)).find(c=>c.proof)!
  expect(saved.proof).toBe('fixture-action-p3');expect(saved.body).toEqual({restricted:true,allowed_movie_codes:['PG'],allowed_tv_codes:[],allow_unknown:false})
  expect((await calls(page)).filter(c=>c.path==='start').every(c=>c.body.action==='admin'&&c.body.profile_id==='p3')).toBe(true)
})

test('mismatched login result never installs an owner session, including after cleared storage',async({page})=>{
  await boot(page);await page.evaluate(()=>{(window as unknown as {pinFixture:{wrongTarget:boolean}}).pinFixture.wrongTarget=true})
  await enterPin(page);await expect(page.getByRole('alert')).toContainText('unknown')
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
  await page.goto('/?google_complete=1');await expect(page.getByTestId('pending-owner-login')).toBeVisible()
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
})


test('an unrestricted member also needs owner PIN to enter a restricted target',async({page})=>{
  await boot(page,'member');await page.getByTestId('switch-profile').click();await page.getByRole('dialog').getByRole('button',{name:'Kids',exact:true}).click()
  await expect(page.getByLabel('Owner PIN',{exact:true})).toBeVisible()
  await expect(page.getByLabel('Existing profile PIN',{exact:true})).toHaveCount(0)
  await enterPin(page);await expect(page.getByTestId('nav-profile')).toContainText('Kids')
  expect((await calls(page)).find(c=>c.path==='start')?.body).toEqual({profile_id:'p3',action:'profile_switch'})
})

 test('a timed-out submitted PIN returns to controlled sign-in without retry or late session acceptance',async({page})=>{
  await restrictedDialog(page)
  await page.clock.install();await page.clock.pauseAt(new Date(Date.now()+60000))
  await page.evaluate(()=>{(window as unknown as {pinFixture:{holdVerify:boolean}}).pinFixture.holdVerify=true})
  await enterPin(page);await expect(page.getByText(/Finishing authorization/)).toBeVisible()
  await page.clock.runFor(30001)
  await expect(page.getByRole('alert')).toContainText('unknown')
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
  expect((await calls(page)).filter(c=>c.path==='verify')).toHaveLength(1)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{releaseVerify:()=>void}}).pinFixture.releaseVerify()})
  await expect(page.getByTestId('nav-profile')).toHaveCount(0)
  expect(await page.evaluate(()=>localStorage.getItem('sh.session.v1'))).toBeNull()
 })

test('Back before PIN dispatch cancels the pending challenge and never sends the PIN',async({page})=>{
  await restrictedDialog(page)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{holdStart:boolean}}).pinFixture.holdStart=true})
  await enterPin(page)
  await expect.poll(async()=> (await calls(page)).filter(c=>c.path==='start').length).toBe(1)
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.evaluate(()=>{(window as unknown as {pinFixture:{releaseStart:()=>void}}).pinFixture.releaseStart()})
  await expect.poll(async()=> (await calls(page)).filter(c=>c.path==='cancel').length).toBe(1)
  expect((await calls(page)).filter(c=>c.path==='verify')).toHaveLength(0)
  await expect(page.getByTestId('nav-profile')).toContainText('Kids')
})
