package com.quartz.wallet.util

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [Validation.isPinValid].
 *
 * Pure JVM — no Android or Compose dependencies.
 */
class PinValidationTest {

    // ── Valid PINs ──────────────────────────────────────────────────

    @Test
    fun fourDigitPin_isValid() {
        assertTrue(Validation.isPinValid("1234"))
    }

    @Test
    fun sixDigitPin_isValid() {
        assertTrue(Validation.isPinValid("123456"))
    }

    @Test
    fun eightDigitPin_isValid() {
        assertTrue(Validation.isPinValid("12345678"))
    }

    @Test
    fun allZerosPin_isValid() {
        assertTrue(Validation.isPinValid("0000"))
    }

    @Test
    fun allNinesPin_isValid() {
        assertTrue(Validation.isPinValid("99999999"))
    }

    // ── Invalid: wrong length ───────────────────────────────────────

    @Test
    fun threeDigitPin_isInvalid() {
        assertFalse(Validation.isPinValid("123"))
    }

    @Test
    fun nineDigitPin_isInvalid() {
        assertFalse(Validation.isPinValid("123456789"))
    }

    @Test
    fun oneDigitPin_isInvalid() {
        assertFalse(Validation.isPinValid("1"))
    }

    @Test
    fun twoDigitPin_isInvalid() {
        assertFalse(Validation.isPinValid("12"))
    }

    @Test
    fun tenDigitPin_isInvalid() {
        assertFalse(Validation.isPinValid("1234567890"))
    }

    // ── Invalid: non-digit characters ───────────────────────────────

    @Test
    fun pinWithLetters_isInvalid() {
        assertFalse(Validation.isPinValid("12ab"))
    }

    @Test
    fun pinWithAllLetters_isInvalid() {
        assertFalse(Validation.isPinValid("abcd"))
    }

    @Test
    fun pinWithSpecialCharacters_isInvalid() {
        assertFalse(Validation.isPinValid("12-4"))
    }

    @Test
    fun pinWithSpace_isInvalid() {
        assertFalse(Validation.isPinValid("12 4"))
    }

    @Test
    fun pinWithDecimalPoint_isInvalid() {
        assertFalse(Validation.isPinValid("12.4"))
    }

    // ── Invalid: empty ──────────────────────────────────────────────

    @Test
    fun emptyPin_isInvalid() {
        assertFalse(Validation.isPinValid(""))
    }

    @Test
    fun blankPin_isInvalid() {
        assertFalse(Validation.isPinValid("    "))
    }

    // ── Boundary checks ─────────────────────────────────────────────

    @Test
    fun exactlyFourDigits_isValid() {
        assertTrue(Validation.isPinValid("4567"))
    }

    @Test
    fun exactlyEightDigits_isValid() {
        assertTrue(Validation.isPinValid("87654321"))
    }

    @Test
    fun threeDigits_isInvalid_belowMinimum() {
        assertFalse(Validation.isPinValid("999"))
    }

    @Test
    fun nineDigits_isInvalid_aboveMaximum() {
        assertFalse(Validation.isPinValid("111111111"))
    }
}