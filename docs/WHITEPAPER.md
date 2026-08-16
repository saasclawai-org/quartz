# Quartz (QZ)

### A quantum-safe, hardware-bound cryptocurrency for ESP32 microcontrollers

---

## Abstract

Quartz is a decentralized cryptocurrency designed to be mined exclusively on ESP32-series microcontrollers. Unlike conventional cryptocurrencies that favor specialized hardware (ASICs, GPUs), Quartz uses SRAM PUF attestation that binds every block to a physical ESP32 chip — making it impossible to mine on any other platform. Wallets use WOTS+ hash-based signatures, providing quantum resistance against future cryptanalytic attacks. The ultra-low power requirement (1.2W) enables off-grid mining using solar panels and small wind turbines with LiFePO4 battery storage — creating the first cryptocurrency that can be mined entirely off-grid, with zero electricity cost.

## Motivation

Existing "IoT-minable" coins (Duino-Coin, etc.) are either centralized or trivially mineable on GPUs/servers, defeating the purpose of distributed mining. Quartz solves this by binding the PoW algorithm to physical ESP32 hardware properties that cannot be emulated efficiently.

## Technical Design

### PoW Algorithm: SHA-256 + PUF Attestation

Quartz uses standard SHA-256 Proof-of-Work (same as Bitcoin) but requires every block to include a PUF attestation response that can only be produced by a physical ESP32 chip. The PoW hash itself is GPU-computable, but the PUF attestation is not — making GPU mining unprofitable regardless of hashrate advantage.

#### Algorithm

```
Mining loop (per nonce):
  1. block_hash = SHA-256(header || nonce)
  2. IF block_hash < difficulty_target:
  3.   puf_response = SHA-256(header || puf_key || nonce || challenge_salt)
  4.   Submit block with puf_response as attestation

Block validation (node-side):
  1. Verify SHA-256(header || nonce) < difficulty_target  [PoW check]
  2. Verify puf_response matches expected PUF fingerprint   [hardware check]
  3. Verify device is registered and not slashed              [registry check]
```

#### Why GPUs Don't Help

| Setup | Hash Rate | Can Produce Valid Blocks? |
|-------|-----------|--------------------------|
| 1 ESP32 | ~28 H/s | ✅ Yes (has PUF key) |
| GPU farm (100 MH/s) | 3.5M× faster | ❌ No (no PUF key) |
| GPU + 1 ESP32 (key extracted) | 3.5M× faster | ✅ But only for 1 device's share |
| GPU + 10,001 ESP32s | 10,001× hardware cost | Same as 10,001 honest ESP32s |

A GPU adds **zero advantage** without physical ESP32 chips. With enough chips, the GPU is unnecessary — you'd mine honestly on the chips themselves.

### Block Structure

Simplified Bitcoin-like blocks, sized for ESP32 constraints:

| Field | Size | Notes |
|-------|------|-------|
| Version | 4 bytes | Protocol version |
| Previous Block Hash | 32 bytes | SHA-256 |
| Merkle Root | 32 bytes | Root of tx hashes |
| Timestamp | 4 bytes | Unix timestamp |
| Difficulty Target | 4 bytes | Compact difficulty |
| Nonce | 8 bytes | Mining nonce |
| Miner ID | 32 bytes | ESP32 chip ID (MAC-based) |
| Transaction Count | 1 byte | Max 255 tx per block |
| Transactions | Variable | See tx structure |

**Block size limit:** 4KB (ESP32-friendly)
**Target block time:** 120 seconds (2 minutes)
**Difficulty retarget:** Every 144 blocks (~48 hours)

### Transaction Structure

UTXO-based, Bitcoin-compatible:

| Field | Size | Notes |
|-------|------|-------|
| Version | 1 byte | Tx format version |
| Input Count | 1 byte | |
| Inputs | Variable | prev_tx_hash(32) + output_index(1) + signature(64) + pubkey(32) |
| Output Count | 1 byte | |
| Outputs | Variable | amount(8) + script(32) |
| Locktime | 4 bytes | |

**Supply:** 42,000,000 QZ (42 million)
**Block reward:** 42 QZ initially to miner, halving every 500,000 blocks (~19 months) — total supply lands exactly on 42M
**Smallest unit:** 1 qz-sat = 10⁻⁸ QZ
**Dev fund:** None (0%). 100% of block rewards go to miners. Pure fair launch.
**Founder timelock:** All coins mined by the project founder are locked via CLTV for 2 years (525,600 blocks). Consensus-enforced, no override.
**Early adopter bonus:** First 1,000 unique ESP32 miners get 2x reward for first 30 days

### Networking

Quartz uses a three-layer radio stack — each layer serves a different purpose:

1. **WiFi (primary)** — TCP connections to known peers for full block sync, node API, and broadcasting large data. ESP32 acts as both client and server. Requires local network or internet.

2. **BLE (phone pairing)** — Phone wallet connects to ESP32 for balance checking, transaction construction, and signing delegation. Range: ~10m. Encrypted + bonded.

3. **LoRa (mesh gossip)** — Long-range (2-15km) device-to-device mesh for block header propagation and transaction relay. Works without internet, WiFi, or cellular. Ideal for rural areas, developing nations, and infrastructure resilience.

#### LoRa Mesh Protocol

- **Frequency**: Region-specific (EU 868, US 915, AS 923, CN 470, IN 865 MHz)
- **Modulation**: LoRa CSS (Chirp Spread Spectrum) — immune to interference
- **Packet size**: Up to 242 bytes (fits a full block header + mesh routing header)
- **Gossip**: Flooding broadcast with TTL decrement (max 7 hops)
- **Store-and-forward**: Each node caches last 16 packets for late-joining peers
- **Beacon**: Every 60 seconds, each node broadcasts its presence + chain height
- **Block propagation**: New block headers (80 bytes) broadcast in one LoRa packet
- **Transaction relay**: Signed transactions (158 bytes) relayed through mesh
- **Duty cycle compliance**: Respects regional regulations (EU: 1% duty cycle on 868 MHz)

#### Supported LoRa Boards

| Board | Chip | Display | LoRa | Price |
|-------|------|---------|------|-------|
| Heltec WiFi LoRa 32 V3 | ESP32-S3 | 0.96" OLED | SX1262 | ~$15 |
| TTGO LoRa32 V2.1 | ESP32 | 0.96" OLED | SX1276 | ~$12 |
| LilyGO T-Beam | ESP32 | — | SX1276 + GPS | ~$25 |
| LilyGO T3 S3 | ESP32-S3 | — | SX1262 | ~$18 |

The Heltec WiFi LoRa 32 V3 is the recommended board for Quartz — it has all three radios (WiFi, BLE, LoRa) plus a display for hardware wallet UI, all for ~$15.
3. **Gateway bridge** — An ESP32 connected to WiFi can relay blocks to a server-side node for block explorer / wallet access.

**Protocol:** Custom, message-based over TCP:
- `VERSION` / `VERACK` — handshake
- `GET_BLOCKS` / `BLOCKS` — chain sync
- `GET_MEMPOOL` / `MEMPOOL` — tx relay
- `SUBMIT_BLOCK` — new block announcement
- `PING` / `PONG` — keepalive

### Node Operation Modes

ESP32 nodes operate in one of three modes:

1. **Full miner** — Mines blocks, validates transactions, stores chain headers + recent blocks. Requires PSRAM for chain storage (WROVER modules).

2. **Light miner** — Mines blocks only. Relies on peers for validation. Works on any ESP32 (even WROOM with 4MB flash).

3. **SPV node** — Only stores block headers (80 bytes each). Can verify transactions via merkle proofs. For wallet/explorer use.

### Chain Storage

- **Headers-only by default:** 80 bytes × N blocks fits easily in flash
- **Pruned mode:** Store last 1000 blocks + headers
- **WROVER (PSRAM):** Can store full chain in external RAM
- **Flash partition:** Custom partition table carves out ~2MB for blockchain data

## Tokenomics

| Parameter | Value |
|-----------|-------|
| Ticker | QZ |
| Total Supply | 42,000,000 QZ (exact by schedule: 42 × 500,000 × 2) |
| Dev Fund | **0%** — killed, pure fair launch |
| Premine | 0 |
| Founder Timelock | 2-year CLTV on all founder-mined coins |
| PUF Block Reward (Era 1) | 42 QZ |
| Block Reward (PUF-required) | 42 QZ |
| Miner Share | **100% of every block** (pre-mesh-fork) |
| Mesh Relayer Pool | 10% — design preserved, **deferred to a future hard fork** |
| Quantum Security Pool | **0% — dropped** (see Emission Decision) |
| Halving Period | 500,000 blocks (~19 months at 2min blocks) |
| Block Time | 120 seconds |
| Difficulty Retarget | 144 blocks (~48 hours) |
| PUF Required | **Block 1. No exceptions.** |
| Smallest Unit | 0.00000001 QZ (1 quartz-sat) |
| Early Adopter Bonus | Removed — violated coinbase validation |
| Bug Bounty | Community-funded |

### Token Distribution

- **No premine** — all QZ is mined through proof-of-work
- **No dev fund** — 100% of block rewards go to miners
- **No ICO** — no token sale, no fundraising
- **No founder allocation** — founder mines like everyone else

### Founder Timelock

The founder's device pubkey is registered at genesis. When the founder mines a block, the coinbase output uses OP_CHECKLOCKTIMEVERIFY:

```
OP_CHECKLOCKTIMEVERIFY <block_height + 525600> OP_DROP
<founder_pubkey> OP_CHECKSIG
```

Each block's reward unlocks exactly 2 years after it was mined. No admin key, no multi-sig override, no emergency unlock. Anyone can verify on the block explorer.

This is strictly stronger than Bitcoin's model. Satoshi made a social promise not to move his ~1M BTC. Quartz makes it a mathematical impossibility.

### Early Adopter Bonus

**Removed.** The originally-planned 2x bonus for the first 1,000 PUF devices paid above the coinbase schedule, which the consensus engine correctly rejects. Fair launch means one schedule for everyone, from block 1.

### Emission Decision (2026-08-15)

Decided on testnet, carried into mainnet genesis:

1. **Base emission is 42.5 QZ/block** (Era 1), halving every 210,000 blocks. The original 50 QZ schedule never minted 50 — the relayer and quantum shares had no recipients — so the schedule now states what the chain actually pays. Verified: no block in the first 20,194 ever paid above it. *(Superseded next day by the Supply Retune — see below.)*
2. **The Quantum Security Pool is dropped.** A protocol tax minting to an address with no spender is dead supply; a founder-governed fund contradicts the 0%-dev-fund fair launch. Quantum readiness (WOTS+ migration) remains an engineering commitment — funded by transparent donations and audits, not by emission.
3. **The Mesh Relayer Pool is preserved in design, deferred in payment.** It activates only by hard fork, only when a real LoRa mesh fleet carries live traffic and relay-share accounting is validated in-block. Paying it before that would mint to nothing; designing it now keeps the off-grid roadmap honest.

### Supply Retune (2026-08-16)

The day after the emission decision, base emission was set to **42 QZ/block with a
500,000-block halving interval**, making the total supply land **exactly on
42,000,000 QZ** (42 × 500,000 × 2). Rationale:

- The interim 42.5/210k schedule summed to ~17.85M — the paper's 42M figure
  had never matched any schedule. This was the last paper-vs-chain drift.
- 42 per block and 42M total is a 1.2% emission trim from 42.5, not an
  expansion — cleanup, not a raise.
- Decided before the first halving (no historical halving crossed), so the
  schedule change rewrites nothing: blocks 0 to ~20,250 minted 42.5 under the
  interim schedule and are grandfathered as loaded.

At the future mesh fork, the split becomes 90% miner / 10% relayer of the
42 QZ base (37.8 / 4.2).

### Quantum Mining Eras

Quartz requires PUF attestation from block 1. There are no non-PUF blocks. The era structure only governs reward halving:

| Era | Block Range | Block Reward | Approx Years |
|-----|-----------|-------------|-------------|
| 1 | 0 – 500,000 | 42 QZ | 0–1.9 |
| 2 | 500,001 – 1,000,000 | 21 QZ | 1.9–3.8 |
| 3 | 1,000,001 – 1,500,000 | 10.5 QZ | 3.8–5.7 |
| 4 | 1,500,001 – 2,000,000 | 5.25 QZ | 5.7–7.6 |
| 5+ | ... | halves | ... |

Every block in every era requires a valid PUF attestation from a physical ESP32 chip. No exceptions, no grace period, no non-PUF path.

### Reward Split

```
Every block (42 QZ, Era 1) — pre-mesh-fork:
└── Miner:          42 QZ  (100%)

At the future mesh fork (activation height set by that fork):
├── Miner:          90% of block reward
└── Mesh Relayers:  10% of block reward (in-block-validated relay shares)
```

All blocks require PUF attestation. There is no non-PUF reward tier, no early-adopter bonus, and no quantum pool.

### Mesh Relayer Pool (10%)

**Status: design preserved, payment deferred** until a hard fork activates it (see Emission Decision). When active, every block splits 10% of its reward to LoRa mesh relayers. The pool coordinator (elected every 16 blocks) distributes shares proportionally to nodes that relayed the most block headers and transactions during that epoch, with relay shares validated in-block. This incentivizes infrastructure without requiring mining hardware:

- $4 ESP32 + $6 LoRa module = passive relay income
- No mining, just block/tx propagation
- Reward proportional to relay work (share-based)
- Re-uses the mesh pool coordinator election mechanism

### Quantum Security Pool (5%)

**Status: dropped from the protocol (2026-08-15).** The originally planned five-percent tax would have minted to an unspendable pool address on a network with no pool infrastructure — or to a founder-governed fund, which contradicts the 0%-dev-fund fair launch. Quantum readiness is pursued off-chain:

- **WOTS+ key rotation subsidies** — helps active miners afford address rotation
- **Third-party security audits** — NIST and academic researchers
- **Future signature upgrades** — if a stronger hash-based scheme emerges

Funded, if at all, by transparent community donations — never by protocol emission.

### Token Distribution

- **No premine** — all QZ is mined through proof-of-work
- **No dev fund** — 0% to developers, founders, or investors
- **No ICO** — no token sale, no fundraising
- **No founder allocation** — founder mines like everyone else
- **100% of rewards** go to miners (relayers, once the mesh fork activates)

## Wallet & Key Management

### Cryptographic Keys
- **Signature algorithm**: Ed25519 (RFC 8032)
- **Key derivation**: BIP39 mnemonic → PBKDF2-HMAC-SHA512 (2048 iterations) → SLIP-0010 `m/44'/789'/0'/0'/0'`
- **Address format**: Base58(0x3B || SHA-256(pubkey)[:20] || SHA-256(SHA-256(payload))[:4])
- **Coin type**: 789 (BIP44 registered identifier)
- **Divisibility**: 1 QZ = 100,000,000 quartz-sats (8 decimals)

### Hardware Wallet Model (Primary)
The ESP32 miner serves as a hardware wallet — no separate device needed.

**Key lifecycle:**
1. On first boot, the ESP32 generates an Ed25519 keypair using its hardware RNG
2. The private key is stored in encrypted NVS flash (AES-256, secure boot protected)
3. A BIP39 12-word seed phrase is derived from the private key
4. The seed phrase is displayed **on the ESP32 screen** (requires T-Display S3 or similar)
5. User writes down the seed phrase and presses the button to confirm
6. The seed phrase is wiped from RAM — it exists only on paper
7. The private key never leaves the device through any interface

**Signing protocol:**
1. Phone constructs unsigned transaction and sends tx hash via BLE
2. ESP32 displays transaction details (amount, recipient) on screen
3. User presses physical button to approve (or holds to reject)
4. ESP32 signs the hash with the on-device private key
5. Only the 64-byte signature is returned via BLE notify
6. Phone broadcasts the signed transaction to the network

**Supported display boards:**
- LilyGO T-Display S3 (recommended) — 1.14" TFT 240×135, ESP32-S3, ~$12
- M5Stack Core2 — 2.0" TFT 320×240, ESP32, ~$35
- Any ESP32-S3 with ILI9341/ST7789 display
- Plain ESP32 (headless mode — uses phone screen for confirmation)

### Recovery
- Enter 12-word seed phrase in any Quartz wallet (PWA, Android, new ESP32)
- BIP39 checksum catches transcription errors
- Same Ed25519 keypair is deterministically derived
- Coins on the blockchain are accessible immediately

## Security Model: Why PUF-Only Is Sufficient

### The Central Question

If Quartz has value and uses standard hashing, someone ports the miner to CUDA and dominates the network overnight. One GPU = 3.5 million ESP32s. How does Quartz defend?

### The Moat: Physical Hardware Requirement

PUF attestation forces every miner to own a physical ESP32 chip. An attacker cannot spin up a GPU farm — they need real devices with real silicon to generate valid PUF responses.

**Attack cost scales linearly with network size:**

| Network Size | Honest Hashrate | Attack Cost (ESP32s) | Attack Cost (USD) |
|-------------|----------------|--------------------|--------------------|
| 100 devices | 2.8 KH/s | 101 × $5 | $505 |
| 1,000 devices | 28 KH/s | 1,001 × $5 | $5,005 |
| 10,000 devices | 280 KH/s | 10,001 × $5 | $50,005 |
| 100,000 devices | 2.8 MH/s | 100,001 × $5 | $500,005 |

The attack is always proportional to the network. This is the same model Bitcoin uses, except Bitcoin's cost is electricity (ASICs) and Quartz's cost is silicon (ESP32s).

### Why GPU Attacks Don't Work

A GPU can hash billions of times faster than an ESP32. But every valid block must include a PUF attestation response:

```
challenge = SHA-256(block_header || puf_key || nonce || challenge_salt)
```

The `puf_key` only exists in powered RTC SRAM on a specific physical chip after a cold boot. A GPU has no PUF key to include.

**Could an attacker extract the PUF key from one chip and use it on a GPU?**

Yes, via decapping + electron microscopy (~$10,000 per chip). But they get ONE miner's hashrate (28 H/s). The ESP32 they destroyed is worth $5. They spent $10,000 to match one $5 device. Not economically rational.

### Why Sensor-Based Mining Is Unnecessary

A proposed enhancement was mixing on-chip sensor readings (ADC noise, hall effect, temperature) into each hash attempt. Analysis showed this provides no additional security:

1. **Sensors can be faked** — an attacker reads sensor data from one $5 ESP32 and feeds it to a GPU
2. **One chip serves unlimited nonces** — a single "sensor oracle" provides authentic readings for GPU computation
3. **Verification breaks down** — other nodes can't reproduce exact sensor readings, making block verification fuzzy
4. **PUF already solves this** — the attacker needs physical hardware regardless

The hardware requirement IS the moat. Not "GPUs can't hash fast enough" — it's "you need N physical chips to match N miners."

### Why No Fallback (The Coldcard Lesson)

Coldcard hardware wallets lost $130M+ because a firmware bug silently switched from hardware TRNG to a software PRNG. The lesson: any fallback path creates a downgrade attack surface.

Quartz applies this to PUF:

- **Warm boot** (reset/watchdog) → RTC SRAM is not fresh → **HALT**
- **PUF reconstruction fails** → **HALT**
- **No enrollment data** → **HALT**
- **No MAC-derived fallback key** — the function was deleted
- **No software path to a weak key** — period

The only recovery is physical power-cycle. An attacker cannot trigger a fallback because there isn't one.

### Security Layers Summary

```
┌─────────────────────────────────────────────────────────────┐
│                     QUARTZ SECURITY MODEL                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Layer 1: RTC SRAM PUF (silicon entropy)                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Cold boot reads genuine power-on SRAM state            │ │
│  │ • Unique per chip, unclonable, not stored anywhere       │ │
│  │ • Fuzzy extractor handles temp/voltage drift (1-bit EC)  │ │
│  │ • Warm boot → REFUSE (no fresh entropy)                  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 2: No Fallback (Coldcard lesson)                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • No MAC-derived key fallback                             │ │
│  │ • No warm boot reconstruction                            │ │
│  │ • Device halts if PUF fails                              │ │
│  │ • Only recovery: physical power-cycle                    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 3: Economic Moat (hardware requirement)               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Attacker needs N physical ESP32 chips to match N miners │ │
│  │ • GPU adds zero advantage (PUF attestation required)      │ │
│  │ • Decapping one chip ($10K) = one miner's hashrate (28 H/s)│ │
│  │ • Attack cost scales linearly with network size           │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 4: Protocol Defenses (consensus rules)                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Checkpoints prevent deep reorgs                         │ │
│  │ • ±25% difficulty cap prevents manipulation               │ │
│  │ • Empty block penalty after 10 consecutive                │ │
│  │ • Slashing for double-signing                             │ │
│  │ • Timing analysis bans super-fast blocks                  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 5: Quantum Resistance (WOTS+)                         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Hash-based signatures (SHA-256 only)                    │ │
│  │ • Immune to Shor's algorithm                              │ │
│  │ • 2,404-byte signatures, 256 uses per address             │ │
│  │ • Key rotation warning at signature #240                   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 6: Supply Chain (birth certificates)                 │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Firmware hash recorded at first boot                    │ │
│  │ • Reseller pre-flash attack detection                     │ │
│  │ • Device registration prevents cloning                    │ │
│  │ • Buyer verifies birth certificate on phone app           │ │
│  └─────────────────────────────────────────────────────────┘ │
│                          ↓                                    │
│  Layer 7: Production Hardening (eFuses)                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ • Flash encryption (AES-256)                              │ │
│  │ • Secure boot (signed firmware only)                      │ │
│  │ • eFuse key burn is IRREVERSIBLE                          │ │
│  │ • User self-provisioning (decentralized, no central key)   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
└─────────────────────────────────────────────────────────────┘

Attack Cost vs Network Value:

  Network Value    │ Attack Cost    │ Ratio    │ Rational?
  ─────────────────┼────────────────┼──────────┼──────────
  $4,200 (100 dev) │ $505           │ 12%      │ Maybe
  $42K (1K dev)    │ $5,005         │ 12%      │ Maybe
  $420K (10K dev)  │ $50,005        │ 12%      │ No (too visible)
  $4.2M (100K dev) │ $500,005       │ 12%      │ No (logistics)

The ratio stays constant (~12%) but absolute cost and logistics
make attacks impractical at scale. Acquiring 10,000 ESP32 chips
is a supply chain problem, not a computing problem.
```

## Decentralized Mining Pools

Bitcoin forces miners into centralized pools (Slush, Foundry, AntPool) because individual block discovery is too rare for stable income. These pools charge 1-2.5% fees, create censorship chokepoints, and concentrate hashpower under operators who can censor transactions.

Quartz replaces this model with **mesh-native mining pools** — decentralized, serverless pools formed automatically by LoRa neighbors.

### How Mesh Pools Work

**Discovery:** ESP32 miners broadcast LoRa beacons every 60 seconds. Nearby miners hear beacons and discover local cluster members. No internet needed.

**Coordinator Election:** Every 16 blocks (~32 minutes), cluster members deterministically elect a coordinator using a verifiable hash election: the candidate with the lowest `SHA-256(pubkey || epoch_number)` wins. All members compute the same result independently — no voting, no communication needed for the election itself.

**Work Distribution:** The coordinator assigns nonce ranges to pool members to reduce duplicate work: "Miner A searches 0–1M, Miner B searches 1M–2M." This is advisory — miners can search any range.

**Share Submission:** Each miner sends near-miss proofs (shares) at pool difficulty (4 bits easier than network difficulty) to the coordinator via LoRa. A share proves the miner is working and counts toward their reward allocation.

**Block Found:** When any member finds a block at network difficulty, the coordinator constructs a multi-output coinbase that splits the reward proportionally by share count. The block finder receives a 5% finder's bonus.

**Coordinator Rotation:** Every epoch (16 blocks), a new election occurs. If the coordinator goes silent for 3 blocks, members automatically trigger re-election. No single point of failure.

### Reward Split Example

| Member | Shares | Proportional | Finder Bonus | Total |
|--------|--------|-------------|-------------|-------|
| Miner A (finder) | 40 | 18.2 QZ | +2.375 QZ | **20.575 QZ** |
| Miner B | 30 | 13.65 QZ | — | **13.65 QZ** |
| Miner C | 20 | 9.1 QZ | — | **9.1 QZ** |
| Miner D | 10 | 4.55 QZ | — | **4.55 QZ** |
| **Total** | **100** | **45.5 QZ** | **2.375 QZ** | **47.875 QZ** |

Remainder from integer rounding goes to the block finder.

### Anti-Cheat

| Attack | Detection | Response |
|--------|-----------|----------|
| Coordinator steals rewards | Split is deterministic from share counts; all members verify locally | Reject block, slash coordinator |
| Miner fakes shares | Each share requires valid PoW at pool difficulty | Forging shares = as hard as mining |
| Sybil (fake members) | One ESP32 = one attested pubkey | Need physical devices to inflate shares |
| Coordinator goes dark | No communication for 3 blocks | Auto re-election |
| Withholding winning blocks | Member gets nothing by withholding | Self-defeating — no economic incentive |

### Solo Mining

Miners can always opt out of pooling by setting `pool_mode = SOLO`. Full reward on block find, zero on miss. Suitable for low-density areas where no mesh neighbors exist.

### Advantages Over Centralized Pools

| | Quartz Mesh Pool | Bitcoin Centralized Pool |
|---|---|---|
| Operator | Rotating, elected | Fixed company |
| Fee | 0% | 1–2.5% |
| Server | None (LoRa mesh) | Required (internet) |
| Payout | Same block (coinbase split) | Daily/weekly threshold |
| Censorship | Impossible (no operator) | Pool can exclude txs |
| Trust | Trustless (deterministic split) | Trust operator |
| Privacy | Pseudonymous (LoRa) | Pool knows your IP |

## Storage Architecture

Quartz nodes use a **three-tier layered storage** design optimized for reliability on embedded hardware. The key insight is that storage failures on ESP32 platforms come from physical socket issues and power-loss corruption — not write endurance. The architecture separates critical state (which must survive any crash) from bulk history (which can be rebuilt from peers).

### Storage Tiers

| Tier | Medium | Capacity | Role | Reliability |
|------|--------|----------|------|------------|
| 0 | On-chip flash | 4-16 MB | Firmware, encrypted keys (NVS) | High (soldered, encrypted) |
| 1 | FRAM (SPI) | 256 KB | Chain tip, UTXO root, recent headers | Extreme (atomic, zero wear) |
| 2 | USB Flash (OTG) | 8-128 GB | Full block history | High (soldered USB, journaling) |

### Tier 1: FRAM — The Anchor

FRAM (Ferroelectric RAM) is the critical innovation for embedded blockchain storage:

- **Infinite write endurance** (>10^14 cycles) — no wear-out ever
- **Byte-addressable** — no erase blocks, no read-modify-write
- **Instant writes** — no programming delay
- **Non-volatile** — data persists instantly on power loss (no capacitor/battery needed)
- **Atomic** — no partial writes possible at the hardware level

A 256KB FRAM module (FM25V256, ~$3) stores:

```
Offset  Size     Content
------  -------  ------------------------------------------
0x0000  32       Chain tip hash (SHA-256 of best block)
0x0020  4        Chain height
0x0024  4        Cumulative chain work
0x0028  32       UTXO set commitment (Merkle root)
0x0048  4        UTXO count
0x004C  2        Magic (0x515A = "QZ")
0x004E  2        Schema version
0x0050  4        Write sequence (incremented each commit)
0x0054  12       Reserved
0x0060  20 KB    Last 256 block headers (ring buffer)
0x5060  256 B    Last 64 difficulty values
0x5160  ~190 KB  UTXO snapshot (compact)
```

**Atomic commit protocol:** Metadata fields are written first, then `write_seq` is incremented last. If power is lost mid-write, the sequence number doesn't match → previous state is used. This mirrors a journaling filesystem's commit barrier.

### Tier 2: USB Flash — The Archive

ESP32-S3 supports USB OTG (Host mode), allowing direct connection to USB flash drives without a computer:

- **Soldered connection** (USB-A socket on board) — no physical socket failure like SD cards
- **Industrial USB drives** available with SLC NAND (10x more reliable than consumer SD)
- **64 GB holds 10+ years** of full chain data at moderate usage
- **Hot-pluggable** — node auto-detects and rebuilds index when drive inserted

Block files stored as `/quartz/blocks/block_NNNNNNNNNN.bin`. Index file maps heights to file offsets. On unclean shutdown, FRAM snapshot tells the node exactly where to resume.

### Why Not SD Cards?

SD cards fail in the field for three reasons — none of which are write endurance:

1. **Physical socket failure** — Vibration, dust, and corrosion damage the spring contacts. SD sockets are rated for ~10,000 insertions; field vibration causes micro-disconnections.
2. **Power-loss corruption** — SD controllers cache writes in volatile RAM. Power loss mid-write corrupts not just the current block but potentially the entire FAT table. Only industrial SD cards have power-loss protection capacitors.
3. **Controller crashes** — Cheap SD controllers lock up under sustained write loads, requiring a power cycle. The flash itself is fine, but the controller is unreliable.

USB flash drives avoid all three: soldered USB traces (no socket), better controllers with wear-leveling, and larger over-provisioning.

### Node Modes

Three storage modes balance resource usage:

**SPV (Simple Payment Verification)**
- Stores: block headers only (80 bytes/block)
- Annual size: ~21 MB/year
- Hardware: ESP32 + FRAM (no USB needed)
- Validates: PoW chain difficulty (can't verify transaction validity)
- Use case: Pure mining (miner only needs current block template)

**Pruned**
- Stores: All headers + last 2016 full blocks (one retarget period) + UTXO set
- Annual size: ~21 MB headers + ~530 KB blocks (rolling window)
- Hardware: ESP32 + FRAM + small USB (even 1 GB suffices)
- Validates: Full block validation for recent blocks
- Use case: Running a verifying node on ESP32

**Full (Archival)**
- Stores: All blocks and headers since genesis
- Annual size: ~70 MB/year (coinbase only) to ~530 MB/year (avg 10 txs/block)
- Hardware: Raspberry Pi or mini PC with SSD
- Validates: Everything, serves historical data to new nodes
- Use case: Seed nodes, block explorers, archival

### Chain Size Projections

Based on actual Quartz block sizes (80-byte header, 176-byte coinbase TX, 2-byte TX length prefix):

| Scenario | Per Block | Year 1 | Year 5 | Year 10 |
|----------|-----------|--------|--------|---------|
| Coinbase only | 265 B | 70 MB | 350 MB | 700 MB |
| Avg 5 txs | 1.2 KB | 303 MB | 1.5 GB | 3.0 GB |
| Avg 20 txs | 3.8 KB | 1.0 GB | 5.0 GB | 10 GB |
| Avg 100 txs | 18 KB | 4.7 GB | 24 GB | 47 GB |

For comparison, Bitcoin grows at ~68 GB/year. Quartz is 100-1000x smaller.

### Recovery Protocol

When a node restarts after a crash or power loss:

1. **Read FRAM** — Get chain tip, height, UTXO root (always consistent)
2. **Verify USB** — Scan block files against FRAM height
3. **If USB gap detected** — Request missing blocks from LoRa mesh peers or WiFi
4. **Rebuild index** — If USB was replaced, scan all block files and rebuild
5. **Resume mining** — Once caught up to mesh consensus tip

The FRAM write_seq field tracks total commits. If it seems wrong (e.g., lower than a peer's), the node knows it's on a stale chain and requests a resync.

### FRAM Module Compatibility

| Module | Capacity | Interface | Price | Notes |
|--------|----------|-----------|-------|-------|
| MB85RS256B (Fujitsu) | 256 KB | SPI | $3.50 | Primary recommendation |
| FM25V256 (Cypress) | 256 KB | SPI | $4.00 | Pin-compatible alternative |
| MB85RS1M (Fujitsu) | 1 MB | SPI | $12.00 | Extended UTXO snapshot space |
| W25Q128 (Winbond) | 16 MB | SPI | $1.50 | NOR Flash (100K writes — NOT FRAM) |

Note: NOR flash (W25Q series) is sometimes marketed alongside FRAM but has limited write endurance (100K cycles vs FRAM's 10^14). For the chain tip commit region, only true FRAM should be used. NOR flash is acceptable for the bulk header region where writes are spread across many sectors.

## RNG Hardening — Preventing the Coldcard Bug Class

### The Coldcard Incident (July 2026)

Coldcard hardware wallets lost $130M+ across 4,500+ addresses due to a firmware bug where `#ifndef MICROPY_HW_ENABLE_RNG` checked macro existence (not value), silently switching the hardware TRNG to a non-cryptographic software PRNG (Yasmarang) seeded from ~40 bits of entropy. The bug was invisible for 5 years because the output passed statistical tests.

### Quartz Five-Layer RNG Defense

**Layer 1: No compile-time RNG switches.** The Coldcard bug was a preprocessor logic error. Quartz has exactly ONE code path for random generation — no `#ifdef`, no fallback. If the hardware RNG isn't ready, the device refuses to boot.

**Layer 2: Radio-first initialization.** ESP32's hardware RNG is seeded by WiFi/BLE radio noise. Before radio init, `esp_random()` returns software-derived values. Quartz requires radio initialization and 100ms warmup before any key generation.

**Layer 3: NIST SP 800-90B health checks.** Before burning eFuse or generating keys, 1024 bytes of raw entropy are tested:
- Repetition count (detect stuck values)
- Adaptive proportion (detect bit bias)
- Chi-square (detect non-uniform distribution)
- Min-entropy estimate (≥ 6.0 bits/byte)

If any test fails, key generation aborts and device displays error.

**Layer 4: Triple-mixed entropy pool.** Three independent sources are XOR'd:
- RF subsystem noise (`esp_fill_random`)
- SAR ADC (floating pin analog noise)
- SRAM PUF (power-on state)

Attacker must control ALL three sources to predict output. If even one is truly random, the mix is truly random.

**Layer 5: Public auditability.** Birth certificates include an entropy sample hash — SHA-256 of the raw health-check samples. Independent auditors can verify entropy quality from purchased devices.

### What Health Checks Can and Cannot Detect

Statistical health checks detect **hardware faults** (stuck bits, broken radio, biased ADC) but **cannot** detect a cryptographically weak PRNG that produces well-distributed output. The Coldcard Yasmarang PRNG passed statistical tests for years. This is a fundamental limitation of black-box testing.

Quartz's defense against this limitation is architectural: radio-first init and triple-mix ensure the entropy comes from physical sources (RF noise, analog noise, SRAM state), not from a deterministic algorithm that could be silently substituted via a compile-time flag.

## Supply Chain Security

### The Reseller Threat

An attacker buys ESP32 boards in bulk, flashes custom firmware that captures the eFuse key during provisioning, then reflashes with official firmware and sells as "Quartz Ready Miners." The attacker retains every buyer's private key.

### Birth Certificate Protocol

Every Quartz device produces a cryptographic **birth certificate** at first boot:

1. **First boot:** Device detects empty eFuse BLOCK6
2. **Key generation:** 32 random bytes from hardware RNG burned into eFuse (irreversible)
3. **Read protection:** eFuse block set to read-protected — key only accessible via hardware HMAC engine
4. **Certificate creation:**
   - `key_commit_hash` = SHA-256(efuse_key || chip_id || "QUARTZ_GENESIS")
   - `device_pubkey` = Ed25519 public key for block signing
   - `firmware_hash` = SHA-256 of running firmware binary
   - `first_boot_timestamp` = RTC time at provisioning
   - `birth_signature` = Ed25519 signature over all fields
5. **Certificate stored in NVS and displayed on screen**

### Buyer Verification

At purchase time, the buyer powers on the device and their phone app checks:

| Check | Pass | Fail |
|-------|------|------|
| Chip ID not on network | Fresh device | Clone or used |
| Firmware hash = official | Running official release | Custom/modified firmware |
| Birth timestamp ≥ release date | Born after firmware shipped | Impossible date = fraud |
| Birth signature valid | Certificate integrity confirmed | Forged certificate |

### Anti-Pre-Flash Detection

A reseller who burns the eFuse with custom firmware, then reflashes with official firmware, faces an unsolvable problem:

- **NVS wiped:** No certificate → `quartz_detect_tampering()` returns true (eFuse burned, no cert)
- **NVS preserved:** Certificate shows the attack firmware's hash → `FIRMWARE_MISMATCH`
- **Cannot re-create certificate:** eFuse is one-way (can't provision a new key)
- **Cannot fake firmware hash:** Certificate signature was created with the original device key

The reseller is trapped. There is no sequence of actions that produces a valid certificate with official firmware hash after burning the eFuse with custom firmware.

### Reseller Certification Program

Authorized resellers receive batch certificates signed by the Quartz multi-sig (3-of-5):
- Batch ID, reseller identity, device count, factory firmware hash
- Buyers can verify their device came from an authorized batch
- Unauthorized resellers still function but the phone app warns: "Not from authorized reseller"

### Summary

| Attack | Detection |
|-------|----------|
| Pre-flash with known key | Firmware mismatch or tampering detection |
| Sell clone with same chip ID | Clone detected on network registration |
| Re-verify used device | Chip ID already registered |
| Forge certificate | Ed25519 signature invalid |
| Backdate birth timestamp | Timestamp before firmware release |

## SRAM PUF — Hardware-Bound Mining Key

### The Problem with Emulatable PoW

CrystalHash v2's eFuse HMAC interleaving prevents GPU acceleration, but the eFuse key can theoretically be extracted via decapping ($10K+ per chip). An attacker with enough resources could build an emulator. We need a second layer of hardware binding that cannot be extracted even with physical access.

### Solution: SRAM Physical Unclonable Function

Every ESP32 chip has unique manufacturing variations in its SRAM cells. On power-up, before the runtime clears memory, these cells settle to deterministic but chip-specific values. This "silicon fingerprint" is:

- **Unclonable** — determined by sub-wavelength transistor variations
- **Stable** — reproducible across the chip's lifetime (with error correction)
- **Unextractable** — even decapping doesn't reveal the PUF; it's a physical property, not stored data

### Fuzzy Extractor Design

SRAM PUF bits have natural drift (~5-10% instability) due to temperature and voltage variations. A fuzzy extractor makes the noisy PUF output reliable:

**Enrollment (first boot):**
1. Sample 256 bytes of PUF data 5 times
2. Hash each sample to 256 bits
3. Build stability map: bit is "stable" if all 5 samples agree
4. Select stable bits → enrolled_key
5. helper_mask = first_sample XOR enrolled_key
6. enrolled_hash = SHA-256(enrolled_key)
7. Store {helper_mask, stability_map, enrolled_hash, salt} in NVS

**Reconstruction (every boot):**
1. Read fresh SRAM sample → hash to 256 bits
2. candidate = sample_hash XOR helper_mask
3. Apply stability map (zero unstable bits)
4. If SHA-256(candidate) == enrolled_hash → success
5. If not: try 1-bit error correction (flip each stable bit)
6. If still failing: try 2-bit correction

With ~70% stable bits, reconstruction succeeds >99% of the time with 1-bit correction.

### Mining Integration

Every block submission includes a PUF attestation:

```
challenge = SHA-256(block_header || puf_key || nonce || challenge_salt)
```

The node stores each device's PUF fingerprint (first 8 bytes of SHA-256(enrolled_key)) and can verify that submitted blocks come from genuine hardware. A GPU farm has no PUF key to include.

### Implementation: RTC NOINIT Method

The ESP32's RTC FAST MEMORY (`.rtc_noinit` section) is never cleared by the bootloader or C runtime startup code. On power-on reset (cold boot), these SRAM cells contain genuine undefined silicon state — real manufacturing entropy unique to each die.

```c
RTC_NOINIT_ATTR volatile uint8_t g_puf_capture[512];
```

**Cold boot (power-on):** RTC SRAM has raw undefined bytes from silicon → genuine PUF sample
**Warm boot (reset/watchdog):** RTC SRAM retained from previous boot → NOT fresh → refuse

The implementation is strictly cold-boot-only:

1. **Cold boot, no enrollment** → Enroll from fresh RTC SRAM, store helper data in NVS
2. **Cold boot, has enrollment** → Verify fresh RTC sample against NVS helper data, with 1-bit error correction for temperature/voltage drift
3. **Warm boot** → **REFUSE. HALT. No fallback.** Device displays "PUF FAILED - HALTED" and stops. Power-cycle required.

There is no software path to a weak key. The `reconstruct_puf()` function that previously used MAC-derived fallback was deleted entirely. This is the Coldcard lesson: any fallback creates a downgrade attack surface.

### Honest Security Assessment

**What's solid:**
- PUF reads actual power-on silicon state via RTC NOINIT
- No fallback, no warm boot mining, no MAC-derived key path
- 1-bit error correction handles legitimate temperature/voltage drift
- Device halts if PUF fails — no mining with compromised keys

**Remaining attack surfaces:**
- **No flash encryption** (on dev devices without burned eFuses) — attacker can read NVS helper data
- **No secure boot** (on dev devices) — attacker can swap firmware to exfiltrate key on next cold boot
- **Physical decapping** — ~$10,000 in lab equipment extracts one chip's PUF state (not economically rational)

**Production devices** must have flash encryption + secure boot burned at provisioning time. Development devices (like our M5Stack) run without burned eFuses for testing.

### Why No On-Chip Sensors in the Mining Loop

A proposed enhancement was mixing ADC noise, hall sensor, and temperature readings into each nonce attempt to make the hash ESP32-specific. Analysis showed this provides no additional security:

- Sensor readings are digital values that can be captured from one $5 ESP32 and fed to a GPU
- One real chip as a "sensor oracle" could serve unlimited simulated nonces
- Verification becomes fuzzy (sensor readings vary between chips)
- The PUF already forces attackers to own physical hardware

The hardware requirement IS the moat. Additional technical moats based on sensor readings are circumventable and add complexity without raising attack cost.

## WOTS+ Quantum-Resistant Signatures

### The Quantum Threat

Shor's algorithm, running on a sufficiently large quantum computer, breaks all commonly used signature schemes:

| Algorithm | Used By | Quantum-Vulnerable? |
|----------|---------|---------------------|
| RSA-2048 | TLS, PGP | Yes (broken ~4096-qubit QC) |
| ECDSA-256 | Bitcoin, Ethereum | Yes |
| Ed25519 | Solana, Quartz (legacy) | Yes |
| **WOTS+** | **Quartz** | **No** |

Grover's algorithm provides a quadratic speedup on hash brute-force, reducing SHA-256's effective security from 128 to 64 bits. This remains computationally infeasible.

### WOTS+ Parameters

| Parameter | Value |
|-----------|-------|
| Hash function | SHA-256 |
| Winternitz parameter (w) | 4 |
| Hash chains | 67 (64 message + 3 checksum) |
| Chain length | 2^4 - 1 = 15 iterations |
| Public key size | 2,144 bytes |
| Signature size | 2,144 bytes |
| Merkle tree height | 8 |
| One-time keys per address | 256 |
| Full signature (sig + auth path) | 2,404 bytes |
| Classical security | ~128 bits |
| Quantum security (post-Grover) | ~64 bits |

### How It Works

**Key generation:**
1. From master seed, derive 256 OTS seeds: `seed_i = SHA-256(master_seed || i)`
2. Each OTS seed generates 67 private key chains
3. Each chain is hashed 15 times to produce a public key chain
4. Public key = H(all 67 public chains)
5. Merkle leaf = H(public_key)
6. Address = Merkle root of 256 leaves

**Signing:**
1. Convert message hash to 67 base-16 values (4 bits each)
2. Compute checksum: sum of (15 - value) for all message values
3. For each chain i: signature[i] = H^values[i](private_key[i])
4. Auth path: siblings from leaf to Merkle root
5. Output: WOTS+ sig (2,144B) + auth path (256B) + OTS index (4B)

**Verification:**
1. For each chain i: complete the hash chain (15 - values[i] more hashes)
2. This reconstructs the public key
3. Hash public key → Merkle leaf
4. Verify auth path from leaf to expected Merkle root

### Why WOTS+ Instead of SPHINCS+

SPHINCS+ is stateless (can sign unlimited times) but has 49KB signatures — too large for ESP32's memory and LoRa packets. WOTS+ with a Merkle tree gives 256 signatures per address with 2.4KB signatures, fitting comfortably in ESP32 RAM. The statefulness (tracking OTS index in NVS) is acceptable for a mining device that signs relatively infrequently.

### Integration with Quartz

- **Address = Merkle root** (32 bytes, like a Bitcoin hash but quantum-safe)
- **Transaction signatures**: 2,404 bytes instead of 64 bytes (Ed25519)
- **Key rotation**: After 256 signed transactions, generate new address
- **NVS storage**: Master seed + Merkle root + next OTS index
- **Node verification**: Python reference implementation validates WOTS+ sigs

## On-Chain Messaging

Transactions include an optional `data` field (up to 256 bytes, 2-byte length prefix) that can carry arbitrary data. This enables:

- **Encrypted messaging**: Messages encoded in transaction data, replicated across all nodes
- **Anti-spam**: Each message costs a transaction fee (micro-QZ)
- **Permanence**: Messages are baked into the blockchain, immutable
- **Off-grid**: Combined with LoRa mesh, enables messaging without internet

### Message Protocol

```
Transaction.data = [
  length (2 bytes) |
  message payload (up to 256 bytes)
]
```

Node endpoints:
- `GET /api/v1/messages` — recent messages from chain
- `POST /api/v1/messages/send` — broadcast a message transaction

ESP32 firmware polls for new messages every 10 seconds and displays them on the MAIL screen.

## Known Risks & Mitigations

### 1. SEC Security Classification (Howey Test)

**Risk**: Quartz could be classified as a security if marketed as an investment with expectation of profit.

**Mitigation**:
- No entity holds tokens for promotion — 0% dev fund, 0% premine
- Marketing emphasizes *utility* (mesh networking, hardware wallet, IoT payments), never profit
- No yield promises, no staking rewards, no "passive income" language
- Early adopter 2x bonus described as network bootstrapping incentive, not ROI
- QZ is a utility token for device attestation, transaction fees, and mesh access

### 2. 51% Attack on Young Chain

**Risk**: At low hashrate (28 H/s per device), a single attacker with 30+ ESP32s could control consensus.

**Mitigation**:
- **Checkpoints**: Consensus-critical checkpoints hardcoded in node software prevent deep reorgs
- **`verify_checkpoint()`**: Nodes reject any reorg that conflicts with a checkpoint
- **Weak subjectivity**: New nodes must trust a recent checkpoint from a reputable source
- **No exchange listings** until hashrate is distributed across 100+ independent devices
- **Founding node** operates as trusted checkpoint oracle during Era 1

### 3. Centralized Node (Single Point of Failure)

**Risk**: Currently one reference node. If it goes down, all mining stops.

**Mitigation**:
- Testnet-only concern — mainnet requires P2P consensus
- Multiple seed nodes planned for mainnet launch
- ESP32 light miners cache block templates and can survive short node outages
- LoRa mesh allows block propagation without any centralized server

### 4. Bridge Hacks ($3B+ Lost Cross-Chain)

**Risk**: A QZ ↔ ETH/BTC bridge introduces custody risk and smart contract vulnerabilities.

**Mitigation**:
- **No official bridges**. Wrapped QZ on other chains is not supported.
- If community builds a bridge, it must be fully trustless (HTLC atomic swaps only)
- No custodial wrapping, no multi-sig bridges, no validator-set bridges
- This is a policy decision documented in the whitepaper, not just a TODO

### 5. Governance Capture (Quantum Pool)

**Risk**: The 5% quantum security pool is governed by on-chain voting from Era 2. A whale could buy enough QZ to redirect funds.

**Mitigation**:
- **Quadratic voting**: vote_weight = sqrt(QZ_held) — reduces whale influence
- **Per-voter cap**: Maximum 5% of total vote per address
- **Time-lock**: QZ must be held for 1000 blocks before voting
- **PUF-attested voting**: 2x vote weight for provably hardware-bound QZ
- Era 1 governance: 3-of-5 founding developer multi-sig (transparent, auditable)

### 6. Empty Block Spam

**Risk**: Miners produce empty blocks to collect rewards without processing transactions.

**Mitigation**:
- **Grace period**: First 10 empty blocks per miner — no penalty (legitimate)
- **Decaying reward**: After 10 consecutive empty blocks, miner reward reduced 10%
- **Mempool awareness**: If mempool has >5 pending tx and block is empty, penalty doubles
- **Anti-gaming**: Penalty resets after including any transaction

### 7. Difficulty Manipulation

**Risk**: Large miner raises difficulty, then leaves, stranding honest miners.

**Mitigation**:
- **Max 25% retarget**: `adjust_difficulty()` clamps changes to ±25% per retarget period
- **Slow retarget window**: 144 blocks (~48 hours) — attack must be sustained
- **Time-weighted**: Uses median time of last 144 blocks, not last block timestamp
- **Testnet difficulty**: Capped at minimum difficulty to prevent lockup during development

### 8. Firmware Update Rugpull

**Risk**: Project lead pushes firmware that changes consensus rules in their favor.

**Mitigation**:
- **Birth certificate**: Device firmware hash is recorded at first boot and verifiable
- **Firmware transparency**: All releases signed and published with SHA-256 hashes
- **Consensus via majority**: Nodes must agree on consensus rules — a single firmware push cannot change them
- **Fork process documented**: Any consensus change requires: (1) RFC published, (2) testnet deployment, (3) community vote, (4) 80% node adoption, (5) flag-day activation
- **Open source**: MIT licensed, anyone can audit and verify

### 9. WOTS+ Key Exhaustion

**Risk**: User burns through 256 one-time signatures and loses wallet access.

**Mitigation**:
- **Warning at #240**: Firmware displays "ROTATE KEY NOW" warning
- **Auto-rotation**: Firmware auto-generates new address before exhaustion
- **Balance transfer**: Automated transaction moves funds to new address
- **Transaction counting**: NVS tracks OTS index persistently across reboots

### 10. Social Engineering / Seed Theft

**Risk**: Fake "Quartz wallet recovery" sites steal seed phrases.

**Mitigation**:
- **On-device warning**: Seed phrase screen shows "NEVER share seed. No support will ask."
- **Pinned URL**: Official wallet URL displayed in firmware
- **No seed in serial**: After confirmation, seed phrase wiped from RAM
- **Hardware confirmation**: Transactions require physical button press
- **Education**: Seed phrase page includes anti-phishing guidance

### 11. Liquidity Death Spiral

**Risk**: Price drops → miners quit → chain stalls → more panic.

**Mitigation**:
- **Natural floor**: Mining cost is $0.007/day — miners can operate at near-zero QZ price
- **No leverage**: No lending, no staking, no derivatives — no cascading liquidations
- **PUF binding**: Can't instant-deploy hashpower — each miner must acquire physical hardware
- **Relayer income**: Non-mining nodes earn from relay pool — economic activity beyond mining

### 12. Relayer Pool Sybil Attack

**Risk**: Attacker runs 100 fake relay nodes to capture the 10% pool.

**Mitigation**:
- **PUF-bound relays**: One relay = one PUF-registered device. No hardware, no relay income.
- **Proof-of-relay**: Must demonstrate actual packet forwarding (cryptographic receipts)
- **Coordinator verification**: Elected coordinator validates relay proofs before distribution
- **Anti-cloning**: Same PUF fingerprint cannot register as both miner and relay simultaneously

## Quarry System — Controlled Release Mining Rewards

### The Problem with Conventional Mining Rewards

In Bitcoin and every other mineable cryptocurrency, block rewards go to a wallet address controlled by whoever found the block. Mining hardware and wallet are separate. A GPU can mine coins to any address — including an exchange hot wallet for immediate sale.

This creates constant sell pressure: miners dump rewards as fast as they mine, price tanks, miners quit, network weakens.

### Quartz Quarries

A **quarry** is a registered mining operation — from a single ESP32 on a desk to 10,000 solar nodes in a field. Every miner must register a quarry before mining.

**How it works:**

```
Registration:
  1. Owner generates Ed25519 keypair (or uses existing wallet)
  2. Owner registers quarry on-chain: pubkey + name + PUF device fingerprints
  3. All registered devices mine to the quarry's owner address

Mining:
  1. ESP32 finds block → reward goes to quarry address
  2. PUF attestation proves which device found it
  3. Quarry accumulates rewards

Withdrawal:
  1. Owner signs withdrawal transaction with their key alone
  2. Network checks withdrawal rate limit
  3. If within limit → transaction valid
  4. If exceeds limit → transaction rejected by consensus
```

### Withdrawal Rate Limit

Quarries can only withdraw a percentage of their balance per period. This creates controlled release — mined QZ enters circulation gradually instead of being dumped instantly.

| Parameter | Value |
|-----------|-------|
| Withdrawal window | 7 days (1008 blocks) |
| Max withdrawal | 15% of quarry balance per window |
| Minimum withdrawal | 0.1 QZ (dust prevention) |
| Carry-over | Unused withdrawal allowance does NOT roll over |
| New quarry | Full withdrawal locked for first 1008 blocks (1 week) |

**Example:**

A quarry accumulates 1,000 QZ from mining.
- Week 1: Can withdraw up to 100 QZ
- If they withdraw 100 → 900 remains → Week 2: can withdraw 90 QZ
- If they withdraw 0 → 1,000 remains → Week 2: can withdraw 100 QZ (not 200)
- Full withdrawal takes ~20 weeks of consistent withdrawals

This makes instant dumping impossible but lets operators access earnings regularly.

### Single Miner (Quarry of One)

Most Quartz miners start as a quarry of one — a single ESP32 on a desk, in a window, or on a rooftop.

- Register with one device and one owner key
- Mine to your own address
- Withdraw up to 15% per week
- No co-signing, no button presses, no hardware wallet
- It's just mining with a slow-release faucet

The rate limit has minimal impact on small miners — someone mining 0.1 QZ/day withdraws 0.7 QZ/week, well under any limit. The rate limit only bites on large operations trying to dump.

### Infrastructure Quarries (Roads, Solar Farms)

A road operator registers a quarry with 500 device fingerprints. All 500 devices mine to one owner address. The operator checks the balance on a phone app and withdraws periodically — no need to dig up asphalt.

- 500 devices × 28 H/s = 14,000 H/s
- ~50 QZ/day mined (at current difficulty)
- Withdraw 350 QZ/week (well within rate limit)
- No physical access to devices needed
- Stealing a device doesn't affect the quarry — owner key is separate

### Anti-Cheat

| Attack | Detection | Response |
|--------|-----------|----------|
| Register many small quarries to bypass rate limit | One owner key per KYIP (identity hash) | Multiple quarries per owner = rate limits aggregated |
| Transfer between own quarries to reset limit | Consensus tracks quarry-to-quarry transfers | Same-owner transfers don't reset withdrawal window |
| Mine to someone else's quarry | Quarry registration specifies allowed PUF fingerprints | Foreign device blocks rejected |
| Fake quarry registration | Registration requires on-chain PUF attestation | Must have real hardware |

### Economic Implications

The quarry rate limit creates natural scarcity without any staking or lockup mechanism:

- **Mined QZ enters circulation slowly** — 15%/week means ~85% of mined supply is always illiquid
- **Infrastructure quarries accumulate** — road operators might withdraw monthly, leaving QZ sitting for weeks
- **No instant dump pressure** — even a large miner can't crash the price by dumping
- **Hardware cost floor** — each QZ cost real silicon + energy to produce, miners won't sell below cost
- **Predictable inflation** — supply enters market at a known maximum rate

This is friction at the protocol level, not the hardware level. No physical button presses, no device co-signing, no buried wallets. Just economic physics: you can only pour out 15% of the bucket per week.


## Off-Grid Mining — Solar, Wind, and LiFePO4

Quartz's ultra-low power requirement (1.2W per ESP32) opens a mining category impossible for any other cryptocurrency: **off-grid mining powered by solar panels and small wind turbines, with LiFePO4 battery storage.** Bitcoin ASICs need 3,000W continuously. Quartz needs 1.2W. This difference enables genuinely green mining — not "carbon offset" green, but "powered by sunlight and wind" green.

### Power Budget

| Source | Average Power | Hours/Day | Daily Energy | Sufficient? |
|--------|-------------|-----------|-------------|------------|
| Solar panel (5W) | 3-5W peak | 4-8h | 12-40 Wh | ✅ Plenty |
| Solar panel (1W mini) | 0.5-1W peak | 4-8h | 2-8 Wh | ✅ With duty cycling |
| Small wind turbine (10W) | 1-10W | Variable | 10-80 Wh | ✅ Day and night |
| USB wall charger | 1.2W continuous | 24h | 28.8 Wh | ✅ Cheapest option |
| Thermoelectric generator | 1-3W | 24h | 24-72 Wh | ✅ Industrial |

ESP32 mining cost: **~28.8 Wh/day** (1.2W × 24h). A 5W solar panel in decent sun produces this in 6-8 hours.

### Solar Mining — The Primary Deployment

The simplest and most proven off-grid mining setup:

```
Solar panel (5W) ──→ TP4056 charge controller ──→ LiFePO4 18650 (1500mAh) ──→ ESP32 + LoRa
     $5                $1                          $4                           $8
     Total: ~$18
```

- Mines during daylight, runs on LFP battery at night
- 5W panel + single LFP 18650 provides ~16h of mining per charge cycle
- LFP chemistry: 2,000+ cycles, fire-safe, non-toxic (unlike LiPo)
- Deployable anywhere with sunlight: rooftops, fields, developing nations
- No internet needed — LoRa mesh relays blocks to the nearest gateway
- This is the most viable deployment for developing nations and remote areas

**Bill of materials per solar node:**

| Component | Cost |
|-----------|------|
| 5W solar panel (100×60mm) | $5 |
| TP4056 charge controller | $1 |
| LiFePO4 18650 battery (1500mAh) | $4 |
| ESP32 + LoRa module | $8 |
| Enclosure + wiring | $2 |
| **Total** | **~$20** |

### Wind Mining — Complementary Power

Small DC wind turbines complement solar by generating power at night and during storms:

- 10W vertical axis wind turbine (VAWT): ~$10
- Works alongside solar — charges the same LFP bank
- Night mining becomes viable without oversized battery
- Best for coastal areas, ridgelines, high-altitude deployments

### LiFePO4 Battery Storage

LiFePO4 (LFP) is the recommended battery chemistry for Quartz mining:

| Chemistry | Cycles | Safety | Cost | Recommendation |
|-----------|--------|--------|------|----------------|
| LiFePO4 (LFP) | 2,000+ | Fire-safe, no thermal runaway | $4/cell | ✅ Best choice |
| Li-ion (NMC) | 500-800 | Fire risk if damaged | $3/cell | ⚠️ Acceptable |
| LiPo | 300-500 | Swelling/fire risk | $2/cell | ❌ Avoid |
| Supercapacitor | 100,000+ | No degradation | $6/10F | ✅ For burst mining |

A single LFP 18650 (1500mAh, 3.2V) stores ~4.8Wh — enough for ~4 hours of continuous mining. Paired with a 5W solar panel, the node mines all day on solar and 4+ hours into the night on battery.

### Intermittent Mining Protocol

For nodes without battery storage (solar-only or burst-powered), Quartz firmware supports intermittent mining:

1. **Energy detection**: ADC monitors input voltage
2. **Boot threshold**: When voltage exceeds 3.3V, ESP32 boots (~1s)
3. **Mine until depleted**: Hash nonces until voltage drops below 3.0V
4. **Submit if found**: Flush any block to LoRa mesh before sleeping
5. **Deep sleep**: ESP32 enters deep sleep (~10μA) until power returns
6. **Repeat**: Next sunrise or wind gust wakes the miner

The protocol handles graceful degradation — partial hashes are not wasted because each nonce is independent. A miner that runs 100 nonces before sleeping contributes 100 attempts to the network, even if it never finds a block solo.

### Mesh Pool Integration for Off-Grid Miners

Intermittent miners join mesh pools automatically:

1. On boot, ESP32 broadcasts LoRa discovery beacon
2. Nearest pool coordinator responds with current work template
3. Miner searches its assigned nonce range until energy depletes
4. Near-miss shares (pool difficulty) are cached in RTC memory
5. On next boot, shares are flushed to coordinator
6. Coordinator aggregates shares across boot cycles

A solar-only node that wakes for 6 hours/day contributes ~600,000 nonce attempts per day pooled. Fifty such nodes on a mesh contribute 30M nonces/day — meaningful hashrate from free sunlight.

### Environmental Impact Statement

Quartz off-grid mining has a near-zero environmental footprint:

- **Solar**: Sunlight was already hitting the ground. Panels simply redirect it to useful computation.
- **Wind**: Wind was already blowing. Turbines convert it to hashes.
- **LFP batteries**: Non-toxic, no heavy metals, 2,000+ cycle life = 5+ years daily use.
- **End of life**: ESP32, solar panels, and LFP cells are all recyclable.

The total energy footprint of Quartz mining is **effectively zero** when off-grid — no grid power, no fossil fuels, no offsets. This is categorically different from Bitcoin's energy consumption, which requires dedicated grid power equivalent to a small country.


## Autonomous Device Agents — Silicon That Earns, Spends, and Acts

### Beyond Passive Mining

Every Quartz mining device already has the ingredients of an autonomous economic agent:

- **Identity** — PUF fingerprint, impossible to impersonate or clone
- **Income** — mining rewards flow to its quarry address
- **Spending** — QR payment system can send QZ to any address
- **Actuation** — GPIO relay control can trigger physical devices
- **Communication** — on-chain messaging can signal other devices and humans
- **Sensors** — ESP32 ADC/GPIO can read vibration, temperature, light, strain gauges

The only missing piece is decision-making logic. Not an LLM — the ESP32 has 520KB SRAM and no PSRAM. But a deterministic rule engine that lets the device respond to on-chain events and real-world sensor input without human intervention.

### Architecture: Quartz Agent Layer

```
┌──────────────────────────────────────────────────────┐
│              ESP32 DEVICE AGENT STACK                 │
├──────────────────────────────────────────────────────┤
│                                                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Sensors    │  │  On-Chain    │  │   Schedule   │ │
│  │  (ADC/GPIO)  │  │   Events     │  │   (Timers)   │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │        │
│         ▼                  ▼                  ▼        │
│  ┌──────────────────────────────────────────────────┐ │
│  │              RULE ENGINE (deterministic)          │ │
│  │                                                    │ │
│  │  IF pothole_detected(balance > repair_cost)      │ │
│  │    THEN pay contractor.maintain.road              │ │
│  │                                                    │ │
│  │  IF relay_request(from=neighbor, fee <= 0.001)   │ │
│  │    THEN relay_blocks() + earn_fee                 │ │
│  │                                                    │ │
│  │  IF balance > threshold AND withdrawal_window     │ │
│  │    THEN withdraw_quarry(owner_address)            │ │
│  │                                                    │ │
│  │  IF battery_low AND has_solar_buffer              │ │
│  │    THEN switch_to_solar() + broadcast(\"low_pwr\")  │ │
│  │                                                    │ │
│  │  IF governance_proposal_affects_quarry            │ │
│  │    THEN vote(based_on_impact_analysis)            │ │
│  └──────────────────────┬───────────────────────────┘ │
│                         │                              │
│         ┌───────────────┼───────────────┐              │
│         ▼               ▼               ▼              │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │   Payments   │ │    Relays    │ │  Broadcasts  │   │
│  │  (send QZ)   │ │  (GPIO pulse) │ │  (on-chain   │   │
│  │              │ │              │ │   messages)  │   │
│  └─────────────┘ └──────────────┘ └──────────────┘   │
│                                                        │
└──────────────────────────────────────────────────────┘
```

The rule engine is a table of trigger→action pairs stored in NVS. Triggers are boolean expressions over sensor readings, on-chain state, and timers. Actions are payments, relay pulses, on-chain messages, or configuration changes.

Rules are owner-defined. The quarry owner writes rules via the wallet PWA and pushes them to the device over WiFi/BLE. Default rules ship with firmware. The owner can revoke or modify rules at any time.

### TinyML: Sensor Intelligence On-Device

For pattern recognition beyond simple thresholds, the ESP32 can run TinyML models using TensorFlow Lite Micro (~20KB overhead):

| Model | Purpose | Size | Accuracy |
|-------|---------|------|----------|
| Vibration classifier | Pothole vs. normal traffic | ~8KB | ~92% |
| Traffic counter | Vehicle classification by vibration | ~12KB | ~87% |
| Energy forecaster | Predict next 24h solar harvest | ~6KB | ~78% |
| Anomaly detector | Detect device tampering/movement | ~4KB | ~95% |

Models are trained off-device on historical data, quantized to int8, and flashed to the device. They are advisory inputs to the rule engine, not autonomous decision-makers. A model says "92% pothole" — the rule engine decides whether to act.

### Application: Self-Maintaining Highway

A deployment of 500 solar nodes, each running as an autonomous agent:

```
Day-to-day operation:
  1. Each node mines QZ from solar energy during daylight
  2. Nodes relay blocks to each other via LoRa mesh (paid from relay pool)
  3. Vibration classifier detects pavement degradation
  4. Rule engine: IF degradation > 70% AND balance > 5 QZ:
     → Send payment to city maintenance wallet
     → Broadcast on-chain message: \"Section 47 degradation critical\"
  5. City receives payment + message → dispatches repair crew
  6. After repair, vibration normal → node stops alerting

No server. No cloud. No API calls. No human monitoring dashboard.
The road literally pays for its own maintenance.```

### Application: Autonomous Solar Microgrid

A solar node mines QZ during daylight, stores excess in a capacitor bank:

```
Energy management:
  1. Mine QZ while solar input > 1.2W (ESP32 active)
  2. Below 1.2W → low-power mode, mine intermittently
  3. Energy forecaster predicts tomorrow's harvest
  4. Rule: IF forecast_low AND battery < 20%:
     → Buy energy credits from grid-tied neighbor via QZ payment
     → Neighbor triggers their relay to share surplus power
  5. During surplus: sell energy credits to nearby devices
```

Nodes trade energy among themselves using QZ as the settlement layer. No utility company, no grid operator — just silicon negotiating with silicon.

### Application: Vending Machine That Mines Its Own Inventory

A vending machine with an ESP32 controller:

```
1. ESP32 mines QZ on idle cycles between customer interactions
2. Customer scans QR code → pays QZ → relay dispenses product
3. Machine accumulates QZ from both mining and sales
4. Rule: IF balance > restock_cost AND inventory < 20%:
   → Auto-pay distributor for restock
   → Message distributor: \"Unit 42 needs restock, SKU list attached\"
5. Machine is self-sustaining — earns from footsteps, pays for its own inventory
```

### Why This Is Different from Existing IoT

Traditional IoT devices are dumb clients. They phone home to a server, the server decides what to do, and sends commands back. This requires:
- A central server (single point of failure)
- Internet connectivity (can't work offline)
- A company to maintain the backend (ongoing cost)
- Trust that the company won't shut down

Quartz device agents are self-contained. The ESP32 has its own:
- Identity (PUF)
- Income (mining)
- Decision-making (rule engine)
- Execution (payments + relays)
- Communication (on-chain messages)

No server needed. Works offline (blocks sync when connectivity returns). No company required — the chain is the backend.

### Why This Is Different from Existing Crypto + AI

Every "AI + crypto" project is one of:
- **LLM trading bot** — an AI agent buys/sells tokens on a server. Boring.
- **AI oracle** — an off-chain AI feeds data to a smart contract. Centralized.
- **AI-generated NFTs** — images minted by a model. Irrelevant.

None of these involve physical hardware making economic decisions in the real world. The Quartz device agent is not an AI calling a blockchain API. It IS the blockchain participant, embedded in physical infrastructure, taking real-world actions based on its own income and sensor data.

### Limitations (Honest)

| Limitation | Impact |
|-----------|--------|
| No LLM inference on-device | Can't do natural language reasoning. Rules are deterministic, not generative. |
| TinyML accuracy | Models are small and quantized. Will have false positives (unnecessary repair dispatch) and false negatives (missed potholes). |
| Rule complexity | Trigger→action rules can't express complex multi-step plans. Each rule is independent. |
| Attack surface | Sensor spoofing (fake vibration to trigger false alerts). Mitigated by requiring multiple sensors to agree. |
| Owner dependence | Rules are owner-defined. A device with no rules is just a regular miner. |

This is not artificial general intelligence. It's a thermostat with a wallet. But thermostats control billions of dollars of HVAC spending, and the fact that they're simple doesn't make them worthless.


## Roadmap

- **Phase 1** — Protocol spec, reference Python node, ESP32 firmware MVP ✅
- **Phase 2** — Testnet launch, block explorer, hardware wallet UI, PWA + Android wallet ✅
- **Phase 3** — BLE mesh networking, difficulty tuning, T-Display S3 firmware release
- **Phase 4** — Mainnet launch, documentation, community
- **Phase 5** — Mobile app (React Native), block explorer, merchant integration

## License

MIT
