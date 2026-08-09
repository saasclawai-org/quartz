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
MINER_REWARD = int(47.5 * 10**8)  # 47.5 QZ to miner (95%)
DEV_FUND_REWARD = int(2.5 * 10**8)  # 2.5 QZ to dev fund (5%)
TOTAL_SUPPLY = 42_000_000 * 10**8
DEV_FUND_TOTAL = 2_100_000 * 10**8  # 5%
EARLY_BONUS_MINERS = 1000  # first 1000 unique ESP32s
EARLY_BONUS_DAYS = 30
DIFFICULTY_BITS = 20
RETARGET_PERIOD = 144


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

    def serialize(self) -> bytes:
        parts = [struct.pack('B', self.version)]
        parts.append(struct.pack('B', len(self.inputs)))
        for prev_hash, idx, sig, pubkey in self.inputs:
            parts.append(prev_hash + struct.pack('B', idx) + sig + pubkey)
        parts.append(struct.pack('B', len(self.outputs)))
        for amount, script in self.outputs:
            parts.append(struct.pack('<Q', amount) + script)
        parts.append(struct.pack('<I', self.locktime))
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


def get_block_reward(height: int) -> int:
    """Calculate total block reward at given height."""
    halvings = height // HALVING_INTERVAL
    if halvings >= 33:
        return 0
    return INITIAL_REWARD >> halvings


def get_miner_reward(height: int) -> int:
    """Calculate miner's portion of block reward (95%)."""
    total = get_block_reward(height)
    return int(total * 0.95)


def get_dev_fund_reward(height: int) -> int:
    """Calculate dev fund portion of block reward (5%).

    Dev fund emission ends after ~525,600 blocks (~4 years).
    After that, 100% goes to miners.
    """
    if height > 525_600:  # ~4 years at 120s blocks
        return 0
    total = get_block_reward(height)
    return int(total * 0.05)


def is_early_bonus_eligible(miner_id: bytes, known_miners: set, first_mined: dict, current_time: int) -> bool:
    """Check if a miner is eligible for the 2x early adopter bonus.

    - Must be one of the first 1000 unique ESP32 miners
    - 2x reward applies for first 30 days from their first mined block
    """
    if len(known_miners) >= EARLY_BONUS_MINERS and miner_id not in known_miners:
        return False
    if miner_id not in first_mined:
        return True  # new miner, eligible
    elapsed = current_time - first_mined[miner_id]
    return elapsed < (EARLY_BONUS_DAYS * 86400)


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
