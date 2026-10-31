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
    // FFmpeg decoder extension (AC-3/E-AC-3/DTS on devices without HW decoders) is NOT published to
    // Maven. See README.md "FFmpeg decoder extension". Once built, drop the AAR in core/player/libs/
    // and uncomment:
    // implementation(files("libs/lib-decoder-ffmpeg-release.aar"))
}
