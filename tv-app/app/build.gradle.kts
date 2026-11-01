import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

// Private signing credentials are read only from an explicitly supplied local file.
// CI and unconfigured developer builds stay unsigned; no ephemeral debug identity.
val privateSigning = providers.environmentVariable("STREAMHUB_SIGNING_PROPERTIES").orNull?.let { path ->
    Properties().apply { file(path).inputStream().use { load(it) } }
}

android {
    val durableSigning = privateSigning?.let { props ->
        signingConfigs.create("streamhub") {
            storeFile = file(requireNotNull(props.getProperty("storeFile")))
            storePassword = requireNotNull(props.getProperty("storePassword"))
            keyAlias = requireNotNull(props.getProperty("keyAlias"))
            keyPassword = requireNotNull(props.getProperty("keyPassword"))
        }
    }
    namespace = "com.streamhub.tv"
    compileSdk = libs.versions.compileSdk.get().toInt()

    defaultConfig {
        applicationId = "com.streamhub.tv"
        minSdk = libs.versions.minSdk.get().toInt()
        targetSdk = libs.versions.targetSdk.get().toInt()
        versionCode = 2
        versionName = "1.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = durableSigning
        }
        debug {
            signingConfig = durableSigning
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        isCoreLibraryDesugaringEnabled = true
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf("META-INF/INDEX.LIST", "META-INF/io.netty.versions.properties", "META-INF/{AL2.0,LGPL2.1}")
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
        optIn.addAll(
            "androidx.compose.foundation.ExperimentalFoundationApi",
            "androidx.compose.ui.ExperimentalComposeUiApi",
            "androidx.tv.material3.ExperimentalTvMaterial3Api",
        )
    }
}

dependencies {
    implementation(project(":core:ui"))
    implementation(project(":core:data"))
    implementation(project(":core:player"))
    implementation(project(":feature:onboarding"))
    implementation(project(":feature:home"))
    implementation(project(":feature:search"))
    implementation(project(":feature:livetv"))
    implementation(project(":feature:guide"))
    implementation(project(":feature:details"))
    implementation(project(":feature:player"))
    implementation(project(":feature:favorites"))
    implementation(project(":feature:sources"))
    implementation(project(":feature:settings"))

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material.icons)
    implementation(libs.tv.material)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.hilt.android)
    implementation(libs.hilt.navigation.compose)
    ksp(libs.hilt.compiler)
    ksp(libs.kotlin.metadata.jvm)
    implementation(libs.coil.compose)
    implementation(libs.coil.network.ktor)
    implementation(libs.ktor.client.core)
    implementation(libs.ktor.client.cio)
    coreLibraryDesugaring(libs.desugar.jdk.libs)
    debugImplementation(libs.compose.ui.tooling)
}
