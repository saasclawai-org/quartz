# 🔮 Quartz (QZ)

**The ESP32-native cryptocurrency. Mine with a $15 board.**

[Website](https://quartz.preview.saasclaw.ai) · [Block Explorer](https://quartz.preview.saasclaw.ai/explorer/) · [Wallet](https://quartz.preview.saasclaw.ai/wallet/) · [Whitepaper](docs/WHITEPAPER.md)

---

## What is Quartz?

Quartz is a decentralized cryptocurrency designed to be mined **exclusively** on ESP32 microcontrollers. No ASICs. No GPUs. No server farms.

### Why?

| | Quartz | Bitcoin |
|---|---|---|
| Mining hardware | ESP32 ($15) | ASIC ($3,000+) |
| Power consumption | 0.5W | 3,000W |
| Miner entry cost | A coffee | A used car |
| Network | LoRa mesh (offline) | Internet required |
| Keys | Hardware wallet (on-device) | Software or $70 Ledger |

## Key Features

- **CrystalHash PoW** — Hardware-bound to ESP32's AES accelerator + silicon PUF. Can't be mined on GPUs or ASICs.
- **Ed25519 self-custody** — BIP39 12-word mnemonic, SLIP-0010 derivation. Keys generated on-chip, never leave the device.
- **ESP32 = Hardware Wallet** — Seed phrase shown on device screen. Signing confirmed with physical button. No separate wallet to buy.
- **LoRa Mesh** — Miners communicate over LoRa (2-15km range). No internet, WiFi, or cellular needed. Works in rural areas.
- **Three-Layer Radio** — LoRa (miner ↔ miner), BLE (miner ↔ phone), WiFi (miner ↔ internet)
- **Fair Launch** — No premine, no ICO, no VC. MIT licensed. 42M total supply.

## Tokenomics

| | Value |
|---|---|
| Total supply | 42,000,000 QZ |
| Block reward (Era 1) | 50 QZ (47.5 miner + 2.5 dev fund) |
| Halving | Every 210,000 blocks (~8 years) |
| Block time | 120 seconds |
| Dev fund | 5% (2.1M QZ), vested 4 years |
| Early adopter bonus | First 1,000 miners get 2x reward for 30 days |
| Bug bounty | 50,000 QZ from dev fund |

## Supported Boards

| Board | Display | LoRa | Price | Recommendation |
|---|---|---|---|---|
| **Heltec WiFi LoRa 32 V3** | 0.96" OLED | SX1262 | ~$15 | ⭐ Best overall |
| **LilyGO T-Display S3** | 1.14" TFT | — | ~$12 | Best display |
| M5Stack Core2 | 2.0" TFT | — | ~$35 | Premium |
| ESP32-WROOM (generic) | — | — | ~$4 | Budget |

## Quick Start

### Flash an ESP32
```bash
# Using ESP-IDF
git clone https://github.com/saasclawai-org/quartz.git
cd quartz/firmware
idf.py set-target esp32s3
idf.py flash
```

Or use the [browser flasher](https://quartz.preview.saasclaw.ai/#flash) (Chrome/Edge, Web Serial).

### Use the Wallet
Open [quartz.preview.saasclaw.ai/wallet/](https://quartz.preview.saasclaw.ai/wallet/) on your phone.

**Recommended:** Pair with your ESP32 for hardware wallet mode (keys never leave device).

**Fallback:** Software wallet mode (keys encrypted in browser with PIN).

### Run a Node
```bash
git clone https://github.com/saasclawai-org/quartz.git
cd quartz/reference-node
pip install -e .
python3 testnet.py
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   ESP32 MINER                    │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ CrystalHash│  │ Hardware │  │   LoRa Mesh   │ │
│  │   Miner   │  │  Wallet  │  │   (2-15km)    │ │
│  └──────────┘  └──────────┘  └───────────────┘ │
│       │              │               │           │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │  AES-256 │  │ Ed25519  │  │     BLE       │ │
│  │ Scratchpad│  │ Sign/Verify│  │  (phone pair) │ │
│  └──────────┘  └──────────┘  └───────────────┘ │
│       │              │                           │
│  ┌──────────┐  ┌──────────┐                     │
│  │TFT Display│  │Encrypted │                     │
│  │(stats/seed│  │  Flash   │                     │
│  │ /signing) │  │(keys/NVS)│                     │
│  └──────────┘  └──────────┘                     │
└─────────────────────────────────────────────────┘
         │                    │
    ┌────┴────┐         ┌─────┴─────┐
    │  Phone  │         │  Other    │
    │  Wallet │         │  Miners   │
    │(watch-  │         │ (LoRa     │
    │  only)  │         │  gossip)  │
    └─────────┘         └───────────┘
```

## Project Structure

```
quartz/
├── docs/WHITEPAPER.md        # Protocol specification
├── firmware/                  # ESP32 firmware (C/ESP-IDF)
│   ├── main/
│   │   ├── main.c            # State machine + UI loop
│   │   ├── quartz.c/h        # CrystalHash PoW + blockchain
│   │   ├── quartz_wallet.c/h # Hardware wallet (keys, signing)
│   │   ├── quartz_display.*  # TFT UI (T-Display S3, Heltec)
│   │   └── quartz_lora.c/h   # LoRa mesh networking
│   ├── sdkconfig.defaults     # ESP32-S3 config
│   └── idf_component.yml      # Dependencies
├── reference-node/            # Python reference implementation
│   ├── quartz/
│   │   ├── blockchain.py      # Blocks, headers, transactions
│   │   ├── crystal_hash.py    # PoW reference
│   │   ├── crypto.py          # Ed25519, BIP39, addresses
│   │   ├── wallet.py          # Wallet operations
│   │   └── node.py            # P2P networking
│   ├── tests/                 # 58 tests, all passing
│   └── testnet.py             # Testnet seed node
├── android/                   # Android wallet (Kotlin/Compose)
└── README.md                  # You are here
```

## Testing

```bash
cd reference-node
python3 -m pytest -v
# 58 passed in 0.11s
```

Covers: BIP39 wordlist, mnemonic generation, entropy roundtrip, PBKDF2 seed derivation, Trezor test vectors, SLIP-0010 key derivation, Ed25519 signing/verification, address generation, Base58 encoding, block headers, merkle roots, block rewards, halving schedule, difficulty targets, CrystalHash determinism.

## License

MIT — see [LICENSE](LICENSE).

## Links

- 🌐 [Website](https://quartz.preview.saasclaw.ai)
- 📊 [Block Explorer](https://quartz.preview.saasclaw.ai/explorer/)
- 💰 [Wallet](https://quartz.preview.saasclaw.ai/wallet/)
- 📄 [Whitepaper](docs/WHITEPAPER.md)
- 💬 [Discord](https://discord.gg/saasclaw) (coming soon)

---

*Quartz: The answer to everything, mined by everyone.*
