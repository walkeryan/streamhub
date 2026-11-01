import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('explicit activation without pointer movement restores its own dialog opener',async({page})=>{
  await signIn(page)
  await page.getByRole('link',{name:'Movies',exact:true}).click()
  const opener=page.getByRole('button',{name:'Sort: Popular',exact:true})
  await expect(opener).toHaveAttribute('data-focused','true')
  await page.evaluate(async()=>{const path='/src/nav/spatial.ts';const {setFocus}=await import(/* @vite-ignore */path);await setFocus('rail:/')})
  await expect(page.getByRole('link',{name:'Home',exact:true})).toHaveAttribute('data-focused','true')
  // Touch and assistive activation need not have a preceding mouse move/hover.
  await opener.dispatchEvent('click')
  await expect(page.getByLabel('Search sort',{exact:true})).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(opener).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Search sort',{exact:true})).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(opener).toHaveAttribute('data-focused','true')
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('absolute mouse movement focuses a target even when movement deltas are zero',async({page})=>{
  await signIn(page)
  await page.getByRole('link',{name:'Movies',exact:true}).click()
  const sort=page.getByRole('button',{name:'Sort: Popular',exact:true})
  await expect(sort).toHaveAttribute('data-focused','true')
  const target=page.getByRole('button',{name:'Search all titles',exact:true})
  await target.dispatchEvent('pointermove',{pointerType:'mouse',clientX:620,clientY:200,movementX:0,movementY:0})
  await expect(target).toHaveAttribute('data-focused','true')
  await page.evaluate(async()=>{const path='/src/nav/spatial.ts';const {setFocus}=await import(/* @vite-ignore */path);await setFocus('discover:sort:pick')})
  await target.dispatchEvent('pointermove',{pointerType:'mouse',clientX:620,clientY:200,movementX:0,movementY:0})
  await expect(sort).toHaveAttribute('data-focused','true')
  await target.hover()
  await expect(target).toHaveAttribute('data-focused','true')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('search')).toBeVisible()
})
