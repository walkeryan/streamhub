import { expect, test } from '@playwright/test'

for (const action of ['switch', 'logout', 'failure'] as const) {
  test(`late token refresh cannot undo ${action}`, async ({ page }) => {
    await page.goto('/')
    const result = await page.evaluate(async (action) => {
      const sessionPath = '/src/store/session.ts'
      const clientPath = '/src/api/client.ts'
      const { useSession } = await import(/* @vite-ignore */ sessionPath)
      const { api } = await import(/* @vite-ignore */ clientPath)
      const original = window.fetch
      let release!: (response: Response) => void
      let started!: () => void
      const refreshStarted = new Promise<void>((resolve) => { started = resolve })
      const oldProfile = { id: 'old', name: 'Old' }
      const newProfile = { id: 'new', name: 'New' }
      useSession.getState().setSession('old-access', 'old-refresh', oldProfile)
      window.fetch = async (input, init) => {
        const url = input instanceof Request ? input.url : String(input)
        if (url.endsWith('/themes')) return new Response('{}', { status: 401, headers: { 'content-type': 'application/json' } })
        if (url.endsWith('/auth/refresh')) {
          started()
          return new Promise<Response>((resolve) => { release = resolve })
        }
        return original(input, init)
      }
      try {
        const request = api.GET('/themes')
        await refreshStarted
        if (action === 'logout') useSession.getState().clear()
        else useSession.getState().setSession('new-access', 'new-refresh', newProfile)
        release(new Response(JSON.stringify({ access_token: 'restored-old', refresh_token: 'restored-refresh', profile: oldProfile }), { status: action === 'failure' ? 401 : 200, headers: { 'content-type': 'application/json' } }))
        await request
        return { id: useSession.getState().profile?.id ?? null, access: useSession.getState().access }
      } finally { window.fetch = original }
    }, action)
    expect(result).toEqual(action === 'logout' ? { id: null, access: null } : { id: 'new', access: 'new-access' })
  })
}
