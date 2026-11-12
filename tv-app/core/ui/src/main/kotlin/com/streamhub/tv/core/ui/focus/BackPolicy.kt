package com.streamhub.tv.core.ui.focus

/** What the shell does with a Back press that no screen claimed. */
enum class BackAction {
    /** Move focus into the left rail (which expands while focused). */
    OpenRail,
    /** Leave the current top-level screen for Home. */
    GoHome,
    /** First Back on Home: show "Press Back again to exit" and remember when. */
    ConfirmExit,
    /** Second Back on Home inside the confirmation window: leave the app. */
    Exit,
    /** Not a top-level screen: ordinary back-stack pop. */
    Default,
}

/**
 * Back on the top-level screens, as a pure function so the ladder is unit-tested:
 * content → rail → Home → "Press Back again to exit" → exit. Each press moves one rung towards
 * leaving, so Back can neither bounce between rail and content forever nor quit by accident.
 */
object BackPolicy {
    const val HOME_ROUTE = "home"

    /** A second Back on Home exits only within this window; later it asks again. */
    const val EXIT_CONFIRM_MS = 2_000L

    /**
     * @param route current destination route, null before the first destination is known
     * @param lastBackAtMs time of the Back press that last returned [BackAction.ConfirmExit], 0 if none
     * @param nowMs same clock as [lastBackAtMs]
     */
    fun decide(route: String?, isTopLevel: Boolean, railExpanded: Boolean, lastBackAtMs: Long, nowMs: Long): BackAction = when {
        route == null || !isTopLevel -> BackAction.Default
        route == HOME_ROUTE ->
            if (lastBackAtMs > 0 && nowMs - lastBackAtMs in 0..EXIT_CONFIRM_MS) BackAction.Exit else BackAction.ConfirmExit
        railExpanded -> BackAction.GoHome
        else -> BackAction.OpenRail
    }
}
