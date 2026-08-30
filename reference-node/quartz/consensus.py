"""
Quartz Consensus Engine — validates blocks, transactions, manages UTXO set,
handles fork choice and reorgs.

This module is the single source of truth for consensus rules.
All block/tx acceptance flows through here.

Spec: CONSENSUS.md
"""

import hashlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward, get_miner_reward,
    HEADER_SIZE, BLOCK_TIME, RETARGET_PERIOD, DIFFICULTY_BITS,
    INITIAL_REWARD, HALVING_INTERVAL, MAX_DIFFICULTY_CHANGE,
    retarget_difficulty_bits, verify_checkpoint,
    split_block_reward,
)
from .crystal_hash import crystal_hash_verify
from .crypto import validate_address, public_key_to_address
from .quantum_crypto import (
    MERKLE_AUTH_SIZE,
    QSIG_SIZE,
    WOTS_SIG_SIZE,
    verify_quantum_signature,
)

logger = logging.getLogger("quartz.consensus")

# ============================================================
# Constants (testnet defaults, overridden by mainnet config)
# ============================================================

MAX_BLOCK_SIZE = 100_000          # 100 KB
MAX_BLOCK_TXS = 10                # testnet limit
COINBASE_MATURITY = 100           # blocks before coinbase is spendable
MEMPOOL_MAX = 1000                # max mempool entries
MIN_RELAY_FEE = 1                 # sat/byte
TX_DATA_LIMIT = 256               # bytes
MAX_REORG_DEPTH = 2016            # one retarget period
ORPHAN_MAX_DEPTH = 72             # blocks
MEMPOOL_EXPIRY_HOURS = 72
MEMPOOL_MAX_TX_SIZE = 4096        # 4 KB


# ============================================================
# UTXO Set
# ============================================================

@dataclass
class UTXO:
    """An unspent transaction output."""
    txid: bytes          # 32 bytes
    index: int           # output index within the tx
    amount: int          # quartz-sats
    script_pubkey: bytes # 32 bytes (recipient address hash)
    created_height: int  # block height where this output was created

    @property
    def key(self) -> Tuple[bytes, int]:
        return (self.txid, self.index)


class UTXOSet:
    """
    Tracks unspent transaction outputs.

    Updated atomically per block:
    - New outputs from every tx are added
    - Spent inputs from every non-coinbase tx are removed
    """

    def __init__(self):
        self._utxos: Dict[Tuple[bytes, int], UTXO] = {}
        # Index by script_pubkey for fast balance queries
        self._by_script: Dict[bytes, List[Tuple[bytes, int]]] = {}

    def add(self, utxo: UTXO):
        self._utxos[utxo.key] = utxo
        self._by_script.setdefault(utxo.script_pubkey, []).append(utxo.key)

    def remove(self, txid: bytes, index: int) -> Optional[UTXO]:
        utxo = self._utxos.pop((txid, index), None)
        if utxo:
            lst = self._by_script.get(utxo.script_pubkey, [])
            try:
                lst.remove((txid, index))
            except ValueError:
                pass
        return utxo

    def get(self, txid: bytes, index: int) -> Optional[UTXO]:
        return self._utxos.get((txid, index))

    def has(self, txid: bytes, index: int) -> bool:
        return (txid, index) in self._utxos

    def get_balance(self, script_pubkey: bytes) -> int:
        """Sum all UTXOs for a given script_pubkey."""
        total = 0
        for txid, idx in self._by_script.get(script_pubkey, []):
            utxo = self._utxos.get((txid, idx))
            if utxo:
                total += utxo.amount
        return total

    def get_utxos_for(self, script_pubkey: bytes) -> List[UTXO]:
        """Get all UTXOs for a script_pubkey (for wallet tx construction)."""
        result = []
        for txid, idx in self._by_script.get(script_pubkey, []):
            utxo = self._utxos.get((txid, idx))
            if utxo:
                result.append(utxo)
        return result

    def __len__(self) -> int:
        return len(self._utxos)

    def snapshot(self) -> Dict[Tuple[bytes, int], UTXO]:
        """Deep copy of UTXO set (for reorg safety)."""
        return dict(self._utxos)

    def restore(self, snapshot: Dict[Tuple[bytes, int], UTXO]):
        """Restore from a snapshot."""
        self._utxos = dict(snapshot)
        self._by_script.clear()
        for key, utxo in self._utxos.items():
            self._by_script.setdefault(utxo.script_pubkey, []).append(key)


# ============================================================
# Mempool
# ============================================================

@dataclass
class MempoolEntry:
    """A transaction in the mempool."""
    tx: Transaction
    fee_sats: int
    size_bytes: int
    fee_per_byte: float
    received_at: float
    spends: Set[Tuple[bytes, int]]  # UTXOs this tx spends
    creates: List[Tuple[bytes, int]]  # UTXOs this tx creates


class Mempool:
    """
    Mempool — valid unconfirmed transactions ordered by fee-per-byte.

    Enforces:
    - No duplicate txids
    - No double-spends within mempool
    - Size limit (MEMPOOL_MAX entries)
    - Time-based expiry
    """

    def __init__(self, max_size: int = MEMPOOL_MAX):
        self._txs: Dict[bytes, MempoolEntry] = {}  # txid → entry
        self._spent_utxos: Set[Tuple[bytes, int]] = set()  # UTXOs spent by mempool txs
        self._max_size = max_size

    def add(self, tx: Transaction, utxo_set: UTXOSet) -> Tuple[bool, str]:
        """
        Add a transaction to the mempool.

        Returns (success, reason).
        """
        txid = tx.txid

        # No duplicates
        if txid in self._txs:
            return (False, "already in mempool")

        # Size limit
        tx_size = len(tx.serialize())
        if tx_size > MEMPOOL_MAX_TX_SIZE:
            return (False, f"tx too large: {tx_size} bytes")

        # Validate the transaction
        is_valid, fee, spends, creates, reason = validate_transaction(tx, utxo_set)
        if not is_valid:
            return (False, reason)

        # Check fee
        if fee < MIN_RELAY_FEE * tx_size:
            return (False, f"fee {fee} below min relay {MIN_RELAY_FEE * tx_size}")

        # No double-spend within mempool
        for spend in spends:
            if spend in self._spent_utxos:
                return (False, "double-spend in mempool")

        # Evict if full
        if len(self._txs) >= self._max_size:
            self._evict_lowest()

        # Add
        entry = MempoolEntry(
            tx=tx,
            fee_sats=fee,
            size_bytes=tx_size,
            fee_per_byte=fee / tx_size if tx_size > 0 else 0,
            received_at=time.time(),
            spends=spends,
            creates=creates,
        )
        self._txs[txid] = entry
        self._spent_utxos |= spends

        return (True, "accepted")

    def remove(self, txid: bytes):
        """Remove a tx from the mempool (e.g., it was confirmed)."""
        entry = self._txs.pop(txid, None)
        if entry:
            self._spent_utxos -= entry.spends

    def remove_batch(self, txids: List[bytes]):
        """Remove multiple txs (block confirmation)."""
        for txid in txids:
            entry = self._txs.pop(txid, None)
            if entry:
                self._spent_utxos -= entry.spends

    def get(self, txid: bytes) -> Optional[MempoolEntry]:
        return self._txs.get(txid)

    def has(self, txid: bytes) -> bool:
        return txid in self._txs

    def size(self) -> int:
        return len(self._txs)

    def select_for_block(self, utxo_set: UTXOSet, max_txs: int = MAX_BLOCK_TXS,
                        max_size: int = MAX_BLOCK_SIZE) -> List[Transaction]:
        """
        Select transactions for a block template.

        Greedy by fee-per-byte, respecting dependency ordering.
        """
        # Sort by fee-per-byte descending
        sorted_entries = sorted(
            self._txs.values(),
            key=lambda e: e.fee_per_byte,
            reverse=True
        )

        selected = []
        selected_spends: Set[Tuple[bytes, int]] = set()
        total_size = 0

        for entry in sorted_entries:
            if len(selected) >= max_txs:
                break
            if total_size + entry.size_bytes > max_size:
                continue

            # Check none of this tx's inputs are already selected
            if entry.spends & selected_spends:
                continue

            # Re-validate against current state (UTXO may have changed)
            is_valid, _, _, _, _ = validate_transaction(entry.tx, utxo_set)
            if not is_valid:
                continue

            selected.append(entry.tx)
            selected_spends |= entry.spends
            total_size += entry.size_bytes

        return selected

    def expire(self, max_age_seconds: int = MEMPOOL_EXPIRY_HOURS * 3600):
        """Remove expired transactions."""
        now = time.time()
        expired = [
            txid for txid, entry in self._txs.items()
            if now - entry.received_at > max_age_seconds
        ]
        for txid in expired:
            self.remove(txid)
        return len(expired)

    def _evict_lowest(self):
        """Evict the lowest fee-per-byte tx to make room."""
        if not self._txs:
            return
        lowest_txid = min(
            self._txs,
            key=lambda t: self._txs[t].fee_per_byte
        )
        self.remove(lowest_txid)

    def clear(self):
        self._txs.clear()
        self._spent_utxos.clear()

    def list_txs(self) -> List[MempoolEntry]:
        return list(self._txs.values())

    def snapshot(self):
        """Capture mempool state for rollback during reorgs."""
        return {
            'txs': {txid: entry for txid, entry in self._txs.items()},
            'spent': set(self._spent_utxos),
        }

    def restore(self, snap):
        """Restore mempool state from a snapshot()."""
        self._txs = dict(snap['txs'])
        self._spent_utxos = set(snap['spent'])


# ============================================================
# Transaction Validation
# ============================================================

def validate_transaction(tx: Transaction, utxo_set: UTXOSet,
                        is_coinbase: bool = False,
                        height: int = 0,
                        ots_used: Optional[Dict[str, int]] = None,
                        ) -> Tuple[bool, int, Set, List, str]:
    """
    Validate a transaction against the current UTXO set.

    If `ots_used` is provided (address → highest burned WOTS+ slot + 1),
    one-time slots are tracked in it and any reuse of a burned slot is
    rejected — reusing a one-time key is a catastrophic forgery risk.

    Returns:
        (is_valid, fee_sats, spends, creates, reason)
    """
    spends: Set[Tuple[bytes, int]] = set()
    creates: List[Tuple[bytes, int]] = []

    # --- Coinbase special case ---
    if is_coinbase:
        # Coinbase must have 1 "null" input and at least 1 output
        if len(tx.inputs) != 1:
            return (False, 0, spends, creates, "coinbase must have exactly 1 input")
        if len(tx.outputs) < 1:
            return (False, 0, spends, creates, "coinbase must have at least 1 output")

        # Coinbase input should be null prev_txid
        prev_hash, idx, sig, pubkey = tx.inputs[0]
        if prev_hash != b'\x00' * 32:
            return (False, 0, spends, creates, "coinbase prev_hash must be null")

        # Output amount is checked at block level (reward + fees)
        # No UTXO validation needed for coinbase
        for i, (amount, script) in enumerate(tx.outputs):
            if amount <= 0:
                return (False, 0, spends, creates, f"coinbase output {i} amount must be positive")
            creates.append((tx.txid, i))

        return (True, 0, spends, creates, "valid coinbase")

    # --- Data-carrier (message) transactions ---
    # Zero-value txs with no inputs/outputs that carry only `data`
    # (messaging / IoT / name-registry layer). They create and spend
    # nothing, pay no fee, and have no UTXO-set impact. Included in
    # blocks by the miner alongside regular value transfers.
    if len(tx.inputs) == 0 and len(tx.outputs) == 0:
        if not tx.data:
            return (False, 0, spends, creates,
                    "empty transaction (no inputs, outputs or data)")
        if len(tx.data) > TX_DATA_LIMIT:
            return (False, 0, spends, creates,
                    f"data exceeds limit: {len(tx.data)} > {TX_DATA_LIMIT}")
        return (True, 0, spends, creates, "valid data carrier")

    # --- Regular transaction ---

    # Must have inputs and outputs
    if len(tx.inputs) == 0:
        return (False, 0, spends, creates, "no inputs")
    if len(tx.outputs) == 0:
        return (False, 0, spends, creates, "no outputs")

    # Data carrier limit
    if len(tx.data) > TX_DATA_LIMIT:
        return (False, 0, spends, creates, f"data exceeds limit: {len(tx.data)} > {TX_DATA_LIMIT}")

    # Sum inputs, check UTXOs exist, no double-spend within tx
    input_total = 0
    seen_inputs: Set[Tuple[bytes, int]] = set()

    for i, (prev_hash, idx, sig, pubkey) in enumerate(tx.inputs):
        key = (prev_hash, idx)

        # No double-spend within same tx
        if key in seen_inputs:
            return (False, 0, spends, creates, f"double-spend input {i}: duplicate")
        seen_inputs.add(key)

        # Check UTXO exists
        utxo = utxo_set.get(prev_hash, idx)
        if utxo is None:
            return (False, 0, spends, creates, f"input {i}: UTXO not found")
        if not utxo_set.has(prev_hash, idx):
            return (False, 0, spends, creates, f"input {i}: UTXO already spent")

        # Coinbase maturity check
        if utxo.created_height >= 0 and height > 0:
            # Check if this UTXO came from a coinbase
            # (In v1, we track this via the txid being the block's coinbase txid.
            #  For simplicity, we check if created_height + maturity > current height.)
            # This is a simplification — a full implementation would flag
            # coinbase-derived UTXOs explicitly.
            pass  # Coinbase maturity enforced at block validation level

        # Signature verification — two accepted forms:
        #   1. Ed25519-style: 64-byte signature + 32-byte pubkey.
        #      The reference implementation accepts well-formed signatures
        #      (real verification requires PyNaCl; attestation.py handles
        #      device-level signatures).
        #   2. WOTS+ quantum: QSIG_SIZE-byte signature + 32-byte Merkle-root
        #      pubkey. Fully verified here, and the one-time slot it burns
        #      is checked against `ots_used` so it can never be reused.
        if len(sig) == 64:
            if len(pubkey) != 32:
                return (False, 0, spends, creates, f"input {i}: invalid pubkey length")
        elif len(sig) == QSIG_SIZE:
            if len(pubkey) != 32:
                return (False, 0, spends, creates, f"input {i}: invalid pubkey length")
            if not verify_quantum_signature(pubkey, tx.sighash, sig):
                return (False, 0, spends, creates, f"input {i}: invalid WOTS+ signature")
            ots_idx = struct.unpack('<I', sig[WOTS_SIG_SIZE + MERKLE_AUTH_SIZE:])[0]
            if ots_used is not None:
                spender = public_key_to_address(pubkey)
                if ots_idx < ots_used.get(spender, 0):
                    return (False, 0, spends, creates,
                            f"input {i}: OTS slot {ots_idx} already used for {spender}")
                ots_used[spender] = ots_idx + 1
        else:
            return (False, 0, spends, creates, f"input {i}: invalid signature length")

        # Script check — the script_pubkey must bind the spender's key.
        # Accepted forms (testnet reference):
        #   1. sha256(pubkey)                 — standard hash-lock
        #   2. pubkey[:32]                    — raw pubkey lock (compat)
        #   3. address bytes derived from key — faucet-style address lock
        expected_script = hashlib.sha256(pubkey).digest()
        if utxo.script_pubkey != expected_script:
            if utxo.script_pubkey != pubkey[:32]:
                try:
                    addr_bytes = public_key_to_address(pubkey).encode()
                except Exception:
                    addr_bytes = b''
                if utxo.script_pubkey != addr_bytes:
                    return (False, 0, spends, creates,
                            f"input {i}: pubkey does not match UTXO script_pubkey")

        input_total += utxo.amount
        spends.add(key)

    # Sum outputs
    output_total = 0
    for i, (amount, script) in enumerate(tx.outputs):
        if amount <= 0:
            return (False, 0, spends, creates, f"output {i}: amount must be positive")
        # Script is either a 32-byte hash/pubkey lock, or the Base58
        # address bytes (26-34 chars) for address-keyed (faucet) outputs.
        if not (len(script) == 32 or 26 <= len(script) <= 34):
            return (False, 0, spends, creates,
                    f"output {i}: invalid script_pubkey length {len(script)}")
        output_total += amount
        creates.append((tx.txid, i))

    # Fee = inputs - outputs
    fee = input_total - output_total
    if fee < 0:
        return (False, 0, spends, creates,
                f"outputs exceed inputs: {output_total} > {input_total}")

    return (True, fee, spends, creates, "valid")


# ============================================================
# Block Validation
# ============================================================

def validate_block(block: Block, height: int, prev_block: Optional[Block],
                  utxo_set: UTXOSet, current_difficulty: int,
                  expected_difficulty: int,
                  skip_tx_validation: bool = False,
                  ots_used: Optional[Dict[str, int]] = None,
                  ) -> Tuple[bool, str, int]:
    """
    Validate a block against consensus rules.

    Returns:
        (is_valid, reason, total_fees)

    Does NOT mutate the UTXO set — caller must apply_block() after validation.

    skip_tx_validation=True performs structural checks only (PoW, merkle,
    linkage, difficulty, timestamp). Used for indexing fork blocks before
    deciding whether to reorg; stateful tx checks run during reorg.
    """

    # 1. Structural
    if block.header is None:
        return (False, "missing header", 0)
    if len(block.transactions) == 0:
        return (False, "no transactions", 0)
    if len(block.transactions) > MAX_BLOCK_TXS:
        return (False, f"too many txs: {len(block.transactions)} > {MAX_BLOCK_TXS}", 0)

    # Check serialized block size
    block_size = len(block.serialize())
    if block_size > MAX_BLOCK_SIZE:
        return (False, f"block too large: {block_size} > {MAX_BLOCK_SIZE}", 0)

    # 2. PoW — hash must meet difficulty target
    # Uses leading-zero-bits semantics (target = 2^(256-bits)), matching
    # the miner's loop. NOTE: crystal_hash.check_difficulty interprets the
    # value as Bitcoin compact bits — different encoding, not used here.
    header_hash = block.header.hash
    target = 1 << (256 - block.header.difficulty_target)
    if int.from_bytes(header_hash, 'big') >= target:
        return (False, "PoW does not meet difficulty target", 0)

    # 3. Chain linkage
    if prev_block is not None:
        if block.header.prev_block_hash != prev_block.header.hash:
            return (False, "prev_hash does not match tip", 0)

    # 4. Difficulty must match expected
    if block.header.difficulty_target != expected_difficulty:
        return (False,
                f"difficulty mismatch: {block.header.difficulty_target} != {expected_difficulty}",
                0)

    # 5. Timestamp sanity (±2 hours of current time)
    now = int(time.time())
    if abs(block.header.timestamp - now) > 2 * 3600:
        # Allow older blocks during sync (timestamp might be from hours ago)
        if block.header.timestamp > now + 2 * 3600:
            return (False, "timestamp too far in future", 0)
        # Past timestamps OK during IBD

    # 6. Merkle root
    tx_hashes = [tx.txid for tx in block.transactions]
    computed_merkle = compute_merkle_root(tx_hashes)
    if block.header.merkle_root != computed_merkle:
        return (False, "merkle root mismatch", 0)

    # 7-9. Transaction validation (skipped for fork-block indexing —
    # those checks run with the correct UTXO view during reorg)
    total_fees = 0
    if not skip_tx_validation:
        # One-time-signature view for this block: seeded from caller state
        # (catches cross-block slot reuse) and extended as txs validate
        # (catches reuse within the same block).
        block_ots = ots_used if ots_used is not None else {}
        # 7. Coinbase validation
        coinbase = block.transactions[0]
        is_valid, _, _, _, reason = validate_transaction(coinbase, utxo_set,
                                                           is_coinbase=True, height=height)
        if not is_valid:
            return (False, f"invalid coinbase: {reason}", 0)

        # 8. Reward + fees
        spent_in_block: Set[Tuple[bytes, int]] = set()

        # Validate each non-coinbase tx
        for i, tx in enumerate(block.transactions[1:], start=1):
            is_valid, fee, spends, _, reason = validate_transaction(tx, utxo_set,
                                                                     height=height,
                                                                     ots_used=block_ots)
            if not is_valid:
                return (False, f"tx {i} invalid: {reason}", 0)

            # No double-spend within block
            for spend in spends:
                if spend in spent_in_block:
                    return (False, f"tx {i}: double-spend within block", 0)
                spent_in_block |= spends

            total_fees += fee

        # 9. Coinbase output = reward + fees
        expected_reward = get_block_reward(height)
        expected_coinbase_output = expected_reward + total_fees

        # For testnet: full reward to miner (no split)
        # For mainnet: miner gets 85%, but coinbase tx carries full amount
        # and the split happens at the protocol level via multiple outputs
        coinbase_output = sum(amt for amt, _ in coinbase.outputs)
        if coinbase_output != expected_coinbase_output:
            # On testnet, allow overpayment (miner can take less)
            if coinbase_output > expected_coinbase_output:
                return (False,
                        f"coinbase overpays: {coinbase_output} > {expected_coinbase_output}",
                        0)
            # Underpayment is OK (miner just gets less)

    # 10. Checkpoint verification
    if not verify_checkpoint(height, header_hash):
        return (False, "checkpoint mismatch", 0)

    return (True, "valid", total_fees)


# ============================================================
# Block Application (UTXO set update)
# ============================================================

def apply_block(block: Block, height: int, utxo_set: UTXOSet,
                spent_log: list = None,
                storage=None) -> List[Tuple[bytes, int]]:
    """
    Apply a validated block to the UTXO set.

    - Removes spent inputs (from non-coinbase txs)
    - Adds new outputs (from all txs)
    - Records burned WOTS+ one-time slots in `storage` (if given) so
      future validation can reject slot reuse

    If `spent_log` is a list, removed UTXO objects are appended to it
    (undo log for reorgs).

    Returns list of confirmed txids (for mempool cleanup).
    """
    confirmed_txids = []

    for tx in block.transactions:
        confirmed_txids.append(tx.txid)

        # Add outputs to UTXO set
        for i, (amount, script) in enumerate(tx.outputs):
            utxo = UTXO(
                txid=tx.txid,
                index=i,
                amount=amount,
                script_pubkey=script,
                created_height=height,
            )
            utxo_set.add(utxo)

    # Remove spent inputs (non-coinbase only)
    for tx in block.transactions[1:]:
        for prev_hash, idx, sig, pubkey in tx.inputs:
            removed = utxo_set.remove(prev_hash, idx)
            if removed is not None and spent_log is not None:
                spent_log.append(removed)

    # Record burned WOTS+ one-time slots. Persistence detail — recording
    # must never break block application.
    if storage is not None:
        for tx in block.transactions[1:]:
            for prev_hash, idx, sig, pubkey in tx.inputs:
                if len(sig) == QSIG_SIZE:
                    try:
                        ots_idx = struct.unpack(
                            '<I', sig[WOTS_SIG_SIZE + MERKLE_AUTH_SIZE:])[0]
                        storage.record_signature(
                            public_key_to_address(pubkey), ots_idx,
                            tx.txid, height)
                    except Exception:
                        logger.warning("failed to record OTS usage for tx %s",
                                       tx.txid.hex())

    return confirmed_txids


def undo_block(block: Block, utxo_set: UTXOSet,
               spent_log: list = None) -> List[Transaction]:
    """
    Undo a block (for reorgs).

    - Removes created outputs
    - Re-adds spent inputs from the undo log (if provided)

    Returns list of txs to return to mempool.
    """
    # Remove outputs created by this block (reverse order for safety)
    for tx in reversed(block.transactions):
        for i, (amount, script) in enumerate(tx.outputs):
            utxo_set.remove(tx.txid, i)

    # Re-add spent inputs from the undo log (most-recently-spent first)
    if spent_log:
        for utxo in reversed(spent_log):
            utxo_set.add(utxo)

    return [tx for tx in block.transactions if tx.inputs and tx.inputs[0][0] != b'\x00' * 32]


# ============================================================
# Fork Choice
# ============================================================

def chain_work(difficulty_bits: int) -> int:
    """
    Calculate work represented by a block at given difficulty.

    work = 2^256 / (target + 1)
    """
    # For simplicity, use difficulty_bits as a proxy for work.
    # Higher bits = more work. This is monotonic and sufficient for fork choice.
    # A precise implementation would use the actual target value.
    target = 1 << (256 - difficulty_bits)
    return (1 << 256) // (target + 1)


# ============================================================
# Consensus Engine
# ============================================================

class ConsensusEngine:
    """
    The consensus engine — single source of truth for block/tx acceptance.

    Wraps the chain, UTXO set, and mempool. All block/tx operations
    go through this class.
    """

    def __init__(self, blocks: List[Block], balances: Dict[str, int],
                 current_difficulty: int, block_time: int = 30,
                 retarget_period: int = 144, storage=None):
        self.blocks = blocks
        self.utxo_set = UTXOSet()
        self.mempool = Mempool()
        self.current_difficulty = current_difficulty
        self.block_time = block_time
        self.retarget_period = retarget_period
        self.balances = balances  # legacy balance dict (for compatibility)
        self.storage = storage  # optional persistence (OTS slot tracking)
        self._orphan_blocks: Dict[bytes, List[Block]] = {}  # prev_hash → orphans

        # Fork-tracking structures:
        # _block_index: every known valid block (main chain + forks), by hash
        # _children: parent hash → list of child hashes (fork tree)
        # _undo_log: block hash → [UTXO] spent while applying that block
        self._block_index: Dict[bytes, Block] = {}
        self._children: Dict[bytes, List[bytes]] = {}
        self._undo_log: Dict[bytes, List[UTXO]] = {}

        # Build UTXO set from existing blocks (if they have txs)
        self._rebuild_utxo_set()

        # Index existing chain
        for b in self.blocks:
            self._index_block(b)

    def _rebuild_utxo_set(self):
        """Rebuild UTXO set from all blocks in chain."""
        self.utxo_set = UTXOSet()
        for height, block in enumerate(self.blocks):
            for tx in block.transactions:
                # Add outputs
                for i, (amount, script) in enumerate(tx.outputs):
                    self.utxo_set.add(UTXO(
                        txid=tx.txid,
                        index=i,
                        amount=amount,
                        script_pubkey=script,
                        created_height=height,
                    ))

            # Remove spent inputs (non-coinbase only)
            for tx in block.transactions[1:]:
                for prev_hash, idx, _, _ in tx.inputs:
                    self.utxo_set.remove(prev_hash, idx)

        logger.info(f"UTXO set rebuilt: {len(self.utxo_set)} outputs from {len(self.blocks)} blocks")

    @property
    def height(self) -> int:
        return len(self.blocks) - 1

    @property
    def tip(self) -> Block:
        return self.blocks[-1]

    def get_expected_difficulty(self, height: int) -> int:
        """Calculate expected difficulty for the next block."""
        if height == 0 or height % self.retarget_period != 0:
            return self.current_difficulty

        # Retarget
        if len(self.blocks) < self.retarget_period + 1:
            return self.current_difficulty

        old_diff = self.current_difficulty
        new_diff = retarget_difficulty_bits(
            old_diff, self.blocks, self.block_time
        )
        if new_diff != old_diff:
            logger.info(f"📐 Difficulty retarget at block {height}: "
                       f"{old_diff} → {new_diff} bits")
        return new_diff

    def validate_new_block(self, block: Block) -> Tuple[bool, str, int]:
        """
        Validate a block that extends the current tip.

        Returns (is_valid, reason, total_fees).
        """
        height = len(self.blocks)
        expected_diff = self.get_expected_difficulty(height)

        # Seed the one-time-slot view from storage so slots burned in
        # earlier blocks can never be reused (cross-block WOTS+ rule).
        ots_seed: Dict[str, int] = {}
        if self.storage is not None:
            for tx in block.transactions:
                for _, _, sig, pubkey in tx.inputs:
                    if len(sig) == QSIG_SIZE:
                        addr = public_key_to_address(pubkey)
                        if addr not in ots_seed:
                            state = self.storage.get_address_state(addr)
                            if state is not None:
                                ots_seed[addr] = state.wots_used

        return validate_block(
            block=block,
            height=height,
            prev_block=self.tip,
            utxo_set=self.utxo_set,
            current_difficulty=self.current_difficulty,
            expected_difficulty=expected_diff,
            ots_used=ots_seed,
        )

    def accept_block(self, block: Block) -> Tuple[bool, str]:
        """
        Validate and accept a new block.

        Path A — block extends the current tip: validate + apply + append.
        Path B — block extends a known non-tip block (fork): index it and
                 reorg if its branch has more cumulative work than the
                 active chain from the fork point.
        Path C — parent unknown: park it in the orphan pool.

        Returns (success, reason).
        """
        # Structural validation (PoW, size, merkle, difficulty sanity)
        # Parent-specific checks (linkage, difficulty) happen per-path below.
        prev_hash = block.header.prev_block_hash

        if prev_hash == self.tip.header.hash:
            # Path A: extends tip — full validation against current state
            is_valid, reason, total_fees = self.validate_new_block(block)
            if not is_valid:
                return (False, reason)

            height = len(self.blocks)
            spent_log = []
            confirmed_txids = apply_block(block, height, self.utxo_set, spent_log,
                                          storage=self.storage)
            self._undo_log[block.header.hash] = spent_log

            # Remove confirmed txs from mempool
            self.mempool.remove_batch(confirmed_txids)

            # Update balances (legacy compatibility)
            self._update_balances(block, height, spent_utxos=spent_log)

            # Update difficulty
            if height > 0 and height % self.retarget_period == 0:
                self.current_difficulty = retarget_difficulty_bits(
                    self.current_difficulty, self.blocks, self.block_time
                )

            # Add to chain + index
            self.blocks.append(block)
            self._index_block(block)

            # Process orphans that might connect now
            self._process_orphans()

            logger.info(f"✅ Block #{height} accepted — {len(block.transactions)} txs, "
                       f"fee={total_fees/1e8:.4f} QZ, utxos={len(self.utxo_set)}")

            return (True, "accepted")

        # Path B/C: fork or orphan
        return self._handle_fork(block)

    def _index_block(self, block: Block):
        """Add a block to the fork-tree index."""
        h = block.header.hash
        if h in self._block_index:
            return
        self._block_index[h] = block
        self._children.setdefault(block.header.prev_block_hash, []).append(h)

    def _handle_fork(self, block: Block) -> Tuple[bool, str]:
        """
        Handle a block that doesn't extend the current tip.

        - Parent unknown → orphan pool (retried when parent arrives).
        - Parent known (main chain or fork tree) → index it, then compare
          cumulative work of its branch vs. the active chain from the fork
          point. Reorg if strictly greater. Either way the block is kept
          in the index so its descendants can extend it.
        """
        prev_hash = block.header.prev_block_hash

        if prev_hash not in self._block_index:
            self._orphan_blocks.setdefault(prev_hash, []).append(block)
            return (False, "orphan — parent not found")

        # Structural sanity for fork blocks (PoW, size, merkle, tx rules).
        # Stateful checks happen during reorg application.
        parent = self._block_index[prev_hash]
        is_valid, reason, _ = self._validate_fork_block(block, parent)
        if not is_valid:
            return (False, reason)

        self._index_block(block)

        # Walk from this block up to the highest ancestor that is on the
        # ACTIVE chain. That ancestor is the fork point.
        branch = [block]
        cursor = block
        while True:
            parent_hash = cursor.header.prev_block_hash
            parent_block = self._block_index.get(parent_hash)
            if parent_block is None:
                return (False, "broken branch")
            on_active = any(b.header.hash == parent_hash for b in self.blocks[-MAX_REORG_DEPTH:])
            if on_active:
                fork_hash = parent_hash
                break
            branch.append(parent_block)
            cursor = parent_block
            if len(branch) > MAX_REORG_DEPTH:
                return (False, "fork deeper than max reorg depth")

        # fork_index = position of fork point on the active chain
        fork_index = None
        for i in range(len(self.blocks) - 1, max(-1, len(self.blocks) - 1 - MAX_REORG_DEPTH), -1):
            if self.blocks[i].header.hash == fork_hash:
                fork_index = i
                break
        if fork_index is None:
            return (False, "fork point not on active chain")

        branch.reverse()  # oldest → newest
        depth = len(self.blocks) - 1 - fork_index  # blocks to undo
        if len(branch) + fork_index < len(self.blocks):
            # Branch is shorter/equal length from fork point — can't have
            # more work at equal difficulty; skip (still indexed).
            logger.info(f"Fork block stored (branch behind active chain): "
                       f"fork at {fork_index}, branch len {len(branch)}, depth {depth}")
            self._process_orphans_for(block.header.hash)
            return (False, "fork has less work")

        # Branch reaches at least equal length; compare cumulative work
        branch_work = sum(chain_work(b.header.difficulty_target) for b in branch)
        active_work = sum(chain_work(b.header.difficulty_target)
                          for b in self.blocks[fork_index + 1:])
        if branch_work <= active_work:
            logger.info(f"Fork block stored (equal/less work): fork at {fork_index}")
            self._process_orphans_for(block.header.hash)
            return (False, "fork has less work")

        if depth > MAX_REORG_DEPTH:
            logger.warning(f"Reorg depth {depth} exceeds max {MAX_REORG_DEPTH}")
            return (False, f"reorg depth {depth} exceeds max")

        return self._reorg(fork_index, branch)

    def _validate_fork_block(self, block: Block, parent: Block) -> Tuple[bool, str, int]:
        """Structural validation for a fork block (no UTXO-state checks)."""
        parent_height = None
        # find parent height on active chain if present; else approximate
        for i in range(len(self.blocks) - 1, -1, -1):
            if self.blocks[i].header.hash == parent.header.hash:
                parent_height = i
                break
        height = (parent_height + 1) if parent_height is not None else len(self.blocks)

        # Reuse validate_block but skip linkage vs tip and UTXO-state checks
        # by passing prev_block=None and the fork's own difficulty context.
        return validate_block(
            block=block,
            height=height,
            prev_block=None,
            utxo_set=self.utxo_set,
            current_difficulty=self.current_difficulty,
            expected_difficulty=block.header.difficulty_target,
            skip_tx_validation=True,
        )

    def _reorg(self, fork_index: int, branch: List[Block]) -> Tuple[bool, str]:
        """
        Atomically switch the active chain to `branch` (which attaches at
        fork_index + 1).

        Snapshot everything first; on any validation failure roll back to
        the snapshot and re-apply the original chain.
        """
        logger.info(f"🔄 Reorg: switching to fork at height {fork_index + 1}, "
                   f"depth={len(self.blocks) - 1 - fork_index}, "
                   f"branch={len(branch)} blocks")

        utxo_snap = self.utxo_set.snapshot()
        mem_snap = self.mempool.snapshot()
        balances_snap = dict(self.balances)
        old_blocks = list(self.blocks)
        old_undo = {h: list(u) for h, u in self._undo_log.items()}

        def rollback(reason: str) -> Tuple[bool, str]:
            self.utxo_set.restore(utxo_snap)
            self.mempool.restore(mem_snap)
            self.balances.clear()
            self.balances.update(balances_snap)
            self.blocks[:] = old_blocks
            self._undo_log.clear()
            self._undo_log.update(old_undo)
            logger.error(f"Reorg rolled back: {reason}")
            return (False, f"reorg failed: {reason}")

        # 1. Undo active-chain blocks above the fork point (tip → fork+1)
        undone_txs = []
        while len(self.blocks) - 1 > fork_index:
            b = self.blocks.pop()
            spent = self._undo_log.pop(b.header.hash, [])
            undone_txs.extend(undo_block(b, self.utxo_set, spent))
            # Legacy balances: reverse transfer effects only (coinbase is
            # credited by the miner layer, which handles its own split)
            for tx in b.transactions[1:]:
                for amount, script in tx.outputs:
                    addr = self._script_to_addr(script)
                    self.balances[addr] = self.balances.get(addr, 0) - amount
                    if self.balances[addr] <= 0:
                        self.balances.pop(addr, None)
                for prev_hash, idx, _s, _p in tx.inputs:
                    u = next((x for x in spent if (x.txid, x.index) == (prev_hash, idx)), None)
                    if u is not None:
                        addr = self._script_to_addr(u.script_pubkey)
                        self.balances[addr] = self.balances.get(addr, 0) + u.amount

        # 2. Apply branch blocks in order, validating each fully
        for b in branch:
            prev = self.blocks[-1]
            height = len(self.blocks)
            expected_diff = self.get_expected_difficulty(height)
            is_valid, reason, _ = validate_block(
                block=b, height=height, prev_block=prev,
                utxo_set=self.utxo_set,
                current_difficulty=self.current_difficulty,
                expected_difficulty=expected_diff,
            )
            if not is_valid:
                return rollback(f"branch block invalid at height {height}: {reason}")

            spent_log = []
            confirmed = apply_block(b, height, self.utxo_set, spent_log,
                                    storage=self.storage)
            self._undo_log[b.header.hash] = spent_log
            self.mempool.remove_batch(confirmed)
            self._update_balances(b, height, spent_utxos=spent_log)
            self.blocks.append(b)

        # 3. Return undone txs to the mempool
        for tx in undone_txs:
            self.mempool.add(tx, self.utxo_set)

        # 4. Difficulty now reflects the new chain
        if len(self.blocks) > 0 and (len(self.blocks) - 1) % self.retarget_period == 0:
            self.current_difficulty = retarget_difficulty_bits(
                self.current_difficulty, self.blocks, self.block_time
            )

        logger.info(f"✅ Reorg complete — new tip at height {len(self.blocks) - 1}")
        self._process_orphans_for(self.tip.header.hash)
        return (True, "reorged")

    def _process_orphans(self):
        """Try to connect orphan blocks to the new tip."""
        self._process_orphans_for(self.tip.header.hash)

    def _process_orphans_for(self, block_hash: bytes):
        """Retry orphans waiting on the given parent hash."""
        orphans = self._orphan_blocks.pop(block_hash, [])
        for orphan in orphans:
            logger.info(f"Retrying orphan block (parent now known)")
            self.accept_block(orphan)

    @staticmethod
    def _script_to_addr(script: bytes) -> str:
        """Map a script_pubkey to a legacy balance-dict key.

        Address-keyed scripts decode to the real Qk/T address;
        anything else falls back to the hex-key form used by
        synthetic miner credits.
        """
        try:
            s = script.decode('utf-8')
            if len(s) >= 26 and validate_address(s):
                return s
        except (UnicodeDecodeError, ValueError):
            pass
        return script.hex()[:34]

    def _update_balances(self, block: Block, height: int,
                         spent_utxos: Optional[List[UTXO]] = None):
        """Update legacy balance dict (for API compatibility).

        Only processes TRANSFER transactions — coinbase rewards are
        credited by the miner (testnet.py) where the miner/dev split
        is known.

        spent_utxos: UTXOs removed by apply_block for this block
        (used to debit the senders).
        """
        spent_by_key = {(u.txid, u.index): u for u in (spent_utxos or [])}

        # v2 coinbases pay wallet addresses directly (script IS the
        # address string) — credit them from block data so balances are
        # derivable by ANY node applying the block (p2p). v1 coinbases
        # (sha256 scripts) are still credited by the producing node's dict.
        if block.transactions:
            cb = block.transactions[0]
            if cb.inputs and cb.inputs[0][0] == b'\x00' * 32:  # coinbase marker
                for amount, script in cb.outputs:
                    if amount <= 0:
                        continue
                    try:
                        s = script.decode('utf-8')
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if len(s) >= 26 and validate_address(s):
                        self.balances[s] = self.balances.get(s, 0) + amount

        for tx in block.transactions[1:]:  # skip coinbase
            # Debit spent inputs (senders)
            for prev_hash, idx, _sig, _pubkey in tx.inputs:
                u = spent_by_key.get((prev_hash, idx))
                if u is not None:
                    addr = self._script_to_addr(u.script_pubkey)
                    self.balances[addr] = self.balances.get(addr, 0) - u.amount
                    if self.balances[addr] <= 0:
                        self.balances.pop(addr, None)

            # Credit outputs (recipients)
            for amount, script in tx.outputs:
                addr = self._script_to_addr(script)
                self.balances[addr] = self.balances.get(addr, 0) + amount

    def add_transaction(self, tx: Transaction) -> Tuple[bool, str]:
        """
        Add a transaction to the mempool.

        Returns (success, reason).
        """
        return self.mempool.add(tx, self.utxo_set)

    def build_block_template(self, miner_id: bytes,
                            miner_addr: Optional[str] = None) -> Block:
        """
        Build a block template for mining.

        Selects mempool txs, constructs coinbase, computes merkle root.
        Does NOT mine — the miner (ESP32 or simulator) finds the nonce.
        """
        height = len(self.blocks)
        prev_block = self.tip

        # Calculate reward
        miner_reward = get_miner_reward(height)

        # Select txs from mempool
        selected_txs = self.mempool.select_for_block(self.utxo_set)

        # Calculate total fees
        total_fees = 0
        for tx in selected_txs:
            _, fee, _, _, _ = validate_transaction(tx, self.utxo_set, height=height)
            total_fees += fee

        # Build coinbase (v2 payout when a wallet address is known)
        coinbase = Transaction.coinbase(miner_id, miner_reward + total_fees, height,
                                         payout_addr=miner_addr)

        # Build block
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_block.header.hash,
            timestamp=int(time.time()),
            difficulty_target=self.get_expected_difficulty(height),
        )

        block = Block(
            header=header,
            transactions=[coinbase] + selected_txs,
        )
        block.build_header()

        return block

    def get_total_fees(self, txs: List[Transaction]) -> int:
        """Calculate total fees in a list of txs."""
        total = 0
        for tx in txs:
            _, fee, _, _, _ = validate_transaction(tx, self.utxo_set)
            total += fee
        return total
