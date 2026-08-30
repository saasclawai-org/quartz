"""Payment privacy tests — address streams and payment channels.

Plain English: these tests prove that (1) a wallet never hands out the
same address twice, (2) every address recovers from the seed alone,
(3) the payer's bundle contains addresses but no keys, and (4) signatures
from stream addresses verify like any other WOTS+ signature.
"""

import json

import pytest

from quartz.crypto import address_is_mainnet, validate_address
from quartz.quantum_crypto import sha256, verify_quantum_signature
from quartz.wallet import (
    ChannelBundle,
    PaymentChannel,
    StreamWallet,
    load_bundle,
)


class TestStreamWallet:
    def test_seed_recovers_everything(self):
        w1 = StreamWallet(seed=b'\x01' * 32)
        w2 = StreamWallet(seed=b'\x01' * 32)
        assert w1.address_at(0) == w2.address_at(0)
        assert w1.address_at(7) == w2.address_at(7)

    def test_rotation_never_repeats(self):
        w = StreamWallet(seed=b'\x02' * 32)
        seen = {w.next_address() for _ in range(6)}
        assert len(seen) == 6

    def test_addresses_are_valid_mainnet(self):
        w = StreamWallet(seed=b'\x03' * 32)
        for i in range(2):
            a = w.address_at(i)
            assert validate_address(a)
            assert address_is_mainnet(a)

    def test_streams_from_different_seeds_are_unrelated(self):
        a = StreamWallet(seed=b'\x0a' * 32).address_at(0)
        b = StreamWallet(seed=b'\x0b' * 32).address_at(0)
        assert a != b

    def test_sign_and_verify_roundtrip(self):
        w = StreamWallet(seed=b'\x04' * 32)
        msg = sha256(b'temperature log, 4.2C, ok')
        sig = w.sign_at(0, msg)
        assert verify_quantum_signature(w.root_at(0), msg, sig)
        assert not verify_quantum_signature(
            w.root_at(0), sha256(b'tampered'), sig)

    def test_json_roundtrip_preserves_stream(self):
        w = StreamWallet(seed=b'\x05' * 32)
        w.next_address()
        w2 = StreamWallet.from_json(w.to_json())
        assert w2.address_at(0) == w.address_at(0)
        assert w2.next_address() == w.address_at(1)


class TestPaymentChannel:
    def test_payer_sees_recipient_addresses(self):
        w = StreamWallet(seed=b'\x06' * 32)
        ch = w.create_channel(41)
        bundle = load_bundle(ch.export_bundle(3))
        assert bundle.addresses == ch.addresses(3)

    def test_bundle_contains_no_key_material(self):
        w = StreamWallet(seed=b'\x07' * 32)
        ch = w.create_channel()
        parsed = json.loads(json.dumps(ch.export_bundle(2)))
        assert set(parsed) == {
            'v', 'type', 'channel', 'net', 'count', 'addresses'}
        for a in parsed['addresses']:
            assert len(a) < 60  # an address string, never a key dump

    def test_bundle_can_pay_but_never_spend(self):
        # Structural guarantee: the payer object has no keys and no signer.
        ch = PaymentChannel.from_shared_secret(b's3')
        bundle = load_bundle(ch.export_bundle(2))
        assert not hasattr(bundle, 'seed')
        assert not hasattr(bundle, 'channel_seed')
        assert not hasattr(bundle, 'sign_at')

    def test_shared_secret_derives_same_stream_on_both_ends(self):
        alice = PaymentChannel.from_shared_secret(
            b'our-little-secret', channel_id=1)
        bob = PaymentChannel.from_shared_secret(
            b'our-little-secret', channel_id=1)
        assert alice.address_at(0) == bob.address_at(0)
        assert alice.address_at(1) == bob.address_at(1)

    def test_bundle_iterates_in_order_then_exhausts(self):
        ch = PaymentChannel.from_shared_secret(b'x', mainnet=False)
        bundle = load_bundle(ch.export_bundle(2))
        assert bundle.next_address() == ch.address_at(0)
        assert bundle.next_address() == ch.address_at(1)
        with pytest.raises(IndexError):
            bundle.next_address()

    def test_bundle_rejects_tampered_address(self):
        w = StreamWallet(seed=b'\x08' * 32)
        d = w.create_channel(2).export_bundle(1)
        d['addresses'][0] = 'Qnotarealaddress'
        with pytest.raises(ValueError):
            ChannelBundle(channel_id=2, mainnet=True,
                          addresses=d['addresses'])

    def test_load_bundle_rejects_foreign_json(self):
        with pytest.raises(ValueError):
            load_bundle({'type': 'something-else'})
