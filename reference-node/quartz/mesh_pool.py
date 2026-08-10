"""
Quartz Mesh Mining Pools — Python Reference Implementation

Decentralized mining pools that run over LoRa mesh. No central operator,
no server, no fees. Local clusters of ESP32s share work and split rewards.

Modes:
    SOLO        — Mine independently. Full reward, full variance.
    MEMBER      — Join a pool, submit shares, get proportional payout.
    COORDINATOR — Accept members, distribute work, compute reward splits.

Election:
    Every 16 blocks, the coordinator is deterministically elected as the
    candidate with the lowest SHA-256(pubkey || epoch). All members compute
    the same result — no voting needed.

Reward split:
    When the pool finds a block, the coinbase has multiple outputs — one
    per active member, proportional to their share count in the epoch.
    The block finder gets a 5% finder's bonus.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import IntEnum


class PoolMode(IntEnum):
    SOLO = 0
    MEMBER = 1
    COORDINATOR = 2


@dataclass
class PoolShare:
    """A near-miss proof of work at pool difficulty."""
    miner_pubkey: bytes     # 32 bytes
    height: int
    nonce: int
    header_hash: bytes      # 32 bytes
    share_sig: bytes        # 64 bytes

    def to_dict(self) -> dict:
        return {
            'miner': self.miner_pubkey.hex()[:16] + '...',
            'height': self.height,
            'nonce': self.nonce,
            'hash': self.header_hash.hex()[:16] + '...',
        }


@dataclass
class PoolMember:
    """A miner participating in this pool."""
    pubkey: bytes
    chip_id: bytes = b'\x00' * 6
    shares: int = 0
    total_shares: int = 0
    last_share_height: int = 0
    last_seen: float = 0.0
    rssi: int = 0
    active: bool = True

    def to_dict(self) -> dict:
        return {
            'pubkey': self.pubkey.hex()[:16] + '...',
            'shares': self.shares,
            'total': self.total_shares,
            'active': self.active,
            'rssi': self.rssi,
        }


@dataclass
class PoolPayout:
    """Reward split entry for coinbase transaction."""
    pubkey: bytes
    amount: int  # sats


class MeshPool:
    """
    Decentralized mesh mining pool.

    Used by COORDINATOR nodes to track members, validate shares,
    and compute reward splits. Used by MEMBER nodes to submit shares.

    In SOLO mode, acts as a passthrough — no pooling.
    """

    MAX_MEMBERS = 32
    EPOCH_BLOCKS = 16
    TIMEOUT_BLOCKS = 3
    FINDER_BONUS_PCT = 5  # 5% bonus to block finder

    def __init__(self, my_pubkey: bytes = b'\x00' * 32):
        self.my_pubkey = my_pubkey
        self.mode = PoolMode.SOLO
        self.members: Dict[bytes, PoolMember] = {}
        self.coordinator_pubkey: Optional[bytes] = None
        self.current_epoch = 0
        self.blocks_found = 0
        self.rewards_earned = 0  # sats
        self.my_shares = 0

        # Coordinator-only state
        self.epoch_shares: List[PoolShare] = []

    def set_mode(self, mode: PoolMode):
        """Change pool mode."""
        if mode == self.mode:
            return

        if mode == PoolMode.COORDINATOR:
            # Add ourselves
            self.members[self.my_pubkey] = PoolMember(
                pubkey=self.my_pubkey,
                last_seen=time.time(),
            )
        elif mode == PoolMode.SOLO:
            self.members.clear()
            self.epoch_shares.clear()
            self.coordinator_pubkey = None

        self.mode = mode

    def add_member(self, pubkey: bytes, chip_id: bytes = None, rssi: int = 0) -> bool:
        """Add a member to the pool (coordinator only)."""
        if pubkey in self.members:
            # Update existing
            self.members[pubkey].last_seen = time.time()
            self.members[pubkey].rssi = rssi
            self.members[pubkey].active = True
            return True

        if len(self.members) >= self.MAX_MEMBERS:
            return False

        self.members[pubkey] = PoolMember(
            pubkey=pubkey,
            chip_id=chip_id or b'\x00' * 6,
            rssi=rssi,
            last_seen=time.time(),
        )
        return True

    def submit_share(self, share: PoolShare) -> bool:
        """Submit a share to the pool (member mode)."""
        if self.mode != PoolMode.MEMBER:
            return False

        self.my_shares += 1
        return True

    def process_share(self, share: PoolShare) -> bool:
        """Process an incoming share from a member (coordinator mode)."""
        if self.mode != PoolMode.COORDINATOR:
            return False

        # Add member if new
        if share.miner_pubkey not in self.members:
            if not self.add_member(share.miner_pubkey):
                return False

        # Update member stats
        member = self.members[share.miner_pubkey]
        member.shares += 1
        member.total_shares += 1
        member.last_share_height = share.height
        member.last_seen = time.time()
        member.active = True

        # Store share for epoch
        self.epoch_shares.append(share)

        return True

    def compute_split(
        self,
        total_reward: int,
        block_finder_pubkey: bytes,
        height: int
    ) -> List[PoolPayout]:
        """
        Compute proportional reward split for this epoch.

        - Each member gets reward proportional to their share count
        - Block finder gets FINDER_BONUS_PCT extra
        - Remainder (rounding) goes to finder
        """
        active_members = {k: v for k, v in self.members.items()
                          if v.active and v.shares > 0}

        if not active_members:
            # No shares — finder gets everything
            return [PoolPayout(pubkey=block_finder_pubkey, amount=total_reward)]

        total_shares = sum(m.shares for m in active_members.values())
        if total_shares == 0:
            return [PoolPayout(pubkey=block_finder_pubkey, amount=total_reward)]

        # Finder bonus
        finder_bonus = int(total_reward * self.FINDER_BONUS_PCT / 100)
        remaining = total_reward - finder_bonus

        payouts = []
        distributed = 0

        for pubkey, member in active_members.items():
            proportional = (remaining * member.shares) // total_shares
            distributed += proportional

            if pubkey == block_finder_pubkey:
                proportional += finder_bonus

            if proportional > 0:
                payouts.append(PoolPayout(pubkey=pubkey, amount=proportional))

        # Remainder to finder
        remainder = total_reward - distributed - finder_bonus
        if remainder > 0:
            for p in payouts:
                if p.pubkey == block_finder_pubkey:
                    p.amount += remainder
                    break
            else:
                payouts.append(PoolPayout(
                    pubkey=block_finder_pubkey, amount=remainder
                ))

        return payouts

    def check_election(
        self,
        block_height: int,
        candidates: List[bytes]
    ) -> bool:
        """
        Deterministic coordinator election.

        Returns True if THIS device should be coordinator.
        Lowest SHA-256(pubkey || epoch_number) wins.
        """
        epoch = block_height // self.EPOCH_BLOCKS

        if epoch == self.current_epoch and self.coordinator_pubkey is not None:
            # Already elected for this epoch
            return self.coordinator_pubkey == self.my_pubkey

        self.current_epoch = epoch

        if not candidates:
            return False

        # Compute hashes
        best_hash = None
        winner = None

        for candidate in candidates:
            data = candidate + epoch.to_bytes(4, 'little')
            h = hashlib.sha256(data).digest()
            if best_hash is None or h < best_hash:
                best_hash = h
                winner = candidate

        self.coordinator_pubkey = winner
        return winner == self.my_pubkey

    def coordinator_timeout(self, block_height: int, last_coord_block: int) -> bool:
        """Check if coordinator has timed out."""
        return block_height - last_coord_block >= self.TIMEOUT_BLOCKS

    def prune_inactive(self, current_height: int):
        """Mark members as inactive if they haven't submitted shares recently."""
        for member in self.members.values():
            if current_height - member.last_share_height > self.EPOCH_BLOCKS:
                member.active = False

    def reset_epoch(self):
        """Reset share counts for new epoch."""
        for member in self.members.values():
            member.shares = 0
        self.epoch_shares.clear()

    def get_stats(self) -> dict:
        active = sum(1 for m in self.members.values() if m.active)
        total_shares = sum(m.shares for m in self.members.values())
        return {
            'mode': PoolMode(self.mode).name,
            'members': len(self.members),
            'active_members': active,
            'my_shares': self.my_shares,
            'pool_shares': total_shares,
            'blocks_found': self.blocks_found,
            'rewards_qz': self.rewards_earned / 1e8,
            'coordinator': self.coordinator_pubkey.hex()[:16] + '...' if self.coordinator_pubkey else None,
            'epoch': self.current_epoch,
        }

    def list_members(self) -> List[dict]:
        """List members sorted by shares."""
        members = sorted(self.members.values(), key=lambda m: m.shares, reverse=True)
        return [
            {
                'pubkey': m.pubkey.hex()[:16] + '...',
                'shares': m.shares,
                'total': m.total_shares,
                'active': m.active,
                'rssi': m.rssi,
            }
            for m in members
        ]
