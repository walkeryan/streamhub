package com.streamhub.tv.core.data

import com.streamhub.tv.core.data.model.LanguageNames
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Locale

class LanguageNamesTest {
    private fun name(code: String?) = LanguageNames.of(code, Locale.ENGLISH)

    @Test fun twoAndThreeLetterCodesNameTheSameLanguage() {
        assertEquals("English", name("en"))
        assertEquals("English", name("eng"))
        assertEquals("Spanish", name("spa"))
        assertEquals("Japanese", name("jpn"))
        assertEquals("Korean", name("KOR"))
    }

    @Test fun bibliographicCodesAreNamedToo() {
        assertEquals("French", name("fre"))
        assertEquals("French", name("fra"))
        assertEquals("German", name("ger"))
        assertEquals("German", name("deu"))
        assertEquals("Chinese", name("chi"))
        assertEquals("Dutch", name("dut"))
        assertEquals("Greek", name("gre"))
    }

    @Test fun aRegionIsKept() {
        assertEquals("Portuguese (Brazil)", name("pt-BR"))
        assertEquals("Portuguese (Brazil)", name("pt_BR"))
    }

    @Test fun aRawCodeIsNeverShown() {
        for (code in listOf("und", "mis", "", "  ", null, "xx", "qqq", "zz9", "english", "e")) {
            assertEquals("for $code", LanguageNames.UNKNOWN, name(code))
        }
        assertEquals("Multiple languages", name("mul"))
        assertEquals("No spoken language", name("zxx"))
        assertNull(LanguageNames.orNull("und", Locale.ENGLISH))
        assertEquals("Italian", LanguageNames.orNull("ita", Locale.ENGLISH))
    }

    @Test fun everyIsoLanguageHasANameThatIsNotItsCode() {
        for (two in Locale.getISOLanguages()) {
            val three = Locale.Builder().setLanguage(two).build().isO3Language
            val shown = name(three)
            assert(!shown.equals(three, ignoreCase = true)) { "$three is shown raw" }
        }
    }
}
