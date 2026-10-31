plugins {
    `kotlin-dsl`
}

dependencies {
    compileOnly("com.android.tools.build:gradle:${libs.versions.agp.get()}")
    compileOnly("org.jetbrains.kotlin:kotlin-gradle-plugin:${libs.versions.kotlin.get()}")
}

gradlePlugin {
    plugins {
        register("library") {
            id = "streamhub.android.library"
            implementationClass = "streamhub.AndroidLibraryConventionPlugin"
        }
        register("compose") {
            id = "streamhub.android.compose"
            implementationClass = "streamhub.AndroidComposeConventionPlugin"
        }
        register("feature") {
            id = "streamhub.android.feature"
            implementationClass = "streamhub.AndroidFeatureConventionPlugin"
        }
    }
}
