"""
quantum_crypto.py — WOTS+ quantum-resistant signatures for Quartz node

Mirrors the ESP32 firmware implementation (quartz_wots.c).
Allows the reference node to verify quantum signatures from devices.

Parameter set: Winternitz w=4, SHA-256, 67 chains, Merkle height 8.
"""

import hashlib
import struct

HASH_SIZE = 32
WINTERNITZ = 4
CHAIN_LEN = (1 << WINTERNITZ) - 1  # 15
MSG_CHAINS = 64
CKSUM_CHAINS = 3
TOTAL_CHAINS = 67
WOTS_SIG_SIZE = TOTAL_CHAINS * HASH_SIZE   # 2144
WOTS_PUBKEY_SIZE = TOTAL_CHAINS * HASH_SIZE # 2144
MERKLE_HEIGHT = 8
MERKLE_LEAVES = 1 << MERKLE_HEIGHT          # 256
MERKLE_AUTH_SIZE = MERKLE_HEIGHT * HASH_SIZE # 256
QSIG_SIZE = WOTS_SIG_SIZE + MERKLE_AUTH_SIZE + 4  # ~2404


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def prf_chain(seed: bytes, chain_idx: int) -> bytes:
    """Derive a WOTS+ private key chain from seed + chain index."""
    buf = seed + struct.pack('<I', chain_idx)
    return sha256(buf)


def hash_chain(value: bytes, iterations: int) -> bytes:
    """Hash a value n times: H^n(input)."""
    for _ in range(iterations):
        value = sha256(value)
    return value


def msg_to_chain_values(msg_hash: bytes) -> list:
    """Convert 32-byte hash into 67 values of w=4 bits each (0-15)."""
    values = []
    for b in msg_hash:
        values.append((b >> 4) & 0x0F)  # high nibble
        values.append(b & 0x0F)          # low nibble

    # Checksum: sum of (chain_len - value) for message chains
    checksum = sum(CHAIN_LEN - v for v in values[:MSG_CHAINS])
    values.append((checksum >> 8) & 0x0F)
    values.append((checksum >> 4) & 0x0F)
    values.append(checksum & 0x0F)

    return values


def wots_gen_privkey(seed: bytes) -> bytes:
    """Generate 67 private key chains from seed."""
    return b''.join(prf_chain(seed, i) for i in range(TOTAL_CHAINS))


def wots_gen_pubkey(privkey: bytes) -> bytes:
    """Generate public key by hashing each chain to full length."""
    pub = bytearray()
    for i in range(TOTAL_CHAINS):
        chain = privkey[i * HASH_SIZE:(i + 1) * HASH_SIZE]
        chain = hash_chain(chain, CHAIN_LEN)
        pub.extend(chain)
    return bytes(pub)


def wots_sign(privkey: bytes, msg_hash: bytes) -> bytes:
    """Create WOTS+ signature."""
    values = msg_to_chain_values(msg_hash)
    sig = bytearray()
    for i in range(TOTAL_CHAINS):
        chain = privkey[i * HASH_SIZE:(i + 1) * HASH_SIZE]
        chain = hash_chain(chain, values[i])
        sig.extend(chain)
    return bytes(sig)


def wots_extract_pubkey(sig: bytes, msg_hash: bytes) -> bytes:
    """Complete the hash chains to reconstruct the public key from a signature."""
    values = msg_to_chain_values(msg_hash)
    pub = bytearray()
    for i in range(TOTAL_CHAINS):
        chain = sig[i * HASH_SIZE:(i + 1) * HASH_SIZE]
        remaining = CHAIN_LEN - values[i]
        chain = hash_chain(chain, remaining)
        pub.extend(chain)
    return bytes(pub)


def merkle_leaf_hash(pubkey: bytes) -> bytes:
    """Compute Merkle leaf: H(pubkey)."""
    return sha256(pubkey)


def merkle_build_tree(leaves: list) -> bytes:
    """Build Merkle tree from leaves, return root."""
    cur = list(leaves)
    while len(cur) > 1:
        nxt = []
        for i in range(0, len(cur), 2):
            nxt.append(sha256(cur[i] + cur[i + 1]))
        cur = nxt
    return cur[0]


def merkle_verify_path(leaf: bytes, leaf_idx: int, auth_path: bytes) -> bytes:
    """Verify auth path: compute root from leaf + auth path."""
    current = leaf
    idx = leaf_idx
    for level in range(MERKLE_HEIGHT):
        sibling = auth_path[level * HASH_SIZE:(level + 1) * HASH_SIZE]
        if idx % 2 == 0:
            current = sha256(current + sibling)
        else:
            current = sha256(sibling + current)
        idx //= 2
    return current


def verify_quantum_signature(
    merkle_root: bytes,
    msg_hash: bytes,
    sig: bytes
) -> bool:
    """
    Verify a quantum-resistant WOTS+ Merkle signature.

    Args:
        merkle_root: 32-byte Merkle root (signer's address)
        msg_hash: 32-byte message hash that was signed
        sig: QSIG_SIZE-byte signature

    Returns:
        True if valid, False otherwise
    """
    if len(sig) != QSIG_SIZE:
        return False

    # Extract components
    wots_sig = sig[:WOTS_SIG_SIZE]
    auth_path = sig[WOTS_SIG_SIZE:WOTS_SIG_SIZE + MERKLE_AUTH_SIZE]
    ots_idx = struct.unpack('<I', sig[WOTS_SIG_SIZE + MERKLE_AUTH_SIZE:])[0]

    if ots_idx >= MERKLE_LEAVES:
        return False

    # Step 1: Complete WOTS+ chains → reconstructed pubkey
    computed_pubkey = wots_extract_pubkey(wots_sig, msg_hash)

    # Step 2: Hash pubkey → leaf
    leaf = merkle_leaf_hash(computed_pubkey)

    # Step 3: Verify Merkle auth path
    computed_root = merkle_verify_path(leaf, ots_idx, auth_path)

    # Step 4: Compare to expected root
    return computed_root == merkle_root


def generate_quantum_address(seed: bytes) -> tuple:
    """
    Generate a quantum-resistant wallet address from a seed.

    Returns: (merkle_root, all_leaves) where all_leaves can be
    used to compute auth paths.
    """
    leaves = []
    for k in range(MERKLE_LEAVES):
        k_seed = sha256(seed + struct.pack('<I', k))
        privkey = wots_gen_privkey(k_seed)
        pubkey = wots_gen_pubkey(privkey)
        leaves.append(merkle_leaf_hash(pubkey))

    root = merkle_build_tree(leaves)
    return root, leaves


def create_quantum_signature(
    seed: bytes,
    ots_idx: int,
    msg_hash: bytes,
    leaves: list = None
) -> bytes:
    """
    Create a quantum-resistant signature (node-side, for testing).

    Args:
        seed: master seed
        ots_idx: which OTS key to use
        msg_hash: message to sign
        leaves: precomputed Merkle leaves (optional, will compute if needed)
    """
    # Derive OTS seed
    ots_seed = sha256(seed + struct.pack('<I', ots_idx))
    privkey = wots_gen_privkey(ots_seed)

    # WOTS+ sign
    wots_sig = wots_sign(privkey, msg_hash)

    # Compute auth path if leaves not provided
    if leaves is None:
        leaves = []
        for k in range(MERKLE_LEAVES):
            k_seed = sha256(seed + struct.pack('<I', k))
            k_priv = wots_gen_privkey(k_seed)
            k_pub = wots_gen_pubkey(k_priv)
            leaves.append(merkle_leaf_hash(k_pub))

    # Compute auth path
    auth_path = bytearray()
    idx = ots_idx
    cur = list(leaves)
    for level in range(MERKLE_HEIGHT):
        sibling = 1 if idx % 2 == 0 else -1
        sib_idx = idx + sibling
        auth_path.extend(cur[sib_idx])

        nxt = []
        for i in range(0, len(cur), 2):
            nxt.append(sha256(cur[i] + cur[i + 1]))
        cur = nxt
        idx //= 2

    return wots_sig + bytes(auth_path) + struct.pack('<I', ots_idx)
