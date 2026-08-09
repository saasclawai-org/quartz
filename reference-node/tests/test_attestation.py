"""
Tests for Quartz device attestation and registry.

Covers:
1. Device registration (valid, duplicate, IP limit, bad pubkey)
2. Block attestation verification (valid, unregistered, banned, flagged)
3. Anti-cheat timing detection (too-fast blocks, auto-ban)
4. Slashing (double-sign evidence, ban enforcement)
5. Early adopter tracking
6. Persistence across restart
7. Registry statistics
"""

import os
import shutil
import tempfile
import hashlib
import pytest
import time
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.attestation import (
    DeviceRegistry, DeviceStatus, MinerDevice, SlashEvidence,
)


def make_pubkey(seed: int) -> bytes:
    """Generate deterministic pubkey for testing."""
    return hashlib.sha256(f"device-{seed}".encode()).digest()


@pytest.fixture
def registry():
    d = tempfile.mkdtemp(prefix='qz_attest_')
    reg_path = os.path.join(d, 'registry.json')
    yield DeviceRegistry(data_path=reg_path)
    shutil.rmtree(d, ignore_errors=True)


class TestRegistration:
    def test_register_new_device(self, registry):
        pubkey = make_pubkey(1)
        chip_id = b'\xAA\xBB\xCC\x01\x00\x01'
        ok, msg, device = registry.register_device(pubkey, chip_id)

        assert ok is True
        assert device is not None
        assert device.pubkey == pubkey
        assert device.status == DeviceStatus.ACTIVE
        assert device.early_adopter is True

    def test_register_duplicate(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        ok, msg, device = registry.register_device(pubkey, b'\x00' * 6)
        assert ok is False
        assert "already" in msg.lower()

    def test_register_bad_pubkey_length(self, registry):
        ok, msg, device = registry.register_device(b'\x00' * 10, b'\x00' * 6)
        assert ok is False
        assert "length" in msg.lower() or "invalid" in msg.lower()

    def test_ip_limit(self, registry):
        """Max 5 devices per IP."""
        for i in range(5):
            ok, _, _ = registry.register_device(
                make_pubkey(i), b'\x00\x00\x00\x00\x00' + bytes([i]),
                source_ip="1.2.3.4"
            )
            assert ok is True

        ok, msg, _ = registry.register_device(
            make_pubkey(99), b'\x00\x00\x00\x00\x00\x63',
            source_ip="1.2.3.4"
        )
        assert ok is False
        assert "limit" in msg.lower()

    def test_different_ip_no_limit(self, registry):
        """Different IPs can each register devices."""
        for i in range(10):
            ip = f"10.0.{i // 5}.{i % 5}"
            ok, _, _ = registry.register_device(
                make_pubkey(i), b'\x00' * 5 + bytes([i]),
                source_ip=ip
            )
            assert ok is True

    def test_early_adopter_limit(self, registry):
        registry.max_early_adopters = 3

        for i in range(3):
            _, _, dev = registry.register_device(make_pubkey(i), b'\x00' * 5 + bytes([i]))
            assert dev.early_adopter is True

        _, _, dev = registry.register_device(make_pubkey(99), b'\x00' * 5 + b'\x63')
        assert dev.early_adopter is False


class TestBlockVerification:
    def test_valid_attestation(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        header_hash = hashlib.sha256(b"block").digest()
        sig = b'\x42' * 64  # Fake sig (reference accepts all)

        ok, reason = registry.verify_block_attestation(
            header_hash, 12345, pubkey, sig, height=1
        )
        assert ok is True

    def test_unregistered_device(self, registry):
        pubkey = make_pubkey(999)  # Not registered
        header_hash = hashlib.sha256(b"block").digest()
        sig = b'\x42' * 64

        ok, reason = registry.verify_block_attestation(
            header_hash, 12345, pubkey, sig, height=1
        )
        assert ok is False
        assert "not registered" in reason.lower()

    def test_banned_device_rejected(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        # Ban the device
        device_id = registry.pubkey_to_device_id[pubkey]
        registry.devices[device_id].status = DeviceStatus.BANNED
        registry.devices[device_id].ban_reason = "Testing ban"
        registry.banned_pubkeys.add(pubkey)

        header_hash = hashlib.sha256(b"block").digest()
        sig = b'\x42' * 64

        ok, reason = registry.verify_block_attestation(
            header_hash, 12345, pubkey, sig, height=1
        )
        assert ok is False
        assert "banned" in reason.lower()

    def test_bad_signature_rejected(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        header_hash = hashlib.sha256(b"block").digest()
        sig = b'\x00' * 10  # Wrong length

        ok, reason = registry.verify_block_attestation(
            header_hash, 12345, pubkey, sig, height=1
        )
        assert ok is False

    def test_double_mining_same_height(self, registry):
        """Device mining two blocks at same height gets banned."""
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        header_hash = hashlib.sha256(b"block1").digest()
        sig = b'\x42' * 64

        # First block at height 5
        registry.record_mined_block(pubkey, 5, 50 * 10**8)

        # Manually set last_block_time in the past to avoid timing ban
        device_id = registry.pubkey_to_device_id[pubkey]
        registry.devices[device_id].last_block_time = time.time() - 60

        ok, _ = registry.verify_block_attestation(header_hash, 100, pubkey, sig, height=6)
        assert ok is True

        # Record block at height 6
        registry.record_mined_block(pubkey, 6, 50 * 10**8)

        # Set time in the past again
        registry.devices[device_id].last_block_time = time.time() - 60

        # Try another block at height 6 — should be banned for double-mining
        ok, reason = registry.verify_block_attestation(
            hashlib.sha256(b"block2").digest(), 200, pubkey, sig, height=6
        )
        assert ok is False
        assert "double" in reason.lower() or "banned" in reason.lower()


class TestAntiCheatTiming:
    def test_fast_blocks_flagged(self, registry):
        """Blocks arriving too fast get flagged."""
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)
        registry.MIN_BLOCK_INTERVAL = 120
        registry.ABNORMAL_THRESHOLD = 0.5

        device_id = registry.pubkey_to_device_id[pubkey]
        device = registry.devices[device_id]

        # Simulate 3 fast blocks (< 60s apart)
        for h in range(3):
            device.recent_block_times.append(30.0)  # 30s — faster than 50% of 120s

        consecutive = registry._count_consecutive_fast(device)
        assert consecutive == 3

    def test_impossible_speed_banned(self, registry):
        """Blocks arriving impossibly fast → auto-ban."""
        registry.MIN_BLOCK_INTERVAL = 120
        registry.BAN_THRESHOLD = 0.25

        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        sig = b'\x42' * 64
        header_hash = hashlib.sha256(b"block1").digest()

        # Record first block
        registry.record_mined_block(pubkey, 0, 50 * 10**8)

        # Immediately verify another — interval is ~0s
        ok, reason = registry.verify_block_attestation(
            header_hash, 100, pubkey, sig, height=1
        )
        assert ok is False
        assert "ban" in reason.lower()

        device = registry.devices[registry.pubkey_to_device_id[pubkey]]
        assert device.status == DeviceStatus.BANNED

    def test_normal_pace_ok(self, registry):
        """Normal block intervals don't trigger flags."""
        registry.MIN_BLOCK_INTERVAL = 30  # Testnet

        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        device_id = registry.pubkey_to_device_id[pubkey]
        device = registry.devices[device_id]

        # Simulate 5 normal-interval blocks
        for t in [30, 32, 28, 35, 31]:
            device.recent_block_times.append(float(t))

        consecutive = registry._count_consecutive_fast(device)
        assert consecutive == 0


class TestSlashing:
    def test_valid_slash_evidence(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        evidence = SlashEvidence(
            pubkey=pubkey,
            block_hash_1=hashlib.sha256(b"block_a").digest(),
            block_hash_2=hashlib.sha256(b"block_b").digest(),
            sig_1=b'\x01' * 64,
            sig_2=b'\x02' * 64,
            height=100,
        )

        slashed, msg = registry.submit_slash_evidence(evidence)
        assert slashed is True

        device = registry.devices[registry.pubkey_to_device_id[pubkey]]
        assert device.status == DeviceStatus.BANNED
        assert pubkey in registry.banned_pubkeys

    def test_same_hash_not_slashable(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        h = hashlib.sha256(b"same").digest()
        evidence = SlashEvidence(
            pubkey=pubkey,
            block_hash_1=h,
            block_hash_2=h,  # Same hash
            sig_1=b'\x01' * 64,
            sig_2=b'\x02' * 64,
            height=100,
        )

        slashed, msg = registry.submit_slash_evidence(evidence)
        assert slashed is False
        assert "not slashable" in msg.lower()

    def test_unknown_device_not_slashed(self, registry):
        evidence = SlashEvidence(
            pubkey=make_pubkey(999),  # Not registered
            block_hash_1=b'\x01' * 32,
            block_hash_2=b'\x02' * 32,
            sig_1=b'\x01' * 64,
            sig_2=b'\x02' * 64,
            height=100,
        )

        slashed, msg = registry.submit_slash_evidence(evidence)
        assert slashed is False
        assert "not found" in msg.lower()


class TestPersistence:
    def test_save_and_reload(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\xAA\xBB\xCC\x01\x00\x01')

        registry.record_mined_block(pubkey, 5, 50 * 10**8)

        # Save
        registry.save()

        # Reload
        reg2 = DeviceRegistry(data_path=registry.data_path)
        assert reg2.get_device_count() == 1

        device = reg2.get_device(pubkey)
        assert device is not None
        assert device.total_blocks == 1
        assert device.chip_id == b'\xAA\xBB\xCC\x01\x00\x01'

    def test_banned_devices_persist(self, registry):
        pubkey = make_pubkey(1)
        registry.register_device(pubkey, b'\x00' * 6)

        evidence = SlashEvidence(
            pubkey=pubkey,
            block_hash_1=hashlib.sha256(b"a").digest(),
            block_hash_2=hashlib.sha256(b"b").digest(),
            sig_1=b'\x01' * 64,
            sig_2=b'\x02' * 64,
            height=10,
        )
        registry.submit_slash_evidence(evidence)
        registry.save()

        reg2 = DeviceRegistry(data_path=registry.data_path)
        assert len(reg2.banned_pubkeys) == 1
        assert pubkey in reg2.banned_pubkeys


class TestStats:
    def test_stats_after_activity(self, registry):
        # Register 3 devices
        for i in range(3):
            registry.register_device(make_pubkey(i), b'\x00' * 5 + bytes([i]))

        # Mine some blocks
        registry.record_mined_block(make_pubkey(0), 0, 50 * 10**8)
        registry.record_mined_block(make_pubkey(0), 1, 50 * 10**8)
        registry.record_mined_block(make_pubkey(1), 2, 50 * 10**8)

        stats = registry.get_stats()
        assert stats['total_devices'] == 3
        assert stats['active'] == 3
        assert stats['total_blocks_mined'] == 3
        assert stats['early_adopters'] == 3

    def test_device_list(self, registry):
        for i in range(5):
            registry.register_device(make_pubkey(i), b'\x00' * 5 + bytes([i]))

        registry.record_mined_block(make_pubkey(0), 0, 50 * 10**8)
        registry.record_mined_block(make_pubkey(0), 1, 50 * 10**8)

        devices = registry.list_devices()
        assert len(devices) == 5
        # Top miner should be device 0
        assert devices[0]['blocks'] == 2
