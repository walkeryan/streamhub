package com.streamhub.tv.feature.player

import android.view.KeyEvent

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
}

/**
 * The player's remote mapping as a pure function of Android key codes, so it is unit-tested
 * without a device.
 *
 *  Live:  Up/Down, CH+/CH- zap · 0-9 channel number · Select channel list · Left archive · Right menu
 *  Film:  Select or Down brings up the control row (scrubber + buttons) · Left/Right seek 10 s
 *  Both:  hold Select or Menu = audio/subtitle/picture menu · media keys always work
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
     */
    fun resolve(key: Int, isLive: Boolean, menuOpen: Boolean, controlsVisible: Boolean, longPress: Boolean = false): PlayerCommand {
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
            KeyEvent.KEYCODE_LAST_CHANNEL -> return if (isLive) PlayerCommand.LastChannel else PlayerCommand.None
        }
        if (isSelect(key)) return when {
            longPress -> PlayerCommand.OpenMenu
            controlsVisible -> PlayerCommand.None
            isLive -> PlayerCommand.OpenChannelList
            else -> PlayerCommand.ShowControls
        }
        if (controlsVisible) return PlayerCommand.None
        digitOf(key)?.let { return if (isLive) PlayerCommand.Digit(it) else PlayerCommand.None }
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

    fun isSelect(key: Int): Boolean =
        key == KeyEvent.KEYCODE_DPAD_CENTER || key == KeyEvent.KEYCODE_ENTER || key == KeyEvent.KEYCODE_NUMPAD_ENTER

    /** 0-9 from the number row or the numeric keypad; null for anything else. */
    fun digitOf(key: Int): Int? = when (key) {
        in KeyEvent.KEYCODE_0..KeyEvent.KEYCODE_9 -> key - KeyEvent.KEYCODE_0
        in KeyEvent.KEYCODE_NUMPAD_0..KeyEvent.KEYCODE_NUMPAD_9 -> key - KeyEvent.KEYCODE_NUMPAD_0
        else -> null
    }

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
