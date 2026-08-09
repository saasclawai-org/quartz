"""
Quartz blockchain tests — verify block/tx structures, merkle roots, difficulty.
"""

import pytest
import hashlib
import struct
from quartz.blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward, HALVING_INTERVAL, INITIAL_REWARD, HEADER_SIZE,
    DIFFICULTY_BITS,
)
from quartz.crystal_hash import (
    crystal_hash_verify, check_difficulty, bits_to_target,
    SCRATCHPAD_SIZE,
)
from quartz.wallet import Wallet, _base58_encode, _base58_decode


class TestBlockHeader:
    def test_header_serialize_roundtrip(self):
        hdr = BlockHeader(
            version=1,
            prev_block_hash=b'\xAA' * 32,
            merkle_root=b'\xBB' * 32,
            timestamp=1700000000,
            difficulty_target=20,
            nonce=12345,
            miner_id=b'\xCC\xCC\xCC\xCC\xCC\xCC',
        )
        data = hdr.serialize()
        assert len(data) == HEADER_SIZE

        hdr2 = BlockHeader.deserialize(data)
        assert hdr2.version == 1
        assert hdr2.prev_block_hash == b'\xAA' * 32
        assert hdr2.merkle_root == b'\xBB' * 32
        assert hdr2.timestamp == 1700000000
        assert hdr2.difficulty_target == 20
        assert hdr2.nonce == 12345
        # miner_id is not in the 80-byte serialized header (stored in block body)

    def test_header_hash_is_sha256(self):
        hdr = BlockHeader()
        expected = hashlib.sha256(hdr.serialize()).digest()
        assert hdr.hash == expected

    def test_genesis_block(self):
        genesis = Block.create_genesis()
        assert genesis.header.prev_block_hash == b'\x00' * 32
        assert genesis.header.version == 1


class TestMerkleRoot:
    def test_empty_merkle(self):
        root = compute_merkle_root([])
        assert root == b'\x00' * 32

    def test_single_tx_merkle(self):
        tx_hash = b'\x11' * 32
        root = compute_merkle_root([tx_hash])
        assert root == tx_hash

    def test_two_tx_merkle(self):
        h1 = b'\x11' * 32
        h2 = b'\x22' * 32
        expected = hashlib.sha256(h1 + h2).digest()
        root = compute_merkle_root([h1, h2])
        assert root == expected

    def test_odd_tx_count(self):
        """Odd number of txs should duplicate the last."""
        h1 = b'\x11' * 32
        h2 = b'\x22' * 32
        h3 = b'\x33' * 32
        # Level 1: hash(h1+h2), hash(h3+h3)
        left = hashlib.sha256(h1 + h2).digest()
        right = hashlib.sha256(h3 + h3).digest()
        expected = hashlib.sha256(left + right).digest()
        root = compute_merkle_root([h1, h2, h3])
        assert root == expected


class TestBlockReward:
    def test_initial_reward(self):
        assert get_block_reward(0) == INITIAL_REWARD  # 50 QZ
        assert get_block_reward(1) == INITIAL_REWARD

    def test_first_halving(self):
        assert get_block_reward(HALVING_INTERVAL) == INITIAL_REWARD // 2  # 25 QZ

    def test_second_halving(self):
        assert get_block_reward(HALVING_INTERVAL * 2) == INITIAL_REWARD // 4  # 12.5 QZ

    def test_zero_after_33_halvings(self):
        assert get_block_reward(HALVING_INTERVAL * 33) == 0


class TestDifficulty:
    def test_bits_to_target_zero(self):
        target = bits_to_target(0)
        assert target == b'\x00' * 32

    def test_low_difficulty_hash_passes(self):
        # Zero hash should pass Bitcoin-style difficulty (0x1d00ffff = easy)
        zero_hash = b'\x00' * 32
        assert check_difficulty(zero_hash, 0x1d00ffff)

    def test_high_difficulty_blocks(self):
        hard_bits = 0x1D00FFFF  # Bitcoin-style
        low_hash = b'\x00' * 31 + b'\x01'
        high_hash = b'\xFF' * 32
        assert check_difficulty(low_hash, hard_bits)
        assert not check_difficulty(high_hash, hard_bits)


class TestCrystalHash:
    def test_deterministic_output(self):
        """Same input should produce same hash (without PUF)."""
        header = b'\x42' * 80
        nonce = 42
        h1 = crystal_hash_verify(header, nonce)
        h2 = crystal_hash_verify(header, nonce)
        assert h1 == h2

    def test_different_nonce_different_hash(self):
        header = b'\x42' * 80
        h1 = crystal_hash_verify(header, 1)
        h2 = crystal_hash_verify(header, 2)
        assert h1 != h2

    def test_different_header_different_hash(self):
        h1 = crystal_hash_verify(b'\x42' * 80, 1)
        h2 = crystal_hash_verify(b'\x43' * 80, 1)
        assert h1 != h2

    def test_output_size(self):
        h = crystal_hash_verify(b'\x42' * 80, 1)
        assert len(h) == 32


class TestWallet:
    def test_wallet_creation(self):
        w = Wallet()
        assert len(w.private_key) == 32
        assert w.address  # has an address

    def test_wallet_testnet(self):
        w = Wallet(testnet=True)
        assert w.address  # has an address
        assert w.testnet is True

    def test_wallet_deterministic(self):
        key = b'\x01' * 32
        w1 = Wallet(private_key=key)
        w2 = Wallet(private_key=key)
        assert w1.address == w2.address

    def test_wallet_json_roundtrip(self):
        w = Wallet()
        data = w.to_json()
        w2 = Wallet.from_json(data)
        assert w.private_key == w2.private_key
        assert w.address == w2.address

    def test_base58_roundtrip(self):
        original = b'\x00\x01\x02\x03\xff\xfe\xfd'
        encoded = _base58_encode(original)
        decoded = _base58_decode(encoded)
        assert decoded == original


class TestTransaction:
    def test_coinbase(self):
        miner_id = b'\xAA\xBB\xCC\xDD\xEE\xFF'
        tx = Transaction.coinbase(miner_id, 50 * 10**8, 0)
        assert tx.version == 1
        assert len(tx.inputs) == 1
        assert len(tx.outputs) == 1
        assert tx.outputs[0][0] == 50 * 10**8

    def test_txid_deterministic(self):
        miner_id = b'\xAA\xBB\xCC\xDD\xEE\xFF'
        tx1 = Transaction.coinbase(miner_id, 50 * 10**8, 0)
        tx2 = Transaction.coinbase(miner_id, 50 * 10**8, 0)
        assert tx1.txid == tx2.txid
