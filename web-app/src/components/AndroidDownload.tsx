import { Button } from './ui'

export const APK_FILENAME = 'streamhub-tv-debug-d99151e.apk'
export const APK_URL = `/downloads/${APK_FILENAME}`

export function AndroidDownload({ focusKey }: { focusKey: string }) {
  const download = () => {
    const link = document.createElement('a')
    link.href = APK_URL
    link.download = APK_FILENAME
    document.body.appendChild(link)
    link.click()
    link.remove()
  }
  return <Button focusKey={focusKey} onEnter={download} testId="android-download">Download Android APK</Button>
}
