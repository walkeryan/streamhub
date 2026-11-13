package com.streamhub.tv.core.data.model

import java.util.Locale

/**
 * A readable name for a language code, from [Locale]'s own display names: ISO 639-1 (`en`), 639-2 in
 * both its forms (`fra` and `fre`), 639-3, and a tag with a region or script (`pt-BR`). The viewer
 * is never shown a raw code: one that names no language reads "Unknown".
 */
object LanguageNames {
    const val UNKNOWN = "Unknown"

    /** ISO 639-2/B (bibliographic) codes, which [Locale] does not map to their language itself. */
    private val BIBLIOGRAPHIC = mapOf(
        "alb" to "sq", "arm" to "hy", "baq" to "eu", "bur" to "my", "chi" to "zh", "cze" to "cs", "dut" to "nl",
        "fre" to "fr", "geo" to "ka", "ger" to "de", "gre" to "el", "ice" to "is", "mac" to "mk", "mao" to "mi",
        "may" to "ms", "per" to "fa", "rum" to "ro", "slo" to "sk", "tib" to "bo", "wel" to "cy",
    )

    /** Codes that say something other than a language. */
    private val SPECIAL = mapOf("und" to UNKNOWN, "mis" to UNKNOWN, "mul" to "Multiple languages", "zxx" to "No spoken language", "qaa" to "Original language")

    /** Three-letter (terminologic) code to the two-letter code [Locale] names best. */
    private val THREE_TO_TWO: Map<String, String> by lazy {
        buildMap {
            for (two in Locale.getISOLanguages()) {
                val three = runCatching { Locale.Builder().setLanguage(two).build().isO3Language }.getOrNull()
                if (!three.isNullOrBlank()) putIfAbsent(three, two)
            }
        }
    }

    /** The name of [code] in [locale]; [UNKNOWN] for null, blank, `und` and anything that names no language. */
    fun of(code: String?, locale: Locale = Locale.getDefault()): String {
        val tag = code?.trim()?.replace('_', '-')?.lowercase(Locale.ROOT).orEmpty()
        if (tag.isEmpty()) return UNKNOWN
        val language = tag.substringBefore('-')
        SPECIAL[language]?.let { return it }
        if (language.length !in 2..3 || !language.all { it in 'a'..'z' }) return UNKNOWN
        val canonical = BIBLIOGRAPHIC[language] ?: THREE_TO_TWO[language] ?: language
        val rest = tag.substringAfter('-', "")
        val parsed = runCatching { Locale.forLanguageTag(if (rest.isEmpty()) canonical else "$canonical-$rest") }.getOrNull()
        val named = parsed?.takeIf { it.language.isNotEmpty() } ?: runCatching { Locale.Builder().setLanguage(canonical).build() }.getOrNull() ?: return UNKNOWN
        val name = (if (named.country.isNotEmpty() || named.script.isNotEmpty()) named.getDisplayName(locale) else named.getDisplayLanguage(locale)).trim()
        // A Locale that does not know the language answers with the code itself.
        if (name.isEmpty() || name.equals(named.language, ignoreCase = true) || name.equals(language, ignoreCase = true)) return UNKNOWN
        return name.replaceFirstChar { if (it.isLowerCase()) it.titlecase(locale) else it.toString() }
    }

    /** As [of], but null for a code that names no language, for lines that leave the language out then. */
    fun orNull(code: String?, locale: Locale = Locale.getDefault()): String? =
        of(code, locale).takeIf { it != UNKNOWN }
}
