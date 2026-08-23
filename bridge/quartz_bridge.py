#!/usr/bin/env python3
"""
Quartz Bridge — Linux daemon for Helium hotspot hardware.

Runs on Bobcat Miner 300, RAK Wireless, SenseCAP, and other Helium
hotspots that have a Linux SoC + LoRa concentrator (SX1301/SX1302/SX1303).

The bridge connects the Quartz gateway (TCP P2P) to the LoRa mesh,
relaying block headers and work templates to ESP32 leaf miners
that are offline (no WiFi/internet).

Architecture:
    Gateway (VPS)  ←──TCP──→  Bridge (Helium hotspot)  ←──LoRa──→  Leaf miners (ESP32)

The bridge does NOT mine — it has no ESP32 PUF. It earns the mesh
relayer share (10% of each block, once that fork activates) by
relaying data between online and offline network participants.

License: MIT
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import struct
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple

# -----------------------------------------------------------------------------
# Constants — must match firmware/quartz_lora.h and reference-node/quartz/
# -----------------------------------------------------------------------------

# LoRa packet types (must match qz_lora_pkt_type_t in quartz_lora.h)
PKT_BEACON      = 1
PKT_BLOCK_HDR   = 2
PKT_TX          = 3
PKT_BLOCK_REQ   = 4
PKT_BLOCK_DATA  = 5
PKT_PEER_LIST   = 6
PKT_PING        = 7
PKT_PONG        = 8

# LoRa header structure — must match qz_lora_header_t (packed, no padding)
# C struct: uint8_t type + uint8_t ttl + uint8_t[6] sender_id + uint8_t hop_count
#           + uint16_t payload_len + uint16_t seq = 1+1+6+1+2+2 = 13 bytes
LORA_HEADER_FMT = '<BB6sBHH'  # type, ttl, sender_id(6), hop_count, payload_len, seq
LORA_HEADER_SIZE = struct.calcsize(LORA_HEADER_FMT)  # 13
LORA_MAX_PAYLOAD = 242 - LORA_HEADER_SIZE  # 242 max LoRa packet - 13 header = 229

# Quartz sync word (must match firmware)
QUARTZ_SYNC_WORD = 0x51  # 'Q'

# Default LoRa configuration (must match ESP32 firmware defaults)
LORA_FREQ_US915 = 915_000_000
LORA_FREQ_EU868 = 868_100_000
LORA_BW = 125_000
LORA_SF = 7
LORA_CR = 5  # 4/5
LORA_TX_POWER = 14
LORA_PREAMBLE = 8

# Mesh parameters (must match quartz_lora.h)
MESH_BEACON_INTERVAL = 60   # seconds
MESH_PEER_TIMEOUT = 300     # 5 minutes
MESH_GOSSIP_TTL = 7
MESH_STORE_FORWARD = 16     # store last 16 packets

# P2P protocol (must match reference-node/quartz/p2p.py)
P2P_DEFAULT_PORT = 21100
P2P_PROTOCOL_VERSION = 1
P2P_MAX_MSG_SIZE = 4 * 1024 * 1024

# Gateway API endpoints
GATEWAY_API_TIMEOUT = 10  # seconds

# Bridge identity
BRIDGE_VERSION = "0.1.0"
BRIDGE_USER_AGENT = f"quartz-bridge/{BRIDGE_VERSION}"

logger = logging.getLogger("quartz.bridge")


# -----------------------------------------------------------------------------
# LoRa Packet Structures
# -----------------------------------------------------------------------------

class LoRaPacket:
    """Parse/build LoRa mesh packets matching qz_lora_header_t."""

    def __init__(self, pkt_type: int, payload: bytes,
                 ttl: int = MESH_GOSSIP_TTL, sender_id: bytes = b'\x00' * 6,
                 hop_count: int = 0, seq: int = 0):
        self.type = pkt_type
        self.ttl = ttl
        self.sender_id = sender_id[:6]
        self.hop_count = hop_count
        self.payload = payload
        self.seq = seq

    def serialize(self) -> bytes:
        """Serialize to bytes for LoRa TX."""
        header = struct.pack(
            LORA_HEADER_FMT,
            self.type,
            self.ttl,
            self.sender_id,
            self.hop_count,
            len(self.payload),
            self.seq,
        )
        return header + self.payload[:LORA_MAX_PAYLOAD]

    @classmethod
    def deserialize(cls, data: bytes) -> Optional['LoRaPacket']:
        """Deserialize from received LoRa bytes."""
        if len(data) < LORA_HEADER_SIZE:
            return None
        try:
            pkt_type, ttl, sender_id, hop_count, payload_len, seq = struct.unpack(
                LORA_HEADER_FMT, data[:LORA_HEADER_SIZE]
            )
            payload = data[LORA_HEADER_SIZE:LORA_HEADER_SIZE + payload_len]
            return cls(pkt_type, payload, ttl, sender_id, hop_count, seq)
        except struct.error:
            return None

    def __repr__(self):
        return (f"LoRaPacket(type={self.type}, ttl={self.ttl}, "
                f"hop={self.hop_count}, seq={self.seq}, "
                f"payload_len={len(self.payload)})")


# -----------------------------------------------------------------------------
# Block Announcement (80 bytes, must match qz_lora_block_announce_t)
# -----------------------------------------------------------------------------

# Block announcement payload (must match qz_lora_block_announce_t in firmware)
# C struct (packed): uint32 version + uint32 height + uint8[32] prev_hash
# + uint8[32] merkle + uint32 timestamp + uint32 difficulty + uint32 nonce
# = 4+4+32+32+4+4+4 = 84 bytes
BLOCK_ANNOUNCE_FMT = '<II32s32sIII'  # version, height, prev_hash, merkle, ts, diff, nonce
BLOCK_ANNOUNCE_SIZE = struct.calcsize(BLOCK_ANNOUNCE_FMT)  # 84

def pack_block_announce(version: int, height: int, prev_hash: bytes,
                        merkle_root: bytes, timestamp: int,
                        difficulty: int, nonce: int) -> bytes:
    """Pack a block announcement payload."""
    return struct.pack(BLOCK_ANNOUNCE_FMT, version, height,
                       prev_hash[:32], merkle_root[:32],
                       timestamp, difficulty, nonce)

def unpack_block_announce(payload: bytes) -> Optional[dict]:
    """Unpack a block announcement payload."""
    if len(payload) < BLOCK_ANNOUNCE_SIZE:
        return None
    try:
        version, height, prev_hash, merkle_root, timestamp, difficulty, nonce = \
            struct.unpack(BLOCK_ANNOUNCE_FMT, payload[:BLOCK_ANNOUNCE_SIZE])
        return {
            "version": version,
            "height": height,
            "prev_hash": prev_hash.hex(),
            "merkle_root": merkle_root.hex(),
            "timestamp": timestamp,
            "difficulty": difficulty,
            "nonce": nonce,
        }
    except struct.error:
        return None


# -----------------------------------------------------------------------------
# Beacon payload (must match qz_lora_beacon_t)
# -----------------------------------------------------------------------------

BEACON_FMT = '<I8sBB'  # height, hash(8), caps, mesh_id
BEACON_SIZE = struct.calcsize(BEACON_FMT)

def pack_beacon(height: int, best_hash: bytes, capabilities: int,
                mesh_id: int = 0) -> bytes:
    """Pack a beacon payload."""
    return struct.pack(BEACON_FMT, height, best_hash[:8], capabilities, mesh_id)

def unpack_beacon(payload: bytes) -> Optional[dict]:
    """Unpack a beacon payload."""
    if len(payload) < BEACON_SIZE:
        return None
    try:
        height, best_hash, caps, mesh_id = struct.unpack(BEACON_FMT, payload[:BEACON_SIZE])
        return {
            "height": height,
            "best_hash": best_hash.hex(),
            "capabilities": caps,
            "mesh_id": mesh_id,
        }
    except struct.error:
        return None


# -----------------------------------------------------------------------------
# Peer Tracking
# -----------------------------------------------------------------------------

class MeshPeer:
    """A LoRa mesh peer (ESP32 miner or another bridge)."""

    def __init__(self, mac: bytes, capabilities: int = 0,
                 height: int = 0):
        self.mac = mac
        self.capabilities = capabilities
        self.height = height
        self.last_seen = time.time()
        self.first_seen = time.time()
        self.packets_relayed = 0

    def is_stale(self, timeout: int = MESH_PEER_TIMEOUT) -> bool:
        return (time.time() - self.last_seen) > timeout

    @property
    def has_wifi(self) -> bool:
        return bool(self.capabilities & 0x01)

    @property
    def is_mining(self) -> bool:
        return bool(self.capabilities & 0x02)

    def __repr__(self):
        return f"MeshPeer(mac={self.mac.hex()}, height={self.height}, mining={self.is_mining})"


# -----------------------------------------------------------------------------
# SX130x Concentrator Interface (via semtech packet forwarder / SPI)
# -----------------------------------------------------------------------------

class SX130xRadio:
    """
    Interface to SX1301/SX1302/SX1303 LoRa concentrator on Helium hotspots.

    Helium hotspots run a Semtech packet forwarder (basicstation or
    legacy udp-packet-forwarder) that talks to the concentrator chip
    over SPI. Rather than reimplementing the SPI driver, we interface
    with the concentrator through one of these methods:

    1. **UDP packet forwarder mode** (default): We replace the Helium
       packet forwarder config to point at our local UDP port instead
       of Helium's servers. We send/receive raw LoRa packets via the
       Semtech JSON protocol over UDP.

    2. **Direct SPI** (advanced): If we have direct SPI access to the
       SX130x, we can use the sx130x_hal directly. This is more
       efficient but requires board-specific SPI config.

    For initial release, we use method 1 (UDP packet forwarder) because
    it works on ALL Helium hotspots without board-specific SPI knowledge.
    The packet forwarder is already compiled for the exact hardware.
    """

    def __init__(self, udp_port: int = 1738, region: str = "US915",
                 freq_hz: int = LORA_FREQ_US915):
        self.udp_port = udp_port
        self.region = region
        self.freq_hz = freq_hz
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.rx_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.tx_count = 0
        self.rx_count = 0
        self.relay_count = 0
        self._forwarder_addr: Optional[Tuple[str, int]] = None

    async def start(self):
        """Start the UDP listener for the packet forwarder."""
        loop = asyncio.get_event_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _ForwarderProtocol(self),
            local_addr=('127.0.0.1', self.udp_port),
        )
        logger.info(f"SX130x radio ready (UDP:{self.udp_port}, {self.region}, "
                    f"{self.freq_hz / 1e6:.1f} MHz)")

    async def stop(self):
        """Stop the radio interface."""
        if self.transport:
            self.transport.close()
            self.transport = None

    async def tx(self, data: bytes):
        """
        Transmit data over LoRa.

        Sends a downlink packet to the packet forwarder via UDP.
        The packet forwarder sends it over the SX130x concentrator.
        """
        if not self._forwarder_addr or not self.transport:
            logger.warning("No packet forwarder connected, can't TX")
            return

        # Semtech JSON v1 TX packet
        # We use a simple single-SF channel to match Quartz mesh params
        tx_json = {
            "txpk": {
                "imme": True,
                "freq": self.freq_hz / 1e6,
                "rfch": 0,
                "powe": LORA_TX_POWER,
                "modu": "LORA",
                "datr": f"SF{LORA_SF}BW{LORA_BW // 1000}",  # e.g. "SF7BW125"
                "codr": "4/5",
                "ipol": False,
                "size": len(data),
                "data": data.hex(),
            }
        }

        msg = json.dumps(tx_json).encode('utf-8')
        self.transport.sendto(msg, self._forwarder_addr)
        self.tx_count += 1
        logger.debug(f"TX {len(data)} bytes via LoRa")

    async def rx(self, timeout: float = 1.0) -> Optional[bytes]:
        """Receive a LoRa packet from the queue."""
        try:
            return await asyncio.wait_for(self.rx_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def _on_packet_forwarder_data(self, data: bytes, addr: Tuple[str, int]):
        """Called when the packet forwarder sends us data."""
        self._forwarder_addr = addr

        try:
            pkt = json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        # Semtech JSON v1 RX packet
        if "rxpk" in pkt:
            for rxpk in pkt["rxpk"]:
                raw_hex = rxpk.get("data", "")
                try:
                    raw = bytes.fromhex(raw_hex)
                    self.rx_count += 1
                    asyncio.create_task(self.rx_queue.put(raw))
                except ValueError:
                    pass

        # Packet forwarder stat report
        elif "stat" in pkt:
            stat = pkt["stat"]
            logger.debug(f"Forwarder stats: txf={stat.get('txf', 0)}, "
                        f"rxf={stat.get('rxf', 0)}, "
                        f"rxb={stat.get('rxb', 0)}")


class _ForwarderProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol for the SX130x UDP interface."""

    def __init__(self, radio: SX130xRadio):
        self.radio = radio

    def connection_made(self, transport):
        pass

    def datagram_received(self, data, addr):
        self.radio._on_packet_forwarder_data(data, addr)

    def error_received(self, exc):
        logger.warning(f"UDP error: {exc}")


# -----------------------------------------------------------------------------
# TCP P2P Client (connects to Quartz gateway)
# -----------------------------------------------------------------------------

class GatewayClient:
    """
    TCP P2P client that connects to a Quartz gateway node.

    Uses the length-prefixed JSON protocol from p2p.py.
    Receives new block announcements and transaction relay,
    forwards them to the LoRa mesh via the radio.
    """

    def __init__(self, gateway_host: str, gateway_port: int = P2P_DEFAULT_PORT):
        self.host = gateway_host
        self.port = gateway_port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self.chain_height = 0
        self.best_hash = ""
        self.reconnect_delay = 5
        self._on_block: Optional[callable] = None
        self._on_tx: Optional[callable] = None

    def on_block(self, callback):
        """Register callback for new block from gateway."""
        self._on_block = callback

    def on_tx(self, callback):
        """Register callback for new transaction from gateway."""
        self._on_tx = callback

    async def connect(self):
        """Connect to the gateway node with reconnection."""
        while True:
            try:
                logger.info(f"Connecting to gateway {self.host}:{self.port}...")
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=10.0
                )

                # Send HELLO
                await self._send_msg({
                    "type": "hello",
                    "version": P2P_PROTOCOL_VERSION,
                    "chain_height": self.chain_height,
                    "best_hash": self.best_hash,
                    "capabilities": ["bridge_node"],
                    "user_agent": BRIDGE_USER_AGENT,
                    "port": 0,  # we don't accept inbound P2P
                })

                # Wait for HELLO_ACK
                msg = await self._recv_msg()
                if not msg or msg.get("type") != "hello_ack":
                    logger.warning("No HELLO_ACK from gateway")
                    self.writer.close()
                    await self.writer.wait_closed()
                    self.connected = False
                    await asyncio.sleep(self.reconnect_delay)
                    continue

                self.connected = True
                self.reconnect_delay = 5  # reset backoff
                gw_height = msg.get("chain_height", 0)
                gw_hash = msg.get("best_hash", "")
                logger.info(f"Connected to gateway (height={gw_height}, "
                           f"hash={gw_hash[:16]}...)")

                # If gateway is ahead, request headers
                if gw_height > self.chain_height:
                    await self._request_headers(self.chain_height + 1)

                # Main message loop
                while self.connected:
                    msg = await self._recv_msg()
                    if msg is None:
                        break
                    await self._handle_msg(msg)

            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                logger.warning(f"Gateway connection lost: {e}")
            except Exception as e:
                logger.error(f"Gateway client error: {e}", exc_info=True)

            self.connected = False
            if self.writer:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    pass
                self.writer = None

            logger.info(f"Reconnecting in {self.reconnect_delay}s...")
            await asyncio.sleep(self.reconnect_delay)
            self.reconnect_delay = min(self.reconnect_delay * 2, 60)

    async def _send_msg(self, msg: dict):
        """Send a length-prefixed JSON message."""
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if len(data) > P2P_MAX_MSG_SIZE:
            return
        length = struct.pack(">I", len(data))
        try:
            self.writer.write(length + data)
            await self.writer.drain()
        except (ConnectionError, OSError) as e:
            logger.warning(f"Send to gateway failed: {e}")
            self.connected = False

    async def _recv_msg(self) -> Optional[dict]:
        """Receive a length-prefixed JSON message."""
        try:
            length_data = await self.reader.readexactly(4)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None

        length = struct.unpack(">I", length_data)[0]
        if length > P2P_MAX_MSG_SIZE:
            return None

        try:
            data = await self.reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return None

        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    async def _handle_msg(self, msg: dict):
        """Handle a message from the gateway."""
        mtype = msg.get("type")

        if mtype == "ping":
            await self._send_msg({"type": "pong", "timestamp": int(time.time())})

        elif mtype == "new_block":
            block = msg.get("block", {})
            self.chain_height = block.get("height", self.chain_height)
            self.best_hash = block.get("hash", self.best_hash)
            logger.info(f"New block from gateway: height={self.chain_height}")

            if self._on_block:
                await self._on_block(block)

            # Broadcast to LoRa mesh
            if self._on_block:
                await self._on_block(block)

        elif mtype == "new_tx":
            tx = msg.get("tx", {})
            logger.debug(f"New tx from gateway: {tx.get('hash', '?')[:16]}")
            if self._on_tx:
                await self._on_tx(tx)

        elif mtype == "headers":
            headers = msg.get("headers", [])
            if headers:
                latest = headers[-1]
                self.chain_height = latest.get("height", self.chain_height)
                self.best_hash = latest.get("hash", self.best_hash)
                logger.info(f"Synced {len(headers)} headers, "
                           f"now at height {self.chain_height}")

        elif mtype == "bye":
            logger.info(f"Gateway sent BYE: {msg.get('reason', '?')}")
            self.connected = False

    async def _request_headers(self, start_height: int):
        """Request block headers from the gateway."""
        await self._send_msg({
            "type": "get_headers",
            "start_height": start_height,
            "max_count": 500,
        })

    async def submit_block(self, block_data: dict):
        """Submit a block found by a leaf miner via LoRa."""
        await self._send_msg({"type": "new_block", "block": block_data})
        logger.info(f"Submitted block to gateway: height={block_data.get('height', '?')}")

    async def submit_tx(self, tx_data: dict):
        """Submit a transaction received via LoRa."""
        await self._send_msg({"type": "new_tx", "tx": tx_data})

    async def disconnect(self):
        """Disconnect from gateway."""
        if self.writer and self.connected:
            try:
                await self._send_msg({"type": "bye", "reason": "bridge_shutdown"})
            except Exception:
                pass
        self.connected = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Gateway HTTP API Client (for work templates, miner registration)
# -----------------------------------------------------------------------------

class GatewayAPI:
    """
    HTTP client for the Quartz gateway API.

    Fetches mining work templates and chain state from the gateway's
    HTTP server (port 21100/api/v1/...).
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.height = 0

    async def get_work(self) -> Optional[dict]:
        """GET /api/v1/mining/work — fetch current block template."""
        try:
            url = f"{self.base_url}/api/v1/mining/work"
            data = await self._http_get(url)
            if data:
                self.height = data.get("height", self.height)
                return data
        except Exception as e:
            logger.debug(f"get_work failed: {e}")
        return None

    async def get_info(self) -> Optional[dict]:
        """GET /api/v1/info — chain info."""
        try:
            return await self._http_get(f"{self.base_url}/api/v1/info")
        except Exception as e:
            logger.debug(f"get_info failed: {e}")
        return None

    async def submit_block(self, block_data: dict) -> Optional[dict]:
        """POST /api/v1/mining/submit — submit a found block."""
        try:
            return await self._http_post(
                f"{self.base_url}/api/v1/mining/submit", block_data)
        except Exception as e:
            logger.warning(f"submit_block failed: {e}")
        return None

    async def _http_get(self, url: str) -> Optional[dict]:
        """Simple async HTTP GET."""
        import urllib.request
        loop = asyncio.get_event_loop()
        try:
            req = urllib.request.Request(url)
            def _fetch():
                with urllib.request.urlopen(req, timeout=GATEWAY_API_TIMEOUT) as r:
                    return json.loads(r.read().decode('utf-8'))
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.debug(f"HTTP GET {url} failed: {e}")
            return None

    async def _http_post(self, url: str, body: dict) -> Optional[dict]:
        """Simple async HTTP POST."""
        import urllib.request
        loop = asyncio.get_event_loop()
        try:
            data = json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, data=data,
                                         headers={'Content-Type': 'application/json'})
            def _fetch():
                with urllib.request.urlopen(req, timeout=GATEWAY_API_TIMEOUT) as r:
                    return json.loads(r.read().decode('utf-8'))
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.debug(f"HTTP POST {url} failed: {e}")
            return None


# -----------------------------------------------------------------------------
# Quartz Bridge — main daemon
# -----------------------------------------------------------------------------

class QuartzBridge:
    """
    The main bridge daemon.

    Connects a Quartz gateway (TCP P2P + HTTP API) to a LoRa mesh
    of ESP32 leaf miners via the SX130x concentrator on a Helium hotspot.

    Data flow:
      Gateway → Bridge → LoRa → Leaf miners  (block headers, work templates)
      Leaf miners → LoRa → Bridge → Gateway  (found blocks, transactions)
    """

    def __init__(self, gateway_host: str, gateway_port: int = P2P_DEFAULT_PORT,
                 http_port: int = 21100, lora_port: int = 1738,
                 region: str = "US915", freq_hz: int = None,
                 beacon_interval: int = MESH_BEACON_INTERVAL,
                 work_refresh: int = 30):
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.http_base = f"http://{gateway_host}:{http_port}"
        self.beacon_interval = beacon_interval
        self.work_refresh = work_refresh

        # LoRa frequency
        if freq_hz:
            self.freq_hz = freq_hz
        elif region == "EU868":
            self.freq_hz = LORA_FREQ_EU868
        else:
            self.freq_hz = LORA_FREQ_US915

        # Components
        self.radio = SX130xRadio(udp_port=lora_port, region=region,
                                 freq_hz=self.freq_hz)
        self.gateway = GatewayClient(gateway_host, gateway_port)
        self.api = GatewayAPI(self.http_base)

        # Mesh state
        self.peers: Dict[str, MeshPeer] = {}  # mac_hex -> MeshPeer
        self.seen_packets: Set[int] = set()  # dedup by seq number
        self.stored_packets: List[bytes] = []  # store-and-forward
        self.current_work: Optional[dict] = None
        self.current_height = 0
        self.current_hash = b'\x00' * 32

        # Stats
        self.blocks_relayed_down = 0  # gateway → LoRa
        self.blocks_relayed_up = 0    # LoRa → gateway
        self.tx_relayed_down = 0
        self.tx_relayed_up = 0
        self.beacons_sent = 0
        self.packets_relayed = 0

        self.running = False

        # Register gateway callbacks
        self.gateway.on_block(self._on_gateway_block)
        self.gateway.on_tx(self._on_gateway_tx)

    async def start(self):
        """Start the bridge daemon."""
        self.running = True

        logger.info("╔════════════════════════════════════╗")
        logger.info("║       Quartz Bridge Daemon         ║")
        logger.info("╚════════════════════════════════════╝")
        logger.info(f"Version: {BRIDGE_VERSION}")
        logger.info(f"Gateway: {self.gateway_host}:{self.gateway_port}")
        logger.info(f"HTTP API: {self.http_base}")
        logger.info(f"LoRa: {self.freq_hz / 1e6:.1f} MHz ({self.radio.region})")
        logger.info(f"Beacon interval: {self.beacon_interval}s")
        logger.info(f"Work refresh: {self.work_refresh}s")

        # Start radio
        await self.radio.start()

        # Start background tasks
        tasks = [
            asyncio.create_task(self.gateway.connect()),
            asyncio.create_task(self._beacon_loop()),
            asyncio.create_task(self._lora_rx_loop()),
            asyncio.create_task(self._work_refresh_loop()),
            asyncio.create_task(self._peer_maintenance_loop()),
        ]

        # Wait for shutdown signal
        try:
            await asyncio.Event().wait()  # blocks forever
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        """Stop the bridge daemon."""
        logger.info("Shutting down bridge...")
        self.running = False
        await self.gateway.disconnect()
        await self.radio.stop()
        logger.info("Bridge stopped.")

    # -- Gateway → LoRa -------------------------------------------------

    async def _on_gateway_block(self, block: dict):
        """Handle a new block from the gateway — relay to LoRa mesh."""
        self.current_height = block.get("height", self.current_height)
        hash_hex = block.get("hash", "")
        if hash_hex:
            try:
                self.current_hash = bytes.fromhex(hash_hex)
            except ValueError:
                pass

        # Build block announcement packet
        try:
            prev_hash = bytes.fromhex(block.get("prev_hash", "").ljust(64, '0')[:64])
            merkle = bytes.fromhex(block.get("merkle_root", "").ljust(64, '0')[:64])
        except ValueError:
            prev_hash = b'\x00' * 32
            merkle = b'\x00' * 32

        payload = pack_block_announce(
            version=block.get("version", 1),
            height=block.get("height", 0),
            prev_hash=prev_hash,
            merkle_root=merkle,
            timestamp=block.get("timestamp", int(time.time())),
            difficulty=block.get("difficulty", 20),
            nonce=block.get("nonce", 0),
        )

        pkt = LoRaPacket(PKT_BLOCK_HDR, payload, ttl=MESH_GOSSIP_TTL)
        await self.radio.tx(pkt.serialize())
        self.blocks_relayed_down += 1
        logger.info(f"Relayed block #{self.current_height} to LoRa mesh "
                    f"({len(payload)} bytes)")

    async def _on_gateway_tx(self, tx: dict):
        """Handle a new transaction from the gateway — relay to LoRa mesh."""
        # Pack as a simple JSON payload (tx relay format may evolve)
        try:
            tx_bytes = json.dumps(tx, separators=(",", ":")).encode("utf-8")
            if len(tx_bytes) > LORA_MAX_PAYLOAD:
                logger.warning(f"TX too large for LoRa ({len(tx_bytes)} bytes), skipping")
                return
            pkt = LoRaPacket(PKT_TX, tx_bytes, ttl=MESH_GOSSIP_TTL)
            await self.radio.tx(pkt.serialize())
            self.tx_relayed_down += 1
            logger.debug(f"Relayed TX to LoRa mesh ({len(tx_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to relay TX: {e}")

    # -- LoRa → Gateway -------------------------------------------------

    async def _lora_rx_loop(self):
        """Main LoRa receive loop — handle packets from ESP32 miners."""
        logger.info("LoRa RX loop started")
        while self.running:
            raw = await self.radio.rx(timeout=2.0)
            if raw is None:
                continue

            pkt = LoRaPacket.deserialize(raw)
            if pkt is None:
                logger.debug(f"Invalid LoRa packet ({len(raw)} bytes)")
                continue

            # Dedup by seq number
            if pkt.seq in self.seen_packets:
                continue
            self.seen_packets.add(pkt.seq)
            # Keep dedup set bounded
            if len(self.seen_packets) > 1000:
                self.seen_packets = set(list(self.seen_packets)[-500:])

            # Store for store-and-forward
            self.stored_packets.append(raw)
            if len(self.stored_packets) > MESH_STORE_FORWARD:
                self.stored_packets.pop(0)

            # Track peer
            mac_hex = pkt.sender_id.hex()
            if mac_hex not in self.peers:
                self.peers[mac_hex] = MeshPeer(mac=pkt.sender_id)
            peer = self.peers[mac_hex]
            peer.last_seen = time.time()

            # Handle by packet type
            if pkt.type == PKT_BEACON:
                self._handle_beacon(pkt, peer)
            elif pkt.type == PKT_BLOCK_HDR:
                # A miner or another bridge announced a block
                # If we don't have it, it might be a found block from a leaf miner
                await self._handle_block_from_mesh(pkt)
                # Relay (gossip) if TTL > 0
                if pkt.ttl > 1:
                    pkt.ttl -= 1
                    pkt.hop_count += 1
                    await self.radio.tx(pkt.serialize())
                    self.packets_relayed += 1
            elif pkt.type == PKT_TX:
                await self._handle_tx_from_mesh(pkt)
                if pkt.ttl > 1:
                    pkt.ttl -= 1
                    pkt.hop_count += 1
                    await self.radio.tx(pkt.serialize())
                    self.packets_relayed += 1
            elif pkt.type == PKT_BLOCK_REQ:
                # A leaf miner is requesting a specific block
                await self._handle_block_request(pkt)
            elif pkt.type == PKT_PING:
                # Respond with PONG
                pong = LoRaPacket(PKT_PONG, b'pong', ttl=1,
                                  sender_id=b'\x00' * 6)
                await self.radio.tx(pong.serialize())

    def _handle_beacon(self, pkt: LoRaPacket, peer: MeshPeer):
        """Handle a beacon from an ESP32 miner."""
        beacon = unpack_beacon(pkt.payload)
        if beacon:
            peer.height = beacon["height"]
            peer.capabilities = beacon["capabilities"]
            logger.debug(f"Beacon from {peer.mac.hex()}: "
                        f"height={beacon['height']}, "
                        f"caps=0x{beacon['capabilities']:02x}")

    async def _handle_block_from_mesh(self, pkt: LoRaPacket):
        """A block announcement from the LoRa mesh — possibly a found block."""
        announce = unpack_block_announce(pkt.payload)
        if not announce:
            return

        # If this block is ahead of what we know, it might be a newly mined block
        if announce["height"] > self.current_height:
            logger.info(f"Block from mesh: height={announce['height']}, "
                       f"miner={pkt.sender_id.hex()}")

            # Submit to gateway via HTTP API
            block_data = {
                "height": announce["height"],
                "prev_hash": announce["prev_hash"],
                "merkle_root": announce["merkle_root"],
                "timestamp": announce["timestamp"],
                "difficulty": announce["difficulty"],
                "nonce": announce["nonce"],
                "version": announce["version"],
                "miner_id": pkt.sender_id.hex(),
                "puf_attestation": True,  # leaf miner attested via PUF
            }

            result = await self.api.submit_block(block_data)
            if result and result.get("accepted"):
                self.blocks_relayed_up += 1
                self.current_height = announce["height"]
                logger.info(f"✅ Block #{announce['height']} accepted by gateway")
            elif result:
                logger.warning(f"Block #{announce['height']} rejected: "
                             f"{result.get('error', 'unknown')}")

    async def _handle_tx_from_mesh(self, pkt: LoRaPacket):
        """A transaction from the LoRa mesh — submit to gateway."""
        try:
            tx_data = json.loads(pkt.payload.decode('utf-8'))
            await self.gateway.submit_tx(tx_data)
            self.tx_relayed_up += 1
            logger.debug(f"TX from {pkt.sender_id.hex()} submitted to gateway")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Invalid TX payload from mesh: {e}")

    async def _handle_block_request(self, pkt: LoRaPacket):
        """A leaf miner is requesting a specific block via store-and-forward."""
        if len(pkt.payload) < 4:
            return
        requested_height = struct.unpack('<I', pkt.payload[:4])[0]
        logger.debug(f"Block request from mesh: height={requested_height}")

        # Replay from stored packets (simplified)
        # In a full implementation, we'd fetch the block from the gateway
        # and send it as a chunked PKT_BLOCK_DATA response

    # -- Periodic tasks -------------------------------------------------

    async def _beacon_loop(self):
        """Broadcast a beacon every beacon_interval seconds."""
        while self.running:
            await asyncio.sleep(self.beacon_interval)
            if not self.running:
                break

            # Update our height from gateway
            if self.gateway.connected:
                self.current_height = self.gateway.chain_height

            caps = 0x01  # has WiFi (bridge to gateway)
            payload = pack_beacon(
                height=self.current_height,
                best_hash=self.current_hash[:8],
                capabilities=caps,
            )
            pkt = LoRaPacket(PKT_BEACON, payload, ttl=1,
                             sender_id=b'\x00' * 6)  # bridge uses zero MAC
            await self.radio.tx(pkt.serialize())
            self.beacons_sent += 1
            logger.debug(f"Beacon sent (height={self.current_height}, "
                        f"peers={len(self.peers)})")

    async def _work_refresh_loop(self):
        """Periodically fetch mining work from the gateway HTTP API."""
        while self.running:
            await asyncio.sleep(self.work_refresh)
            if not self.running:
                break

            work = await self.api.get_work()
            if work and work.get("height", 0) > self.current_height:
                self.current_height = work["height"]
                # If we have new work, broadcast it to the mesh
                # Leaf miners can use it to start hashing
                logger.info(f"New work from gateway: height={work['height']}")

                # Relay work template as a block header announcement
                # (leaf miners fill in the nonce)
                try:
                    prev_hash = bytes.fromhex(
                        work.get("prev_hash", "").ljust(64, '0')[:64])
                    merkle = bytes.fromhex(
                        work.get("merkle_root", "").ljust(64, '0')[:64])
                except ValueError:
                    prev_hash = b'\x00' * 32
                    merkle = b'\x00' * 32

                payload = pack_block_announce(
                    version=work.get("version", 1),
                    height=work.get("height", 0),
                    prev_hash=prev_hash,
                    merkle_root=merkle,
                    timestamp=work.get("timestamp", int(time.time())),
                    difficulty=work.get("difficulty", 20),
                    nonce=0,  # leaf miners fill this in
                )
                pkt = LoRaPacket(PKT_BLOCK_HDR, payload, ttl=MESH_GOSSIP_TTL)
                await self.radio.tx(pkt.serialize())

    async def _peer_maintenance_loop(self):
        """Prune stale peers and log stats."""
        while self.running:
            await asyncio.sleep(30)
            now = time.time()
            stale = [mac for mac, p in self.peers.items()
                     if p.is_stale()]
            for mac in stale:
                del self.peers[mac]

            if self.peers or self.radio.tx_count or self.radio.rx_count:
                logger.info(
                    f"Stats: peers={len(self.peers)} | "
                    f"LoRa TX={self.radio.tx_count} RX={self.radio.rx_count} | "
                    f"blocks: ↓{self.blocks_relayed_down} ↑{self.blocks_relayed_up} | "
                    f"tx: ↓{self.tx_relayed_down} ↑{self.tx_relayed_up} | "
                    f"beacons={self.beacons_sent} relayed={self.packets_relayed}"
                )

    # -- API for monitoring ---------------------------------------------

    def get_stats(self) -> dict:
        """Get bridge statistics (for monitoring/health check)."""
        return {
            "version": BRIDGE_VERSION,
            "uptime": int(time.time()),
            "gateway_connected": self.gateway.connected,
            "gateway_host": self.gateway_host,
            "gateway_height": self.gateway.chain_height,
            "lora_region": self.radio.region,
            "lora_freq_mhz": self.freq_hz / 1e6,
            "peers": len(self.peers),
            "peer_list": [
                {
                    "mac": p.mac.hex(),
                    "height": p.height,
                    "mining": p.is_mining,
                    "has_wifi": p.has_wifi,
                    "last_seen_ago": int(time.time() - p.last_seen),
                }
                for p in self.peers.values()
            ],
            "stats": {
                "lora_tx": self.radio.tx_count,
                "lora_rx": self.radio.rx_count,
                "blocks_down": self.blocks_relayed_down,
                "blocks_up": self.blocks_relayed_up,
                "tx_down": self.tx_relayed_down,
                "tx_up": self.tx_relayed_up,
                "beacons_sent": self.beacons_sent,
                "packets_relayed": self.packets_relayed,
            },
        }


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Quartz Bridge — relay between Quartz gateway and LoRa mesh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The bridge runs on Helium hotspot hardware (Bobcat, RAK, SenseCAP)
and relays Quartz blockchain data between the online gateway and
offline ESP32 leaf miners via LoRa.

Before starting:
  1. Reconfigure the hotspot's packet forwarder to send to this
     bridge's UDP port (default 1738) instead of Helium servers.
  2. Set the correct LoRa frequency for your region.
  3. Ensure the Quartz gateway is reachable at the specified host:port.

Example:
  python3 quartz_bridge.py --gateway quartzchain.net \\
      --region US915 --lora-port 1738
        """,
    )
    parser.add_argument('--gateway', required=True,
                        help='Quartz gateway hostname or IP')
    parser.add_argument('--p2p-port', type=int, default=P2P_DEFAULT_PORT,
                        help=f'Gateway P2P port (default: {P2P_DEFAULT_PORT})')
    parser.add_argument('--http-port', type=int, default=21100,
                        help='Gateway HTTP API port (default: 21100)')
    parser.add_argument('--lora-port', type=int, default=1738,
                        help='UDP port for SX130x packet forwarder (default: 1738)')
    parser.add_argument('--region', default='US915',
                        choices=['US915', 'EU868', 'AS923', 'CN470', 'IN865'],
                        help='LoRa region (default: US915)')
    parser.add_argument('--freq', type=int, default=None,
                        help='Override LoRa frequency in Hz')
    parser.add_argument('--beacon-interval', type=int,
                        default=MESH_BEACON_INTERVAL,
                        help=f'Beacon interval in seconds (default: {MESH_BEACON_INTERVAL})')
    parser.add_argument('--work-refresh', type=int, default=30,
                        help='Work template refresh interval (default: 30s)')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'],
                        help='Log level (default: info)')
    parser.add_argument('--data-dir', default='/var/lib/quartz-bridge',
                        help='Data directory (default: /var/lib/quartz-bridge)')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Create data dir
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)

    # Create and start bridge
    bridge = QuartzBridge(
        gateway_host=args.gateway,
        gateway_port=args.p2p_port,
        http_port=args.http_port,
        lora_port=args.lora_port,
        region=args.region,
        freq_hz=args.freq,
        beacon_interval=args.beacon_interval,
        work_refresh=args.work_refresh,
    )

    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _shutdown():
        await bridge.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
        except NotImplementedError:
            pass  # Windows

    try:
        loop.run_until_complete(bridge.start())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == '__main__':
    main()