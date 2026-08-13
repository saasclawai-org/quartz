package com.quartz.wallet.ble

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.UUID

/**
 * Pure JVM unit tests for QuartzBLEManager companion-object UUIDs.
 *
 * No Android runtime required — these tests only touch [java.util.UUID]
 * and the string constants declared in the companion object.
 */
class QuartzBLEManagerTest {

    // The canonical Bluetooth Base UUID prefix/suffix
    private val baseSuffix = "-0000-1000-8000-00805f9b34fb"

    private fun expected(hex: String): UUID =
        UUID.fromString("$hex$baseSuffix")

    @Test
    fun pinSetUuid_isCorrect() {
        assertEquals(
            expected("00000a06"),
            QuartzBLEManager.PIN_SET_UUID
        )
    }

    @Test
    fun pinUnlockUuid_isCorrect() {
        assertEquals(
            expected("00000a07"),
            QuartzBLEManager.PIN_UNLOCK_UUID
        )
    }

    @Test
    fun pinStatusUuid_isCorrect() {
        assertEquals(
            expected("00000a08"),
            QuartzBLEManager.PIN_STATUS_UUID
        )
    }

    @Test
    fun seedUuid_isCorrect() {
        assertEquals(
            expected("00000a04"),
            QuartzBLEManager.SEED_UUID
        )
    }

    @Test
    fun confirmUuid_isCorrect() {
        assertEquals(
            expected("00000a05"),
            QuartzBLEManager.CONFIRM_UUID
        )
    }

    // ── Bonus: verify the service and other UUIDs are consistent ───────

    @Test
    fun serviceUuid_isCorrect() {
        assertEquals(
            expected("00000a01"),
            QuartzBLEManager.SERVICE_UUID
        )
    }

    @Test
    fun statsUuid_isCorrect() {
        assertEquals(
            expected("00000a02"),
            QuartzBLEManager.STATS_UUID
        )
    }

    @Test
    fun addressUuid_isCorrect() {
        assertEquals(
            expected("00000a03"),
            QuartzBLEManager.ADDRESS_UUID
        )
    }

    @Test
    fun allUuids_useBluetoothBaseUuid() {
        val all = listOf(
            QuartzBLEManager.SERVICE_UUID,
            QuartzBLEManager.STATS_UUID,
            QuartzBLEManager.ADDRESS_UUID,
            QuartzBLEManager.SEED_UUID,
            QuartzBLEManager.CONFIRM_UUID,
            QuartzBLEManager.PIN_SET_UUID,
            QuartzBLEManager.PIN_UNLOCK_UUID,
            QuartzBLEManager.PIN_STATUS_UUID
        )
        // Every UUID should end with the standard Bluetooth base suffix
        for (uuid in all) {
            val str = uuid.toString()
            assert(str.endsWith("00805f9b34fb")) {
                "$str does not use Bluetooth base UUID suffix"
            }
        }
    }

    @Test
    fun allUuids_areDistinct() {
        val all = listOf(
            QuartzBLEManager.SERVICE_UUID,
            QuartzBLEManager.STATS_UUID,
            QuartzBLEManager.ADDRESS_UUID,
            QuartzBLEManager.SEED_UUID,
            QuartzBLEManager.CONFIRM_UUID,
            QuartzBLEManager.PIN_SET_UUID,
            QuartzBLEManager.PIN_UNLOCK_UUID,
            QuartzBLEManager.PIN_STATUS_UUID
        )
        assertEquals(all.size, all.toSet().size)
    }
}