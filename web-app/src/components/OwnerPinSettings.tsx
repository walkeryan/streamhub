import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useAuthCapabilities } from '../api/contentPolicy'
import { useSession } from '../store/session'
import { useCan } from '../api/me'
import { OwnerPinSetup } from './OwnerPinSetup'
import { Button, Modal } from './ui'
import { GlassPanel } from '../ds/States'
import { SectionHeader } from '../ds/Section'

export function OwnerPinSettings() {
  const capability=useAuthCapabilities()
  const profile=useSession(s=>s.profile)!
  const [open,setOpen]=useState(false)
  const origin=useRef(0)
  const committing=useRef(false)
  const [finishing,setFinishing]=useState(false)
  const navigate=useNavigate()
  const qc=useQueryClient()
  const owner=useCan('system.manage')
  if(!capability.data?.content_policy_enabled || !owner || profile.content_restricted)return null
  return <GlassPanel as="section" className="acct-group">
    <SectionHeader title="Owner PIN protection" />
    <p className="acct-note">{capability.data.owner_pin_configured?'Owner PIN configured. Google sign-in still identifies the owner; the PIN authorizes protected actions.':'Owner PIN setup is required before enabling restricted profiles.'} Setup and recovery require separate local VM approval.</p>
    <div><Button focusKey="owner-settings:open" icon="lock" onEnter={()=>{origin.current=useSession.getState().generation;setOpen(true)}}>Owner PIN setup or recovery</Button></div>
    <Modal open={open} onClose={()=>{if(!committing.current)setOpen(false)}} title="Owner PIN management" firstFocusKey="owner-settings:cancel" wide>
      {open&&<OwnerPinSetup onCommitting={value=>{committing.current=value;setFinishing(value)}} onUncertain={()=>{if(useSession.getState().generation===origin.current){useSession.getState().clear();qc.clear();navigate("/?owner_pin_uncertain=1",{replace:true})}setOpen(false)}} recovery={!!capability.data.owner_pin_configured} onApproved={async(tokens,signal)=>{
        if(signal.aborted || useSession.getState().generation!==origin.current)return
        if(tokens.profile.id!==profile.id)throw new Error('Setup returned a different owner profile.')
        await qc.cancelQueries()
        if(signal.aborted || useSession.getState().generation!==origin.current)return
        qc.clear();useSession.getState().setSession(tokens.access_token,tokens.refresh_token,tokens.profile);setOpen(false)
      }}/>}
      <div><Button focusKey="owner-settings:cancel" ghost disabled={finishing} onEnter={()=>{if(!committing.current)setOpen(false)}}>Cancel</Button></div>
    </Modal>
  </GlassPanel>
}
