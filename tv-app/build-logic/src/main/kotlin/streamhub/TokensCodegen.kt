package streamhub

import groovy.json.JsonSlurper
import java.util.Locale

/**
 * Generates `Tokens.kt` (Kotlin/Compose) from `design/tokens.json`.
 * Deterministic output so `checkTokens` can diff it against the checked-in file.
 */
object TokensCodegen {

    @Suppress("UNCHECKED_CAST")
    fun generate(json: String): String {
        val root = JsonSlurper().parseText(json) as Map<String, Any?>
        val canvas = root["canvas"] as Map<String, Any?>
        val safe = canvas["safe_margin_dp"] as Map<String, Any?>
        val design = canvas["design_dp"] as List<Number>
        val radius = root["radius"] as Map<String, Number>
        val spacing = root["spacing"] as Map<String, Number>
        val type = root["type"] as Map<String, Any?>
        val focus = root["focus"] as Map<String, Any?>
        val motion = root["motion"] as Map<String, Number>
        val tile = root["tile"] as Map<String, Number>
        val rail = root["rail"] as Map<String, Number>
        val themes = root["themes"] as Map<String, Map<String, Any?>>

        val sb = StringBuilder()
        sb.appendLine("// GENERATED from design/tokens.json by `./gradlew :core:ui:generateTokens`. Do not edit by hand.")
        sb.appendLine("@file:Suppress(\"MagicNumber\")")
        sb.appendLine()
        sb.appendLine("package com.streamhub.tv.core.ui.theme")
        sb.appendLine()
        sb.appendLine("import androidx.compose.ui.graphics.Color")
        sb.appendLine("import androidx.compose.ui.text.font.FontWeight")
        sb.appendLine("import androidx.compose.ui.unit.dp")
        sb.appendLine("import androidx.compose.ui.unit.sp")
        sb.appendLine()
        sb.appendLine("object Tokens {")
        sb.appendLine("    object Canvas {")
        sb.appendLine("        const val DESIGN_WIDTH_DP = ${design[0].toInt()}")
        sb.appendLine("        const val DESIGN_HEIGHT_DP = ${design[1].toInt()}")
        sb.appendLine("        val SafeMarginX = ${dp(safe["x"] as Number)}")
        sb.appendLine("        val SafeMarginY = ${dp(safe["y"] as Number)}")
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Radius {")
        radius.forEach { (k, v) -> sb.appendLine("        val ${camelUpper(k)} = ${dp(v)}") }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Spacing {")
        spacing.forEach { (k, v) -> sb.appendLine("        val ${camelUpper(k)} = ${dp(v)}") }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Type {")
        sb.appendLine("        const val FAMILY = \"${type["family"]}\"")
        sb.appendLine("        val MinWeight = FontWeight(${(type["min_weight"] as Number).toInt()})")
        for (style in listOf("display", "title", "heading", "body", "label")) {
            val s = type[style] as Map<String, Number>
            val size = s["size"]!!.toDouble()
            val line = s["line"]!!.toDouble()
            sb.appendLine(
                "        val ${camelUpper(style)} = TypeToken(size = ${fmt(size)}.sp, " +
                    "weight = FontWeight(${s["weight"]!!.toInt()}), lineHeight = ${fmt(size * line)}.sp)",
            )
        }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Focus {")
        sb.appendLine("        const val SCALE = ${fmt((focus["scale"] as Number).toDouble())}f")
        sb.appendLine("        val Border = ${dp(focus["border_dp"] as Number)}")
        sb.appendLine("        val GlowBlur = ${dp(focus["glow_blur_dp"] as Number)}")
        sb.appendLine("        const val GLOW_ALPHA = ${fmt((focus["glow_alpha"] as Number).toDouble())}f")
        sb.appendLine("        const val DURATION_MS = ${(focus["duration_ms"] as Number).toInt()}")
        sb.appendLine("        const val EASING = \"${focus["easing"]}\"")
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Motion {")
        motion.forEach { (k, v) -> sb.appendLine("        const val ${k.uppercase(Locale.ROOT)} = ${v.toInt()}") }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Tile {")
        tile.forEach { (k, v) -> sb.appendLine("        val ${camelUpper(k)} = ${dp(v)}") }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    object Rail {")
        rail.forEach { (k, v) -> sb.appendLine("        val ${camelUpper(k)} = ${dp(v)}") }
        sb.appendLine("    }")
        sb.appendLine()
        sb.appendLine("    val themes: Map<String, ThemeTokens> = linkedMapOf(")
        themes.forEach { (id, raw) ->
            val parent = (raw["extends"] as String?)?.let { themes[it] } ?: emptyMap()
            val t = parent + raw.filterKeys { it != "extends" }
            val dynamic = t.values.any { it is String && it.startsWith("dynamic:") }
            fun c(key: String): String {
                val v = t[key] as String
                return if (v.startsWith("dynamic:")) color(parent[key] as String) else color(v)
            }
            val scale = (t["focus_scale_override"] as Number?)?.toDouble() ?: (focus["scale"] as Number).toDouble()
            val border = (t["focus_border_override"] as Number?) ?: (focus["border_dp"] as Number)
            sb.appendLine("        \"$id\" to ThemeTokens(")
            sb.appendLine("            id = \"$id\",")
            sb.appendLine("            name = \"${t["name"]}\",")
            for (k in COLOR_KEYS) sb.appendLine("            ${camelLower(k)} = ${c(k)},")
            sb.appendLine("            focusScale = ${fmt(scale)}f,")
            sb.appendLine("            focusBorder = ${dp(border)},")
            sb.appendLine("            dynamicAccent = $dynamic,")
            sb.appendLine("            isLight = ${isLight(c("bg"), t["bg"] as String)},")
            sb.appendLine("        ),")
        }
        sb.appendLine("    )")
        sb.appendLine("}")
        return sb.toString()
    }

    private val COLOR_KEYS = listOf(
        "bg", "surface", "surface_alt", "overlay", "text", "text_muted", "accent", "accent_text",
        "focus_ring", "focus_glow", "danger", "success", "scrim_top", "scrim_bottom",
    )

    private fun isLight(@Suppress("UNUSED_PARAMETER") expr: String, hex: String): Boolean {
        val (r, g, b, _) = parse(hex)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0 > 0.5
    }

    private fun parse(v: String): List<Int> {
        val s = v.trim()
        if (s.startsWith("#")) {
            val h = s.substring(1)
            val r = h.substring(0, 2).toInt(16)
            val g = h.substring(2, 4).toInt(16)
            val b = h.substring(4, 6).toInt(16)
            val a = if (h.length == 8) h.substring(6, 8).toInt(16) else 255
            return listOf(r, g, b, a)
        }
        val m = Regex("""rgba?\(([^)]*)\)""").find(s) ?: error("Unsupported colour: $v")
        val p = m.groupValues[1].split(',').map { it.trim() }
        val a = if (p.size > 3) Math.round(p[3].toDouble() * 255).toInt() else 255
        return listOf(p[0].toInt(), p[1].toInt(), p[2].toInt(), a)
    }

    private fun color(v: String): String {
        val (r, g, b, a) = parse(v)
        return "Color(0x%02X%02X%02X%02X)".format(Locale.ROOT, a, r, g, b)
    }

    private fun dp(n: Number): String = if (n.toDouble() % 1.0 == 0.0) "${n.toInt()}.dp" else "${fmt(n.toDouble())}.dp"
    private fun fmt(d: Double): String {
        val r = Math.round(d * 1000.0) / 1000.0
        return if (r % 1.0 == 0.0) "${r.toLong()}.0" else r.toString()
    }
    private fun camelLower(k: String): String = k.split('_').mapIndexed { i, s -> if (i == 0) s else s.replaceFirstChar { it.uppercase() } }.joinToString("")
    private fun camelUpper(k: String): String = camelLower(k).replaceFirstChar { it.uppercase() }
}
