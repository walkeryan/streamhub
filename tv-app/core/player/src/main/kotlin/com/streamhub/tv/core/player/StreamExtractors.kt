package com.streamhub.tv.core.player

import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.ts.DefaultTsPayloadReaderFactory
import androidx.media3.extractor.ts.TsExtractor

/**
 * How the player reads containers, live MPEG-TS above all.
 *
 * Many IPTV streams start mid-GOP, so an I-frame that is not an IDR still counts as a place to start
 * (FLAG_ALLOW_NON_IDR_KEYFRAMES; the cost is a moment of blockiness at the start).
 *
 * FLAG_DETECT_ACCESS_UNITS is deliberately off. On streams that carry access unit delimiters, which
 * every channel checked does, it cuts each frame into separate samples (delimiter, SEI, captions,
 * picture). The TV's hardware decoder shows the first picture and then stalls on the fragments while
 * audio plays on: a frozen first frame. ExtractorsTest pins this.
 */
@OptIn(UnstableApi::class)
internal fun streamExtractors(): DefaultExtractorsFactory = DefaultExtractorsFactory()
    .setTsExtractorFlags(DefaultTsPayloadReaderFactory.FLAG_ALLOW_NON_IDR_KEYFRAMES)
    .setTsExtractorMode(TsExtractor.MODE_SINGLE_PMT)
