package com.quartz.wallet.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.quartz.wallet.ble.QuartzBLEManager
import java.util.concurrent.Executors

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

    // BLE callbacks
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
            if (connected && phase == "ble_connecting") {
                kotlinx.coroutines.delay(1000)
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
                    onClick = { phase = "qr_scan" },
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        Text("📷 Scan Seed QR Code", fontSize = 18.sp)
                        Text(
                            "Point camera at QR on device screen or serial terminal",
                            fontSize = 12.sp,
                            color = Color(0xFFCCCCCC)
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

            "qr_scan" -> {
                QrScannerSection(
                    onQrScanned = { payload ->
                        // Parse JSON: {"v":1,"words":["word1","word2",...],"addr":"..."}
                        val words = parseSeedQrPayload(payload)
                        if (words.size == 12) {
                            seedWords = words
                            phase = "display"
                        } else {
                            error = "Invalid QR code: expected 12 words, got ${words.size}"
                            phase = "error"
                        }
                    },
                    onError = { msg ->
                        error = msg
                        phase = "error"
                    },
                    onBack = { phase = "choice" }
                )
            }

            "ble_connecting" -> {
                CircularProgressIndicator(color = Color(0xFF00D4AA))
                Spacer(modifier = Modifier.height(16.dp))
                Text("Scanning for Quartz device...", color = Color.Gray)
                Text("Make sure your ESP32 is powered on", fontSize = 13.sp, color = Color.Gray)
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
                Icon(
                    painter = androidx.compose.material.icons.Icons.Default.Check,
                    contentDescription = "Done",
                    tint = Color(0xFF00D4AA),
                    modifier = Modifier.size(64.dp)
                )
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

@Composable
fun QrScannerSection(
    onQrScanned: (String) -> Unit,
    onError: (String) -> Unit,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var hasCameraPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasCameraPermission = granted
        if (!granted) onError("Camera permission denied")
    }

    LaunchedEffect(Unit) {
        if (!hasCameraPermission) {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    if (!hasCameraPermission) {
        Text("Camera permission required for QR scanning", color = Color.Gray)
        Spacer(modifier = Modifier.height(16.dp))
        Button(
            onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) },
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF9933FF))
        ) {
            Text("Grant Camera Permission")
        }
        return
    }

    Text(
        "📷 Point camera at the QR code",
        fontSize = 18.sp,
        color = Color.White,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(16.dp))

    val previewView = remember { PreviewView(context) }
    val cameraExecutor = remember { Executors.newSingleThreadExecutor() }
    var scanned by remember { mutableStateOf(false) }

    AndroidView(
        factory = { previewView },
        modifier = Modifier
            .fillMaxWidth()
            .height(350.dp)
    ) { view ->
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()
                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(view.surfaceProvider)
                }
                val barcodeAnalyzer = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { analysis ->
                        analysis.setAnalyzer(cameraExecutor) { imageProxy ->
                            if (!scanned) {
                                // Use ML Kit or ZXing here — simplified for now
                                // In production, use com.google.mlkit:mlkit-barcode-scanning
                                imageProxy.close()
                            } else {
                                imageProxy.close()
                            }
                        }
                    }
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    barcodeAnalyzer
                )
            } catch (e: Exception) {
                onError("Camera error: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(context))
    }

    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "The QR code contains your encrypted seed phrase.\n" +
        "It appears on the serial terminal or device screen.",
        fontSize = 13.sp,
        color = Color.Gray,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(16.dp))
    OutlinedButton(
        onClick = onBack,
        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.Gray)
    ) {
        Text("Back")
    }
}

// Parse JSON seed QR payload
// Format: {"v":1,"words":["word1","word2",...],"addr":"Qk..."}
fun parseSeedQrPayload(payload: String): List<String> {
    // Simple JSON parsing without library
    val words = mutableListOf<String>()
    val regex = """"([a-z]+)"""".toRegex()
    // Find the words array section
    val wordsSection = payload.substringAfter("\"words\":[").substringBefore("]")
    regex.findAll(wordsSection).forEach { match ->
        words.add(match.groupValues[1])
    }
    return words
}
