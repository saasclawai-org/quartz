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

## Roadmap

- **Phase 1** — Protocol spec, reference Python node, ESP32 firmware MVP ✅
- **Phase 2** — Testnet launch, block explorer, hardware wallet UI, PWA + Android wallet ✅
- **Phase 3** — BLE mesh networking, difficulty tuning, T-Display S3 firmware release
- **Phase 4** — Mainnet launch, documentation, community
- **Phase 5** — Mobile app (React Native), block explorer, merchant integration

## License

MIT
