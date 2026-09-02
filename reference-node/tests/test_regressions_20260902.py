"""
Quartz regression tests — 2026-09-02 session scars (pay-to-relay bring-up).

Each test pins a production bug found during the vending-machine bring-up
so it can never return:

  REG-1  Multi-input txs falsely rejected as 'double-spend within block'.
         Root cause: `spent_in_block |= spends` sat INSIDE the per-spend
         loop, so any tx with 2+ inputs conflicted with itself. Whole
         blocks were rejected, the txs poison-evicted, nothing confirmed
         (all of 2026-09-02's "storms" were this, not client double-taps).

  REG-2  Fee floor: a zero-fee tx must fail mempool min-relay. (The
         production path: /send selection with a UTXO exactly equal to
         the price left change <= 0 → fee collapsed to 0 → min-relay
         reject. The consensus-level floor is pinned here.)

  REG-3  Genuine within-block double-spends must STILL be rejected —
         REG-1's fix must not over-correct.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.blockchain import Block, BlockHeader, Transaction
from quartz.consensus import (
    UTXOSet, UTXO, Mempool, validate_transaction, validate_block,
)

QZ = 100_000_000            # 1 QZ in sats
SIG = b'\x00' * 64          # well-formed ed25519-style (ref impl accepts)
KEY = b'\xAA' * 32          # spender pubkey
LOCK = KEY                  # script_pubkey: raw-pubkey lock (accepted form)


def _mine_genesis(difficulty=12):
    g = Block.create_genesis()
    g.header.timestamp = int(time.time())
    g.header.difficulty_target = difficulty
    g.build_header()
    target = 1 << (256 - difficulty)
    while int.from_bytes(g.header.hash, 'big') >= target:
        g.header.nonce += 1
    return g


def _mine_block(prev, txs, difficulty=12):
    b = Block(
        header=BlockHeader(
            version=1,
            prev_block_hash=prev.header.hash,
            timestamp=int(time.time()),
            difficulty_target=difficulty,
        ),
        transactions=txs,
    )
    b.build_header()
    target = 1 << (256 - difficulty)
    while int.from_bytes(b.header.hash, 'big') >= target:
        b.header.nonce += 1
    return b


def _utxos(*specs):
    """specs: (txid_32b, amount_sats) — all locked to KEY."""
    s = UTXOSet()
    for txid, amt in specs:
        s.add(UTXO(txid=txid, index=0, amount=amt,
                   script_pubkey=LOCK, created_height=1))
    return s


class TestMultiInputTx:
    """REG-1: multi-input transactions must validate."""

    def test_two_input_tx_validates_in_block(self):
        utxos = _utxos((b'\x01' * 32, 5 * QZ), (b'\x02' * 32, 7 * QZ))
        tx = Transaction(
            version=1,
            inputs=[
                (b'\x01' * 32, 0, SIG, KEY),
                (b'\x02' * 32, 0, SIG, KEY),
            ],
            outputs=[(12 * QZ - 1000, b'\xBB' * 32)],  # inputs 5+7 QZ → fee exactly 1000
        )
        ok, fee, spends, creates, reason = validate_transaction(tx, utxos, height=10)
        assert ok, f"multi-input tx must validate: {reason}"
        assert fee == 1000
        assert spends == {(b'\x01' * 32, 0), (b'\x02' * 32, 0)}

        genesis = _mine_genesis()
        coinbase = Transaction.coinbase(b'\x00' * 6, 42 * QZ + 1000, 1)
        block = _mine_block(genesis, [coinbase, tx])
        valid, reason, fees = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=utxos, current_difficulty=12, expected_difficulty=12,
        )
        assert valid, f"block with 2-input tx must validate: {reason}"
        assert fees == 1000

    def test_three_input_tx_validates_in_block(self):
        utxos = _utxos(
            (b'\x01' * 32, QZ), (b'\x02' * 32, QZ), (b'\x03' * 32, QZ),
        )
        tx = Transaction(
            version=1,
            inputs=[
                (b'\x01' * 32, 0, SIG, KEY),
                (b'\x02' * 32, 0, SIG, KEY),
                (b'\x03' * 32, 0, SIG, KEY),
            ],
            outputs=[(3 * QZ - 1000, b'\xBB' * 32)],
        )
        genesis = _mine_genesis()
        coinbase = Transaction.coinbase(b'\x00' * 6, 42 * QZ + 1000, 1)
        block = _mine_block(genesis, [coinbase, tx])
        valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=utxos, current_difficulty=12, expected_difficulty=12,
        )
        assert valid, f"3-input tx must validate: {reason}"


class TestWithinBlockDoubleSpend:
    """REG-3: real double-spends inside one block must still be rejected."""

    def test_same_outpoint_in_two_txs_rejected(self):
        utxos = _utxos((b'\x01' * 32, 5 * QZ))
        tx1 = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, SIG, KEY)],
            outputs=[(2 * QZ, b'\xBB' * 32)],
        )
        tx2 = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, SIG, KEY)],
            outputs=[(3 * QZ, b'\xCC' * 32)],
        )
        genesis = _mine_genesis()
        coinbase = Transaction.coinbase(b'\x00' * 6, 42 * QZ + 5 * QZ, 1)
        block = _mine_block(genesis, [coinbase, tx1, tx2])
        valid, reason, _ = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=utxos, current_difficulty=12, expected_difficulty=12,
        )
        assert not valid, "same-outpoint tx pair in one block must be rejected"
        assert "double-spend" in reason

    def test_disjoint_outpoints_in_two_txs_valid(self):
        """Two DIFFERENT txs spending DIFFERENT UTXOs — must both mine."""
        utxos = _utxos((b'\x01' * 32, 5 * QZ), (b'\x02' * 32, 6 * QZ))
        tx1 = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, SIG, KEY)],
            outputs=[(5 * QZ - 1000, b'\xBB' * 32)],
        )
        tx2 = Transaction(
            version=1,
            inputs=[(b'\x02' * 32, 0, SIG, KEY)],
            outputs=[(6 * QZ - 1000, b'\xCC' * 32)],
        )
        genesis = _mine_genesis()
        coinbase = Transaction.coinbase(b'\x00' * 6, 42 * QZ + 2000, 1)
        block = _mine_block(genesis, [coinbase, tx1, tx2])
        valid, reason, fees = validate_block(
            block=block, height=1, prev_block=genesis,
            utxo_set=utxos, current_difficulty=12, expected_difficulty=12,
        )
        assert valid, f"disjoint-input txs must both mine: {reason}"
        assert fees == 2000


class TestFeeFloor:
    """REG-2: zero-fee txs must fail mempool min-relay; paid fees pass."""

    def test_zero_fee_tx_rejected_by_mempool(self):
        utxos = _utxos((b'\x01' * 32, 5 * QZ))
        tx = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, SIG, KEY)],
            outputs=[(5 * QZ, b'\xBB' * 32)],   # consumes everything: fee 0
        )
        ok, fee, spends, creates, reason = validate_transaction(tx, utxos, height=10)
        assert ok, "structurally valid"
        assert fee == 0

        mp = Mempool()
        added, reason = mp.add(tx, utxos)
        assert not added, "zero-fee tx must be refused by mempool min-relay"
        assert 'fee' in str(reason).lower()

    def test_fee_paying_tx_accepted_by_mempool(self):
        utxos = _utxos((b'\x01' * 32, 5 * QZ))
        tx = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, SIG, KEY)],
            outputs=[(5 * QZ - 1000, b'\xBB' * 32)],
        )
        mp = Mempool()
        added, reason = mp.add(tx, utxos)
        assert added, f"fee-paying tx must enter mempool: {reason}"

    def test_multi_input_fee_paying_tx_accepted_by_mempool(self):
        """The exact REG-1 + REG-2 combo: 2 inputs, proper fee."""
        utxos = _utxos((b'\x01' * 32, QZ), (b'\x02' * 32, QZ))
        tx = Transaction(
            version=1,
            inputs=[
                (b'\x01' * 32, 0, SIG, KEY),
                (b'\x02' * 32, 0, SIG, KEY),
            ],
            outputs=[(2 * QZ - 1000, b'\xBB' * 32)],
        )
        mp = Mempool()
        added, reason = mp.add(tx, utxos)
        assert added, f"multi-input fee-paying tx must enter mempool: {reason}"
