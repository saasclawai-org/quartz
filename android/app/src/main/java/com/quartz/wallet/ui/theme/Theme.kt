package com.quartz.wallet.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

val QuartzBg = Color(0xFF0A0A0F)
val QuartzSurface = Color(0xFF12121A)
val QuartzCard = Color(0xFF1A1A2E)
val QuartzBorder = Color(0xFF2A2A3E)
val QuartzText = Color(0xFFE4E4EF)
val QuartzMuted = Color(0xFF8888A0)
val QuartzAccent = Color(0xFF00D4AA)
val QuartzPurple = Color(0xFF9D4EDD)
val QuartzOrange = Color(0xFFFF6B35)

private val QuartzColorScheme = darkColorScheme(
    primary = QuartzAccent,
    onPrimary = QuartzBg,
    secondary = QuartzPurple,
    onSecondary = QuartzText,
    tertiary = QuartzOrange,
    background = QuartzBg,
    onBackground = QuartzText,
    surface = QuartzSurface,
    onSurface = QuartzText,
    surfaceVariant = QuartzCard,
    onSurfaceVariant = QuartzMuted,
    outline = QuartzBorder,
)

@Composable
fun QuartzTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = QuartzColorScheme,
        content = content
    )
}
