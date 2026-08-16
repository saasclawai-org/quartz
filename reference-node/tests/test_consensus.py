"""
Quartz consensus engine tests — UTXO set, mempool, block validation, fork choice.
"""

import pytest
import hashlib
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward, get_miner_reward, DIFFICULTY_BITS,
)
from quartz.consensus import (
    UTXOSet, UTXO, Mempool, ConsensusEngine,
    validate_transaction, validate_block, apply_block,
    MEMPOOL_MAX, MAX_BLOCK_TXS, COINBASE_MATURITY,
)


# ============================================================
# UTXO Set Tests
# ============================================================

class TestUTXOSet:
    def test_add_and_get(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        u = UTXO(txid=txid, index=0, amount=100, script_pubkey=b'\x02' * 32, created_height=1)
        utxos.add(u)
        assert utxos.get(txid, 0) is not None
        assert utxos.has(txid, 0)

    def test_remove(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        u = UTXO(txid=txid, index=0, amount=100, script_pubkey=b'\x02' * 32, created_height=1)
        utxos.add(u)
        removed = utxos.remove(txid, 0)
        assert removed is not None
        assert removed.amount == 100
        assert not utxos.has(txid, 0)

    def test_get_balance(self):
        utxos = UTXOSet()
        script = b'\x02' * 32
        utxos.add(UTXO(txid=b'\x01' * 32, index=0, amount=50, script_pubkey=script, created_height=1))
        utxos.add(UTXO(txid=b'\x02' * 32, index=0, amount=30, script_pubkey=script, created_height=2))
        utxos.add(UTXO(txid=b'\x03' * 32, index=0, amount=100, script_pubkey=b'\x99' * 32, created_height=3))
        assert utxos.get_balance(script) == 80

    def test_get_utxos_for(self):
        utxos = UTXOSet()
        script = b'\x02' * 32
        u1 = UTXO(txid=b'\x01' * 32, index=0, amount=50, script_pubkey=script, created_height=1)
        u2 = UTXO(txid=b'\x02' * 32, index=1, amount=30, script_pubkey=script, created_height=2)
        utxos.add(u1)
        utxos.add(u2)
        result = utxos.get_utxos_for(script)
        assert len(result) == 2

    def test_snapshot_and_restore(self):
        utxos = UTXOSet()
        utxos.add(UTXO(txid=b'\x01' * 32, index=0, amount=50, script_pubkey=b'\x02' * 32, created_height=1))
        snap = utxos.snapshot()
        utxos.add(UTXO(txid=b'\x03' * 32, index=0, amount=100, script_pubkey=b'\x04' * 32, created_height=2))
        assert len(utxos) == 2
        utxos.restore(snap)
        assert len(utxos) == 1


# ============================================================
# Transaction Validation Tests
# ============================================================

class TestTransactionValidation:
    def test_valid_coinbase(self):
        tx = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 0)
        is_valid, fee, spends, creates, reason = validate_transaction(tx, UTXOSet(), is_coinbase=True)
        assert is_valid, f"coinbase should be valid: {reason}"
        assert fee == 0
        assert len(creates) == 1

    def test_coinbase_must_have_one_input(self):
        tx = Transaction(version=1, inputs=[], outputs=[(100, b'\x00' * 32)])
        is_valid, _, _, _, reason = validate_transaction(tx, UTXOSet(), is_coinbase=True)
        assert not is_valid
        assert "exactly 1 input" in reason

    def test_regular_tx_no_inputs(self):
        tx = Transaction(version=1, inputs=[], outputs=[(100, b'\x00' * 32)])
        is_valid, _, _, _, reason = validate_transaction(tx, UTXOSet())
        assert not is_valid
        assert "no inputs" in reason

    def test_regular_tx_no_outputs(self):
        tx = Transaction(version=1, inputs=[(b'\x01' * 32, 0, b'\x00' * 64, b'\x00' * 32)], outputs=[])
        is_valid, _, _, _, reason = validate_transaction(tx, UTXOSet())
        assert not is_valid
        assert "no outputs" in reason

    def test_utxo_not_found(self):
        tx = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, b'\x00' * 64, b'\x00' * 32)],
            outputs=[(100, b'\x00' * 32)],
        )
        is_valid, _, _, _, reason = validate_transaction(tx, UTXOSet())
        assert not is_valid
        assert "UTXO not found" in reason

    def test_double_spend_within_tx(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=200, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[
                (txid, 0, b'\x00' * 64, pubkey),
                (txid, 0, b'\x00' * 64, pubkey),  # same UTXO
            ],
            outputs=[(150, b'\x00' * 32)],
        )
        is_valid, _, _, _, reason = validate_transaction(tx, utxos)
        assert not is_valid
        assert "double-spend" in reason

    def test_valid_tx_with_utxo(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=200, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(150, b'\x00' * 32)],
        )
        is_valid, fee, spends, creates, reason = validate_transaction(tx, utxos)
        assert is_valid, f"should be valid: {reason}"
        assert fee == 50  # 200 - 150
        assert len(spends) == 1
        assert len(creates) == 1

    def test_outputs_exceed_inputs(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=100, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(150, b'\x00' * 32)],
        )
        is_valid, _, _, _, reason = validate_transaction(tx, utxos)
        assert not is_valid
        assert "exceed" in reason.lower()

    def test_data_limit(self):
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=200, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(100, b'\x00' * 32)],
            data=b'x' * 300,  # exceeds 256 limit
        )
        is_valid, _, _, _, reason = validate_transaction(tx, utxos)
        assert not is_valid
        assert "data" in reason.lower()


# ============================================================
# Mempool Tests
# ============================================================

class TestMempool:
    def test_add_valid_tx(self):
        pool = Mempool()
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=10000, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(9000, b'\x00' * 32)],
        )
        ok, reason = pool.add(tx, utxos)
        assert ok, f"should accept: {reason}"
        assert pool.size() == 1

    def test_no_duplicates(self):
        pool = Mempool()
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=10000, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(9000, b'\x00' * 32)],
        )
        pool.add(tx, utxos)
        ok, reason = pool.add(tx, utxos)
        assert not ok
        assert "already" in reason

    def test_double_spend_in_mempool(self):
        pool = Mempool()
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=10000, script_pubkey=script, created_height=1))

        tx1 = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(9000, b'\x00' * 32)],
        )
        tx2 = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],  # same UTXO
            outputs=[(8000, b'\x00' * 32)],
        )
        pool.add(tx1, utxos)
        ok, reason = pool.add(tx2, utxos)
        assert not ok
        assert "double-spend" in reason

    def test_remove_after_confirm(self):
        pool = Mempool()
        utxos = UTXOSet()
        txid = b'\x01' * 32
        pubkey = b'\x00' * 32
        script = hashlib.sha256(pubkey).digest()
        utxos.add(UTXO(txid=txid, index=0, amount=10000, script_pubkey=script, created_height=1))

        tx = Transaction(
            version=1,
            inputs=[(txid, 0, b'\x00' * 64, pubkey)],
            outputs=[(9000, b'\x00' * 32)],
        )
        pool.add(tx, utxos)
        assert pool.size() == 1
        pool.remove(tx.txid)
        assert pool.size() == 0


# ============================================================
# Block Validation Tests
# ============================================================

class TestBlockValidation:
    def _mine_test_block(self, prev_block, transactions, difficulty=12):
        """Helper: create and mine a block at given difficulty."""
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_block.header.hash if prev_block else b'\x00' * 32,
            timestamp=int(time.time()),
            difficulty_target=difficulty,
        )
        block = Block(header=header, transactions=transactions)
        block.build_header()

        target = 1 << (256 - difficulty)
        while True:
            h = block.header.hash
            if int.from_bytes(h, 'big') < target:
                break
            block.header.nonce += 1
        return block

    def test_valid_block_with_coinbase(self):
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        genesis.build_header()

        # Mine genesis
        target = 1 << (256 - 12)
        while True:
            if int.from_bytes(genesis.header.hash, 'big') < target:
                break
            genesis.header.nonce += 1

        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        block = self._mine_test_block(genesis, [coinbase])

        utxos = UTXOSet()
        is_valid, reason, fees = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=utxos, current_difficulty=12, expected_difficulty=12,
        )
        assert is_valid, f"block should be valid: {reason}"
        assert fees == 0

    def test_invalid_pow(self):
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        # Don't mine — set a high hash that won't meet difficulty
        genesis.header.nonce = 0
        genesis.build_header()

        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        block = Block(
            header=BlockHeader(
                version=1,
                prev_block_hash=genesis.header.hash,
                timestamp=int(time.time()),
                difficulty_target=12,
                nonce=0,  # not mined
            ),
            transactions=[coinbase],
        )
        block.build_header()

        is_valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=UTXOSet(), current_difficulty=12, expected_difficulty=12,
        )
        assert not is_valid
        assert "PoW" in reason or "difficulty" in reason

    def test_wrong_prev_hash(self):
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        genesis.build_header()

        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        # Build on a WRONG prev hash, but mine it to valid PoW so we
        # reach the linkage check instead of failing PoW first.
        block = self._mine_test_block_from_prev(
            b'\xFF' * 32, [coinbase], difficulty=12,
        )

        is_valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=UTXOSet(), current_difficulty=12, expected_difficulty=12,
        )
        assert not is_valid
        assert "prev_hash" in reason

    def _mine_test_block_from_prev(self, prev_hash, transactions, difficulty=12):
        """Mine a block against an explicit prev hash (may be wrong on purpose)."""
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_hash,
            timestamp=int(time.time()),
            difficulty_target=difficulty,
        )
        block = Block(header=header, transactions=transactions)
        block.build_header()

        target = 1 << (256 - difficulty)
        while True:
            if int.from_bytes(block.header.hash, 'big') < target:
                break
            block.header.nonce += 1
        return block

    def test_no_transactions(self):
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        genesis.build_header()

        block = Block(
            header=BlockHeader(
                version=1,
                prev_block_hash=genesis.header.hash,
                timestamp=int(time.time()),
                difficulty_target=12,
            ),
            transactions=[],
        )
        block.build_header()

        is_valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=UTXOSet(), current_difficulty=12, expected_difficulty=12,
        )
        assert not is_valid
        assert "no transactions" in reason

    def test_wrong_difficulty(self):
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        genesis.build_header()

        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        block = self._mine_test_block(genesis, [coinbase], difficulty=12)

        is_valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=UTXOSet(), current_difficulty=12, expected_difficulty=14,  # wrong
        )
        assert not is_valid
        assert "difficulty" in reason.lower()


# ============================================================
# Consensus Engine Integration Tests
# ============================================================

class TestConsensusEngine:
    def _create_genesis(self):
        """Create a mined genesis block."""
        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())
        genesis.header.difficulty_target = 12
        genesis.transactions = [Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 0)]
        genesis.build_header()

        target = 1 << (256 - 12)
        while True:
            if int.from_bytes(genesis.header.hash, 'big') < target:
                break
            genesis.header.nonce += 1
        return genesis

    def test_init_builds_utxo_set(self):
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )
        assert len(engine.utxo_set) == 1  # one coinbase output
        assert engine.height == 0

    def test_accept_valid_block(self):
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )

        # Build a new block
        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        header = BlockHeader(
            version=1,
            prev_block_hash=genesis.header.hash,
            timestamp=int(time.time()),
            difficulty_target=12,
        )
        block = Block(header=header, transactions=[coinbase])
        block.build_header()

        # Mine it
        target = 1 << (256 - 12)
        while True:
            if int.from_bytes(block.header.hash, 'big') < target:
                break
            block.header.nonce += 1

        ok, reason = engine.accept_block(block)
        assert ok, f"should accept: {reason}"
        assert engine.height == 1
        assert len(engine.utxo_set) == 2  # genesis + new coinbase

    def test_reject_block_with_bad_prev_hash(self):
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )

        coinbase = Transaction.coinbase(b'\x00' * 6, 4_200_000_000, 1)
        header = BlockHeader(
            version=1,
            prev_block_hash=b'\xFF' * 32,  # wrong
            timestamp=int(time.time()),
            difficulty_target=12,
        )
        block = Block(header=header, transactions=[coinbase])
        block.build_header()

        # Mine it
        target = 1 << (256 - 12)
        while True:
            if int.from_bytes(block.header.hash, 'big') < target:
                break
            block.header.nonce += 1

        ok, reason = engine.accept_block(block)
        assert not ok

    def test_build_block_template(self):
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )

        template = engine.build_block_template(b'\x00' * 6)
        assert template is not None
        assert len(template.transactions) >= 1  # at least coinbase
        assert template.transactions[0].inputs[0][0] == b'\x00' * 32  # coinbase null input

    def _mine_on(self, prev_block, height, difficulty=12, miner_id=b'\x00' * 6):
        """Mine a coinbase-only block on top of prev_block."""
        coinbase = Transaction.coinbase(miner_id, 4_200_000_000, height)
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_block.header.hash,
            timestamp=int(time.time()),
            difficulty_target=difficulty,
        )
        block = Block(header=header, transactions=[coinbase])
        block.build_header()
        target = 1 << (256 - difficulty)
        while True:
            if int.from_bytes(block.header.hash, 'big') < target:
                break
            block.header.nonce += 1
        return block

    def test_fork_choice_longer_chain_wins(self):
        """Two competing forks: the longer one must become the active chain."""
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )

        # Fork A: 1 block
        a1 = self._mine_on(genesis, 1)
        ok, reason = engine.accept_block(a1)
        assert ok, reason
        assert engine.height == 1

        # Fork B: 2 blocks building on genesis (sibling of a1, then a child)
        b1 = self._mine_on(genesis, 1)
        # b1 is a sibling of a1 — equal work, first-seen rule keeps a1 active,
        # but b1 must be INDEXED so its children can extend it.
        ok, reason = engine.accept_block(b1)
        assert not ok  # does not become active
        assert "less work" in reason
        assert engine.height == 1  # still on fork A
        assert b1.header.hash in engine._block_index  # but stored

        b2 = self._mine_on(b1, 2)
        ok, reason = engine.accept_block(b2)
        assert ok, reason
        # Fork B is now longer (2 > 1) — must be the active chain
        assert engine.height == 2
        assert engine.blocks[-1].header.hash == b2.header.hash

    def test_reorg_moves_utxo_set(self):
        """After a reorg, UTXO set must reflect the winning fork's coinbases."""
        genesis = self._create_genesis()
        engine = ConsensusEngine(
            blocks=[genesis],
            balances={},
            current_difficulty=12,
            block_time=30,
            retarget_period=144,
        )

        a1 = self._mine_on(genesis, 1, miner_id=b'\xAA' * 6)  # fork A miner
        assert engine.accept_block(a1)[0]
        a1_utxos = len(engine.utxo_set)

        b1 = self._mine_on(genesis, 1, miner_id=b'\xBB' * 6)  # fork B miner
        result = engine.accept_block(b1)  # stored as fork, not active
        assert not result[0]
        b2 = self._mine_on(b1, 2, miner_id=b'\xBB' * 6)
        assert engine.accept_block(b2)[0]

        # Active chain is now b1+b2; a1's coinbase must be gone,
        # b1's and b2's coinbases must exist.
        assert engine.height == 2
        assert len(engine.utxo_set) == a1_utxos + 1  # genesis + b1 + b2
        assert engine.utxo_set.has(b1.transactions[0].txid, 0)
        assert engine.utxo_set.has(b2.transactions[0].txid, 0)
        assert not engine.utxo_set.has(a1.transactions[0].txid, 0)