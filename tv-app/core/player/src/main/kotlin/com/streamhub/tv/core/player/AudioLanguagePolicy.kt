package com.streamhub.tv.core.player

import java.util.Locale

/** Only actual track tags count; provider titles and labels never establish spoken language. */
internal fun normalizedAudioLanguage(value: String?): String? {
    val raw=value?.trim()?.lowercase(Locale.ROOT)?.replace('_','-') ?: return null
    if(!raw.matches(Regex("[a-z]{2,3}(-([a-z]{2}|[a-z]{4}|[0-9]{3})){0,2}"))) return null
    val tag=raw.substringBefore('-')
    if(tag in listOf("","und","zxx") || !tag.matches(Regex("[a-z]{2,3}"))) return null
    return when(tag) {
        "en","eng"->"eng"; "fr","fra","fre"->"fra"; "de","deu","ger"->"deu"
        "es","spa"->"spa"; "it","ita"->"ita"; "nl","nld","dut"->"nld"; "pt","por"->"por"
        else->runCatching {Locale.forLanguageTag(tag).isO3Language}.getOrNull()?.takeIf {it.isNotBlank()}
    }
}
internal fun preferredAudioSelected(preferred:List<String>,selected:List<String?>):Boolean {
    val wanted=preferred.mapNotNull(::normalizedAudioLanguage).toSet()
    return wanted.isNotEmpty() && selected.isNotEmpty() && selected.all {normalizedAudioLanguage(it) in wanted}
}
/** A source-level exception permits resolving that source, not future unidentified audio tracks. */
internal fun needsRuntimeAudioCheck(required:Boolean,preferred:List<String>):Boolean = required || preferred.isNotEmpty()
internal enum class RuntimeAudioDecision { Allowed, Unobservable, Blocked }
internal fun runtimeAudioDecision(required:Boolean,preferred:List<String>,exposedTracks:Int,selected:List<String?>,explicitSelected:Boolean):RuntimeAudioDecision = when {
    !needsRuntimeAudioCheck(required,preferred) -> RuntimeAudioDecision.Allowed // Explicit All languages.
    exposedTracks==0 -> RuntimeAudioDecision.Unobservable // Preserve successful source preflight, without claiming track verification.
    explicitSelected -> RuntimeAudioDecision.Allowed
    preferredAudioSelected(preferred,selected) -> RuntimeAudioDecision.Allowed
    else -> RuntimeAudioDecision.Blocked
}
/** Approval is an exact current media epoch + immutable TrackGroup object, never a reusable index. */
internal class RuntimeAudioApproval<T:Any> {
    private var value:Triple<Long,T,Int>?=null
    fun approve(epoch:Long,group:T,index:Int) {value=Triple(epoch,group,index)}
    fun accepts(epoch:Long,group:T,index:Int):Boolean=value?.let {it.first==epoch && it.second===group && it.third==index}==true
    fun clear(){value=null}
}
