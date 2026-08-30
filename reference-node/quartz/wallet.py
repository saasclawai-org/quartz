"""
Quartz wallet — key management, address generation, transaction signing.

Uses secp256k1 (same curve as Bitcoin) for Bitcoin-compatible keys.
Addresses are base58check encoded with Quartz-specific version byte.
"""

import hashlib
import json
import struct
import os
from typing import Tuple, List, Optional

from .crypto import (
    public_key_to_address,
    validate_address,
    address_is_mainnet,
)
from .quantum_crypto import (
    create_quantum_signature,
    generate_quantum_address,
    sha256 as qz_sha256,
)

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


# ---------------------------------------------------------------------------
# Payment privacy — address streams and payment channels
#
# Plain English:
#   1) StreamWallet never reuses an address. Every address grows from one
#      secret seed (one phrase recovers everything), but on the public
#      chain the addresses look completely unrelated.
#   2) PaymentChannel is a private lane between two parties. The receiver
#      hands the payer a BUNDLE of future addresses — addresses only, no
#      keys — so the payer can pay but can never steal.
#   3) The same bundle doubles as a watch-only "view key" for an accountant
#      or insurer: they see every payment, and can sign nothing.
# ---------------------------------------------------------------------------


def _le32(n: int) -> bytes:
    """Pack an int as 4 little-endian bytes (same packing as OTS leaf seeds)."""
    return struct.pack('<I', n)


class StreamWallet:
    """A wallet that never reuses an address.

    Every address comes from one secret seed (32 bytes) — the seed alone
    recovers everything. Each address is a WOTS+ Merkle tree carrying 256
    one-time signatures; when one runs low, move to the next. On the public
    chain the addresses share nothing visible — no prefix, no pattern,
    nothing to link them back to you.
    """

    def __init__(self, seed: Optional[bytes] = None, mainnet: bool = True):
        self.seed = seed or os.urandom(32)
        self.mainnet = mainnet
        self._trees = {}            # index -> (merkle_root, leaves)
        self._cursor = 0            # next stream position to hand out
        self._channel_seq = 0       # auto channel counter

    # -- address stream ----------------------------------------------------

    def account_seed(self, index: int) -> bytes:
        """Seed for stream position #index. Different seed → unrelated address."""
        return qz_sha256(b'qz/account' + self.seed + _le32(index))

    def root_at(self, index: int) -> bytes:
        """Raw 32-byte Merkle root (the on-chain identity) at a position."""
        if index not in self._trees:
            self._trees[index] = generate_quantum_address(self.account_seed(index))
        return self._trees[index][0]

    def address_at(self, index: int) -> str:
        """The address at a stream position — deterministic forever."""
        return public_key_to_address(self.root_at(index), self.mainnet)

    def next_address(self) -> str:
        """Hand out a fresh address. Never returns the same one twice."""
        addr = self.address_at(self._cursor)
        self._cursor += 1
        return addr

    @property
    def issued(self) -> List[str]:
        """Addresses handed out so far (oldest first)."""
        return [self.address_at(i) for i in range(self._cursor)]

    # -- spending -----------------------------------------------------------

    def sign_at(self, index: int, msg_hash: bytes, ots_idx: int = 0) -> bytes:
        """Sign with address #index using one-time signature slot ots_idx.

        Each slot may be used ONCE per address (the WOTS+ rule) — the node
        rejects a reused slot, so callers must track their slots.
        """
        if index not in self._trees:
            self.root_at(index)
        root, leaves = self._trees[index]
        return create_quantum_signature(
            self.account_seed(index), ots_idx, msg_hash, leaves)

    # -- payment channels ---------------------------------------------------

    def create_channel(self, channel_id: Optional[int] = None) -> "PaymentChannel":
        """Open a private payment lane (see PaymentChannel)."""
        if channel_id is None:
            channel_id = self._channel_seq
            self._channel_seq += 1
        seed = qz_sha256(b'qz/channel' + self.seed + _le32(channel_id))
        return PaymentChannel(
            channel_seed=seed, channel_id=channel_id, mainnet=self.mainnet)

    # -- persistence ---------------------------------------------------------

    def to_json(self) -> dict:
        return {
            'seed': self.seed.hex(),
            'mainnet': self.mainnet,
            'next_index': self._cursor,
            'channel_seq': self._channel_seq,
        }

    @classmethod
    def from_json(cls, data: dict) -> "StreamWallet":
        w = cls(seed=bytes.fromhex(data['seed']),
                mainnet=data.get('mainnet', True))
        w._cursor = int(data.get('next_index', 0))
        w._channel_seq = int(data.get('channel_seq', 0))
        return w


class PaymentChannel:
    """A private payment lane between you and one other party.

    Two flavors, one rule of thumb:

    * Receiving from SOMEONE ELSE: create the channel from YOUR wallet
      (StreamWallet.create_channel) and hand them export_bundle(n) — a
      list of future addresses and nothing else. They can pay; they can
      never steal, because the bundle carries no keys.

    * YOUR OWN devices / data commitments: PaymentChannel.from_shared_secret()
      gives both ends the same address stream. Anyone holding the secret
      can SPEND from the stream — fine when both ends are yours, never
      fine for receiving from strangers.
    """

    def __init__(self, channel_seed: bytes, channel_id: int = 0,
                 mainnet: bool = True):
        self.channel_seed = channel_seed
        self.channel_id = channel_id
        self.mainnet = mainnet
        self._trees = {}

    @classmethod
    def from_shared_secret(cls, secret: bytes, channel_id: int = 0,
                           mainnet: bool = True) -> "PaymentChannel":
        """Both parties derive the same stream from one shared secret.

        WARNING: anyone holding the secret can spend from this stream.
        Use only when both ends are yours, or for pure data commitments.
        """
        return cls(channel_seed=qz_sha256(b'qz/shared' + secret),
                   channel_id=channel_id, mainnet=mainnet)

    def stream_seed(self, i: int) -> bytes:
        return qz_sha256(b'qz/stream' + self.channel_seed + _le32(i))

    def root_at(self, i: int) -> bytes:
        if i not in self._trees:
            self._trees[i] = generate_quantum_address(self.stream_seed(i))
        return self._trees[i][0]

    def address_at(self, i: int) -> str:
        return public_key_to_address(self.root_at(i), self.mainnet)

    def addresses(self, n: int) -> List[str]:
        """First n addresses of the lane, in order."""
        return [self.address_at(i) for i in range(n)]

    def sign_at(self, i: int, msg_hash: bytes, ots_idx: int = 0) -> bytes:
        """Sign with stream address #i (requires the channel seed)."""
        if i not in self._trees:
            self.root_at(i)
        root, leaves = self._trees[i]
        return create_quantum_signature(
            self.stream_seed(i), ots_idx, msg_hash, leaves)

    def export_bundle(self, n: int) -> dict:
        """Hand this to your payer — or your accountant. Addresses only."""
        return {
            'v': 1,
            'type': 'quartz/payment-bundle',
            'channel': self.channel_id,
            'net': 'main' if self.mainnet else 'test',
            'count': n,
            'addresses': self.addresses(n),
        }


class ChannelBundle:
    """The payer's (or auditor's) side of a payment channel.

    Contains addresses ONLY — no key material — so a bundle can be pasted
    into a chat, emailed, or printed as a QR code. Whoever holds it can
    watch the payments and pay into the list, but can never spend from it.
    """

    def __init__(self, channel_id: int, mainnet: bool,
                 addresses: List[str]):
        for a in addresses:
            if not validate_address(a) or address_is_mainnet(a) != mainnet:
                raise ValueError(f'invalid address in bundle: {a!r}')
        self.channel_id = channel_id
        self.mainnet = mainnet
        self.addresses = list(addresses)
        self._cursor = 0

    def next_address(self) -> str:
        """Next address to pay, in order. Raises when the list runs out."""
        if self._cursor >= len(self.addresses):
            raise IndexError('bundle exhausted — ask the receiver for a fresh one')
        addr = self.addresses[self._cursor]
        self._cursor += 1
        return addr

    def remaining(self) -> int:
        return len(self.addresses) - self._cursor


def load_bundle(data) -> ChannelBundle:
    """Load a payment bundle from a dict or JSON string (payer side)."""
    if isinstance(data, (str, bytes)):
        data = json.loads(data)
    if not isinstance(data, dict) or data.get('type') != 'quartz/payment-bundle':
        raise ValueError('not a quartz payment bundle')
    return ChannelBundle(
        channel_id=int(data['channel']),
        mainnet=data.get('net') == 'main',
        addresses=data['addresses'],
    )
