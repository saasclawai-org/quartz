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

        // Restore user-configured node URL (Settings → Node URL)
        getSharedPreferences("quartz_wallet", Context.MODE_PRIVATE)
            .getString("node_url", null)?.let { SoftwareWallet.setNodeUrl(it) }

        // Request BLE permissions on startup
        if (!BLEPermissions.allGranted(this)) {
            permissionLauncher.launch(BLEPermissions.requiredPermissions())
        }

        setContent {
            QuartzTheme {
                QuartzWalletApp()
            }
        }
    }
}
