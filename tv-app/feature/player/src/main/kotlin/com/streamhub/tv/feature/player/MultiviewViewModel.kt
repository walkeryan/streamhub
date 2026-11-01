package com.streamhub.tv.feature.player

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.streamhub.tv.core.data.api.ApiException
import com.streamhub.tv.core.data.api.StreamHubApi
import com.streamhub.tv.core.data.auth.Session
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.sameSwitchSession
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.data.repo.AtomicMultiviewLease
import com.streamhub.tv.core.data.repo.PlaybackCoordinator
import com.streamhub.tv.core.player.PlayerFactory
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import javax.inject.Inject

internal data class PairAudioChoice(val pane:Int,val options:PlaybackOptions)
internal data class MultiviewState(
    val selected:List<Channel?> = listOf(null,null),val picker:Int?=null,val query:String="",val allLanguages:Boolean=false,
    val channels:ChannelList=ChannelList(),val listing:Boolean=false,val busy:Boolean=false,val active:Boolean=false,
    val audioPane:Int=0,val choice:PairAudioChoice?=null,val message:String?=null,
)

@HiltViewModel
class MultiviewViewModel @Inject constructor(
    private val api:StreamHubApi,private val sessions:SessionStore,private val coordinator:PlaybackCoordinator,
    factory:PlayerFactory,@ApplicationContext context:Context,
):ViewModel() {
    val players=listOf(factory.create(false,false,false),factory.create(false,false,false))
    private val mutable=MutableStateFlow(MultiviewState())
    internal val state=mutable.asStateFlow()
    private val lease=AtomicMultiviewLease(api::reserveMultiview,api::releaseMultiview)
    private val registration="multiview-${java.util.UUID.randomUUID()}"
    private var owner:Session?=null
    private var epoch=0L
    private var startJob:Job?=null
    private var listJob:Job?=null
    private var stopJob:Job?=null
    private var audioInput:CompletableDeferred<Pair<String,Boolean>>?=null
    private var foreground=true
    private val manager=context.getSystemService(AudioManager::class.java)
    private val focusListener=AudioManager.OnAudioFocusChangeListener {change->if(change<0)stop("Audio focus changed. Start again when ready.")}
    private val focusRequest=if(Build.VERSION.SDK_INT>=26) AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
        .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).build()).setOnAudioFocusChangeListener(focusListener).build() else null
    init {
        chooseAudio(0)
        viewModelScope.launch {sessions.session.collect {current->
            val prior=owner
            if(prior!=null && (!sameSwitchSession(prior,current) || prior.profile?.languages!=current.profile?.languages)) {
                stop("Profile or language preferences changed. Select channels again.",clearSelection=true)
                owner=null
            }
        }}
        viewModelScope.launch {combine(players[0].state,players[1].state){left,right->listOf(left.error,right.error)}.collect {errors->
            if(mutable.value.active && errors.any{it!=null}) stop("A pane failed. Both streams stopped; Start checks admission and audio again.")
        }}
    }
    private suspend fun valid(expected:Session,generation:Long):Boolean {
        val current=sessions.current()
        return foreground && epoch==generation && sameSwitchSession(expected,current) && expected.profile?.languages==current.profile?.languages
    }
    private suspend fun ensure(expected:Session,generation:Long) {
        currentCoroutineContext().ensureActive()
        if(!valid(expected,generation))throw CancellationException("Multiview intent changed")
    }
    fun picker(index:Int) {
        if(mutable.value.busy || mutable.value.active || index !in 0..1)return
        mutable.update{it.copy(picker=index,query="",channels=ChannelList(),message=null)};loadChannels(0)
    }
    fun closePicker(){listJob?.cancel();mutable.update{it.copy(picker=null,listing=false)}}
    fun query(value:String){mutable.update{it.copy(query=value.take(200))}}
    fun allLanguages(value:Boolean){mutable.update{it.copy(allLanguages=value)};loadChannels(0)}
    fun loadChannels(offset:Int=0) {
        listJob?.cancel()
        val query=mutable.value.query.trim();val all=mutable.value.allLanguages
        listJob=viewModelScope.launch {
            val original=sessions.current();val generation=epoch
            mutable.update{it.copy(listing=true,message=null)}
            try {
                val result=api.channels(q=query.takeIf{it.isNotEmpty()},languages=if(all)emptyList() else null,limit=40,offset=offset.coerceAtLeast(0))
                ensure(original,generation)
                mutable.update{it.copy(channels=result,listing=false)}
            }catch(e:CancellationException){throw e}
            catch(_:Exception){if(valid(original,generation))mutable.update{it.copy(listing=false,message="Could not load channels. Retry the search.")}}
        }
    }
    fun select(channel:Channel) {
        val pane=mutable.value.picker ?: return
        if(mutable.value.channels.channels.none{it.id==channel.id} || !channel.id.startsWith("ch:"))return
        if(mutable.value.selected[1-pane]?.id==channel.id){mutable.update{it.copy(message="Choose two different channels.")};return}
        mutable.update{it.copy(selected=it.selected.toMutableList().also{list->list[pane]=channel},picker=null,message=null)}
    }
    fun confirmAudio(variant:String,confirmed:Boolean) {
        val choice=mutable.value.choice ?: return
        val option=choice.options.options.firstOrNull{it.variantId==variant} ?: return
        if(!option.preferredAudioAvailable && choice.options.preferredLanguages.isNotEmpty() && !confirmed)return
        audioInput?.complete(variant to confirmed)
    }
    fun cancelAudio(){stop("Multiview canceled. No pair was started.")}
    private suspend fun preflight(channel:Channel,pane:Int,original:Session,generation:Long):PlaybackRequest {
        val options=api.playbackOptions(channel.id,channel.sourceId);ensure(original,generation)
        require(options.id==channel.id && options.options.isNotEmpty()) {"Channel audio options unavailable"}
        val automatic=options.options.firstOrNull{it.variantId==options.automaticVariantId && it.sourceId==channel.sourceId && it.variantId==channel.id && (it.preferredAudioAvailable || options.preferredLanguages.isEmpty())}
        if(automatic!=null)return PlaybackRequest(channel.id,sourceId=channel.sourceId,variantId=automatic.variantId)
        val input=CompletableDeferred<Pair<String,Boolean>>()
        audioInput=input;mutable.update{it.copy(choice=PairAudioChoice(pane,options))}
        try {
            val (variant,confirmed)=input.await();ensure(original,generation)
            val selected=options.options.firstOrNull{it.variantId==variant} ?: error("Audio selection changed")
            require(selected.sourceId==channel.sourceId && selected.variantId==channel.id)
            return PlaybackRequest(channel.id,sourceId=channel.sourceId,variantId=variant,allowUnverifiedAudio=confirmed)
        } finally {audioInput=null;mutable.update{it.copy(choice=null)}}
    }
    fun start() {
        if(mutable.value.busy || mutable.value.active || !foreground)return
        val channels=mutable.value.selected.filterNotNull()
        if(channels.size!=2 || channels[0].id==channels[1].id)return
        val generation=++epoch
        mutable.update{it.copy(busy=true,message="Checking both audio choices and source capacity…")}
        startJob=viewModelScope.launch {
            val original=sessions.current();owner=original
            try {
                coordinator.unregisterActivePlayer(registration)
                players.forEach{it.stop()};check(lease.close()) {"Previous pair cleanup uncertain"}
                coordinator.clearForProfileSwitch() // Release any ordinary/DVR player before reserving a pair.
                ensure(original,generation)
                coordinator.registerActivePlayer(registration){stopForExternal()}
                val requests=channels.mapIndexed{pane,channel->preflight(channel,pane,original,generation)}
                ensure(original,generation)
                val recovery=api.playbackGeneration();ensure(original,generation)
                val granted=lease.open(original,MultiviewRequest(requests,recovery.generation)){valid(original,generation)}
                ensure(original,generation)
                if(!requestFocus())error("Audio focus unavailable")
                players.forEachIndexed{index,player->ensure(original,generation);player.play(granted.panes[index],original)}
                check(players.none{it.state.value.error!=null}) {"A pane failed during startup"}
                mutable.update{it.copy(active=true,busy=false,message="Two live streams admitted together. Local rewind is unavailable.")}
            }catch(e:CancellationException){
                withContext(NonCancellable){players.forEach{it.stop()};lease.close();abandonFocus()}
                throw e
            }
            catch(e:Exception) {
                players.forEach{it.stop()};lease.close();abandonFocus()
                if(epoch==generation)mutable.update{it.copy(active=false,message=when((e as? ApiException)?.error?.code){
                    "source_busy","source_limit_reached","multiview_capacity_unknown"->"The server could not reserve two source connections. Neither pane started; capacity settings are unchanged."
                    else->"Pair could not start. No single-pane fallback. The source may permit only one connection; wait before a manual retry."
                })}
            }finally{if(epoch==generation)mutable.update{it.copy(busy=false,choice=null)}}
        }
    }
    private fun requestFocus():Boolean = if(Build.VERSION.SDK_INT>=26)manager.requestAudioFocus(focusRequest!!)==AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        else @Suppress("DEPRECATION") (manager.requestAudioFocus(focusListener,AudioManager.STREAM_MUSIC,AudioManager.AUDIOFOCUS_GAIN)==AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
    private fun abandonFocus(){if(Build.VERSION.SDK_INT>=26)manager.abandonAudioFocusRequest(focusRequest!!) else @Suppress("DEPRECATION") manager.abandonAudioFocus(focusListener)}
    fun chooseAudio(index:Int){if(index !in 0..1)return;players.forEach{it.exoPlayer.volume=0f};players[index].exoPlayer.volume=1f;mutable.update{it.copy(audioPane=index)}}
    private suspend fun stopForExternal(message:String="Pair stopped. Start checks audio and capacity again.") {
        epoch++;val pending=startJob;startJob=null;pending?.cancelAndJoin();audioInput?.cancel();audioInput=null
        players.forEach{it.stop()};val released=lease.close();abandonFocus();coordinator.unregisterActivePlayer(registration)
        mutable.update{it.copy(active=false,busy=false,choice=null,message=if(released)message else "Stop could not be confirmed. The server lease must expire or be recovered before retry.")}
    }
    fun stop(message:String="Paused: both streams released. Start resumes at live with new checks.",clearSelection:Boolean=false) {
        epoch++;startJob?.cancel();audioInput?.cancel();listJob?.cancel()
        mutable.update{it.copy(active=false,busy=true,choice=null,picker=null,message=message,selected=if(clearSelection)listOf(null,null) else it.selected)}
        if(stopJob?.isActive!=true) stopJob=viewModelScope.launch {stopForExternal(message)}
    }
    fun background(){foreground=false;stop()}
    fun foreground(){foreground=true}
    override fun onCleared() {
        epoch++;val pending=startJob;pending?.cancel();listJob?.cancel();audioInput?.cancel();abandonFocus();coordinator.unregisterActivePlayer(registration)
        val cleanup=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
        cleanup.launch {try{pending?.join();lease.close()}finally{players.forEach{it.release()};cleanup.cancel()}}
    }
}
