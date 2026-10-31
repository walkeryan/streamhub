package streamhub

import com.android.build.api.dsl.LibraryExtension
import org.gradle.api.JavaVersion
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.artifacts.VersionCatalog
import org.gradle.api.artifacts.VersionCatalogsExtension
import org.gradle.kotlin.dsl.configure
import org.gradle.kotlin.dsl.dependencies
import org.gradle.kotlin.dsl.getByType
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.KotlinAndroidProjectExtension

internal val Project.libs: VersionCatalog
    get() = extensions.getByType<VersionCatalogsExtension>().named("libs")

internal fun VersionCatalog.v(name: String): String = findVersion(name).get().requiredVersion
internal fun VersionCatalog.lib(name: String) = findLibrary(name).get()

class AndroidLibraryConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) = with(target) {
        pluginManager.apply("com.android.library")
        pluginManager.apply("org.jetbrains.kotlin.android")
        extensions.configure<LibraryExtension> {
            compileSdk = libs.v("compileSdk").toInt()
            defaultConfig {
                minSdk = libs.v("minSdk").toInt()
            }
            compileOptions {
                sourceCompatibility = JavaVersion.VERSION_17
                targetCompatibility = JavaVersion.VERSION_17
                isCoreLibraryDesugaringEnabled = true
            }
            // Namespace derived from module path: :feature:livetv -> com.streamhub.tv.feature.livetv
            namespace = "com.streamhub.tv" + path.replace(':', '.')
        }
        extensions.configure<KotlinAndroidProjectExtension> {
            compilerOptions {
                jvmTarget.set(JvmTarget.JVM_17)
            }
        }
        dependencies {
            add("coreLibraryDesugaring", libs.lib("desugar-jdk-libs"))
            add("testImplementation", libs.lib("junit"))
        }
    }
}

class AndroidComposeConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) = with(target) {
        pluginManager.apply("streamhub.android.library")
        pluginManager.apply("org.jetbrains.kotlin.plugin.compose")
        extensions.configure<LibraryExtension> {
            buildFeatures { compose = true }
        }
        extensions.configure<KotlinAndroidProjectExtension> {
            compilerOptions {
                optIn.addAll(
                    "androidx.compose.foundation.ExperimentalFoundationApi",
                    "androidx.compose.ui.ExperimentalComposeUiApi",
                    "androidx.tv.material3.ExperimentalTvMaterial3Api",
                )
            }
        }
        dependencies {
            add("implementation", platform(libs.lib("compose-bom")))
            add("implementation", libs.lib("compose-ui"))
            add("implementation", libs.lib("compose-foundation"))
            add("implementation", libs.lib("compose-animation"))
            add("implementation", libs.lib("compose-ui-tooling-preview"))
            add("implementation", libs.lib("compose-material-icons"))
            add("implementation", libs.lib("tv-material"))
            add("implementation", libs.lib("tv-foundation"))
            add("debugImplementation", libs.lib("compose-ui-tooling"))
        }
    }
}

class AndroidFeatureConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) = with(target) {
        pluginManager.apply("streamhub.android.compose")
        pluginManager.apply("com.google.devtools.ksp")
        pluginManager.apply("com.google.dagger.hilt.android")
        dependencies {
            add("implementation", project(":core:ui"))
            add("implementation", project(":core:data"))
            add("implementation", libs.lib("hilt-android"))
            add("ksp", libs.lib("hilt-compiler"))
            add("ksp", libs.lib("kotlin-metadata-jvm"))
            add("implementation", libs.lib("hilt-navigation-compose"))
            add("implementation", libs.lib("androidx-lifecycle-runtime-compose"))
            add("implementation", libs.lib("androidx-lifecycle-viewmodel-compose"))
            add("implementation", libs.lib("androidx-navigation-compose"))
            add("implementation", libs.lib("androidx-activity-compose"))
            add("implementation", libs.lib("coil-compose"))
            add("implementation", libs.lib("kotlinx-coroutines-android"))
        }
    }
}
