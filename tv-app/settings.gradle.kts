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
include(
    ":feature:onboarding",
    ":feature:home",
    ":feature:search",
    ":feature:livetv",
    ":feature:guide",
    ":feature:details",
    ":feature:player",
    ":feature:favorites",
    ":feature:sources",
    ":feature:settings",
)
