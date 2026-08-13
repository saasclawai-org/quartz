package com.quartz.wallet.util

/**
 * Pure validation logic for the Quartz Wallet app.
 *
 * These functions are intentionally free of Android/Compose dependencies
 * so they can be unit-tested on a plain JVM with JUnit.
 */
object Validation {

    /** PIN must be 4–8 digits (numbers only). */
    fun isPinValid(pin: String): Boolean {
        return pin.length in 4..8 && pin.all { it.isDigit() }
    }

    /** Recovery form is valid when exactly 12 non-blank, letters-only words are provided. */
    fun isRecoveryFormValid(words: List<String>): Boolean {
        return words.size == 12 && words.all { it.isNotBlank() && it.all { c -> c.isLetter() } }
    }
}