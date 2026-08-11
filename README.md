# 🔮 Quartz (QZ)

**The quantum-safe, hardware-bound cryptocurrency. Mine with a $15 chip.**

[Website](https://quartz.preview.saasclaw.ai) · [Block Explorer](https://quartz.preview.saasclaw.ai/explorer/) · [Wallet](https://quartz.preview.saasclaw.ai/wallet/) · [Whitepaper](docs/WHITEPAPER.md)

---

## What is Quartz?

Quartz is a decentralized cryptocurrency designed to be mined **exclusively** on ESP32 microcontrollers. No ASICs. No GPUs. No server farms. Every block is bound to the physical silicon that mined it, and every wallet is quantum-resistant.

### What makes Quartz different?

| | Bitcoin | Helium | Quartz |
|---|---|---|---|
| Mining hardware | ASIC ($3,000+) | Hotspot ($400) | ESP32 ($15) |
| Power consumption | 3,000W | 5W | 0.5W |
| GPU-farmable | No (ASIC) | N/A | **No (PUF-bound)** |
| Quantum-safe wallets | No | No | **Yes (WOTS+)** |
| Mesh networking | No | Yes (sub-GHz) | **Yes (LoRa)** |
| Hardware wallet | $70 Ledger | N/A | **Built-in** |
| Barrier to entry | High | Medium | **$15 + USB power** |

## Key Features

- **SRAM PUF Mining** — Block hashes include a key derived from the ESP32's silicon. Each chip has a unique fingerprint that cannot be cloned, simulated, or extracted. Mining is physically impossible without the hardware.
- **WOTS+ Quantum Wallets** — Hash-based signatures (not elliptic curve) that resist both classical and quantum attacks. Shor's algorithm can't break them. Grover's gives only quadratic speedup.
- **CrystalHash PoW** — Memory-hard algorithm using ESP32's AES accelerator + PUF attestation per block.
- **On-Chain Messaging** — Transactions carry an optional data field (up to 256 bytes). Send messages that are permanently recorded on the blockchain. Anti-spam via transaction fees.
- **LoRa Mesh** — Miners gossip over LoRa (2-15km range). No internet, WiFi, or cellular needed. Works offline in rural areas.
- **Captive Portal WiFi** — First boot creates a WiFi hotspot. Connect your phone, enter your WiFi password, and the device auto-connects forever. No computer needed for setup.
- **Fair Launch** — No premine, no ICO, no VC. MIT licensed. 42M total supply.

## Quantum Resistance

Quartz uses **WOTS+ (Winternitz One-Time Signature Plus)** with a Merkle tree for key aggregation:

- **Signature basis**: SHA-256 hash chains (no number theory assumptions)
- **Parameters**: Winternitz w=4, 67 chains, Merkle height 8
- **Security**: ~128 bits classical, ~64 bits quantum (post-Grover)
- **Capacity**: 256 one-time signatures per address
- **Signature size**: 2,404 bytes (WOTS+ sig + Merkle auth path)
- **Why it matters**: Shor's algorithm breaks RSA, ECC, and Ed25519. WOTS+ only requires hash preimage resistance — no known quantum algorithm breaks it efficiently.

## Hardware-Bound Mining (PUF)

Each ESP32 has a unique silicon fingerprint:

1. **Enrollment** (first boot): 5 SRAM samples → stability map → helper data stored in NVS
2. **Reconstruction** (every boot): Fresh sample + helper mask → error correction → same key
3. **Mining**: `SHA256(header || puf_key || nonce || salt)` mixed into every block hash
4. **Attestation**: PUF fingerprint published on boot, verified by node

**Result**: A GPU farm can't out-mine real devices because the PUF key is physically embedded in the silicon. Cloning a device requires decapping and electron microscopy ($10K+ per chip).

## Tokenomics

| | Value |
|---|---|
| Total supply | 42,000,000 QZ |
| Dev fund | **0%** — killed, pure fair launch |
| Block reward (PUF block) | 50 QZ (Era 1) |
| Block reward (non-PUF) | 25 QZ (Era 1, phased out by Era 3) |
| Mesh relayer share | 10% of every block |
| Quantum security pool | 5% of every block |
| Miner share | 85% (PUF) or 42.5% (non-PUF) |
| Halving | Every 210,000 blocks (~8 years) |
| PUF required | Era 3+ (blocks 420,001+) |
| Early adopter bonus | First 1,000 PUF devices: 2x for 30 days |
| Founder timelock | 2-year CLTV, no override |
| Bug bounty | Community-funded |
| Premine | 0 |

### Reward Split per Block (Era 1)

```
PUF-attested block (50 QZ):
├── Miner:        42.5 QZ  (85%)
├── Mesh relayers:  5.0 QZ  (10%)
└── Quantum pool:    2.5 QZ  (5%)

Non-PUF block (25 QZ):
├── Miner:        21.25 QZ (42.5% of 50)
├── Mesh relayers:  2.5 QZ  (5% of 50)
└── Quantum pool:    1.25 QZ (2.5% of 50)

Era 3+: Non-PUF blocks = 0 QZ (PUF required)
```

### Quantum Mining Eras

| Era | Blocks | PUF Reward | Non-PUF Reward | Years |
|-----|--------|-----------|----------------|-------|
| 1 | 0–210K | 50 QZ | 25 QZ | 0–8 |
| 2 | 210K–420K | 25 QZ | 12.5 QZ | 8–16 |
| 3 | 420K–630K | 12.5 QZ | **0 (banned)** | 16–24 |
| 4 | 630K–840K | 6.25 QZ | — | 24–32 |
| 5+ | ... | halves | — | ... |

Era 3 is the "quantum enforcement era" — only PUF-attested blocks earn rewards. This gives the network 16 years to transition fully to hardware-bound mining while rewarding early PUF adopters with 2x throughout Era 1.

## Supported Boards

| Board | Display | LoRa | Price | Recommendation |
|---|---|---|---|---|
| **Heltec WiFi LoRa 32 V3** | 0.96" OLED | SX1262 | ~$15 | Best overall |
| **LilyGO T-Display S3** | 1.14" TFT | — | ~$12 | Best display |
| **M5Stack Core** | 2.0" TFT | — | ~$35 | Premium (tested) |
| ESP32-WROOM (generic) | — | — | ~$4 | Budget |

## Quick Start

### Flash an ESP32
```bash
# Using esptool
pip install esptool
python -m esptool --chip esp32 --port COM6 --baud 115200 write_flash \
  0x1000 bootloader.bin 0x10000 partition-table.bin 0x20000 quartz-miner.bin
```

Or use the [browser flasher](https://quartz.preview.saasclaw.ai/#flash) (Chrome/Edge, Web Serial).

### Run a Node
```bash
git clone https://github.com/saasclawai-org/quartz.git
cd quartz/reference-node
pip install -e .
python3 testnet.py
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    ESP32 MINER                       │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │ CrystalHash│  │  WOTS+   │  │   SRAM PUF       │ │
│  │   Miner   │  │ Quantum  │  │   Hardware       │ │
│  │           │  │  Wallet  │  │   Binding        │ │
│  └─────┬────┘  └────┬─────┘  └────────┬──────────┘ │
│        │            │                  │            │
│  ┌─────┴────┐  ┌────┴─────┐  ┌────────┴──────────┐ │
│  │ AES-256  │  │ SHA-256  │  │  Fuzzy Extractor  │ │
│  │ Scratchpad│  │ Chains  │  │  (NVS helper data)│ │
│  └──────────┘  └──────────┘  └───────────────────┘ │
│        │            │                  │            │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │TFT Display│  │Encrypted │  │  WiFi Captive     │ │
│  │(stats/seed│  │  Flash   │  │  Portal + Mining  │ │
│  │ /mail/sign│  │(keys/NVS)│  │  Client (HTTP)    │ │
│  └──────────┘  └──────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────┘
         │                    │                │
    ┌────┴────┐         ┌─────┴─────┐    ┌─────┴─────┐
    │  Phone  │         │  Other    │    │  Quartz   │
    │  Wallet │         │  Miners   │    │  Node     │
    │(watch-  │         │ (LoRa     │    │ (block    │
    │  only)  │         │  gossip)  │    │  validation)│
    └─────────┘         └───────────┘    └───────────┘
```

## Project Structure

```
quartz/
├── docs/WHITEPAPER.md          # Protocol specification
├── firmware/                    # ESP32 firmware (C/ESP-IDF)
│   ├── main/
│   │   ├── main.c              # Entry point + mining loop
│   │   ├── quartz.c/h          # CrystalHash PoW + blockchain
│   │   ├── quartz_puf.c/h      # SRAM PUF + fuzzy extractor
│   │   ├── quartz_wots.c/h     # WOTS+ quantum signatures
│   │   ├── quartz_wallet.c/h   # Hardware wallet (keys, signing)
│   │   ├── quartz_crypto.c     # BIP39 mnemonic + Ed25519 stubs
│   │   ├── quartz_display.c/h  # ILI9341 TFT driver
│   │   ├── quartz_wifi.c/h     # WiFi portal + mining HTTP client
│   │   └── quartz_supply_chain.c # eFuse + birth certificates
│   └── CMakeLists.txt
├── reference-node/              # Python reference implementation
│   ├── quartz/
│   │   ├── blockchain.py       # Blocks, headers, transactions
│   │   ├── crystal_hash.py     # PoW reference
│   │   ├── crypto.py           # Ed25519, BIP39, addresses
│   │   ├── quantum_crypto.py   # WOTS+ verification (node-side)
│   │   └── node.py             # P2P networking
│   ├── testnet.py              # Testnet seed node + API
│   └── tests/                   # Unit tests
├── android/                     # Android wallet (Kotlin/Compose)
└── README.md                    # You are here
```

## Current Status (Aug 2026)

**Working on real hardware:**
- M5Stack Core (ESP32-D0WDQ6) mining on testnet
- First blocks mined and accepted by node
- WiFi captive portal for easy setup
- On-chain messaging (send/receive via transactions)
- Display: mining dashboard, wallet, mail, seed phrase
- PUF enrollment + per-block attestation
- WOTS+ key generation + signing

**Confirmed metrics:**
- Hashrate: 28 H/s on M5Stack Core
- Power: ~1.2W (ESP32 + WiFi + display)
- Electricity cost: $0.007/day at $0.25/kWh
- Block time: ~120 seconds at current difficulty

## Testing

```bash
cd reference-node
python3 -m pytest -v
```

## License

MIT — see [LICENSE](LICENSE).

## Links

- Website: https://quartz.preview.saasclaw.ai
- Block Explorer: https://quartz.preview.saasclaw.ai/explorer/
- Wallet: https://quartz.preview.saasclaw.ai/wallet/
- Whitepaper: [docs/WHITEPAPER.md](docs/WHITEPAPER.md)

---

*Quartz: The answer to everything, mined by everyone.*
