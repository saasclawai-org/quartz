# Quartz (QZ) 🔮

**A cryptocurrency mineable exclusively on ESP32 microcontrollers.**

## Why Quartz?

Most cryptocurrencies end up dominated by specialized hardware (ASICs, GPUs). Quartz flips this: the Proof-of-Work algorithm (**CrystalHash**) is designed so that ESP32 microcontrollers are the *most efficient* mining hardware. No ASIC exists for it, GPUs are inefficient at its memory access pattern, and the physical hardware fingerprint (PUF) makes spoofing uneconomical.

## Quick Start

### ESP32 Miner (firmware)

```bash
cd firmware/
idf.py set-target esp32
idf.py menuconfig    # Set WiFi SSID/password
idf.py build flash monitor
```

**Requirements:** ESP32 (WROVER recommended for PSRAM), ESP-IDF v5.0+

### Reference Node (Python)

```bash
cd reference-node/
pip install -e .
quartz-node --peer peer.example.com:8420
```

## Architecture

```
quartz/
├── docs/
│   └── WHITEPAPER.md          # Full protocol specification
├── firmware/                  # ESP-IDF firmware (C)
│   ├── main/
│   │   ├── quartz.h           # Block/tx structures, CrystalHash API
│   │   ├── quartz.c           # CrystalHash implementation
│   │   └── main.c             # Mining loop + WiFi init
│   ├── sdkconfig.defaults     # ESP32 build config
│   └── partitions.csv         # Flash layout (2MB for blockchain data)
├── reference-node/            # Python node for testing/exploring
│   ├── quartz/
│   │   ├── blockchain.py      # Block/Transaction/BlockHeader
│   │   ├── crystal_hash.py    # PoW verification
│   │   ├── node.py            # P2P networking
│   │   └── wallet.py          # Key management, addresses
│   ├── tests/                 # Protocol tests
│   └── pyproject.toml
└── README.md
```

## Tokenomics

| Parameter | Value |
|-----------|-------|
| Ticker | QZ |
| Total Supply | 42,000,000 QZ |
| Block Time | 120 seconds |
| Initial Reward | 50 QZ |
| Halving | Every 210,000 blocks (~8 years) |
| Smallest Unit | 0.00000001 QZ (1 quartz-sat) |
| Premine | 0 |
| Dev Fee | 0 |

## How CrystalHash Works

1. **AES-256 scratchpad init** — 256KB filled using ESP32 hardware AES
2. **Memory-hard mixing** — 64 rounds of random reads from scratchpad
3. **PUF timing** — Flash cache timing samples (unique per chip)
4. **Final SHA-256** — Via hardware accelerator

The 256KB scratchpad fits in ESP32 PSRAM but is too small to interest ASIC developers and too awkward for GPU parallelism. The PUF component means each physical ESP32 chip produces slightly different hashes — you can't fake it in software.

## Status

🚧 **Pre-alpha** — Protocol spec + reference implementation. Not yet mineable.

### Roadmap
- [x] Protocol design (WHITEPAPER.md)
- [x] CrystalHash algorithm (firmware + reference)
- [x] Block/transaction structures
- [x] Python reference node
- [ ] P2P networking (ESP32 firmware)
- [ ] BLE mesh peer discovery
- [ ] Testnet launch
- [ ] Block explorer
- [ ] Mobile wallet app

## License

MIT — see [WHITEPAPER.md](docs/WHITEPAPER.md)

---

*Every ESP32 runs on a quartz crystal. Now it can mine one too.* 🔮
