import { expect, test, type Locator, type Page } from '@playwright/test'
import { openMore, signIn } from './helpers'

/** The owner, signed in, with recording on. `demo` starts the fake recorder with recordings and rules; `off` is a server without a recordings folder. */
async function owner(page: Page, { demo = false, off = false }: { demo?: boolean; off?: boolean } = {}) {
  await page.addInitScript(([withDemo, turnedOff]) => {
    const profile = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2']))
    if (withDemo) sessionStorage.setItem('mock.recordings.demo', '1')
    if (turnedOff && !sessionStorage.getItem('mock.recordings.on')) sessionStorage.setItem('mock.recordings.off', '1')
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile }))
  }, [demo, off] as const)
}

async function openGuide(page: Page) {
  await page.goto('/live?view=guide')
  await expect(page.getByTestId('guide')).toBeVisible()
  await expect(page.locator('.epg-cell[data-now="true"]').first()).toBeVisible()
}
/** The first programme still to come on a channel, and its title as the guide names it. */
async function upcoming(page: Page, channel: string): Promise<{ cell: Locator; title: string }> {
  const cell = page.locator(`.epg-cell[aria-label^="${channel}: "][data-now="false"][data-past="false"]`).first()
  const label = (await cell.getAttribute('aria-label'))!
  return { cell, title: label.slice(channel.length + 2, label.lastIndexOf(', ')) }
}
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const focused = (page: Page) => page.locator('.fx[data-focused="true"]').last()

test('record a programme from the guide, see it in Upcoming, and cancel it', async ({ page }) => {
  await owner(page)
  await openGuide(page)
  const { cell, title } = await upcoming(page, 'Harbor News 24')
  await cell.click()
  const dialog = page.getByTestId('record-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('heading')).toHaveText(title)
  // Not on the air, so there is nothing to watch yet; a news programme is not offered as a team's game.
  await expect(dialog.getByTestId('record-watch')).toHaveCount(0)
  await expect(dialog.getByTestId('record-team')).toHaveCount(0)
  await expect(dialog.getByTestId('record-keyword')).toHaveCount(0)
  await expect(dialog.getByTestId('record-one')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText(`${title} will be recorded at`)).toBeVisible()
  await expect(cell.getByTestId('recording-badge')).toHaveText('Will record')
  await expect(cell).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('guide-recording')).toHaveText('Will record')

  await openMore(page, 'Recordings')
  await expect(page.getByTestId('recordings-tab-upcoming')).toHaveAttribute('aria-pressed', 'true')
  const row = page.getByTestId('recording-row')
  await expect(row).toHaveCount(1)
  await expect(row).toContainText(title)
  await expect(row).toContainText('Harbor News 24')
  await row.getByTestId('recording-cancel').click()
  await expect(page.getByText(`${title} will not be recorded.`)).toBeVisible()
  await expect(row).toHaveCount(0)
  await expect(page.getByText('Nothing is set to record')).toBeVisible()
  // Nothing is left without focus when the row goes.
  await expect(focused(page)).toHaveCount(1)
})

test('a programme on the air offers Watch now first; recording it starts at once and can be stopped', async ({ page }) => {
  await owner(page)
  await openGuide(page)
  // Keyboard only: the guide opens on what is on now.
  const now = page.locator('.epg-cell[data-now="true"][data-focused="true"]')
  await expect(now).toHaveCount(1)
  await page.keyboard.press('Enter')
  const dialog = page.getByTestId('record-dialog')
  await expect(dialog.getByTestId('record-watch')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(dialog.getByTestId('record-one')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByText(/^Recording .+ now\.$/)).toBeVisible()
  await expect(now.getByTestId('recording-badge')).toHaveText('REC')

  // The same programme again: stop it, or still just watch.
  await page.keyboard.press('Enter')
  await expect(dialog.getByTestId('record-watch')).toHaveAttribute('data-focused', 'true')
  await expect(dialog.getByTestId('record-one')).toHaveCount(0)
  await dialog.getByTestId('record-cancel').click()
  await expect(page.getByText(/^Stopped recording /)).toBeVisible()
  await expect(now.getByTestId('recording-badge')).toHaveCount(0)
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\/ch/)
})

test('rules: every new episode from the guide, then switched off; a game offers its team; keyword is tucked away', async ({ page }) => {
  await owner(page)
  await openGuide(page)
  const { cell, title } = await upcoming(page, 'Harbor News 24')
  const series = title.replace(' (Live)', '')
  await cell.click()
  const dialog = page.getByTestId('record-dialog')
  await expect(dialog.getByTestId('record-series')).toContainText(`${series} on Harbor News 24`)
  await dialog.getByTestId('record-series').click()
  await expect(page.getByText(`Every new episode of ${series} will be recorded.`)).toBeVisible()
  await expect(cell.getByTestId('recording-badge')).toHaveText('Will record')

  // A sports listing: every game for a team, prefilled from the title and editable; keyword only on request.
  const game = await upcoming(page, 'Metro Sports 1')
  await game.cell.click()
  await expect(dialog.getByLabel('Team')).toHaveValue(game.title.replace(' (Live)', ''))
  await expect(dialog.getByTestId('record-keyword')).toHaveCount(0)
  await dialog.getByTestId('record-advanced').click()
  await expect(dialog.getByTestId('record-keyword-go')).toHaveAttribute('aria-disabled', 'true')
  await dialog.getByLabel('Team').fill('Riverton Rovers')
  await dialog.getByTestId('record-team-go').click()
  await expect(page.getByText('Every game for Riverton Rovers will be recorded.')).toBeVisible()

  await openMore(page, 'Recordings')
  await expect(page.getByTestId('recording-row').filter({ hasText: 'From a rule' }).first()).toBeVisible()
  await page.getByTestId('recordings-tab-rules').click()
  const rules = page.getByTestId('rule-row')
  await expect(rules).toHaveCount(2)
  const rule = rules.filter({ hasText: series })
  await expect(rule).toContainText('Every new episode · Harbor News 24 · New only · Keeps all')
  await expect(rules.filter({ hasText: 'Riverton Rovers' })).toContainText('Every game · Any channel')
  await rule.getByTestId('rule-toggle').click()
  await expect(page.getByText('Rule off. Its upcoming recordings were removed.')).toBeVisible()
  await expect(rule.getByTestId('rule-toggle')).toHaveText('Off')
  await expect(rule.getByTestId('rule-toggle')).toHaveAttribute('aria-pressed', 'false')
  await page.getByTestId('recordings-tab-upcoming').click()
  await expect(page.getByTestId('recording-row')).toHaveCount(0)

  // Deleting says what goes with the rule.
  await page.getByTestId('recordings-tab-rules').click()
  await rule.getByTestId('rule-delete').click()
  await expect(page.getByTestId('rule-delete-confirm')).toContainText('also removes the upcoming recordings it scheduled')
  await page.getByTestId('rule-delete-go').click()
  await expect(rules).toHaveCount(1)
})

test('keyboard only: walk Upcoming, Recorded and Rules', async ({ page }) => {
  await owner(page, { demo: true })
  await page.goto('/recordings')
  await expect(page.getByTestId('recordings-tab-upcoming')).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('recording-row')).toHaveCount(3)
  await expect(page.getByTestId('recording-row').first().getByTestId('recording-badge')).toHaveText('Recording')
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveText('Stop recording')
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveText('Cancel')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('recording-row')).toHaveCount(2)
  // The row took its button with it: focus is back on the tab, not lost.
  await expect(page.getByTestId('recordings-tab-upcoming')).toHaveAttribute('data-focused', 'true')

  // Recorded: finished ones play; a failed or missed one says why.
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('recordings-tab-recorded')).toHaveAttribute('aria-pressed', 'true')
  const rows = page.getByTestId('recording-row')
  await expect(rows).toHaveCount(4)
  await expect(rows.filter({ hasText: 'Recording failed' }).getByTestId('recording-reason')).toHaveText('The disk fell below the free space the server keeps.')
  await expect(rows.filter({ hasText: 'Recording missed' }).getByTestId('recording-reason')).toHaveText('No connection to the provider was free for ten minutes.')
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveText('Play')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\/rec%3A/)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('recordings')).toBeVisible()
  await expect(focused(page)).toHaveText('Play')
  // Delete asks first, and Keep is where focus starts.
  await page.keyboard.press('ArrowRight')
  await expect(focused(page)).toHaveText('Delete')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('recording-delete-confirm')).toContainText('its file are deleted from the server')
  await expect(focused(page)).toHaveText('Keep')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(rows).toHaveCount(3)
  await expect(page.getByText(/^Deleted /)).toBeVisible()
  await expect(page.getByTestId('recordings-tab-recorded')).toHaveAttribute('data-focused', 'true')

  // Rules: on/off, edit, delete.
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  const rules = page.getByTestId('rule-row')
  await expect(rules).toHaveCount(2)
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveAttribute('aria-label', 'On: Evening Edition')
  await page.keyboard.press('ArrowRight')
  await expect(focused(page)).toHaveText('Edit')
  await page.keyboard.press('Enter')
  const edit = page.getByTestId('rule-edit-dialog')
  await expect(edit.getByLabel('Match')).toBeFocused()
  await expect(edit.getByLabel('Match')).toHaveValue('Evening Edition')
  await page.keyboard.type(' Late')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Rule saved.')).toBeVisible()
  await expect(rules.first()).toContainText('Evening Edition Late')
  await expect(focused(page)).toHaveText('Edit')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowLeft')
  await expect(focused(page)).toHaveAttribute('aria-label', 'Off: Riverton Rovers')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Rule on: matching programmes are being scheduled.')).toBeVisible()
  await expect(focused(page)).toHaveAttribute('aria-label', 'On: Riverton Rovers')
})

test('the server\'s refusal is shown where the viewer asked', async ({ page }) => {
  await owner(page)
  await openGuide(page)
  const { cell } = await upcoming(page, 'Harbor News 24')
  await cell.click()
  // The recordings folder goes away while the dialog is open.
  await page.evaluate(() => sessionStorage.setItem('mock.recordings.off', '1'))
  await page.getByTestId('record-one').click()
  await expect(page.getByTestId('record-error')).toHaveText('Recording is not set up on this server.')
  await expect(page.getByTestId('record-dialog')).toBeVisible()
  await expect(page.getByTestId('record-one')).toHaveAttribute('data-focused', 'true')
})

test('a recording that cannot be played says what the server said', async ({ page }) => {
  await owner(page, { demo: true })
  await page.goto(`/play/${encodeURIComponent('rec:gone')}`)
  await expect(page.getByTestId('player-error')).toContainText('This recording no longer exists.')
  // A recording has no title page to choose a source on.
  await expect(page.getByTestId('player-error').getByRole('button')).toHaveText(['Try again', 'Back'])
})

test('recording that is not set up: the owner is told why; nobody is offered Record', async ({ page }) => {
  await owner(page, { off: true })
  await page.goto('/')
  await openMore(page, 'Recordings')
  await expect(page.getByTestId('recordings-off')).toContainText('Recording is not set up on this server')
  await expect(page.getByTestId('recordings-tab-upcoming')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open the guide' })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.locator('.epg-cell[data-now="true"]').first()).toBeVisible()
  const { cell, title } = await upcoming(page, 'Harbor News 24')
  await cell.click()
  await expect(page.getByText(`${title} starts at`)).toBeVisible()
  await expect(page.getByTestId('record-dialog')).toHaveCount(0)
})

test('a profile that may not record sees no Recordings entry and the guide as it was', async ({ page }) => {
  await signIn(page)
  await page.getByTestId('nav-more').click()
  await expect(page.getByTestId('more-menu').getByRole('link')).toHaveText(['Sports', 'Requests', 'Settings'])
  await page.keyboard.press('Escape')
  await openGuide(page)
  const { cell, title } = await upcoming(page, 'Harbor News 24')
  await cell.click()
  await expect(page.getByText(`${title} starts at`)).toBeVisible()
  await page.goto('/recordings')
  await expect(page.getByText('Recordings are not available')).toBeVisible()
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('Recordings and the Record dialog fit a phone without sideways scrolling', async ({ page }) => {
    await owner(page, { demo: true })
    await page.goto('/recordings')
    for (const tab of ['upcoming', 'recorded', 'rules'] as const) {
      await page.getByTestId(`recordings-tab-${tab}`).tap()
      await expect(page.getByTestId(tab === 'rules' ? 'rule-row' : 'recording-row').first()).toBeVisible()
      expect(await fits(page)).toBe(true)
    }
    await openGuide(page)
    const { cell } = await upcoming(page, 'Metro Sports 1')
    await cell.tap()
    await expect(page.getByTestId('record-dialog')).toBeVisible()
    await page.getByTestId('record-advanced').tap()
    await expect(page.getByTestId('record-keyword')).toBeVisible()
    expect(await fits(page)).toBe(true)
    const box = (await page.getByTestId('record-dialog').boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)
  })
})
