package com.streamhub.tv.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PipLayoutTest {
    private fun report(json: String) = parseKeepClear(json)

    // ---- keep_clear messages ------------------------------------------------------------------------

    @Test fun theFocusedElementAndTheBarArriveAsCssRects() {
        val r = report("""{"v":1,"kind":"keep_clear","viewport_width":1920,"rects":[{"x":100,"y":200.5,"w":300,"h":170},{"x":0,"y":984,"w":1920,"h":96}],"extra":1}""")!!
        assertEquals(listOf(CssRect(100.0, 200.5, 300.0, 170.0), CssRect(0.0, 984.0, 1920.0, 96.0)), r.rects)
        assertEquals(1920.0, r.viewportWidth!!, 0.0)
        assertEquals("an empty list clears", emptyList<CssRect>(), report("""{"v":1,"kind":"keep_clear","rects":[]}""")!!.rects)
        assertNull("no viewport width: the WebView's own scale is used", report("""{"v":1,"kind":"keep_clear","rects":[]}""")!!.viewportWidth)
    }

    @Test fun anythingMalformedIsRefused() {
        for (bad in listOf(
            "not json", "[]",
            """{"v":2,"kind":"keep_clear","rects":[]}""",
            """{"v":1,"kind":"mini_player_command","rects":[]}""",
            """{"v":1,"kind":"keep_clear"}""",
            """{"v":1,"kind":"keep_clear","rects":[{"x":"1","y":0,"w":1,"h":1}]}""",
            """{"v":1,"kind":"keep_clear","rects":[{"x":0,"y":0,"w":-1,"h":1}]}""",
            """{"v":1,"kind":"keep_clear","rects":[{"x":0,"y":0,"w":1}]}""",
            """{"v":1,"kind":"keep_clear","rects":[{"x":1e9,"y":0,"w":1,"h":1}]}""",
            """{"v":1,"kind":"keep_clear","rects":[1]}""",
            """{"v":1,"kind":"keep_clear","rects":[${List(9) { """{"x":0,"y":0,"w":1,"h":1}""" }.joinToString(",")}]}""",
            """{"v":1,"kind":"keep_clear","rects":[],"pad":"${"x".repeat(5000)}"}""",
        )) assertNull(bad.take(60), report(bad))
        assertNull("a bar command is not a keep-clear report", parseMiniPlayerCommand("""{"v":1,"kind":"keep_clear","rects":[]}"""))
    }

    // ---- CSS px → device px ------------------------------------------------------------------------

    @Test fun cssPixelsBecomeTheWebViewsPixelsAtItsScale() {
        // 1920 CSS px laid out across a 3840 px wide WebView (a 4K output): two device px per CSS px.
        val r = report("""{"v":1,"kind":"keep_clear","viewport_width":1920,"rects":[{"x":100,"y":200,"w":300,"h":170}]}""")!!
        assertEquals(listOf(PxRect(200, 400, 800, 740)), keepClearPx(r, 3840, 2160, fallbackScale = 9f))
        // 1080p output: one to one.
        assertEquals(listOf(PxRect(100, 200, 400, 370)), keepClearPx(r, 1920, 1080, fallbackScale = 9f))
    }

    @Test fun withoutTheViewportWidthTheWebViewScaleIsUsed() {
        val r = report("""{"v":1,"kind":"keep_clear","rects":[{"x":10,"y":10,"w":10,"h":10}]}""")!!
        assertEquals(listOf(PxRect(15, 15, 30, 30)), keepClearPx(r, 1920, 1080, fallbackScale = 1.5f))
        assertEquals("no usable scale at all: one to one", listOf(PxRect(10, 10, 20, 20)), keepClearPx(r, 1920, 1080, fallbackScale = 0f))
    }

    @Test fun rectsRoundOutwardsAreClippedToTheViewAndEmptyOnesGo() {
        val r = report("""{"v":1,"kind":"keep_clear","viewport_width":1920,"rects":[{"x":10.4,"y":10.6,"w":0.2,"h":0.2},{"x":-50,"y":1000,"w":200,"h":500},{"x":5000,"y":0,"w":10,"h":10},{"x":0,"y":0,"w":0,"h":10}]}""")!!
        val px = keepClearPx(r, 1280, 720, 1f) // 1280 / 1920 device px per CSS px
        assertEquals("never shrinks off what it covers", PxRect(6, 7, 8, 8), px[0])
        assertEquals("clipped to the view", PxRect(0, 666, 100, 720), px[1])
        assertEquals("off-screen and empty rects are dropped", 2, px.size)
        assertTrue("an unmeasured view takes nothing", keepClearPx(r, 0, 0, 1f).isEmpty())
    }

    // ---- the PiP window, for the page ---------------------------------------------------------------

    @Test fun theWindowIsReportedInThePagesCssPixels() {
        // A 4K screen, the window in the top right corner, the WebView at the screen's origin.
        val css = pipInCss(PxRect(2880, 96, 3744, 582), 0, 0, 3840, 2160, viewportCssWidth = 1920.0, fallbackScale = 1f)
        assertEquals(CssRect(1440.0, 48.0, 432.0, 243.0), css)
        assertNull("off the view", pipInCss(PxRect(4000, 0, 4100, 100), 0, 0, 3840, 2160, 1920.0, 1f))
        assertNull("empty", pipInCss(PxRect(10, 10, 10, 20), 0, 0, 3840, 2160, 1920.0, 1f))
        assertNotNull("the fallback scale works too", pipInCss(PxRect(0, 0, 100, 100), 0, 0, 1920, 1080, null, 1f))
    }

    // ---- the window's shape --------------------------------------------------------------------------

    @Test fun theWindowTakesThePicturesAspectRatioWithinAndroidsLimits() {
        assertEquals(1778 to 1000, pipAspect(1920, 1080))
        assertEquals(1333 to 1000, pipAspect(640, 480))
        assertEquals("anamorphic: the pixel aspect counts", 1778 to 1000, pipAspect(1440, 1080, pixelRatio = 4f / 3f))
        assertEquals("scope films are clamped to 2.39:1", 2390 to 1000, pipAspect(3840, 1240))
        assertEquals("portrait is clamped to 1:2.39", 419 to 1000, pipAspect(400, 2000))
        assertNull("no video size yet", pipAspect(0, 0))
        val (n, d) = pipAspect(1920, 1)!!
        assertTrue(n.toDouble() / d <= PIP_MAX_ASPECT)
    }

    @Test fun theSourceRectIsThePictureFittedAndCentred() {
        assertEquals("16:9 on a 16:9 screen: all of it", PxRect(0, 0, 1920, 1080), pipSourceRect(1920, 1080, 16 to 9))
        assertEquals("4:3 pillarboxed", PxRect(240, 0, 1680, 1080), pipSourceRect(1920, 1080, 4 to 3))
        assertEquals("2.39:1 letterboxed", PxRect(0, 138, 1920, 941), pipSourceRect(1920, 1080, 2390 to 1000))
        assertNull("not laid out yet", pipSourceRect(0, 0, 16 to 9))
        assertNull("no aspect", pipSourceRect(1920, 1080, null))
    }
}
