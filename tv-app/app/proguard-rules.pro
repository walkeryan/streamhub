# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keep,includedescriptorclasses class com.streamhub.tv.**$$serializer { *; }
-keepclassmembers class com.streamhub.tv.** { *** Companion; }
-keepclasseswithmembers class com.streamhub.tv.** { kotlinx.serialization.KSerializer serializer(...); }
# Ktor CIO
-dontwarn io.ktor.**
-dontwarn org.slf4j.**
-dontwarn java.lang.management.**
# Media3 FFmpeg extension (optional, loaded reflectively when present)
-keep class androidx.media3.decoder.ffmpeg.FfmpegAudioRenderer { <init>(...); }
