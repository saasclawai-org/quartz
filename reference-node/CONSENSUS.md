# Quartz Consensus Engine Specification

**Version:** 1.0
**Status:** Draft
**Date:** 2026-08-15

## 1. Overview

Quartz uses a Proof-of-Work consensus with hardware-bound mining (CrystalHash v2).
Blocks require both a valid PoW hash and a valid attestation signature from a
registered ESP32 device. This document specifies the consensus rules that every
full node must enforce.

### Design Principles

1. **Hardware-bound:** Every block must be co-signed by a registered ESP32.
   PoW alone is insufficient — the attestation signature is consensus-critical.
2. **UTXO model:** Quartz tracks unspent transaction outputs, not account
   balances. This prevents double-spends at the consensus layer.
3. **Longest work chain wins:** Fork choice is by cumulative chain work,
   not block count. Ties broken by first-seen timestamp.
4. **Finality via checkpoints:** Hardcoded checkpoints prevent deep reorgs
   on young chains. Checkpoints are consensus-critical.

## 2. Data Structures

### 2.1 UTXO Set

The UTXO set maps `(txid, output_index)` → `(amount, script_pubkey, height)`.

```
UTXO = {
    txid: bytes[32],
    index: int,
    amount: int,          # quartz-sats
    script_pubkey: bytes[32],
    created_height: int,
}
```

The UTXO set is updated atomically with each block:
- **Add:** outputs of every valid transaction in the block
- **Remove:** inputs (spent outputs) of every non-coinbase transaction

### 2.2 Mempool

The mempool is a set of valid, unconfirmed transactions ordered by fee-per-byte.

```
MempoolEntry = {
    tx: Transaction,
    fee_sats: int,
    size_bytes: int,
    fee_per_byte: float,
    received_at: float,
    depends_on: list[bytes],   # txids this tx spends (must be in mempool or chain)
}
```

### 2.3 Chain State

```
ChainState = {
    tip_hash: bytes[32],
    height: int,
    utxo_set: dict,
    chain_work: int,           # cumulative difficulty
    current_difficulty: int,   # bits
    mempool: dict,
    known_miners: set,
    checkpoints: dict,
}
```

## 3. Transaction Validation

### 3.1 Transaction Structure

A valid transaction has:
- `version` = 1
- `inputs`: list of (prev_txid, output_index, signature, pubkey)
- `outputs`: list of (amount, script_pubkey)
- `locktime`: 0 (future: timelock)
- `data`: optional message (max 256 bytes)

### 3.2 Validation Rules

A transaction is valid if ALL of:

1. **Well-formed:** Can be deserialized without error
2. **Non-empty:** Has at least one input and one output (coinbase exempt)
3. **No zero outputs:** Every output amount > 0
4. **No overflow:** Sum of outputs ≤ sum of inputs + coinbase (if coinbase)
5. **Input exists:** Every input references a UTXO in the current set
   (coinbase exempt — it creates new coins)
6. **No double-spend:** No two inputs in the same tx reference the same UTXO
7. **Signature valid:** Ed25519 signature over (txid || input_index) verifies
   against the pubkey, and the pubkey matches the script_pubkey of the
   referenced UTXO
8. **Fee non-negative:** sum(inputs) - sum(outputs) >= 0
9. **Data limit:** len(data) <= 256
10. **Locktime:** locktime == 0 (no timelocks in v1)

### 3.3 Coinbase Special Rules

The coinbase transaction (always tx index 0 in a block):
- Has exactly 1 input with prev_txid = 0x00*32 (null)
- Output amount = block_reward(height) + sum(fees from all other txs)
- script_pubkey = SHA256(miner_id)
- Not spendable until 100 confirmations (coinbase maturity)
- Signature field is used for PUF attestation, not a real signature

### 3.4 Mempool Acceptance

A transaction enters the mempool if:
1. It passes all validation rules above (using current UTXO set)
2. Its inputs are not already spent by another mempool tx (unless that tx
   is being replaced by a higher-fee tx — RBF not supported in v1)
3. It is not already in the mempool (same txid)
4. Size ≤ 4KB (prevent block stuffing)
5. Fee ≥ 1 sat/byte (minimum relay fee)

## 4. Block Validation

### 4.1 Block Structure

A valid block has:
- 80-byte header (version, prev_hash, merkle_root, timestamp, difficulty, nonce)
- 6-byte miner_id (stored after header, not part of the hashed header)
- Transaction count (1 byte) + transactions

### 4.2 Validation Pipeline

When a block arrives, validate in order:

1. **Structural:** Can deserialize, header is 80 bytes, has ≥ 1 tx
2. **PoW:** `block_hash < target(difficulty_bits)` — SHA-256 of header
3. **Chain linkage:** `prev_hash == current_tip_hash`
   (if not, it's an orphan or a fork — see §5)
4. **Difficulty:** `difficulty_target == expected_difficulty(height)`
   — matches retarget calculation for this height
5. **Timestamp:** `|timestamp - now| < 2 hours` (median-time-past in v2)
6. **Merkle root:** Recomputed merkle root matches header
7. **Coinbase:** First tx is a valid coinbase for this height
8. **Reward:** Coinbase output = block_reward + total_fees (no off-by-one)
9. **Transaction validation:** Every non-coinbase tx passes §3.2
10. **No double-spend:** No two txs in the block spend the same UTXO
11. **Attestation:** (When hardware attestation is enforced)
    Block includes valid Ed25519 signature from registered device
12. **Checkpoint:** If height has a checkpoint, hash must match

If any check fails, the block is REJECTED and not stored.

### 4.3 Reward Calculation

```
total_reward = block_reward(height)      # from halving schedule
total_fees   = sum(fees of all non-coinbase txs)

coinbase_output = total_reward + total_fees

split (pre-mesh-fork):
  miner   = 100% of total_reward   (42 QZ Era-1 base)
  relayer = 0   (design preserved, deferred — see Emission Decision)
  quantum = 0   (dropped)
```

Overpayment is REJECTED; underpayment is allowed (a miner may burn part of
the reward). Historical note: no block has ever paid above the schedule —
verified across all 20,194 genesis-to-#19417-era blocks when the decision
was adopted.

### 4.3.1 Emission Decision (2026-08-15)

Decided on testnet, carried into mainnet genesis:

1. **Base emission = 42.5 QZ/block** (Era 1), halving every 210,000 blocks.
   The previous 50 QZ nominal schedule minted only 42.5 in practice because
   neither pool had recipients; the schedule now states reality.
   *(Superseded next day — see §4.3.2.)*
2. **Quantum Security Pool (5%): dropped.** No on-chain quantum tax. The
   WOTS+ migration mission continues as an engineering roadmap item funded
   by transparent donations/audits, not by protocol emission.
3. **Mesh Relayer Pool (10%): design preserved, payment deferred.** The
   share activates only via a future hard fork, and only when (a) a real
   LoRa mesh fleet carries live traffic and (b) relay-share accounting is
   validated in-block (fake-relay-work and self-dealing resistance). Until
   both exist, paying the share would be minting to an unspendable or
   centrally-controlled address — worse than not paying it.
4. **No early-adopter 2× bonus** — removed earlier (it violated coinbase
   validation); blocks pay exactly the schedule + fees.

### 4.3.2 Supply Retune (2026-08-16)

Base emission set to **42 QZ/block, halving every 500,000 blocks** →
total supply lands **exactly 42,000,000 QZ** (42 × 500,000 × 2).

- The 42.5/210k interim summed to ~17.85M; the paper's 42M never matched
  any schedule. This closes the last emission paper-vs-chain drift.
- It is a 1.2% trim, not an expansion.
- Applied before the first halving, so no historical halving boundary moved.
- Historical note: blocks 0 to ~20,250 paid 42.5 QZ under the interim
  schedule. They are honored as-loaded (the loader does not re-validate
  historical coinbases; reorg windows never reach back that far).
- At the future mesh fork the split becomes 37.8 QZ miner / 4.2 QZ relayer.

## 5. Fork Choice & Reorgs

### 5.1 Fork Choice Rule

The valid chain with the **most cumulative work** is the tip.

```
chain_work(block) = chain_work(parent) + work(difficulty_bits)

work(bits) = target_to_work(bits_to_target(bits))
         = 2^256 / (target + 1)
```

When a new block arrives:
- If it extends the current tip → append (common case)
- If it extends a non-tip block → it's a fork. Compute cumulative work.
  If `work(new_chain) > work(current_chain)`, reorg to the new chain.
- If `work(new_chain) <= work(current_chain)`, store as orphan, do not reorg.

### 5.2 Reorg Procedure

To reorg from chain A to chain B (B has more work):

1. Find the **fork point** — the last common ancestor of A and B
2. **Undo** blocks from A's tip back to the fork point:
   a. For each tx in each unwound block (reverse order):
      - Re-add spent UTXOs (inputs become unspent again)
      - Remove created UTXOs (outputs no longer exist)
      - Return txs to the mempool
3. **Apply** blocks from fork point to B's tip:
   a. For each block, validate per §4.2
   b. Update UTXO set (remove spent inputs, add new outputs)
   c. Remove confirmed txs from mempool
4. If any block in B fails validation → reorg fails, revert to A
5. Emit `chain_reorg` event with old_tip, new_tip, fork_point, depth

### 5.3 Orphan Blocks

Blocks whose `prev_hash` doesn't match any known block are stored as orphans
for up to 72 blocks deep. If a parent arrives later, the orphan is connected.
Orphans older than 72 blocks of the tip are evicted.

### 5.4 Checkpoints

Checkpoints are `(height, block_hash)` pairs hardcoded in the binary.
- No reorg may go deeper than the most recent checkpoint
- If a fork would require reverting past a checkpoint, it is rejected
- Checkpoints are updated in software releases as the chain grows

### 5.5 Maximum Reorg Depth

**Max reorg depth: 2016 blocks** (one retarget period).
If a reorg would exceed this depth, the block is rejected. This protects
against eclipse attacks on young chains.

## 6. Difficulty Adjustment

### 6.1 Retarget Schedule

Difficulty retargets every `RETARGET_PERIOD` blocks (144 on testnet, 2016 on mainnet).

### 6.2 Retarget Algorithm

```
first_block  = blocks[-(RETARGET_PERIOD + 1)]
last_block   = blocks[-1]
actual_time  = last_block.timestamp - first_block.timestamp
expected_time = RETARGET_PERIOD * BLOCK_TIME

ratio = actual_time / expected_time

# Clamp to ±25% per retarget
ratio = clamp(ratio, 0.75, 1.25)

new_target = current_target * ratio
new_bits = target_to_bits(new_target)

# Additional stability: max ±2 bits change per retarget
new_bits = clamp(new_bits, current_bits - 2, current_bits + 2)
new_bits = max(1, min(new_bits, 32))
```

### 6.3 Testnet Override

On testnet, if no block has been found in `20 * BLOCK_TIME` seconds,
difficulty drops to `max(current_bits - 4, 1)` for the next block.
This prevents the testnet from stalling if miners go offline.

## 7. Mempool Policy

### 7.1 Block Template Construction

When building a block template for mining:

1. Sort mempool by `fee_per_byte` descending
2. Select txs until block size reaches 100KB or mempool is empty
3. For each selected tx, re-validate against current UTXO set
4. Skip txs that depend on unconfirmed mempool txs not yet included
5. Calculate total fees
6. Set coinbase output = block_reward + total_fees

### 7.2 Eviction

Mempool is bounded to 1000 entries. When full, evict lowest fee-per-byte txs.

### 7.3 Expiry

Transactions older than 72 hours (not in a block) are evicted.

## 8. P2P Consensus Messages

### 8.1 Block Relay

When a node accepts a new block:
1. Broadcast `new_block` message to all peers
2. Peers validate per §4.2
3. If valid, peers broadcast to their peers (gossip)
4. If invalid, peers disconnect the sender ( DoS protection)

### 8.2 Transaction Relay

When a node accepts a tx into mempool:
1. Broadcast `new_tx` to all peers
2. Peers validate per §3.2 and add to their mempool
3. If invalid, peers ignore but don't disconnect (false positives possible)

### 8.3 Chain Sync

On connect, peers exchange heights. The shorter chain requests headers:
1. `get_headers(start_height, max_count=500)`
2. `headers` response with batch
3. For each header, request full block if we don't have it
4. Validate each block per §4.2 before accepting

## 9. Slashing

### 9.1 Double-Signing

If a device signs two different blocks at the same height:
1. Anyone can submit `SlashEvidence` to any node
2. Node verifies both signatures are valid Ed25519 over different block hashes
3. Device is banned: `status = BANNED`, pubkey added to banned set
4. Banned devices cannot mine — their blocks are rejected per §4.2

### 9.2 Rate Violations

If a device mines blocks faster than physically possible on an ESP32:
- `interval < MIN_BLOCK_INTERVAL * 0.25` → auto-ban
- `interval < MIN_BLOCK_INTERVAL * 0.50` → flag, 3 consecutive → ban

## 10. Initial Block Download (IBD)

New nodes syncing from scratch:
1. Start with genesis block (hardcoded)
2. Connect to peers, request headers from height 0
3. Validate each header's PoW and chain linkage
4. Request full blocks for any height where we lack block data
5. Validate each block per §4.2, build UTXO set incrementally
6. Once caught up, enter normal relay mode

## 11. Testnet Parameters

| Parameter | Testnet | Mainnet |
|-----------|---------|---------|
| Difficulty | 12 bits | 20 bits |
| Block time | 30s | 120s |
| Retarget period | 144 | 2016 |
| Max reorg | 2016 | 2016 |
| Max block size | 100KB | 100KB |
| Coinbase maturity | 100 | 100 |
| Mempool max | 1000 | 10000 |
| Min relay fee | 1 sat/byte | 1 sat/byte |
| Tx data limit | 256 bytes | 256 bytes |
| Max block txs | 10 | 100 |

## 12. Error Handling

- **Invalid block:** Reject, do not store, do not relay. Log reason.
- **Invalid tx:** Drop from mempool, do not relay. Log reason.
- **Invalid peer:** If peer sends >3 invalid blocks/txs, disconnect + ban 24h.
- **Validation error during reorg:** Revert to previous chain, log, alert.

## 13. Future Improvements (v2)

- Median-time-past (MTP) for timestamp validation
- BIP9-style soft fork deployment
- Replace-by-fee (RBF)
- SegWit-style witness data separation
- Batched validation for IBD
- Compact block relay (BIP152)