"""
Tests for Quartz mesh mining pools.

Covers:
1. Solo mode (passthrough, no pooling)
2. Coordinator election (deterministic, lowest hash wins)
3. Share submission and tracking
4. Reward splitting (proportional + finder bonus)
5. Member management (add, prune inactive, max limit)
6. Epoch reset (share counts reset every 16 blocks)
7. Coordinator timeout and re-election
8. Edge cases (no shares, single member, equal shares)
"""

import os
import hashlib
import pytest
import time

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.mesh_pool import (
    MeshPool, PoolMode, PoolShare, PoolMember, PoolPayout,
)


def make_pubkey(seed: int) -> bytes:
    return hashlib.sha256(f"miner-{seed}".encode()).digest()


def make_share(pubkey: bytes, height: int, nonce: int = 0) -> PoolShare:
    return PoolShare(
        miner_pubkey=pubkey,
        height=height,
        nonce=nonce,
        header_hash=hashlib.sha256(f"{pubkey.hex()}{height}{nonce}".encode()).digest(),
        share_sig=b'\x00' * 64,
    )


class TestSoloMode:
    def test_solo_default(self):
        pool = MeshPool(make_pubkey(0))
        assert pool.mode == PoolMode.SOLO
        assert len(pool.members) == 0

    def test_solo_no_split(self):
        pool = MeshPool(make_pubkey(0))
        payouts = pool.compute_split(5000000000, make_pubkey(0), 1)
        assert len(payouts) == 1
        assert payouts[0].amount == 5000000000  # Full reward to finder


class TestCoordinatorElection:
    def test_deterministic_election(self):
        """Same candidates + epoch → same winner every time."""
        candidates = [make_pubkey(i) for i in range(5)]

        pool1 = MeshPool(make_pubkey(0))
        winner1_is_me = pool1.check_election(0, candidates)

        pool2 = MeshPool(make_pubkey(0))
        winner2_is_me = pool2.check_election(0, candidates)

        assert winner1_is_me == winner2_is_me
        # Coordinator should be set
        assert pool1.coordinator_pubkey is not None
        assert pool1.coordinator_pubkey == pool2.coordinator_pubkey

    def test_elected_device_is_lowest_hash(self):
        """The winner has the lowest SHA-256(pubkey || epoch)."""
        candidates = [make_pubkey(i) for i in range(10)]

        # Compute expected winner
        epoch = 0
        hashes = []
        for c in candidates:
            data = c + epoch.to_bytes(4, 'little')
            hashes.append((hashlib.sha256(data).digest(), c))
        expected_winner = min(hashes, key=lambda x: x[0])[1]

        pool = MeshPool(expected_winner)
        assert pool.check_election(0, candidates) is True
        assert pool.coordinator_pubkey == expected_winner

    def test_epoch_rotation_triggers_new_election(self):
        """New epoch triggers re-election (may pick different coordinator)."""
        candidates = [make_pubkey(i) for i in range(3)]
        pool = MeshPool(make_pubkey(99))  # Not a candidate

        pool.check_election(0, candidates)
        coord1 = pool.coordinator_pubkey
        assert coord1 is not None

        # Same epoch — no re-election, same coordinator
        pool.check_election(5, candidates)
        assert pool.coordinator_pubkey == coord1

        # New epoch (block 16) — re-election runs
        pool.check_election(16, candidates)
        assert pool.coordinator_pubkey is not None
        # Winner may differ since epoch is part of the hash

    def test_same_epoch_same_result(self):
        """Within the same epoch, election is deterministic."""
        candidates = [make_pubkey(i) for i in range(5)]

        pool1 = MeshPool(make_pubkey(99))
        pool1.check_election(32, candidates)  # Epoch 2

        pool2 = MeshPool(make_pubkey(99))
        pool2.check_election(35, candidates)  # Same epoch 2

        assert pool1.coordinator_pubkey == pool2.coordinator_pubkey

    def test_different_epoch_different_hash(self):
        """Election hash includes epoch, so different epochs differ."""
        pubkey = make_pubkey(1)
        h0 = hashlib.sha256(pubkey + (0).to_bytes(4, 'little')).digest()
        h1 = hashlib.sha256(pubkey + (1).to_bytes(4, 'little')).digest()
        assert h0 != h1  # Sanity check

    def test_no_candidates(self):
        pool = MeshPool(make_pubkey(0))
        assert pool.check_election(0, []) is False


class TestShareTracking:
    def test_process_share_adds_member(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pubkey = make_pubkey(1)
        share = make_share(pubkey, height=1)
        assert pool.process_share(share) is True

        assert pubkey in pool.members
        assert pool.members[pubkey].shares == 1

    def test_multiple_shares_tracked(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pubkey = make_pubkey(1)
        for i in range(10):
            pool.process_share(make_share(pubkey, height=1, nonce=i))

        assert pool.members[pubkey].shares == 10
        assert pool.members[pubkey].total_shares == 10

    def test_member_not_coordinator_rejects_shares(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.MEMBER)

        share = make_share(make_pubkey(1), height=1)
        assert pool.process_share(share) is False


class TestRewardSplit:
    def test_proportional_split(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        # Add 3 members with different share counts
        for i, shares in enumerate([10, 30, 60]):
            pubkey = make_pubkey(i + 1)
            pool.add_member(pubkey)
            for _ in range(shares):
                pool.process_share(make_share(pubkey, height=1))

        total_reward = 47_500_000_000  # 47.5 QZ in sats
        payouts = pool.compute_split(total_reward, make_pubkey(1), height=1)

        assert len(payouts) == 3

        # Member with 60 shares should get more than 30, which gets more than 10
        amounts_by_pubkey = {p.pubkey: p.amount for p in payouts}
        assert amounts_by_pubkey[make_pubkey(3)] > amounts_by_pubkey[make_pubkey(2)]
        assert amounts_by_pubkey[make_pubkey(2)] > amounts_by_pubkey[make_pubkey(1)]

    def test_finder_bonus(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        # Two members with equal shares
        for i in range(1, 3):
            pubkey = make_pubkey(i)
            pool.add_member(pubkey)
            for _ in range(50):
                pool.process_share(make_share(pubkey, height=1))

        total_reward = 47_500_000_000
        payouts = pool.compute_split(total_reward, make_pubkey(1), height=1)

        # Finder (make_pubkey(1)) should get more than non-finder
        amounts = {p.pubkey: p.amount for p in payouts}
        assert amounts[make_pubkey(1)] > amounts[make_pubkey(2)]

    def test_no_shares_finder_gets_all(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)
        pool.add_member(make_pubkey(1))  # Active but 0 shares

        payouts = pool.compute_split(47_500_000_000, make_pubkey(0), height=1)
        assert len(payouts) == 1
        assert payouts[0].pubkey == make_pubkey(0)
        assert payouts[0].amount == 47_500_000_000

    def test_total_payout_equals_reward(self):
        """Sum of all payouts should equal total reward (no creation/destruction)."""
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        for i in range(1, 6):
            pubkey = make_pubkey(i)
            pool.add_member(pubkey)
            for j in range(i * 7):  # Uneven share counts
                pool.process_share(make_share(pubkey, height=1, nonce=j))

        total_reward = 47_500_000_000
        payouts = pool.compute_split(total_reward, make_pubkey(3), height=1)

        total_paid = sum(p.amount for p in payouts)
        assert total_paid == total_reward, f"Paid {total_paid}, expected {total_reward}"

    def test_single_member_gets_full(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        # Only ourselves (coordinator is auto-added)
        for _ in range(10):
            pool.process_share(make_share(pool.my_pubkey, height=1))

        payouts = pool.compute_split(47_500_000_000, pool.my_pubkey, height=1)
        assert len(payouts) >= 1
        assert sum(p.amount for p in payouts) == 47_500_000_000


class TestMemberManagement:
    def test_max_members(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        for i in range(1, MeshPool.MAX_MEMBERS):  # -1 because coordinator is already in
            assert pool.add_member(make_pubkey(i)) is True

        # Should fail when full
        assert pool.add_member(make_pubkey(999)) is False

    def test_add_duplicate_updates_existing(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pubkey = make_pubkey(1)
        pool.add_member(pubkey, rssi=-70)
        pool.add_member(pubkey, rssi=-65)  # Update

        assert len(pool.members) == 2  # coordinator + 1
        assert pool.members[pubkey].rssi == -65

    def test_prune_inactive(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pubkey = make_pubkey(1)
        pool.add_member(pubkey)
        pool.process_share(make_share(pubkey, height=1))

        # Member active
        assert pool.members[pubkey].active is True

        # Prune after epoch with no shares
        pool.prune_inactive(current_height=20)
        assert pool.members[pubkey].active is False


class TestEpochReset:
    def test_reset_clears_shares(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pubkey = make_pubkey(1)
        for _ in range(20):
            pool.process_share(make_share(pubkey, height=1))

        assert pool.members[pubkey].shares == 20

        pool.reset_epoch()
        assert pool.members[pubkey].shares == 0
        assert pool.members[pubkey].total_shares == 20  # Lifetime preserved

    def test_reset_clears_epoch_shares_list(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        pool.process_share(make_share(make_pubkey(1), height=1))
        pool.process_share(make_share(make_pubkey(2), height=1))
        assert len(pool.epoch_shares) == 2

        pool.reset_epoch()
        assert len(pool.epoch_shares) == 0


class TestCoordinatorTimeout:
    def test_timeout_triggers(self):
        """Coordinator times out after TIMEOUT_BLOCKS without communication."""
        pool = MeshPool(make_pubkey(0))
        # block_height=10, last_coord=8 → diff=2 → no timeout (< 3)
        assert pool.coordinator_timeout(block_height=10, last_coord_block=8) is False
        # block_height=10, last_coord=7 → diff=3 → timeout (>= 3)
        assert pool.coordinator_timeout(block_height=10, last_coord_block=7) is True
        # block_height=10, last_coord=5 → diff=5 → timeout
        assert pool.coordinator_timeout(block_height=10, last_coord_block=5) is True

    def test_timeout_exactly_at_threshold(self):
        pool = MeshPool(make_pubkey(0))
        # TIMEOUT_BLOCKS=3: 10-7=3 → timeout (>=)
        assert pool.coordinator_timeout(10, 7) is True
        # 10-8=2 → no timeout
        assert pool.coordinator_timeout(10, 8) is False


class TestStats:
    def test_stats_after_activity(self):
        pool = MeshPool(make_pubkey(0))
        pool.set_mode(PoolMode.COORDINATOR)

        for i in range(1, 4):
            pubkey = make_pubkey(i)
            pool.add_member(pubkey)
            for _ in range(i * 5):
                pool.process_share(make_share(pubkey, height=1))

        stats = pool.get_stats()
        assert stats['mode'] == 'COORDINATOR'
        assert stats['members'] == 4  # coordinator + 3
        assert stats['pool_shares'] == 30  # 5 + 10 + 15
