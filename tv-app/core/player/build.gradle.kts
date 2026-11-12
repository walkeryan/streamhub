plugins {
    id("streamhub.android.compose")
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

dependencies {
    implementation(project(":core:data"))
    api(libs.media3.exoplayer)
    api(libs.media3.exoplayer.hls)
    api(libs.media3.exoplayer.dash)
    api(libs.media3.ui)
    api(libs.media3.session)
    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    ksp(libs.kotlin.metadata.jvm)
    implementation(libs.kotlinx.coroutines.android)
    // Optional software audio decoding (AC-3 / E-AC-3 / DTS on TVs without those decoders):
    //   ./gradlew :app:assembleDebug -Pstreamhub.ffmpegDecoder=true
    // Off by default: the prebuilt FFmpeg build is GPL-3.0 and the default APK must not inherit that.
    // StreamHubPlayer picks the renderer up by reflection, so no source changes with the flag.
    // See README.md "Software audio decoding".
    if (providers.gradleProperty("streamhub.ffmpegDecoder").orNull == "true") {
        implementation(libs.media3.ffmpeg.decoder)
    }
}
