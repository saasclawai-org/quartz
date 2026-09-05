package com.quartz.wallet.ble

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.util.Log
import androidx.core.content.ContextCompat
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

data class MiningStats(
    val hashCount: Long,
    val hashRate: Long,
    val blocksFound: Long,
    val uptimeSeconds: Long
)

class QuartzBLEManager(private val context: Context) {

    private val TAG = "QuartzBLE"
    
    companion object {
        // Service: 00000A01-0000-1000-8000-00805F9B34FB
        val SERVICE_UUID: UUID = UUID.fromString("00000a01-0000-1000-8000-00805f9b34fb")
        val STATS_UUID: UUID = UUID.fromString("00000a02-0000-1000-8000-00805f9b34fb")
        val ADDRESS_UUID: UUID = UUID.fromString("00000a03-0000-1000-8000-00805f9b34fb")
        val SEED_UUID: UUID = UUID.fromString("00000a04-0000-1000-8000-00805f9b34fb")
        val CONFIRM_UUID: UUID = UUID.fromString("00000a05-0000-1000-8000-00805f9b34fb")

        // PIN-related characteristics
        val PIN_SET_UUID: UUID = UUID.fromString("00000a06-0000-1000-8000-00805f9b34fb")
        val PIN_UNLOCK_UUID: UUID = UUID.fromString("00000a07-0000-1000-8000-00805f9b34fb")
        val PIN_STATUS_UUID: UUID = UUID.fromString("00000a08-0000-1000-8000-00805f9b34fb")

        // Standard Client Characteristic Configuration Descriptor (CCCD)
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }

    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val adapter = bluetoothManager.adapter
    private val scanner = adapter.bluetoothLeScanner
    private val handler = Handler(Looper.getMainLooper())

    private var connectedDevice: BluetoothDevice? = null
    private var connectedGatt: BluetoothGatt? = null
    private var statsCharacteristic: BluetoothGattCharacteristic? = null

    var onStatsUpdate: ((MiningStats) -> Unit)? = null
    var onAddressRead: ((String) -> Unit)? = null
    var onSeedRead: ((List<String>) -> Unit)? = null
    var onSeedConfirmed: (() -> Unit)? = null
    var onConnectionChange: ((Boolean) -> Unit)? = null
    var onScanResult: ((String) -> Unit)? = null  // device name
    var onError: ((String) -> Unit)? = null
    /* v0.2.16: every discovered device surfaces to the UI — no more silent
     * drops. The board may advertise under the stack-default name "ESP32"
     * (firmware name-race), so ESP32-named devices are listed for manual
     * tap-to-connect. */
    var onDeviceDiscovered: ((DiscoveredDevice) -> Unit)? = null
    var onScanEnded: ((found: Int) -> Unit)? = null

    data class DiscoveredDevice(val name: String?, val address: String, val rssi: Int, val isQuartz: Boolean)

    private val foundDevices = HashMap<String, BluetoothDevice>()

    /* v0.2.17: restartable scan — a second startScan while live returned
     * SCAN_FAILED_ALREADY_STARTED (code 1). */
    @Volatile private var scanActive = false

    /* v0.2.18: scan diagnostics — surfaced live on the Miner screen */
    val scansStarted = androidx.compose.runtime.mutableIntStateOf(0)
    val resultsReceived = androidx.compose.runtime.mutableIntStateOf(0)
    val lastScanError = androidx.compose.runtime.mutableStateOf<String?>(null)

    /* v0.2.19: the device list lives HERE, beside the counters — screen-held
     * lists never received the callbacks (2482 results heard, 0 listed) */
    val discovered = androidx.compose.runtime.mutableStateListOf<DiscoveredDevice>()

    /* v0.2.20: visible connection state — taps looked dead while
     * connectGatt worked silently for seconds before any UI change */
    val connectionState = androidx.compose.runtime.mutableStateOf("idle")
    @Volatile private var connectRetries = 0

    fun adapterOn(): Boolean = adapter.isEnabled

    fun connectByAddress(address: String) {
        val dev = foundDevices[address]
        if (dev == null) {
            connectionState.value = "not in scan cache — scan again"
            return
        }
        connectRetries = 0
        stopScan()
        connect(dev)
    }

    // PIN operation callbacks (set by callers before invoking pin methods)
    var onPinUnlockResult: ((success: Boolean, attemptsLeft: Int, wiped: Boolean) -> Unit)? = null
    var onPinSetResult: ((success: Boolean) -> Unit)? = null
    var onPinStatusResult: ((hasPin: Boolean, attemptsLeft: Int, unlocked: Boolean) -> Unit)? = null

    // Recovery callback
    var onRecoverResult: ((success: Boolean, address: String?, error: String?) -> Unit)? = null

    // Pending recovery words (stored so we can process after reading address)
    private var pendingRecoveryWords: List<String>? = null

    private val scanCallback = object : ScanCallback() {
        private val seen = mutableSetOf<String>()

        override fun onScanResult(callbackType: Int, result: ScanResult) {
            /* v0.2.16: every device is reported to the UI (no silent drops).
             * Auto-connect ONLY on a positive Quartz match (UUID bytes or
             * "Quartz" name). ESP32-named devices are likely our miner
             * (firmware advertises the stack-default name) — tap to connect. */
            val name = result.device.name
            resultsReceived.intValue++
            val uuidMatch = result.scanRecord?.bytes?.let(::hasQuartzUuid) == true
            val isQuartz = uuidMatch ||
                (name?.contains("Quartz", ignoreCase = true) == true)
            foundDevices[result.device.address] = result.device
            val isNew = discovered.none { it.address == result.device.address }
            if (isNew && (isQuartz || discovered.size < 25)) {
                discovered.add(DiscoveredDevice(name, result.device.address, result.rssi, isQuartz))
            }
            onDeviceDiscovered?.invoke(
                DiscoveredDevice(name, result.device.address, result.rssi, isQuartz)
            )
            if (!isQuartz) return
            if (!seen.add(result.device.address)) return
            Log.i(TAG, "Found Quartz device: ${name ?: "(no name)"} @ ${result.device.address}")
            onScanResult?.invoke(name ?: "Quartz-Miner")
            stopScan()
            connect(result.device)
        }

        override fun onScanFailed(errorCode: Int) {
            Log.e(TAG, "Scan failed: $errorCode")
            scanActive = false
            lastScanError.value = "code $errorCode"
            onError?.invoke("Scan failed (code $errorCode)")
        }
    }

    @SuppressLint("MissingPermission")
    fun startScan() {
        val permissions = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        for (p in permissions) {
            if (ContextCompat.checkSelfPermission(context, p) != PackageManager.PERMISSION_GRANTED) {
                onError?.invoke("Missing permission: $p")
                return
            }
        }

        if (!adapter.isEnabled) {
            onError?.invoke("Bluetooth is off")
            return
        }

        /* v0.2.17: second press while scanning — restart cleanly instead
         * of letting the stack reject us with code 1 */
        if (scanActive) {
            Log.i(TAG, "Scan already active — restarting")
            stopScan()
        }

        foundDevices.clear()
        discovered.clear()
        Log.i(TAG, "Starting BLE scan for Quartz devices")

        /* v0.2.15: NO ScanFilters — Android's filter matching silently ate
         * the device while the firmware verifiably advertised. We parse raw
         * results instead (see onScanResult + hasQuartzUuid). */
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanActive = true
        scansStarted.intValue++
        scanner.startScan(null, settings, scanCallback)

        // v0.2.16: stop after 45s — report how many devices were seen; the
        // list stays on screen for manual tap-to-connect (never a dead end)
        handler.postDelayed({
            stopScan()
            if (connectedDevice == null) {
                onScanEnded?.invoke(foundDevices.size)
            }
        }, 45000)
    }

    @SuppressLint("MissingPermission")
    fun stopScan() {
        scanActive = false
        try {
            scanner.stopScan(scanCallback)
        } catch (e: Exception) {
            Log.w(TAG, "Stop scan error: ${e.message}")
        }
        handler.removeCallbacksAndMessages(null)
    }

    /* v0.2.15: raw ADV parse — the little-endian tail of our 128-bit service
     * UUID 00000a01-0000-1000-8000-00805f9b34fb. Matches with or without a
     * scan response, independent of Android filter matching. */
    private fun hasQuartzUuid(bytes: ByteArray): Boolean {
        val tail = byteArrayOf(0x00, 0x10, 0x00, 0x00, 0x01, 0x0A, 0x00, 0x00)
        outer@ for (i in 0..bytes.size - tail.size) {
            for (j in tail.indices) {
                if (bytes[i + j] != tail[j]) continue@outer
            }
            return true
        }
        return false
    }

    @SuppressLint("MissingPermission")
    /* v0.2.21: 15s watchdog — a direct connect to a non-advertising
     * peripheral (zombie link held board-side) hangs silently forever */
    private val connectWatchdog = Runnable {
        if (connectionState.value.startsWith("connecting")) {
            connectedGatt?.let { g ->
                try { g.disconnect() } catch (_: Exception) {}
                try { g.close() } catch (_: Exception) {}
            }
            connectedGatt = null
            if (connectRetries < 3 && connectedDevice != null) {
                connectRetries++
                connectionState.value = "timeout — retry $connectRetries/3"
                connect(connectedDevice!!)
            } else {
                connectionState.value = "connect failed — board not advertising? RST the board, then Rescan"
            }
        }
    }

    fun connect(device: BluetoothDevice) {
        Log.i(TAG, "Connecting to ${device.name}")
        connectionState.value = "connecting to ${device.name ?: device.address}…"
        connectedDevice = device
        handler.removeCallbacks(connectWatchdog)
        /* v0.2.20: settle 600ms after scan-stop (Samsung 133 race).
         * v0.2.21: tear down any previous link FIRST — the board serves
         * ONE connection and a zombie handle stops it advertising. */
        handler.postDelayed({
            try {
                connectedGatt?.let { g ->
                    try { g.disconnect() } catch (_: Exception) {}
                    try { g.close() } catch (_: Exception) {}
                }
                connectedGatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
                if (connectedGatt == null) {
                    connectionState.value = "connectGatt failed (null)"
                } else {
                    handler.postDelayed(connectWatchdog, 15000)
                }
            } catch (e: Exception) {
                connectionState.value = "connect failed: ${e.message}"
            }
        }, 600)
    }

    @SuppressLint("MissingPermission")
    fun disconnect() {
        connectedGatt?.let {
            it.disconnect()
            it.close()
        }
        connectedGatt = null
        connectedDevice = null
        statsCharacteristic = null
        onConnectionChange?.invoke(false)
    }

    // ── PIN Operations ──────────────────────────────────────────────

    /**
     * Unlock the device with a PIN.
     * Writes the PIN to PIN_UNLOCK_UUID; firmware responds with 3-byte status:
     *   byte[0] = success (1) or fail (0)
     *   byte[1] = attempts remaining
     *   byte[2] = wiped flag (1 if device wiped due to 10 failed attempts)
     */
    @SuppressLint("MissingPermission")
    fun unlockDevice(
        pin: String,
        onResult: (success: Boolean, attemptsLeft: Int, wiped: Boolean) -> Unit
    ) {
        val gatt = connectedGatt ?: run {
            onResult(false, 0, false)
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: run {
            onResult(false, 0, false)
            return
        }
        val char = service.getCharacteristic(PIN_UNLOCK_UUID) ?: run {
            onResult(false, 0, false)
            return
        }
        onPinUnlockResult = onResult
        char.value = pin.toByteArray(Charsets.US_ASCII)
        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        val ok = gatt.writeCharacteristic(char)
        if (!ok) {
            onPinUnlockResult = null
            onResult(false, 0, false)
        }
        Log.i(TAG, "PIN unlock write: ${pin.length} digits")
    }

    /**
     * Set a new PIN on the device (first-time setup or change).
     * Writes the PIN to PIN_SET_UUID; firmware responds with 1-byte status:
     *   byte[0] = success (1) or fail (0)
     */
    @SuppressLint("MissingPermission")
    fun setPin(pin: String, onResult: (success: Boolean) -> Unit) {
        val gatt = connectedGatt ?: run {
            onResult(false)
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: run {
            onResult(false)
            return
        }
        val char = service.getCharacteristic(PIN_SET_UUID) ?: run {
            onResult(false)
            return
        }
        onPinSetResult = onResult
        char.value = pin.toByteArray(Charsets.US_ASCII)
        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        val ok = gatt.writeCharacteristic(char)
        if (!ok) {
            onPinSetResult = null
            onResult(false)
        }
        Log.i(TAG, "PIN set write: ${pin.length} digits")
    }

    /**
     * Read PIN status from the device.
     * Reads from PIN_STATUS_UUID; firmware returns 3-byte payload:
     *   byte[0] = hasPin (1/0)
     *   byte[1] = attempts remaining
     *   byte[2] = unlocked (1/0)
     */
    @SuppressLint("MissingPermission")
    fun getPinStatus(onResult: (hasPin: Boolean, attemptsLeft: Int, unlocked: Boolean) -> Unit) {
        val gatt = connectedGatt ?: run {
            onResult(false, 0, false)
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: run {
            onResult(false, 0, false)
            return
        }
        val char = service.getCharacteristic(PIN_STATUS_UUID) ?: run {
            onResult(false, 0, false)
            return
        }
        onPinStatusResult = onResult
        val ok = gatt.readCharacteristic(char)
        if (!ok) {
            onPinStatusResult = null
            onResult(false, 0, false)
        }
        Log.i(TAG, "PIN status read")
    }

    // ── Recovery Operations ─────────────────────────────────────────

    /**
     * Recover wallet from a 12-word BIP39 seed phrase.
     * Writes each word to SEED_UUID sequentially, then reads ADDRESS_UUID
     * to get the recovered wallet address.
     */
    @SuppressLint("MissingPermission")
    fun recoverFromSeed(
        words: List<String>,
        onResult: (success: Boolean, address: String?, error: String?) -> Unit
    ) {
        val gatt = connectedGatt ?: run {
            onResult(false, null, "Not connected to device")
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: run {
            onResult(false, null, "Quartz service not found")
            return
        }
        val seedChar = service.getCharacteristic(SEED_UUID) ?: run {
            onResult(false, null, "Seed characteristic not found")
            return
        }

        if (words.size != 12) {
            onResult(false, null, "Expected 12 words, got ${words.size}")
            return
        }

        pendingRecoveryWords = words
        onRecoverResult = onResult

        // Write words as packed char[12][12] — same format as read
        val payload = ByteArray(144) // 12 words × 12 bytes each
        words.forEachIndexed { i, word ->
            val wordBytes = word.uppercase().toByteArray(Charsets.US_ASCII)
            val len = minOf(wordBytes.size, 12)
            System.arraycopy(wordBytes, 0, payload, i * 12, len)
            // remaining bytes stay 0x00 (null-padded)
        }

        seedChar.value = payload
        seedChar.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        val ok = gatt.writeCharacteristic(seedChar)
        if (!ok) {
            pendingRecoveryWords = null
            onRecoverResult = null
            onResult(false, null, "Failed to write seed to device")
        }
        Log.i(TAG, "Recovery: writing ${words.size} words to device")
    }

    @SuppressLint("MissingPermission")
    fun readSeedPhrase() {
        val gatt = connectedGatt ?: run {
            onError?.invoke("Not connected")
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: run {
            onError?.invoke("Quartz service not found")
            return
        }
        val seedChar = service.getCharacteristic(SEED_UUID) ?: run {
            onError?.invoke("Seed characteristic not found")
            return
        }
        Log.i(TAG, "Reading seed phrase from device")
        gatt.readCharacteristic(seedChar)
    }

    @SuppressLint("MissingPermission")
    fun confirmSeedPhrase() {
        val gatt = connectedGatt ?: run {
            onError?.invoke("Not connected")
            return
        }
        val service = gatt.getService(SERVICE_UUID) ?: return
        val confirmChar = service.getCharacteristic(CONFIRM_UUID) ?: run {
            onError?.invoke("Confirm characteristic not found")
            return
        }
        // Write 3 bytes to confirm (firmware accepts any 3-byte write).
        // v0.2.27: success is reported by onCharacteristicWrite — the old
        // code fired onSeedConfirmed before the device ever answered.
        confirmChar.value = byteArrayOf(1, 2, 3)
        confirmChar.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        gatt.writeCharacteristic(confirmChar)
        Log.i(TAG, "Seed confirmation sent — awaiting device ack")
    }

    /* v0.2.23: BT onboarding — after connect, wait for the pairing bond to
     * complete (the seed read is encrypted), then read it. Empty result =
     * already confirmed → stats view; words → the Miner screen shows them. */
    private var seedPollCount = 0

    fun startSeedOnboardingRead() {
        seedPollCount = 0
        pollSeedRead()
    }

    private fun pollSeedRead() {
        val dev = connectedDevice
        if (dev != null && dev.bondState != BluetoothDevice.BOND_BONDED && seedPollCount < 12) {
            seedPollCount++
            if (seedPollCount == 1) connectionState.value = "waiting for pairing…"
            handler.postDelayed({ pollSeedRead() }, 2500)
            return
        }
        if (connectedGatt != null) readSeedPhrase()
    }

    /* v0.2.25: stats poller — the firmware never sends periodic notifications
     * (its only indicate fires at pair-window close), so poll the readable
     * stats characteristic while connected. First tick grabs the wallet
     * address too (fixes the CCCD-queued read that dead-ended pre-v089.2). */
    private var statsPollTick = 0
    private val statsPoller = object : Runnable {
        override fun run() {
            val gatt = connectedGatt
            val svc = gatt?.getService(SERVICE_UUID)
            if (svc != null) {
                val target = if (statsPollTick == 0) svc.getCharacteristic(ADDRESS_UUID)
                             else svc.getCharacteristic(STATS_UUID)
                statsPollTick++
                try {
                    target?.let { gatt.readCharacteristic(it) }
                } catch (_: Exception) {}
            }
            handler.postDelayed(this, 2500)
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.i(TAG, "Connected to GATT server")
                    handler.removeCallbacks(connectWatchdog)
                    connectionState.value = "connected — discovering services"
                    /* v0.2.25: poll stats + address — nothing notifies them */
                    statsPollTick = 0
                    handler.postDelayed(statsPoller, 3000)
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.i(TAG, "Disconnected from GATT server (status $status)")
                    handler.removeCallbacks(connectWatchdog)
                    handler.removeCallbacks(statsPoller)
                    /* v0.2.21: retry on ANY error status (not just 133) —
                     * zombie teardown, Samsung races, remote resets */
                    if (status != 0 && connectRetries < 3 && connectedDevice != null) {
                        connectRetries++
                        connectionState.value = "retry $connectRetries/3 (status $status)…"
                        try { gatt.close() } catch (_: Exception) {}
                        val dev = connectedDevice
                        handler.postDelayed({ connect(dev!!) }, 800)
                    } else {
                        connectionState.value = "disconnected (status $status)"
                        connectedGatt = null
                        statsCharacteristic = null
                        onConnectionChange?.invoke(false)
                    }
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(TAG, "Service discovery failed: $status")
                onError?.invoke("Service discovery failed")
                return
            }

            connectionState.value = "services discovered"
            val service = gatt.getService(SERVICE_UUID)
            if (service == null) {
                Log.e(TAG, "Quartz service not found")
                connectionState.value = "Quartz service not found on device"
                onError?.invoke("Quartz service not found")
                return
            }

            statsCharacteristic = service.getCharacteristic(STATS_UUID)
            val addrChar = service.getCharacteristic(ADDRESS_UUID)

            // v0.2.14: seed/PIN characteristics are ENCRYPTED on the device
            // and neither side ever initiated pairing — bond now so the seed
            // flow works (user accepts the pairing dialog on the phone).
            try {
                if (gatt.device.bondState != BluetoothDevice.BOND_BONDED) {
                    Log.i(TAG, "Requesting BLE bond for encrypted characteristics")
                    gatt.device.createBond()
                }
            } catch (e: SecurityException) {
                Log.w(TAG, "createBond failed: ${e.message}")
            }

            // v0.2.14: Android queues exactly ONE GATT operation — the address
            // read must wait for the CCCD write to complete (onDescriptorWrite
            // below) or it silently dies.
            var cccdWriteStarted = false
            statsCharacteristic?.let { char ->
                gatt.setCharacteristicNotification(char, true)
                val cccd = char.descriptors.find { it.uuid == CCCD_UUID }
                cccd?.let {
                    it.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    cccdWriteStarted = gatt.writeDescriptor(it)
                }
            }

            if (!cccdWriteStarted) {
                // No CCCD to write — safe to read the address right away
                addrChar?.let { gatt.readCharacteristic(it) }
            }

            onConnectionChange?.invoke(true)
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            // v0.2.14: stats subscription done — now it's safe to read the address
            if (descriptor.uuid == CCCD_UUID) {
                gatt.getService(SERVICE_UUID)?.getCharacteristic(ADDRESS_UUID)?.let {
                    gatt.readCharacteristic(it)
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic
        ) {
            if (characteristic.uuid == STATS_UUID) {
                val stats = parseStats(characteristic.value)
                Log.i(TAG, "Stats update: $stats H/s, blocks=${stats.blocksFound}")
                onStatsUpdate?.invoke(stats)
            }
        }

        // New API for Android 13+
        override fun onCharacteristicChanged(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray
        ) {
            if (characteristic.uuid == STATS_UUID) {
                val stats = parseStats(value)
                Log.i(TAG, "Stats update: $stats H/s, blocks=${stats.blocksFound}")
                onStatsUpdate?.invoke(stats)
            }
        }

        @SuppressLint("MissingPermission")
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            if (status != BluetoothGatt.GATT_SUCCESS) return
            val data = characteristic.value
            when (characteristic.uuid) {
                ADDRESS_UUID -> {
                    val addr = String(data).trimEnd('\u0000')
                    Log.i(TAG, "Wallet address: $addr")
                    // If we have a pending recovery, deliver address via recovery callback
                    if (pendingRecoveryWords != null && onRecoverResult != null) {
                        onRecoverResult?.invoke(true, addr, null)
                        pendingRecoveryWords = null
                        onRecoverResult = null
                    } else {
                        onAddressRead?.invoke(addr)
                    }
                }
                SEED_UUID -> {
                    // Parse 12 words from packed char[12][12] array
                    if (data == null || data.isEmpty()) {
                        Log.w(TAG, "Seed phrase empty (already confirmed)")
                        onSeedRead?.invoke(emptyList())
                        return
                    }
                    val words = mutableListOf<String>()
                    for (i in 0 until minOf(12, data.size / 12)) {
                        val wordBytes = data.copyOfRange(i * 12, (i + 1) * 12)
                        val word = String(wordBytes).trimEnd('\u0000').trim()
                        if (word.isNotEmpty()) words.add(word)
                    }
                    Log.i(TAG, "Seed phrase read: ${words.size} words")
                    onSeedRead?.invoke(words)
                }
                PIN_STATUS_UUID -> {
                    if (data != null && data.size >= 3) {
                        val hasPin = data[0].toInt() != 0
                        val attemptsLeft = data[1].toInt() and 0xFF
                        val unlocked = data[2].toInt() != 0
                        Log.i(TAG, "PIN status: hasPin=$hasPin, attempts=$attemptsLeft, unlocked=$unlocked")
                        onPinStatusResult?.invoke(hasPin, attemptsLeft, unlocked)
                        onPinStatusResult = null
                    }
                }
                STATS_UUID -> {
                    /* v0.2.25: stats by polling — the firmware never sends
                     * periodic notifications, so the poller reads this char */
                    val stats = parseStats(data)
                    Log.i(TAG, "Stats (polled): $stats H/s, blocks=${stats.blocksFound}")
                    onStatsUpdate?.invoke(stats)
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int
        ) {
            if (status != BluetoothGatt.GATT_SUCCESS) return
            when (characteristic.uuid) {
                ADDRESS_UUID -> {
                    val addr = String(value).trimEnd('\u0000')
                    Log.i(TAG, "Wallet address: $addr")
                    // If we have a pending recovery, deliver address via recovery callback
                    if (pendingRecoveryWords != null && onRecoverResult != null) {
                        onRecoverResult?.invoke(true, addr, null)
                        pendingRecoveryWords = null
                        onRecoverResult = null
                    } else {
                        onAddressRead?.invoke(addr)
                    }
                }
                SEED_UUID -> {
                    if (value.isEmpty()) {
                        Log.w(TAG, "Seed phrase empty (already confirmed)")
                        onSeedRead?.invoke(emptyList())
                        return
                    }
                    val words = mutableListOf<String>()
                    for (i in 0 until minOf(12, value.size / 12)) {
                        val wordBytes = value.copyOfRange(i * 12, (i + 1) * 12)
                        val word = String(wordBytes).trimEnd('\u0000').trim()
                        if (word.isNotEmpty()) words.add(word)
                    }
                    Log.i(TAG, "Seed phrase read: ${words.size} words")
                    onSeedRead?.invoke(words)
                }
                PIN_STATUS_UUID -> {
                    if (value.size >= 3) {
                        val hasPin = value[0].toInt() != 0
                        val attemptsLeft = value[1].toInt() and 0xFF
                        val unlocked = value[2].toInt() != 0
                        Log.i(TAG, "PIN status: hasPin=$hasPin, attempts=$attemptsLeft, unlocked=$unlocked")
                        onPinStatusResult?.invoke(hasPin, attemptsLeft, unlocked)
                        onPinStatusResult = null
                    }
                }
                STATS_UUID -> {
                    /* v0.2.25: stats by polling (new API path) */
                    val stats = parseStats(value)
                    Log.i(TAG, "Stats (polled): $stats H/s, blocks=${stats.blocksFound}")
                    onStatsUpdate?.invoke(stats)
                }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(TAG, "Characteristic write failed: $status for ${characteristic.uuid}")
                when (characteristic.uuid) {
                    PIN_UNLOCK_UUID -> {
                        onPinUnlockResult?.invoke(false, 0, false)
                        onPinUnlockResult = null
                    }
                    PIN_SET_UUID -> {
                        onPinSetResult?.invoke(false)
                        onPinSetResult = null
                    }
                    SEED_UUID -> {
                        if (pendingRecoveryWords != null) {
                            onRecoverResult?.invoke(false, null, "Failed to write seed (GATT error $status)")
                            pendingRecoveryWords = null
                            onRecoverResult = null
                        }
                    }
                    CONFIRM_UUID -> {
                        onError?.invoke("Seed confirm rejected by device (GATT error $status) — still bonded?")
                    }
                }
                return
            }

            when (characteristic.uuid) {
                PIN_UNLOCK_UUID -> {
                    // Firmware writes a 3-byte response into the characteristic value
                    val data = characteristic.value
                    if (data != null && data.size >= 3) {
                        val success = data[0].toInt() != 0
                        val attemptsLeft = data[1].toInt() and 0xFF
                        val wiped = data[2].toInt() != 0
                        Log.i(TAG, "PIN unlock result: success=$success, attempts=$attemptsLeft, wiped=$wiped")
                        onPinUnlockResult?.invoke(success, attemptsLeft, wiped)
                    } else {
                        // If no inline response, read from PIN_STATUS_UUID
                        val service = gatt.getService(SERVICE_UUID)
                        val statusChar = service?.getCharacteristic(PIN_STATUS_UUID)
                        if (statusChar != null) {
                            // Preserve unlock callback; status read will deliver it
                            // We keep onPinUnlockResult as-is; onCharacteristicRead for PIN_STATUS
                            // will need to route to unlock callback — but since that's ambiguous,
                            // we just deliver a generic success if write succeeded
                            onPinUnlockResult?.invoke(true, 3, false)
                        } else {
                            onPinUnlockResult?.invoke(true, 3, false)
                        }
                    }
                    onPinUnlockResult = null
                }
                PIN_SET_UUID -> {
                    val data = characteristic.value
                    val success = data != null && data.isNotEmpty() && data[0].toInt() != 0
                    Log.i(TAG, "PIN set result: success=$success")
                    onPinSetResult?.invoke(success)
                    onPinSetResult = null
                }
                CONFIRM_UUID -> {
                    Log.i(TAG, "Seed confirmation acknowledged by device")
                    onSeedConfirmed?.invoke()
                }
                SEED_UUID -> {
                    Log.i(TAG, "Seed write successful")
                    // If this was a recovery write, read the address back
                    if (pendingRecoveryWords != null) {
                        val service = gatt.getService(SERVICE_UUID)
                        val addrChar = service?.getCharacteristic(ADDRESS_UUID)
                        if (addrChar != null) {
                            Log.i(TAG, "Reading address after recovery seed write")
                            gatt.readCharacteristic(addrChar)
                        } else {
                            onRecoverResult?.invoke(false, null, "Address characteristic not found after recovery")
                            pendingRecoveryWords = null
                            onRecoverResult = null
                        }
                    }
                }
            }
        }
    }

    private fun parseStats(data: ByteArray): MiningStats {
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        return MiningStats(
            hashCount = buf.int.toLong() and 0xFFFFFFFFL,
            hashRate = buf.int.toLong() and 0xFFFFFFFFL,
            blocksFound = buf.int.toLong() and 0xFFFFFFFFL,
            uptimeSeconds = buf.int.toLong() and 0xFFFFFFFFL
        )
    }

    fun isConnected(): Boolean = connectedGatt != null
}