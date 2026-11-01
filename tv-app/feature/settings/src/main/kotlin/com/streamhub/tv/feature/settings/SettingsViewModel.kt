package com.streamhub.tv.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.sameSwitchSession
import com.streamhub.tv.core.data.model.AuthConfig
import com.streamhub.tv.core.data.model.AuthTokens
import com.streamhub.tv.core.data.model.OwnerPinChallenge
import com.streamhub.tv.core.data.model.OwnerPinCancel
import com.streamhub.tv.core.data.repo.ChannelListCache
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.data.repo.ProfileSwitchPath
import com.streamhub.tv.core.data.repo.profileSwitchPath
import com.streamhub.tv.core.data.repo.AuthorizationCommitGate
import com.streamhub.tv.core.data.repo.AuthorizationOutcomeUnknown
import com.streamhub.tv.core.data.repo.awaitOwnerPin
import com.streamhub.tv.core.data.repo.validOwnerPin
import com.streamhub.tv.core.data.repo.ownerPinRetryDelay
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import com.streamhub.tv.core.data.repo.catalogScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.Job
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.model.ChannelGroup
import com.streamhub.tv.core.data.model.ChannelGroupPatch
import com.streamhub.tv.core.data.model.HomeLayout
import com.streamhub.tv.core.data.model.HomeLayoutRow
import com.streamhub.tv.core.data.model.LanguageCount
import com.streamhub.tv.core.data.model.LoginRequest
import com.streamhub.tv.core.data.model.Profile
import com.streamhub.tv.core.data.model.Row
import com.streamhub.tv.core.data.model.Settings
import com.streamhub.tv.core.data.repo.LocalPrefs
import com.streamhub.tv.core.ui.theme.ThemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class SettingsSection(val label: String) {
    Profiles("Profiles"), Theme("Theme"), Languages("Languages"), Rows("Home rows"),
    Parental("Parental controls"), Playback("Playback"), About("About"),
}

data class SettingsState(
    val section: SettingsSection = SettingsSection.Profiles,
    val profiles: List<Profile> = emptyList(),
    val current: Profile? = null,
    val languages: List<LanguageCount> = emptyList(),
    val groups: List<ChannelGroup> = emptyList(),
    val rows: List<Row> = emptyList(),
    val rowOrder: List<String> = emptyList(),
    val hiddenRows: Set<String> = emptySet(),
    val serverSettings: Settings? = null,
    val serverUrl: String? = null,
    val message: String? = null,
    val pinPromptFor: Profile? = null,
    val authConfig: AuthConfig? = null,
    val switching: Boolean = false,
    val ownerPin: OwnerPinChallenge? = null,
    val ownerPinBusy: Boolean = false, val ownerPinError: String? = null,
    val authorizationFinishing: Boolean = false, val authorizationUncertain: Boolean = false,
    val switchTargetName: String? = null,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val api: StreamHubApi,
    private val session: SessionStore,
    private val prefs: LocalPrefs,
    private val themes: ThemeRepository,
    private val playback: PlaybackCoordinator,
    private val channelCache: ChannelListCache,
) : ViewModel() {
    private val _state = MutableStateFlow(SettingsState())
    val state: StateFlow<SettingsState> = _state.asStateFlow()

    val selectedTheme = themes.selectedThemeId.stateIn(viewModelScope, SharingStarted.Eagerly, ThemeRepository.DEFAULT_THEME)
    val reducedMotion = prefs.reducedMotion.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    private var loadJob: Job? = null
    private var switchJob: Job? = null
    private var ownerPinInput: Channel<String>? = null
    private val authorization=AuthorizationCommitGate { finishing -> _state.update{it.copy(authorizationFinishing=finishing)} }
    init { load() }

    private fun pid() = _state.value.current?.id ?: "default"

    fun load(): Job {
        loadJob?.cancel()
        return viewModelScope.launch {
            val original=session.current()
            suspend fun guard() { currentCoroutineContext().ensureActive(); if(session.current().catalogScope()!=original.catalogScope()) throw CancellationException("Profile changed") }
            _state.update { it.copy(current=original.profile,serverUrl=original.serverUrl,rows=emptyList(),groups=emptyList(),languages=emptyList(),authConfig=null) }
            launch { try { val config=api.authConfig();guard();_state.update {it.copy(authConfig=config)} } catch(e:CancellationException){throw e}catch(_:Exception){guard();_state.update{it.copy(message="Could not verify profile-switch capability. Retry when connected.")}} }
            launch { try {val profiles=api.profiles();guard();_state.update{it.copy(profiles=profiles,current=profiles.firstOrNull{p->p.id==original.profile?.id} ?: it.current)}}catch(e:CancellationException){throw e}catch(_:Exception){} }
            launch {try{val values=api.channelLanguages();guard();_state.update{it.copy(languages=values)}}catch(e:CancellationException){throw e}catch(_:Exception){} }
            launch {try{val values=api.channelGroups();guard();_state.update{it.copy(groups=values)}}catch(e:CancellationException){throw e}catch(_:Exception){} }
            launch {try{val values=api.settings();guard();_state.update{it.copy(serverSettings=values)}}catch(e:CancellationException){throw e}catch(_:Exception){} }
            launch {
                try {
                    val home=api.home();guard()
                    val id=original.profile?.id ?: return@launch
                    val layout=runCatching{api.homeLayout(id)}.getOrNull();guard()
                    val order=layout?.rows?.takeIf{it.isNotEmpty()}?.sortedBy{it.sort ?: Int.MAX_VALUE}?.map{it.id} ?: prefs.rowOrder(id).first()
                    val hidden=layout?.rows?.takeIf{it.isNotEmpty()}?.filter{it.hidden}?.map{it.id}?.toSet() ?: prefs.hiddenRows(id).first()
                    guard()
                    val rows=home.rows.sortedBy{r->order.indexOf(r.id).let{if(it<0)Int.MAX_VALUE else it}}
                    _state.update{it.copy(rows=rows,rowOrder=rows.map{it.id},hiddenRows=hidden)}
                }catch(e:CancellationException){throw e}catch(_:Exception){}
            }
        }.also {loadJob=it}
    }

    fun select(section: SettingsSection) = _state.update { it.copy(section = section) }

    // ---- Theme: focus previews live, OK commits ------------------------------------------------
    fun previewTheme(id: String?) = themes.preview(id)
    fun selectTheme(id: String) = viewModelScope.launch {
        themes.select(id)
        _state.value.current?.let { p ->
            val updated = p.copy(themeId = id)
            runCatching { api.updateProfile(updated) }
            session.updateProfile(updated)
            _state.update { it.copy(current = updated) }
        }
    }
    fun setReducedMotion(on: Boolean) = viewModelScope.launch { prefs.setReducedMotion(on) }

    // ---- Languages (profile.languages, ISO 639-3) ---------------------------------------------
    fun toggleLanguage(code: String) = viewModelScope.launch {
        val p = _state.value.current ?: return@launch
        val langs = if (code in p.languages) p.languages - code else p.languages + code
        val updated = p.copy(languages = langs)
        runCatching { api.updateProfile(updated) }.onSuccess {
            session.updateProfile(updated)
            _state.update { it.copy(current = updated) }
        }
    }

    // ---- Home rows ----------------------------------------------------------------------------
    fun moveRow(id: String, delta: Int) = viewModelScope.launch {
        val order = _state.value.rowOrder.toMutableList()
        val i = order.indexOf(id); val j = i + delta
        if (i < 0 || j !in order.indices) return@launch
        order.removeAt(i); order.add(j, id)
        prefs.setRowOrder(pid(), order)
        _state.update { s -> s.copy(rowOrder = order, rows = order.mapNotNull { rid -> s.rows.firstOrNull { it.id == rid } }) }
        pushLayout()
    }

    private suspend fun pushLayout() {
        val p = _state.value.current ?: return
        val s = _state.value
        val layout = HomeLayout(s.rowOrder.mapIndexed { i, id -> HomeLayoutRow(id = id, hidden = id in s.hiddenRows, sort = i) })
        runCatching { api.putHomeLayout(p.id, layout) }
    }

    fun toggleRowHidden(id: String) = viewModelScope.launch {
        val h = _state.value.hiddenRows.let { if (id in it) it - id else it + id }
        prefs.setHiddenRows(pid(), h)
        _state.update { it.copy(hiddenRows = h) }
        pushLayout()
    }

    // ---- Parental / groups --------------------------------------------------------------------
    fun toggleGroupHidden(g: ChannelGroup) = viewModelScope.launch {
        runCatching { api.patchChannelGroup(g.id, ChannelGroupPatch(hidden = !g.hidden)) }
            .onSuccess { _state.update { s -> s.copy(groups = s.groups.map { if (it.id == g.id) it.copy(hidden = !g.hidden) else it }) } }
    }

    fun setPin(pin: String?) = viewModelScope.launch {
        val p = _state.value.current ?: return@launch
        runCatching { api.setPin(p.id, pin?.takeIf { it.isNotBlank() }) }
            .onSuccess {
                val updated = p.copy(hasPin = !pin.isNullOrBlank())
                session.updateProfile(updated)
                _state.update { it.copy(current = updated, message = if (pin.isNullOrBlank()) "PIN removed" else "PIN set") }
            }
            .onFailure { e -> _state.update { it.copy(message = e.message) } }
    }

    // ---- Profiles -----------------------------------------------------------------------------
    fun switchProfile(p: Profile, pin: String? = null) {
        if(_state.value.switching || _state.value.authorizationUncertain || p.id==_state.value.current?.id) return
        switchJob?.cancel()
        _state.update {it.copy(switching=true,message=null)}
        switchJob=viewModelScope.launch {
            val original=session.current()
            try {
                val config=api.authConfig()
                currentCoroutineContext().ensureActive()
                if(!sameSwitchSession(original,session.current())) throw CancellationException("Profile changed")
                _state.update{it.copy(authConfig=config)}
                when(profileSwitchPath(config,_state.value.current,p)) {
                    ProfileSwitchPath.SameProfile -> Unit
                    ProfileSwitchPath.OwnerPin -> ownerPinSafely(original,p)
                    ProfileSwitchPath.Secure -> {
                        loadJob?.cancel();playback.clearForProfileSwitch()
                        authorization.finish {commitSwitch(original,p,api.switchProfile(LoginRequest(p.id)))}
                    }
                    ProfileSwitchPath.Legacy -> if(p.hasPin && pin==null) _state.update{it.copy(pinPromptFor=p)} else commitSwitch(original,p,api.login(LoginRequest(p.id,pin=pin)))
                    ProfileSwitchPath.Unavailable -> _state.update{it.copy(message="Secure profile switching is unavailable. Google sign-in restrictions remain in place.")}
                }
            } catch(_:AuthorizationOutcomeUnknown) {markAuthorizationUncertain()}
            catch(e:TimeoutCancellationException) {_state.update{it.copy(message="Owner authorization expired. Start a new request.",ownerPin=null)}}
            catch(e:CancellationException){throw e}
            catch(e:ApiException) {
                if(e.error?.code=="owner_pin_required") ownerPinSafely(original,p)
                else _state.update{it.copy(message=if(e.error?.code=="owner_pin_setup_required") "Set up the owner PIN in Streamhub on your phone with local VM approval." else when(e.status){401->"Authentication failed. Use the existing credential or sign in again.";403->"Profile switch denied. Use the existing owner PIN or approved owner phone flow.";410->"Owner sign-in expired. Start again.";else->"Could not switch profiles. Current profile remains selected."},pinPromptFor=null)}
            } catch(_:Exception){_state.update{it.copy(message="Could not switch profiles. Current profile remains selected.",pinPromptFor=null)}}
            finally {_state.update{it.copy(switching=false)}}
        }
    }

    private suspend fun ownerPinSafely(original: Session, target: Profile) {
        try { ownerPinFlow(original,target) }
        catch(_:AuthorizationOutcomeUnknown) {markAuthorizationUncertain()}
        catch(e:TimeoutCancellationException) {_state.update{it.copy(message="Owner PIN request expired. Start a new request.")}}
        catch(e:CancellationException){throw e}
        catch(e:ApiException) {_state.update{it.copy(message=when {
            e.error?.code=="owner_pin_setup_required" -> "Set up the owner PIN in Streamhub on your phone. Setup requires local VM approval; this TV cannot create or reset it."
            e.status==429 -> "Too many attempts. Wait ${ownerPinRetryDelay(e.retryAfterSeconds)} seconds before trying again."
            else -> "Owner PIN authorization was denied, expired or unavailable. Current profile remains selected."
        })}}
        catch(_:Exception){_state.update{it.copy(message="Owner PIN authorization unavailable. Current profile remains selected.")}}
    }

    private suspend fun ownerPinFlow(original: Session,target: Profile) {
        var challenge: OwnerPinChallenge?=null
        val input=Channel<String>(Channel.RENDEZVOUS)
        var awaiting=false
        try {
            currentCoroutineContext().ensureActive()
            if(!sameSwitchSession(original,session.current())) throw CancellationException("Profile changed")
            // Verify revokes the initiating SID: release its playback lease while it is still valid.
            loadJob?.cancel()
            playback.clearForProfileSwitch()
            val active=api.startOwnerPin(target.id)
            challenge=active
            currentCoroutineContext().ensureActive()
            if(!sameSwitchSession(original,session.current())) throw CancellationException("Profile changed")
            ownerPinInput=input
            _state.update{it.copy(ownerPin=active,ownerPinBusy=false,ownerPinError=null,pinPromptFor=null,switchTargetName=target.name)}
            awaiting=true
            awaitOwnerPin(active,target.id,
                nextPin={_state.update{it.copy(ownerPinBusy=false)};input.receive()},
                stillCurrent={sameSwitchSession(original,session.current())},verify=api::verifyOwnerPin,
                rejected={seconds->_state.update{it.copy(ownerPinBusy=seconds>0,ownerPinError=if(seconds>0) "Too many attempts. Wait $seconds seconds, then enter the PIN again." else "PIN not accepted. Re-enter the existing owner PIN.")}},
                cancel=api::cancelOwnerPin,
                authorize={block->authorization.finish(block)},
                complete={verified ->
                    challenge=null // Consumed: commit before leaving the protected response phase.
                    commitSwitch(original,target,verified)
                })
        } finally {
            ownerPinInput=null;input.close()
            if(!awaiting) challenge?.let { active -> withContext(NonCancellable) {withTimeoutOrNull(5000) {runCatching {if(sameSwitchSession(original,session.current()))api.cancelOwnerPin(OwnerPinCancel(active.challengeId,active.pollToken))}}} }
            _state.update{it.copy(ownerPin=null,ownerPinBusy=false,ownerPinError=null,switchTargetName=null)}
        }
    }

    fun submitOwnerPin(pin: String) {
        if(!validOwnerPin(pin) || _state.value.ownerPinBusy || _state.value.ownerPin==null) return
        if(ownerPinInput?.trySend(pin)?.isSuccess==true) _state.update{it.copy(ownerPinBusy=true,ownerPinError=null)}
    }

    private suspend fun commitSwitch(original: Session, target: Profile, tokens: AuthTokens) {
        currentCoroutineContext().ensureActive()
        require(tokens.profile.id==target.id)
        if(!sameSwitchSession(original,session.current())) throw CancellationException("Profile changed")
        loadJob?.cancel()
        _state.update{it.copy(rows=emptyList(),groups=emptyList(),languages=emptyList(),pinPromptFor=null)}
        playback.clearForProfileSwitch()
        channelCache.clear()
        currentCoroutineContext().ensureActive()
        session.saveSwitchedTokens(original,target.id,tokens)
        _state.value=SettingsState(current=tokens.profile,serverUrl=original.serverUrl,message="Switched to ${tokens.profile.name}")
        themes.select(tokens.profile.themeId)
        load()
    }

    private fun markAuthorizationUncertain() {
        _state.update{it.copy(authorizationUncertain=true,ownerPin=null,message="Authorization may have completed. The old session cannot be assumed valid. Sign in again; the PIN will not be retried.")}
    }
    fun recoverAuthorization() {
        if(!_state.value.authorizationUncertain || authorization.finishing) return
        viewModelScope.launch {session.clearTokens()}
    }
    fun cancelPin() {
        authorization.cancelIfIdle {
            switchJob?.cancel()
            _state.update{it.copy(pinPromptFor=null,ownerPin=null,switchTargetName=null)}
        }
    }

    fun patchServer(s: Settings) = viewModelScope.launch {
        runCatching { api.patchSettings(s) }.onSuccess { _state.update { it.copy(serverSettings = (it.serverSettings ?: Settings()).merge(s)) } }
    }

    fun unpair() = viewModelScope.launch { session.clearTokens() }

    private fun Settings.merge(o: Settings) = Settings(
        tmdbApiKeySet = o.tmdbApiKeySet ?: tmdbApiKeySet, region = o.region ?: region,
        sleepTimerMin = o.sleepTimerMin ?: sleepTimerMin, epgRefreshHours = o.epgRefreshHours ?: epgRefreshHours,
        transcodeHw = o.transcodeHw ?: transcodeHw, proxyMode = o.proxyMode ?: proxyMode,
    )
}
