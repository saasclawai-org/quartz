"""
Founder timelock covenant tests (P0-1, docs/FOUNDER_TIMELOCK.md).

Covenant: while active, coinbase outputs paying a configured founder
address are consensus-locked for FOUNDER_TIMELOCK_BLOCKS (2 years at
30 s blocks); coinbase maturity (COINBASE_MATURITY) rides the same
mechanism. Enforcement is two-sided:

  - creation: apply_block / engine rebuild stamp lock_until on coinbase
    outputs (regular transfers are never locked — the covenant binds
    founder-MINED coins only)
  - spend:    validate_transaction rejects any input whose UTXO has
    lock_until > block height (mempool checks at tip+1, inclusive)

Activation is forward-only: locks stamp NEW outputs, never existing ones.
Default (no config) is fully inert — today's testnet behavior.
"""

import hashlib
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward,
)
from quartz.consensus import (
    UTXOSet, UTXO, ConsensusEngine,
    validate_transaction, validate_block, apply_block, undo_block,
    configure_timelock, timelock_enabled, timelock_status,
    FOUNDER_TIMELOCK_BLOCKS, COINBASE_MATURITY,
)

FOUNDER = 'R8kfauVUExPnQdKoFeFVMAFyyrSCks3BVg'
OTHER = 'Qq82MvBbjUwhQ3BjD7Z9Rs8XGSc1JZmWgi'


@pytest.fixture(autouse=True)
def _reset_timelock():
    """Every test starts and ends with the covenant fully reset."""
    configure_timelock([], enabled=False, activation_height=0)
    yield
    configure_timelock([], enabled=False, activation_height=0)


# ============================================================
# Helpers
# ============================================================

def _pubkey(seed: int) -> bytes:
    return hashlib.sha256(seed.to_bytes(4, 'little')).digest()[:32]


def _coinbase(payout_addr: str, reward: int, height: int) -> Transaction:
    return Transaction.coinbase(b'\x42' * 6, reward, height,
                                payout_addr=payout_addr)


def _block(txs, difficulty=1, timestamp=1_750_000_000):
    """Mined block at trivial difficulty (target 2^255 — a few nonces)."""
    header = BlockHeader(
        prev_block_hash=b'\x00' * 32,
        merkle_root=compute_merkle_root([tx.txid for tx in txs]),
        timestamp=timestamp,
        difficulty_target=difficulty,
    )
    target = 1 << (256 - difficulty)
    nonce = 0
    while True:
        header.nonce = nonce
        if int.from_bytes(header.hash, 'big') < target:
            break
        nonce += 1
    return Block(header=header, transactions=txs)


def _spend_tx(utxo_txid: bytes, utxo_index: int, utxo_amount: int,
              pk: bytes, out_amount: int) -> Transaction:
    """Spend a UTXO locked with sha256(pk) script (ed25519-style form)."""
    return Transaction(
        version=1,
        inputs=[(utxo_txid, utxo_index, b'\x11' * 64, pk)],
        outputs=[(out_amount, b'\x22' * 32)],
    )


# ============================================================
# Configuration
# ============================================================

class TestTimelockConfig:
    def test_default_is_inert(self):
        assert not timelock_enabled()
        cb = _coinbase(FOUNDER, get_block_reward(0), 0)
        us = UTXOSet()
        apply_block(_block([cb]), 0, us)
        assert us.get(cb.txid, 0).lock_until == 0

    def test_configure_enables_and_reports(self):
        configure_timelock([FOUNDER], enabled=True)
        assert timelock_enabled()
        st = timelock_status()
        assert st['enabled'] is True
        assert st['blocks'] == FOUNDER_TIMELOCK_BLOCKS
        assert st['founder_addresses'] == [FOUNDER]

    def test_two_years_of_blocks(self):
        # 2 years * 365.25 days * 86400 s / 30 s blocks = 2,102,400
        assert FOUNDER_TIMELOCK_BLOCKS == 2_102_400

    def test_activation_height_gates_stamping(self):
        # Mid-chain activation: coins mined BEFORE the activation height
        # stay free; from the activation height on, the covenant applies.
        configure_timelock([FOUNDER], enabled=True, activation_height=105)
        us = UTXOSet()
        pre = _coinbase(FOUNDER, 1000, 104)
        at = _coinbase(FOUNDER, 1000, 105)
        post = _coinbase(FOUNDER, 1000, 106)
        apply_block(_block([pre]), 104, us)
        apply_block(_block([at]), 105, us)
        apply_block(_block([post]), 106, us)
        assert us.get(pre.txid, 0).lock_until == 0
        assert us.get(at.txid, 0).lock_until == 105 + FOUNDER_TIMELOCK_BLOCKS
        assert us.get(post.txid, 0).lock_until == 106 + FOUNDER_TIMELOCK_BLOCKS


class TestActivationRebuildAgreement:
    """Incremental apply and full rebuild must agree, or a restart would
    rewrite lock history (locking coins that were free, or freeing locked
    ones). The activation height is what keeps them in agreement."""

    def _chain(self, n=8):
        return [_block([_coinbase(OTHER, get_block_reward(i), i)])
                for i in range(n)]

    def test_rebuild_matches_incremental_mid_chain(self):
        # Mine a founder coin at height 5 with covenant OFF; block 7 (post-
        # activation) is also a founder coinbase. Blocks must be applied at
        # their INDEX heights — appending shifts heights and diverges locks.
        blocks = self._chain()
        fcb = _coinbase(FOUNDER, get_block_reward(5), 5)
        blocks[5] = _block([fcb])

        # Activate at height 6; block 7 mimes founder coins too
        configure_timelock([FOUNDER], enabled=True, activation_height=6)
        late = _coinbase(FOUNDER, get_block_reward(7), 7)
        blocks[7] = _block([late])

        us = UTXOSet()
        for h, b in enumerate(blocks):
            apply_block(b, h, us)

        # Rebuild from the same blocks must produce identical locks
        eng = ConsensusEngine(blocks=blocks,
                              balances={}, current_difficulty=1)
        assert (eng.utxo_set.get(fcb.txid, 0).lock_until
                == us.get(fcb.txid, 0).lock_until == 0)
        assert (eng.utxo_set.get(late.txid, 0).lock_until
                == us.get(late.txid, 0).lock_until
                == 7 + FOUNDER_TIMELOCK_BLOCKS)


# ============================================================
# Stamping (creation side)
# ============================================================

class TestCovenantStamping:
    def test_founder_coinbase_locked_two_years(self):
        configure_timelock([FOUNDER], enabled=True)
        height = 100
        cb = _coinbase(FOUNDER, 1000, height)
        us = UTXOSet()
        apply_block(_block([cb]), height, us)
        u = us.get(cb.txid, 0)
        assert u is not None
        assert u.lock_until == height + FOUNDER_TIMELOCK_BLOCKS

    def test_other_coinbase_gets_maturity_only(self):
        configure_timelock([FOUNDER], enabled=True)
        height = 100
        cb = _coinbase(OTHER, 1000, height)
        us = UTXOSet()
        apply_block(_block([cb]), height, us)
        assert us.get(cb.txid, 0).lock_until == height + COINBASE_MATURITY

    def test_transfer_to_founder_not_locked(self):
        # The covenant binds founder-MINED coins, not coins other people
        # send the founder.
        configure_timelock([FOUNDER], enabled=True)
        pk = _pubkey(7)
        us = UTXOSet()
        us.add(UTXO(txid=b'\x01' * 32, index=0, amount=500,
                    script_pubkey=hashlib.sha256(pk).digest(),
                    created_height=5))
        transfer = Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, b'\x11' * 64, pk)],
            outputs=[(400, FOUNDER.encode())],
        )
        apply_block(
            _block([_coinbase(OTHER, get_block_reward(6), 6), transfer]),
            6, us)
        assert us.get(transfer.txid, 0).lock_until == 0

    def test_no_locks_when_disabled(self):
        # Explicitly NOT configured: even maturity stays off (today's
        # testnet behavior — no consensus change until activation).
        cb = _coinbase(OTHER, 1000, 100)
        us = UTXOSet()
        apply_block(_block([cb]), 100, us)
        assert us.get(cb.txid, 0).lock_until == 0


# ============================================================
# Spend enforcement
# ============================================================

class TestSpendEnforcement:
    def _locked_utxo(self, lock_until=200, amount=500):
        pk = _pubkey(3)
        us = UTXOSet()
        us.add(UTXO(txid=b'\x01' * 32, index=0, amount=amount,
                    script_pubkey=hashlib.sha256(pk).digest(),
                    created_height=10, lock_until=lock_until))
        return us, pk

    def test_spend_before_expiry_rejected(self):
        configure_timelock([FOUNDER], enabled=True)
        us, pk = self._locked_utxo()
        tx = _spend_tx(b'\x01' * 32, 0, 500, pk, 400)
        ok, _, _, _, reason = validate_transaction(tx, us, height=199)
        assert not ok
        assert 'locked' in reason

    def test_spend_at_unlock_height_accepted(self):
        # Inclusive: valid in the block AT lock_until.
        configure_timelock([FOUNDER], enabled=True)
        us, pk = self._locked_utxo()
        tx = _spend_tx(b'\x01' * 32, 0, 500, pk, 400)
        ok, _, _, _, reason = validate_transaction(tx, us, height=200)
        assert ok, reason

    def test_spend_after_expiry_accepted(self):
        configure_timelock([FOUNDER], enabled=True)
        us, pk = self._locked_utxo()
        tx = _spend_tx(b'\x01' * 32, 0, 500, pk, 400)
        ok, _, _, _, reason = validate_transaction(tx, us, height=10_000)
        assert ok, reason

    def test_block_with_locked_spend_rejected(self):
        configure_timelock([FOUNDER], enabled=True)
        us, pk = self._locked_utxo(lock_until=200)
        spend = _spend_tx(b'\x01' * 32, 0, 500, pk, 400)
        height = 150  # < 200
        cb = _coinbase(OTHER, get_block_reward(height), height)
        ok, reason, _ = validate_block(_block([cb, spend]), height, None,
                                       us, 1, 1)
        assert not ok
        assert 'locked' in reason

    def test_founder_coinbase_block_itself_still_valid(self):
        # The covenant never rejects a founder-mined BLOCK — it locks the
        # coins, it does not change block validity.
        configure_timelock([FOUNDER], enabled=True)
        height = 10
        cb = _coinbase(FOUNDER, get_block_reward(height), height)
        ok, reason, _ = validate_block(_block([cb]), height, None,
                                       UTXOSet(), 1, 1)
        assert ok, reason


# ============================================================
# Mempool / engine integration
# ============================================================

class TestMempoolIntegration:
    def _tx_spending_locked(self, pk):
        return Transaction(
            version=1,
            inputs=[(b'\x01' * 32, 0, b'\x11' * 64, pk)],
            outputs=[(4000, b'\x22' * 32)],
        )

    def test_mempool_rejects_locked_spend(self):
        configure_timelock([FOUNDER], enabled=True)
        cb = _coinbase(OTHER, get_block_reward(0), 0)
        eng = ConsensusEngine(blocks=[_block([cb])], balances={},
                              current_difficulty=1)
        pk = _pubkey(9)
        eng.utxo_set.add(UTXO(txid=b'\x01' * 32, index=0, amount=5000,
                              script_pubkey=hashlib.sha256(pk).digest(),
                              created_height=0, lock_until=300))
        ok, reason = eng.add_transaction(self._tx_spending_locked(pk))
        assert not ok
        assert 'locked' in reason

    def test_mempool_accepts_at_boundary_tip_plus_one(self):
        # tip = 1, next block height = 2, lock_until = 2 → relayable.
        configure_timelock([FOUNDER], enabled=True)
        eng = ConsensusEngine(
            blocks=[_block([_coinbase(OTHER, get_block_reward(0), 0)]),
                    _block([_coinbase(OTHER, get_block_reward(1), 1)])],
            balances={}, current_difficulty=1)
        pk = _pubkey(9)
        eng.utxo_set.add(UTXO(txid=b'\x01' * 32, index=0, amount=5000,
                              script_pubkey=hashlib.sha256(pk).digest(),
                              created_height=0, lock_until=2))
        ok, reason = eng.add_transaction(self._tx_spending_locked(pk))
        assert ok, reason


# ============================================================
# Engine configuration & rebuild
# ============================================================

class TestEngineConfig:
    def test_founder_addresses_param_activates_covenant(self):
        cb = _coinbase(FOUNDER, get_block_reward(0), 0)
        eng = ConsensusEngine(blocks=[_block([cb])], balances={},
                              current_difficulty=1,
                              founder_addresses=[FOUNDER])
        assert timelock_enabled()
        u = eng.utxo_set.get(cb.txid, 0)
        assert u is not None
        assert u.lock_until == FOUNDER_TIMELOCK_BLOCKS

    def test_rebuild_is_rule_faithful(self):
        # A chain built while the covenant was OFF, rebuilt with it ON:
        # locks are recomputed (rule-faithful rebuild), so an old founder
        # coinbase gets its covenant on rebuild.
        configure_timelock([FOUNDER], enabled=False)
        height = 5
        cb = _coinbase(FOUNDER, get_block_reward(height), height)
        blocks = [_block([_coinbase(OTHER, get_block_reward(i), i)])
                  for i in range(height)]
        blocks.append(_block([cb]))

        configure_timelock([FOUNDER], enabled=True)
        eng = ConsensusEngine(blocks=blocks, balances={}, current_difficulty=1)
        u = eng.utxo_set.get(cb.txid, 0)
        assert u is not None
        assert u.lock_until == height + FOUNDER_TIMELOCK_BLOCKS

    def test_no_founder_addresses_keeps_covenant_off(self):
        cb = _coinbase(FOUNDER, get_block_reward(0), 0)
        eng = ConsensusEngine(blocks=[_block([cb])], balances={},
                              current_difficulty=1)
        assert not timelock_enabled()
        assert eng.utxo_set.get(cb.txid, 0).lock_until == 0


# ============================================================
# Reorg safety
# ============================================================

class TestReorgSafety:
    def test_undo_redo_preserves_lock(self):
        configure_timelock([FOUNDER], enabled=True)
        height = 100
        cb = _coinbase(FOUNDER, 10_000, height)
        blk = _block([cb])
        us = UTXOSet()
        spent_log = []
        apply_block(blk, height, us, spent_log)
        u1 = us.get(cb.txid, 0)
        assert u1.lock_until == height + FOUNDER_TIMELOCK_BLOCKS

        snap = us.snapshot()
        undo_block(blk, us, spent_log)
        assert us.get(cb.txid, 0) is None

        apply_block(blk, height, us, [])
        u2 = us.get(cb.txid, 0)
        assert u2.lock_until == u1.lock_until

        us.restore(snap)
        assert us.get(cb.txid, 0).lock_until == u1.lock_until

    def test_activation_never_locks_existing_coins(self):
        # Mine founder coins while OFF, then activate: the already-created
        # UTXO must stay spendable (locks are forward-only).
        # NB: coinbase txids depend only on (miner_id, reward, height,
        # payout) — different heights, or the two txs collide.
        height = 100
        cb = _coinbase(FOUNDER, 10_000, height)
        us = UTXOSet()
        apply_block(_block([cb]), height, us)
        assert us.get(cb.txid, 0).lock_until == 0

        configure_timelock([FOUNDER], enabled=True)
        # A NEW founder coinbase one block later gets the covenant…
        cb2 = _coinbase(FOUNDER, 10_000, height + 1)
        apply_block(_block([cb2]), height + 1, us)
        assert (us.get(cb2.txid, 0).lock_until
                == height + 1 + FOUNDER_TIMELOCK_BLOCKS)
        # …the pre-activation coin does not.
        assert us.get(cb.txid, 0).lock_until == 0
