package com.streamhub.tv.feature.player

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/**
 * The player's icons, drawn from the approved mockup's SVG symbols (24-unit box, 2-unit round
 * strokes) so the TV and the web player look the same. White; tinted where they are used.
 */
internal object PlayerIcons {
    private fun icon(name: String, strokes: List<String> = emptyList(), fills: List<String> = emptyList()): ImageVector =
        ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
            fills.forEach { addPath(addPathNodes(it), fill = SolidColor(Color.White)) }
            strokes.forEach {
                addPath(
                    addPathNodes(it),
                    stroke = SolidColor(Color.White),
                    strokeLineWidth = 2f,
                    strokeLineCap = StrokeCap.Round,
                    strokeLineJoin = StrokeJoin.Round,
                )
            }
        }.build()

    private fun circle(cx: Float, cy: Float, r: Float) = "M${cx - r},$cy a$r,$r 0 1,0 ${2 * r},0 a$r,$r 0 1,0 ${-2 * r},0z"
    private fun rect(x: Float, y: Float, w: Float, h: Float, r: Float) =
        "M${x + r},$y h${w - 2 * r} a$r,$r 0 0,1 $r,$r v${h - 2 * r} a$r,$r 0 0,1 ${-r},$r h${-(w - 2 * r)} a$r,$r 0 0,1 ${-r},${-r} v${-(h - 2 * r)} a$r,$r 0 0,1 $r,${-r}z"

    val Play = icon("play", fills = listOf("M7 4.5v15l13-7.5z"))
    val Pause = icon("pause", fills = listOf(rect(6f, 4.5f, 4f, 15f, 1f), rect(14f, 4.5f, 4f, 15f, 1f)))
    val Next = icon("next", strokes = listOf("M5 5v14l10-7z", "M19 5v14"))
    val Previous = icon("prev", strokes = listOf("M19 5v14L9 12z", "M5 5v14"))
    /** The arrows of "back 10 s" / "forward 30 s"; the number is drawn over them as text. */
    val Back = icon("b10", strokes = listOf("M4 12a8 8 0 1 0 2.4-5.7", "M4 4v5h5"))
    val Forward = icon("f30", strokes = listOf("M20 12a8 8 0 1 1-2.4-5.7", "M20 4v5h-5"))
    val Audio = icon("audio", strokes = listOf("M4 9.5h4l5-4v13l-5-4H4z", "M16.5 8.5a5 5 0 0 1 0 7", "M19 6a8.5 8.5 0 0 1 0 12"))
    val Subtitles = icon("cc", strokes = listOf(rect(3f, 5f, 18f, 14f, 3f), "M10.5 10.2a2.3 2.3 0 1 0 0 3.6M17 10.2a2.3 2.3 0 1 0 0 3.6"))
    val People = icon(
        "people",
        strokes = listOf(circle(9f, 8f, 3.5f), "M2.5 20a6.5 6.5 0 0 1 13 0", circle(17f, 9f, 2.8f), "M16 14.2a5.5 5.5 0 0 1 5.5 5.8"),
    )
    val Settings = icon(
        "gear",
        strokes = listOf(
            circle(12f, 12f, 3f),
            "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
        ),
    )
    val Close = icon("x", strokes = listOf("M6 6l12 12M18 6 6 18"))
    /** Minimise into picture-in-picture: a screen with the small picture in its corner. */
    val Minimise = icon("pip", strokes = listOf(rect(3f, 5f, 18f, 14f, 2.5f)), fills = listOf(rect(12f, 11.5f, 6.5f, 5f, 1f)))
    val SkipForward = Next
}
