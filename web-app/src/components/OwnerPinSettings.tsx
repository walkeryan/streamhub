import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useAuthCapabilities } from '../api/contentPolicy'
import { useSession } from '../store/session'
import { OwnerPinSetup } from './OwnerPinSetup'
import { Button, Modal } from './ui'

export function OwnerPinSettings() {
  const capability=useAuthCapabilities()
  const profile=useSession(s=>s.profile)!
  const [open,setOpen]=useState(false)
  const origin=useRef(0)
  const committing=useRef(false)
  const [finishing,setFinishing]=useState(false)
  const navigate=useNavigate()
  const qc=useQueryClient()
  if(!capability.data?.content_policy_enabled || !profile.is_admin || profile.content_restricted)return null
  return <section className="panel flex flex-col gap-3">
    <h2 className="heading">Owner PIN protection</h2>
    <p>{capability.data.owner_pin_configured?'Owner PIN configured. Google sign-in still identifies the owner; the PIN authorizes protected actions.':'Owner PIN setup is required before enabling restricted profiles.'} Setup and recovery require separate local VM approval.</p>
    <Button focusKey="owner-settings:open" onEnter={()=>{origin.current=useSession.getState().generation;setOpen(true)}}>Owner PIN setup or recovery</Button>
    <Modal open={open} onClose={()=>{if(!committing.current)setOpen(false)}} title="Owner PIN management" firstFocusKey="owner-settings:cancel" wide>
      {open&&<OwnerPinSetup onCommitting={value=>{committing.current=value;setFinishing(value)}} onUncertain={()=>{if(useSession.getState().generation===origin.current){useSession.getState().clear();qc.clear();navigate("/?owner_pin_uncertain=1",{replace:true})}setOpen(false)}} recovery={!!capability.data.owner_pin_configured} onApproved={async(tokens,signal)=>{
        if(signal.aborted || useSession.getState().generation!==origin.current)return
        if(tokens.profile.id!==profile.id)throw new Error('Setup returned a different owner profile.')
        await qc.cancelQueries()
        if(signal.aborted || useSession.getState().generation!==origin.current)return
        qc.clear();useSession.getState().setSession(tokens.access_token,tokens.refresh_token,tokens.profile);setOpen(false)
      }}/>}
      <Button focusKey="owner-settings:cancel" disabled={finishing} onEnter={()=>{if(!committing.current)setOpen(false)}}>Cancel</Button>
    </Modal>
  </section>
}
