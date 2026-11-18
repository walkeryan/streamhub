package com.streamhub.tv.feature.player

import android.view.KeyEvent
import com.streamhub.tv.core.ui.input.ChannelNumberEntry

/** What a remote key means to the player, before any side effect. */
sealed interface PlayerCommand {
    /** Not the player's: the focused control (or the system) handles the key. */
    data object None : PlayerCommand
    /** Close what is open, commit a typed number, swap to the last channel or leave; the screen knows which. */
    data object Back : PlayerCommand
    /** +1 = next channel in the list, -1 = previous. */
    data class Zap(val delta: Int) : PlayerCommand
    data class Digit(val value: Int) : PlayerCommand
    data object OpenChannelList : PlayerCommand
    data object OpenMenu : PlayerCommand
    data object OpenArchive : PlayerCommand
    /** Bring up the film control row with focus on the scrubber. */
    data object ShowControls : PlayerCommand
    data object ShowInfo : PlayerCommand
    data object TogglePlayPause : PlayerCommand
    data object Play : PlayerCommand
    data object Pause : PlayerCommand
    data class Seek(val deltaMs: Long) : PlayerCommand
    data object LastChannel : PlayerCommand
    /** Next / previous episode (media next / previous on a title; nothing when it has no neighbour). */
    data object NextItem : PlayerCommand
    data object PreviousItem : PlayerCommand
}

/** What one press of Back does in the player, decided by [PlayerKeyMap.back]. */
enum class BackAction {
    /** "Keep watching": the Up next card goes until the very end. */
    DismissUpNext,
    /** Close the open menu or the control row. */
    HideOverlay,
    /** Leaving a room is leaving other people: "Leave the room?" first. */
    AskLeaveRoom,
    /** A channel number being typed is committed. */
    CommitNumber,
    /** Live with a last channel: a second Back inside the window swaps channels, else the player closes. */
    LastChannelWindow,
    /** Shrink into the mini player (picture-in-picture); only when the profile asked for it. */
    Minimise,
    /** Stop and return to where the player was opened from. */
    Exit,
}

/**
 * What the player screen knows when Back is pressed (see [PlayerKeyMap.back]).
 *
 * @param canMinimise the player can go into picture-in-picture (the device supports it)
 * @param titlePlaying a title (not live, not a room) is loaded and the viewer means it to play
 * @param backMinimises the profile's `back_minimises` preference ("Back shrinks to mini player")
 */
data class BackContext(
    val upNextCard: Boolean = false,
    val menuOpen: Boolean = false,
    val controlsVisible: Boolean = false,
    val inRoom: Boolean = false,
    val typingNumber: Boolean = false,
    val liveWithLastChannel: Boolean = false,
    val canMinimise: Boolean = false,
    val titlePlaying: Boolean = false,
    val backMinimises: Boolean = false,
)

/**
 * The player's remote mapping as a pure function of Android key codes, so it is unit-tested
 * without a device.
 *
 *  Live:  Up/Down, CH+/CH- zap · 0-9 channel number · Select channel list · Left archive · Right menu
 *  Film:  Select or Down brings up the control row (scrubber + buttons) · Left/Right seek 10 s
 *  Both:  hold Select or Menu = audio/subtitle/picture menu · media keys always work
 *  Title: media next / previous = next / previous episode
 *
 *  Room:  a watch room stays on what it is watching, so on a live channel there is no zapping,
 *         channel list, number entry or archive: Select plays/pauses for everyone, Up/Down show the banner
 *
 * While a menu is open, or the control row has focus, D-pad and Select belong to the focused
 * control and come back as [PlayerCommand.None].
 */
object PlayerKeyMap {
    const val SEEK_STEP_MS = 10_000L
    const val MEDIA_SEEK_STEP_MS = 30_000L

    /**
     * @param isLive true live playback (not a film, not buffered history)
     * @param menuOpen a focus-holding overlay is up: channel list, menu, archive or buffer dialog
     * @param controlsVisible the film control row is up and holds focus
     * @param longPress Select has been held past the long-press timeout
     * @param inRoom playing as a member of a watch room
     */
    fun resolve(key: Int, isLive: Boolean, menuOpen: Boolean, controlsVisible: Boolean, longPress: Boolean = false, inRoom: Boolean = false): PlayerCommand {
        if (key == KeyEvent.KEYCODE_BACK) return PlayerCommand.Back
        if (menuOpen) return PlayerCommand.None
        when (key) {
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> return PlayerCommand.TogglePlayPause
            KeyEvent.KEYCODE_MEDIA_PLAY -> return PlayerCommand.Play
            KeyEvent.KEYCODE_MEDIA_PAUSE -> return PlayerCommand.Pause
            KeyEvent.KEYCODE_MEDIA_REWIND -> return PlayerCommand.Seek(-MEDIA_SEEK_STEP_MS)
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> return PlayerCommand.Seek(MEDIA_SEEK_STEP_MS)
            KeyEvent.KEYCODE_MENU, KeyEvent.KEYCODE_SETTINGS -> return PlayerCommand.OpenMenu
            KeyEvent.KEYCODE_INFO, KeyEvent.KEYCODE_GUIDE -> return PlayerCommand.ShowInfo
            KeyEvent.KEYCODE_LAST_CHANNEL -> return if (isLive && !inRoom) PlayerCommand.LastChannel else PlayerCommand.None
            KeyEvent.KEYCODE_MEDIA_NEXT -> return if (!isLive && !inRoom) PlayerCommand.NextItem else PlayerCommand.None
            KeyEvent.KEYCODE_MEDIA_PREVIOUS -> return if (!isLive && !inRoom) PlayerCommand.PreviousItem else PlayerCommand.None
        }
        if (isSelect(key)) return when {
            longPress -> PlayerCommand.OpenMenu
            controlsVisible -> PlayerCommand.None
            isLive -> if (inRoom) PlayerCommand.TogglePlayPause else PlayerCommand.OpenChannelList
            else -> PlayerCommand.ShowControls
        }
        if (controlsVisible) return PlayerCommand.None
        if (isLive && inRoom) return when (key) {
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> PlayerCommand.ShowInfo
            KeyEvent.KEYCODE_DPAD_RIGHT -> PlayerCommand.OpenMenu
            else -> PlayerCommand.None
        }
        ChannelNumberEntry.digitOf(key)?.let { return if (isLive) PlayerCommand.Digit(it) else PlayerCommand.None }
        return when (key) {
            KeyEvent.KEYCODE_DPAD_UP -> if (isLive) PlayerCommand.Zap(+1) else PlayerCommand.ShowControls
            KeyEvent.KEYCODE_DPAD_DOWN -> if (isLive) PlayerCommand.Zap(-1) else PlayerCommand.ShowControls
            KeyEvent.KEYCODE_CHANNEL_UP -> if (isLive) PlayerCommand.Zap(+1) else PlayerCommand.None
            KeyEvent.KEYCODE_CHANNEL_DOWN -> if (isLive) PlayerCommand.Zap(-1) else PlayerCommand.None
            KeyEvent.KEYCODE_DPAD_LEFT -> if (isLive) PlayerCommand.OpenArchive else PlayerCommand.Seek(-SEEK_STEP_MS)
            KeyEvent.KEYCODE_DPAD_RIGHT -> if (isLive) PlayerCommand.OpenMenu else PlayerCommand.Seek(SEEK_STEP_MS)
            else -> PlayerCommand.None
        }
    }

    /**
     * Back, topmost first: the Up next card, an open menu or the control row, a room (asks first), a
     * typed channel number, the last-channel window. Then the player closes, as it always did, unless
     * the profile turned on "Back shrinks to mini player" and a title is playing: then it minimises.
     * The minimise button minimises whatever this preference says.
     */
    fun back(c: BackContext): BackAction = when {
        c.upNextCard -> BackAction.DismissUpNext
        c.menuOpen || c.controlsVisible -> BackAction.HideOverlay
        c.inRoom -> BackAction.AskLeaveRoom
        c.typingNumber -> BackAction.CommitNumber
        c.liveWithLastChannel -> BackAction.LastChannelWindow
        c.backMinimises && c.canMinimise && c.titlePlaying -> BackAction.Minimise
        else -> BackAction.Exit
    }

    fun isSelect(key: Int): Boolean =
        key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER || key == KeyEvent.KEYCODE_NUMPAD_ENTER

    /**
     * Seek step for one Left/Right event on the scrubber. [repeatCount] is the key event's
     * auto-repeat count: a tap moves 10 s, holding the key speeds up so a two-hour film can be
     * crossed in a few seconds.
     */
    fun seekStepMs(repeatCount: Int): Long = when {
        repeatCount < 5 -> SEEK_STEP_MS
        repeatCount < 15 -> 30_000L
        repeatCount < 30 -> 60_000L
        else -> 120_000L
    }
}
