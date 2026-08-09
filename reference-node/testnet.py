#!/usr/bin/env python3
"""
Quartz Testnet Node — runs a full node with genesis block, mining simulation,
and a JSON-RPC API for the block explorer.

This is the SEED node for the Quartz testnet. It:
1. Mines the genesis block (or loads it if already mined)
2. Simulates ESP32 miners finding blocks (for demo purposes)
3. Serves a JSON API for block explorers and wallets
4. Persists the chain to disk
"""

import hashlib
import json
import os
import struct
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Add parent dir to path
import sys
sys.path.insert(0, os.path.dirname(__file__))

from quartz.blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward, get_miner_reward, get_dev_fund_reward,
    HEADER_SIZE, BLOCK_TIME,
)
from quartz.crystal_hash import crystal_hash_verify, check_difficulty
from quartz.crypto import create_new_wallet, public_key_to_address, sign_message

# Override difficulty for fast testnet blocks (real network uses 20)
import quartz.blockchain as _bc
_bc.DIFFICULTY_BITS = 12
TESTNET_DIFFICULTY = 12
TESTNET_BLOCK_TIME = 30  # 30 second blocks for fast testing

DATA_DIR = os.path.join(os.path.dirname(__file__), 'testnet-data')
CHAIN_FILE = os.path.join(DATA_DIR, 'chain.json')

# Dev fund wallet (persistent)
DEV_WALLET_FILE = os.path.join(DATA_DIR, 'dev-wallet.json')

# Simulated ESP32 miners (for demo — real testnet uses real ESP32s)
DEMO_MINERS = [
    {"id": bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x01]), "name": "heltec-001"},
    {"id": bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x02]), "name": "tdisplay-001"},
    {"id": bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x03]), "name": "tdisplay-002"},
    {"id": bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x04]), "name": "m5stack-001"},
    {"id": bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x05]), "name": "heltec-002"},
]


class QuartzChain:
    """Simple in-memory blockchain with persistence."""

    def __init__(self):
        self.blocks = []
        self.mempool = []
        self.known_miners = set()
        self.first_mined = {}
        self.balances = {}  # address -> balance (in sats)
        self.dev_wallet = None

        os.makedirs(DATA_DIR, exist_ok=True)

        # Load or create dev wallet
        if os.path.exists(DEV_WALLET_FILE):
            with open(DEV_WALLET_FILE) as f:
                self.dev_wallet = json.load(f)
        else:
            self.dev_wallet = create_new_wallet()
            with open(DEV_WALLET_FILE, 'w') as f:
                json.dump(self.dev_wallet, f, indent=2)

        # Load or create chain
        if os.path.exists(CHAIN_FILE):
            self.load()
        else:
            self.mine_genesis()

    def mine_genesis(self):
        """Mine the genesis block."""
        print("⛏️  Mining genesis block...")

        genesis = Block.create_genesis()
        genesis.header.timestamp = int(time.time())

        # Genesis coinbase — 50 QZ to dev fund (pre-allocation for faucet)
        coinbase = Transaction.coinbase(b'\x00' * 6, INITIAL_GENESIS_REWARD, 0)
        genesis.transactions = [coinbase]
        genesis.build_header()

        # Mine with low difficulty for fast testnet genesis
        target = 1 << (256 - TESTNET_DIFFICULTY)
        while True:
            header_hash = genesis.header.hash
            header_int = int.from_bytes(header_hash, 'big')
            if header_int < target:
                break
            genesis.header.nonce += 1
            if genesis.header.nonce % 100000 == 0:
                print(f"  ...tried {genesis.header.nonce} nonces")

        self.blocks.append(genesis)
        print(f"✅ Genesis block mined!")
        print(f"   Hash: {genesis.header.hash.hex()[:32]}...")
        print(f"   Nonce: {genesis.header.nonce}")
        print(f"   Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(genesis.header.timestamp))}")
        self.save()

    def mine_block(self, miner_id: bytes, miner_name: str = ""):
        """Mine a new block (simulated ESP32 mining)."""
        height = len(self.blocks)
        prev_block = self.blocks[-1]

        # Calculate reward
        miner_reward = get_miner_reward(height)
        dev_reward = get_dev_fund_reward(height)

        # Early adopter bonus
        if miner_id not in self.known_miners:
            self.known_miners.add(miner_id)
            self.first_mined[miner_id] = time.time()
            miner_reward *= 2  # 2x bonus
            print(f"🎉 New miner joined! {miner_name or miner_id.hex()} (early adopter 2x bonus)")

        # Build block
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_block.header.hash,
            timestamp=int(time.time()),
            difficulty_target=TESTNET_DIFFICULTY,
        )

        # Coinbase transaction
        coinbase = Transaction.coinbase(miner_id, miner_reward + dev_reward, height)
        block = Block(header=header, transactions=[coinbase] + self.mempool[:10])
        block.build_header()

        # Mine (simulated — real ESP32 would do CrystalHash)
        target = 1 << (256 - TESTNET_DIFFICULTY)
        attempts = 0
        while True:
            header_hash = block.header.hash
            header_int = int.from_bytes(header_hash, 'big')
            if header_int < target:
                break
            block.header.nonce += 1
            attempts += 1
            if attempts > 500000:
                # Reset timestamp if taking too long
                block.header.timestamp = int(time.time())
                block.header.nonce = 0
                attempts = 0

        # Update balances
        miner_addr = hashlib.sha256(miner_id).hexdigest()[:34]
        self.balances[miner_addr] = self.balances.get(miner_addr, 0) + miner_reward
        if dev_reward > 0 and self.dev_wallet:
            dev_addr = self.dev_wallet['address']
            self.balances[dev_addr] = self.balances.get(dev_addr, 0) + dev_reward

        self.blocks.append(block)
        self.mempool.clear()

        print(f"⛏️  Block #{height} mined by {miner_name or miner_id.hex()}")
        print(f"   Hash: {block.header.hash.hex()[:16]}...")
        print(f"   Nonce: {block.header.nonce} (attempts: {attempts})")
        print(f"   Reward: {miner_reward / 1e8:.1f} QZ → miner, {dev_reward / 1e8:.1f} QZ → dev fund")
        print(f"   Total supply: {sum(self.balances.values()) / 1e8:.1f} QZ")

        self.save()
        return block

    def get_chain_info(self):
        """Get chain summary for API."""
        total_supply = sum(self.balances.values())
        return {
            "height": len(self.blocks) - 1,
            "best_hash": self.blocks[-1].header.hash.hex() if self.blocks else None,
            "difficulty": TESTNET_DIFFICULTY,
            "block_time": TESTNET_BLOCK_TIME,
            "total_supply": total_supply,
            "total_supply_qz": total_supply / 1e8,
            "max_supply": 42_000_000,
            "circulating_pct": round(total_supply / (42_000_000 * 1e8) * 100, 4),
            "miner_count": len(self.known_miners),
            "mempool_size": len(self.mempool),
            "dev_fund_address": self.dev_wallet['address'] if self.dev_wallet else None,
            "dev_fund_balance": self.balances.get(self.dev_wallet['address'], 0) if self.dev_wallet else 0,
            "dev_fund_balance_qz": self.balances.get(self.dev_wallet['address'], 0) / 1e8 if self.dev_wallet else 0,
        }

    def get_block(self, height: int):
        """Get block data for API."""
        if height < 0 or height >= len(self.blocks):
            return None
        block = self.blocks[height]
        reward = get_block_reward(height)
        return {
            "height": height,
            "hash": block.header.hash.hex(),
            "prev_hash": block.header.prev_block_hash.hex(),
            "merkle_root": block.header.merkle_root.hex(),
            "timestamp": block.header.timestamp,
            "time": time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(block.header.timestamp)),
            "difficulty": block.header.difficulty_target,
            "nonce": block.header.nonce,
            "version": block.header.version,
            "miner_id": block.header.miner_id.hex(),
            "tx_count": len(block.transactions),
            "reward_qz": reward / 1e8,
            "size_bytes": len(block.serialize()),
            "txs": [
                {
                    "txid": tx.txid.hex(),
                    "type": "coinbase" if i == 0 else "transfer",
                    "outputs": [
                        {"amount_sats": amt, "amount_qz": amt / 1e8}
                        for amt, _ in tx.outputs
                    ],
                }
                for i, tx in enumerate(block.transactions)
            ],
        }

    def get_recent_blocks(self, count=10):
        """Get recent blocks for explorer."""
        blocks = []
        start = max(0, len(self.blocks) - count)
        for i in range(len(self.blocks) - 1, start - 1, -1):
            blocks.append(self.get_block(i))
        return blocks

    def get_address(self, address: str):
        """Get address info for explorer."""
        # Find transactions for this address
        txs = []
        for i, block in enumerate(self.blocks):
            for j, tx in enumerate(block.transactions):
                for amount, script in tx.outputs:
                    script_addr = script.hex()[:34]
                    if script_addr == address or address in script.hex():
                        txs.append({
                            "block": i,
                            "txid": tx.txid.hex(),
                            "type": "coinbase" if j == 0 else "transfer",
                            "amount_sats": amount,
                            "amount_qz": amount / 1e8,
                        })
        return {
            "address": address,
            "balance_sats": self.balances.get(address, 0),
            "balance_qz": self.balances.get(address, 0) / 1e8,
            "tx_count": len(txs),
            "transactions": txs[-20:],  # last 20
        }

    def save(self):
        """Persist chain to disk."""
        data = {
            "blocks": [
                {
                    "header": {
                        "version": b.header.version,
                        "prev_block_hash": b.header.prev_block_hash.hex(),
                        "merkle_root": b.header.merkle_root.hex(),
                        "timestamp": b.header.timestamp,
                        "difficulty_target": b.header.difficulty_target,
                        "nonce": b.header.nonce,
                        "miner_id": b.header.miner_id.hex(),
                    },
                    "tx_count": len(b.transactions),
                }
                for b in self.blocks
            ],
            "balances": self.balances,
            "known_miners": [m.hex() for m in self.known_miners],
        }
        with open(CHAIN_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def load(self):
        """Load chain from disk."""
        with open(CHAIN_FILE) as f:
            data = json.load(f)

        for bd in data['blocks']:
            header = BlockHeader(
                version=bd['header']['version'],
                prev_block_hash=bytes.fromhex(bd['header']['prev_block_hash']),
                merkle_root=bytes.fromhex(bd['header']['merkle_root']),
                timestamp=bd['header']['timestamp'],
                difficulty_target=bd['header']['difficulty_target'],
                nonce=bd['header']['nonce'],
                miner_id=bytes.fromhex(bd['header']['miner_id']),
            )
            self.blocks.append(Block(header=header))

        self.balances = data.get('balances', {})
        self.known_miners = set(bytes.fromhex(m) for m in data.get('known_miners', []))
        print(f"📦 Loaded {len(self.blocks)} blocks from disk")


# Genesis pre-allocation for faucet
INITIAL_GENESIS_REWARD = 50 * 10**8  # 50 QZ


class QuartzAPIHandler(BaseHTTPRequestHandler):
    """HTTP API for block explorer and wallets."""

    chain = None  # set by main()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'

        if path == '/' or path == '/api':
            self.json_response({"name": "Quartz Testnet", "version": "0.1.0", "status": "running"})

        elif path == '/api/v1/info':
            self.json_response(self.chain.get_chain_info())

        elif path == '/api/v1/blocks/recent':
            self.json_response(self.chain.get_recent_blocks(20))

        elif path.startswith('/api/v1/block/'):
            try:
                height = int(path.split('/')[-1])
                block = self.chain.get_block(height)
                if block:
                    self.json_response(block)
                else:
                    self.json_error(404, "Block not found")
            except ValueError:
                self.json_error(400, "Invalid block height")

        elif path.startswith('/api/v1/address/'):
            address = path.split('/')[-1]
            self.json_response(self.chain.get_address(address))

        elif path == '/api/v1/dev-fund':
            info = self.chain.get_chain_info()
            self.json_response({
                "address": info['dev_fund_address'],
                "balance_qz": info['dev_fund_balance_qz'],
                "vesting_period": "4 years (~525,600 blocks)",
                "allocation_pct": 5,
                "uses": ["Development", "Bug bounty (50K QZ)", "Ecosystem grants"],
            })

        elif path == '/api/v1/miners':
            miners = []
            for m in DEMO_MINERS:
                addr = hashlib.sha256(m['id']).hexdigest()[:34]
                miners.append({
                    "id": m['id'].hex(),
                    "name": m['name'],
                    "address": addr,
                    "balance_qz": self.chain.balances.get(addr, 0) / 1e8,
                    "early_adopter": m['id'] in self.chain.known_miners,
                })
            self.json_response({"miners": miners, "count": len(miners)})

        else:
            self.json_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/api/v1/faucet':
            # Testnet faucet — send 1 QZ to an address
            content_length = int(self.headers['Content-Length'])
            body = json.loads(self.rfile.read(content_length))
            address = body.get('address')
            if not address:
                self.json_error(400, "Missing address")
                return
            # Add to mempool
            self.chain.mempool.append((
                f"faucet:{address}:{time.time()}",
                1 * 10**8,  # 1 QZ
            ))
            self.json_response({"status": "queued", "amount": "1 QZ", "address": address})

        elif path == '/api/v1/mine':
            # Manual mine trigger (demo)
            miner_idx = int(body.get('miner', 0)) if 'body' in dir() else 0
            miner = DEMO_MINERS[miner_idx % len(DEMO_MINERS)]
            block = self.chain.mine_block(miner['id'], miner['name'])
            self.json_response({"status": "mined", "height": len(self.chain.blocks) - 1})

        else:
            self.json_error(404, "Not found")

    def json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def json_error(self, code, message):
        self.json_response({"error": message}, code)

    def log_message(self, format, *args):
        pass  # suppress default logging


def mining_simulator(chain):
    """Background thread that simulates ESP32 miners finding blocks."""
    miner_idx = 0
    while True:
        time.sleep(TESTNET_BLOCK_TIME)  # 120 seconds
        try:
            miner = DEMO_MINERS[miner_idx % len(DEMO_MINERS)]
            chain.mine_block(miner['id'], miner['name'])
            miner_idx += 1
        except Exception as e:
            print(f"Mining error: {e}")


def main():
    print("=" * 60)
    print("  Quartz Testnet Seed Node v0.1.0")
    print("  https://quartz.preview.saasclaw.ai")
    print("=" * 60)

    chain = QuartzChain()
    QuartzAPIHandler.chain = chain

    # Start mining simulator in background
    miner_thread = threading.Thread(target=mining_simulator, args=(chain,), daemon=True)
    miner_thread.start()
    print(f"\n🚀 Mining simulator started ({len(DEMO_MINERS)} virtual ESP32s)")
    print(f"📡 API: http://localhost:{QUARTZ_PORT}")
    print(f"🔗 Chain height: {len(chain.blocks)}")
    print(f"💰 Total supply: {sum(chain.balances.values()) / 1e8:.1f} QZ")
    print()

    # Start HTTP server
    server = HTTPServer(('127.0.0.1', QUARTZ_PORT), QuartzAPIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")


QUARTZ_PORT = 21100

if __name__ == '__main__':
    main()
