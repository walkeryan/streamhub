import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, absent, api, ok, refreshMe, unwrap } from './client'
import type { components } from './schema'
import { useSession } from '../store/session'

type S = components['schemas']
export type User = S['User']
export type UserListItem = S['UserListItem']
export type UserDetail = S['UserDetail']
export type Role = S['Role']
export type RoleInput = S['RoleInput']
export type Invite = S['Invite']
export type InviteCreated = S['InviteCreated']
export type AuditEntry = S['AuditEntry']
export type AuthSession = S['AuthSession']
export type LimitsInput = S['LimitsInput']
/** One user on a shared device's "Who's watching?" picker. */
export type Switchable = S['SwitchableUser']
export type Switched = S['Switched']

const useScope = () => useSession((s) => s.profile?.id ?? null)
const path = (id: string) => ({ params: { path: { id } } })

// Approvals, sign-ins and what people are watching change elsewhere, so the lists poll.
export const useUsers = (enabled = true) => useQuery({ queryKey: ['users', useScope()], enabled, staleTime: 0, refetchInterval: 20_000, queryFn: () => unwrap<UserListItem[]>(api.GET('/users')) })
export const useUser = (id: string | undefined) =>
  useQuery({ queryKey: ['users', useScope(), id], enabled: !!id, staleTime: 0, queryFn: () => unwrap<UserDetail>(api.GET('/users/{id}', path(id!))) })
export const useRoles = (enabled = true) => useQuery({ queryKey: ['roles', useScope()], enabled, queryFn: () => unwrap<Role[]>(api.GET('/roles')) })
export const useInvites = () => useQuery({ queryKey: ['invites', useScope()], staleTime: 0, queryFn: () => unwrap<Invite[]>(api.GET('/invites')) })
export const useAudit = (userId: string | undefined) =>
  useQuery({ queryKey: ['audit', useScope(), userId ?? ''], staleTime: 0, queryFn: () => unwrap<AuditEntry[]>(api.GET('/audit', { params: { query: { user_id: userId, limit: 100 } } })) })
/** The caller's own signed-in devices. */
export const useSessions = () => useQuery({ queryKey: ['sessions', useScope()], staleTime: 0, queryFn: () => unwrap<AuthSession[]>(api.GET('/auth/sessions')) })
/**
 * Who this shared device can switch to. `null`: there is no picker to show, because the server has
 * no such call (404, a server from before users) or says this is a personal device (403 not_shared_device).
 */
export const useSwitchable = (enabled: boolean) =>
  useQuery({
    queryKey: ['switchable', useScope()], enabled, staleTime: 30_000, retry: false,
    queryFn: async ({ signal }): Promise<Switchable[] | null> => {
      try { return (await unwrap<{ users: Switchable[] }>(api.GET('/auth/switchable', { signal }))).users } catch (e) {
        if (absent(e) || (e as { code?: string }).code === 'not_shared_device') return null
        throw e
      }
    },
  })

/** POST /auth/switch. A switch that collided with another change to the session (409 switch_conflict) is asked once more. */
async function switchTo(user_id: string, pin?: string): Promise<Switched> {
  const ask = () => unwrap<Switched>(api.POST('/auth/switch', { body: pin === undefined ? { user_id } : { user_id, pin } }))
  try { return await ask() } catch (e) {
    if (e instanceof ApiError && e.code === 'switch_conflict') return ask()
    throw e
  }
}

/** Every call that changes people, roles, invites or devices. */
export const people = {
  create: (body: { name: string; avatar?: string | null; role_id?: string; pin?: string | null }) => unwrap<User>(api.POST('/users', { body })),
  patch: (id: string, body: { name?: string; avatar?: string | null; role_id?: string; grants?: S['Permission'][]; denials?: S['Permission'][]; limits?: LimitsInput; on_shared_devices?: boolean | null; plex_account_linked?: boolean }) =>
    unwrap<UserDetail>(api.PATCH('/users/{id}', { ...path(id), body })),
  approve: (id: string, role_id: string) => unwrap<UserDetail>(api.POST('/users/{id}/approve', { ...path(id), body: { role_id } })),
  deny: (id: string) => ok(api.POST('/users/{id}/deny', path(id))),
  suspend: (id: string) => unwrap<UserDetail>(api.POST('/users/{id}/suspend', path(id))),
  reinstate: (id: string) => unwrap<UserDetail>(api.POST('/users/{id}/reinstate', path(id))),
  remove: (id: string) => ok(api.DELETE('/users/{id}', path(id))),
  setPin: (id: string, pin: string | null) => ok(api.PUT('/users/{id}/pin', { ...path(id), body: { pin } })),
  createRole: (body: RoleInput & { name: string }) => unwrap<Role>(api.POST('/roles', { body })),
  patchRole: (id: string, body: RoleInput) => unwrap<Role>(api.PATCH('/roles/{id}', { ...path(id), body })),
  removeRole: (id: string) => ok(api.DELETE('/roles/{id}', path(id))),
  invite: (body: { role_id: string; expires_in_hours: number; max_uses: number }) => unwrap<InviteCreated>(api.POST('/invites', { body })),
  revokeInvite: (id: string) => ok(api.DELETE('/invites/{id}', path(id))),
  revokeSession: (sid: string) => ok(api.DELETE('/auth/sessions/{sid}', { params: { path: { sid } } })),
  patchSession: (sid: string, body: { shared_device?: boolean; label?: string }) => unwrap<AuthSession>(api.PATCH('/auth/sessions/{sid}', { params: { path: { sid } }, body })),
  switchTo,
  adultUnlock: (pin: string) => unwrap<{ adult_unlocked_until: string }>(api.POST('/auth/adult-unlock', { body: { pin } })),
  adultLock: () => ok(api.POST('/auth/adult-lock')),
}

const TOUCHED = [['users'], ['roles'], ['invites'], ['audit'], ['sessions'], ['switchable']] as const
/** Runs one of the calls above, then re-reads everything it can have changed, the caller's own access included. */
export function usePeopleChange<V, T>(run: (v: V) => Promise<T>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const queryKey of TOUCHED) void qc.invalidateQueries({ queryKey })
      refreshMe()
    },
  })
}
