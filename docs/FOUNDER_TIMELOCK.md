# Quartz Founder Timelock Specification

## Overview

All QZ mined by the project founder (Norman Moore) during the first 2 years
after mainnet launch is automatically timelocked. The coins cannot be moved
until the timelock expires. This is enforced by consensus rules, not by
trust.

## Implementation

### Coinbase Timelock Output

When the founder's ESP32 mines a block, the coinbase transaction contains
a special output with a timelock covenant:

```
Output Script (founder coins):
    OP_CHECKLOCKTIMEVERIFY <2_years_from_block_height> OP_DROP
    <founder_pubkey> OP_CHECKSIG

Meaning: "These coins are spendable by founder_pubkey, but ONLY after
block height X."
```

### How It Works

1. Founder registers their ESP32 device normally (birth certificate, attestation)
2. Founder's device mines blocks like any other miner
3. The protocol detects founder's device_pubkey in the coinbase
4. Instead of a standard P2PKH output, the coinbase creates a CLTV output
5. The timelock expiry = current_block_height + 105,120 (≈2 years at 120s blocks)
6. Any attempt to spend these coins before expiry = invalid transaction (rejected by all nodes)

### Key Properties

- **Consensus-enforced:** No special software needed. Every full node validates CLTV.
- **Transparent:** Anyone can see the timelock on the block explorer. The founder's coins and their unlock dates are public.
- **Per-block:** Each block's reward is individually locked. Coins unlock gradually — block 1's reward unlocks first, block 2 next, etc.
- **No admin key:** There is no way to unlock early. No multi-sig override. No emergency key. The CLTV is absolute.
- **Non-custodial:** The founder holds the private key. The timelock only restricts *when* they can spend, not *who* can spend.

### Numbers

| Parameter | Value |
|-----------|-------|
| Block time | 120 seconds |
| Blocks per year | 262,800 |
| Timelock period | 2 years = 525,600 blocks |
| Founder coins per block | 47.5 QZ (same as any miner) |
| Estimated founder total (solo, 1 ESP32, 2 years) | ~4,275 QZ |
| First unlock | Block 525,601 (≈2 years after the block was mined) |

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

Any miner can optionally timelock their own coins by using a CLTV output
in their spend transaction. This could be used for:

- Long-term holders proving commitment
- ESP32 weather stations locking operating funds
- Mesh pool coordinators bonding stake

The protocol supports this generically — CLTV is part of the script language.
