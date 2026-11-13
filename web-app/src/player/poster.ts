/**
 * A transparent picture for a `<video>` to show before its first frame. Without a poster, the
 * browsers built into TVs and Android WebViews draw their own placeholder there: a large grey play
 * button stretched over the whole picture until playback starts. The player draws its own waiting
 * state on top, so the element itself should show nothing.
 */
export const NO_POSTER = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=='
