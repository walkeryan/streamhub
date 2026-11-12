package com.streamhub.tv.feature.player

import android.view.KeyEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayerKeyMapTest {
    private fun live(key: Int, longPress: Boolean = false) = PlayerKeyMap.resolve(key, isLive = true, menuOpen = false, controlsVisible = false, longPress = longPress)
    private fun film(key: Int, controls: Boolean = false, longPress: Boolean = false) =
        PlayerKeyMap.resolve(key, isLive = false, menuOpen = false, controlsVisible = controls, longPress = longPress)

    @Test fun backIsAlwaysThePlayers() {
        for (live in listOf(true, false)) for (menu in listOf(true, false)) for (controls in listOf(true, false)) {
            assertEquals(PlayerCommand.Back, PlayerKeyMap.resolve(KeyEvent.KEYCODE_BACK, live, menu, controls))
        }
    }

    @Test fun liveZapsWithDpadAndChannelKeys() {
        assertEquals(PlayerCommand.Zap(+1), live(KeyEvent.KEYCODE_DPAD_UP))
        assertEquals(PlayerCommand.Zap(-1), live(KeyEvent.KEYCODE_DPAD_DOWN))
        assertEquals(PlayerCommand.Zap(+1), live(KeyEvent.KEYCODE_CHANNEL_UP))
        assertEquals(PlayerCommand.Zap(-1), live(KeyEvent.KEYCODE_CHANNEL_DOWN))
    }

    @Test fun liveSelectOpensTheChannelListAndHoldingItOpensTheMenu() {
        for (key in listOf(KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER)) {
            assertEquals(PlayerCommand.OpenChannelList, live(key))
            assertEquals(PlayerCommand.OpenMenu, live(key, longPress = true))
        }
    }

    @Test fun liveLeftIsArchiveAndRightIsMenu() {
        assertEquals(PlayerCommand.OpenArchive, live(KeyEvent.KEYCODE_DPAD_LEFT))
        assertEquals(PlayerCommand.OpenMenu, live(KeyEvent.KEYCODE_DPAD_RIGHT))
    }

    @Test fun digitsTypeAChannelNumberOnlyInLive() {
        assertEquals(PlayerCommand.Digit(0), live(KeyEvent.KEYCODE_0))
        assertEquals(PlayerCommand.Digit(7), live(KeyEvent.KEYCODE_7))
        assertEquals(PlayerCommand.Digit(9), live(KeyEvent.KEYCODE_NUMPAD_9))
        assertEquals(PlayerCommand.None, film(KeyEvent.KEYCODE_7))
    }

    @Test fun filmSelectOrDownBringsUpTheControls() {
        assertEquals(PlayerCommand.ShowControls, film(KeyEvent.KEYCODE_DPAD_CENTER))
        assertEquals(PlayerCommand.ShowControls, film(KeyEvent.KEYCODE_DPAD_DOWN))
        assertEquals(PlayerCommand.ShowControls, film(KeyEvent.KEYCODE_DPAD_UP))
        assertEquals(PlayerCommand.OpenMenu, film(KeyEvent.KEYCODE_DPAD_CENTER, longPress = true))
    }

    @Test fun filmLeftRightSeekTenSecondsWhileTheControlsAreHidden() {
        assertEquals(PlayerCommand.Seek(-10_000), film(KeyEvent.KEYCODE_DPAD_LEFT))
        assertEquals(PlayerCommand.Seek(10_000), film(KeyEvent.KEYCODE_DPAD_RIGHT))
    }

    @Test fun visibleControlsOwnDpadAndSelect() {
        for (key in listOf(KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN, KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER)) {
            assertEquals(PlayerCommand.None, film(key, controls = true))
        }
    }

    @Test fun openMenuSwallowsNothingButBack() {
        for (key in listOf(KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_5, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MENU)) {
            assertEquals(PlayerCommand.None, PlayerKeyMap.resolve(key, isLive = true, menuOpen = true, controlsVisible = false))
            assertEquals(PlayerCommand.None, PlayerKeyMap.resolve(key, isLive = false, menuOpen = true, controlsVisible = false, longPress = true))
        }
    }

    @Test fun mediaKeysWorkWhateverHasFocus() {
        for (controls in listOf(true, false)) {
            assertEquals(PlayerCommand.TogglePlayPause, film(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, controls))
            assertEquals(PlayerCommand.Play, film(KeyEvent.KEYCODE_MEDIA_PLAY, controls))
            assertEquals(PlayerCommand.Pause, film(KeyEvent.KEYCODE_MEDIA_PAUSE, controls))
            assertEquals(PlayerCommand.Seek(-30_000), film(KeyEvent.KEYCODE_MEDIA_REWIND, controls))
            assertEquals(PlayerCommand.Seek(30_000), film(KeyEvent.KEYCODE_MEDIA_FAST_FORWARD, controls))
            assertEquals(PlayerCommand.OpenMenu, film(KeyEvent.KEYCODE_MENU, controls))
        }
        assertEquals(PlayerCommand.TogglePlayPause, live(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE))
        assertEquals(PlayerCommand.ShowInfo, live(KeyEvent.KEYCODE_INFO))
    }

    @Test fun lastChannelKeyIsLiveOnly() {
        assertEquals(PlayerCommand.LastChannel, live(KeyEvent.KEYCODE_LAST_CHANNEL))
        assertEquals(PlayerCommand.None, film(KeyEvent.KEYCODE_LAST_CHANNEL))
    }

    @Test fun channelKeysAndUnknownKeysDoNothingInAFilm() {
        assertEquals(PlayerCommand.None, film(KeyEvent.KEYCODE_CHANNEL_UP))
        assertEquals(PlayerCommand.None, film(KeyEvent.KEYCODE_CHANNEL_DOWN))
        assertEquals(PlayerCommand.None, film(KeyEvent.KEYCODE_VOLUME_UP))
        assertEquals(PlayerCommand.None, live(KeyEvent.KEYCODE_VOLUME_UP))
    }

    @Test fun holdingTheScrubberAccelerates() {
        assertEquals(10_000L, PlayerKeyMap.seekStepMs(0))
        assertEquals(10_000L, PlayerKeyMap.seekStepMs(4))
        assertEquals(30_000L, PlayerKeyMap.seekStepMs(5))
        assertEquals(60_000L, PlayerKeyMap.seekStepMs(15))
        assertEquals(120_000L, PlayerKeyMap.seekStepMs(30))
        val steps = (0..60).map(PlayerKeyMap::seekStepMs)
        assertTrue(steps.zipWithNext().all { (a, b) -> b >= a })
    }
}
