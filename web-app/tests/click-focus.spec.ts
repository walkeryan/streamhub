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
