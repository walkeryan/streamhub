package com.streamhub.tv.shell

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import com.streamhub.tv.feature.player.MiniPlayerCommand
import com.streamhub.tv.feature.player.MiniPlayerInfo
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Whether the native player is open, and how. */
sealed interface NativePlayer {
    data object None : NativePlayer
    data object FullScreen : NativePlayer
    /** In picture-in-picture over the web app; [info] is what the web app's bar shows (null until known). */
    data class Mini(val info: MiniPlayerInfo?) : NativePlayer
}

/**
 * The channel between [com.streamhub.tv.PlayerActivity] and the shell ([com.streamhub.tv.MainActivity]).
 *
 * They are separate activities, and in picture-in-picture the player even sits in a task of its own,
 * so an activity result cannot carry this any more (the player no longer finishes when it is left).
 * Both live in one process, though: this process-wide object is the whole channel. The player
 * [attach]es when it opens and publishes how it is shown ([publish]); the shell observes [state],
 * forwards it to the web app as `mini_player` events, and treats [NativePlayer.None] as "the player
 * closed" (what the activity result used to say). The web app's bar sends commands through the
 * shell's bridge into [commands], which only the attached player collects.
 *
 * An owner token keeps a player that is going away (a PiP player replaced by a new title) from
 * clearing the state the new one has already set.
 */
object PlayerLink {
    private val _state = MutableStateFlow<NativePlayer>(NativePlayer.None)
    val state: StateFlow<NativePlayer> = _state.asStateFlow()

    private val _commands = MutableSharedFlow<MiniPlayerCommand>(extraBufferCapacity = 8, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val commands: SharedFlow<MiniPlayerCommand> = _commands

    private var owner: Any? = null

    @Synchronized fun attach(player: Any) { owner = player; _state.value = NativePlayer.FullScreen }

    @Synchronized fun publish(player: Any, shown: NativePlayer) { if (owner === player) _state.value = shown }

    @Synchronized fun owns(player: Any): Boolean = owner === player

    @Synchronized fun detach(player: Any) { if (owner === player) { owner = null; _state.value = NativePlayer.None } }

    /** From the web app's bar; dropped unless a player is in picture-in-picture. */
    fun command(c: MiniPlayerCommand): Boolean = state.value is NativePlayer.Mini && _commands.tryEmit(c)

    /** A new title replaces whatever player is open: the old one closes. */
    fun closeOpenPlayer() { if (state.value != NativePlayer.None) _commands.tryEmit(MiniPlayerCommand.Close) }

    fun pipSupported(context: Context): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && context.packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)
}
