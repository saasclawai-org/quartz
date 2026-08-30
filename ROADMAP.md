# Quartz Roadmap

## ✅ Done
- [x] ESP32 mining firmware (M5Stack Core + LilyGO T3)
- [x] WiFi captive portal + mining protocol
- [x] On-chain messaging
- [x] SRAM PUF hardware binding (RTC NOINIT, cold-boot only)
- [x] WOTS+ quantum-resistant signatures
- [x] QR payment system + GPIO relay control
- [x] Quarry rate-limiting system (15%/week)
- [x] Autonomous device agent (rule engine + LLM endpoint)
- [x] Energy harvesting positioning (whitepaper + website)
- [x] Live hashrate tracking on node (`/api/v1/miners/active`)
- [x] **Real Ed25519** (orlp/ed25519 public domain impl + mbedtls SHA-512)
- [x] Headless firmware for LilyGO T3 (no display dependency)
- [x] PUF dev mode (warm boot re-enrollment, no cold boot needed for flashing)

## 🔨 In Progress
- [ ] Norman to flash Ed25519 build + erase flash for new wallets
- [ ] Norman to flash M5Stack display orientation picker (BTN C to cycle, BTN A to save)
- [ ] Norman needs WiFi setup on both devices after erase_flash

## 📋 Next Up — Build Order

### 1. SX1276 LoRa Driver (point-to-point)
- [ ] Get T3 silkscreen version (V1.6? V1.6.1?) for pin mapping
- [ ] SPI driver for SX1276 register interface
- [ ] Basic TX/RX: send block header, receive nonce back
- [ ] Test with 2 T3 devices
- **Goal:** Prove radio works, 2 devices talk over LoRa

### 2. BLE Wallet Service (GATT)
- [ ] Implement service: `PUBKEY` (read), `ADDRESS` (read)
- [ ] Implement `SIGN` (write/notify) — phone sends tx hash, ESP32 returns signature
- [ ] Implement `SETUP` (write) — WiFi config via BLE
- [ ] Implement `WIPE` (write) — factory reset with button hold
- [ ] BLE bonding + MITM protection
- **Goal:** Phone app can read balance and sign transactions offline

### 3. LoRa Mesh Protocol
- [ ] Packet format: block headers (80 bytes), work templates (~150 bytes), nonce submissions (~64 bytes)
- [ ] Mesh routing: 3+ T3s relay for each other
- [ ] Bridge node logic: WiFi ↔ LoRa header rebroadcast
- [ ] Light client sync: last 1000 headers in flash (80KB)
- **Goal:** Off-grid mining with no internet dependency

### 4. Gateway P2P (TCP gossip)
- [ ] Peer discovery + connection management
- [ ] Block/tx gossip protocol
- [ ] Chain sync (new node catches up)
- [ ] Conflict resolution (fork choice)
- **Goal:** Multiple gateway nodes, network survives single node failure

### 5. Production Hardening
- [ ] Real Ed25519 verified on hardware (sign + verify test vectors)
- [ ] Secure boot + flash encryption (on fresh ESP-WROOM-32 boards)
- [ ] Difficulty retarget algorithm (replace fixed difficulty)
- [ ] Checkpoints for deep reorg prevention
- [ ] Mobile app (Android, BLE + QR scanner)

## 📈 Scaling Ladder — If Quartz Catches On

**The invariant that outranks TPS:** a $3 board must always be able to watch
its own wallet directly. Every scaling decision gets tested against
*"can a Super Mini still confirm its own payment without a server?"*

The bottleneck is not the protocol — it is the node. Machine payments are
~300 bytes; a 1MB block at 30s ≈ 3,000 txs ≈ 100+ TPS, and machine-economy
UX tolerates seconds-level confirmation. The ladder, in order:

### 1. Decentralize + real ledger model (trust work IS scaling work)
- [ ] Postgres-backed address/tx index (retire the in-memory balances dict)
- [ ] Multiple independent nodes with real p2p authority
- [ ] Compiled reference node (Rust/Go port) when Python becomes the ceiling
- **Goal:** no single process is the chain

### 2. Protocol dials
- [ ] Block size / cadence tuning (small txs make this cheap)
- [ ] Mempool admission + fee policy under load
- **Goal:** 100+ TPS without changing the device story

### 3. Physical aggregation (the structural rung)
- [ ] Gateway-batched swarms: N off-grid boards = one on-chain presence
- [ ] Batched sensor logs + pooled mining payouts
- [ ] Relayer Pool economics pay the batching layer
- **Goal:** tx pressure grows slower than device count

### 4. Light proofs for devices
- [ ] Merkle-ized address/tx proofs (SPV-for-Quartz)
- [ ] Headers-only device sync (last-N headers in flash — pairs with the light-client item in the build order)
- **Goal:** a Super Mini VERIFIES its payment instead of trusting a node's JSON

**Growth curve buys time:** adoption arrives as miners first (each adds
hashrate, not transactions). Tx pressure comes from the use-cases — exactly
the gateway-batchable kind.

## 🏗️ Architecture Summary

```
                    ┌─── BLE ──── Phone App
                    │              (wallet, config,
                    │               balance, send)
                    │
     WiFi ESP32s ──┼──→ Gateway ──→ Other Gateways
     (M5Stack)     │    (VPS)       (TCP gossip)
                    │
     LoRa Bridge ──┘
     (T3+WiFi+LoRa+SD)
          │
     LoRa Mesh
     (T3 leaf miners)
```

| Transport | Purpose | Range |
|-----------|---------|-------|
| BLE | Phone ↔ ESP32 wallet | 10m |
| WiFi | ESP32 ↔ Gateway (mining/sync) | 50m |
| LoRa | ESP32 ↔ ESP32 (off-grid mesh) | 2-15km |

### Node Types
| Type | Hardware | Storage | Connectivity |
|------|----------|---------|-------------|
| Gateway | VPS | Full chain (disk) | WiFi + TCP |
| Bridge | T3 + SD card | Full chain (SD) | WiFi + LoRa |
| Leaf | T3 | Headers only (80KB flash) | LoRa only |
| Wallet | M5Stack/T3 | Keys only (NVS) | WiFi or BLE |
