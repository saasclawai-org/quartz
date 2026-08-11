"""
Quartz Gateway P2P — TCP Gossip Protocol Implementation

Gateway nodes form a TCP overlay network that relays blocks and
transactions. ESP32 miners connect to gateways via WiFi HTTP API
or LoRa mesh bridges.

This module runs in the reference node process.

Protocol:
    - TCP with length-prefixed JSON messages (4-byte BE length + JSON)
    - Handshake: HELLO → HELLO_ACK
    - Periodic: PING/PONG every 30s
    - Gossip: NEW_BLOCK, NEW_TX broadcast to all connected peers
    - Sync: GET_HEADERS → HEADERS (batch up to 500)
    - Discovery: GET_PEERS → PEERS

License: MIT
"""

import asyncio
import json
import logging
import struct
import time
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger("quartz.p2p")

PROTOCOL_VERSION = 1
DEFAULT_PORT = 21100
MAX_PEERS = 32
MAX_MSG_SIZE = 4 * 1024 * 1024
PING_INTERVAL = 30  # seconds
PEER_TIMEOUT = 120  # seconds


@dataclass
class Peer:
    """A connected P2P peer."""
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    addr: tuple  # (ip, port)
    version: int = 0
    chain_height: int = 0
    best_hash: str = ""
    capabilities: list = field(default_factory=list)
    user_agent: str = ""
    last_ping: float = field(default_factory=time.time)
    connected_at: float = field(default_factory=time.time)
    bytes_sent: int = 0
    bytes_recv: int = 0
    blocks_sent: int = 0
    blocks_recv: int = 0


class QuartzP2P:
    """Quartz gateway P2P node."""

    def __init__(self, blockchain, host="0.0.0.0", port=DEFAULT_PORT):
        self.bc = blockchain
        self.host = host
        self.port = port
        self.peers: dict[str, Peer] = {}  # key = "ip:port"
        self.server: Optional[asyncio.Server] = None
        self.bootnodes = [
            # Add known seed nodes here
            # ("seed1.quartz.network", DEFAULT_PORT),
        ]
        self.running = False
        self._outbound_tasks = []

    # ============================================================
    # Message I/O
    # ============================================================

    async def _send_msg(self, peer: Peer, msg: dict):
        """Send a length-prefixed JSON message."""
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_MSG_SIZE:
            logger.warning(f"Message too large ({len(data)} bytes), not sending")
            return
        length_prefix = struct.pack(">I", len(data))
        try:
            peer.writer.write(length_prefix + data)
            await peer.writer.drain()
            peer.bytes_sent += len(data) + 4
        except (ConnectionError, OSError) as e:
            logger.warning(f"Send to {peer.addr} failed: {e}")
            await self._disconnect(peer)

    async def _recv_msg(self, peer: Peer) -> Optional[dict]:
        """Receive a length-prefixed JSON message."""
        try:
            length_data = await peer.reader.readexactly(4)
        except asyncio.IncompleteReadError:
            return None

        length = struct.unpack(">I", length_data)[0]
        if length > MAX_MSG_SIZE:
            logger.warning(f"Message too large from {peer.addr}: {length} bytes")
            return None

        try:
            data = await peer.reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return None

        peer.bytes_recv += length + 4
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON from {peer.addr}: {e}")
            return None

    # ============================================================
    # Connection Handling
    # ============================================================

    async def _handle_inbound(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle an inbound TCP connection."""
        addr = writer.get_extra_info("peername")
        peer_key = f"{addr[0]}:{addr[1]}"
        logger.info(f"Inbound connection from {peer_key}")

        peer = Peer(reader=reader, writer=writer, addr=addr)

        # Wait for HELLO
        msg = await self._recv_msg(peer)
        if not msg or msg.get("type") != "hello":
            logger.warning(f"No HELLO from {peer_key}, disconnecting")
            writer.close()
            return

        # Process handshake
        peer.version = msg.get("version", 0)
        peer.chain_height = msg.get("chain_height", 0)
        peer.best_hash = msg.get("best_hash", "")
        peer.capabilities = msg.get("capabilities", [])
        peer.user_agent = msg.get("user_agent", "unknown")

        if peer.version != PROTOCOL_VERSION:
            logger.warning(f"Protocol mismatch from {peer_key}: v{peer.version}")
            await self._send_msg(peer, {"type": "bye", "reason": "protocol_mismatch"})
            writer.close()
            return

        if len(self.peers) >= MAX_PEERS:
            await self._send_msg(peer, {"type": "bye", "reason": "too_many_peers"})
            writer.close()
            return

        # Send HELLO_ACK
        await self._send_msg(peer, {
            "type": "hello_ack",
            "version": PROTOCOL_VERSION,
            "chain_height": self.bc.height,
            "best_hash": self.bc.best_hash.hex() if hasattr(self.bc.best_hash, 'hex') else str(self.bc.best_hash),
            "capabilities": ["full_node", "miner_api"],
            "user_agent": "quartz-node/0.1.0",
        })

        self.peers[peer_key] = peer
        logger.info(f"Peer connected: {peer_key} (v{peer.version}, height={peer.chain_height}, ua={peer.user_agent})")

        # Check if we need to sync from them
        if peer.chain_height > self.bc.height:
            logger.info(f"Peer {peer_key} is ahead ({peer.chain_height} > {self.bc.height}), requesting headers")
            await self._request_headers(peer, self.bc.height + 1)

        # Main message loop
        try:
            while self.running:
                msg = await self._recv_msg(peer)
                if msg is None:
                    break
                await self._handle_msg(peer, msg)
        except Exception as e:
            logger.warning(f"Connection error with {peer_key}: {e}")
        finally:
            await self._disconnect(peer)

    async def _disconnect(self, peer: Peer):
        """Gracefully disconnect a peer."""
        peer_key = f"{peer.addr[0]}:{peer.addr[1]}"
        if peer_key in self.peers:
            del self.peers[peer_key]
        try:
            peer.writer.close()
            await peer.writer.wait_closed()
        except Exception:
            pass
        logger.info(f"Peer disconnected: {peer_key}")

    # ============================================================
    # Message Handling
    # ============================================================

    async def _handle_msg(self, peer: Peer, msg: dict):
        """Dispatch incoming message by type."""
        mtype = msg.get("type")

        if mtype == "ping":
            await self._send_msg(peer, {"type": "pong", "timestamp": int(time.time())})
            peer.last_ping = time.time()

        elif mtype == "pong":
            peer.last_ping = time.time()

        elif mtype == "new_block":
            await self._handle_new_block(peer, msg)

        elif mtype == "new_tx":
            await self._handle_new_tx(peer, msg)

        elif mtype == "get_headers":
            await self._handle_get_headers(peer, msg)

        elif mtype == "headers":
            await self._handle_headers(peer, msg)

        elif mtype == "get_block":
            await self._handle_get_block(peer, msg)

        elif mtype == "get_peers":
            await self._handle_get_peers(peer, msg)

        elif mtype == "bye":
            logger.info(f"Peer {peer.addr} sent BYE: {msg.get('reason', 'unknown')}")
            await self._disconnect(peer)

        else:
            logger.debug(f"Unknown message type '{mtype}' from {peer.addr}")

    async def _handle_new_block(self, peer: Peer, msg: dict):
        """A peer announced a new block."""
        block_data = msg.get("block")
        if not block_data:
            return

        block_height = block_data.get("height", 0)
        block_hash = block_data.get("hash", "")

        # Don't re-process if we already have it
        if block_height <= self.bc.height:
            return

        logger.info(f"New block from {peer.addr}: height={block_height}")

        # Add to chain (the blockchain module handles validation)
        try:
            # block_data should have: height, prev_hash, merkle_root, timestamp,
        # difficulty, nonce, transactions, puf_attestation, hash
            added = self.bc.add_block(block_data)
            if added:
                peer.blocks_recv += 1
                # Gossip to other peers
                await self.broadcast_block(block_data, exclude=peer)
        except Exception as e:
            logger.warning(f"Failed to add block from {peer.addr}: {e}")

    async def _handle_new_tx(self, peer: Peer, msg: dict):
        """A peer announced a new transaction."""
        tx_data = msg.get("tx")
        if not tx_data:
            return

        tx_hash = tx_data.get("hash", "")
        if tx_hash in [t.get("hash", "") for t in self.bc.mempool]:
            return  # already have it

        logger.info(f"New tx from {peer.addr}: {tx_hash[:16]}...")

        try:
            self.bc.add_to_mempool(tx_data)
            await self.broadcast_tx(tx_data, exclude=peer)
        except Exception as e:
            logger.warning(f"Failed to add tx from {peer.addr}: {e}")

    async def _handle_get_headers(self, peer: Peer, msg: dict):
        """Peer wants block headers."""
        start_height = msg.get("start_height", 0)
        max_count = min(msg.get("max_count", 500), 500)

        headers = []
        for h in range(start_height, min(start_height + max_count, self.bc.height + 1)):
            block = self.bc.get_block(h)
            if block:
                headers.append({
                    "height": block.get("height"),
                    "prev_hash": block.get("prev_hash"),
                    "merkle_root": block.get("merkle_root"),
                    "timestamp": block.get("timestamp"),
                    "difficulty": block.get("difficulty"),
                    "nonce": block.get("nonce"),
                    "hash": block.get("hash"),
                })

        await self._send_msg(peer, {"type": "headers", "headers": headers})
        logger.debug(f"Sent {len(headers)} headers to {peer.addr}")

    async def _handle_headers(self, peer: Peer, msg: dict):
        """We received block headers (chain sync)."""
        headers = msg.get("headers", [])
        logger.info(f"Received {len(headers)} headers from {peer.addr}")

        for hdr in headers:
            h = hdr.get("height", 0)
            if h > self.bc.height:
                # We're behind — request full block
                await self._request_block(peer, hdr.get("hash"))

    async def _handle_get_block(self, peer: Peer, msg: dict):
        """Peer wants a full block."""
        block_hash = msg.get("hash")
        block = self.bc.get_block_by_hash(block_hash)
        if block:
            await self._send_msg(peer, {"type": "block", "block": block})

    async def _handle_get_peers(self, peer: Peer, msg: dict):
        """Peer wants to know about other peers."""
        peer_list = []
        for k, p in list(self.peers.items())[:10]:  # max 10 peers
            if p != peer:
                peer_list.append({"addr": p.addr[0], "port": p.addr[1]})
        await self._send_msg(peer, {"type": "peers", "peers": peer_list})

    # ============================================================
    # Sync Helpers
    # ============================================================

    async def _request_headers(self, peer: Peer, start_height: int):
        """Request block headers starting at start_height."""
        await self._send_msg(peer, {
            "type": "get_headers",
            "start_height": start_height,
            "max_count": 500,
        })

    async def _request_block(self, peer: Peer, block_hash: str):
        """Request a single block by hash."""
        await self._send_msg(peer, {"type": "get_block", "hash": block_hash})

    # ============================================================
    # Broadcast / Gossip
    # ============================================================

    async def broadcast_block(self, block_data: dict, exclude: Peer = None):
        """Broadcast a new block to all peers."""
        msg = {"type": "new_block", "block": block_data}
        disconnected = []
        for key, peer in self.peers.items():
            if peer == exclude:
                continue
            try:
                await self._send_msg(peer, msg)
                peer.blocks_sent += 1
            except Exception:
                disconnected.append(key)

        for key in disconnected:
            p = self.peers.pop(key, None)
            if p:
                logger.info(f"Peer {key} dropped during broadcast")

    async def broadcast_tx(self, tx_data: dict, exclude: Peer = None):
        """Broadcast a new transaction to all peers."""
        msg = {"type": "new_tx", "tx": tx_data}
        for key, peer in list(self.peers.items()):
            if peer == exclude:
                continue
            try:
                await self._send_msg(peer, msg)
            except Exception:
                pass

    # ============================================================
    # Outbound Connections
    # ============================================================

    async def connect_to(self, host: str, port: int = DEFAULT_PORT):
        """Connect to a remote peer."""
        peer_key = f"{host}:{port}"
        if peer_key in self.peers:
            return  # already connected
        if len(self.peers) >= MAX_PEERS:
            return

        logger.info(f"Connecting to {peer_key}...")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=5.0
            )
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            logger.warning(f"Connect to {peer_key} failed: {e}")
            return

        peer = Peer(reader=reader, writer=writer, addr=(host, port))

        # Send HELLO
        await self._send_msg(peer, {
            "type": "hello",
            "version": PROTOCOL_VERSION,
            "chain_height": self.bc.height,
            "best_hash": self.bc.best_hash.hex() if hasattr(self.bc.best_hash, 'hex') else str(self.bc.best_hash),
            "capabilities": ["full_node", "miner_api"],
            "user_agent": "quartz-node/0.1.0",
            "port": self.port,
        })

        # Wait for HELLO_ACK
        msg = await self._recv_msg(peer)
        if not msg or msg.get("type") != "hello_ack":
            logger.warning(f"No HELLO_ACK from {peer_key}")
            writer.close()
            return

        peer.version = msg.get("version", 0)
        peer.chain_height = msg.get("chain_height", 0)
        peer.capabilities = msg.get("capabilities", [])
        peer.user_agent = msg.get("user_agent", "unknown")

        self.peers[peer_key] = peer
        logger.info(f"Connected to {peer_key} (v{peer.version}, height={peer.chain_height})")

        # Start message loop for this peer
        asyncio.create_task(self._peer_loop(peer))

    async def _peer_loop(self, peer: Peer):
        """Message loop for outbound peers."""
        try:
            while self.running:
                msg = await self._recv_msg(peer)
                if msg is None:
                    break
                await self._handle_msg(peer, msg)
        except Exception as e:
            logger.warning(f"Peer loop error: {e}")
        finally:
            await self._disconnect(peer)

    async def connect_bootnodes(self):
        """Connect to all configured bootnodes."""
        for host, port in self.bootnodes:
            asyncio.create_task(self.connect_to(host, port))

    # ============================================================
    # Background Tasks
    # ============================================================

    async def _ping_loop(self):
        """Send periodic pings and check for stale peers."""
        while self.running:
            await asyncio.sleep(PING_INTERVAL)
            now = time.time()
            stale = []
            for key, peer in self.peers.items():
                if now - peer.last_ping > PEER_TIMEOUT:
                    stale.append(key)
                else:
                    try:
                        await self._send_msg(peer, {"type": "ping", "timestamp": int(now)})
                    except Exception:
                        stale.append(key)

            for key in stale:
                logger.info(f"Peer {key} timed out")
                peer = self.peers.pop(key, None)
                if peer:
                    try:
                        peer.writer.close()
                    except Exception:
                        pass

    async def _discovery_loop(self):
        """Periodically ask peers for their peer lists."""
        while self.running:
            await asyncio.sleep(300)  # every 5 minutes
            for peer in list(self.peers.values()):
                try:
                    await self._send_msg(peer, {"type": "get_peers"})
                except Exception:
                    pass

    # ============================================================
    # Lifecycle
    # ============================================================

    async def start(self):
        """Start the P2P server."""
        self.server = await asyncio.start_server(
            self._handle_inbound, self.host, self.port
        )
        self.running = True

        # Start background tasks
        asyncio.create_task(self._ping_loop())
        asyncio.create_task(self._discovery_loop())

        # Connect to bootnodes
        await self.connect_bootnodes()

        addrs = ", ".join(str(s.getsockname()) for s in self.server.sockets)
        logger.info(f"P2P server listening on {addrs}")

    async def stop(self):
        """Stop the P2P server."""
        self.running = False

        # Send BYE to all peers
        for peer in list(self.peers.values()):
            try:
                await self._send_msg(peer, {"type": "bye", "reason": "shutting_down"})
            except Exception:
                pass

        # Close all connections
        for peer in list(self.peers.values()):
            try:
                peer.writer.close()
                await peer.writer.wait_closed()
            except Exception:
                pass

        self.peers.clear()

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        logger.info("P2P server stopped")

    def get_peer_info(self) -> list:
        """Get info about all connected peers (for API)."""
        return [
            {
                "addr": f"{p.addr[0]}:{p.addr[1]}",
                "version": p.version,
                "user_agent": p.user_agent,
                "chain_height": p.chain_height,
                "capabilities": p.capabilities,
                "connected_at": p.connected_at,
                "bytes_sent": p.bytes_sent,
                "bytes_recv": p.bytes_recv,
                "blocks_sent": p.blocks_sent,
                "blocks_recv": p.blocks_recv,
            }
            for p in self.peers.values()
        ]
