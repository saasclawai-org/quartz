package com.quartz.wallet.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Restore
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.quartz.wallet.ble.QuartzBLEManager
import com.quartz.wallet.ui.theme.*
import com.quartz.wallet.util.Validation
import kotlinx.coroutines.delay

@Composable
fun RecoveryScreen(
    bleManager: QuartzBLEManager,
    onRecovered: (address: String) -> Unit
) {
    // 12 word fields
    val words = remember { mutableStateListOf(*Array(12) { "" }) }
    var isRecovering by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var recoveredAddress by remember { mutableStateOf<String?>(null) }
    var balance by remember { mutableStateOf<String?>(null) }
    var signatureCount by remember { mutableStateOf<Int?>(null) }
    var success by remember { mutableStateOf(false) }

    val allFilled = Validation.isRecoveryFormValid(words.toList())

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(QuartzBg)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Header
        Icon(
            imageVector = Icons.Default.Restore,
            contentDescription = "Recover",
            tint = QuartzAccent,
            modifier = Modifier.size(40.dp)
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text = "Recover Wallet",
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            color = QuartzText
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "Enter your 12-word seed phrase to restore your wallet",
            fontSize = 14.sp,
            color = QuartzMuted,
            textAlign = TextAlign.Center
        )

        Spacer(Modifier.height(24.dp))

        if (success && recoveredAddress != null) {
            // ── Success State ───────────────────────────────────────
            RecoverySuccessCard(
                address = recoveredAddress!!,
                balance = balance,
                signatureCount = signatureCount,
                onResumeMining = { onRecovered(recoveredAddress!!) }
            )
        } else {
            // ── Seed Entry State ────────────────────────────────────

            // 12 word fields in a 3×4 grid (4 rows × 3 cols)
            for (row in 0 until 4) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    for (col in 0 until 3) {
                        val index = row * 3 + col
                        WordField(
                            index = index,
                            value = words[index],
                            onValueChange = { newValue ->
                                words[index] = newValue.uppercase().trim()
                                error = null
                            },
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
            }

            // Error message
            Spacer(Modifier.height(8.dp))
            AnimatedVisibility(visible = error != null) {
                Text(
                    text = error ?: "",
                    color = QuartzOrange,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth()
                )
            }

            Spacer(Modifier.height(20.dp))

            // Recover button
            Button(
                onClick = {
                    if (!allFilled) return@Button
                    isRecovering = true
                    error = null

                    val wordList = words.toList()
                    bleManager.recoverFromSeed(wordList) { ok, address, err ->
                        isRecovering = false
                        if (ok && address != null) {
                            recoveredAddress = address
                            success = true
                            // Simulate fetching balance/signature count
                            // In production, this would come from the node API
                            balance = "0.00"
                            signatureCount = 0
                        } else {
                            error = err ?: "Recovery failed"
                        }
                    }
                },
                enabled = allFilled && !isRecovering,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = QuartzAccent,
                    disabledContainerColor = QuartzBorder
                ),
                shape = RoundedCornerShape(12.dp)
            ) {
                if (isRecovering) {
                    CircularProgressIndicator(
                        color = QuartzBg,
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 2.dp
                    )
                    Spacer(Modifier.width(12.dp))
                    Text(
                        "Syncing with node...",
                        color = QuartzBg,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp
                    )
                } else {
                    Text(
                        "Recover Wallet",
                        color = if (allFilled) QuartzBg else QuartzMuted,
                        fontWeight = FontWeight.Bold,
                        fontSize = 16.sp
                    )
                }
            }

            // Progress section while recovering
            if (isRecovering) {
                Spacer(Modifier.height(24.dp))
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    LinearProgressIndicator(
                        color = QuartzAccent,
                        trackColor = QuartzBorder,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "Deriving keys and syncing with the Quartz network...",
                        fontSize = 13.sp,
                        color = QuartzMuted,
                        textAlign = TextAlign.Center
                    )
                }
            }

            Spacer(Modifier.height(24.dp))

            // Helper text
            Text(
                "⚠️ Make sure you are in a private location. Anyone who sees your seed phrase can steal your funds.",
                fontSize = 12.sp,
                color = QuartzOrange,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 16.dp)
            )
        }
    }
}

@Composable
private fun WordField(
    index: Int,
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text("${index + 1}", fontSize = 11.sp) },
        singleLine = true,
        enabled = true,
        textStyle = LocalTextStyle.current.copy(
            fontSize = 14.sp,
            fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.Monospace
        ),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = QuartzText,
            unfocusedTextColor = QuartzText,
            focusedBorderColor = QuartzAccent,
            unfocusedBorderColor = QuartzBorder,
            focusedLabelColor = QuartzAccent,
            unfocusedLabelColor = QuartzMuted,
            cursorColor = QuartzAccent,
            focusedContainerColor = QuartzSurface,
            unfocusedContainerColor = QuartzSurface
        ),
        shape = RoundedCornerShape(8.dp),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
            capitalization = KeyboardCapitalization.Characters,
            autoCorrect = false,
            imeAction = if (index == 11) ImeAction.Done else ImeAction.Next
        ),
        modifier = modifier
    )
}

@Composable
private fun RecoverySuccessCard(
    address: String,
    balance: String?,
    signatureCount: Int?,
    onResumeMining: () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Success checkmark
        Box(
            modifier = Modifier
                .size(64.dp)
                .clip(RoundedCornerShape(32.dp))
                .background(QuartzAccent.copy(alpha = 0.15f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Check,
                contentDescription = "Success",
                tint = QuartzAccent,
                modifier = Modifier.size(36.dp)
            )
        }

        Spacer(Modifier.height(16.dp))
        Text(
            "Wallet Recovered!",
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            color = QuartzAccent
        )
        Spacer(Modifier.height(24.dp))

        // Address card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = QuartzCard),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text("Wallet Address", color = QuartzMuted, fontSize = 13.sp)
                Spacer(Modifier.height(4.dp))
                Text(
                    text = address,
                    color = QuartzText,
                    fontSize = 14.sp,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium
                )

                Spacer(Modifier.height(16.dp))

                // Balance
                Text("Balance", color = QuartzMuted, fontSize = 13.sp)
                Spacer(Modifier.height(4.dp))
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(
                        text = balance ?: "—",
                        fontSize = 28.sp,
                        fontWeight = FontWeight.ExtraBold,
                        color = QuartzText
                    )
                    if (balance != null) {
                        Text(
                            " QZ",
                            color = QuartzAccent,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.padding(start = 4.dp, bottom = 2.dp)
                        )
                    }
                }

                Spacer(Modifier.height(12.dp))

                // Signature count
                Text("Signatures", color = QuartzMuted, fontSize = 13.sp)
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "${signatureCount ?: 0}",
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Medium,
                    color = QuartzText
                )
            }
        }

        Spacer(Modifier.height(24.dp))

        // Resume Mining button
        Button(
            onClick = onResumeMining,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text(
                "⛏️ Resume Mining",
                color = QuartzBg,
                fontWeight = FontWeight.Bold,
                fontSize = 16.sp
            )
        }
    }
}