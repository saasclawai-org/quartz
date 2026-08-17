# Quartz — Value Propositions

> **One-liner:** Quartz anchors digital value to verifiable physical computation — a proof-of-work chain mined and served by $5 microcontrollers, where every chip's unclonable hardware identity (PUF) turns the network into a marketplace of provably-real machines.

Status legend: **[LIVE]** running on the testnet today · **[BENCH]** built, awaiting hardware · **[ROADMAP]** next phases

---

## 1. Mine on a $5 board

**Claim:** Real proof-of-work, but the target hardware is a microcontroller on your desk — not an ASIC farm in a warehouse.

**Why it matters:** Bitcoin industrialized mining until individuals were priced out. Quartz inverts that: a $3 ESP32-C3 super-mini is a full node-participant. Energy per hash is milliwatts. Participation is the product.

**State today [LIVE]:** One C3 board is mining at 29 H/s — 65+ blocks found, 3,200+ QZ earned. v071 firmware stashes blocks found during WiFi outages and re-submits on reconnect; a miner never loses work to a network blip.

---

## 2. Hardware-attested compute — the moat

**Claim:** Every ESP32 carries a Physically Unclonable Function — a key derived from silicon irregularities that cannot be extracted or copied. Quartz uses it to sign compute receipts: proof that *this specific physical chip* ran *this model* on *this input* and produced *this output*.

**Receipt format:** `SHA256(puf_key ‖ input_hash ‖ output_hash ‖ model_hash)`

**Why it matters:** Software claiming "I ran the model" is free to fake. A receipt bound to unclonable silicon is the strongest attestation a $5 device can give. This is the foundation for buying compute you can actually trust — and it's the thing no GPU-chain or cloud API can copy at any price.

**State today [LIVE]:** Inference marketplace endpoints on the testnet — request compute (1 QZ escrowed), post attested result, provider paid. The full loop has run end-to-end. ML demo firmware with the receipt format is built for all three chip targets. **[BENCH]** Real-hardware attestation: second board flashed with the demo registers as an inference node.

**Honest limit:** v1 attestation trusts the device's own PUF derivation. Trustless third-party PUF verification is a declared v2 research track — we say so rather than pretend.

---

## 3. Privacy at the network layer, not the identity layer

**Claim:** PUF identity is durable — you can't rotate your chip's identity the way you rotate a keypair. So anonymity can't come from identity games; it must come from the transport. Radio means no IP address exists to correlate.

**State today:**
- **[LIVE]** Tor hidden service fronting the reference node — IP-layer privacy for standard clients now.
- **[BENCH]** ESP-NOW radio transport firmware (published; two-board bench in progress). Point-to-point, no infrastructure, no IP.
- **[ROADMAP]** LoRa (SX1262 915 MHz) long-range transport — boards already in hand.

**Why it matters:** Every other "private" chain plays identity games that PUF hardware makes impossible. Quartz accepts durable identity and moves privacy to where hardware can't betray you: the network itself.

---

## 4. Utility-first token

**Claim:** QZ is payment for verifiable services — attested inference, sensor data feeds, relay bandwidth — not a speculation vehicle.

**Why it matters:** Tokens whose only utility is resale depend on the next buyer. Tokens that buy something *provable* have a floor set by the service's value.

**Mechanics:** 42 QZ per block, halving every 500K blocks, 30-second target block time, 10% relayer share reserved (activates at the mesh fork — deferred, not abandoned). Emission rules are in the whitepaper and match the running chain.

**State today [LIVE]:** The escrow→attest→pay loop exists and has been exercised. Wallets (Android app + PWA) hold real keys; the first consumer transactions are confirmed on-chain.

---

## 5. A node anyone can actually run

**Claim:** The Bitcoin lesson, learned before launch: infrastructure before incentives. Running a Quartz node is a 15-minute, one-tarball job on a Raspberry Pi.

**State today [LIVE]:** `quartz-pi-node.tar.gz` — install script, systemd unit, snapshot sync from the reference node. Standby mode (`QUARTZ_NO_MINER=1`) is baked in so a second node *cannot* accidentally fork the chain.

**[ROADMAP] sequencing:** easy self-hosted nodes first → dockerized one-liners → pay-per-service (signed templates, 90/10 split) only at the mesh fork → bonded registration for Sybil resistance when real money is at stake.

---

## 6. An honest chain

**Claim:** No mystery miners, no inflated claims, no hidden simulator.

**Why it matters:** Trust in a new chain starts with the operator not bluffing.

**State today [LIVE]:**
- The testnet simulator is labeled (`heltec-001…` demo identities), per-miner real hashrates are public, and the whole simulation is one environment variable from off.
- Real crypto end-to-end: BIP39 + SLIP-0010 derivation, Ed25519 signatures, UTXO accounting — verified by actual consumer transactions this week.
- Engineering hygiene: atomic chain persistence, PIN-gated wallet with PBKDF2 + lockout, rate limiting, fail2ban/UFW-hardened node.

---

## What we deliberately do **not** promise

- **No mainnet until 20–50+ independent, real miners.** Launching earlier would be a centralized chain wearing a costume.
- **No anonymity beyond what the transport delivers.** Today that's IP-layer (Tor). Radio-layer privacy is bench-stage hardware, not a shipping feature.
- **No TPS marketing.** The bench firmware *measures* and prints real throughput ceilings (e.g., a 100KB block over point-to-point ESP-NOW ≈ tens of seconds) instead of quoting theoretical numbers.
- **The testnet distribution is simulated** — demo wallets hold most of the current supply. Tokenomics describe the *rules*, not the current spread.

---

## Sequencing

```
testnet + simulator (now)
  → real miners: C3 + Heltec S3s + Pi nodes [20–50 target]
    → radio mesh fork: ESP-NOW/LoRa transport, relayer share activates
      → mainnet
```

Each gate is measurable hardware on desks, not dates on a calendar.
