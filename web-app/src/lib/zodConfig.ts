import { z } from 'zod'

// Imported first, for its effect. Zod speeds object parsing up with `new Function` after probing
// whether that is allowed. Under the server's Content-Security-Policy (script-src 'self', no eval)
// the probe is refused: harmless, but the browser reports every page load as a policy violation.
// The schemas here are small; the interpreted path is plenty.
z.config({ jitless: true })
