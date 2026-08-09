package com.quartz.wallet

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.quartz.wallet.ui.QuartzWalletApp
import com.quartz.wallet.ui.theme.QuartzTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            QuartzTheme {
                QuartzWalletApp()
            }
        }
    }
}
