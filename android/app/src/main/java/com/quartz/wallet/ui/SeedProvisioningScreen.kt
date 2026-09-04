package com.quartz.wallet.ui

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
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
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanIntentResult
import com.journeyapps.barcodescanner.ScanOptions
import com.quartz.wallet.ble.QuartzBLEManager

@Composable
fun SeedProvisioningScreen(
    bleManager: QuartzBLEManager,
    onDone: () -> Unit
) {
    var phase by remember { mutableStateOf("choice") }
    var seedWords by remember { mutableStateOf<List<String>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmStep by remember { mutableIntStateOf(0) }
    var userInput by remember { mutableStateOf("") }

    val context = androidx.compose.ui.platform.LocalContext.current

    // ZXing barcode scanner launcher (declared first so permission callback can reference it)
    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { result: ScanIntentResult ->
        if (result.contents == null) {
            error = "Scan cancelled"
            phase = "error"
        } else {
            val words = parseSeedQrPayload(result.contents)
            if (words.size == 12) {
                seedWords = words
                phase = "display"
            } else {
                error = "Invalid QR: expected 12 words, got ${words.size}"
                phase = "error"
            }
        }
    }

    // Camera permission launcher — launches scanner after permission granted
    val cameraPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            val options = ScanOptions().apply {
                setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                setPrompt("Point camera at the QR code on your device screen")
                setBeepEnabled(true)
                setOrientationLocked(false)
            }
            scanLauncher.launch(options)
        } else {
            error = "Camera permission required to scan QR code"
            phase = "error"
        }
    }

    // BLE callbacks
    LaunchedEffect(Unit) {
        bleManager.onScanEnded = { }
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
            if (connected && phase == "ble_connecting") {
                bleManager.readSeedPhrase()
            }
        }
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
            "choice" -> {
                Text(
                    "How do you want to get your seed phrase?",
                    fontSize = 18.sp,
                    color = Color.White,
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(24.dp))

                // Option 1: Scan QR from device screen or serial terminal
                Button(
                    onClick = {
                        // Check camera permission first, request if needed
                        val hasCamera = ContextCompat.checkSelfPermission(
                            context, Manifest.permission.CAMERA
                        ) == PackageManager.PERMISSION_GRANTED
                        if (hasCamera) {
                            val options = ScanOptions().apply {
                                setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                                setPrompt("Point camera at the QR code on your device screen")
                                setBeepEnabled(true)
                                setOrientationLocked(false)
                                setTimeout(30000L)
                            }
                            scanLauncher.launch(options)
                        } else {
                            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                        }
                    },
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Text("📷 Scan Seed QR Code", fontSize = 18.sp)
                        Text(
                            "Point camera at QR on device screen",
                            fontSize = 12.sp,
                            color = Color(0xFFCCCCCC)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(12.dp))

                // Option 1b: Manual entry — type/paste seed from phone camera scan
                OutlinedButton(
                    onClick = { phase = "manual_entry" },
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFF00D4AA))
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Text("⌨️ Type Seed Manually", fontSize = 16.sp)
                        Text(
                            "Scan with phone camera, then paste/type the 12 words",
                            fontSize = 12.sp,
                            color = Color(0xFF888888)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(8.dp))

                // Option 2: BLE direct (requires bonded device)
                OutlinedButton(
                    onClick = {
                        phase = "ble_connecting"
                        bleManager.startScan()
                    },
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Color(0xFF00D4AA))
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Text("📱 Read via Bluetooth", fontSize = 18.sp)
                        Text(
                            "Pair with device first, then read seed",
                            fontSize = 12.sp,
                            color = Color(0xFF888888)
                        )
                    }
                }
            }

            "ble_connecting" -> {
                CircularProgressIndicator(color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(16.dp))
                Text("Scanning — the miner may show as ESP32", color = Color.Gray)
                Text("Tap your device to connect:", fontSize = 13.sp, color = Color.Gray)
                Spacer(modifier = Modifier.height(8.dp))
                bleManager.discovered.forEach { d ->
                    OutlinedButton(
                        onClick = { bleManager.connectByAddress(d.address) },
                        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)
                    ) {
                        Column {
                            Text(if (d.isQuartz) "🔮 ${d.name ?: "Quartz"}" else (d.name ?: "(unnamed)"))
                            Text(d.address, fontSize = 11.sp, color = Color(0xFF888888))
                        }
                    }
                }
                if (bleManager.discovered.isEmpty()) {
                    Text("No devices yet — make sure the board is powered on", fontSize = 12.sp, color = Color.Gray)
                }
                Text("Conn: ${bleManager.connectionState.value}", fontSize = 11.sp, color = Color.Gray)
                Spacer(modifier = Modifier.height(16.dp))
                Text(
                    "⚠️ BLE requires pairing. Accept the pairing dialog on both devices.",
                    fontSize = 13.sp,
                    color = Color(0xFFFF6B35),
                    textAlign = TextAlign.Center
                )
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
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Color(0xFF9933FF),
                            unfocusedBorderColor = Color(0xFF444466),
                            focusedTextColor = Color.White,
                            unfocusedTextColor = Color.White
                        )
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Button(
                        onClick = {
                            if (userInput.trim().equals(seedWords[randomIdx], ignoreCase = true)) {
                                userInput = ""
                                confirmStep++
                                if (confirmStep >= 3) {
                                    // Try BLE confirm if connected, otherwise just save
                                    if (bleManager.isConnected()) {
                                        bleManager.confirmSeedPhrase()
                                        phase = "confirming"
                                    } else {
                                        // QR path — no BLE, just save locally
                                        phase = "done"
                                    }
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
                Text("✅", fontSize = 48.sp)
                Spacer(modifier = Modifier.height(16.dp))
                Text("✅ Wallet Setup Complete!", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    if (bleManager.isConnected()) "Seed phrase confirmed and wiped from device."
                    else "Seed phrase saved. Keep your paper backup safe!",
                    fontSize = 14.sp,
                    color = Color.Gray,
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(24.dp))
                Button(
                    onClick = onDone,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("Continue to Dashboard")
                }
            }

            "manual_entry" -> {
                Text(
                    "Enter your 12-word seed phrase",
                    fontSize = 18.sp,
                    color = Color.White,
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "Tip: Scan the QR with your phone's camera app, copy the text, and paste it here.",
                    fontSize = 13.sp,
                    color = Color(0xFF888888),
                    textAlign = TextAlign.Center
                )
                Spacer(modifier = Modifier.height(16.dp))
                OutlinedTextField(
                    value = userInput,
                    onValueChange = { userInput = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("quartz-seed:word1 word2 ... or just the 12 words") },
                    minLines = 3,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = Color(0xFF9933FF),
                        unfocusedBorderColor = Color(0xFF444466),
                        focusedTextColor = Color.White,
                        unfocusedTextColor = Color.White
                    )
                )
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = {
                        val words = parseSeedQrPayload(userInput)
                        if (words.size == 12) {
                            seedWords = words
                            error = null
                            phase = "display"
                        } else {
                            error = "Expected 12 words, got ${words.size}. Check your input."
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("Import Seed")
                }
                error?.let {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(it, color = Color.Red, fontSize = 14.sp)
                }
                Spacer(modifier = Modifier.height(8.dp))
                TextButton(onClick = { phase = "choice" }) {
                    Text("Back", color = Color.Gray)
                }
            }

            "error" -> {
                Text("❌ $error", color = Color.Red, textAlign = TextAlign.Center)
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = {
                        error = null
                        phase = "choice"
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Text("Back")
                }
            }
        }
    }
}

// Parse seed QR payload
// New format: quartz-seed:word1 word2 ... word12
// Legacy JSON format: {"v":1,"words":["word1","word2",...],"addr":"Qk..."}
fun parseSeedQrPayload(payload: String): List<String> {
    val trimmed = payload.trim()
    
    // New compact format: quartz-seed:word1 word2 ...
    if (trimmed.startsWith("quartz-seed:")) {
        return trimmed.removePrefix("quartz-seed:").trim().split(" ").filter { it.isNotEmpty() }
    }
    
    // Legacy JSON format
    if (trimmed.startsWith("{")) {
        val words = mutableListOf<String>()
        val regex = """"([a-z]+)"""".toRegex()
        val wordsSection = trimmed.substringAfter("\"words\":[").substringBefore("]")
        regex.findAll(wordsSection).forEach { match ->
            words.add(match.groupValues[1])
        }
        return words
    }
    
    // Fallback: try space-separated words (raw BIP-39)
    val parts = trimmed.split(" ").filter { it.matches(Regex("[a-z]+")) }
    return if (parts.size == 12) parts else emptyList()
}
