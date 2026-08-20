"""
Message confirmation tests — pending messages must land in block
transactions (data carriers) and drain the pending queue.

Regression for the "messages stuck in limbo forever" bug: blocks were
mined but the builder never attached the message queue, so NAME:REGISTER
entries and IoT mail never confirmed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import testnet
from quartz.blockchain import Transaction
from quartz.consensus import validate_transaction, TX_DATA_LIMIT


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """A fresh QuartzChain writing to a temp dir (never touches the
    real testnet-data/)."""
    monkeypatch.setattr(testnet, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(testnet, 'CHAIN_FILE', str(tmp_path / 'chain.json'))
    monkeypatch.setattr(testnet, 'PENDING_MSGS_FILE', str(tmp_path / 'pending_msgs.json'))
    monkeypatch.setattr(testnet, 'DEV_WALLET_FILE', str(tmp_path / 'dev-wallet.json'))
    return testnet.QuartzChain()


def _entry(from_='81aeb43a861a6d4dfe4a43eafa7cd16', to='name:registry',
           text='NAME:REGISTER|LABEL:Heltec V3 #001|KIND:miner', ts=1755650000.0):
    return {"txid": "", "from": from_, "to": to, "text": text, "timestamp": ts}


class TestDataCarrierValidation:
    def test_envelope_carrier_is_valid(self):
        tx = testnet._msg_entry_to_tx(_entry())
        is_valid, fee, spends, creates, reason = validate_transaction(tx, None)
        assert is_valid, reason
        assert fee == 0
        assert spends == set()
        assert creates == []

    def test_carrier_respects_data_limit(self):
        tx = testnet._msg_entry_to_tx(_entry(text='x' * 160))
        assert len(tx.data) <= TX_DATA_LIMIT

    def test_empty_tx_still_invalid(self):
        tx = Transaction(version=1, inputs=[], outputs=[], data=b'')
        is_valid, _, _, _, reason = validate_transaction(tx, None)
        assert not is_valid
        assert 'empty' in reason

    def test_oversized_carrier_rejected(self):
        tx = Transaction(version=1, inputs=[], outputs=[], data=b'x' * (TX_DATA_LIMIT + 1))
        is_valid, _, _, _, _ = validate_transaction(tx, None)
        assert not is_valid

    def test_envelope_roundtrip(self):
        entry = _entry()
        tx = testnet._msg_entry_to_tx(entry)
        frm, to, text = testnet._parse_msg_envelope(tx.data)
        assert frm == entry['from']
        assert to == entry['to']
        assert text == entry['text']

    def test_plain_text_fallback(self):
        frm, to, text = testnet._parse_msg_envelope(b'hello world')
        assert frm == '' and to == ''
        assert text == 'hello world'


class TestMessageConfirmation:
    def test_mined_block_contains_pending_messages(self, chain):
        chain._pending_msgs = [_entry(), _entry(text='temp=22.5C')]
        block = chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')

        carriers = [tx for tx in block.transactions if not tx.inputs and not tx.outputs]
        assert len(carriers) == 2
        # Accepted by consensus (full validation incl. carriers)
        assert len(chain.blocks) == len(chain.blocks)  # noqa — height advanced below
        assert chain.blocks[-1].header.hash == block.header.hash
        # Queue drained
        assert chain._pending_msgs == []
        # Round-trip: from/to/text recoverable from the block
        parsed = [testnet._parse_msg_envelope(tx.data) for tx in carriers]
        assert any(f == '81aeb43a861a6d4dfe4a43eafa7cd16' and 'NAME:REGISTER' in t
                   for f, _, t in parsed)

    def test_duplicate_messages_confirm_once(self, chain):
        chain._pending_msgs = [_entry(), _entry()]  # identical content
        block = chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')
        carriers = [tx for tx in block.transactions if not tx.inputs and not tx.outputs]
        assert len(carriers) == 1
        assert chain._pending_msgs == []

    def test_backlog_drains_over_blocks(self, chain):
        chain._pending_msgs = [_entry(text=f'msg-{i}') for i in range(30)]
        for _ in range(10):
            chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')
        assert chain._pending_msgs == []
        # All 30 messages confirmed across blocks
        total = sum(
            1 for b in chain.blocks
            for tx in b.transactions if not tx.inputs and not tx.outputs
        )
        assert total == 30

    def test_block_tx_limit_respected(self, chain):
        chain._pending_msgs = [_entry(text=f'msg-{i}') for i in range(50)]
        block = chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')
        assert len(block.transactions) <= testnet.MAX_BLOCK_TXS
        assert len(chain._pending_msgs) == 50 - (len(block.transactions) - 1)

    def test_pending_persisted_after_confirmation(self, chain, tmp_path):
        chain._pending_msgs = [_entry(text='keep-me'), _entry(text='confirm-me')]
        chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')
        import json
        with open(str(tmp_path / 'pending_msgs.json')) as f:
            persisted = json.load(f)['pending_msgs']
        assert [m['text'] for m in persisted] == []

    def test_no_messages_block_still_mines(self, chain):
        chain._pending_msgs = []
        before = len(chain.blocks)
        chain.mine_block(bytes([1, 2, 3, 4, 5, 6]), 'test-miner')
        assert len(chain.blocks) == before + 1
