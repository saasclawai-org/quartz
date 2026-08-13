"""
Quartz P2P node — handles block relay, chain sync, and peer management.

For the Python reference implementation. ESP32 nodes connect to this
node (and each other) for chain synchronization.

Also serves a lightweight HTTP API for wallet state queries
(key rotation tracking, balance lookup, chain height).
"""

import asyncio
import json
import logging
import struct
import time
from typing import Dict, List, Optional, Set
from pathlib import Path

from .blockchain import (
    Block, BlockHeader, Transaction, Block as BlockType,
    compute_merkle_root, get_block_reward,
    RETARGET_PERIOD, DIFFICULTY_BITS, BLOCK_TIME,
    retarget_difficulty_bits,
)
from .crystal_hash import check_difficulty
from .storage import LayeredStorage, StorageMode, AddressState

logger = logging.getLogger("quartz.node")

# Protocol version
PROTO_VERSION = 1

# Message types
MSG_VERSION = 1
MSG_VERACK = 2
MSG_GET_BLOCKS = 3
MSG_BLOCKS = 4
MSG_GET_MEMPOOL = 5
MSG_MEMPOOL = 6
MSG_SUBMIT_BLOCK = 7
MSG_PING = 8
MSG_PONG = 9
MSG_TX = 10

DEFAULT_PORT = 8420  # QZ = 0x51 5A → close enough
HTTP_PORT = 8421     # One above P2P port
MAX_PEERS = 32


class QuartzNode:
    """Quartz P2P node.

    Maintains the blockchain state, handles incoming peer connections,
    and broadcasts new blocks/transactions.

    Also runs an HTTP API server for wallet state queries.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 http_port: int = HTTP_PORT, data_dir: str = "quartz-data"):
        self.host = host
        self.port = port
        self.http_port = http_port
        self.chain: List[Block] = []
        self.mempool: Dict[bytes, Transaction] = {}
        self.peers: Set[tuple] = set()  # (host, port)
        self.running = False
        self.server: Optional[asyncio.Server] = None
        self.http_server: Optional[asyncio.Server] = None

        # Persistent storage for address state, headers, blocks
        self.storage = LayeredStorage(data_dir, mode=StorageMode.FULL)

        # Initialize with genesis block
        genesis = Block.create_genesis()
        self.chain.append(genesis)

    @property
    def height(self) -> int:
        return len(self.chain) - 1

    @property
    def tip(self) -> BlockHeader:
        return self.chain[-1].header

    def get_block(self, height: int) -> Optional[Block]:
        if 0 <= height < len(self.chain):
            return self.chain[height]
        return None

    def get_block_by_hash(self, block_hash: bytes) -> Optional[Block]:
        for block in reversed(self.chain):
            if block.header.hash == block_hash:
                return block
        return None

    def add_peer(self, host: str, port: int = DEFAULT_PORT):
        """Add a peer to the peer list."""
        self.peers.add((host, port))
        logger.info(f"Peer added: {host}:{port} ({len(self.peers)} total)")

    async def start(self):
        """Start the P2P server and HTTP API server."""
        self.running = True

        # Start P2P server
        self.server = await asyncio.start_server(
            self._handle_peer, self.host, self.port
        )
        logger.info(f"Quartz P2P listening on {self.host}:{self.port}")

        # Start HTTP API server
        self.http_server = await asyncio.start_server(
            self._handle_http, self.host, self.http_port
        )
        logger.info(f"Quartz HTTP API listening on {self.host}:{self.http_port}")

        logger.info(f"Chain height: {self.height}")

        async with self.server:
            async with self.http_server:
                await asyncio.gather(
                    self.server.serve_forever(),
                    self.http_server.serve_forever(),
                )

    async def stop(self):
        """Stop the node."""
        self.running = False
        if self.http_server:
            self.http_server.close()
            await self.http_server.wait_closed()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_peer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an incoming peer connection."""
        peer_addr = writer.get_extra_info('peername')
        logger.info(f"Peer connected: {peer_addr}")

        try:
            while self.running:
                data = await reader.read(4096)
                if not data:
                    break
                await self._process_message(data, writer)
        except asyncio.ConnectionError:
            pass
        finally:
            writer.close()
            logger.info(f"Peer disconnected: {peer_addr}")

    async def _process_message(self, data: bytes, writer: asyncio.StreamWriter):
        """Process incoming protocol message."""
        if len(data) < 4:
            return

        msg_type = struct.unpack('<I', data[:4])[0]
        payload = data[4:]

        if msg_type == MSG_VERSION:
            # Peer announces their version/height
            peer_height = struct.unpack('<I', payload[:4])[0] if len(payload) >= 4 else 0
            await self._send(writer, MSG_VERACK, struct.pack('<I', self.height))

            # If peer is ahead, request their blocks
            if peer_height > self.height:
                await self._send(writer, MSG_GET_BLOCKS,
                                 self.tip.hash + struct.pack('<I', 10))

        elif msg_type == MSG_GET_BLOCKS:
            # Peer wants blocks — send from requested hash
            await self._send_blocks(writer, payload)

        elif msg_type == MSG_SUBMIT_BLOCK:
            # Peer found a new block
            await self._handle_new_block(payload)

        elif msg_type == MSG_TX:
            # Peer has a transaction
            await self._handle_tx(payload)

        elif msg_type == MSG_PING:
            await self._send(writer, MSG_PONG, b'pong')

    async def _send(self, writer: asyncio.StreamWriter, msg_type: int, payload: bytes):
        """Send a protocol message."""
        data = struct.pack('<I', msg_type) + payload
        writer.write(data)
        await writer.drain()

    async def _send_blocks(self, writer, payload):
        """Send requested blocks to peer."""
        # Simplified — send last N blocks
        blocks_data = struct.pack('<I', min(10, len(self.chain)))
        for block in self.chain[-10:]:
            block_bytes = block.serialize()
            blocks_data += struct.pack('<H', len(block_bytes)) + block_bytes
        await self._send(writer, MSG_BLOCKS, blocks_data)

    async def _handle_new_block(self, payload: bytes):
        """Validate and add a new block from a peer."""
        try:
            block = Block.deserialize(payload)
            logger.info(f"Received block at height {self.height + 1}")

            # Verify: previous hash matches our tip
            if block.header.prev_block_hash != self.tip.hash:
                logger.warning("Block prev_hash doesn't match tip — orphan")
                return

            # Verify: difficulty
            if not check_difficulty(block.header.hash, block.header.difficulty_target):
                logger.warning("Block doesn't meet difficulty target")
                return

            # Verify: merkle root
            if block.transactions:
                tx_hashes = [tx.txid for tx in block.transactions]
                if block.header.merkle_root != compute_merkle_root(tx_hashes):
                    logger.warning("Block merkle root mismatch")
                    return

            # Accept
            self.chain.append(block)
            logger.info(f"✅ Block accepted! Height: {self.height}")

            # Difficulty retarget every RETARGET_PERIOD blocks
            if self.height > 0 and self.height % RETARGET_PERIOD == 0:
                current_bits = block.header.difficulty_target
                new_bits = retarget_difficulty_bits(
                    current_bits, self.chain, BLOCK_TIME)
                if new_bits != current_bits:
                    logger.info(f"📐 Difficulty retarget at block {self.height}: "
                                f"{current_bits} → {new_bits} bits")
                    # Update expected difficulty for next block template
                    self._current_difficulty = new_bits

            # Remove confirmed txs from mempool
            for tx in block.transactions:
                self.mempool.pop(tx.txid, None)

            # Broadcast to peers
            await self._broadcast(MSG_SUBMIT_BLOCK, payload)

        except Exception as e:
            logger.error(f"Failed to process new block: {e}")

    async def _handle_tx(self, payload: bytes):
        """Add transaction to mempool."""
        # Simplified — real impl would validate signatures
        logger.info(f"Transaction received, added to mempool ({len(self.mempool)} pending)")

    # ============ HTTP API Server ============

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an incoming HTTP request using bare asyncio streams."""
        try:
            # Read request line
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return
            try:
                parts = request_line.decode('ascii').strip().split()
                method, path, _proto = parts
            except (ValueError, UnicodeDecodeError):
                await self._http_respond(writer, 400, {"error": "bad request"})
                return

            # Read and discard headers
            while True:
                header_line = await reader.readline()
                if header_line in (b'\r\n', b'\n', b''):
                    break

            if method != 'GET':
                await self._http_respond(writer, 405, {"error": "method not allowed"})
                return

            # Route the request
            await self._route_http(path, writer)

        except asyncio.ConnectionError:
            pass
        except Exception as e:
            logger.error(f"HTTP handler error: {e}")
            try:
                await self._http_respond(writer, 500, {"error": "internal server error"})
            except Exception:
                pass
        finally:
            writer.close()

    async def _route_http(self, path: str, writer: asyncio.StreamWriter):
        """Route HTTP GET request to the appropriate handler."""

        # GET /api/v1/wallet/{address}/state
        if path.startswith('/api/v1/wallet/') and path.endswith('/state'):
            address = path[len('/api/v1/wallet/'):-len('/state')]
            await self._api_wallet_state(address, writer)
            return

        # GET /api/v1/wallet/{address}/balance
        if path.startswith('/api/v1/wallet/') and path.endswith('/balance'):
            address = path[len('/api/v1/wallet/'):-len('/balance')]
            await self._api_wallet_balance(address, writer)
            return

        # GET /api/v1/height
        if path == '/api/v1/height':
            await self._api_height(writer)
            return

        # Unknown route
        await self._http_respond(writer, 404, {"error": "not found"})

    async def _api_wallet_state(self, address: str, writer: asyncio.StreamWriter):
        """GET /api/v1/wallet/{address}/state — full wallet state for recovery."""
        state = self.storage.get_address_state(address)
        if state is None:
            await self._http_respond(writer, 404, {
                "error": "address not found",
                "wots_used": 0,
            })
            return

        wots_remaining = state.wots_max - state.wots_used

        # Determine rotation status
        if state.rotated_to:
            rotation_status = "rotated"
        elif state.wots_used >= state.wots_max:
            rotation_status = "required"
        elif state.wots_used >= 240:  # KEY_ROTATION_WARN_AT
            rotation_status = "warning"
        else:
            rotation_status = "ok"

        await self._http_respond(writer, 200, {
            "address": address,
            "derivation_index": state.derivation_index,
            "balance": state.balance,
            "wots_used": state.wots_used,
            "wots_remaining": wots_remaining,
            "rotation_status": rotation_status,
            "last_sig_index": state.wots_used,
            "rotated_to": state.rotated_to,
            "first_seen": state.first_seen_height,
            "last_active": state.last_tx_height,
        })

    async def _api_wallet_balance(self, address: str, writer: asyncio.StreamWriter):
        """GET /api/v1/wallet/{address}/balance — simple balance lookup."""
        balance = self.storage.get_balance(address)
        await self._http_respond(writer, 200, {"balance": balance})

    async def _api_height(self, writer: asyncio.StreamWriter):
        """GET /api/v1/height — current chain tip info."""
        tip = self.tip
        await self._http_respond(writer, 200, {
            "height": self.height,
            "hash": tip.hash.hex(),
        })

    async def _http_respond(self, writer: asyncio.StreamWriter,
                            status: int, body: dict):
        """Send a JSON HTTP response."""
        status_text = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
        }.get(status, "OK")

        json_body = json.dumps(body).encode('utf-8')
        headers = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(json_body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode('ascii')

        writer.write(headers + json_body)
        await writer.drain()

    async def _broadcast(self, msg_type: int, payload: bytes):
        """Broadcast to all connected peers (placeholder)."""
        pass


def main():
    """Run a standalone Quartz node."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
    )

    parser = argparse.ArgumentParser(description='Quartz reference node')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--http-port', type=int, default=HTTP_PORT,
                        help='HTTP API port (default: 8421)')
    parser.add_argument('--data-dir', default='quartz-data',
                        help='Data directory for storage (default: quartz-data)')
    parser.add_argument('--peer', action='append', help='Connect to peer (host:port)')
    args = parser.parse_args()

    node = QuartzNode(
        host=args.host,
        port=args.port,
        http_port=args.http_port,
        data_dir=args.data_dir,
    )

    if args.peer:
        for p in args.peer:
            if ':' in p:
                host, port = p.rsplit(':', 1)
                node.add_peer(host, int(port))
            else:
                node.add_peer(p)

    logger = logging.getLogger("quartz")
    logger.info("╔══════════════════════════════════╗")
    logger.info("║     Quartz (QZ) Reference Node   ║")
    logger.info("╚══════════════════════════════════╝")
    logger.info(f"Genesis hash: {node.tip.hash.hex()[:16]}...")
    logger.info(f"Supply cap: 42,000,000 QZ")
    logger.info(f"P2P: {args.host}:{args.port}  HTTP API: {args.host}:{args.http_port}")

    asyncio.run(node.start())


if __name__ == '__main__':
    main()
