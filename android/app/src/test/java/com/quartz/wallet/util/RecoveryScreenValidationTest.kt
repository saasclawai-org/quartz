package com.quartz.wallet.util

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [Validation.isRecoveryFormValid].
 *
 * Pure JVM — no Android or Compose dependencies.
 */
class RecoveryScreenValidationTest {

    private val validWords = listOf(
        "abandon", "ability", "able", "about", "above", "absent",
        "absorb", "abstract", "absurd", "abuse", "access", "accident"
    )

    // ── Happy path ──────────────────────────────────────────────────

    @Test
    fun allTwelveWordsFilled_formIsValid() {
        assertTrue(Validation.isRecoveryFormValid(validWords))
    }

    // ── Too few words ───────────────────────────────────────────────

    @Test
    fun onlyElevenWords_formIsInvalid() {
        val eleven = validWords.take(11)
        assertFalse(Validation.isRecoveryFormValid(eleven))
    }

    @Test
    fun emptyList_formIsInvalid() {
        assertFalse(Validation.isRecoveryFormValid(emptyList()))
    }

    @Test
    fun tooManyWords_formIsInvalid() {
        val thirteen = validWords + "extra"
        assertFalse(Validation.isRecoveryFormValid(thirteen))
    }

    // ── Blank / empty entries ───────────────────────────────────────

    @Test
    fun emptyWordInMiddle_formIsInvalid() {
        val words = validWords.toMutableList()
        words[5] = ""
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    @Test
    fun blankWordInMiddle_formIsInvalid() {
        val words = validWords.toMutableList()
        words[5] = "   "
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    @Test
    fun lastWordEmpty_formIsInvalid() {
        val words = validWords.toMutableList()
        words[11] = ""
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    // ── Words with numbers ──────────────────────────────────────────

    @Test
    fun wordWithNumbers_formIsInvalid() {
        val words = validWords.toMutableList()
        words[3] = "ab0ut"
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    @Test
    fun wordThatIsOnlyNumbers_formIsInvalid() {
        val words = validWords.toMutableList()
        words[0] = "12345"
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    // ── All same word ───────────────────────────────────────────────

    @Test
    fun allSameWord_formIsValid() {
        // The UI validation only checks format (12 non-blank, letters-only words).
        // Whether the seed is a valid BIP39 mnemonic is checked at the node/firmware level.
        val words = List(12) { "abandon" }
        assertTrue(Validation.isRecoveryFormValid(words))
    }

    // ── Mixed case (should still be valid — letters only) ───────────

    @Test
    fun mixedCaseLetters_formIsValid() {
        val words = validWords.mapIndexed { i, w ->
            if (i % 2 == 0) w.uppercase() else w
        }
        assertTrue(Validation.isRecoveryFormValid(words))
    }

    // ── Words with special characters ───────────────────────────────

    @Test
    fun wordWithHyphen_formIsInvalid() {
        val words = validWords.toMutableList()
        words[2] = "ab-out"
        assertFalse(Validation.isRecoveryFormValid(words))
    }

    @Test
    fun wordWithSpace_formIsInvalid() {
        val words = validWords.toMutableList()
        words[7] = "ab stract"
        assertFalse(Validation.isRecoveryFormValid(words))
    }
}