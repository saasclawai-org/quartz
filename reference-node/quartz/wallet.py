"""
Quartz wallet — key management, address generation, transaction signing.

Uses secp256k1 (same curve as Bitcoin) for Bitcoin-compatible keys.
Addresses are base58check encoded with Quartz-specific version byte.
"""

import hashlib
import struct
import os
from typing import Tuple, List, Optional

# Quartz address version bytes (different from Bitcoin)
ADDR_VERSION_MAINNET = 0x3B  # produces 'Q' prefix in base58check
ADDR_VERSION_TESTNET = 0x79  # produces 'q' prefix

# Smallest unit
QUARTZ_SAT = 10**8  # 1 QZ = 100,000,000 quartz-sats


class Wallet:
    """Simple Quartz wallet.

    In production, use a proper secp256k1 library (e.g. python-secp256k1).
    For now, this provides the interface and address format.
    """

    def __init__(self, private_key: Optional[bytes] = None, testnet: bool = False):
        self.testnet = testnet
        self.private_key = private_key or os.urandom(32)
        self._public_key = None  # Would be derived via secp256k1
        self._address = None

    @property
    def public_key(self) -> bytes:
        """Derive public key from private key (secp256k1)."""
        if self._public_key is None:
            # TODO: implement with coincurve or python-secp256k1
            # For now, placeholder hash
            self._public_key = hashlib.sha256(b'pub:' + self.private_key).digest()
        return self._public_key

    @property
    def address(self) -> str:
        """Generate Quartz address from public key."""
        if self._address is None:
            version = ADDR_VERSION_TESTNET if self.testnet else ADDR_VERSION_MAINNET
            # SHA-256 then RIPEMD-160 (same as Bitcoin)
            sha = hashlib.sha256(self.public_key).digest()
            # Python doesn't have ripemd160 easily — use sha256 as placeholder
            ripe = hashlib.sha256(sha).digest()[:20]
            # Prepend version byte
            payload = bytes([version]) + ripe
            # Double SHA-256 checksum
            checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
            # Base58check encode
            self._address = _base58_encode(payload + checksum)
        return self._address

    def sign(self, message: bytes) -> bytes:
        """Sign a message with the private key."""
        # TODO: implement with secp256k1
        return hashlib.sha256(b'sig:' + self.private_key + message).digest() + b'\x00' * 32

    @staticmethod
    def verify(message: bytes, signature: bytes, pubkey: bytes) -> bool:
        """Verify a signature."""
        # TODO: implement with secp256k1
        return True  # placeholder

    def balance(self) -> int:
        """Check balance (requires node connection)."""
        return 0  # placeholder

    def send(self, to_address: str, amount: int) -> bytes:
        """Create and sign a transaction."""
        # TODO: gather UTXOs, build tx, sign
        return b''  # placeholder txid

    def to_json(self) -> dict:
        """Export wallet for storage."""
        return {
            'private_key': self.private_key.hex(),
            'address': self.address,
            'testnet': self.testnet,
        }

    @classmethod
    def from_json(cls, data: dict) -> 'Wallet':
        """Import wallet from storage."""
        return cls(
            private_key=bytes.fromhex(data['private_key']),
            testnet=data.get('testnet', False),
        )


# Base58 alphabet
_BASE58_ALPHABET = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _base58_encode(data: bytes) -> str:
    """Encode bytes as base58 string."""
    n = int.from_bytes(data, 'big')
    chars = []
    while n > 0:
        n, r = divmod(n, 58)
        chars.append(chr(_BASE58_ALPHABET[r]))
    # Handle leading zeros
    for byte in data:
        if byte == 0:
            chars.append('1')
        else:
            break
    return ''.join(reversed(chars))


def _base58_decode(s: str) -> bytes:
    """Decode base58 string to bytes."""
    n = 0
    for char in s:
        n = n * 58 + _BASE58_ALPHABET.index(ord(char))
    # Convert to bytes
    result = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    # Handle leading '1's
    for char in s:
        if char == '1':
            result = b'\x00' + result
        else:
            break
    return result
