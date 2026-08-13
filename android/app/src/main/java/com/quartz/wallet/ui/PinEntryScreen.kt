package com.quartz.wallet.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Backspace
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.quartz.wallet.ble.QuartzBLEManager
import com.quartz.wallet.ui.theme.*
import com.quartz.wallet.util.Validation
import kotlinx.coroutines.delay

// Maximum PIN attempts before device wipe
private const val MAX_ATTEMPTS = 10
private const val MIN_PIN_LENGTH = 4
private const val MAX_PIN_LENGTH = 8

@Composable
fun PinEntryScreen(
    bleManager: QuartzBLEManager,
    onUnlocked: () -> Unit,
    onRecoveryNeeded: () -> Unit
) {
    var pin by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var attemptsLeft by remember { mutableStateOf(MAX_ATTEMPTS) }
    var wiped by remember { mutableStateOf(false) }
    var isSubmitting by remember { mutableStateOf(false) }

    // Shake animation trigger
    var shakeTrigger by remember { mutableIntStateOf(0) }
    val shakeOffset by animateFloatAsState(
        targetValue = if (shakeTrigger > 0) 0f else 0f,
        animationSpec = spring(
            dampingRatio = Spring.DampingRatioMediumBouncy,
            stiffness = Spring.StiffnessHigh
        ),
        label = "shake"
    )

    // Shake animation using infinite transition that we stop quickly
    val shakeAnim = remember(shakeTrigger) {
        Animatable(0f)
    }
    LaunchedEffect(shakeTrigger) {
        if (shakeTrigger > 0) {
            shakeAnim.snapTo(0f)
            shakeAnim.animateTo(
                targetValue = 0f,
                animationSpec = keyframes {
                    durationMillis = 400
                    for (i in 0..5) {
                        val sign = if (i % 2 == 0) 1f else -1f
                        sign * 12f * (1f - i / 6f) at (i * 70)
                    }
                }
            )
        }
    }

    // Auto-dismiss on success
    var unlocked by remember { mutableStateOf(false) }
    LaunchedEffect(unlocked) {
        if (unlocked) {
            delay(300)
            onUnlocked()
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(QuartzBg)
            .padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Lock icon
        Icon(
            imageVector = Icons.Default.Lock,
            contentDescription = "Locked",
            tint = QuartzAccent,
            modifier = Modifier.size(40.dp)
        )
        Spacer(Modifier.height(12.dp))

        // Title
        Text(
            text = if (wiped) "Device Wiped" else "Enter PIN",
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            color = QuartzText
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = if (wiped) "10 failed attempts — seed recovery required"
            else "Unlock your Quartz device",
            fontSize = 14.sp,
            color = QuartzMuted
        )

        Spacer(Modifier.height(32.dp))

        // Wiped state — show recovery message
        if (wiped) {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    "🔐",
                    fontSize = 48.sp
                )
                Spacer(Modifier.height(16.dp))
                Text(
                    "Device wiped — recover from seed",
                    fontSize = 16.sp,
                    color = QuartzOrange,
                    textAlign = TextAlign.Center
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "All keys have been erased from the device.\nUse your 12-word backup to restore your wallet.",
                    fontSize = 13.sp,
                    color = QuartzMuted,
                    textAlign = TextAlign.Center
                )
                Spacer(Modifier.height(32.dp))
                Button(
                    onClick = onRecoveryNeeded,
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Text("Go to Recovery", color = QuartzBg, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                }
            }
            return@Column
        }

        // PIN dot display with shake animation
        Row(
            modifier = Modifier.graphicsLayer { translationX = shakeAnim.value },
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            val displayCount = maxOf(pin.length, MIN_PIN_LENGTH)
            for (i in 0 until displayCount) {
                val filled = i < pin.length
                Box(
                    modifier = Modifier
                        .size(16.dp)
                        .clip(CircleShape)
                        .background(if (filled) QuartzAccent else QuartzBorder)
                )
            }
        }

        // Attempts remaining
        Spacer(Modifier.height(12.dp))
        AnimatedVisibility(visible = error != null || attemptsLeft < MAX_ATTEMPTS) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                error?.let {
                    Text(
                        text = it,
                        color = QuartzOrange,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium
                    )
                    Spacer(Modifier.height(4.dp))
                }
                if (attemptsLeft < MAX_ATTEMPTS && !isSubmitting) {
                    Text(
                        text = "$attemptsLeft attempt${if (attemptsLeft != 1) "s" else ""} left",
                        color = if (attemptsLeft <= 3) QuartzOrange else QuartzMuted,
                        fontSize = 13.sp
                    )
                }
            }
        }

        Spacer(Modifier.height(24.dp))

        // Number pad
        NumberPad(
            enabled = !isSubmitting && !wiped,
            onDigit = { digit ->
                if (pin.length < MAX_PIN_LENGTH) {
                    pin += digit
                    error = null
                }
            },
            onDelete = {
                if (pin.isNotEmpty()) {
                    pin = pin.dropLast(1)
                    error = null
                }
            },
            onSubmit = {
                val submittedPin = pin
                if (Validation.isPinValid(submittedPin)) {
                    isSubmitting = true
                    error = null
                    bleManager.unlockDevice(submittedPin) { success, remaining, deviceWiped ->
                        isSubmitting = false
                        attemptsLeft = remaining
                        if (deviceWiped) {
                            wiped = true
                            error = "Device wiped due to too many failed attempts"
                        } else if (success) {
                            unlocked = true
                        } else {
                            pin = ""
                            error = "Wrong PIN"
                            shakeTrigger++
                            if (remaining <= 0) {
                                wiped = true
                            }
                        }
                    }
                } else {
                    error = "PIN must be 4–8 digits"
                    shakeTrigger++
                }
            },
            canSubmit = pin.length >= MIN_PIN_LENGTH && !isSubmitting
        )

        // Submitting indicator
        if (isSubmitting) {
            Spacer(Modifier.height(16.dp))
            CircularProgressIndicator(
                color = QuartzAccent,
                modifier = Modifier.size(28.dp),
                strokeWidth = 2.dp
            )
        }
    }
}

@Composable
private fun NumberPad(
    enabled: Boolean,
    onDigit: (String) -> Unit,
    onDelete: () -> Unit,
    onSubmit: () -> Unit,
    canSubmit: Boolean
) {
    val buttons = listOf(
        listOf("1", "2", "3"),
        listOf("4", "5", "6"),
        listOf("7", "8", "9"),
        listOf("", "0", "DEL")
    )

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        buttons.forEach { row ->
            Row(
                horizontalArrangement = Arrangement.spacedBy(20.dp)
            ) {
                row.forEach { key ->
                    when (key) {
                        "" -> {
                            // Empty spacer to maintain grid
                            Box(modifier = Modifier.size(72.dp))
                        }
                        "DEL" -> {
                            KeyButton(
                                text = "",
                                icon = Icons.Default.Backspace,
                                enabled = enabled,
                                onClick = onDelete,
                                modifier = Modifier.size(72.dp)
                            )
                        }
                        else -> {
                            KeyButton(
                                text = key,
                                enabled = enabled,
                                onClick = { onDigit(key) },
                                modifier = Modifier.size(72.dp)
                            )
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))

        // Submit button
        Button(
            onClick = onSubmit,
            enabled = canSubmit,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = QuartzAccent,
                disabledContainerColor = QuartzBorder
            ),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(
                "Unlock",
                color = if (canSubmit) QuartzBg else QuartzMuted,
                fontWeight = FontWeight.Bold,
                fontSize = 16.sp
            )
        }
    }
}

@Composable
private fun KeyButton(
    text: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector? = null,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(16.dp))
            .background(QuartzCard)
            .clickable(enabled = enabled) { onClick() },
        contentAlignment = Alignment.Center
    ) {
        if (icon != null) {
            Icon(
                imageVector = icon,
                contentDescription = "Delete",
                tint = if (enabled) QuartzMuted else QuartzBorder,
                modifier = Modifier.size(24.dp)
            )
        } else {
            Text(
                text = text,
                fontSize = 26.sp,
                fontWeight = FontWeight.Medium,
                color = if (enabled) QuartzText else QuartzMuted
            )
        }
    }
}