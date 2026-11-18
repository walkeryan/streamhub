plugins {
    id("streamhub.android.feature")
}

android {
    // Robolectric renders the player overlays for the screenshot test (PlayerScreenshotTest).
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

dependencies {
    implementation(project(":core:player"))
    testImplementation(libs.robolectric)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    debugImplementation(libs.compose.ui.test.manifest)
}

// Screenshots are written only when asked for: -Pstreamhub.screenshots=<dir> (and optionally
// -Pstreamhub.screenshotArt=<dir with backdrop.jpg / ep2.jpg / ep4.jpg> for the picture behind them).
tasks.withType<Test>().configureEach {
    systemProperty("streamhub.screenshots", providers.gradleProperty("streamhub.screenshots").getOrElse(""))
    systemProperty("streamhub.screenshotArt", providers.gradleProperty("streamhub.screenshotArt").getOrElse(""))
}
