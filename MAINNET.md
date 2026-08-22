# MAINNET.md — Definition of Mainnet & Launch Checklist

**Status: DRAFT** — this document defines what "mainnet launch" means for Quartz
and tracks readiness against it. It is deliberately honest: no bars are met by
assuming, every bar is checkable, and the network bars (which only other people
can satisfy) are separated from the engineering bars (which we can do ourselves).

A date is chosen only after every bar below is green. Mainnet is launched when
the definition is met, not when a calendar says so.

---

## 1. Definition of mainnet

Quartz mainnet is live when **all** of the following hold simultaneously:

1. **D1 — Independent security.** ≥ 20 independently-owned hardware miners
   (ESP32s owned by ≥ 15 different people) actively mining, and no single party
   (including the founder) controls enough hashrate to reorganize the chain at
   will. Operational proxy: founder share of blocks over any 7-day window < 40%.
2. **D2 — Independent infrastructure.** ≥ 4 publicly reachable, continuously
   synced nodes run by ≥ 3 different parties on ≥ 2 different hosting
   providers/networks. At least one must be on residential/home connectivity
   (proves the mesh is not cloud-dependent).
3. **D3 — No simulator.** The block-simulator fleet (`DEMO_MINERS`) is not
   running anywhere on mainnet. Every block comes from real hardware. Every
   coinbase pays a real device wallet.
4. **D4 — Consensus rules frozen and exercised.** Founder timelock, difficulty
   retarget, and reorg rules are in the code, tested, and have been exercised
   on a public dry-run testnet for ≥ 2 weeks without a surprise fork or stall.
5. **D5 — Genesis ceremony completed and documented.** Genesis parameters
   frozen, genesis block hash published, chain launched from multiple nodes
   simultaneously, ceremony write-up in this repo.

## 2. Where we are today (2026-08-22 snapshot)

| Area | State |
|---|---|
| Chain, consensus, p2p sync | ✅ Working, field-verified (first user-Pi-mined block 2026-08-22) |
| Gateway mining + coinbase payouts | ✅ Live (v2 coinbase pays device wallet in-block) |
| Reorg machinery (cumulative work, Path B) | ✅ Implemented; ⚠️ not yet exercised under adversarial load |
| Wallets (web + Android), explorer, docs | ✅ Working |
| One-tarball Pi node | ✅ Working, deployed in the field |
| Test suite | ✅ 289 tests passing (consensus 29, crypto 33, difficulty 12, attestation 21, …) — mostly happy-path |
| Founder timelock | ❌ Spec only (`docs/FOUNDER_TIMELOCK.md`) — not implemented |
| Adversarial/hardening tests | ❌ Not started |
| Difficulty retarget at near-zero hashrate | ⚠️ Logic exists (±1-bit clamp, 25% max change) but never dry-run with the simulator off |
| Independent nodes | ❌ 1 (seed, founder-controlled) + 1 (founder's Pi) |
| Independent miners | ❌ 1 hardware miner (founder's ESP32) |
| Simulator on testnet | ⚠️ Yes — most testnet blocks are the demo fleet |

**Blunt summary:** the software is ~4 engineering weeks from launch-ready.
The network is the long pole: D1/D2 require 15+ people we don't control yet.

## 3. Engineering checklist (P0 = launch blockers)

### P0-1 Implement founder timelock (spec → consensus rule)
- [ ] Coinbase covenant output: founder-mined coins timelocked 2 years
      (per `docs/FOUNDER_TIMELOCK.md`)
- [ ] Consensus validation: reject blocks that pay founder without the covenant;
      reject spends of timelocked outputs before expiry
- [ ] Dev-fund handling decided and coded (static 5,724.5 QZ today; where does
      it live on mainnet — same covenant? separate keys?)
- [ ] Tests: timelock honored across reorgs; expiry unlocks
- Est: 3–5 days. **Must exist at height 0 — cannot be retrofitted.**

### P0-2 Adversarial test pass
- [ ] Invalid-PoW / malformed block floods (fuzz block headers, txs)
- [ ] Timestamp drift attacks (future blocks, backwards timestamps)
- [ ] WOTS+ one-time signature: replay attempts, index reuse, state exhaustion
- [ ] Double-spend across reorgs (Path B under load; MAX_REORG_DEPTH boundary)
- [ ] Difficulty edge cases: zero/near-zero hashrate, sudden hashrate exit,
      retarget at boundaries
- [ ] p2p: hostile peer serving bad chains, snapshot poisoning
- Est: 1–2 weeks. Findings feed fixes; serious findings reset the timer.

### P0-3 Simulator-off dry run (public testnet phase 2)
- [ ] New testnet with `DEMO_MINERS` disabled network-wide; real hardware only
- [ ] Difficulty retarget observed converging with real (low) hashrate
- [ ] ≥ 2 weeks continuous, no manual chain surgery
- Est: 2 weeks calendar (runs in parallel with P1 work)

### P0-4 Genesis ceremony
- [ ] Freeze all chain params (supply, halving, block time, difficulty floor,
      timelock, checkpoint policy) in a GENESIS.md
- [ ] Generate genesis block from committed, public inputs; publish hash
- [ ] Bootstrap ≥ 4 seed nodes simultaneously from genesis
- [ ] Ceremony write-up (who, when, inputs, hash, witnesses)
- Est: 2–3 days incl. documentation

### P1 — strongly recommended, not strictly blocking
- [ ] External audit of PUF enrollment + CrystalHash (can start pre-launch,
      report post-launch)
- [ ] Wallet: fee estimation + UX for mempool-less environment
- [ ] Explorer: independent instance (not only the seed's)
- [ ] Tooling: chain-state export/verify (prove balances from raw blocks)
- [ ] Docs: RECOVERY.md dry-run on mainnet params

## 4. Network bars (community, not code)

- [ ] **20+ hardware miners owned by 15+ people.** Mechanism: get the Pi-node +
      $5-board story in front of hobbyist communities (the node.html page and
      one-tarball install are the funnel). Track via distinct coinbase payout
      addresses (v2 coinbase makes this trivially measurable).
- [ ] **4+ public nodes, 3+ operators, 2+ providers, 1+ residential.**
      Mechanism: ask Pi-bundle users to opt into the public peer list
      (Cloudflare Tunnel path is already documented).
- [ ] **Founder block share < 40% over 7 days.** Measured from chain data;
      published openly (this number is the honest decentralization gauge).
- [ ] A "friends of the chain" group of ≥ 5 people who have flashed, mined,
      and run a node end-to-end without our help (proves the docs).

## 5. Launch sequence (once all bars are green)

1. Freeze code: tag `mainnet-candidate`, cut release binaries (esp32, esp32s3,
   c3), final 289+ test run + adversarial suite green
2. ANNOUNCE: 2-week countdown, genesis hash published in advance
3. Genesis ceremony (P0-4) → mainnet chain starts
4. Seed nodes serve genesis; miners point portals at them
5. Founder devices mine **with timelock covenant active** from block 1
6. Post-launch: weekly published stats (blocks by owner-class, node count,
   founder share) — transparency as policy

## 6. Explicitly NOT launch blockers

- L2/payment channels (`docs/L2.md`) — post-launch
- LoRa mesh mining — post-launch, mainnet-compatible if consensus unchanged
- Exchange listings — never a launch criterion
- Perfect docs/polish — good enough is good enough; correctness is not negotiable

---

*This document is updated as bars are met or scope changes. Last update:
2026-08-22.*
