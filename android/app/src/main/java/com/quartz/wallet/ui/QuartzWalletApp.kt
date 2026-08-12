package com.quartz.wallet.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.quartz.wallet.ble.MiningStats
import com.quartz.wallet.ble.QuartzBLEManager
import com.quartz.wallet.ui.theme.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuartzWalletApp() {
    val pagerState = rememberPagerState(pageCount = { 3 })
    val scope = rememberCoroutineScope()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("🔮 ", fontSize = 20.sp)
                        Text("Quartz", fontWeight = FontWeight.Bold, color = QuartzAccent)
                    }
                },
                actions = {
                    Text("Testnet", fontSize = 12.sp, color = QuartzMuted, modifier = Modifier.padding(end = 16.dp))
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = QuartzBg
                )
            )
        },
        bottomBar = {
            NavigationBar(containerColor = QuartzSurface) {
                val tabs = listOf("Wallet" to Icons.Default.AccountBalanceWallet,
                                  "Miner" to Icons.Default.Memory,
                                  "Settings" to Icons.Default.Settings)
                tabs.forEachIndexed { index, (label, icon) ->
                    NavigationBarItem(
                        icon = { Icon(icon, contentDescription = label) },
                        label = { Text(label, fontSize = 11.sp) },
                        selected = pagerState.currentPage == index,
                        onClick = { scope.launch { pagerState.animateScrollToPage(index) } },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = QuartzAccent,
                            selectedTextColor = QuartzAccent,
                            unselectedIconColor = QuartzMuted,
                            unselectedTextColor = QuartzMuted
                        )
                    )
                }
            }
        }
    ) { padding ->
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.padding(padding)
        ) { page ->
            when (page) {
                0 -> WalletScreen()
                1 -> MinerScreen()
                2 -> SettingsScreen()
            }
        }
    }
}

@Composable
fun WalletScreen() {
    var hasWallet by remember { mutableStateOf(false) }
    var balance by remember { mutableStateOf("0.00") }
    var address by remember { mutableStateOf("Q7Xk9m2...3pQr") }

    if (!hasWallet) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Text("🔮", fontSize = 64.sp)
            Spacer(Modifier.height(24.dp))
            Text("Welcome to Quartz", fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Text("Create a wallet or import an existing one",
                color = QuartzMuted, fontSize = 15.sp, textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 8.dp, bottom = 32.dp))
            Button(
                onClick = { hasWallet = true },
                modifier = Modifier.fillMaxWidth().height(52.dp),
                colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
            ) { Text("Create New Wallet", color = QuartzBg, fontWeight = FontWeight.Bold) }
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = { hasWallet = true },
                modifier = Modifier.fillMaxWidth().height(52.dp)
            ) { Text("Import Wallet") }
        }
    } else {
        Column(modifier = Modifier.fillMaxSize().padding(20.dp)) {
            // Balance card
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = QuartzCard),
                shape = MaterialTheme.shapes.large
            ) {
                Column(modifier = Modifier.padding(24.dp)) {
                    Text("Total Balance", color = QuartzMuted, fontSize = 14.sp)
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(balance, fontSize = 40.sp, fontWeight = FontWeight.ExtraBold)
                        Text(" QZ", color = QuartzAccent, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 4.dp, bottom = 4.dp))
                    }
                    Text("≈ \$0.00 USD", color = QuartzMuted, fontSize = 14.sp)
                    Spacer(Modifier.height(16.dp))
                    Surface(color = QuartzBg, shape = MaterialTheme.shapes.medium) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp).fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(address, color = QuartzMuted, fontSize = 12.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace, modifier = Modifier.weight(1f))
                            Text("📋", color = QuartzAccent)
                        }
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // Action buttons
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = {},
                    modifier = Modifier.weight(1f).height(56.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzCard),
                    shape = MaterialTheme.shapes.medium
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📥", fontSize = 20.sp)
                        Text("Receive", fontSize = 13.sp)
                    }
                }
                Button(
                    onClick = {},
                    modifier = Modifier.weight(1f).height(56.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzCard),
                    shape = MaterialTheme.shapes.medium
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📤", fontSize = 20.sp)
                        Text("Send", fontSize = 13.sp)
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            Text("Transactions", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(12.dp))
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text("📋", fontSize = 48.sp, modifier = Modifier.alpha(0.3f))
                Text("No transactions yet", color = QuartzMuted, fontSize = 14.sp)
            }
        }
    }
}

@Composable
fun MinerScreen() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val bleManager = remember { QuartzBLEManager(context) }

    var isScanning by remember { mutableStateOf(false) }
    var isConnected by remember { mutableStateOf(false) }
    var stats by remember { mutableStateOf<MiningStats?>(null) }
    var walletAddress by remember { mutableStateOf("") }
    var statusMsg by remember { mutableStateOf("") }

    // Set up BLE callbacks
    LaunchedEffect(Unit) {
        bleManager.onStatsUpdate = { newStats -> stats = newStats }
        bleManager.onAddressRead = { addr -> walletAddress = addr }
        bleManager.onConnectionChange = { connected ->
            isConnected = connected
            isScanning = false
            statusMsg = if (connected) "Connected to Quartz-Miner" else "Disconnected"
        }
        bleManager.onScanResult = { name ->
            statusMsg = "Found $name, connecting..."
        }
        bleManager.onError = { err ->
            statusMsg = "Error: $err"
            isScanning = false
        }
    }

    DisposableEffect(Unit) {
        onDispose { bleManager.disconnect() }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("⛏️", fontSize = 48.sp)
        Spacer(Modifier.height(8.dp))
        Text("Quartz Miner", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(16.dp))

        if (!isConnected && stats == null) {
            // Not connected — show pair button
            Text(
                "Connect to your ESP32 miner via Bluetooth",
                color = QuartzMuted, fontSize = 15.sp, textAlign = TextAlign.Center,
                modifier = Modifier.padding(bottom = 24.dp)
            )

            if (isScanning) {
                CircularProgressIndicator(color = QuartzAccent, modifier = Modifier.size(32.dp))
                Spacer(Modifier.height(8.dp))
                Text("Scanning for Quartz-Miner...", color = QuartzMuted, fontSize = 14.sp)
            } else {
                Button(
                    onClick = {
                        isScanning = true
                        statusMsg = "Scanning..."
                        bleManager.startScan()
                    },
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
                ) {
                    Text("🔗 Pair ESP32 Miner", color = QuartzBg, fontWeight = FontWeight.Bold)
                }
            }
        } else {
            // Connected — show mining stats
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = QuartzCard),
                shape = MaterialTheme.shapes.large
            ) {
                Column(modifier = Modifier.padding(20.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("● ", color = QuartzAccent, fontSize = 14.sp)
                        Text("Connected", color = QuartzAccent, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Spacer(Modifier.height(16.dp))

                    stats?.let { s ->
                        StatRow("Hashrate", "${s.hashRate} H/s")
                        StatRow("Total Hashes", "${s.hashCount}")
                        StatRow("Blocks Found", "${s.blocksFound}")
                        StatRow("Uptime", formatUptime(s.uptimeSeconds))
                    } ?: Text("Waiting for stats...", color = QuartzMuted, fontSize = 14.sp)

                    if (walletAddress.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        Text("Wallet Address", color = QuartzMuted, fontSize = 12.sp)
                        Text(
                            walletAddress,
                            color = QuartzText, fontSize = 11.sp,
                            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                        )
                    }
                }
            }

            Spacer(Modifier.height(16.dp))

            OutlinedButton(
                onClick = { bleManager.disconnect() },
                modifier = Modifier.fillMaxWidth().height(48.dp)
            ) {
                Text("Disconnect", color = QuartzOrange)
            }
        }

        if (statusMsg.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            Text(statusMsg, color = QuartzMuted, fontSize = 13.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
fun StatRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, color = QuartzMuted, fontSize = 14.sp)
        Text(value, color = QuartzText, fontSize = 14.sp, fontWeight = FontWeight.Medium)
    }
}

fun formatUptime(seconds: Long): String {
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    val s = seconds % 60
    return if (h > 0) "${h}h ${m}m ${s}s"
    else if (m > 0) "${m}m ${s}s"
    else "${s}s"
}

@Composable
fun SettingsScreen() {
    Column(modifier = Modifier.fillMaxSize().padding(20.dp)) {
        Text("Settings", fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(24.dp))

        OutlinedTextField(
            value = "https://quartz.preview.saasclaw.ai",
            onValueChange = {},
            label = { Text("Node URL") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true
        )
        Spacer(Modifier.height(16.dp))

        OutlinedTextField(
            value = "USD",
            onValueChange = {},
            label = { Text("Currency") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true
        )
        Spacer(Modifier.height(24.dp))

        OutlinedButton(onClick = {}, modifier = Modifier.fillMaxWidth().height(52.dp)) {
            Text("📤 Export Wallet")
        }
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = {},
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = QuartzOrange)
        ) { Text("🗑 Delete Wallet") }
    }
}
