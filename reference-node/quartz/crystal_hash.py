"""
CrystalHash reference implementation for verification.

This is NOT for mining — it's for verifying blocks mined on real ESP32 hardware.
The PUF component is ESP32-specific and cannot be reproduced on a desktop CPU,
so this implements a verification mode that skips the PUF check and trusts
the miner_id binding.

On the ESP32, the full CrystalHash includes:
1. AES-256-CTR scratchpad initialization (256KB)
2. 64 rounds of memory-hard mixing
3. Flash cache PUF timing samples
4. Final SHA-256

This reference can do steps 1, 2, and 4 for testing.
Step 3 (PUF) produces different results on every chip and must be verified
by the network consensus (other ESP32 nodes confirm timing is in valid range).
"""

import hashlib
import struct
from Crypto.Cipher import AES
from typing import Tuple


SCRATCHPAD_SIZE = 256 * 1024  # 256KB


def init_scratchpad(header_bytes: bytes, nonce: int) -> bytes:
    """Initialize 256KB scratchpad via AES-256-CTR.

    Matches the ESP32 firmware implementation exactly.
    """
    key = bytearray(header_bytes[:32])

    # IV = nonce (8 bytes) + header[32:48] (8 bytes)
    iv = bytearray(8)
    struct.pack_into('<Q', iv, 0, nonce)
    iv.extend(header_bytes[32:40])

    # AES-256-CTR to fill 256KB
    cipher = AES.new(bytes(key), AES.MODE_CTR, nonce=bytes(iv[:8]),
                     initial_value=bytes(iv[8:16]))
    # Generate enough keystream
    scratchpad = cipher.encrypt(b'\x00' * SCRATCHPAD_SIZE)
    return scratchpad


def memory_hard_mix(state: bytes, scratchpad: bytes, nonce: int, rounds: int = 64) -> bytes:
    """64 rounds of memory-hard mixing.

    Each round reads from a pseudo-random offset in the scratchpad
    and SHA-256 mixes the result.
    """
    state = bytearray(state[:32])
    state_seed = struct.unpack('<I', struct.pack('<I', nonce & 0xFFFFFFFF))[0]

    for round_num in range(rounds):
        # Derive index from state
        idx_bytes = bytes(state[:4])
        idx_val = struct.unpack('<I', idx_bytes)[0]
        idx = (idx_val ^ state_seed ^ (round_num * 0x9E3779B9)) & 0xFFFFFFFF
        idx = idx % (SCRATCHPAD_SIZE // 32)

        # XOR state with scratchpad
        for i in range(32):
            state[i] ^= scratchpad[idx * 32 + i]

        # SHA-256 diffusion
        state = bytearray(hashlib.sha256(bytes(state)).digest())

    return bytes(state)


def crystal_hash_verify(header_bytes: bytes, nonce: int) -> bytes:
    """Compute CrystalHash WITHOUT the PUF step.

    Used for verifying that a block's hash is structurally correct.
    The PUF component adds chip-specific entropy that can't be reproduced here.

    Returns the 32-byte hash (without PUF mixing).
    """
    scratchpad = init_scratchpad(header_bytes, nonce)
    state = memory_hard_mix(header_bytes[:32], scratchpad, nonce)

    # Final SHA-256 of state + nonce
    final_input = state + struct.pack('<Q', nonce)
    return hashlib.sha256(final_input).digest()


def check_difficulty(block_hash: bytes, difficulty_bits: int) -> bool:
    """Check if a hash meets the difficulty target."""
    target = bits_to_target(difficulty_bits)
    return block_hash < target


def bits_to_target(bits: int) -> bytes:
    """Convert compact difficulty bits to 256-bit target."""
    exponent = bits >> 24
    mantissa = bits & 0x007FFFFF

    target = bytearray(32)

    if exponent <= 3:
        mantissa >>= (8 * (3 - exponent))
        target[28] = (mantissa >> 24) & 0xFF
        target[29] = (mantissa >> 16) & 0xFF
        target[30] = (mantissa >> 8) & 0xFF
        target[31] = mantissa & 0xFF
    else:
        pos = 32 - exponent
        if 0 <= pos < 30:
            target[pos] = (mantissa >> 16) & 0xFF
            if pos + 1 < 32:
                target[pos + 1] = (mantissa >> 8) & 0xFF
            if pos + 2 < 32:
                target[pos + 2] = mantissa & 0xFF

    return bytes(target)


def estimate_esp32_hashrate() -> float:
    """Rough estimate of ESP32 hashrate.

    ESP32 at 240MHz with hardware AES/SHA:
    - ~1000 H/s per core (mining core)
    - Difficulty 20 bits → expected ~1M hashes per block
    - ~17 minutes per block at difficulty 20

    Real benchmarks needed from actual hardware.
    """
    return 1000.0  # placeholder — needs real measurement
