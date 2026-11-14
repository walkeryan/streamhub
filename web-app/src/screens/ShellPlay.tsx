import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { handOffUrl } from '../shell'

/**
 * `/play/…` reached by an address rather than a press of Play (a link, a reload) inside the Android TV
 * app: handed to the TV's player, and the page goes back to where it was. Never rendered in a browser.
 */
export function ShellPlay() {
  const loc = useLocation()
  const navigate = useNavigate()
  const done = useRef(false)
  useEffect(() => {
    if (done.current) return
    done.current = true
    handOffUrl(loc.pathname + loc.search)
    if (Number(window.history.state?.idx) > 0) navigate(-1)
    else navigate('/', { replace: true })
  }, [loc.pathname, loc.search, navigate])
  return null
}
