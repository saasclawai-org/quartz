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
import com.quartz.wallet.data.WalletStore
import com.quartz.wallet.wallet.SoftwareWallet
import com.quartz.wallet.crypto.QuartzCrypto
import com.quartz.wallet.ui.theme.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.text.AnnotatedString
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QuartzWalletApp() {
    val pagerState = rememberPagerState(pageCount = { 3 })
    val scope = rememberCoroutineScope()
    val context = androidx.compose.ui.platform.LocalContext.current
    val bleManager = remember { QuartzBLEManager(context) }

    // Navigation state for seed provisioning overlay
    var showProvisioning by remember { mutableStateOf(false) }
    var walletCreated by remember { mutableStateOf(false) }
    // Bumped when the wallet is deleted — forces WalletScreen to rebuild
    // from storage (shows onboarding again instead of stale state)
    var walletEpoch by remember { mutableIntStateOf(0) }

    if (showProvisioning) {
        SeedProvisioningScreen(
            bleManager = bleManager,
            onDone = {
                showProvisioning = false
                walletCreated = true
            }
        )
        return
    }

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
                0 -> key(walletEpoch) {
                    WalletScreen(
                        walletCreated = walletCreated,
                        onImport = { showProvisioning = true }
                    )
                }
                1 -> MinerScreen(bleManager = bleManager)
                2 -> SettingsScreen(onWalletDeleted = {
                    walletCreated = false
                    walletEpoch++
                })
            }
        }
    }
}

@Composable
fun WalletScreen(walletCreated: Boolean = false, onImport: () -> Unit = {}) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = androidx.compose.ui.platform.LocalClipboardManager.current

    var hasWallet by remember { mutableStateOf(WalletStore(context).hasWallet()) }
    var address by remember { mutableStateOf(WalletStore(context).getAddress()) }
    var balanceSats by remember { mutableStateOf<Long?>(null) }
    var txCount by remember { mutableStateOf(0) }
    var refreshing by remember { mutableStateOf(false) }
    var statusMsg by remember { mutableStateOf<String?>(null) }

    // Create flow
    var pendingWallet by remember { mutableStateOf<SoftwareWallet.NewWallet?>(null) }
    var backedUp by remember { mutableStateOf(false) }

    // Restore flow
    var showRestore by remember { mutableStateOf(false) }
    var restoreWords by remember { mutableStateOf(List(12) { "" }) }
    var restoreError by remember { mutableStateOf<String?>(null) }

    var showReceive by remember { mutableStateOf(false) }
    var showSend by remember { mutableStateOf(false) }
    var fauceting by remember { mutableStateOf(false) }

    // On-chain name registry
    var myName by remember { mutableStateOf<String?>(null) }
    var showNameDialog by remember { mutableStateOf(false) }

    // BLE provisioning may have created a wallet — re-check when told so
    LaunchedEffect(walletCreated) {
        val store = WalletStore(context)
        if (store.hasWallet()) {
            hasWallet = true
            address = store.getAddress()
        }
    }

    fun refreshBalance() {
        val addr = address ?: return
        refreshing = true
        scope.launch {
            SoftwareWallet.fetchBalance(addr).onSuccess {
                balanceSats = it.balanceSats
                txCount = it.txCount
            }.onFailure { statusMsg = it.message }
            refreshing = false
        }
    }

    LaunchedEffect(address) { if (address != null) refreshBalance() }

    LaunchedEffect(address) {
        address?.let { myName = SoftwareWallet.fetchName(it) }
    }

    val pending = pendingWallet
    when {
        // ── Seed reveal (create flow, step 2) ──────────────────────
        pending != null -> SeedRevealScreen(
            words = pending.words,
            backedUp = backedUp,
            onChecked = { backedUp = it },
            onConfirm = {
                SoftwareWallet.save(context, pending)
                pendingWallet = null
                backedUp = false
                hasWallet = true
                address = pending.address
            }
        )

        // ── Onboarding ─────────────────────────────────────────────
        !hasWallet -> Column(
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
                onClick = { pendingWallet = SoftwareWallet.create() },
                modifier = Modifier.fillMaxWidth().height(52.dp),
                colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
            ) { Text("Create New Wallet", color = QuartzBg, fontWeight = FontWeight.Bold) }
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = { showRestore = true },
                modifier = Modifier.fillMaxWidth().height(52.dp)
            ) { Text("🗝 Restore from Seed Phrase") }
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = onImport,
                modifier = Modifier.fillMaxWidth().height(52.dp)
            ) { Text("📷 Import Wallet (Scan QR / ESP32)") }
        }

        // ── Wallet view ────────────────────────────────────────────
        else -> Column(modifier = Modifier.fillMaxSize().padding(20.dp)) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = QuartzCard),
                shape = MaterialTheme.shapes.large
            ) {
                Column(modifier = Modifier.padding(24.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Total Balance", color = QuartzMuted, fontSize = 14.sp,
                            modifier = Modifier.weight(1f))
                        if (refreshing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp, color = QuartzAccent
                            )
                        } else {
                            TextButton(onClick = { refreshBalance() }) {
                                Text("↻", color = QuartzMuted, fontSize = 18.sp)
                            }
                        }
                    }
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            balanceSats?.let { formatQz(it) } ?: "…",
                            fontSize = 40.sp, fontWeight = FontWeight.ExtraBold
                        )
                        Text(" QZ", color = QuartzAccent, fontSize = 20.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.padding(start = 4.dp, bottom = 4.dp))
                    }
                    Spacer(Modifier.height(16.dp))
                    Surface(color = QuartzBg, shape = MaterialTheme.shapes.medium) {
                        Row(
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)
                                .fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                address ?: "",
                                color = QuartzMuted, fontSize = 12.sp,
                                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                                modifier = Modifier.weight(1f)
                            )
                            Text("📋", color = QuartzAccent)
                        }
                    }
                    myName?.let {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "🏷 $it",
                            color = QuartzAccent, fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }
            }

            statusMsg?.let {
                Spacer(Modifier.height(8.dp))
                Text(it, color = QuartzOrange, fontSize = 13.sp, textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth())
            }

            // Zero balance — offer the testnet faucet prominently
            if (balanceSats == 0L && !refreshing) {
                Spacer(Modifier.height(16.dp))
                Card(
                    colors = CardDefaults.cardColors(containerColor = QuartzCard),
                    shape = MaterialTheme.shapes.large
                ) {
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(20.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text("🚰", fontSize = 32.sp)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "This wallet is new — it has no testnet QZ yet.",
                            fontSize = 14.sp, textAlign = TextAlign.Center
                        )
                        Text(
                            "Use the faucet to get 100 test QZ.",
                            fontSize = 13.sp, color = QuartzMuted, textAlign = TextAlign.Center
                        )
                        Spacer(Modifier.height(12.dp))
                        Button(
                            onClick = {
                                val addr = address ?: return@Button
                                fauceting = true
                                scope.launch {
                                    SoftwareWallet.faucet(addr)
                                        .onSuccess {
                                            statusMsg = "🚰 Faucet sent 100 QZ — refreshing in ~35s"
                                            android.widget.Toast.makeText(
                                                context, "Faucet sent — confirm in ~35s", android.widget.Toast.LENGTH_SHORT
                                            ).show()
                                        }
                                        .onFailure { statusMsg = "Faucet: ${it.message}" }
                                    fauceting = false
                                    refreshing = true
                                    kotlinx.coroutines.delay(40_000)
                                    SoftwareWallet.fetchBalance(addr).onSuccess {
                                        balanceSats = it.balanceSats
                                        txCount = it.txCount
                                    }
                                    refreshing = false
                                }
                            },
                            enabled = !fauceting,
                            modifier = Modifier.fillMaxWidth().height(48.dp),
                            colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
                        ) {
                            Text(
                                if (fauceting) "Requesting…" else "Get 100 Test QZ",
                                color = QuartzBg, fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = { showReceive = true },
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
                    onClick = { showSend = true },
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

            OutlinedButton(
                onClick = { showNameDialog = true },
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp).height(44.dp)
            ) {
                Text(if (myName == null) "🏷 Set On-Chain Name" else "🏷 Rename Wallet", fontSize = 13.sp)
            }

            Spacer(Modifier.height(24.dp))

            Text("Transactions", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(12.dp))
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text("📋", fontSize = 48.sp, modifier = Modifier.alpha(0.3f))
                Text(
                    if (txCount > 0) "$txCount transaction${if (txCount == 1) "" else "s"} on-chain" else "No transactions yet",
                    color = QuartzMuted, fontSize = 14.sp
                )
            }
        }
    }

    // ── Restore dialog ────────────────────────────────────────────
    if (showRestore) {
        AlertDialog(
            onDismissRequest = { showRestore = false; restoreError = null },
            title = { Text("🗝 Restore Wallet") },
            text = {
                Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                    Text("Enter your 12-word seed phrase",
                        fontSize = 13.sp, color = QuartzMuted,
                        modifier = Modifier.padding(bottom = 12.dp))
                    for (row in 0 until 4) {
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            for (col in 0 until 3) {
                                val i = row * 3 + col
                                OutlinedTextField(
                                    value = restoreWords[i],
                                    onValueChange = { v -> restoreWords = restoreWords.toMutableList().also { it[i] = v.trim().lowercase() } },
                                    label = { Text("${i + 1}", fontSize = 10.sp) },
                                    textStyle = androidx.compose.ui.text.TextStyle(fontSize = 13.sp),
                                    modifier = Modifier.weight(1f),
                                    singleLine = true
                                )
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                    restoreError?.let {
                        Spacer(Modifier.height(6.dp))
                        Text(it, color = QuartzOrange, fontSize = 13.sp)
                    }
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        restoreError = null
                        try {
                            val wallet = SoftwareWallet.restore(restoreWords)
                            SoftwareWallet.save(context, wallet)
                            hasWallet = true
                            address = wallet.address
                            showRestore = false
                        } catch (e: IllegalArgumentException) {
                            restoreError = e.message
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
                ) { Text("Restore", color = QuartzBg) }
            },
            dismissButton = {
                TextButton(onClick = { showRestore = false; restoreError = null }) { Text("Cancel") }
            }
        )
    }

    // ── Receive dialog ────────────────────────────────────────────
    if (showReceive && address != null) {
        AlertDialog(
            onDismissRequest = { showReceive = false },
            title = { Text("📥 Receive QZ") },
            text = {
                Column {
                    Text("Your address:", fontSize = 13.sp, color = QuartzMuted)
                    Spacer(Modifier.height(6.dp))
                    Surface(color = QuartzBg, shape = MaterialTheme.shapes.medium) {
                        Text(
                            address!!,
                            fontSize = 13.sp,
                            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                            modifier = Modifier.padding(12.dp)
                        )
                    }
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = {
                            clipboard.setText(AnnotatedString(address!!))
                            android.widget.Toast.makeText(context, "Address copied", android.widget.Toast.LENGTH_SHORT).show()
                        }, modifier = Modifier.weight(1f)) { Text("📋 Copy") }
                        OutlinedButton(onClick = {
                            scope.launch {
                                SoftwareWallet.faucet(address!!).onSuccess {
                                    statusMsg = "🚰 Faucet: $it"
                                    android.widget.Toast.makeText(context, "Faucet sent — refresh in ~30s", android.widget.Toast.LENGTH_SHORT).show()
                                    refreshBalance()
                                }.onFailure { statusMsg = "Faucet: ${it.message}" }
                            }
                        }, modifier = Modifier.weight(1f)) { Text("🚰 Testnet Faucet") }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { showReceive = false }) { Text("Close") } }
        )
    }

    // ── Send dialog ───────────────────────────────────────────────────────
    if (showSend) {
        SendDialog(
            address = address ?: "",
            balanceSats = balanceSats ?: 0,
            onDismiss = { showSend = false },
            onSent = { msg -> showSend = false; statusMsg = msg; refreshBalance() }
        )
    }

    // ── On-chain name dialog ────────────────────────────────────────
    if (showNameDialog && address != null) {
        NameDialog(
            address = address!!,
            initial = myName,
            onDismiss = { showNameDialog = false },
            onRegistered = { label ->
                myName = label
                showNameDialog = false
                statusMsg = "🏷 Name registered — confirming in ~30s"
            }
        )
    }
}

@Composable
private fun SeedRevealScreen(
    words: List<String>,
    backedUp: Boolean,
    onChecked: (Boolean) -> Unit,
    onConfirm: () -> Unit
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp).verticalScroll(rememberScrollState())
    ) {
        Text("🔮", fontSize = 48.sp)
        Spacer(Modifier.height(8.dp))
        Text("Your Seed Phrase", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Text("Write these 12 words down on paper. They are the ONLY way to recover your wallet.",
            color = QuartzOrange, fontSize = 14.sp,
            modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
        Text("Never share them. Never screenshot them.",
            color = QuartzMuted, fontSize = 13.sp,
            modifier = Modifier.padding(bottom = 20.dp))

        for (row in 0 until 4) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (col in 0 until 3) {
                    val i = row * 3 + col
                    Surface(
                        color = QuartzCard,
                        shape = MaterialTheme.shapes.medium,
                        modifier = Modifier.weight(1f)
                    ) {
                        Row(
                            modifier = Modifier.padding(10.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text("${i + 1}. ", color = QuartzMuted, fontSize = 12.sp)
                            Text(words[i], fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
        }

        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = backedUp, onCheckedChange = onChecked)
            Text("I have written down my seed phrase", fontSize = 14.sp)
        }
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = onConfirm,
            enabled = backedUp,
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = QuartzAccent,
                disabledContainerColor = QuartzCard
            )
        ) { Text("Confirm & Open Wallet", color = QuartzBg, fontWeight = FontWeight.Bold) }
    }
}

@Composable
private fun NameDialog(
    address: String,
    initial: String?,
    onDismiss: () -> Unit,
    onRegistered: (String) -> Unit
) {
    val scope = rememberCoroutineScope()
    var label by remember { mutableStateOf(initial ?: "") }
    var kind by remember { mutableStateOf("wallet") }
    var kindMenu by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val kinds = listOf("wallet", "miner", "sensor", "station", "dev", "exchange", "other")

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("🏷 On-Chain Name") },
        text = {
            Column {
                Text(
                    "Your name lives on-chain, registered by your address. " +
                    "On mainnet only the address owner can set it. A rename replaces the old name.",
                    fontSize = 13.sp, color = QuartzMuted,
                    modifier = Modifier.padding(bottom = 12.dp)
                )
                OutlinedTextField(
                    value = label,
                    onValueChange = { label = it },
                    label = { Text("Name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(8.dp))
                Box {
                    OutlinedButton(onClick = { kindMenu = true }) {
                        Text("Kind: $kind", fontSize = 13.sp)
                    }
                    DropdownMenu(expanded = kindMenu, onDismissRequest = { kindMenu = false }) {
                        kinds.forEach { k ->
                            DropdownMenuItem(
                                text = { Text(k) },
                                onClick = { kind = k; kindMenu = false }
                            )
                        }
                    }
                }
                error?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, color = QuartzOrange, fontSize = 13.sp)
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    error = null
                    if (label.isBlank()) { error = "Enter a name"; return@Button }
                    busy = true
                    val finalLabel = label.trim()
                    scope.launch {
                        SoftwareWallet.registerName(address, finalLabel, kind)
                            .onSuccess { onRegistered(finalLabel) }
                            .onFailure {
                                error = it.message ?: it.toString()
                                busy = false
                            }
                    }
                },
                enabled = !busy,
                colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
            ) { Text(if (busy) "Registering…" else "Register", color = QuartzBg) }
        },
        dismissButton = {
            TextButton(onClick = { if (!busy) onDismiss() }) { Text("Cancel") }
        }
    )
}

@Composable
private fun SendDialog(
    address: String,
    balanceSats: Long,
    onDismiss: () -> Unit,
    onSent: (String) -> Unit
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var toAddress by remember { mutableStateOf("") }
    var amount by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var sending by remember { mutableStateOf(false) }
    var resolvedHint by remember { mutableStateOf<String?>(null) }

    // Live on-chain name resolution while typing a recipient that isn't an address
    LaunchedEffect(toAddress) {
        val t = toAddress.trim()
        resolvedHint = when {
            t.isEmpty() || QuartzCrypto.isValidAddress(t) -> null
            else -> SoftwareWallet.resolveAddressForLabel(t)?.first
        }
    }

    val keys = remember(address) { SoftwareWallet.load(context) }
    val watchOnly = keys == null

    // ── PIN gate: sends require wallet PIN (set on first send) ──
    val pinStore = remember { WalletStore(context) }
    var pinGate by remember { mutableStateOf<WalletPinMode?>(null) }

    val doSend: () -> Unit = {
        sending = true
        scope.launch {
            try {
                val keysLocal = keys
                if (keysLocal == null) {
                    error = "Wallet keys not loaded — re-import your seed"
                    sending = false
                    return@launch
                }
                val (priv, _, from) = keysLocal
                if (priv.isEmpty()) {
                    error = "Private key is empty — re-import your seed"
                    sending = false
                    return@launch
                }
                val amt = amount.toDoubleOrNull()
                val sats = amt?.let { (it * 1e8).toLong() } ?: 0
                // Resolve recipient: raw address, or on-chain name lookup
                var target = toAddress.trim()
                if (!QuartzCrypto.isValidAddress(target)) {
                    val hit = SoftwareWallet.resolveAddressForLabel(target)
                    if (hit == null) {
                        error = "Invalid address (no on-chain name matches \"$target\")"
                        sending = false
                        return@launch
                    }
                    if (hit.second) {
                        error = "Name \"$target\" is claimed by multiple addresses — use the full address"
                        sending = false
                        return@launch
                    }
                    target = hit.first
                }
                SoftwareWallet.send(priv, from, target, sats)
                    .onSuccess { onSent("✅ Sent $amount QZ — txid $it") }
                    .onFailure { e ->
                        error = e.message ?: e.toString()
                        sending = false
                        android.widget.Toast.makeText(context, "Send failed: ${e.message ?: e.toString()}", android.widget.Toast.LENGTH_LONG).show()
                    }
            } catch (ce: Exception) {
                error = ce.message ?: ce.toString()
                sending = false
                android.widget.Toast.makeText(context, "Send crashed: ${ce.message ?: ce.toString()}", android.widget.Toast.LENGTH_LONG).show()
            }
        }
    }

    AlertDialog(
        onDismissRequest = { if (!sending) onDismiss() },
        title = { Text("📤 Send QZ") },
        text = {
            Column {
                if (watchOnly) {
                    Text("This is a hardware (ESP32) wallet — sending requires the device. Keys are not on this phone.",
                        color = QuartzOrange, fontSize = 14.sp)
                } else {
                    OutlinedTextField(
                        value = toAddress,
                        onValueChange = { toAddress = it.trim() },
                        label = { Text("Recipient address or name") },
                        textStyle = androidx.compose.ui.text.TextStyle(fontSize = 13.sp),
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    resolvedHint?.let {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "→ $it",
                            fontSize = 11.sp, color = QuartzAccent,
                            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        value = amount,
                        onValueChange = { amount = it },
                        label = { Text("Amount (QZ)") },
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Decimal
                        ),
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Available: ${formatQz(balanceSats)} QZ · fee 0.00001 QZ",
                        fontSize = 12.sp, color = QuartzMuted
                    )
                    error?.let {
                        Spacer(Modifier.height(6.dp))
                        Text(it, color = QuartzOrange, fontSize = 13.sp)
                    }
                }
            }
        },
        confirmButton = {
            if (!watchOnly) {
                Button(
                    onClick = {
                        error = null
                        val amt = amount.toDoubleOrNull()
                        val sats = amt?.let { (it * 1e8).toLong() } ?: 0
                        when {
                            sats <= 0 -> error = "Enter a valid amount"
                            toAddress.isEmpty() -> error = "Enter a recipient address"
                            sats + SoftwareWallet.FEE_SATS > balanceSats ->
                                error = "Insufficient balance (need amount + fee)"
                            else -> {
                                // PIN gate before any send: verify existing PIN
                                // or set one on first use, then dispatch
                                pinGate = if (pinStore.hasPin()) WalletPinMode.VERIFY
                                          else WalletPinMode.SET
                            }
                        }
                    },
                    enabled = !sending,
                    colors = ButtonDefaults.buttonColors(containerColor = QuartzAccent)
                ) { Text(if (sending) "Sending…" else "Send", color = QuartzBg) }
            }
        },
        dismissButton = {
            TextButton(onClick = { if (!sending) onDismiss() }) { Text("Cancel") }
        }
    )

    // PIN gate dialog — rendered on top of the send dialog
    pinGate?.let { mode ->
        WalletPinDialog(
            mode = mode,
            onDone = { pinGate = null; doSend() },
            onDismiss = { pinGate = null }
        )
    }
}

private fun formatQz(sats: Long): String {
    val qz = sats / 1e8
    val s = String.format(java.util.Locale.US, if (qz == Math.floor(qz) && qz < 1e9) "%.0f" else "%.8f", qz)
    return s.trimEnd('0').trimEnd('.')
}
@Composable
fun MinerScreen(bleManager: QuartzBLEManager) {
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
fun SettingsScreen(onWalletDeleted: () -> Unit = {}) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val store = remember { WalletStore(context) }
    var showConfirm by remember { mutableStateOf(false) }
    var confirmText by remember { mutableStateOf("") }
    var deleted by remember { mutableStateOf(false) }

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
            onClick = {
                deleted = false
                confirmText = ""
                showConfirm = true
            },
            enabled = store.hasWallet(),
            modifier = Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = QuartzOrange)
        ) { Text(if (store.hasWallet()) "🗑 Delete Wallet" else "🗑 No Wallet on This Phone") }

        if (!store.hasWallet()) {
            Spacer(Modifier.height(8.dp))
            Text(
                if (deleted) "✓ Wallet deleted. Go to the Wallet tab to create or restore one."
                else "No wallet found on this phone.",
                color = QuartzMuted, fontSize = 13.sp
            )
        }
    }

    if (showConfirm) {
        AlertDialog(
            onDismissRequest = { showConfirm = false; confirmText = "" },
            title = { Text("Delete wallet?") },
            text = {
                Column {
                    Text("This permanently removes your seed phrase and keys from this phone.", fontSize = 14.sp)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Your QZ is ONLY recoverable with your 12-word backup phrase. " +
                        "If you don't have it written down, it will be gone forever.",
                        color = QuartzOrange, fontSize = 14.sp
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = confirmText,
                        onValueChange = { confirmText = it },
                        label = { Text("Type DELETE to confirm") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        store.deleteWallet()
                        showConfirm = false
                        confirmText = ""
                        deleted = true
                        onWalletDeleted()
                    },
                    enabled = confirmText.trim() == "DELETE"
                ) { Text("Delete", color = QuartzOrange, fontWeight = FontWeight.Bold) }
            },
            dismissButton = {
                TextButton(onClick = { showConfirm = false; confirmText = "" }) { Text("Cancel") }
            }
        )
    }
}
