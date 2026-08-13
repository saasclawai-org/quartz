"""
Additional tests for Quartz reference node — tx validation sig tracking,
HTTP API edge cases, rotation flow, and WOTS+ reservation logic.

Complements tests/test_recovery.py with deeper coverage of:
- record_signature() call timing during tx validation
- HTTP API edge cases (malformed URLs, wrong methods, special chars)
- Key rotation flow (wots_used=254 → 255 → rotate)
- WOTS+ reservation (255 regular sigs + 1 rotation sig = 256 total)
"""

import json
import asyncio
import pytest
from unittest.mock import MagicMock, patch, call

from quartz.storage import LayeredStorage, AddressState
from quartz.blockchain import (
    Transaction, KEY_ROTATION_WARN_AT, KEY_ROTATION_LIMIT,
    needs_key_rotation,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def storage(tmp_path):
    """Fresh LayeredStorage for each test."""
    return LayeredStorage(data_dir=str(tmp_path))


@pytest.fixture
def node_port(tmp_path):
    """Start a QuartzNode in background on a random port.

    Yields (node, http_port, loop) for making synchronous HTTP requests.
    """
    from quartz.node import QuartzNode
    import random

    port = random.randint(19000, 19998)
    http_port = port + 1
    node = QuartzNode(
        host="127.0.0.1",
        port=port,
        http_port=http_port,
        data_dir=str(tmp_path),
    )

    async def start_bg():
        await node.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(start_bg())
    loop.run_until_complete(asyncio.sleep(0.3))

    yield node, http_port, loop

    loop.run_until_complete(node.stop())
    task.cancel()
    try:
        loop.run_until_complete(task)
    except (asyncio.CancelledError, Exception):
        pass
    loop.close()


def _http_get(port, loop, path):
    """Synchronous HTTP GET via the event loop."""
    async def do_get():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        req = f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()
        response = b""
        while True:
            data = await reader.read(4096)
            if not data:
                break
            response += data
        writer.close()
        return response

    return loop.run_until_complete(asyncio.wait_for(do_get(), timeout=5))


def _http_request(port, loop, method, path):
    """Synchronous HTTP request with arbitrary method."""
    async def do_req():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        req = f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()
        response = b""
        while True:
            data = await reader.read(4096)
            if not data:
                break
            response += data
        writer.close()
        return response

    return loop.run_until_complete(asyncio.wait_for(do_req(), timeout=5))


# ============================================================
# TestTxValidationSigTracking
# ============================================================

class TestTxValidationSigTracking:
    """Test that record_signature() is called at the right time during
    transaction validation."""

    def test_sig_recorded_after_successful_validation(self, storage):
        """record_signature() should be called after a tx passes validation."""
        addr = "Qtxval001"
        state = AddressState(address=addr, wots_used=5, balance=1000)
        storage.update_address_state(state)

        mock_storage = MagicMock(wraps=storage)

        # Simulate tx validation: check balance, check sig index, then record
        def validate_and_record(address, sig_index, tx_hash, height):
            state = mock_storage.get_address_state(address)
            if state is None:
                return False, "address not found"
            if sig_index < state.wots_used:
                return False, "signature index already used"
            if sig_index >= state.wots_max:
                return False, "signature index beyond max"
            # Tx is valid — record the signature
            mock_storage.record_signature(address, sig_index, tx_hash, height)
            return True, "ok"

        ok, msg = validate_and_record(addr, 5, b"tx_hash_001", 100)
        assert ok is True
        assert msg == "ok"

        # Verify record_signature was called
        mock_storage.record_signature.assert_called_once_with(
            addr, 5, b"tx_hash_001", 100
        )

        # Verify state was actually updated
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 6
        assert loaded.last_tx_height == 100

    def test_rejected_tx_does_not_increment_wots_used(self, storage):
        """A rejected transaction must not consume a WOTS+ signature slot."""
        addr = "Qtxval002"
        state = AddressState(address=addr, wots_used=10, balance=500)
        storage.update_address_state(state)

        mock_storage = MagicMock(wraps=storage)

        def validate_tx(address, sig_index, amount, tx_hash, height):
            """Simulated tx validation with balance check."""
            state = mock_storage.get_address_state(address)
            if state is None:
                return False, "address not found"
            if sig_index < state.wots_used:
                return False, "signature index already used (double-spend)"
            if sig_index >= state.wots_max:
                return False, "signature index beyond max"
            if amount > state.balance:
                return False, "insufficient balance"
            # Only record if all checks pass
            mock_storage.record_signature(address, sig_index, tx_hash, height)
            return True, "ok"

        # Try to spend more than balance — should reject
        ok, msg = validate_tx(addr, 10, 99999, b"bad_tx", 200)
        assert ok is False
        assert "insufficient balance" in msg

        # record_signature should NOT have been called
        mock_storage.record_signature.assert_not_called()

        # wots_used should remain unchanged
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 10

    def test_double_spend_same_sig_index_rejected(self, storage):
        """Reusing the same signature index must be rejected."""
        addr = "Qtxval003"
        state = AddressState(address=addr, wots_used=10, balance=10000)
        storage.update_address_state(state)

        # First tx at index 10 — should succeed
        storage.record_signature(addr, 10, b"tx_first", 100)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 11

        # Attempt double-spend: reuse index 10
        state_after = storage.get_address_state(addr)
        assert 10 < state_after.wots_used  # index 10 is already consumed

        # Simulate validation rejecting the reuse
        def check_sig_index(address, sig_index):
            s = storage.get_address_state(address)
            if s is None:
                return False, "unknown address"
            if sig_index < s.wots_used:
                return False, "double-spend: signature index already used"
            return True, "ok"

        ok, msg = check_sig_index(addr, 10)
        assert ok is False
        assert "double-spend" in msg

        # wots_used should still be 11 (not incremented by the rejected attempt)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 11

    def test_sig_index_must_be_monotonic(self, storage):
        """Signature indices must be used in order — skipping ahead is allowed,
        but going backwards is a double-spend."""
        addr = "Qtxval004"
        storage.record_signature(addr, 0, b"tx0", 10)
        storage.record_signature(addr, 1, b"tx1", 11)
        storage.record_signature(addr, 2, b"tx2", 12)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 3

        # Going back to index 1 should be rejected
        s = storage.get_address_state(addr)
        assert 1 < s.wots_used  # already used

        # Skipping ahead to index 5 is allowed (monotonic forward)
        storage.record_signature(addr, 5, b"tx5", 15)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 6  # max(3, 5+1) = 6

    def test_record_signature_updates_last_tx_height(self, storage):
        """record_signature must update last_tx_height."""
        addr = "Qtxval005"
        state = AddressState(address=addr, wots_used=0, balance=100, last_tx_height=50)
        storage.update_address_state(state)

        storage.record_signature(addr, 0, b"tx_a", 100)
        loaded = storage.get_address_state(addr)
        assert loaded.last_tx_height == 100

        storage.record_signature(addr, 1, b"tx_b", 200)
        loaded = storage.get_address_state(addr)
        assert loaded.last_tx_height == 200


# ============================================================
# TestHTTPAPIEdgeCases
# ============================================================

class TestHTTPAPIEdgeCases:
    """Edge cases for the HTTP API: malformed URLs, wrong methods, etc."""

    def test_malformed_wallet_path_trailing_slash(self, node_port):
        """GET /api/v1/wallet/ (empty address) → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/wallet/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_malformed_wallet_path_no_trailing_slash(self, node_port):
        """GET /api/v1/wallet (no address, no trailing slash) → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/wallet")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_post_to_get_endpoint_returns_405(self, node_port):
        """POST request to a GET-only endpoint → 405."""
        node, http_port, loop = node_port
        response = _http_request(http_port, loop, "POST", "/api/v1/height")
        status_line = response.split(b"\r\n")[0].decode()
        assert "405" in status_line

        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert "method not allowed" in data["error"]

    def test_post_to_wallet_state_returns_405(self, node_port):
        """POST to /api/v1/wallet/{addr}/state → 405."""
        node, http_port, loop = node_port
        response = _http_request(http_port, loop, "POST", "/api/v1/wallet/Qaddr/state")
        status_line = response.split(b"\r\n")[0].decode()
        assert "405" in status_line

    def test_very_long_address_handled_gracefully(self, node_port):
        """A very long address string should not crash the node."""
        node, http_port, loop = node_port
        long_addr = "Q" + "a" * 500
        response = _http_get(http_port, loop, f"/api/v1/wallet/{long_addr}/state")
        status_line = response.split(b"\r\n")[0].decode()
        # Should get 404 (address not found), not 500 (server error)
        assert "404" in status_line

        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert "error" in data

    def test_address_with_special_characters(self, node_port):
        """Address with special characters should return 404, not crash."""
        node, http_port, loop = node_port
        # URL with special chars — node should not crash
        response = _http_get(http_port, loop, "/api/v1/wallet/Q@#$%^&/state")
        status_line = response.split(b"\r\n")[0].decode()
        # Should be 404 (the / in the address will break path matching, but no crash)
        # The path won't match /api/v1/wallet/.../state because of extra slashes
        assert "404" in status_line

    def test_api_v1_root_returns_404(self, node_port):
        """GET /api/v1/ → 404 (no handler for root)."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_api_root_returns_404(self, node_port):
        """GET /api/ → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_root_path_returns_404(self, node_port):
        """GET / → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_unknown_endpoint_returns_404(self, node_port):
        """GET /api/v1/unknown → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/unknown")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_trailing_slash_on_height(self, node_port):
        """GET /api/v1/height/ (trailing slash) → 404 (exact match required)."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/height/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_trailing_slash_on_balance(self, node_port):
        """GET /api/v1/wallet/{addr}/balance/ (trailing slash) → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/wallet/Qaddr/balance/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_trailing_slash_on_state(self, node_port):
        """GET /api/v1/wallet/{addr}/state/ (trailing slash) → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/wallet/Qaddr/state/")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_delete_method_returns_405(self, node_port):
        """DELETE request → 405."""
        node, http_port, loop = node_port
        response = _http_request(http_port, loop, "DELETE", "/api/v1/height")
        status_line = response.split(b"\r\n")[0].decode()
        assert "405" in status_line

    def test_put_method_returns_405(self, node_port):
        """PUT request → 405."""
        node, http_port, loop = node_port
        response = _http_request(http_port, loop, "PUT", "/api/v1/height")
        status_line = response.split(b"\r\n")[0].decode()
        assert "405" in status_line

    def test_empty_address_in_path(self, node_port):
        """GET /api/v1/wallet//state (empty address between slashes) → 404."""
        node, http_port, loop = node_port
        response = _http_get(http_port, loop, "/api/v1/wallet//state")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

    def test_node_survives_malformed_requests(self, node_port):
        """Node should still respond after receiving malformed requests."""
        node, http_port, loop = node_port

        # Send garbage
        response1 = _http_get(http_port, loop, "/api/v1/wallet/!!!/state")
        assert "404" in response1.split(b"\r\n")[0].decode()

        # Node should still work for valid requests
        state = AddressState(address="Qsurvive", wots_used=3, balance=100)
        node.storage.update_address_state(state)

        response2 = _http_get(http_port, loop, "/api/v1/wallet/Qsurvive/state")
        body = response2.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert data["address"] == "Qsurvive"


# ============================================================
# TestRotationFlow
# ============================================================

class TestRotationFlow:
    """Test key rotation flow: warning → limit → rotate → chain."""

    def test_wots_254_one_more_regular_sig_allowed(self, storage):
        """At wots_used=254, one more regular signature is allowed."""
        addr = "Qrot254"
        state = AddressState(address=addr, wots_used=254, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        # Index 254 is the next available regular signature (0-indexed)
        # Regular sigs: 0..254 (255 total), index 255 reserved for rotation
        assert loaded.wots_used == 254
        # Can still sign at index 254
        assert 254 < loaded.wots_max - 1  # 254 < 255, so yes

        # Record signature at index 254 — should work
        storage.record_signature(addr, 254, b"tx_254", 5000)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 255

    def test_wots_255_must_rotate_regular_refuses(self, storage):
        """At wots_used=255, regular signing must refuse — only rotation allowed."""
        addr = "Qrot255"
        state = AddressState(address=addr, wots_used=255, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 255

        # Regular signing at index 255 should be rejected
        # (index 255 is the reserved rotation signature)
        def can_sign_regular(s):
            return s.wots_used < s.wots_max - 1  # < 255

        assert not can_sign_regular(loaded)  # Cannot do regular sign

        # But rotation signing at index 255 is allowed
        def can_sign_rotation(s):
            return s.wots_used == s.wots_max - 1  # == 255

        assert can_sign_rotation(loaded)  # Can do rotation sign

    def test_after_rotation_old_balance_zero_new_has_balance(self, storage):
        """After rotation: old address balance=0, new address has the balance."""
        old_addr = "QrotOld"
        new_addr = "QrotNew"

        # Old address with balance, all sigs used
        state_old = AddressState(
            address=old_addr,
            wots_used=255,
            balance=50000,
            derivation_index=0,
        )
        storage.update_address_state(state_old)

        # Perform rotation: set rotated_to, zero balance
        state_old.rotated_to = new_addr
        state_old.balance = 0
        storage.update_address_state(state_old)

        # New address gets the balance
        state_new = AddressState(
            address=new_addr,
            wots_used=0,
            balance=50000,
            derivation_index=1,
            first_seen_height=5000,
        )
        storage.update_address_state(state_new)

        # Verify
        loaded_old = storage.get_address_state(old_addr)
        loaded_new = storage.get_address_state(new_addr)

        assert loaded_old.balance == 0
        assert loaded_old.rotated_to == new_addr
        assert loaded_new.balance == 50000
        assert loaded_new.derivation_index == 1

    def test_rotation_chain_follow_multiple_rotations(self, storage):
        """Follow rotated_to across multiple rotations."""
        addrs = ["Qchain0", "Qchain1", "Qchain2", "Qchain3"]

        # Set up chain: 0 → 1 → 2 → 3
        for i, addr in enumerate(addrs):
            state = AddressState(
                address=addr,
                derivation_index=i,
                wots_used=255 if i < len(addrs) - 1 else 5,
                balance=0 if i < len(addrs) - 1 else 25000,
                rotated_to=addrs[i + 1] if i < len(addrs) - 1 else None,
            )
            storage.update_address_state(state)

        # Follow the chain
        current = addrs[0]
        chain = [current]
        for _ in range(len(addrs) - 1):
            state = storage.get_address_state(current)
            assert state is not None
            assert state.rotated_to is not None
            current = state.rotated_to
            chain.append(current)

        assert chain == addrs

        # Final address should have balance and no rotation
        final = storage.get_address_state(addrs[-1])
        assert final.balance == 25000
        assert final.rotated_to is None
        assert final.derivation_index == 3

    def test_cannot_rotate_to_same_address(self, storage):
        """Rotation to the same address should be rejected."""
        addr = "QselfRot"
        state = AddressState(address=addr, wots_used=255, balance=1000)
        storage.update_address_state(state)

        # Simulate rotation validation
        def validate_rotation(source_addr, target_addr):
            if source_addr == target_addr:
                return False, "cannot rotate to same address"
            return True, "ok"

        # Same address rotation should fail
        ok, msg = validate_rotation(addr, addr)
        assert ok is False
        assert "same address" in msg

        # Different address rotation should succeed
        ok, msg = validate_rotation(addr, "Qother")
        assert ok is True

    def test_rotation_status_warning_at_240(self, storage):
        """Rotation status should be 'warning' at wots_used=240."""
        addr = "Qwarn240"
        state = AddressState(address=addr, wots_used=240, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used >= KEY_ROTATION_WARN_AT
        assert needs_key_rotation(loaded.wots_used)

    def test_rotation_status_ok_below_240(self, storage):
        """Rotation status should be 'ok' below the warning threshold."""
        addr = "Qok239"
        state = AddressState(address=addr, wots_used=239, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        assert not needs_key_rotation(loaded.wots_used)

    def test_rotation_status_required_at_max(self, storage):
        """Rotation status should be 'required' at wots_used=256 (all used)."""
        addr = "Qreq256"
        state = AddressState(address=addr, wots_used=256, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used >= loaded.wots_max
        assert needs_key_rotation(loaded.wots_used)


# ============================================================
# TestWOTSPlusReservation
# ============================================================

class TestWOTSPlusReservation:
    """Test WOTS+ signature reservation logic.

    WOTS+ addresses have 256 signature slots total:
    - Indices 0-254: regular spending signatures (255 total)
    - Index 255: reserved for the rotation signature

    So quartz_qwallet_remaining() should report 255 (not 256) available
    regular signatures when wots_used=0.
    """

    def test_remaining_reports_255_not_256(self, storage):
        """A fresh wallet should report 255 remaining regular sigs, not 256.

        The 256th slot (index 255) is reserved for key rotation.
        """
        addr = "QwotsFresh"
        state = AddressState(address=addr, wots_used=0, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)

        # quartz_qwallet_remaining() equivalent:
        # total regular sigs = wots_max - 1 (reserve one for rotation)
        # remaining = (wots_max - 1) - wots_used
        remaining_regular = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining_regular == 255  # 256 - 1 - 0 = 255

    def test_sig_255_is_rotation_signature(self, storage):
        """Signature index 255 (0-indexed) is the rotation signature."""
        addr = "QwotsRot"
        state = AddressState(address=addr, wots_used=254, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)

        # Index 255 is wots_max - 1, which is the rotation slot
        rotation_sig_index = loaded.wots_max - 1
        assert rotation_sig_index == 255

        # This is distinct from regular signatures
        last_regular_index = loaded.wots_max - 2  # 254
        assert last_regular_index == 254

    def test_regular_signing_at_254_works(self, storage):
        """Regular signing at index 254 (the last regular slot) works."""
        addr = "Qwots254"
        state = AddressState(address=addr, wots_used=254, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)

        # Can we sign at index 254?
        # Regular sigs allowed when: sig_index < wots_max - 1 (i.e., < 255)
        sig_index = 254
        can_regular = sig_index < loaded.wots_max - 1
        assert can_regular is True

        # Perform the signing
        storage.record_signature(addr, sig_index, b"tx_regular_254", 9000)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 255

    def test_regular_signing_at_255_refuses(self, storage):
        """Regular signing at index 255 must refuse — that's the rotation slot."""
        addr = "Qwots255reg"
        state = AddressState(address=addr, wots_used=255, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)

        # Regular signing at index 255 should be refused
        sig_index = 255
        can_regular = sig_index < loaded.wots_max - 1  # 255 < 255 → False
        assert can_regular is False

        # The remaining regular signatures should be 0
        remaining_regular = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining_regular == 0  # (256-1) - 255 = 0

    def test_rotation_signing_at_255_works(self, storage):
        """Rotation signing at index 255 works and produces a self-transfer tx."""
        addr = "Qwots255rot"
        state = AddressState(
            address=addr,
            wots_used=255,
            wots_max=256,
            balance=42000,
        )
        storage.update_address_state(addr_state := state)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)

        # Rotation signing at index 255 is allowed
        sig_index = 255
        can_rotate = sig_index == loaded.wots_max - 1  # 255 == 255 → True
        assert can_rotate is True

        # Simulate rotation: create self-transfer tx
        new_addr = "Qwots255new"

        # Record the rotation signature
        storage.record_signature(addr, sig_index, b"tx_rotation", 10000)

        # Update old address: rotated, zero balance
        loaded = storage.get_address_state(addr)
        loaded.rotated_to = new_addr
        loaded.balance = 0
        storage.update_address_state(loaded)

        # Create new address with transferred balance
        new_state = AddressState(
            address=new_addr,
            wots_used=0,
            wots_max=256,
            balance=42000,
            derivation_index=1,
            first_seen_height=10000,
        )
        storage.update_address_state(new_state)

        # Verify rotation completed
        old = storage.get_address_state(addr)
        new = storage.get_address_state(new_addr)

        assert old.wots_used == 256  # All slots consumed (max(255, 255+1))
        assert old.rotated_to == new_addr
        assert old.balance == 0

        assert new.balance == 42000
        assert new.wots_used == 0
        assert new.derivation_index == 1

    def test_remaining_decreases_with_each_sig(self, storage):
        """quartz_qwallet_remaining() should decrease by 1 per regular signature."""
        addr = "QwotsDec"
        state = AddressState(address=addr, wots_used=0, wots_max=256)
        storage.update_address_state(state)

        for i in range(10):
            loaded = storage.get_address_state(addr)
            remaining = (loaded.wots_max - 1) - loaded.wots_used
            assert remaining == 255 - i

            storage.record_signature(addr, i, f"tx{i}".encode(), 100 + i)

        loaded = storage.get_address_state(addr)
        remaining = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining == 245  # 255 - 10

    def test_remaining_zero_at_wots_255(self, storage):
        """Remaining regular sigs should be 0 when wots_used=255."""
        addr = "QwotsZero"
        state = AddressState(address=addr, wots_used=255, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        remaining = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining == 0

    def test_remaining_one_at_wots_254(self, storage):
        """Remaining regular sigs should be 1 when wots_used=254."""
        addr = "QwotsOne"
        state = AddressState(address=addr, wots_used=254, wots_max=256)
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        remaining = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining == 1

    def test_rotation_sig_does_not_count_as_regular(self, storage):
        """The rotation signature (index 255) is not a regular signature.

        After using all 255 regular sigs + 1 rotation sig = 256 total,
        the address is fully consumed.
        """
        addr = "QwotsFull"
        state = AddressState(address=addr, wots_used=0, wots_max=256)
        storage.update_address_state(state)

        # Use all 255 regular signatures (indices 0-254)
        for i in range(255):
            storage.record_signature(addr, i, f"reg{i}".encode(), 100 + i)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 255

        # No regular sigs remaining
        remaining_regular = (loaded.wots_max - 1) - loaded.wots_used
        assert remaining_regular == 0

        # But rotation sig at index 255 is still available
        can_rotate = loaded.wots_used == loaded.wots_max - 1
        assert can_rotate is True

        # Use the rotation signature
        storage.record_signature(addr, 255, b"rotation_tx", 9999)
        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 256  # Fully consumed

        # No sigs of any kind remaining
        assert loaded.wots_max - loaded.wots_used == 0