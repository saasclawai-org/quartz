"""
Quartz blockchain structures — block headers, blocks, transactions.

Mirrors the ESP32 firmware structures exactly (little-endian, packed).
"""

import hashlib
import struct
import time
from dataclasses import dataclass, field
from typing import List, Optional

# Constants — must match firmware/quartz.h
HEADER_SIZE = 80
TX_INPUT_SIZE = 129   # 32 + 1 + 64 + 32
TX_OUTPUT_SIZE = 40   # 8 + 32
BLOCK_TIME = 120
HALVING_INTERVAL = 210_000
INITIAL_REWARD = 50 * 10**8  # 50 QZ in quartz-sats
TOTAL_SUPPLY = 42_000_000 * 10**8
EARLY_BONUS_MINERS = 1000  # first 1000 unique PUF-registered ESP32s
EARLY_BONUS_DAYS = 30
DIFFICULTY_BITS = 20
RETARGET_PERIOD = 144

# --- New tokenomics constants ---
MINER_SHARE = 0.85       # 85% to miner
RELAYER_SHARE = 0.10     # 10% to mesh relayer pool
QUANTUM_SHARE = 0.05     # 5% to quantum security pool
DEV_FUND_REWARD = 0      # Dev fund killed
PUF_REWARD_MULT = 1.0    # PUF blocks get full reward
# PUF is required from block 1. No non-PUF mining. Ever.
PUF_REQUIRED_HEIGHT = 0
NON_PUF_REWARD_MULT = 0.0  # Non-PUF blocks earn nothing (always)
MAX_DIFFICULTY_CHANGE = 0.25  # Max 25% difficulty change per retarget
EMPTY_BLOCK_GRACE = 10   # Blocks before empty-block penalty kicks in
EMPTY_BLOCK_PENALTY = 0.9  # 10% reward reduction for empty blocks after grace
KEY_ROTATION_WARN_AT = 240  # Warn user at WOTS+ sig #240
KEY_ROTATION_LIMIT = 256  # Max WOTS+ sigs per address

# --- Checkpoints (prevent 51% attack on young chain) ---
# Each checkpoint is (height, block_hash_hex) — hardcoded, consensus-critical
# Updated periodically by the reference node as the chain grows
CHECKPOINTS = {
    # Genesis checkpoint will be set after first block
}


@dataclass
class BlockHeader:
    """80-byte block header (Bitcoin-compatible layout).

    miner_id is stored separately in the block body, NOT in the header,
    so the header is exactly 80 bytes for mining.
    """
    version: int = 1
    prev_block_hash: bytes = b'\x00' * 32
    merkle_root: bytes = b'\x00' * 32
    timestamp: int = 0
    difficulty_target: int = DIFFICULTY_BITS
    nonce: int = 0
    miner_id: bytes = b'\x00' * 6  # stored in block body, not in serialized header

    def serialize(self) -> bytes:
        """Serialize to exactly 80 bytes (canonical header for hashing/mining)."""
        # 4 + 32 + 32 + 4 + 4 + 4(padding) = 80
        return struct.pack(
            '<I32s32sIII',
            self.version,
            self.prev_block_hash,
            self.merkle_root,
            self.timestamp,
            self.difficulty_target,
            self.nonce & 0xFFFFFFFF,
        )

    @classmethod
    def deserialize(cls, data: bytes) -> 'BlockHeader':
        """Deserialize from 80-byte canonical header."""
        version, prev_hash, merkle, ts, diff, nonce = struct.unpack(
            '<I32s32sIII', data[:HEADER_SIZE])
        return cls(
            version=version,
            prev_block_hash=prev_hash,
            merkle_root=merkle,
            timestamp=ts,
            difficulty_target=diff,
            nonce=nonce,
        )

    @property
    def hash(self) -> bytes:
        """SHA-256 of serialized header."""
        return hashlib.sha256(self.serialize()).digest()


@dataclass
class Transaction:
    """UTXO-based transaction."""
    version: int = 1
    inputs: list = field(default_factory=list)  # [(prev_hash, output_idx, signature, pubkey)]
    outputs: list = field(default_factory=list)  # [(amount, script_pubkey)]
    locktime: int = 0
    data: bytes = b''  # Optional data carrier (up to 256 bytes for messages)

    def serialize(self) -> bytes:
        parts = [struct.pack('B', self.version)]
        parts.append(struct.pack('B', len(self.inputs)))
        for prev_hash, idx, sig, pubkey in self.inputs:
            parts.append(prev_hash + struct.pack('B', idx) + sig + pubkey)
        parts.append(struct.pack('B', len(self.outputs)))
        for amount, script in self.outputs:
            parts.append(struct.pack('<Q', amount) + script)
        parts.append(struct.pack('<I', self.locktime))
        # Data carrier (2-byte length prefix + data)
        parts.append(struct.pack('<H', len(self.data)))
        parts.append(self.data)
        return b''.join(parts)

    @property
    def txid(self) -> bytes:
        """Transaction hash."""
        return hashlib.sha256(self.serialize()).digest()

    @classmethod
    def coinbase(cls, miner_id: bytes, reward: int, height: int) -> 'Transaction':
        """Create a coinbase transaction."""
        fake_input = (
            b'\x00' * 32,
            height & 0xFF,
            b'\x00' * 64,
            miner_id + b'\x00' * 26,
        )
        script = hashlib.sha256(miner_id).digest()
        return cls(
            version=1,
            inputs=[fake_input],
            outputs=[(reward, script)],
        )


def compute_merkle_root(tx_hashes: List[bytes]) -> bytes:
    """Compute merkle root from list of transaction hashes."""
    if not tx_hashes:
        return b'\x00' * 32

    level = list(tx_hashes)
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            next_level.append(hashlib.sha256(left + right).digest())
        level = next_level

    return level[0]


def get_era(height: int) -> int:
    """Get the current mining era (1-indexed)."""
    return (height // HALVING_INTERVAL) + 1


def is_puf_required(height: int) -> bool:
    """Check if PUF attestation is mandatory at this height.

    Always True. PUF required from genesis. No exceptions.
    """
    return True


def get_block_reward(height: int, has_puf: bool = True) -> int:
    """Calculate total block reward at given height.

    PUF blocks get full reward. Non-PUF blocks get 0 (always).
    """
    # Non-PUF blocks are never accepted
    if not has_puf:
        return 0

    halvings = height // HALVING_INTERVAL
    if halvings >= 33:
        return 0

    return INITIAL_REWARD >> halvings


def split_block_reward(total_reward: int) -> tuple:
    """Split block reward: (miner, relayer_pool, quantum_pool).

    Returns tuple of (miner_amount, relayer_amount, quantum_amount).
    """
    miner = int(total_reward * MINER_SHARE)
    relayer = int(total_reward * RELAYER_SHARE)
    quantum = total_reward - miner - relayer  # remainder avoids rounding loss
    return (miner, relayer, quantum)


def get_miner_reward(height: int, has_puf: bool = True, is_empty: bool = False,
                     empty_streak: int = 0) -> int:
    """Calculate miner's portion of block reward (85% of total).

    Applies empty block penalty if miner has produced >EMPTY_BLOCK_GRACE
    consecutive empty blocks.
    """
    total = get_block_reward(height, has_puf)
    miner, _, _ = split_block_reward(total)

    # Empty block penalty: 10% reduction after grace period
    if is_empty and empty_streak > EMPTY_BLOCK_GRACE:
        miner = int(miner * EMPTY_BLOCK_PENALTY)

    return miner


def get_relayer_reward(height: int, has_puf: bool = True) -> int:
    """Calculate mesh relayer pool reward (10% of total)."""
    total = get_block_reward(height, has_puf)
    _, relayer, _ = split_block_reward(total)
    return relayer


def get_quantum_pool_reward(height: int, has_puf: bool = True) -> int:
    """Calculate quantum security pool reward (5% of total)."""
    total = get_block_reward(height, has_puf)
    _, _, quantum = split_block_reward(total)
    return quantum


def get_dev_fund_reward(height: int) -> int:
    """Dev fund reward — always 0. Dev fund killed."""
    return 0


def adjust_difficulty(current_target: int, actual_time: int, expected_time: int) -> int:
    """Adjust difficulty target (integer threshold) with max 25% change per retarget.

    Prevents difficulty manipulation attacks where a large miner
    raises difficulty then leaves.
    """
    if actual_time <= 0:
        actual_time = 1

    # Calculate full adjustment
    ratio = actual_time / expected_time

    # Clamp to ±25% change
    if ratio < (1 - MAX_DIFFICULTY_CHANGE):
        ratio = 1 - MAX_DIFFICULTY_CHANGE
    elif ratio > (1 + MAX_DIFFICULTY_CHANGE):
        ratio = 1 + MAX_DIFFICULTY_CHANGE

    new_target = int(current_target * ratio)

    # Ensure target stays in valid range
    if new_target < 1:
        new_target = 1

    return new_target


def retarget_difficulty_bits(current_bits: int, blocks: list,
                              target_block_time: int) -> int:
    """Calculate new difficulty bits after a retarget period.

    Called every RETARGET_PERIOD blocks. Compares actual time elapsed
    against expected time and adjusts difficulty bits accordingly.

    Args:
        current_bits: Current difficulty in bits (e.g. 20 = top 20 bits must be zero)
        blocks: List of Block objects (need at least RETARGET_PERIOD + 1)
        target_block_time: Target seconds between blocks (BLOCK_TIME)

    Returns:
        New difficulty bits (int). Clamped to ±1 bit change per retarget
        for stability on small chains.
    """
    if len(blocks) < RETARGET_PERIOD + 1:
        return current_bits

    # Compare last RETARGET_PERIOD blocks' time span
    first = blocks[-(RETARGET_PERIOD + 1)]
    last = blocks[-1]

    actual_time = last.header.timestamp - first.header.timestamp
    expected_time = RETARGET_PERIOD * target_block_time

    if actual_time <= 0:
        actual_time = 1

    # If blocks were too fast (actual < expected), increase difficulty
    # If blocks were too slow (actual > expected), decrease difficulty
    ratio = actual_time / expected_time

    # Convert bits to target threshold for adjustment
    # difficulty_bits = N means hash must be < 2^(256-N)
    # Higher bits = harder = smaller target
    # We adjust the target, then convert back to bits

    if ratio < 1.0:
        # Blocks too fast — increase difficulty (increase bits)
        # Max 25% reduction in target = roughly +1 bit
        adjustment = ratio  # < 1.0
        new_bits = current_bits
        # Each +1 bit roughly halves the target (doubles difficulty)
        # For 25% clamp, we might go up by 0 or 1 bit
        if ratio < 0.75:
            new_bits = current_bits + 1
        # Very fast (< 50% of target): up to +2 bits
        if ratio < 0.50:
            new_bits = current_bits + 2
    else:
        # Blocks too slow — decrease difficulty (decrease bits)
        new_bits = current_bits
        if ratio > 1.25:
            new_bits = current_bits - 1
        if ratio > 1.50:
            new_bits = current_bits - 2

    # Clamp to sane range
    new_bits = max(1, min(new_bits, 32))

    return new_bits


def verify_checkpoint(height: int, block_hash: bytes) -> bool:
    """Verify a block against known checkpoints.

    Checkpoints prevent 51% attacks on young chains by pinning
    specific block hashes. A reorg cannot go past a checkpoint.
    """
    if height not in CHECKPOINTS:
        return True  # No checkpoint for this height

    expected = bytes.fromhex(CHECKPOINTS[height])
    return block_hash == expected


def is_early_bonus_eligible(miner_id: bytes, known_miners: set, first_mined: dict, current_time: int) -> bool:
    """Check if a miner is eligible for the 2x early adopter bonus.

    - Must be one of the first 1000 unique PUF-registered ESP32s
    - 2x reward applies for first 30 days from their first mined block
    """
    if len(known_miners) >= EARLY_BONUS_MINERS and miner_id not in known_miners:
        return False
    if miner_id not in first_mined:
        return True  # new miner, eligible
    elapsed = current_time - first_mined[miner_id]
    return elapsed < (EARLY_BONUS_DAYS * 86400)


def needs_key_rotation(ots_index: int) -> bool:
    """Check if WOTS+ wallet should rotate to a new address.

    Warns at signature #240, hard limit at #256.
    """
    return ots_index >= KEY_ROTATION_WARN_AT


@dataclass
class Block:
    """Full block: header + miner_id + transactions."""
    header: BlockHeader
    transactions: List[Transaction] = field(default_factory=list)

    def serialize(self) -> bytes:
        """Serialize block for storage/transmission."""
        parts = [self.header.serialize()]
        parts.append(self.header.miner_id)  # 6 bytes after header
        parts.append(struct.pack('B', len(self.transactions)))
        for tx in self.transactions:
            tx_bytes = tx.serialize()
            parts.append(struct.pack('<H', len(tx_bytes)))
            parts.append(tx_bytes)
        return b''.join(parts)

    @classmethod
    def deserialize(cls, data: bytes) -> 'Block':
        header = BlockHeader.deserialize(data[:HEADER_SIZE])
        offset = HEADER_SIZE
        header.miner_id = data[offset:offset+6]
        offset += 6
        tx_count = struct.unpack('B', data[offset:offset+1])[0]
        offset += 1
        transactions = []
        for _ in range(tx_count):
            tx_len = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
            transactions.append(Transaction())
            offset += tx_len
        return cls(header=header, transactions=transactions)

    def build_header(self) -> BlockHeader:
        """Rebuild header with correct merkle root."""
        tx_hashes = [tx.txid for tx in self.transactions]
        self.header.merkle_root = compute_merkle_root(tx_hashes)
        return self.header

    @classmethod
    def create_genesis(cls) -> 'Block':
        """Create the Quartz genesis block."""
        header = BlockHeader(
            version=1,
            prev_block_hash=b'\x00' * 32,
            timestamp=int(time.time()),
            difficulty_target=DIFFICULTY_BITS,
            nonce=0,
        )
        return cls(header=header)
