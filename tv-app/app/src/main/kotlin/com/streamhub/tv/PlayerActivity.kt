package com.streamhub.tv

import android.app.ActivityManager
import android.app.PictureInPictureParams
import android.content.Intent
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.util.Rational
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import com.streamhub.tv.core.data.auth.SessionStore
import com.streamhub.tv.core.data.auth.ShellEntry
import com.streamhub.tv.core.data.auth.shellEntry
import com.streamhub.tv.core.ui.theme.StreamHubTheme
import com.streamhub.tv.core.ui.theme.ThemeRepository
import com.streamhub.tv.feature.player.MiniPlayerCommand
import com.streamhub.tv.feature.player.PlayerScreen
import com.streamhub.tv.feature.player.PlayerViewModel
import com.streamhub.tv.feature.player.miniPlayerInfo
import com.streamhub.tv.shell.NativePlayer
import com.streamhub.tv.shell.PlayerLink
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The native player (rooms, recordings, live zapping, audio tracks, passthrough) as the shell's
 * playback surface. It finds what to play under [EXTRA_KEY], parked by PlaybackCoordinator after the
 * TV resolved the web app's request itself. Close leaves it and the web app is where it was.
 *
 * Mini player: the minimise button, or Back while a title plays, puts this activity into
 * picture-in-picture (16:9). The system then moves it into a task of its own and the shell's web app
 * shows full screen underneath, drawing its mini player bar from the `mini_player` events the shell
 * forwards; the bar's buttons come back as commands. Both travel through [PlayerLink] (a process-wide
 * object: the two activities share the process, and an activity result cannot follow the player into
 * its own task). Expand brings this activity back to full screen; closing the picture-in-picture
 * window, or Close in the bar, stops playback.
 *
 * The screen is kept awake by the video surface while the picture plays, and only then.
 */
@AndroidEntryPoint
class PlayerActivity : ComponentActivity() {
    @Inject lateinit var themes: ThemeRepository
    @Inject lateinit var sessions: SessionStore

    private val vm: PlayerViewModel by viewModels()
    private val pip = MutableStateFlow(false)
    private val pipSupported by lazy { PlayerLink.pipSupported(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // PlayerViewModel reads the key from its SavedStateHandle, which an activity fills from its extras.
        if (intent.getStringExtra(EXTRA_KEY) == null) { finish(); return }
        PlayerLink.attach(this)
        setContent {
            val selected by themes.selectedThemeId.collectAsStateWithLifecycle(ThemeRepository.DEFAULT_THEME)
            val inPip by pip.collectAsStateWithLifecycle()
            StreamHubTheme(tokens = themes.tokens(selected)) {
                PlayerScreen(onExit = ::close, vm = vm, onMinimise = if (pipSupported) ::minimise else null, inPip = inPip)
            }
        }
        if (pipSupported && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) runCatching { setPictureInPictureParams(pipParams()) }
        // Signed out meanwhile (the web app had no token to give): nothing here can play.
        lifecycleScope.launch { sessions.session.first { shellEntry(it) == ShellEntry.Pair }; close() }
        // How this player is shown, for the shell: about once a second (positions are whole seconds) and on every change.
        lifecycleScope.launch {
            combine(vm.state, vm.player.state, pip) { s, ps, mini ->
                if (mini) NativePlayer.Mini(miniPlayerInfo(s, ps, vm.player.wantsToPlay)) else NativePlayer.FullScreen
            }.distinctUntilChanged().collect { PlayerLink.publish(this@PlayerActivity, it) }
        }
        lifecycleScope.launch { PlayerLink.commands.collect(::onCommand) }
    }

    private fun onCommand(c: MiniPlayerCommand) {
        // A player that is being replaced still closes; everything else is the current player's.
        if (c == MiniPlayerCommand.Close) { close(); return }
        if (!PlayerLink.owns(this)) return
        // A press on the bar is somebody watching, like a key press in full screen.
        vm.userInteracted()
        when (c) {
            MiniPlayerCommand.PlayPause -> vm.togglePlayPause()
            is MiniPlayerCommand.SeekBy -> vm.scrubBy(c.deltaMs)
            MiniPlayerCommand.Next -> vm.playNextEpisode()
            MiniPlayerCommand.Previous -> vm.playPreviousEpisode()
            MiniPlayerCommand.Expand -> expand()
            MiniPlayerCommand.Close -> Unit
        }
    }

    @RequiresApi(Build.VERSION_CODES.O)
    private fun pipParams(): PictureInPictureParams =
        PictureInPictureParams.Builder().setAspectRatio(Rational(16, 9)).build()

    /** Into picture-in-picture: overlays close first, the picture keeps playing. */
    private fun minimise() {
        if (!pipSupported || Build.VERSION.SDK_INT < Build.VERSION_CODES.O || isInPictureInPictureMode) return
        vm.hideOverlay()
        val entered = try { enterPictureInPictureMode(pipParams()) } catch (e: IllegalStateException) { Log.w(TAG, "No picture-in-picture: ${e.message}"); false }
        if (!entered) close()
    }

    /**
     * Back to full screen from picture-in-picture. Starting this activity again from its own (pinned)
     * task brings that task out of picture-in-picture; moving the task to the front first covers
     * builds that need it. The new intent only says "expand" (see [onNewIntent]).
     */
    private fun expand() {
        if (!isInPictureInPictureMode) return
        runCatching { getSystemService(ActivityManager::class.java)?.appTasks?.firstOrNull { it.taskInfo.taskId == taskId }?.moveToFront() }
        runCatching {
            startActivity(
                Intent(this, PlayerActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    .putExtra(EXTRA_EXPAND, true),
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Only "expand" ever comes here; a new title opens a new player (the shell closes this one first).
        if (intent.getBooleanExtra(EXTRA_EXPAND, false)) vm.showOsd()
    }

    override fun onPictureInPictureModeChanged(isInPictureInPictureMode: Boolean, newConfig: Configuration) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        pip.value = isInPictureInPictureMode
        when {
            isInPictureInPictureMode -> Unit
            // Left picture-in-picture without coming back to the front: its window was closed.
            !lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED) -> close()
            else -> vm.showOsd()
        }
    }

    override fun onStop() {
        super.onStop()
        // The picture-in-picture window was dismissed (it is stopped while still in that mode): playback ends.
        if (pip.value && !isChangingConfigurations) close()
    }

    /** Stops playback and leaves; a player in a task of its own takes that task with it. */
    private fun close() {
        if (isFinishing) return
        if (isTaskRoot) finishAndRemoveTask() else finish()
    }

    override fun onDestroy() {
        PlayerLink.detach(this)
        super.onDestroy()
    }

    companion object {
        /** Must be "key": the name PlayerViewModel reads. */
        const val EXTRA_KEY = "key"
        private const val EXTRA_EXPAND = "expand"
        private const val TAG = "StreamHubPlayer"
    }
}
