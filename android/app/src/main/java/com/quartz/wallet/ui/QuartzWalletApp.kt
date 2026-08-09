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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
                Text("📋", fontSize = 48.sp, alpha = 0.3f)
                Text("No transactions yet", color = QuartzMuted, fontSize = 14.sp)
            }
        }
    }
}

@Composable
fun MinerScreen() {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text("⛏️", fontSize = 64.sp)
        Spacer(Modifier.height(24.dp))
        Text("Connect Your ESP32", fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text("Pair via Bluetooth to monitor mining stats",
            color = QuartzMuted, fontSize = 15.sp, textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 8.dp, bottom = 32.dp))
        Button(
            onClick = {},
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
        ) { Text("🔗 Pair ESP32 Miner", color = QuartzBg, fontWeight = FontWeight.Bold) }
    }
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
