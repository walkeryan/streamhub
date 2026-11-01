package com.streamhub.tv.core.data.repo

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.localPrefs: DataStore<Preferences> by androidx.datastore.preferences.preferencesDataStore("local_prefs")


/**
 * Device-local, per-profile UI preferences that the contract has no endpoint for:
 * home row order / hidden rows, recent searches, reduced motion, last channel.
 */
@Singleton
class LocalPrefs @Inject constructor(@ApplicationContext private val context: Context) {

    private fun key(profileId: String, name: String) = stringPreferencesKey("$profileId.$name")

    fun rowOrder(profileId: String): Flow<List<String>> =
        context.localPrefs.data.map { it[key(profileId, "row_order")]?.split('|')?.filter(String::isNotBlank).orEmpty() }

    fun hiddenRows(profileId: String): Flow<Set<String>> =
        context.localPrefs.data.map { it[key(profileId, "hidden_rows")]?.split('|')?.filter(String::isNotBlank)?.toSet().orEmpty() }

    suspend fun setRowOrder(profileId: String, ids: List<String>) {
        context.localPrefs.edit { it[key(profileId, "row_order")] = ids.joinToString("|") }
    }

    suspend fun setHiddenRows(profileId: String, ids: Set<String>) {
        context.localPrefs.edit { it[key(profileId, "hidden_rows")] = ids.joinToString("|") }
    }

    fun recentSearches(serverUrl: String, profileId: String): Flow<List<String>> {
        val scoped=recentSearchStorageKey(serverUrl,profileId) ?: return flowOf(emptyList())
        val key=stringPreferencesKey(scoped)
        return context.localPrefs.data.map { it[key]?.split('\u001F')?.filter(String::isNotBlank).orEmpty() }
    }

    suspend fun pushRecentSearch(serverUrl: String, profileId: String, q: String) {
        val scoped=recentSearchStorageKey(serverUrl,profileId) ?: return
        val key=stringPreferencesKey(scoped)
        context.localPrefs.edit {
            val cur=it[key]?.split('\u001F')?.filter(String::isNotBlank).orEmpty()
            it[key]=(listOf(q)+cur.filter{s->!s.equals(q,true)}).take(10).joinToString("\u001F")
        }
    }

    val reducedMotion: Flow<Boolean> = context.localPrefs.data.map { it[REDUCED_MOTION] ?: false }

    suspend fun setReducedMotion(on: Boolean) {
        context.localPrefs.edit { it[REDUCED_MOTION] = on }
    }

    /** Disclaimer acceptance cached locally so the gate shows before pairing (first run). */
    val disclaimerAcceptedVersion: Flow<String?> = context.localPrefs.data.map { it[DISCLAIMER] }

    suspend fun setDisclaimerAccepted(version: String) {
        context.localPrefs.edit { it[DISCLAIMER] = version }
    }

    private companion object {
        val REDUCED_MOTION = booleanPreferencesKey("reduced_motion")
        val DISCLAIMER = stringPreferencesKey("disclaimer_version")
    }
}
