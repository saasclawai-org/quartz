package com.quartz.wallet.util

/**
 * Parses Quartz payment payloads scanned from QR codes.
 *
 * Supported:
 *   quartz:QszCPh9z...                     → address only
 *   quartz:QszCPh9z...?amount=5            → address + amount (QZ)
 *   quartz:QszCPh9z...?amount=5&label=Arcade%20Credit
 *   quartz://QszCPh9z...                    → leading // tolerated
 *   QszCPh9z...                             → raw address
 *
 * Pure Kotlin (no Android deps) so it stays unit-testable.
 * Does NOT do checksum validation — callers validate with
 * QuartzCrypto.isValidAddress() so the user gets the same
 * "Invalid address" error path as manual entry.
 */
data class PaymentUri(
    val address: String,
    val amountQz: Double?,
    val label: String?
)

object PaymentUriParser {
    // Base58check of a 25-byte payload is ~33-34 chars; 20-45 is a safe window
    private val BASE58 = Regex("^[1-9A-HJ-NP-Za-km-z]{20,45}$")

    fun parse(payload: String): PaymentUri? {
        val s = payload.trim()
        if (s.isEmpty()) return null

        var body = s
        val lower = s.lowercase()
        when {
            lower.startsWith("quartz://") -> body = s.substring(9)
            lower.startsWith("quartz:") -> body = s.substring(7)
        }

        val qIdx = body.indexOf('?')
        val addrPart = if (qIdx >= 0) body.substring(0, qIdx) else body
        val query = if (qIdx >= 0) body.substring(qIdx + 1) else null

        val address = addrPart.trim()
        if (!BASE58.matches(address)) return null

        var amount: Double? = null
        var label: String? = null
        query?.split('&')?.forEach { kv ->
            val eq = kv.indexOf('=')
            if (eq <= 0) return@forEach
            val key = kv.substring(0, eq).lowercase()
            val value = kv.substring(eq + 1)
            when (key) {
                "amount" -> value.toDoubleOrNull()?.let { d ->
                    if (d > 0 && d.isFinite()) amount = d
                }
                "label" -> label = urlDecode(value).takeIf { it.isNotEmpty() }
            }
        }
        return PaymentUri(address, amount, label)
    }

    private fun urlDecode(s: String): String = try {
        java.net.URLDecoder.decode(s, "UTF-8")
    } catch (e: Exception) {
        s
    }
}
