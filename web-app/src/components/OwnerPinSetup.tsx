import { useCallback, useEffect, useRef, useState } from 'react'
import { ownerPinCall, OwnerPinError, isAuthTokens, type OwnerPinResult, type SetupStatus } from '../api/ownerPin'
import type { AuthTokens } from '../api/types'
import { FocusableInput } from '../nav/Focusable'
import { Button } from './ui'

export function OwnerPinSetup({pendingToken,onApproved,recovery=false,onCommitting,onUncertain}:{pendingToken?:string;recovery?:boolean;onCommitting?:(value:boolean)=>void;onUncertain?:()=>void;onApproved:(tokens:AuthTokens,signal:AbortSignal)=>Promise<void>}) {
  const [status,setStatus]=useState<SetupStatus|null>(null)
  const [pin,setPin]=useState(''),[confirm,setConfirm]=useState('')
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const [retryAt,setRetryAt]=useState(0),[now,setNow]=useState(Date.now())
  const request=useRef<AbortController|null>(null)
  const live=useRef(true)
  const callbacks=useRef({onCommitting,onUncertain});callbacks.current={onCommitting,onUncertain}
  const check=useCallback(async()=>{
    if(request.current || Date.now()<retryAt)return
    const controller=new AbortController();request.current=controller;setBusy(true);setError('')
    try {const value=await ownerPinCall<SetupStatus>(recovery?'recovery-status':'setup-status',undefined,pendingToken,controller.signal);if(!controller.signal.aborted)setStatus(value)}
    catch(e){if(!controller.signal.aborted){setError(e instanceof Error?e.message:'Local approval status is unavailable.');if(e instanceof OwnerPinError && e.status===429){setRetryAt(e.retryAt);setNow(Date.now())}}}
    finally{if(request.current===controller){request.current=null;if(live.current)setBusy(false)}}
  },[pendingToken,retryAt,recovery])
  useEffect(()=>{live.current=true;void check();return()=>{live.current=false;request.current?.abort();request.current=null}},[check])
  useEffect(()=>{if(!retryAt)return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(timer)},[retryAt])
  const submit=async()=>{
    if(!status || request.current || Date.now()<retryAt || !(recovery?status?.recovery_authorized:status?.setup_authorized) || !status.grant_id || !status.grant_secret || !/^\d{6,12}$/.test(pin) || pin!==confirm)return
    const body={grant_id:status.grant_id,grant_secret:status.grant_secret,pin}
    setPin('');setConfirm('');setStatus({...status,setup_authorized:false,recovery_authorized:false,grant_id:undefined,grant_secret:undefined});setBusy(true);setError('')
    const controller=new AbortController();request.current=controller
    let timedOut=false;callbacks.current.onCommitting?.(true)
    const timer=window.setTimeout(()=>{timedOut=true;controller.abort()},30_000)
    try {
      const result=await ownerPinCall<OwnerPinResult>(recovery?'recover':'setup',body,pendingToken,controller.signal)
      if(controller.signal.aborted || !live.current)return
      if(!isAuthTokens(result))throw new Error('Owner setup did not return an authorized session.')
      await onApproved(result,controller.signal)
    } catch(e){if(!controller.signal.aborted||timedOut){if(!(e instanceof OwnerPinError)||e.status>=500){setError('PIN change outcome is unknown. Sign in with Google again; the submission will not be retried.');callbacks.current.onUncertain?.();return}setError(e instanceof Error?e.message:'Owner PIN setup failed. Check local approval before trying again.');if(e instanceof OwnerPinError&&e.status===429){setRetryAt(e.retryAt);setNow(Date.now())}}}
    finally{window.clearTimeout(timer);callbacks.current.onCommitting?.(false);if(request.current===controller){request.current=null;if(live.current)setBusy(false)}}
  }
  const wait=Math.max(0,Math.ceil((retryAt-now)/1000))
  return <section className="flex flex-col gap-3" data-testid="owner-pin-setup">
    <h2 className="heading">{recovery?"Recover owner PIN":"Set up owner PIN"}</h2>
    <p>Setting or replacing the owner PIN requires separate approval on your VM. Google sign-in alone cannot reset it. Keep this page open while the owner approves this exact request locally.{recovery?" Your existing PIN keeps working until the approved replacement is saved.":""}</p>
    {(status?.setup_request_id||status?.recovery_request_id) && <p>Local approval request: <code>{status.setup_request_id??status.recovery_request_id}</code></p>}
    {(recovery?status?.recovery_authorized:status?.setup_authorized) ? <>
      <p>Local approval received. Enter the new 6–12 digit PIN privately below. Do not send it in chat. This replaces the owner PIN and signs existing sessions out.</p>
      <FocusableInput focusKey="owner-setup:pin" label="New owner PIN" type="password" inputMode="numeric" autoComplete="new-password" maxLength={12} readOnly={busy} value={pin} onChange={setPin} onSubmit={()=>void submit()} />
      <FocusableInput focusKey="owner-setup:confirm" label="Confirm new owner PIN" type="password" inputMode="numeric" autoComplete="new-password" maxLength={12} readOnly={busy} value={confirm} onChange={setConfirm} onSubmit={()=>void submit()} />
      {confirm && pin!==confirm && <p role="status">The PIN entries do not match.</p>}
      <Button focusKey="owner-setup:save" primary disabled={busy||wait>0||!/^\d{6,12}$/.test(pin)||pin!==confirm} onEnter={()=>void submit()}>{busy?'Saving privately…':recovery?'Replace owner PIN':'Set owner PIN'}</Button>
    </> : <p role="status">{busy?'Finishing the current request…':'Waiting for local VM approval. No PIN can be set yet.'}</p>}
    {wait>0&&<p role="status">Try again in {wait} seconds.</p>}{error&&<p role="alert">{error}</p>}
    <Button focusKey="owner-setup:check" disabled={busy||wait>0} onEnter={()=>void check()}>Check local approval</Button>
  </section>
}
