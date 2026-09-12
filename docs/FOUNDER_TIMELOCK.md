# Quartz Founder Timelock Specification

> **Status: IMPLEMENTED 2026-09-12** (P0-1). Consensus rules live in
> `reference-node/quartz/consensus.py` (UTXO `lock_until`, stamped at
> output creation, enforced at spend). Tests: `tests/test_founder_timelock.py`.
> Activation: `QUARTZ_FOUNDER_ADDRESSES` env (comma-separated wallet
> addresses); fresh chains covenant from genesis, existing chains activate
> at the current tip (forward-only — never retroactive). This document
> describes the mechanism as shipped; the original draft's Bitcoin-style
> CLTV script framing was aspirational — Quartz has no script language,
> so the covenant is a consensus-derived per-output spend floor.

## Overview

All QZ mined by the project founder (Norman Moore) during the first 2 years
after mainnet launch is automatically timelocked. The coins cannot be moved
until the timelock expires. This is enforced by consensus rules, not by
trust.

## Implementation

### Coinbase Timelock Output

When the founder's ESP32 mines a block, the coinbase output paying the
founder's wallet address creates a UTXO with a consensus-enforced spend
floor:

```
UTXO.lock_until = block_height + FOUNDER_TIMELOCK_BLOCKS
                 (= block_height + 2,102,400)

Meaning: "These coins are spendable by the founder's key, but ONLY in
blocks at height >= lock_until."
```

Quartz validates WOTS+/Ed25519 signatures rather than scripts, so the
covenant is carried by the UTXO itself (not an output script): every
node stamps `lock_until` when the coinbase output is created and rejects
any transaction spending a locked output before its unlock height.

### How It Works

1. Founder registers their ESP32 device normally (birth certificate, attestation)
2. Founder's device mines blocks like any other miner
3. Consensus detects the configured founder address in the coinbase payout
4. The output's UTXO is stamped `lock_until = height + 2,102,400`
5. Any attempt to spend these coins before expiry = invalid transaction
   (rejected by all nodes — mempool, block validation, and /send all
   refuse locked outputs)
6. Coinbase maturity (100 blocks) rides the same mechanism while the
   covenant is active

### Key Properties

- **Consensus-enforced:** No special software needed. Every full node stamps and validates `lock_until`.
- **Transparent:** Locked balances and unlock heights are public (`/api/v1/address/<addr>` reports `locked_balance_*` + `next_unlock_block`; `/api/v1/info` reports covenant status).
- **Per-block:** Each block's reward is individually locked. Coins unlock gradually — block 1's reward unlocks first, block 2 next, etc.
- **No admin key:** There is no way to unlock early. No multi-sig override. No emergency key. The CLTV is absolute.
- **Non-custodial:** The founder holds the private key. The timelock only restricts *when* they can spend, not *who* can spend.

### Numbers

| Parameter | Value |
|-----------|-------|
| Block time | 30 seconds |
| Blocks per year | 1,051,200 |
| Timelock period | 2 years = 2,102,400 blocks |
| Founder coins per block | same as any miner |
| First unlock of a block mined at height H | H + 2,102,401 (≈2 years later) |

### What This Proves

1. **No pump-and-dump:** Founder literally cannot sell for 2 years
2. **Skin in the game:** Founder is betting the project will be worth something in 2 years
3. **Equal rules:** Founder mines at the same difficulty, same reward, same hardware as everyone else
4. **Verifiable:** Timelock transactions are visible on-chain from day 1

### Comparison

| | Bitcoin (Satoshi) | Quartz (Founder) |
|---|---|---|
| Premine | None | None |
| Dev fund | None | None (dropped to 0%) |
| Founder coins | ~1M BTC (mined honestly) | ~4,275 QZ (mined honestly) |
| Timelock | None (Satoshi never moved coins) | 2-year CLTV (consensus enforced) |
| Founder identity | Anonymous | Public (Norman Moore) |
| Can founder dump? | Technically yes (never did) | **Physically impossible for 2 years** |

Quartz is strictly stronger than Bitcoin here. Satoshi chose not to move
his coins as a social promise. Quartz makes it a mathematical impossibility.

### Future Extension: Community Timelock

Any miner could opt into locking their own coins via the same mechanism
(long-term holders proving commitment, mesh coordinators bonding stake).
That would need a tx-level opt-in flag — consensus-derived covenant locks
today cover founder coinbases only.
