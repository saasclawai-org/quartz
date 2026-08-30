"""One-time signature (OTS) slot-reuse rejection tests.

Plain English: a WOTS+ address can sign only 256 times, ever — each
signature burns one numbered slot, and signing twice with the same slot
would let an attacker forge signatures. These tests prove that (1) the
first use of a slot is accepted, (2) any attempt to reuse a burned slot
is rejected — even in a later block, (3) the next fresh slot still works,
(4) tampered signatures are rejected outright, and (5) the old 64-byte
signature path is untouched.
"""

import hashlib
from types import SimpleNamespace

import pytest

from quartz.blockchain import Transaction
from quartz.consensus import UTXO, UTXOSet, apply_block, validate_transaction
from quartz.crypto import public_key_to_address
from quartz.quantum_crypto import QSIG_SIZE
from quartz.wallet import StreamWallet

SEED = b'\x11' * 32
TO_SCRIPT = hashlib.sha256(b'recipient').digest()  # 32-byte hash-lock


def _fund(utxo_set: UTXOSet, pubkey: bytes, amount: int = 100_000_000) -> bytes:
    """Create a UTXO locked to sha256(pubkey) — the standard hash-lock."""
    txid = hashlib.sha256(b'funding:' + pubkey).digest()
    utxo_set.add(UTXO(
        txid=txid, index=0, amount=amount,
        script_pubkey=hashlib.sha256(pubkey).digest(),
        created_height=1,
    ))
    return txid


def _spend(prev_txid: bytes, wallet: StreamWallet, index: int,
           ots_idx: int, amount: int) -> Transaction:
    """Build a signed WOTS+ spend. Signs the sighash (sig-free tx hash),
    then swaps the real signature in — sighash is unchanged by that."""
    root = wallet.root_at(index)
    tx = Transaction(
        inputs=[(prev_txid, 0, b'\x00' * QSIG_SIZE, root)],
        outputs=[(amount, TO_SCRIPT)],
    )
    sig = wallet.sign_at(index, tx.sighash, ots_idx=ots_idx)
    tx.inputs[0] = (prev_txid, 0, sig, root)
    return tx


@pytest.fixture(scope='module')
def wallet():
    return StreamWallet(seed=SEED)


class TestSlotReuse:
    def test_first_use_of_slot_is_valid(self, wallet):
        us = UTXOSet()
        root = wallet.root_at(0)
        addr = public_key_to_address(root)
        prev = _fund(us, root)
        tx = _spend(prev, wallet, 0, ots_idx=0, amount=90_000_000)

        overlay = {}
        ok, fee, _, _, reason = validate_transaction(
            tx, us, height=5, ots_used=overlay)
        assert ok, reason
        assert fee == 10_000_000
        assert overlay == {addr: 1}

        # Also valid without an overlay (crypto still verified)
        ok2, *_ = validate_transaction(tx, us, height=5)
        assert ok2

    def test_burned_slot_is_rejected(self, wallet):
        us = UTXOSet()
        root = wallet.root_at(0)
        prev = _fund(us, root)

        overlay = {}
        tx1 = _spend(prev, wallet, 0, ots_idx=0, amount=90_000_000)
        ok1, *_ = validate_transaction(tx1, us, height=5, ots_used=overlay)
        assert ok1

        # Same slot again — different payment, different sighash, same theft
        tx2 = _spend(prev, wallet, 0, ots_idx=0, amount=80_000_000)
        ok2, _, _, _, reason = validate_transaction(
            tx2, us, height=5, ots_used=overlay)
        assert not ok2
        assert 'slot' in reason.lower()

    def test_next_fresh_slot_still_works(self, wallet):
        us = UTXOSet()
        root = wallet.root_at(0)
        prev = _fund(us, root)

        overlay = {}
        tx1 = _spend(prev, wallet, 0, ots_idx=0, amount=90_000_000)
        ok1, *_ = validate_transaction(tx1, us, height=5, ots_used=overlay)
        assert ok1

        tx2 = _spend(prev, wallet, 0, ots_idx=1, amount=85_000_000)
        ok2, *_ = validate_transaction(tx2, us, height=5, ots_used=overlay)
        assert ok2
        assert overlay[public_key_to_address(root)] == 2

    def test_cross_block_state_rejects_old_slot(self, wallet):
        # "Storage says slot 0 was burned in an earlier block."
        us = UTXOSet()
        root = wallet.root_at(0)
        addr = public_key_to_address(root)
        prev = _fund(us, root)

        seeded = {addr: 1}
        tx = _spend(prev, wallet, 0, ots_idx=0, amount=90_000_000)
        ok, _, _, _, reason = validate_transaction(
            tx, us, height=99, ots_used=seeded)
        assert not ok
        assert 'slot' in reason.lower()

    def test_tampered_signature_rejected(self, wallet):
        us = UTXOSet()
        root = wallet.root_at(0)
        prev = _fund(us, root)

        tx = _spend(prev, wallet, 0, ots_idx=0, amount=90_000_000)
        prev_hash, idx, sig, pubkey = tx.inputs[0]
        bad = bytearray(sig)
        bad[100] ^= 0x01  # corrupt inside the WOTS+ portion
        tx.inputs[0] = (prev_hash, idx, bytes(bad), pubkey)

        ok, _, _, _, reason = validate_transaction(
            tx, us, height=5, ots_used={})
        assert not ok
        assert 'wots' in reason.lower()

    def test_ed25519_style_path_unchanged(self):
        # 64-byte sigs (well-formed policy) still accepted
        us = UTXOSet()
        pubkey = hashlib.sha256(b'some-ed25519-pubkey').digest()
        prev = _fund(us, pubkey)
        tx = Transaction(
            inputs=[(prev, 0, b'\x00' * 64, pubkey)],
            outputs=[(90_000_000, TO_SCRIPT)],
        )
        ok, *_ = validate_transaction(tx, us, height=5, ots_used={})
        assert ok

        # Wrong-length signatures still rejected
        tx_bad = Transaction(
            inputs=[(prev, 0, b'\x00' * 32, pubkey)],
            outputs=[(90_000_000, TO_SCRIPT)],
        )
        ok2, _, _, _, reason = validate_transaction(
            tx_bad, us, height=5, ots_used={})
        assert not ok2
        assert 'signature length' in reason


class TestApplyBlockRecording:
    def test_apply_block_records_burned_slots(self, wallet, tmp_path):
        from quartz.storage import LayeredStorage, StorageMode

        storage = LayeredStorage(str(tmp_path), mode=StorageMode.FULL)
        us = UTXOSet()
        root = wallet.root_at(0)
        addr = public_key_to_address(root)
        prev = _fund(us, root)

        tx = _spend(prev, wallet, 0, ots_idx=3, amount=90_000_000)
        block = SimpleNamespace(transactions=[Transaction(), tx])  # [coinbase, tx]

        apply_block(block, 3, us, storage=storage)

        state = storage.get_address_state(addr)
        assert state is not None
        assert state.wots_used == 4  # slot 3 burned → next forbidden below 4
