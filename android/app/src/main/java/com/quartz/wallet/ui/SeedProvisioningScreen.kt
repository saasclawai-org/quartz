package com.quartz.wallet.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.quartz.wallet.ble.QuartzBLEManager

@Composable
fun SeedProvisioningScreen(
    bleManager: QuartzBLEManager,
    onDone: () -> Unit
) {
    var phase by remember { mutableStateOf("connecting") }
    var seedWords by remember { mutableStateOf<List<String>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmStep by remember { mutableIntStateOf(0) }
    var userInput by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        bleManager.onError = { msg ->
            error = msg
            phase = "error"
        }
        bleManager.onSeedRead = { words ->
            seedWords = words
            phase = "display"
        }
        bleManager.onSeedConfirmed = {
            phase = "done"
        }
        bleManager.onConnectionChange = { connected ->
            if (connected && phase == "connecting") {
                // Wait a moment for service discovery, then read seed
                kotlinx.coroutines.delay(1000)
                bleManager.readSeedPhrase()
            }
        }

        // Start scanning
        bleManager.startScan()
        phase = "scanning"
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp)
            .verticalScroll(rememberScrollState()),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "🔮 Quartz Wallet Setup",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = Color(0xFF9933FF),
            textAlign = TextAlign.Center
        )
        Spacer(modifier = Modifier.height(24.dp))

        when (phase) {
            "scanning" -> {
                CircularProgressIndicator(color = Color(0xFF9933FF))
                Spacer(modifier = Modifier.height(16.dp))
                Text("Scanning for Quartz device...", color = Color.Gray)
                Text("Make sure your ESP32 is powered on", fontSize = 13.sp, color = Color.Gray)
            }

            "connecting" -> {
                CircularProgressIndicator(color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(16.dp))
                Text("Connecting to device...", color = Color.Gray)
            }

            "display" -> {
                Text(
                    "⚠️ Write down all 12 words",
                    color = Color(0xFFFF6B35),
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "This is shown ONCE. Lose this and you lose your funds.",
                    fontSize = 13.sp,
                    color = Color.Gray,
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(20.dp))

                seedWords.forEachIndexed { i, word ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        colors = CardDefaults.cardColors(containerColor = Color(0xFF2A2A4E))
                    ) {
                        Row(
                            modifier = Modifier.padding(16.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                "${i + 1}.",
                                color = Color(0xFF9933FF),
                                fontWeight = FontWeight.Bold,
                                fontSize = 18.sp,
                                modifier = Modifier.width(40.dp)
                            )
                            Text(
                                word,
                                color = Color.White,
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(24.dp))
                Button(
                    onClick = { phase = "confirm" },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("I've written it down", fontSize = 18.sp, modifier = Modifier.padding(4.dp))
                }
            }

            "confirm" -> {
                Text(
                    "Confirm your backup",
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.White
                )
                Spacer(modifier = Modifier.height(8.dp))
                if (confirmStep < 3 && seedWords.isNotEmpty()) {
                    val randomIdx = remember { (0..11).shuffled().take(3)[confirmStep] }
                    Text("Type word #${randomIdx + 1}:")
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedTextField(
                        value = userInput,
                        onValueChange = { userInput = it },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        colors = TextFieldDefaults.outlinedTextFieldColors(
                            focusedBorderColor = Color(0xFF9933FF),
                            textColor = Color.White
                        )
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Button(
                        onClick = {
                            if (userInput.trim().equals(seedWords[randomIdx], ignoreCase = true)) {
                                userInput = ""
                                confirmStep++
                                if (confirmStep >= 3) {
                                    bleManager.confirmSeedPhrase()
                                    phase = "confirming"
                                }
                            } else {
                                error = "Wrong word. Try again."
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                    ) {
                        Text("Confirm")
                    }
                    error?.let {
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(it, color = Color.Red, fontSize = 14.sp)
                        error = null
                    }
                }
            }

            "confirming" -> {
                CircularProgressIndicator(color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(16.dp))
                Text("Confirming with device...", color = Color.Gray)
            }

            "done" -> {
                Icon(
                    painter = androidx.compose.material.icons.Icons.Default.Check,
                    contentDescription = "Done",
                    tint = Color(0xFF00D4AA),
                    modifier = Modifier.size(64.dp)
                )
                Spacer(modifier = Modifier.height(16.dp))
                Text("✅ Wallet Setup Complete!", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(8.dp))
                Text("Your seed phrase has been wiped from the device.", fontSize = 14.sp, color = Color.Gray, textAlign = TextAlign.Center)
                Spacer(modifier = Modifier.height(24.dp))
                Button(
                    onClick = onDone,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("Continue to Dashboard")
                }
            }

            "error" -> {
                Text("❌ $error", color = Color.Red, textAlign = TextAlign.Center)
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = {
                        error = null
                        phase = "scanning"
                        bleManager.startScan()
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("Retry")
                }
            }
        }
    }
}
