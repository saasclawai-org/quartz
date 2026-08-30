package com.quartz.wallet

import android.Manifest
import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.*
import androidx.compose.runtime.*
import com.quartz.wallet.ble.BLEPermissions
import com.quartz.wallet.wallet.SoftwareWallet
import com.quartz.wallet.ui.QuartzWalletApp
import com.quartz.wallet.ui.theme.QuartzTheme

class MainActivity : ComponentActivity() {
    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { result ->
        // Permissions granted or denied — app continues regardless
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Restore user-configured node URL (Settings → Node URL).
        // v0.2.9: lives in plain "quartz_settings" — a plain handle writing into the
        // encrypted wallet file corrupted EncryptedSharedPreferences. Legacy fallback once.
        val savedUrl = getSharedPreferences("quartz_settings", Context.MODE_PRIVATE)
            .getString("node_url", null)
            ?: getSharedPreferences("quartz_wallet", Context.MODE_PRIVATE)
                .getString("node_url", null)
        savedUrl?.let { SoftwareWallet.setNodeUrl(it) }

        // Request BLE permissions on startup
        if (!BLEPermissions.allGranted(this)) {
            permissionLauncher.launch(BLEPermissions.requiredPermissions())
        }

        // v0.2.10: if the previous run crashed, show the recorded stack trace
        val crashFile = java.io.File(filesDir, "last_crash.txt")
        var lastCrash by androidx.compose.runtime.mutableStateOf(
            crashFile.takeIf { it.exists() }?.readText()
        )

        setContent {
            QuartzTheme {
                QuartzWalletApp()
                lastCrash?.let { trace ->
                    AlertDialog(
                        onDismissRequest = {},
                        title = { Text("Previous run crashed") },
                        text = { Text(trace.take(2000)) },
                        confirmButton = {
                            TextButton(onClick = {
                                @Suppress("DEPRECATION")
                                val cm = getSystemService(Context.CLIPBOARD_SERVICE)
                                        as android.content.ClipboardManager
                                cm.setPrimaryClip(android.content.ClipData.newPlainText("crash", trace))
                                android.widget.Toast.makeText(
                                    this, "Copied — send it to me",
                                    android.widget.Toast.LENGTH_SHORT).show()
                            }) { Text("Copy") }
                        },
                        dismissButton = {
                            TextButton(onClick = {
                                crashFile.delete()
                                lastCrash = null
                            }) { Text("Dismiss") }
                        }
                    )
                }
            }
        }
    }
}
