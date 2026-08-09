"""
Quartz cryptography — Ed25519 key generation, BIP39 mnemonics, and address derivation.

Uses:
- BIP39 12-word mnemonic with checksum (2048-word wordlist)
- PBKDF2-HMAC-SHA512 for seed derivation (BIP39 standard)
- Ed25519 for signing (via PyNaCl / libsodium)
- SHA-256 + Base58 for addresses

Key hierarchy: m/44'/789'/0'/0'/n
  - 44 = BIP44 multi-account
  - 789 = Quartz coin number (arbitrary, below Bitcoin 0 and above common altcoins)
  - External chain (0) / index (n)
"""

import hashlib
import hmac
import os
import struct

# ============================================================
# BIP39 Wordlist — official English (2048 words)
# ============================================================
import os as _os
_WORDLIST_PATH = _os.path.join(_os.path.dirname(__file__), 'bip39_english.txt')
with open(_WORDLIST_PATH, 'r') as _f:
    BIP39_WORDLIST = [line.strip() for line in _f if line.strip()]
assert len(BIP39_WORDLIST) == 2048, f"BIP39 wordlist must have 2048 words, got {len(BIP39_WORDLIST)}"


# ============================================================
# BIP39 Mnemonic Generation
# ============================================================

def generate_mnemonic(strength: int = 128) -> list[str]:
    """
    Generate a BIP39-compatible 12-word mnemonic.
    strength=128 → 12 words, strength=256 → 24 words.
    """
    assert strength in (128, 256), "Strength must be 128 or 256"
    entropy = os.urandom(strength // 8)
    return entropy_to_mnemonic(entropy)


def entropy_to_mnemonic(entropy: bytes) -> list[str]:
    """Convert entropy bytes to BIP39 mnemonic word list."""
    entropy_bits = len(entropy) * 8
    checksum_bits = entropy_bits // 32
    total_bits = entropy_bits + checksum_bits

    # SHA-256 checksum
    checksum = hashlib.sha256(entropy).digest()
    
    # Combine entropy + checksum bits
    entropy_int = int.from_bytes(entropy, 'big')
    checksum_int = checksum[0] >> (8 - checksum_bits)
    combined = (entropy_int << checksum_bits) | checksum_int

    # Convert to words (11 bits each)
    word_count = total_bits // 11
    words = []
    for i in range(word_count):
        shift = 11 * (word_count - 1 - i)
        index = (combined >> shift) & 0x7FF
        words.append(BIP39_WORDLIST[index])
    
    return words


def mnemonic_to_entropy(words: list[str]) -> bytes:
    """Convert mnemonic words back to entropy. Validates checksum."""
    word_count = len(words)
    assert word_count in (12, 24), "Mnemonic must be 12 or 24 words"
    
    # Convert words to indices
    indices = []
    word_set = set(BIP39_WORDLIST)
    for w in words:
        if w not in word_set:
            raise ValueError(f"Invalid word: {w}")
        indices.append(BIP39_WORDLIST.index(w))
    
    # Convert indices to bits
    combined = 0
    for idx in indices:
        combined = (combined << 11) | idx
    
    total_bits = word_count * 11
    checksum_bits = total_bits // 33
    entropy_bits = total_bits - checksum_bits
    entropy_len = entropy_bits // 8
    
    # Extract entropy and checksum
    checksum = combined & ((1 << checksum_bits) - 1)
    entropy_int = combined >> checksum_bits
    entropy = entropy_int.to_bytes(entropy_len, 'big')
    
    # Verify checksum
    expected = hashlib.sha256(entropy).digest()[0] >> (8 - checksum_bits)
    if checksum != expected:
        raise ValueError("Invalid mnemonic checksum — words may be entered incorrectly")
    
    return entropy


# ============================================================
# Seed Derivation (BIP39 PBKDF2)
# ============================================================

def mnemonic_to_seed(words: list[str], passphrase: str = "") -> bytes:
    """
    Convert mnemonic to 64-byte seed using PBKDF2-HMAC-SHA512.
    This seed is used for HD key derivation.
    """
    mnemonic_str = " ".join(words)
    salt = "mnemonic" + passphrase
    return hashlib.pbkdf2_hmac('sha512', mnemonic_str.encode('utf-8'), salt.encode('utf-8'), 2048, dklen=64)


# ============================================================
# Ed25519 Key Derivation
# ============================================================

def seed_to_master_key(seed: bytes) -> tuple[bytes, bytes]:
    """
    Derive master key pair from BIP39 seed using HMAC-SHA512.
    Returns (private_key, chain_code).
    """
    I = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    return I[:32], I[32:]


def derive_child_key(parent_key: bytes, parent_chain: bytes, index: int) -> tuple[bytes, bytes]:
    """
    Derive a hardened child key for Ed25519 (SLIP-0010).
    Ed25519 only supports hardened derivation (index >= 0x80000000).
    """
    assert index >= 0x80000000, "Ed25519 requires hardened derivation"
    # Data: 0x00 || parent_key || index (4 bytes BE)
    data = b'\x00' + parent_key + struct.pack('>I', index)
    I = hmac.new(parent_chain, data, hashlib.sha512).digest()
    return I[:32], I[32:]


def derive_quartz_keypair(words: list[str], account: int = 0, index: int = 0):
    """
    Derive Ed25519 keypair from mnemonic using Quartz derivation path.
    Path: m/44'/789'/account'/0'/index'
    
    Returns (private_key, public_key) as bytes.
    Requires PyNaCl for Ed25519.
    """
    # Seed → master
    seed = mnemonic_to_seed(words)
    master_key, master_chain = seed_to_master_key(seed)
    
    # m/44'
    k, c = derive_child_key(master_key, master_chain, 44 | 0x80000000)
    # m/44'/789'
    k, c = derive_child_key(k, c, 789 | 0x80000000)
    # m/44'/789'/account'
    k, c = derive_child_key(k, c, (account | 0x80000000))
    # m/44'/789'/account'/0'
    k, c = derive_child_key(k, c, 0x80000000)
    # m/44'/789'/account'/0'/index'
    k, c = derive_child_key(k, c, index | 0x80000000)
    
    # The derived 32-byte key IS the Ed25519 private seed
    try:
        from nacl.signing import SigningKey
        sk = SigningKey(k)
        pk = sk.verify_key.encode()
        return k, pk  # raw private seed bytes, public key bytes
    except ImportError:
        # Fallback: derive public key manually (for environments without PyNaCl)
        pk = _ed25519_publickey(k)
        return k, pk


def _ed25519_publickey(private_seed: bytes) -> bytes:
    """
    Pure-Python Ed25519 public key derivation (fallback when PyNaCl unavailable).
    Implements the scalar multiplication needed for public key.
    """
    # This is a placeholder — in production, always use libsodium/PyNaCl
    # Ed25519 public key = scalar * basepoint
    # For safety, require PyNaCl in the reference implementation
    raise NotImplementedError(
        "PyNaCl is required for Ed25519. Install with: pip install pynacl"
    )


# ============================================================
# Address Derivation
# ============================================================

# Base58 alphabet (Bitcoin-style, no 0/O/I/l)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58_encode(data: bytes) -> str:
    """Encode bytes to Base58 string."""
    num = int.from_bytes(data, 'big')
    result = ""
    while num > 0:
        num, rem = divmod(num, 58)
        result = BASE58_ALPHABET[rem] + result
    # Leading zeros → '1'
    for byte in data:
        if byte == 0:
            result = '1' + result
        else:
            break
    return result


def base58_decode(s: str) -> bytes:
    """Decode Base58 string to bytes."""
    num = 0
    for char in s:
        if char not in BASE58_ALPHABET:
            raise ValueError(f"Invalid Base58 character: {char}")
        num = num * 58 + BASE58_ALPHABET.index(char)
    # Convert to bytes
    byte_len = (num.bit_length() + 7) // 8
    result = num.to_bytes(byte_len, 'big')
    # Handle leading '1's → leading zero bytes
    pad = 0
    for char in s:
        if char == '1':
            pad += 1
        else:
            break
    return b'\x00' * pad + result


def public_key_to_address(public_key: bytes, mainnet: bool = True) -> str:
    """
    Convert Ed25519 public key to Quartz address.
    Format: prefix || SHA-256(pubkey)[:20] || checksum
    - Mainnet prefix: 0x3B (displays as 'Q')
    - Testnet prefix: 0x7F (displays as 'T')
    - Checksum: first 4 bytes of SHA-256(prefix || hash)
    - Encoding: Base58
    """
    prefix = bytes([0x3B]) if mainnet else bytes([0x7F])
    pubkey_hash = hashlib.sha256(public_key).digest()[:20]
    
    # Checksum
    payload = prefix + pubkey_hash
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    
    address_bytes = payload + checksum
    return base58_encode(address_bytes)


def validate_address(address: str) -> bool:
    """Validate a Quartz address format and checksum."""
    try:
        decoded = base58_decode(address)
        if len(decoded) != 25:
            return False
        payload = decoded[:21]
        checksum = decoded[21:]
        expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return checksum == expected
    except (ValueError, Exception):
        return False


def address_is_mainnet(address: str) -> bool:
    """Check if address is mainnet (Q-prefix) or testnet (T-prefix)."""
    try:
        decoded = base58_decode(address)
        return decoded[0] == 0x3B
    except Exception:
        return False


# ============================================================
# Transaction Signing
# ============================================================

def sign_message(private_key: bytes, message: bytes) -> bytes:
    """
    Sign a message with Ed25519 private key.
    Returns 64-byte signature.
    """
    from nacl.signing import SigningKey
    sk = SigningKey(private_key)
    return sk.sign(message).signature


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """
    Verify an Ed25519 signature.
    """
    from nacl.signing import VerifyKey
    try:
        vk = VerifyKey(public_key)
        vk.verify(message, signature)
        return True
    except Exception:
        return False


# ============================================================
# Wallet Helpers
# ============================================================

QUARTZ_DERIVATION_PATH = "m/44'/789'/0'/0'/n"


def create_new_wallet(passphrase: str = "") -> dict:
    """
    Create a complete new Quartz wallet.
    Returns dict with mnemonic, seed, private_key, public_key, address.
    """
    words = generate_mnemonic(128)
    seed = mnemonic_to_seed(words, passphrase)
    priv, pub = derive_quartz_keypair(words)
    address = public_key_to_address(pub)
    
    return {
        'mnemonic': words,
        'seed': seed.hex(),
        'private_key': priv.hex(),
        'public_key': pub.hex(),
        'address': address,
        'derivation_path': QUARTZ_DERIVATION_PATH,
   }


def import_wallet_from_mnemonic(words: list[str], passphrase: str = "") -> dict:
    """
    Import wallet from existing mnemonic phrase.
    """
    # Validate mnemonic
    mnemonic_to_entropy(words)  # raises if invalid
    
    seed = mnemonic_to_seed(words, passphrase)
    priv, pub = derive_quartz_keypair(words)
    address = public_key_to_address(pub)
    
    return {
        'mnemonic': words,
        'seed': seed.hex(),
        'private_key': priv.hex(),
        'public_key': pub.hex(),
        'address': address,
        'derivation_path': QUARTZ_DERIVATION_PATH,
    }
