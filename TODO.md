# Quartz Security TODO

**Last updated:** 2026-08-10
**Status:** Pre-mainnet development
**Tests:** 176 passing | 10 git commits

---

## 🔴 Critical (before mainnet)

### 1. Fault Injection Defense
**Problem:** $50 voltage glitching tool can skip the `if (status != OK)` health check branch on ESP32, bypassing entropy validation and burning a predictable low-entropy key.

**Fix:** Positive-logic defense — health check result is baked into key derivation as a required input token. Glitching past the check produces a garbage token → garbage key → unusable. Security doesn't depend on the branch executing correctly.

**Status:** Not started

### 2. Reproducible Builds
**Problem:** Without reproducible builds, the firmware hash in birth certificates is meaningless — nobody can verify the binary matches source. An attacker could compile backdoored firmware and the birth certificate would just show that different hash.

**Fix:** Document exact ESP-IDF toolchain version, compiler flags, environment variables. Anyone with the same toolchain should produce a byte-identical binary.

**Status:** Not started

### 3. OTA Update Protocol
**Problem:** If OTA is ever enabled, a compromised update server can push firmware with a key-extraction backdoor. Every device that updates is permanently compromised (eFuse key exfiltrated during update).

**Fix:**
- USB-only updates (no OTA during mining)
- Physical button hold (5s) required
- Update must be signed by 3-of-5 multi-sig
- BLE re-pair after update (old sessions invalidated)
- Health checks re-run before resuming mining

**Status:** Not started

### 4. Dev Fund Multi-Sig
**Problem:** Dev fund (2.1M QZ) controlled by single key = single point of failure.

**Fix:** 3-of-5 multi-sig with 24h time-locked withdrawals. Community can detect and fork if malicious withdrawal attempted.

**Status:** Not started

---

## 🟡 Important (before scale)

### 5. BLE MITM Defense
**Problem:** Attacker intercepts BLE pairing between phone and ESP32.

**Fix:** 6-digit pairing code displayed on ESP32 screen, must match what phone sees. Display-based verification prevents MITM.

**Status:** Not started

### 6. Clock Manipulation Defense
**Problem:** Forged timestamps enable selfish mining attacks.

**Fix:** Block timestamps must be within ±2 hours of network median. Reject blocks with future-dated or excessively old timestamps.

**Status:** Not started

### 7. Launch Schedule
**Problem:** Solo mining at low difficulty before public launch = effective premine.

**Fix:**
- Announce launch date 4+ weeks in advance
- Start at production difficulty (20), not testnet (12)
- Difficulty floor for first 6 months (can go up, not below launch value)
- First 1,000 blocks: 10% rewards (4.75 QZ instead of 47.5 QZ)
- Public genesis ceremony at specific UTC time
- Attestation required from block 1

**Status:** Designed, not implemented in code

### 8. QZ-IP Governance Process
**Problem:** No formal process for protocol upgrades = backroom deals (SegWit2x problem).

**Fix:**
- Quartz Improvement Proposals (QZ-IPs) — public documents
- 8-week minimum review period
- 90% miner threshold for activation (higher than Bitcoin's)
- Testnet implementation required before mainnet vote

**Status:** Not started

---

## 🟢 Nice to Have (post-launch)

### 9. FPGA Proxy Analysis
**Problem:** FPGA could potentially proxy HMAC requests to ESP32 at higher speed.

**Fix:** Benchmark actual HMAC round-trip latency on real hardware. Quantify the realistic 2-3x gain. May need per-device block frequency cap if higher than expected.

**Status:** Needs real hardware

### 10. Independent Security Audit
**Problem:** We're the developers — we can't audit our own blind spots.

**Fix:** Pay a firmware security firm to review ESP32 code, protocol design, and consensus rules before mainnet. Budget $20-50K.

**Status:** Not started

### 11. Formal Legal Opinion
**Problem:** Without a securities lawyer letter, QZ's regulatory status is uncertain.

**Fix:** Engage crypto-focused securities lawyer for opinion letter on QZ's status (commodity vs security vs currency). Budget $5-15K.

**Status:** Not started

### 12. LLC/Nonprofit Registration
**Problem:** Personal liability for project creators.

**Fix:** Register Quartz as LLC or nonprofit for liability separation.

**Status:** Not started

---

## 📋 Non-Security Pending

- **GitHub push** — needs `gh auth login` from Norman to create `saasclawai-org/quartz` repo
- **Actual ESP32 hardware benchmarks** — compile firmware on real ESP-IDF toolchain, measure hashrate
- **ESP32 firmware binary** — for website flasher (currently no binary, flasher shows instructions)
- **Terms page wiring** — terms.html exists but footer link on main page needs to be verified on all pages
- **Aider integration for PVC** — separate project
- **Testnet attestation enforcement** — testnet currently simulates mining, no device registry enforcement

---

## ✅ Completed Security Measures

| Threat | Defense | Status |
|--------|---------|--------|
| GPU dominance | CrystalHash v2 (eFuse HMAC in hash loop) | ✅ Implemented |
| Supply chain (reseller key theft) | Birth certificates + tampering detection | ✅ Implemented |
| RNG degradation (Coldcard bug class) | 5-layer entropy defense + NIST 800-90B | ✅ Implemented |
| Server-side compromise | Non-custodial (keys never on server) | ✅ By design |
| BTCPay-style auth bypass | No server-side 2FA to bypass | ✅ By design |
| $5 wrench attack | Hardware wipe (3s button hold) | ✅ Implemented |
| Eclipse attack | LoRa cross-check (physical radio) | ✅ By design |
| Quantum attacks | 128-bit post-quantum margin (SHA-256 + Ed25519) | ✅ By design |
| Pre-mainnet instamine | Launch schedule with difficulty floor + reduced rewards | ✅ Designed |
| Centralized mining pools | Mesh pools (rotating elected coordinator, 0% fee) | ✅ Implemented |
| Selfish mining | Timestamp rules + block withholding detection | ⚠️ Designed, not coded |
| Fork prevention | Hardware binding to genesis + adaptive block sizing | ✅ By design |
| Fork governance | QZ-IPs, 8-week review, 90% threshold | ⚠️ Designed, not coded |

---

## Test Summary

| Module | Tests | Status |
|--------|-------|--------|
| Blockchain (blocks, txs, merkle, difficulty) | 25 | ✅ |
| Crypto (BIP39, SLIP-0010, Ed25519, Base58) | 33 | ✅ |
| Storage (FRAM, SPV/PRUNED/FULL, ring buffer) | 22 | ✅ |
| Attestation (device registry, slashing, anti-cheat) | 21 | ✅ |
| Mesh pools (election, shares, reward split) | 24 | ✅ |
| Supply chain (birth cert, reseller attack, clone detection) | 19 | ✅ |
| Entropy (NIST 800-90B, triple-mix, Coldcard scenario) | 32 | ✅ |
| **Total** | **176** | **All passing** |
