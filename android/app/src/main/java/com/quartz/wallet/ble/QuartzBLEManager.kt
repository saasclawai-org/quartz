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
    var onConnectionChange: ((Boolean) -> Unit)? = null
    var onScanResult: ((String) -> Unit)? = null  // device name
    var onError: ((String) -> Unit)? = null

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
            if (characteristic.uuid == ADDRESS_UUID) {
                val addr = String(characteristic.value).trimEnd('\u0000')
                Log.i(TAG, "Wallet address: $addr")
                onAddressRead?.invoke(addr)
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
            if (characteristic.uuid == ADDRESS_UUID) {
                val addr = String(value).trimEnd('\u0000')
                Log.i(TAG, "Wallet address: $addr")
                onAddressRead?.invoke(addr)
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