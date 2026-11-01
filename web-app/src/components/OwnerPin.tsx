import { useCallback, useEffect, useRef, useState } from 'react'
import { ownerPinCall, OwnerPinError, type OwnerChallenge, type OwnerPinResult } from '../api/ownerPin'
import { FocusableInput } from '../nav/Focusable'
import { Button } from './ui'

export function OwnerPin({ targetId, targetName, action, pendingToken, onApproved, onCommitting, onUncertain }: { targetId: string; targetName: string; action: 'login'|'profile_switch'|'admin'; pendingToken?: string; onApproved: (result: OwnerPinResult, signal: AbortSignal) => Promise<void>; onCommitting?: (value:boolean)=>void; onUncertain?:()=>void }) {
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain,setUncertain]=useState(false)
  const [committing,setCommitting]=useState(false)
  const [retryAt, setRetryAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const challenge = useRef<(OwnerChallenge & {expiresAt:number}) | null>(null)
  const live = useRef(true)
  const inFlight = useRef<AbortController | null>(null)
  const approved = useRef(onApproved); approved.current = onApproved
  const callbacks=useRef({onCommitting,onUncertain});callbacks.current={onCommitting,onUncertain}
  const cancel = useCallback((value: OwnerChallenge) => { void ownerPinCall('cancel', {challenge_id:value.challenge_id,poll_token:value.poll_token},pendingToken).catch(()=>{}) },[pendingToken])
  useEffect(()=>{ live.current=true;return()=>{live.current=false;inFlight.current?.abort();if(challenge.current)cancel(challenge.current);challenge.current=null} }, [cancel,targetId,action])
  useEffect(()=>{if(!retryAt)return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer)},[retryAt])
  const submit = async () => {
    if(uncertain || inFlight.current || Date.now()<retryAt || !/^\d{6,12}$/.test(pin))return
    const secret=pin;setPin('');setError('');setBusy(true)
    const controller=new AbortController();inFlight.current=controller
    let timer=0,timedOut=false,submitted=false
    const deadline=()=>{window.clearTimeout(timer);timer=window.setTimeout(()=>{timedOut=true;controller.abort()},30_000)}
    try {
      if(!challenge.current || challenge.current.expiresAt<=Date.now()) {
        if(challenge.current)cancel(challenge.current)
        // A late returned challenge is canceled; an aborted unknown challenge expires.
        // Challenge creation never contains the PIN.
        deadline()
        const value=await ownerPinCall<OwnerChallenge>('start',{profile_id:targetId,action},pendingToken,controller.signal)
        window.clearTimeout(timer)
        if(!live.current || controller.signal.aborted){cancel(value);return}
        challenge.current={...value,expiresAt:Date.now()+value.expires_in*1000}
      }
      const value=challenge.current
      submitted=true;setCommitting(true);if(action!=='admin')callbacks.current.onCommitting?.(true);deadline()
      const result=await ownerPinCall<OwnerPinResult>('verify',{challenge_id:value.challenge_id,poll_token:value.poll_token,pin:secret},pendingToken,controller.signal)
      window.clearTimeout(timer)
      if(!live.current || controller.signal.aborted)return
      challenge.current=null
      await approved.current(result,controller.signal)
    } catch(e) {
      if(live.current && (!controller.signal.aborted || timedOut)) {
        if(action!=='admin' && submitted && (!(e instanceof OwnerPinError) || e.status>=500)){setUncertain(true);setError('Authorization outcome is unknown. Sign in with Google again; this PIN submission will not be retried.');callbacks.current.onUncertain?.();return}
        setError(timedOut?'Challenge request timed out. Try again.':e instanceof Error?e.message:'Owner PIN was not accepted.')
        if(e instanceof OwnerPinError && e.status===429){setRetryAt(e.retryAt);setNow(Date.now())}
        if(e instanceof OwnerPinError && [400,404,410].includes(e.status)){if(challenge.current)cancel(challenge.current);challenge.current=null}
      }
    } finally {window.clearTimeout(timer);callbacks.current.onCommitting?.(false);if(live.current)setCommitting(false);if(inFlight.current===controller){inFlight.current=null;if(live.current){setBusy(false);setPin('')}}}
  }
  const wait=Math.max(0,Math.ceil((retryAt-now)/1000))
  return <div className="flex flex-col gap-3" data-testid="owner-pin">
    <p>{action==='admin'?`Authorize content restrictions for ${targetName}.`:action==='login'?'Google identity confirmed. Enter the owner PIN to finish signing in.':`The owner PIN is required to switch to ${targetName}.`} This approval applies only to this action.</p>
    {!uncertain&&<FocusableInput focusKey="owner-pin:input" label="Owner PIN" type="password" inputMode="numeric" autoComplete="current-password" maxLength={12} readOnly={busy} value={pin} onChange={setPin} onSubmit={()=>void submit()} />}
    <p className="label">Enter your existing 6–12 digit owner PIN privately. Never send it in chat.</p>
    {wait>0 && <p role="status">Too many attempts. Try again in {wait} seconds.</p>}
    {error && <p role="alert">{error}</p>}
    {committing&&<p role="status">Finishing authorization… Please wait for the result.</p>}
    {!uncertain&&<Button focusKey="owner-pin:verify" primary disabled={busy||wait>0||!/^\d{6,12}$/.test(pin)} onEnter={()=>void submit()}>{busy?'Verifying…':'Authorize with owner PIN'}</Button>}
  </div>
}
