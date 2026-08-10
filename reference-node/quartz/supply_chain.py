"""
Quartz Supply Chain Security — Python Reference

Birth certificate creation, verification, and tampering detection.
Protects buyers from resellers who pre-flash devices with known keys.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set
from enum import IntEnum


class CertStatus(IntEnum):
    OK = 0
    ALREADY_REGISTERED = 1
    FIRMWARE_MISMATCH = 2
    BAD_SIGNATURE = 3
    EFUSE_TAMPERED = 4
    EXPIRED = 5
    UNAUTHORIZED_RESELLER = 6


@dataclass
class BirthCertificate:
    """Proves a device's eFuse key was generated on-chip at first boot."""
    version: int = 1
    chip_id: bytes = b'\x00' * 6
    key_commit_hash: bytes = b'\x00' * 32   # SHA-256(efuse_key || chip_id || "QUARTZ_GENESIS")
    device_pubkey: bytes = b'\x00' * 32     # Ed25519 public key for block signing
    first_boot_timestamp: int = 0           # When eFuse was first burned
    firmware_hash: bytes = b'\x00' * 32     # SHA-256 of firmware binary at birth
    birth_signature: bytes = b'\x00' * 64   # Ed25519 signature over all above

    def serialize(self) -> bytes:
        """Serialize for signing/transmission."""
        return (
            self.version.to_bytes(1, 'little') +
            self.chip_id +
            self.key_commit_hash +
            self.device_pubkey +
            self.first_boot_timestamp.to_bytes(8, 'little') +
            self.firmware_hash +
            b'\x00' * 3  # reserved
        )

    def to_hex(self) -> str:
        """Full certificate as hex string (for QR code)."""
        data = self.serialize() + self.birth_signature
        return data.hex()

    @classmethod
    def from_hex(cls, hex_str: str) -> 'BirthCertificate':
        """Deserialize from hex string."""
        data = bytes.fromhex(hex_str)
        if len(data) != 178:
            raise ValueError(f"Expected 178 bytes, got {len(data)}")

        cert = cls()
        cert.version = data[0]
        cert.chip_id = data[1:7]
        cert.key_commit_hash = data[7:39]
        cert.device_pubkey = data[39:71]
        cert.first_boot_timestamp = int.from_bytes(data[71:79], 'little')
        cert.firmware_hash = data[79:111]
        # reserved 3 bytes at 111:114
        cert.birth_signature = data[114:178]
        return cert

    def short_hash(self) -> str:
        """Short display hash (first 4 bytes of key_commit_hash)."""
        return self.key_commit_hash[:4].hex()


class DeviceRegistry:
    """
    Tracks registered devices on the Quartz network.

    Used by:
    - Phone apps to verify a device at purchase time
    - Network nodes to detect clones/duplicates
    - Reseller certification program
    """

    def __init__(self):
        self.registered_chip_ids: Set[bytes] = set()
        self.certificates: dict[bytes, BirthCertificate] = {}  # chip_id → cert
        self.reseller_batches: dict[str, dict] = {}  # batch_id → metadata
        self.official_firmware_hash: Optional[bytes] = None
        self.firmware_release_date: int = 0

    def set_official_firmware(self, fw_hash: bytes, release_date: int):
        """Set the current official firmware hash and release date."""
        self.official_firmware_hash = fw_hash
        self.firmware_release_date = release_date

    def register_reseller_batch(
        self,
        batch_id: str,
        reseller_pubkey: bytes,
        device_count: int,
        batch_signature: bytes
    ):
        """Register an authorized reseller batch (signed by multi-sig)."""
        self.reseller_batches[batch_id] = {
            'reseller_pubkey': reseller_pubkey,
            'device_count': device_count,
            'signature': batch_signature,
            'registered_at': int(time.time()),
        }

    def verify_certificate(
        self,
        cert: BirthCertificate,
        check_network: bool = True,
        check_firmware: bool = True,
        check_date: bool = True
    ) -> CertStatus:
        """
        Verify a birth certificate at purchase time.

        Returns CertStatus.OK if the device is genuine and fresh.
        """
        # Version check
        if cert.version != 1:
            return CertStatus.BAD_SIGNATURE

        # Firmware hash check
        if check_firmware and self.official_firmware_hash:
            if cert.firmware_hash != self.official_firmware_hash:
                return CertStatus.FIRMWARE_MISMATCH

        # Birth date check (device can't be born before firmware was released)
        if check_date and self.firmware_release_date:
            if cert.first_boot_timestamp < self.firmware_release_date:
                return CertStatus.EXPIRED

        # Already registered? (clone detection)
        if check_network and cert.chip_id in self.registered_chip_ids:
            existing = self.certificates.get(cert.chip_id)
            if existing and existing.key_commit_hash != cert.key_commit_hash:
                # Same chip ID, different key = definite fraud
                return CertStatus.ALREADY_REGISTERED
            # Same chip_id with same key = already on network
            return CertStatus.ALREADY_REGISTERED

        # Signature verification (in production: Ed25519 verify)
        # ed25519_verify(cert.birth_signature, cert.serialize(), cert.device_pubkey)

        return CertStatus.OK

    def register_device(self, cert: BirthCertificate) -> bool:
        """Register a device on the network after verification."""
        if cert.chip_id in self.registered_chip_ids:
            return False  # Already registered

        self.registered_chip_ids.add(cert.chip_id)
        self.certificates[cert.chip_id] = cert
        return True

    def is_authorized_reseller(self, batch_id: str) -> bool:
        """Check if a batch ID is from an authorized reseller."""
        return batch_id in self.reseller_batches

    def get_device_count(self) -> int:
        """Total registered devices."""
        return len(self.registered_chip_ids)


def compute_key_commit_hash(efuse_key: bytes, chip_id: bytes) -> bytes:
    """
    Compute the one-way key commitment hash.

    SHA-256(efuse_key || chip_id || "QUARTZ_GENESIS")

    This proves the eFuse key was burned without revealing the key itself.
    """
    return hashlib.sha256(
        efuse_key + chip_id + b"QUARTZ_GENESIS"
    ).digest()


def simulate_first_boot(chip_id: bytes, firmware_hash: bytes) -> tuple:
    """
    Simulate an ESP32 first-boot provisioning sequence.

    Returns (certificate, efuse_key) where efuse_key should be
    immediately discarded by the caller (simulating read-protect).

    In real hardware, the key is burned into eFuse and then
    becomes physically unreadable.
    """
    import os

    # Step 1: Generate 32 random bytes from hardware RNG
    efuse_key = os.urandom(32)

    # Step 2: Compute key commitment
    commit_hash = compute_key_commit_hash(efuse_key, chip_id)

    # Step 3: Generate Ed25519 keypair (simulated)
    device_pubkey = hashlib.sha256(
        chip_id + commit_hash + b"QUARTZ_KEYGEN"
    ).digest()

    # Step 4: Create certificate
    cert = BirthCertificate(
        version=1,
        chip_id=chip_id,
        key_commit_hash=commit_hash,
        device_pubkey=device_pubkey,
        first_boot_timestamp=int(time.time()),
        firmware_hash=firmware_hash,
        birth_signature=b'\x00' * 64,  # Would be Ed25519 signature
    )

    return cert, efuse_key


def simulate_reseller_attack(
    chip_id: bytes,
    official_firmware_hash: bytes,
    attack_firmware_hash: bytes
) -> tuple:
    """
    Simulate a reseller attack scenario.

    Reseller:
    1. Buys device
    2. Flashes custom firmware with their code
    3. Boots → eFuse key generated → reseller captures it
    4. Reflashes with official firmware
    5. Sells to buyer

    Returns (reseller_cert, buyer_cert, reseller_has_key)

    The buyer's certificate will show firmware mismatch or tampering.
    """
    # Reseller's first boot with malicious firmware
    reseller_cert, efuse_key = simulate_first_boot(chip_id, attack_firmware_hash)

    # Reseller now has the key
    reseller_has_key = True

    # Reseller reflashes with official firmware
    # But eFuse is ALREADY burned — can't re-provision
    # Certificate in NVS was created with attack firmware hash
    # After reflash, NVS may be wiped → no certificate → TAMPERING DETECTED

    # Scenario A: NVS wiped on reflash
    buyer_cert = None  # No cert → quartz_detect_tampering() returns True

    # Scenario B: NVS preserved but firmware hash doesn't match
    buyer_cert_mismatch = BirthCertificate(
        version=1,
        chip_id=chip_id,
        key_commit_hash=reseller_cert.key_commit_hash,
        device_pubkey=reseller_cert.device_pubkey,
        first_boot_timestamp=reseller_cert.first_boot_timestamp,
        firmware_hash=attack_firmware_hash,  # Still shows attack firmware!
        birth_signature=b'\x00' * 64,
    )

    return reseller_cert, buyer_cert_mismatch, reseller_has_key
