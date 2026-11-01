package com.streamhub.tv.feature.player

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.C
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.player.TrackOption
import com.streamhub.tv.core.player.VideoSurface
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

/** Two atomic server grants, never two independent resolve calls. No hidden remote gestures. */
@Composable
fun MultiviewScreen(onExit:()->Unit,vm:MultiviewViewModel=hiltViewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    val left by vm.players[0].state.collectAsStateWithLifecycle()
    val right by vm.players[1].state.collectAsStateWithLifecycle()
    val t=LocalThemeTokens.current
    val first=remember{FocusRequester()}
    val firstPane=remember{FocusRequester()};val secondPane=remember{FocusRequester()}
    val audioFocus=remember{FocusRequester()}
    var tracks by remember{mutableStateOf(false)}
    var confirm by remember{mutableStateOf<TrackOption?>(null)}
    val lifecycle=LocalLifecycleOwner.current.lifecycle
    DisposableEffect(vm,lifecycle) {
        val observer=LifecycleEventObserver {_,event->when(event){
            Lifecycle.Event.ON_STOP->vm.background()
            Lifecycle.Event.ON_START->vm.foreground()
            else->Unit
        }}
        lifecycle.addObserver(observer)
        onDispose{lifecycle.removeObserver(observer);vm.background()}
    }
    LaunchedEffect(Unit){first.requestFocus()}
    LaunchedEffect(s.active){if(!s.active){tracks=false;confirm=null}}
    var wasBusy by remember{mutableStateOf(false)}
    LaunchedEffect(s.busy){if(wasBusy&&!s.busy)runCatching{first.requestFocus()};wasBusy=s.busy}
    BackHandler { vm.stop("Left multiview. Both streams stopped.");onExit() }
    Column(Modifier.fillMaxSize().padding(28.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            ShButton("Back",{vm.stop();onExit()},Modifier.focusRequester(first))
            Text("Two-pane live TV",style=MaterialTheme.typography.headlineMedium,color=t.text)
        }
        Text("Requires two available source connections. Admission is atomic; a one-connection source cannot start a pair. No local rewind or recording.",color=t.textMuted)
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            ShButton("Left: ${s.selected[0]?.name ?: "Choose channel"}",{vm.picker(0)},Modifier.weight(1f).focusRequester(firstPane),enabled=!s.busy&&!s.active)
            ShButton("Right: ${s.selected[1]?.name ?: "Choose channel"}",{vm.picker(1)},Modifier.weight(1f).focusRequester(secondPane),enabled=!s.busy&&!s.active)
        }
        Row(Modifier.fillMaxWidth().weight(1f),horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            listOf(left,right).forEachIndexed {index,state->
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)) {
                    VideoSurface(vm.players[index],Modifier.fillMaxWidth().weight(1f))
                    Text(if(s.audioPane==index) "${if(index==0)"Left" else "Right"} · Audio selected" else "${if(index==0)"Left" else "Right"} · Muted",color=t.text)
                    state.audioNotice?.let{Text(it,color=t.textMuted,maxLines=3)}
                    if(state.buffering)Text("Buffering…",color=t.textMuted)
                }
            }
        }
        s.message?.let{Text(it,color=t.textMuted,maxLines=3)}
        Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
            ShButton(if(s.busy)"Finishing…" else "Start both",vm::start,enabled=!s.busy&&!s.active&&s.selected.all{it!=null})
            ShButton("Pause / release both",{vm.stop()},enabled=s.active||s.busy)
            ShButton("Listen left",{vm.chooseAudio(0)},enabled=s.active)
            ShButton("Listen right",{vm.chooseAudio(1)},enabled=s.active)
            ShButton("Audio tracks",{tracks=true},Modifier.focusRequester(audioFocus),enabled=s.active)
        }
    }
    s.picker?.let {pane->
        val close={vm.closePicker();runCatching{(if(pane==0)firstPane else secondPane).requestFocus()};Unit}
        val cancel=remember{FocusRequester()}
        Dialog(onDismissRequest=close) {
            Column(Modifier.width(700.dp).heightIn(max=560.dp).background(t.surface,RoundedCornerShape(20.dp)).padding(20.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
                Text("Choose ${if(pane==0)"left" else "right"} channel",style=MaterialTheme.typography.titleLarge,color=t.text)
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    ShButton("Cancel",close,Modifier.focusRequester(cancel))
                    ShButton(if(s.allLanguages)"All languages" else "My languages",{vm.allLanguages(!s.allLanguages)})
                    ShButton("Search",{vm.loadChannels()},enabled=!s.listing)
                }
                ShTextField(s.query,vm::query,"Channel name",onImeAction={vm.loadChannels()})
                Text("Language labels are provider hints. Each play checks audio separately.",color=t.textMuted)
                if(s.listing)Text("Loading…",color=t.textMuted)
                s.message?.let{Text(it,color=t.textMuted)}
                if(!s.listing&&s.channels.channels.isEmpty())Text("No matching channels. Change the query or language filter.",color=t.textMuted)
                LazyColumn(Modifier.weight(1f,false),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    items(s.channels.channels,key={it.id}) {channel->ShButton("${channel.name} · ${channel.language}",{
                        vm.select(channel)
                        if(vm.state.value.picker==null)runCatching{(if(pane==0)firstPane else secondPane).requestFocus()}
                    },Modifier.fillMaxWidth(),enabled=!s.listing)}
                }
                Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    ShButton("Previous",{vm.loadChannels(s.channels.offset-40)},enabled=!s.listing&&s.channels.offset>0)
                    Text("Page ${s.channels.offset/40+1}",color=t.text)
                    ShButton("Next",{vm.loadChannels(s.channels.offset+40)},enabled=!s.listing&&s.channels.channels.size==40&&(s.channels.total==null||s.channels.offset+40<s.channels.total!!))
                }
            }
        }
        LaunchedEffect(pane){cancel.requestFocus()}
    }
    s.choice?.let{choice->AudioSourceDialog(choice.options,onDismiss=vm::cancelAudio,confirmLabel="Use for this pair once",onChoose=vm::confirmAudio)}
    if(tracks&&s.active) {
        val player=vm.players[s.audioPane]
        val state=if(s.audioPane==0)left else right
        val close={tracks=false;confirm=null;runCatching{audioFocus.requestFocus()};Unit}
        val cancel=remember{FocusRequester()}
        Dialog(onDismissRequest={if(confirm!=null)confirm=null else close()}) {
            Column(Modifier.width(640.dp).heightIn(max=500.dp).background(t.surface,RoundedCornerShape(20.dp)).padding(24.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
                Text("${if(s.audioPane==0)"Left" else "Right"} audio tracks",style=MaterialTheme.typography.titleLarge,color=t.text)
                ShButton(if(confirm==null)"Close" else "Back to tracks",{if(confirm!=null)confirm=null else close()},Modifier.focusRequester(cancel))
                val pending=confirm
                if(pending!=null) {
                    Text("${pending.label}: audio is unverified or outside your preference. Allow only this track for the current playback?",color=t.text)
                    ShButton("Use this track once",{if(player.selectTrack(pending,C.TRACK_TYPE_AUDIO,true))close() else confirm=null})
                } else {
                    if(state.audioTracks.isEmpty())Text("The stream exposes no selectable audio tracks. Its language remains unverified.",color=t.textMuted)
                    LazyColumn(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                        items(state.audioTracks){track->ShButton("${if(track.selected)"✓ " else ""}${track.label} · ${track.language ?: "Unknown"}",{
                            if(player.audioTrackNeedsConfirmation(track))confirm=track else if(player.selectTrack(track,C.TRACK_TYPE_AUDIO))close()
                        })}
                    }
                }
            }
        }
        LaunchedEffect(confirm){cancel.requestFocus()}
    }
}
