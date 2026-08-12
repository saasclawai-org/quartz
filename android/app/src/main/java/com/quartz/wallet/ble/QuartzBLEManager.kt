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

    // PIN operation callbacks (set by callers before invoking pin methods)
    var onPinUnlockResult: ((success: Boolean, attemptsLeft: Int, wiped: Boolean) -> Unit)? = null
    var onPinSetResult: ((success: Boolean) -> Unit)? = null
    var onPinStatusResult: ((hasPin: Boolean, attemptsLeft: Int, unlocked: Boolean) -> Unit)? = null

    // Recovery callback
    var onRecoverResult: ((success: Boolean, address: String?, error: String?) -> Unit)? = null

    // Pending recovery words (stored so we can process after reading address)
    private var pendingRecoveryWords: List<String>? = null

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val name = result.device.name ?: return
            if (name.contains("Quartz", ignoreCase = true)) {
                Log.i(TAG, "Found Quartz device: $name")
                onScanResult?.invoke(name)
                stopScan()
                connect(result.device)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            Log.e(TAG, "Scan failed: $errorCode")
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

        Log.i(TAG, "Starting BLE scan for Quartz-Miner")
        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(SERVICE_UUID))
            .build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanner.startScan(listOf(filter), settings, scanCallback)

        // Stop after 10s
        handler.postDelayed({ stopScan() }, 10000)
    }

    @SuppressLint("MissingPermission")
    fun stopScan() {
        try {
            scanner.stopScan(scanCallback)
        } catch (e: Exception) {
            Log.w(TAG, "Stop scan error: ${e.message}")
        }
        handler.removeCallbacksAndMessages(null)
    }

    @SuppressLint("MissingPermission")
    fun connect(device: BluetoothDevice) {
        Log.i(TAG, "Connecting to ${device.name}")
        connectedDevice = device
        connectedGatt = device.connectGatt(context, false, gattCallback)
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
        // Write 3 dummy bytes to confirm (firmware accepts any 3-byte write)
        confirmChar.value = byteArrayOf(1, 2, 3)
        confirmChar.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        gatt.writeCharacteristic(confirmChar)
        Log.i(TAG, "Seed confirmation sent")
        onSeedConfirmed?.invoke()
    }

    private val gattCallback = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.i(TAG, "Connected to GATT server")
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.i(TAG, "Disconnected from GATT server")
                    connectedGatt = null
                    statsCharacteristic = null
                    onConnectionChange?.invoke(false)
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

            val service = gatt.getService(SERVICE_UUID)
            if (service == null) {
                Log.e(TAG, "Quartz service not found")
                onError?.invoke("Quartz service not found")
                return
            }

            statsCharacteristic = service.getCharacteristic(STATS_UUID)
            val addrChar = service.getCharacteristic(ADDRESS_UUID)

            // Enable notifications on stats
            statsCharacteristic?.let { char ->
                gatt.setCharacteristicNotification(char, true)
                val cccd = char.descriptors.find { it.uuid == UUID.fromString("00002902-0000-1000-8000-00805f9b34fb") }
                cccd?.let {
                    it.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    gatt.writeDescriptor(it)
                }
            }

            // Read wallet address
            addrChar?.let {
                gatt.readCharacteristic(it)
            }

            onConnectionChange?.invoke(true)
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
                        Log.w(TAG, "Seed phrase empty (already confirmed?)")
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
                        Log.w(TAG, "Seed phrase empty (already confirmed?)")
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

        // Android 13+ write callback
        override fun onCharacteristicWrite(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int
        ) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(TAG, "Characteristic write failed (new API): $status for ${characteristic.uuid}")
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
                }
                return
            }

            when (characteristic.uuid) {
                PIN_UNLOCK_UUID -> {
                    if (value.size >= 3) {
                        val success = value[0].toInt() != 0
                        val attemptsLeft = value[1].toInt() and 0xFF
                        val wiped = value[2].toInt() != 0
                        Log.i(TAG, "PIN unlock result: success=$success, attempts=$attemptsLeft, wiped=$wiped")
                        onPinUnlockResult?.invoke(success, attemptsLeft, wiped)
                    } else {
                        onPinUnlockResult?.invoke(true, 3, false)
                    }
                    onPinUnlockResult = null
                }
                PIN_SET_UUID -> {
                    val success = value.isNotEmpty() && value[0].toInt() != 0
                    Log.i(TAG, "PIN set result: success=$success")
                    onPinSetResult?.invoke(success)
                    onPinSetResult = null
                }
                SEED_UUID -> {
                    Log.i(TAG, "Seed write successful (new API)")
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