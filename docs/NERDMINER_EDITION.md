# NerdMiner × Quartz — Design Paper

**Status:** paper only — no code written against this yet. Sequenced behind C3 bring-up and the reward mechanism.
**Date:** 2026-08-15

---

## 1. One-liner

NerdMiner firmware, unchanged lottery mining, plus a grafted Quartz identity:
the chip itself becomes a sybil-proof enrolled device on the Quartz network.

> "Your lottery miner now also earns QUARTZ — sybil-proof because the chip
> itself is the identity. One chip, one identity, no extra hardware."

## 2. Why this wedge (the numbers)

| Fact | Number |
|---|---|
| NerdMiner_v2 GitHub stars | 2,763 (613 forks, active June 2026) |
| Boards supported by nerdminer.io web flasher | 45+ |
| Community build videos (aggregate views) | millions |
| Quartz roadmap gate for exchange listings | 100 enrolled devices |

**Funnel math:** conservatively assume 10k active NerdMiner devices in the wild.
A 1% enrollment conversion = 100 devices = the roadmap gate, hit with zero
hardware sales. Optimistic (5% on the back of a good community response) =
500 devices. Pessimistic (0.2%) = 20 devices, which still beats zero and
costs one weekend.

This audience already: owns the exact silicon (ESP32, ESP32-S3), flashes miner
firmware for fun, and tolerates ~zero expected earnings. The marginal ask
("also enroll this chip") is small and free.

## 3. Product shape (user experience)

1. User visits **our** web flasher page (we already host esptool-js for v055)
   and flashes "NerdMiner Quartz Edition" — same flow they know.
2. Mining works exactly as before (stratum → public-pool.io, their screens,
   their WifiManager — we reuse their stored WiFi credentials).
3. New menu item / status line: **Quartz ID**.
   - `Enroll` → PUF keygen → HTTPS enrollment → device shows its short ID +
     QR (they already have QR rendering in some builds; we have quartz_qr).
   - Thereafter: a lightweight attestation heartbeat rides their existing
     WiFi connection (stratum is always-on; heartbeat is a few hundred bytes).
4. Display shows `Quartz: enrolled ✓` (or `dormant`) on one of their screens.

**Rejected alternative:** dual-boot (ota_0/ota_1, bootloader menu). Rejected
because it doubles flash footprint, complicates the web flasher, and splits
the device's attention. The graft is strictly better for the funnel: the
Quartz identity is ambient, not a mode you boot into.

## 4. Architecture — what ports, what stays home

NerdMiner is Arduino-ESP32 / PlatformIO; Arduino sits on ESP-IDF, so our
ESP-IDF C code drops in as a PlatformIO component.

**Ports over (from `firmware/main/`):**
| Module | Notes |
|---|---|
| `quartz_puf.c` | SRAM PUF + fuzzy extractor. RTC_NOINIT_ATTR works under Arduino-ESP32 unchanged. |
| `ed25519-lib/` | vendored, build-system-agnostic C |
| `quartz_attest.c` (device_id derivation + attestation builder) | trimmed of our display/mining hooks |
| enrollment client (HTTPS) | ~200 lines, mbedtls already present in Arduino-ESP32 |

**Stays home:** scratchpad miner (their miner does BTC hashing), our display
stack, LoRa, agent, pay — none of it needed.

**The one real technical subtlety — PUF capture timing.** The PUF reads RTC
FAST SRAM power-on state; it must run before anything writes that region.
In our IDF firmware this is the first thing in `app_main()`. In Arduino,
the equivalent is a global constructor (`__attribute__((constructor))`) or
the very first lines of `setup()` — before `Serial.begin()`, before WiFi.
The cold-boot marker pattern (`g_puf_cold_marker`) already distinguishes
power-on from deep-sleep/soft-reset, so the port is mechanical, but it
**must be validated per chip variant** (see §7).

## 5. Registry / chain side

- Enrollment + attestation endpoints: partially exist in `reference-node/`;
  need a public-facing endpoint + auth policy. **This is the real
  prerequisite, not the firmware.**
- Registry gains a `device_class` field: `quartz_esp32` (our hardware) vs
  `nerdminer_esp32` / `nerdminer_esp32s3` (their hardware). Same
  one-identity-per-chip rule; class tag enables weighting if rewards ever
  go proportional (see WHITEPAPER §sybil-resistance: equal-per-identity
  makes hashrate variance cosmetic).
- Reward policy assumed for this paper: **equal share per enrolled,
  attest-keeping identity.** No hashrate component — their miner's BTC
  hashrate is irrelevant to Quartz issuance, by design.

## 6. Sequence & gating (nothing starts before its gate)

1. **[GATE: now]** C3 bring-up when Norman's boards arrive — proves the
   portability path, exercises multi-target firmware.
2. **[GATE]** Reward mechanism defined + enrollment endpoint live. A Quartz
   identity that earns nothing is a button nobody presses.
3. **[GATE]** S3 PUF validation — their flagship board is the T-Display S3
   (ESP32-S3, 16KB RTC FAST). Our PUF is proven on classic ESP32 and
   compiles for C3; S3 needs one ~$10 board and an afternoon.
4. Fork, graft, dogfood on our own devices (target: 3 internal units
   enrolled before any public post).
5. Our flasher page live; then the community pitch (§8) + PR to
   nerdminer.io's flasher list.

## 7. Risks & honest unknowns

- **Support burden across 45 board variants** — mostly neutralized: PUF
  enrollment is board-agnostic and headless; their display stack is theirs.
  The only variant-sensitive code is the PUF constructor (§4), which is
  chip-family (not board) specific: 3 families = ESP32, S3, C3.
- **Community reception to "another token"** — mitigated by tone: this is a
  free add-on to a toy miner, not a yield promise. Never lead with earnings.
- **Reward obligation** — once QUARTZ is promised to enrollees, the emission
  schedule is a commitment. That's why rewards are a gate, not an afterthought.
- **Upstream drift** — NerdMiner is actively developed (June 2026 commits).
  Graft should be a thin overlay branch rebased on their releases, not a
  hard fork; keep our diff under ~1k lines so rebases stay cheap.
- **License** — NerdMiner is **MIT**; Quartz is **AGPL-3.0**. Compatible:
  we keep their MIT notices and license our additions AGPL. No copyleft
  friction either direction. (Earlier assumption of "GPL family" was wrong.)

## 8. The pitch (draft for community post — wordsmith before shipping)

> Your NerdMiner is already a lottery ticket. Now it can also be something
> rarer: a cryptographic identity. Quartz Edition adds a hardware-rooted ID
> to your miner — generated by the silicon itself (SRAM power-on fingerprint,
> the same tech Synopsys sells to SoC vendors), unclonable, one per chip.
> Enroll once, your device attests on its own, and it earns QUARTZ for as
> long as it stays honest. No extra hardware. No keys to back up. Flash,
> enroll, forget. Same lottery mining you already run.

Flasher-listing blurb (nerdminer.io):
> **NerdMiner Quartz Edition** — adds a sybil-proof hardware identity +
> QUARTZ rewards to your lottery miner. Same NerdMiner you know.

## 9. Cost estimate

| Item | Cost |
|---|---|
| Fork + graft + UI item (after gates) | ~1 weekend |
| S3 validation board | ~$10 |
| Enrollment endpoint hardening | ~2–3 days server-side |
| Our flasher page variant | ~2 hours (reuse v055 page) |

---

## Open decisions (Norman)

1. Reward policy: confirm **equal share per identity** (no hashrate component)?
2. Order an ESP32-S3 board now so gate #3 is cleared when the C3s arrive?
3. Edition naming: "NerdMiner Quartz Edition" vs "Quartz ID for NerdMiner"?
4. Do we want our own discord/thread presence before the community post,
   or route all support through GitHub issues?
