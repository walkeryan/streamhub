pluginManagement {
    includeBuild("build-logic")
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "streamhub-tv"

include(":app")
include(":core:ui", ":core:data", ":core:player")
// Browsing is the web app, shown by the shell (app); natively there is only pairing and playback.
include(":feature:onboarding", ":feature:player")
