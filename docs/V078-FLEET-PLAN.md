# Quartz v078 — Fleet Stability & Setup Plan

**Date:** 2026-08-29
**Source:** live fleet bring-up day — 3 boards (2× C3 Super Mini, 1× LilyGO T3 classic ESP32), 5 bricked states, 1 stranded wallet, mesh validated end-to-end.
**Goal:** stable testnet miners + documented setup → clear demo/use case (§D).

## What today proved (in the field, not on the bench)

- **Mesh work distribution**: link-less board pulls paid work from a connected peer (`Got work via mesh (pays us)`)
- **Mesh block submission**: 3 blocks relayed over ESP-NOW, accepted on-chain (42 QZ each)
- Portal provisioning, Pi-node topology, boot-time channel discovery all work
- Every failure mode below is field-observed, not hypothetical

## A. Firmware (v078)

### A1. Mining-mode console: `wifi` + `node`
- **Problem:** `wifi` exists only in the seed-confirm wait loop (main.c ~699). The mining dispatcher (pin/setpin/pinstatus/meshscan/meshch/send) has no wifi/node command. A mistyped node URL today required `esptool erase_region 0x11000 0x4000` + full portal re-provision — which also rolls the wallet.
- **Fix:** add to the mining dispatcher:
  - `wifi` — wipe `qz_wifi` (ssid/pass/node) → reboot to portal (same behavior as the seed-loop version)
  - `node` — print current endpoint; `node <host[:port]>` — update NVS `node` live and reconnect
- **Acceptance:** a typo fix is one console command. No flash, no wallet wipe.

### A2. On-device `recover <12 words>`
- **Problem:** console stub prints "not yet fully implemented — use app". A stranded wallet (420 QZ, 2026-08-29) has no on-device recovery path.
- **Fix:** parse seed → derive address → re-init wallet NVS → require seed re-confirm.
- **Acceptance:** recovery from wiped NVS without the Android/web wallet.

### A3. Seed-confirm must be unmissable
- **Problem:** a fresh wallet waits for confirmation with **no timeout** and a quiet banner. Field result: board "up" (WiFi connected, mesh active) but silently not mining for 40+ minutes.
- **Fix:**
  - LED blink pattern while awaiting confirm
  - Repeating banner every 10s: `⏳ WALLET NOT CONFIRMED — type 'confirm' + Enter (or hold BOOT 3s). Mining will not start until confirmed.`
  - Portal page shows a confirm step for phone-first setups
- **Acceptance:** no reachable state where the board is "up" but silently not mining.

### A4. Seed backup enforcement
- **Problem:** the seed is displayed once; confirm is a single word — nothing verifies the words were recorded. Field cost: 420 QZ stranded.
- **Fix:** at creation, require typing one randomly-chosen seed word back (serial or app) before confirm completes.
- **Acceptance:** a confirmed wallet implies a recorded seed.
- **Design note:** conflicts with the headless BOOT-hold confirm — gate behind a build flag or make it first-boot-only. Decide deliberately.

### A5. Mesh polish
- **ROOT CAUSE FOUND 2026-08-29 15:37 (peer flap)**: `on_recv` stamps `last_seen = xTaskGetTickCount()/pdMS_TO_TICKS(1000)` (tick clock, seconds since boot), but the sweeper `quartz_mesh_step(uptime)` receives `uptime = esp_timer_get_time()/1e6 - s_start_time` (main.c:1082 classic / :999 c3) — **a different clock AND a different epoch** (s_start_time set when mining starts, main.c:911/835). The unsigned subtraction `uptime - last_seen` underflows or skews ≥120s → the 120s timeout fires seconds after a fresh HELLO → peer dropped → next HELLO re-adds ("Peer discovered" only prints for NEW entries) → infinite discover/timeout cycle. Observed 0.4–48s cycles all day on both boards.
- **Fix (v078)**: one clock — compute `now` inside `quartz_mesh_step` with the same `xTaskGetTickCount()` formula `on_recv` uses; ignore the caller's uptime. Also: don't `esp_now_del_peer` on timeout (direct sends fail while de-registered — work-serving/relays become timing-lucky); populate HELLO `height` field (always 0 today); fix 0 H/s estimator on mesh work.
- **Acceptance:** two v078 boards in range show stable peer entries with real heights.

### A6. Board identity (optional, cheap)
- **Observed:** fleet debugging required wallet-address forensics via server logs.
- **Fix:** `name <text>` stored in NVS, printed in boot banner, included in mesh beacon payload.
- **Acceptance:** `Peer discovered: desk-heltec (caps=0x03 height=7514)`.

## B. Flash safety & docs

### B1. Flash doctrine (documented; enforce everywhere)

| Situation | Command | Preserves |
|---|---|---|
| Update a running board | app-only: `write_flash 0x20000 quartz-app-vXXX[-c3].bin` | wallet, WiFi, node, PIN |
| Revive a bricked board (bootloader intact) | same app-only flash | wallet, WiFi, node, PIN |
| New/empty board, or dead bootloader | merged: `write_flash 0x0 quartz-<chip>-merged-vXXX.bin` | **nothing — wipes WiFi + wallet on C3** |

- Root cause (C3): the merged image's 0xFF padding overlaps the default NVS partition at 0x11000, and the C3 build stores the wallet in default NVS. **Assume merged wipes on all boards.**
- **Web flasher:** WebSerial aborts mid-write leave erased bricks (3 observed on one Windows machine in one day). Manifests are already hardened (no erase offer, v077, no-cache). Docs steer flaky-USB/Windows users to the Python scripts.

### B2. Flash scripts
- flash_c3.py / flash_esp32.py: print an explicit `FULL IMAGE — resets WiFi + wallet` warning before writing.
- Done 2026-08-29: plug-detection fix (a replugged known port now registers), versioned-filename glob.

### B3. Setup guide v2 (shipped 2026-08-29)
- Rewritten around: flash doctrine, seed-confirm prominence, node URL guidance, healthy-boot timeline, troubleshooting matrix (10 field-observed failure modes), honest per-state serial command list. Live at quartzchain.net/setup.html.

## C. Release gates

1. **Soak:** 7 days, ≥2 boards, zero console interventions, blocks flowing. Start: 2026-08-29 12:10 UTC (both v077 boards mining).
2. **Stranger test:** a person who didn't build this goes bare-board → mining using only setup.html. No chat help.
3. **S3 parity:** S3 is still v074 app / v073 merged. Build v078 for S3 (Heltec V3, LilyGO S3 variants).

## D. Demo track (parallel — hardware exists today)

**"The $3 off-grid miner"**

- C3 Super Mini + USB battery pack (+ optional small solar panel) — **no WiFi, no node, no internet**
- It joins the mesh, pulls work from a connected peer, mines; its blocks are relayed and paid on-chain
- Kill-the-router moment mid-demo; close on the explorer with payouts landing
- Tie-in pages already on the site: energy.html, ev-charging.html, cold-chain.html — "a device that earns its own keep"
- Extension after S3/LoRa ships: km-range off-grid (Heltec V3 / LilyGO T3 + LoRa stack)

## Appendix — 2026-08-29 incident log (compressed)

| Time (UTC) | Event | Outcome |
|---|---|---|
| 09:46 | COM3 boot loop `invalid header 0xffffffff` (empty flash) | recovered via flash_c3.py + merged (stale v076 local copy) |
| 10:15 | Web flasher app-update on COM3 aborted mid-write | app slot erased; wallet intact; recovered later (app-only) |
| 10:31 | Web flasher app-update on LilyGO aborted mid-write | ROM-level brick; recovered via direct esptool merged |
| 11:07 | LilyGO mesh-mining with broken node URL | **first mesh work distribution proof** |
| 11:24 | COM5 flashed with merged v077 | **wallet+WiFi wiped** (doctrine learned; 420 QZ stranded pending seed) |
| 11:45 | 3 blocks relayed via mesh, paid on-chain | **first mesh block submission proof** |
| 11:56 | COM5 "up but not mining" | seed-confirm no-timeout trap; fixed with `confirm` |
| 12:08 | Full fleet mining v077 (~52 H/s combined) | soak started |
