package com.streamhub.tv.core.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.common.BitMatrix
import com.google.zxing.qrcode.QRCodeWriter
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel

fun encodeQr(text: String): BitMatrix? = runCatching {
    QRCodeWriter().encode(
        text, BarcodeFormat.QR_CODE, 0, 0,
        mapOf(EncodeHintType.MARGIN to 1, EncodeHintType.ERROR_CORRECTION to ErrorCorrectionLevel.M),
    )
}.getOrNull()

/** QR code drawn as vector modules; always black-on-white with quiet zone so phones can scan it on any theme. */
@Composable
fun QrCode(text: String, modifier: Modifier = Modifier, size: Dp = 200.dp) {
    val matrix = remember(text) { encodeQr(text) }
    Canvas(modifier.size(size).background(Color.White).padding(8.dp)) {
        val m = matrix ?: return@Canvas
        val cell = this.size.minDimension / m.width
        for (y in 0 until m.height) for (x in 0 until m.width) {
            if (m.get(x, y)) drawRect(Color.Black, Offset(x * cell, y * cell), Size(cell + 0.5f, cell + 0.5f))
        }
    }
}
