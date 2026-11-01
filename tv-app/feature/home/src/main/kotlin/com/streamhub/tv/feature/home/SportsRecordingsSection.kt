package com.streamhub.tv.feature.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.streamhub.tv.core.data.model.*
import com.streamhub.tv.core.ui.components.*
import com.streamhub.tv.core.ui.theme.LocalThemeTokens

/** Exact library/source identity only. No fuzzy event association or implicit audio consent. */
internal fun sportsRecordingPlayback(item: SportsRecording): Pair<String,String>? =
    (item.titleId to item.sourceId).takeIf {
        item.kind=="library_video" && !item.live && item.sourceId.isNotBlank() &&
            item.titleId.startsWith("lib:${item.sourceId}:") && item.titleId.length>"lib:${item.sourceId}:".length
    }
internal fun sportsRecordingAudio(item: SportsRecording): String =
    if(item.audioVerified && item.audioLanguages.isNotEmpty()) "Verified audio: ${item.audioLanguages.joinToString(", ")}"
    else "Audio unverified" + (item.languageHint?.let { " · Provider hint: $it" } ?: "")
internal fun sportsRecordingDate(item: SportsRecording): String = item.originallyAvailableAt?.let {
    runCatching { java.time.LocalDate.parse(it.take(10)); "Original release: ${it.take(10)}" }.getOrNull()
} ?: "Original release date unavailable"
internal fun sportsRecordingsNext(page: SportsRecordingsPage): Int? =
    (page.offset+page.limit).takeIf { page.limit>0 && it<page.total }

@Composable
internal fun ColumnScope.SportsRecordingsSection(page: SportsRecordingsPage, loading: Boolean, error: String?, onPage: (Int)->Unit, onPlay: (SportsRecording)->Unit) {
    val t=LocalThemeTokens.current
    val list=rememberLazyListState()
    val previous=remember {FocusRequester()}
    var restorePageFocus by remember {mutableStateOf(false)}
    LaunchedEffect(page.offset,loading) {
        if(!loading && restorePageFocus) {list.scrollToItem(0);withFrameNanos{};runCatching {previous.requestFocus()};restorePageFocus=false}
    }
    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
        ShButton("Previous recordings", {if(!loading && page.offset>0){restorePageFocus=true;onPage((page.offset-page.limit).coerceAtLeast(0))}}, enabled=!loading && page.offset>0)
        ShButton(if(loading) "Loading…" else "Refresh recordings", {if(!loading)onPage(page.offset)},Modifier.focusRequester(previous))
        ShButton("More recordings", {if(!loading)sportsRecordingsNext(page)?.let {restorePageFocus=true;onPage(it)}},enabled=!loading && sportsRecordingsNext(page)!=null)
        Text("${if(page.items.isEmpty())0 else page.offset+1}–${page.offset+page.items.size} · ${if(page.truncated) "at least " else ""}${page.total}",color=t.textMuted)
    }
    if(page.truncated) Text("Results capped. Narrow sport, language or search.",color=t.textMuted)
    when {
        error!=null -> CenterMessage("Couldn't load library recordings",error) {ShButton("Retry recordings",{onPage(page.offset)})}
        loading && page.items.isEmpty() -> LoadingView()
        page.items.isEmpty() -> CenterMessage("No matching library recordings","Only actual linked library videos appear here. This does not prove an event has no broadcast.")
        else -> LazyColumn(Modifier.weight(1f),state=list,verticalArrangement=Arrangement.spacedBy(12.dp),contentPadding=PaddingValues(8.dp)) {
            items(page.items,key={"${it.sourceId}:${it.titleId}"}) {item->
                Column(Modifier.fillMaxWidth().background(t.surfaceAlt,RoundedCornerShape(16.dp)).padding(16.dp),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                    Text(item.title,color=t.text,style=MaterialTheme.typography.titleLarge)
                    Text("Library recording · ${item.sport} · ${item.sourceName}",color=t.textMuted)
                    Text(sportsRecordingDate(item),color=t.textMuted)
                    Text(sportsRecordingAudio(item),color=t.textMuted)
                    ShButton("Choose recording audio",{if(!loading)onPlay(item)},enabled=!loading && sportsRecordingPlayback(item)!=null)
                }
            }
        }
    }
}

@Composable
internal fun SportsSearchDialog(initial: String,onApply:(String)->Unit,onDismiss:()->Unit) {
    val t=LocalThemeTokens.current
    var text by remember {mutableStateOf(initial)}
    val field=remember {FocusRequester()}
    Dialog(onDismissRequest=onDismiss) {
        Column(Modifier.width(620.dp).background(t.surface,RoundedCornerShape(20.dp)).padding(24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Text("Search guide and library sports",color=t.text,style=MaterialTheme.typography.titleLarge)
            ShTextField(text,{text=it.take(200)},label="Title or team",modifier=Modifier.focusRequester(field))
            Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                ShButton("Apply",{onApply(text.trim());onDismiss()})
                ShButton("Clear",{onApply("");onDismiss()})
                ShButton("Cancel",onDismiss)
            }
        }
    }
    LaunchedEffect(Unit){runCatching{field.requestFocus()}}
}
