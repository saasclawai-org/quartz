"""
Quartz Device Registry & Attestation Verification

Maintains the registry of registered ESP32 devices and verifies
block attestation signatures. This is the network-side counterpart
to the firmware's quartz_attest.c.

Flow:
    1. Device sends registration → registry validates → adds device
    2. Miner finds block → signs with device key → includes attestation
    3. Verifying nodes check: PoW valid AND signature valid AND device registered
    4. If device double-signs → slashing evidence → device banned

Security model:
    - CrystalHash PoW is "easy" to compute on a PC (100x faster than ESP32)
    - But blocks require a co-signature from a registered ESP32's Ed25519 key
    - The private key lives in the ESP32's encrypted NVS, bound to eFuse
    - A PC can find nonces fast, but cannot sign blocks without a physical ESP32
    - One ESP32 signing for many PCs → rate-limited + slashable for equivocation
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Optional, List, Dict, Set, Tuple
from collections import defaultdict


class DeviceStatus(IntEnum):
    UNKNOWN = 0
    ACTIVE = 1
    FLAGGED = 2
    BANNED = 3


@dataclass
class MinerDevice:
    """A registered ESP32 mining device."""
    pubkey: bytes                  # 32-byte Ed25519 public key
    chip_id: bytes                 # 6-byte MAC address
    device_id: str                 # Hex of first 16 bytes of SHA-256(pubkey)
    registered_at: float           # Unix timestamp
    last_block_height: int = -1    # Last block mined
    last_block_time: float = 0     # When last block was mined
    total_blocks: int = 0          # Lifetime block count
    total_reward: int = 0          # Lifetime reward (sats)
    status: DeviceStatus = DeviceStatus.ACTIVE
    ban_reason: str = ""
    early_adopter: bool = False
    fastest_block_time: float = 0  # Fastest time between consecutive blocks

    # Anti-cheat tracking
    recent_block_times: List[float] = field(default_factory=list)  # Last 10 block timestamps
    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['pubkey'] = self.pubkey.hex()
        d['chip_id'] = self.chip_id.hex()
        d['status'] = int(self.status)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'MinerDevice':
        return cls(
            pubkey=bytes.fromhex(d['pubkey']),
            chip_id=bytes.fromhex(d.get('chip_id', '000000000000')),
            device_id=d['device_id'],
            registered_at=d['registered_at'],
            last_block_height=d.get('last_block_height', -1),
            last_block_time=d.get('last_block_time', 0),
            total_blocks=d.get('total_blocks', 0),
            total_reward=d.get('total_reward', 0),
            status=DeviceStatus(d.get('status', 1)),
            ban_reason=d.get('ban_reason', ''),
            early_adopter=d.get('early_adopter', False),
            fastest_block_time=d.get('fastest_block_time', 0),
            recent_block_times=d.get('recent_block_times', []),
            flags=d.get('flags', []),
        )


@dataclass
class SlashEvidence:
    """Evidence that a device signed conflicting blocks."""
    pubkey: bytes
    block_hash_1: bytes
    block_hash_2: bytes
    sig_1: bytes
    sig_2: bytes
    height: int
    submitted_at: float = field(default_factory=time.time)


class DeviceRegistry:
    """
    Maintains the registry of registered ESP32 devices.

    In production, this is a consensus-critical data structure — all
    verifying nodes must agree on which devices are registered and banned.
    Implemented as a Merkle tree for efficient inclusion proofs.

    For the reference implementation, uses a simple JSON file.
    """

    # Anti-cheat thresholds
    MIN_BLOCK_INTERVAL = 30        # Seconds (testnet: 30, mainnet: 120)
    ABNORMAL_THRESHOLD = 0.5       # If blocks come faster than 50% of target → flag
    ABNORMAL_COUNT = 3             # Flag after 3 consecutive abnormal blocks
    BAN_THRESHOLD = 0.25           # If blocks come faster than 25% of target → ban
    MAX_DEVICES_PER_IP = 5         # One IP can register max 5 devices

    def __init__(self, data_path: str = None):
        self.devices: Dict[str, MinerDevice] = {}  # device_id → MinerDevice
        self.pubkey_to_device_id: Dict[bytes, str] = {}
        self.banned_pubkeys: Set[bytes] = set()
        self.slash_evidence: List[SlashEvidence] = []
        self.ip_registrations: Dict[str, Set[str]] = defaultdict(set)
        self.early_adopter_count = 0
        self.max_early_adopters = 1000

        self.data_path = data_path
        if data_path and os.path.exists(data_path):
            self.load()

    def register_device(
        self,
        pubkey: bytes,
        chip_id: bytes,
        attestation: bytes = None,
        source_ip: str = None,
        firmware_hash: bytes = None
    ) -> Tuple[bool, str, Optional[MinerDevice]]:
        """
        Register a new ESP32 device.

        Returns (success, message, device).
        """
        if len(pubkey) != 32:
            return (False, "Invalid public key length", None)

        device_id = hashlib.sha256(pubkey).hexdigest()[:32]

        # Check if already registered
        if device_id in self.devices:
            return (False, "Device already registered", self.devices[device_id])

        # Check IP limit
        if source_ip and len(self.ip_registrations[source_ip]) >= self.MAX_DEVICES_PER_IP:
            return (False, f"IP {source_ip} has reached device limit ({self.MAX_DEVICES_PER_IP})", None)

        # Verify attestation (in production: check against manufacturer registry)
        if attestation is not None:
            if not self._verify_attestation(pubkey, attestation):
                return (False, "Invalid attestation proof", None)

        # Check early adopter status
        is_early = self.early_adopter_count < self.max_early_adopters

        # Create device record
        device = MinerDevice(
            pubkey=pubkey,
            chip_id=chip_id,
            device_id=device_id,
            registered_at=time.time(),
            early_adopter=is_early,
        )

        self.devices[device_id] = device
        self.pubkey_to_device_id[pubkey] = device_id

        if source_ip:
            self.ip_registrations[source_ip].add(device_id)

        if is_early:
            self.early_adopter_count += 1

        self.save()

        return (True, "Registered", device)

    def verify_block_attestation(
        self,
        header_hash: bytes,
        nonce: int,
        pubkey: bytes,
        signature: bytes,
        height: int
    ) -> Tuple[bool, str]:
        """
        Verify a block's attestation.

        Returns (is_valid, reason).
        """
        # Check device is registered
        device_id = self.pubkey_to_device_id.get(pubkey)
        if device_id is None:
            return (False, "Device not registered")

        device = self.devices.get(device_id)
        if device is None:
            return (False, "Device not in registry")

        # Check device is active
        if device.status == DeviceStatus.BANNED:
            return (False, f"Device banned: {device.ban_reason}")

        if device.status == DeviceStatus.FLAGGED:
            # Allow flagged devices to mine, but monitor closely
            pass

        # Verify Ed25519 signature
        msg = header_hash + nonce.to_bytes(8, 'little')
        if not self._verify_ed25519(pubkey, msg, signature):
            return (False, "Invalid Ed25519 signature")

        # Anti-cheat: check timing
        now = time.time()
        if device.last_block_time > 0:
            interval = now - device.last_block_time
            if interval < self.MIN_BLOCK_INTERVAL * self.BAN_THRESHOLD:
                # Ban: physically impossible on real ESP32
                device.status = DeviceStatus.BANNED
                device.ban_reason = f"Block interval {interval:.1f}s < {self.BAN_THRESHOLD * self.MIN_BLOCK_INTERVAL:.1f}s minimum"
                device.flags.append(f"auto-ban:{height}:{interval:.1f}s")
                self.banned_pubkeys.add(pubkey)
                self.save()
                return (False, f"Auto-banned: {device.ban_reason}")

            if interval < self.MIN_BLOCK_INTERVAL * self.ABNORMAL_THRESHOLD:
                device.flags.append(f"fast-block:{height}:{interval:.1f}s")
                consecutive = self._count_consecutive_fast(device)
                if consecutive >= self.ABNORMAL_COUNT:
                    device.status = DeviceStatus.FLAGGED

        # Check for double-mining at same height
        if device.last_block_height == height:
            device.status = DeviceStatus.BANNED
            device.ban_reason = f"Double-mining at height {height}"
            self.banned_pubkeys.add(pubkey)
            self.save()
            return (False, f"Banned: {device.ban_reason}")

        return (True, "Valid")

    def record_mined_block(
        self,
        pubkey: bytes,
        height: int,
        reward: int,
        block_time: float = None
    ):
        """Record that a device mined a block (for stats + anti-cheat)."""
        device_id = self.pubkey_to_device_id.get(pubkey)
        if device_id is None:
            return

        device = self.devices[device_id]

        # Track timing for anti-cheat
        now = time.time()
        if device.last_block_time > 0:
            interval = now - device.last_block_time
            device.recent_block_times.append(interval)
            if len(device.recent_block_times) > 10:
                device.recent_block_times.pop(0)
            if device.fastest_block_time == 0 or interval < device.fastest_block_time:
                device.fastest_block_time = interval

        device.last_block_height = height
        device.last_block_time = now
        device.total_blocks += 1
        device.total_reward += reward

        self.save()

    def submit_slash_evidence(self, evidence: SlashEvidence) -> Tuple[bool, str]:
        """
        Submit slashing evidence for a double-signing device.

        Returns (slashed, reason).
        """
        device_id = self.pubkey_to_device_id.get(evidence.pubkey)
        if device_id is None:
            return (False, "Device not found")

        device = self.devices[device_id]

        # Verify evidence
        if evidence.height < 0:
            return (False, "Invalid height")

        if evidence.block_hash_1 == evidence.block_hash_2:
            return (False, "Same block hash — not slashable")

        # In production: verify both signatures are valid Ed25519 over
        # their respective block hashes with the same pubkey

        # Slash
        device.status = DeviceStatus.BANNED
        device.ban_reason = f"Slashed: double-sign at height {evidence.height}"
        self.banned_pubkeys.add(evidence.pubkey)
        self.slash_evidence.append(evidence)
        self.save()

        return (True, f"Device {device_id[:16]} slashed")

    def get_device(self, pubkey: bytes) -> Optional[MinerDevice]:
        """Get device by public key."""
        device_id = self.pubkey_to_device_id.get(pubkey)
        if device_id is None:
            return None
        return self.devices.get(device_id)

    def get_device_count(self) -> int:
        return len(self.devices)

    def get_active_count(self) -> int:
        return sum(1 for d in self.devices.values() if d.status == DeviceStatus.ACTIVE)

    def get_banned_count(self) -> int:
        return sum(1 for d in self.devices.values() if d.status == DeviceStatus.BANNED)

    def get_stats(self) -> dict:
        """Registry statistics for API."""
        total_blocks = sum(d.total_blocks for d in self.devices.values())
        return {
            "total_devices": len(self.devices),
            "active": self.get_active_count(),
            "flagged": sum(1 for d in self.devices.values() if d.status == DeviceStatus.FLAGGED),
            "banned": self.get_banned_count(),
            "early_adopters": self.early_adopter_count,
            "early_adopter_slots": self.max_early_adopters - self.early_adopter_count,
            "total_blocks_mined": total_blocks,
            "slash_events": len(self.slash_evidence),
        }

    def list_devices(self, limit=50) -> List[dict]:
        """List devices for explorer."""
        devices = sorted(self.devices.values(), key=lambda d: d.total_blocks, reverse=True)
        return [
            {
                "device_id": d.device_id[:16] + "...",
                "pubkey": d.pubkey.hex()[:16] + "...",
                "chip_id": d.chip_id.hex(),
                "status": DeviceStatus(d.status).name,
                "blocks": d.total_blocks,
                "reward_qz": d.total_reward / 1e8,
                "early_adopter": d.early_adopter,
                "fastest_block": f"{d.fastest_block_time:.1f}s" if d.fastest_block_time else "—",
                "registered": time.strftime('%Y-%m-%d', time.gmtime(d.registered_at)),
                "flags": len(d.flags),
            }
            for d in devices[:limit]
        ]

    # ============ Internal ============

    def _verify_attestation(self, pubkey: bytes, attestation: bytes) -> bool:
        """
        Verify HMAC-SHA256 attestation proof.

        In production, this checks against a manufacturer root of trust:
        1. Look up chip's eFuse key hash from manufacturer DB
        2. Recompute HMAC(key, pubkey)
        3. Compare with attestation

        For the reference implementation, we accept all attestations.
        """
        if len(attestation) != 32:
            return False
        return True  # Production: actual HMAC verification

    def _verify_ed25519(self, pubkey: bytes, msg: bytes, sig: bytes) -> bool:
        """
        Verify an Ed25519 signature.

        Production: Use PyNaCl/libsodium.
        Reference: Accept all well-formed signatures (testing only).
        """
        if len(sig) != 64:
            return False
        if len(pubkey) != 32:
            return False

        # In production with PyNaCl:
        # from nacl.signing import VerifyKey
        # vk = VerifyKey(pubkey)
        # try:
        #     vk.verify(msg, sig)
        #     return True
        # except Exception:
        #     return False

        return True  # Reference: accept all

    def _count_consecutive_fast(self, device: MinerDevice) -> int:
        """Count consecutive blocks that came suspiciously fast."""
        count = 0
        for t in reversed(device.recent_block_times):
            if t < self.MIN_BLOCK_INTERVAL * self.ABNORMAL_THRESHOLD:
                count += 1
            else:
                break
        return count

    # ============ Persistence ============

    def save(self):
        if not self.data_path:
            return

        data = {
            'devices': {did: d.to_dict() for did, d in self.devices.items()},
            'banned_pubkeys': [p.hex() for p in self.banned_pubkeys],
            'early_adopter_count': self.early_adopter_count,
            'slash_count': len(self.slash_evidence),
        }
        tmp = self.data_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, self.data_path)

    def load(self):
        with open(self.data_path) as f:
            data = json.load(f)

        for did, dd in data.get('devices', {}).items():
            device = MinerDevice.from_dict(dd)
            self.devices[did] = device
            self.pubkey_to_device_id[device.pubkey] = did

        self.banned_pubkeys = set(
            bytes.fromhex(p) for p in data.get('banned_pubkeys', [])
        )
        self.early_adopter_count = data.get('early_adopter_count', 0)
