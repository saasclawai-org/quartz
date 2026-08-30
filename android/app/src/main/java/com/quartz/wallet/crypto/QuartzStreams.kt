package com.quartz.wallet.crypto

/**
 * Payment streams — Kotlin port of the reference node
 * (reference-node/quartz/wallet.py streams + quantum_crypto.py WOTS+).
 *
 * Plain English: a stream is a never-ending list of fresh addresses that
 * all grow from one seed (one backup recovers everything, but the chain
 * can't link them). A lane is a list of future addresses you hand to a
 * payer or an accountant — addresses only, no keys: they can pay you (or
 * watch you), never rob you.
 *
 * Cross-validated against the Python reference — see QuartzStreamsTest.
 * If those vectors fail, the app and the chain have diverged.
 */
object QuartzStreams {

    private const val TOTAL_CHAINS = 67
    private const val CHAIN_LEN = 15
    private const val MERKLE_LEAVES = 256

    private fun le32(n: Int): ByteArray = byteArrayOf(
        (n and 0xff).toByte(),
        ((n ushr 8) and 0xff).toByte(),
        ((n ushr 16) and 0xff).toByte(),
        ((n ushr 24) and 0xff).toByte()
    )

    private fun concat(vararg arrays: ByteArray): ByteArray {
        val out = ByteArray(arrays.sumOf { it.size })
        var pos = 0
        for (a in arrays) {
            a.copyInto(out, pos)
            pos += a.size
        }
        return out
    }

    /** WOTS+ public key (w=4, 67 chains, 15 hashes each) from a leaf seed. */
    private fun wotsPubkey(kSeed: ByteArray): ByteArray {
        val pub = ByteArray(TOTAL_CHAINS * 32)
        for (c in 0 until TOTAL_CHAINS) {
            var v = QuartzCrypto.sha256(concat(kSeed, le32(c)))
            repeat(CHAIN_LEN) { v = QuartzCrypto.sha256(v) }
            v.copyInto(pub, c * 32)
        }
        return pub
    }

    /**
     * Merkle root (the on-chain identity) of the 256-leaf WOTS+ tree
     * grown from `seed`. Mirrors generate_quantum_address() in Python.
     */
    fun generateQuantumAddressRoot(seed: ByteArray): ByteArray {
        val leaves = Array(MERKLE_LEAVES) { k ->
            val kSeed = QuartzCrypto.sha256(concat(seed, le32(k)))
            QuartzCrypto.sha256(wotsPubkey(kSeed))
        }
        var cur = leaves
        while (cur.size > 1) {
            cur = Array(cur.size / 2) { i ->
                QuartzCrypto.sha256(concat(cur[i * 2], cur[i * 2 + 1]))
            }
        }
        return cur[0]
    }

    // ------------------------------------------------------------------
    // Address streams (StreamWallet on the node)
    // ------------------------------------------------------------------

    fun streamAccountSeed(seed: ByteArray, index: Int): ByteArray =
        QuartzCrypto.sha256(concat("qz/account".toByteArray(), seed, le32(index)))

    fun streamAddressAt(seed: ByteArray, index: Int): String =
        QuartzCrypto.publicKeyToAddress(
            generateQuantumAddressRoot(streamAccountSeed(seed, index)))

    // ------------------------------------------------------------------
    // Payment channels / lanes (PaymentChannel on the node)
    // ------------------------------------------------------------------

    /** Receiver-side: lane derived from YOUR wallet seed (only you can spend). */
    fun channelSeedFromWallet(seed: ByteArray, channelId: Int): ByteArray =
        QuartzCrypto.sha256(concat("qz/channel".toByteArray(), seed, le32(channelId)))

    fun channelStreamSeed(channelSeed: ByteArray, index: Int): ByteArray =
        QuartzCrypto.sha256(concat("qz/stream".toByteArray(), channelSeed, le32(index)))

    fun channelAddressAt(channelSeed: ByteArray, index: Int): String =
        QuartzCrypto.publicKeyToAddress(
            generateQuantumAddressRoot(channelStreamSeed(channelSeed, index)))

    /** Own devices / data commitments only — anyone holding the secret can spend. */
    fun sharedSecretChannelSeed(secret: ByteArray): ByteArray =
        QuartzCrypto.sha256(concat("qz/shared".toByteArray(), secret))

    /**
     * Bundle JSON (same format as the node's export_bundle) — the list of
     * the lane's first [count] addresses. Safe to hand to payers/auditors.
     */
    fun bundleJson(channelSeed: ByteArray, channelId: Int, count: Int, mainnet: Boolean = true): String {
        val sb = StringBuilder()
        sb.append("{\"v\":1,\"type\":\"quartz/payment-bundle\",\"channel\":").append(channelId)
        sb.append(",\"net\":\"").append(if (mainnet) "main" else "test").append('"')
        sb.append(",\"count\":").append(count).append(",\"addresses\":[")
        for (i in 0 until count) {
            if (i > 0) sb.append(',')
            sb.append('"').append(channelAddressAt(channelSeed, i)).append('"')
        }
        sb.append("]}")
        return sb.toString()
    }
}
