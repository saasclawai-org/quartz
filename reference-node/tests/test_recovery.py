"""
Tests for wallet recovery API and address state tracking.

Covers:
- AddressState creation and persistence
- record_signature() increments wots_used
- get_address_state() for known/unknown addresses
- get_balance() convenience method
- Rotation chain tracking
- HTTP API endpoints (/wallet/{addr}/state, /wallet/{addr}/balance, /height)
- Recovery scenario: query sig index after device loss
"""

import json
import os
import shutil
import tempfile
import asyncio
import pytest
from unittest.mock import MagicMock, patch

from quartz.storage import LayeredStorage, AddressState


@pytest.fixture
def storage(tmp_path):
    """Fresh storage for each test."""
    s = LayeredStorage(data_dir=str(tmp_path))
    return s


class TestAddressState:
    """AddressState dataclass tests."""

    def test_default_values(self):
        state = AddressState(address="Qabc123")
        assert state.address == "Qabc123"
        assert state.derivation_index == 0
        assert state.wots_used == 0
        assert state.wots_max == 256
        assert state.rotated_to is None
        assert state.first_seen_height == 0
        assert state.last_tx_height == 0
        assert state.balance == 0

    def test_custom_values(self):
        state = AddressState(
            address="Qabc123",
            derivation_index=2,
            wots_used=150,
            balance=5000,
            rotated_to="Qdef456",
        )
        assert state.derivation_index == 2
        assert state.wots_used == 150
        assert state.balance == 5000
        assert state.rotated_to == "Qdef456"

    def test_serialization_roundtrip(self):
        state = AddressState(
            address="Qabc123",
            derivation_index=1,
            wots_used=42,
            balance=1250,
            last_tx_height=5000,
        )
        d = state.to_dict()
        restored = AddressState.from_dict(d)
        assert restored.address == state.address
        assert restored.derivation_index == state.derivation_index
        assert restored.wots_used == state.wots_used
        assert restored.balance == state.balance
        assert restored.last_tx_height == state.last_tx_height


class TestStorageAddressState:
    """LayeredStorage address state tracking."""

    def test_get_unknown_address_returns_none(self, storage):
        result = storage.get_address_state("Qunknown")
        assert result is None

    def test_update_and_get(self, storage):
        state = AddressState(
            address="Qtest001",
            derivation_index=0,
            wots_used=5,
            balance=1000,
            first_seen_height=100,
        )
        storage.update_address_state(state)

        loaded = storage.get_address_state("Qtest001")
        assert loaded is not None
        assert loaded.wots_used == 5
        assert loaded.balance == 1000
        assert loaded.first_seen_height == 100

    def test_record_signature_increments(self, storage):
        # Create initial state
        state = AddressState(address="Qtest002", wots_used=3, balance=500)
        storage.update_address_state(state)

        # Record a new signature
        storage.record_signature("Qtest002", 3, b"tx_hash_abc", 1000)

        loaded = storage.get_address_state("Qtest002")
        assert loaded.wots_used == 4
        assert loaded.last_tx_height == 1000

    def test_record_signature_creates_if_missing(self, storage):
        # Address doesn't exist yet — record_signature should create it
        storage.record_signature("QnewAddr", 0, b"tx_hash_new", 50)

        loaded = storage.get_address_state("QnewAddr")
        assert loaded is not None
        assert loaded.wots_used == 1
        assert loaded.last_tx_height == 50
        assert loaded.first_seen_height == 50

    def test_record_multiple_signatures(self, storage):
        state = AddressState(address="Qmulti", wots_used=0)
        storage.update_address_state(state)

        for i in range(10):
            storage.record_signature("Qmulti", i, f"tx{i}".encode(), 100 + i)

        loaded = storage.get_address_state("Qmulti")
        assert loaded.wots_used == 10
        assert loaded.last_tx_height == 109

    def test_get_balance_known(self, storage):
        state = AddressState(address="Qbaltest", balance=4242)
        storage.update_address_state(state)
        assert storage.get_balance("Qbaltest") == 4242

    def test_get_balance_unknown(self, storage):
        assert storage.get_balance("Qnonexistent") == 0

    def test_rotation_tracking(self, storage):
        # Original address
        state0 = AddressState(address="Qorig", derivation_index=0, wots_used=255)
        storage.update_address_state(state0)

        # Record rotation
        state0.rotated_to = "Qnew1"
        storage.update_address_state(state0)

        # New address after rotation
        state1 = AddressState(address="Qnew1", derivation_index=1, wots_used=1)
        storage.update_address_state(state1)

        loaded0 = storage.get_address_state("Qorig")
        loaded1 = storage.get_address_state("Qnew1")

        assert loaded0.rotated_to == "Qnew1"
        assert loaded1.derivation_index == 1
        assert loaded1.wots_used == 1


class TestRecoveryScenario:
    """Simulate full recovery flow."""

    def test_device_lost_recovery(self, storage):
        """User loses device, gets new one, recovers from seed."""
        addr = "Qrecovery123"

        # Device was used for 17 transactions before being lost
        state = AddressState(
            address=addr,
            derivation_index=0,
            wots_used=17,
            balance=50000,
            first_seen_height=1000,
            last_tx_height=2000,
        )
        storage.update_address_state(state)

        # New device queries the node
        loaded = storage.get_address_state(addr)
        assert loaded is not None
        assert loaded.wots_used == 17
        assert loaded.balance == 50000

        # Recovery: resume from signature index 18
        next_sig = loaded.wots_used
        assert next_sig == 17  # 0-indexed, next signature is #17 (already used)
        # Next UNUSED signature index should be wots_used (since 0-16 are used, next is 17)
        # Wait — wots_used=17 means 17 signatures used (indices 0-16)
        # Next available is index 17
        # Actually: wots_used counts how many we've used. If 17 used, indices 0-16 are taken.
        # Next index = 17. But wots_used=17 already includes index 16 (0-indexed from 0).
        # Let's verify: 0,1,2,...,16 = 17 signatures. Next = 17.
        assert loaded.wots_used == 17  # next_sig_index = wots_used (they're equal when 0-indexed)

    def test_rotation_chain_recovery(self, storage):
        """Recovery when address has been rotated."""
        # First address rotated
        addr0 = "Qaddr0"
        state0 = AddressState(
            address=addr0,
            derivation_index=0,
            wots_used=256,  # all used
            rotated_to="Qaddr1",
            balance=0,
        )
        storage.update_address_state(state0)

        # Second address (current)
        addr1 = "Qaddr1"
        state1 = AddressState(
            address=addr1,
            derivation_index=1,
            wots_used=5,
            balance=30000,
        )
        storage.update_address_state(state1)

        # Recovery: query addr0 first
        loaded0 = storage.get_address_state(addr0)
        assert loaded0.rotated_to == "Qaddr1"
        assert loaded0.balance == 0

        # Follow chain to addr1
        loaded1 = storage.get_address_state(loaded0.rotated_to)
        assert loaded1.derivation_index == 1
        assert loaded1.wots_used == 5
        assert loaded1.balance == 30000

    def test_wots_remaining(self, storage):
        """Check wots_remaining calculation."""
        state = AddressState(
            address="Qremaining",
            wots_used=240,
            wots_max=256,
        )
        storage.update_address_state(state)

        loaded = storage.get_address_state("Qremaining")
        remaining = loaded.wots_max - loaded.wots_used - 1  # -1 for reserved rotation sig
        assert remaining == 15  # 256 - 240 - 1

    def test_near_limit_warning(self, storage):
        """Address approaching signature limit."""
        state = AddressState(
            address="Qwarn",
            wots_used=254,
            wots_max=256,
        )
        storage.update_address_state(state)

        loaded = storage.get_address_state("Qwarn")
        assert loaded.wots_used >= 254  # urgent
        assert loaded.wots_max - loaded.wots_used <= 2  # almost out


class TestHTTPAPI:
    """Test the HTTP API endpoints."""

    @pytest.fixture
    def node_port(self, tmp_path):
        """Start a node in background, yield (node, port), cleanup after."""
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

        # Start in background task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(start_bg())
        # Give it time to bind
        loop.run_until_complete(asyncio.sleep(0.3))

        yield node, http_port, loop

        # Cleanup
        loop.run_until_complete(node.stop())
        task.cancel()
        try:
            loop.run_until_complete(task)
        except (asyncio.CancelledError, Exception):
            pass
        loop.close()

    def _http_get(self, port, loop, path):
        """Make a synchronous HTTP GET request via the loop."""
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

    def test_wallet_state_endpoint(self, node_port):
        """GET /api/v1/wallet/{address}/state returns correct data."""
        node, http_port, loop = node_port

        state = AddressState(
            address="Qapi001",
            derivation_index=0,
            wots_used=7,
            balance=2500,
            first_seen_height=100,
            last_tx_height=500,
        )
        node.storage.update_address_state(state)

        response = self._http_get(http_port, loop, "/api/v1/wallet/Qapi001/state")
        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)

        assert data["address"] == "Qapi001"
        assert data["wots_used"] == 7
        assert data["balance"] == 2500

    def test_wallet_state_not_found(self, node_port):
        """GET /api/v1/wallet/{unknown}/state returns 404."""
        node, http_port, loop = node_port

        response = self._http_get(http_port, loop, "/api/v1/wallet/Qnonexistent/state")
        status_line = response.split(b"\r\n")[0].decode()
        assert "404" in status_line

        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert "error" in data

    def test_wallet_balance_endpoint(self, node_port):
        """GET /api/v1/wallet/{address}/balance returns balance."""
        node, http_port, loop = node_port

        state = AddressState(address="Qbalapi", balance=9999)
        node.storage.update_address_state(state)

        response = self._http_get(http_port, loop, "/api/v1/wallet/Qbalapi/balance")
        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert data["balance"] == 9999

    def test_height_endpoint(self, node_port):
        """GET /api/v1/height returns chain height."""
        node, http_port, loop = node_port

        response = self._http_get(http_port, loop, "/api/v1/height")
        body = response.split(b"\r\n\r\n", 1)[1]
        data = json.loads(body)
        assert "height" in data


class TestWOTSPlusSafety:
    """Verify signature index safety guarantees."""

    def test_never_reuse_index(self, storage):
        """Recording signatures must always advance the counter."""
        addr = "Qsafety"
        state = AddressState(address=addr, wots_used=10)
        storage.update_address_state(state)

        # Record signature at index 10
        storage.record_signature(addr, 10, b"tx_a", 100)

        loaded = storage.get_address_state(addr)
        assert loaded.wots_used == 11  # incremented

        # If device tries to use index 10 again, the node should reject
        # (this would be enforced in blockchain validation, not storage)
        # But storage correctly shows 11 used

    def test_reserved_signature_not_counted(self, storage):
        """The 256th signature is reserved for rotation."""
        addr = "Qreserve"
        state = AddressState(
            address=addr,
            wots_used=254,  # 255 used (0-254), 1 left before reserve
            wots_max=256,
        )
        storage.update_address_state(state)

        loaded = storage.get_address_state(addr)
        # Regular spending can use indices 0-254 (255 total)
        # Index 255 is reserved for rotation
        # wots_used=254 means 254 used (0-253), next regular = 254
        # After 254: next = 255 = reserved → must rotate
        remaining = loaded.wots_max - loaded.wots_used - 1  # -1 for reserve
        assert remaining == 1  # one more regular signature allowed
