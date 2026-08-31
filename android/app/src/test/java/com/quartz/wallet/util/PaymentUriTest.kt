package com.quartz.wallet.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PaymentUriTest {

    // Not a checksum-valid address — parser only checks shape, callers validate
    private val addr = "QszCPh9z7VpXqYe8xwFN3mJRcTb2WdHkGsL"

    @Test
    fun `raw address`() {
        val p = PaymentUriParser.parse(addr)!!
        assertEquals(addr, p.address)
        assertNull(p.amountQz)
        assertNull(p.label)
    }

    @Test
    fun `quartz uri without params`() {
        val p = PaymentUriParser.parse("quartz:$addr")!!
        assertEquals(addr, p.address)
        assertNull(p.amountQz)
    }

    @Test
    fun `quartz uri with amount`() {
        val p = PaymentUriParser.parse("quartz:$addr?amount=5")!!
        assertEquals(addr, p.address)
        assertEquals(5.0, p.amountQz!!, 1e-9)
        assertNull(p.label)
    }

    @Test
    fun `quartz uri with amount and encoded label`() {
        val p = PaymentUriParser.parse("quartz:$addr?amount=0.5&label=Arcade%20Credit")!!
        assertEquals(addr, p.address)
        assertEquals(0.5, p.amountQz!!, 1e-9)
        assertEquals("Arcade Credit", p.label)
    }

    @Test
    fun `uppercase scheme accepted`() {
        val p = PaymentUriParser.parse("QUARTZ:$addr?amount=2")!!
        assertEquals(addr, p.address)
        assertEquals(2.0, p.amountQz!!, 1e-9)
    }

    @Test
    fun `double slash tolerated`() {
        val p = PaymentUriParser.parse("quartz://$addr")!!
        assertEquals(addr, p.address)
    }

    @Test
    fun `whitespace trimmed`() {
        val p = PaymentUriParser.parse("  quartz:$addr?amount=1.25  ")!!
        assertEquals(addr, p.address)
        assertEquals(1.25, p.amountQz!!, 1e-9)
    }

    @Test
    fun `garbage payloads rejected`() {
        assertNull(PaymentUriParser.parse("hello world"))
        assertNull(PaymentUriParser.parse("https://quartzchain.net"))
        assertNull(PaymentUriParser.parse(""))
        assertNull(PaymentUriParser.parse("   "))
        assertNull(PaymentUriParser.parse("quartz:"))
        assertNull(PaymentUriParser.parse("quartz:?amount=5"))
    }

    @Test
    fun `bad amount ignored but address kept`() {
        val p = PaymentUriParser.parse("quartz:$addr?amount=abc")!!
        assertEquals(addr, p.address)
        assertNull(p.amountQz)
        // zero/negative amounts are not payment requests
        val z = PaymentUriParser.parse("quartz:$addr?amount=0")!!
        assertNull(z.amountQz)
        val n = PaymentUriParser.parse("quartz:$addr?amount=-3")!!
        assertNull(n.amountQz)
    }

    @Test
    fun `unknown params ignored`() {
        val p = PaymentUriParser.parse("quartz:$addr?foo=bar&amount=3&baz")!!
        assertEquals(addr, p.address)
        assertEquals(3.0, p.amountQz!!, 1e-9)
    }
}
