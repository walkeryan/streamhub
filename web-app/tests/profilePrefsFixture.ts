import type { Page } from '@playwright/test'

export type Document = Record<string, unknown>
export async function backend(page: Page, documents: Record<string, Document>, beforeRequest?: (id: string, method: string) => Promise<void>) {
  await page.exposeFunction('prefsBackend', async (id: string, method: string, body: Document | null) => {
    await beforeRequest?.(id, method)
    if (method === 'IMPORT') documents[id] = { ...body, ...documents[id] }
    else if (method === 'PATCH') documents[id] = { ...documents[id], ...body }
    else if (method === 'PUT') documents[id] = body!
    return documents[id] ?? {}
  })
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      // A language row is offered only to a catalog with more than one language.
      if (new URL(req.url).pathname === '/api/v1/browse/movies/languages') return Response.json([{ code: 'eng', name: 'English', count: 10 }, { code: 'spa', name: 'Spanish', count: 2 }])
      const match = new URL(req.url).pathname.match(/^\/api\/v1\/profiles\/([^/]+)\/prefs$/)
      if (match) {
        const state = window as unknown as { prefsBackend: (id: string, method: string, body: Record<string, unknown> | null) => Promise<Record<string, unknown>> }
        return Response.json(await state.prefsBackend(match[1], new URL(req.url).searchParams.get('only_missing') === 'true' ? 'IMPORT' : req.method, req.method !== 'GET' ? await req.json() : null))
      }
      return original(req)
    }
  })
}
