plugins {
    id("streamhub.android.feature")
}

android {
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

dependencies {
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.robolectric)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    debugImplementation(libs.compose.ui.test.manifest)
}

// Robolectric screenshots of the glass screens and dialogs (written only when asked for:
// -Pstreamhub.screenshots=<dir>, and -Pstreamhub.screenshotArt=<dir> for the picture behind player dialogs).
tasks.withType<Test>().configureEach {
    systemProperty("streamhub.screenshots", providers.gradleProperty("streamhub.screenshots").getOrElse(""))
    systemProperty("streamhub.screenshotArt", providers.gradleProperty("streamhub.screenshotArt").getOrElse(""))
}

