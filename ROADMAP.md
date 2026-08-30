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
- [x] Payment privacy v1 (reference node) — address streams, payment-channel bundles, watch-only audit export
- [x] OTS slot-reuse rejection + real WOTS+ verification in consensus tx validation

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

## 🔐 Payment Privacy — Plain English

**The rule: the chain is a notary, not a vault.** Use it to prove,
timestamp, and trigger. Keep the secrets' bodies off it.

### What's public — on purpose
Every payment shows which-address-paid-which-address, how much, and when,
forever. That's the price of a $3 Super Mini checking its own money without
trusting anyone's server: what everyone can verify, everyone can see.
Addresses are pseudonyms — but pseudonyms leak the moment they're reused.
Reuse one twice and strangers start assembling your customer list, your
price sheet, and your volume chart.

### Move 1 — Never reuse an address (done)
Every payment lands on a brand-new address that has nothing in common with
any address you've used before. They all grow from one secret seed, so one
backup still recovers everything — but on the public chain your payments
look like strangers passing cash. The reference-node wallet now works this
way (`StreamWallet`): fresh address on demand, rotation built in.

### Move 2 — Payment lanes (address bundles)
A utility paying 500 devices. A dairy paying a carrier every week. At
enrollment — QR code, contract signing, device setup — the receiver hands
the payer a list of future addresses, say the next 100. The payer works
down the list in order. The list carries addresses only, no keys, so the
payer can pay but can never steal. The world sees 100 unrelated addresses;
the two parties see one relationship. Hand the same list to an accountant
or insurer and they can watch every payment — and sign nothing. That's the
whole view-key story: private to the world, transparent to whoever you
choose.

### Move 3 — Your own devices (shared-secret streams)
When both ends are yours — sensor and your wallet — one shared secret
derives the same address list on both ends. Great for your own hardware
and for data commitments. Never for receiving from strangers: anyone
holding the secret can spend from that stream. From strangers, accept
bundles only.

### What stays visible — deliberately
Amounts and timing stay public. Hiding amounts ("confidential transactions")
is heavy cryptography — the one thing most likely to break "$3 board watches
its own wallet directly" — so it stays out until the hardware says otherwise.

### Data on chain: anchor, don't store
The 256-byte tx data field is for hashes, tiny commitments, short messages.
A machine that must prove what happened without publishing it writes only a
hash while things are quiet and reveals the real readings only if there's a
dispute. Every stored byte is a byte every $3 board must sync past — and
once written, it is there forever, unmoderatable. Keep payloads small and
priced so abuse is uneconomical.

**Status:** reference node done — `StreamWallet`, `PaymentChannel` bundles,
watch-only audit export, 17 new tests. Next: same derivation in the web
wallet, then bundle UX in the phone app and gateway APIs.

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
