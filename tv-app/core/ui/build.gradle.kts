import streamhub.TokensCodegen

plugins {
    id("streamhub.android.compose")
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

dependencies {
    api(project(":core:data"))
    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    ksp(libs.kotlin.metadata.jvm)
    api(platform(libs.compose.bom))
    api(libs.tv.material)
    api(libs.tv.foundation)
    api(libs.compose.foundation)
    api(libs.compose.material.icons)
    api(libs.coil.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.zxing.core)
}

// ---------------------------------------------------------------------------------------------
// Design tokens: design/tokens.json -> src/main/kotlin/.../Tokens.kt (checked in).
// Run `./gradlew :core:ui:generateTokens` after editing tokens.json; `checkTokens` (wired into
// `check`) fails the build if the checked-in file drifted from the JSON.
// ---------------------------------------------------------------------------------------------
val tokensJson = rootProject.file("../design/tokens.json")
val tokensKt = file("src/main/kotlin/com/streamhub/tv/core/ui/theme/Tokens.kt")

val generateTokens by tasks.registering {
    group = "streamhub"
    description = "Regenerate Tokens.kt from design/tokens.json"
    inputs.file(tokensJson)
    outputs.file(tokensKt)
    doLast { tokensKt.writeText(TokensCodegen.generate(tokensJson.readText())) }
}

val checkTokens by tasks.registering {
    group = "verification"
    description = "Fail if Tokens.kt is out of date with design/tokens.json"
    inputs.file(tokensJson)
    inputs.file(tokensKt)
    doLast {
        if (tokensJson.exists()) {
            val expected = TokensCodegen.generate(tokensJson.readText())
            check(tokensKt.readText() == expected) {
                "Tokens.kt is stale. Run ./gradlew :core:ui:generateTokens"
            }
        }
    }
}
tasks.matching { it.name == "check" }.configureEach { dependsOn(checkTokens) }
