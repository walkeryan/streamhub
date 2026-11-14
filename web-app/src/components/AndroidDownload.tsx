import type { TvAppRelease } from '../api/types'
import { Button } from './ui'

/** Downloads the build the server publishes (its tv-release.json), under the file's own name. */
export function AndroidDownload({ focusKey, release }: { focusKey: string; release: TvAppRelease }) {
  const download = () => {
    const link = document.createElement('a')
    link.href = release.apk_url
    link.download = release.apk_url.split('/').pop() ?? 'streamhub-tv.apk'
    document.body.appendChild(link)
    link.click()
    link.remove()
  }
  return <Button focusKey={focusKey} onEnter={download} testId="android-download">Download Android APK</Button>
}
