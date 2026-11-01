import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

async function fixture(page:Page) {
  await page.addInitScript(()=>{
    const original=window.fetch.bind(window)
    const state=window as unknown as {pendingFixture:{resolves:number;checks:number}}
    state.pendingFixture={resolves:0,checks:0}
    window.fetch=async(input,init)=>{
      const request=new Request(input,init),url=new URL(request.url)
      if(url.pathname==='/api/v1/playback/resolve')state.pendingFixture.resolves++
      if(url.pathname==='/api/v1/playback/options'){
        state.pendingFixture.checks++
        return new Promise<Response>((_,reject)=>{request.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})})
      }
      if(url.pathname==='/api/v1/sports/events') {
        const now=Date.now(),start=new Date(now-60000).toISOString(),stop=new Date(now+3600000).toISOString()
        const events=['First fixture event','Chosen fixture event'].map((title,i)=>({id:`event-${i}`,title,sport:'Soccer',start,stop,status:'current',replay:false,live_evidence:false,grouping_evidence:'same_source_programme',feeds:[{channel_id:`ch:s_xt:${1001+i}`,channel_name:`Fixture feed ${i}`,source_id:'s_xt',source_name:'Fixture IPTV',programme_id:`fixture-${i}`,language:'eng',language_verified:false}]}))
        return Response.json({events,total:2,limit:40,offset:0,sports:[{name:'Soccer',count:2}],coverage:{eligible_channels:2,channels_with_sports_schedule:2},as_of:new Date(now).toISOString(),timezone:'America/New_York',truncated:false})
      }
      return original(request)
    }
  })
  await signIn(page)
}
async function pending(page:Page) {
  await expect(page.getByTestId('player')).toHaveAttribute('data-state','checking-audio')
  await expect(page.getByRole('button',{name:'Cancel playback',exact:true})).toHaveAttribute('data-focused','true')
}
async function noResolve(page:Page) {expect(await page.evaluate(()=>(window as unknown as {pendingFixture:{resolves:number}}).pendingFixture.resolves)).toBe(0)}

test('canceling delayed initial Guide audio returns to the exact Guide view and channel focus without starting media',async({page})=>{
  await fixture(page);await page.goto('/guide')
  const channel=page.getByTestId('guide').getByRole('button',{name:'1 Harbor News 24',exact:true})
  await channel.click();await pending(page);await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live\?view=guide$/)
  await expect(channel).toHaveAttribute('data-focused','true')
  await expect(page.getByRole('button',{name:'Guide',exact:true})).toHaveAttribute('aria-pressed','true')
  await noResolve(page)
})

test('canceling delayed Sports audio restores the selected event instead of the first event',async({page})=>{
  await fixture(page);await page.goto('/sports')
  const event=page.getByRole('button',{name:'View feeds for Chosen fixture event',exact:true})
  await event.click();await page.getByRole('button',{name:'Watch Fixture feed 1',exact:true}).click()
  await pending(page);await page.getByRole('button',{name:'Cancel playback',exact:true}).click()
  await expect(page).toHaveURL(/\/sports$/)
  await expect(event).toHaveAttribute('data-focused','true')
  await noResolve(page)
})

for(const mode of ['direct','reload'] as const) test(`${mode} pending playback has no reusable entry context and safely returns to Live TV`,async({page})=>{
  await fixture(page)
  if(mode==='reload') {
    await page.goto('/guide')
    await page.getByTestId('guide').getByRole('button',{name:'1 Harbor News 24',exact:true}).click()
    await pending(page);await page.reload()
  } else await page.goto('/play/ch%3As_xt%3A1001')
  await pending(page);await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live$/)
  await expect(page.getByRole('button',{name:'Channels',exact:true})).toHaveAttribute('aria-pressed','true')
  await noResolve(page)
})

test('a replacement login cannot reuse the prior session playback entry',async({page})=>{
  await fixture(page);await page.goto('/guide')
  await page.getByTestId('guide').getByRole('button',{name:'1 Harbor News 24',exact:true}).click();await pending(page)
  await page.evaluate(async()=>{const path='/src/store/session.ts';const {useSession}=await import(/* @vite-ignore */path);const current=useSession.getState();current.setSession(current.access,current.refresh,current.profile)})
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live$/)
  await noResolve(page)
})
