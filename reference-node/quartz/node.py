"""
Quartz P2P node — handles block relay, chain sync, and peer management.

For the Python reference implementation. ESP32 nodes connect to this
node (and each other) for chain synchronization.
"""

import asyncio
import json
import logging
import struct
import time
from typing import Dict, List, Optional, Set

from .blockchain import (
    Block, BlockHeader, Transaction, Block as BlockType,
    compute_merkle_root, get_block_reward,
    RETARGET_PERIOD, DIFFICULTY_BITS, BLOCK_TIME,
)
from .crystal_hash import check_difficulty

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
MAX_PEERS = 32


class QuartzNode:
    """Quartz P2P node.

    Maintains the blockchain state, handles incoming peer connections,
    and broadcasts new blocks/transactions.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.chain: List[Block] = []
        self.mempool: Dict[bytes, Transaction] = {}
        self.peers: Set[tuple] = set()  # (host, port)
        self.running = False
        self.server: Optional[asyncio.Server] = None

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
        """Start the P2P server."""
        self.running = True
        self.server = await asyncio.start_server(
            self._handle_peer, self.host, self.port
        )
        logger.info(f"Quartz node listening on {self.host}:{self.port}")
        logger.info(f"Chain height: {self.height}")

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        """Stop the node."""
        self.running = False
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
    parser.add_argument('--peer', action='append', help='Connect to peer (host:port)')
    args = parser.parse_args()

    node = QuartzNode(host=args.host, port=args.port)

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

    asyncio.run(node.start())


if __name__ == '__main__':
    main()
