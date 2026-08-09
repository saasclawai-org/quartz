# Quartz (QZ)

### A cryptocurrency mineable exclusively on ESP32 microcontrollers

---

## Abstract

Quartz is a decentralized cryptocurrency designed to be mined exclusively on ESP32-series microcontrollers. Unlike conventional cryptocurrencies that favor specialized hardware (ASICs, GPUs), Quartz uses a hardware-bound Proof-of-Work algorithm that leverages the ESP32's unique silicon characteristics — making it impossible to mine profitably on any other platform.

## Motivation

Existing "IoT-minable" coins (Duino-Coin, etc.) are either centralized or trivially mineable on GPUs/servers, defeating the purpose of distributed mining. Quartz solves this by binding the PoW algorithm to physical ESP32 hardware properties that cannot be emulated efficiently.

## Technical Design

### PoW Algorithm: CrystalHash

CrystalHash exploits three ESP32-specific properties:

1. **Hardware SHA/AES accelerator** — The ESP32 has a dedicated cryptographic accelerator. CrystalHash requires chains of SHA-256 + AES operations that are fastest on this hardware vs. software implementations on general CPUs.

2. **Memory-hard dataset** — A 256KB scratchpad (half of ESP32's 520KB SRAM) filled with hardware-derived pseudo-random data. This is too large for cache, too small to be interesting for ASIC development, and awkward for GPUs (which handle large parallel datasets better).

3. **Physical unclonable function (PUF)** — Subtle timing variations in the ESP32's flash cache and SRAM are hardware-specific. Each chip produces slightly different timing fingerprints. CrystalHash incorporates a calibration step that binds mining to the physical chip.

```
CrystalHash(block_header, nonce):
    1. Initialize 256KB scratchpad from block_header using hardware AES
    2. Perform 64 rounds of random reads from scratchpad (memory-hard)
    3. Mix nonce + flash cache timing samples
    4. Final SHA-256 via hardware accelerator
    5. Output: 32-byte hash
```

**Why this is hard to fake:**
- GPUs: Memory access patterns don't map well to 256KB per-thread scratchpad. GPU threads share VRAM, not per-core SRAM.
- CPUs: Software SHA is slower than ESP32's hardware accelerator for this specific chained pattern.
- ASICs: Building a custom chip for a niche coin isn't economically viable, and emulating the PUF requires physical ESP32 silicon.

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
**Block reward:** 50 QZ initially (47.5 QZ to miner + 2.5 QZ to dev fund), halving every 210,000 blocks (~8 years)
**Smallest unit:** 1 qz-sat = 10⁻⁸ QZ
**Dev fund:** 2.1M QZ (5%), vested over 4 years via per-block emission
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
| Total Supply | 42,000,000 QZ |
| Mineable Supply | 39,900,000 QZ (95%) |
| Dev Fund | 2,100,000 QZ (5%), 4-year vesting |
| Initial Block Reward | 50 QZ (47.5 miner + 2.5 dev fund) |
| Halving Period | 210,000 blocks (~8 years at 2min blocks) |
| Block Time | 120 seconds |
| Difficulty Retarget | 144 blocks (~48 hours) |
| Smallest Unit | 0.00000001 QZ (1 quartz-sat) |
| Premine | 0 |
| Early Adopter Bonus | First 1,000 miners: 2x reward for 30 days |
| Bug Bounty | 50,000 QZ allocated from dev fund |

### Dev Fund Vesting Schedule

The dev fund is emitted per-block alongside mining rewards, not premined:

| Period | Dev Fund Per Block | Blocks | Total |
|--------|-------------------|--------|-------|
| Year 1 | 2.5 QZ | ~131,400 | 328,500 QZ |
| Year 2 | 2.5 QZ | ~131,400 | 328,500 QZ |
| Year 3 | 2.5 QZ | ~131,400 | 328,500 QZ |
| Year 4 | 2.5 QZ | ~131,400 | 328,500 QZ |
| **Total** | | ~525,600 | **1,314,000 QZ** |

Remaining 786,000 QZ allocated to: bug bounties (50K), infrastructure grants (200K), community/marketing (536K).

Dev fund addresses are public and auditable on-chain.

### Early Adopter Bonus

The first 1,000 unique ESP32 devices (by chip MAC) that mine a block receive **2x reward** for their first 30 days of mining. This bootstraps network hashpower and rewards early hardware participants.

### Reward Split

```
Block reward (Era 1): 50 QZ total
├── Miner:      47.5 QZ (95%)
├── Dev fund:    2.5 QZ (5%)
└── Bonus (if eligible): +47.5 QZ (from dev fund allocation)
```

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

## Mining Attestation — ESP32 Hardware Binding

### The Problem

CrystalHash is designed to be computable on ESP32 hardware, but it cannot prevent computation on faster hardware. A desktop CPU with AES-NI instructions is ~100x faster than an ESP32 at the AES-256 operations in CrystalHash. Without hardware binding, a single PC could dominate mining and centralize the network.

### Solution: Remote Attestation via eFuse + Ed25519

Quartz requires every mined block to include a **hardware attestation signature** that only a real ESP32-S3 can produce. This makes the PoW a necessary-but-insufficient condition — blocks must have BOTH valid PoW AND a valid device signature.

**ESP32-S3 Security Features Used:**

| Feature | Role |
|---------|------|
| eFuse BLOCK6 | 256-bit one-time-programmable key, unique per chip |
| Hardware HMAC | Computes HMAC using eFuse key — key never readable by software |
| Flash Encryption | Encrypts NVS where Ed25519 private key is stored |
| Secure Boot v2 | Ensures only signed firmware can boot and access keys |
| Hardware RNG | Generates Ed25519 keypair with true randomness |

**Protocol:**

```
┌──────────────┐                        ┌──────────────┐
│    ESP32-S3   │                        │   Network    │
│    (Miner)    │                        │  (Verifier)  │
└──────┬───────┘                        └──────┬───────┘
       │                                       │
       │  1. FIRST BOOT                        │
       │  Generate Ed25519 keypair (HW RNG)    │
       │  Burn pubkey hash → eFuse BLOCK6       │
       │  Store private key → encrypted NVS     │
       │                                       │
       │  2. REGISTRATION                      │
       │  HMAC(eFuse_key, pubkey) → attestation │
       │  ─── registration ──────────────────→ │
       │                                       │  Verify attestation
       │                                       │  Add to Miner Registry
       │  ←── registered ────────────────────  │
       │                                       │
       │  3. MINING (every block)              │
       │  Find CrystalHash nonce (PoW)         │
       │  Ed25519_sign(priv, header||nonce)    │
       │  Block = header + txs + attestation   │
       │  ─── block ────────────────────────→ │
       │                                       │  Verify PoW
       │                                       │  Verify Ed25519 sig
       │                                       │  Check registry
       │                                       │  Check timing
       │                                       │
       │  4. SLASHING (if cheating)            │
       │                                       │  Evidence of double-sign
       │                                       │  → Ban device
       │                                       │  → Slash remaining balance
```

**Why a PC can't fake this:**

1. The eFuse HMAC key is physically embedded in the ESP32 silicon — it cannot be read by any software, JTAG, or even the firmware itself
2. Only the hardware HMAC peripheral can use the key — there is no instruction to read it
3. An emulator would need to physically extract the key from the silicon (decapping, electron microscopy) — cost: $10,000+ per chip, far exceeding mining revenue
4. Each device registers once, and the network tracks unique pubkeys

**Anti-Cheat Layers:**

| Layer | Detection | Response |
|-------|-----------|----------|
| Timing analysis | Blocks arriving faster than ESP32 hardware can compute | Auto-ban (threshold: 25% of target block time) |
| Double-signing | Same device signs two blocks at same height | Slash + ban (cryptographic proof) |
| Rate monitoring | Consecutive fast blocks from same device | Flag → ban after 3 consecutive |
| IP clustering | > 5 devices from one IP | Reject new registrations |
| Community audit | Abnormal hash rates reported by network | Manual review + governance vote |

**Slashing:**

If a device signs two different blocks at the same height (a fork attempt), anyone can submit the two signed blocks as evidence. The evidence is verifiable by all nodes. The device is permanently banned and any unclaimed rewards are forfeited. This makes it economically irrational to mine on multiple forks.

**Limitations:**

- **Physical attacks** (decapping, laser fault injection) can extract eFuse keys. Cost ($10K+) exceeds mining revenue for the foreseeable future.
- **Botnet of ESP32s** — an attacker buying many ESP32s can mine legitimately. This is acceptable: they're contributing real hardware hashrate.
- **One ESP32, many PCs** — the ESP32 signs blocks found by PCs. Rate-limited by the device's signing capacity and detectable via timing analysis.

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

## Roadmap

- **Phase 1** — Protocol spec, reference Python node, ESP32 firmware MVP ✅
- **Phase 2** — Testnet launch, block explorer, hardware wallet UI, PWA + Android wallet ✅
- **Phase 3** — BLE mesh networking, difficulty tuning, T-Display S3 firmware release
- **Phase 4** — Mainnet launch, documentation, community
- **Phase 5** — Mobile app (React Native), block explorer, merchant integration

## License

MIT
