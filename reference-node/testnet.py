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
from urllib.parse import urlparse, parse_qs

# Add parent dir to path
import sys
sys.path.insert(0, os.path.dirname(__file__))

from quartz.blockchain import (
    Block, BlockHeader, Transaction, compute_merkle_root,
    get_block_reward, get_miner_reward, get_dev_fund_reward,
    HEADER_SIZE, BLOCK_TIME, RETARGET_PERIOD,
    retarget_difficulty_bits as _retarget_bits,
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
        self.current_difficulty = TESTNET_DIFFICULTY

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

    def mine_block(self, miner_id: bytes, miner_name: str = "", miner_addr: str = None):
        """Mine a new block (simulated ESP32 mining).

        miner_addr: real wallet address (Qk.../Qw...) to credit the reward to.
        When omitted (simulated fleet), falls back to the synthetic
        sha256(miner_id) address as before.
        """
        height = len(self.blocks)
        prev_block = self.blocks[-1]

        # Check for difficulty retarget
        if height > 0 and height % RETARGET_PERIOD == 0:
            old_diff = self.current_difficulty
            self.current_difficulty = _retarget_bits(
                self.current_difficulty, self.blocks, TESTNET_BLOCK_TIME)
            if self.current_difficulty != old_diff:
                print(f"📐 Difficulty retarget at block {height}: "
                      f"{old_diff} → {self.current_difficulty} bits")

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
            difficulty_target=self.current_difficulty,
        )

        # Coinbase transaction
        coinbase = Transaction.coinbase(miner_id, miner_reward + dev_reward, height)
        block = Block(header=header, transactions=[coinbase] + self.mempool[:10])
        block.build_header()

        # Mine (simulated — real ESP32 would do CrystalHash)
        target = 1 << (256 - self.current_difficulty)
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

        # Update balances — credit the REAL wallet address when the miner
        # provided one (hardware submit sends it). Synthetic sha256 address
        # is only a fallback for the simulated fleet.
        # (Fix: rewards were landing at sha256(addr[:6]) — unspendable.)
        credit_addr = miner_addr or hashlib.sha256(miner_id).hexdigest()[:34]
        self.balances[credit_addr] = self.balances.get(credit_addr, 0) + miner_reward
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
            "difficulty": self.current_difficulty,
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
            "total_hashrate": sum(m.get('hashrate', 0) for m in self._active_miners().values()),
            "hardware_miners": len(self._active_miners()),
        }

    def _active_miners(self, max_age_s=600):
        """Return miner_stats pruned to only recently-seen miners."""
        now = time.time()
        stats = getattr(self, 'miner_stats', {})
        return {k: v for k, v in stats.items()
                if (now - v.get('last_submit', 0)) <= max_age_s}

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
            "current_difficulty": self.current_difficulty,
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
        self.current_difficulty = data.get('current_difficulty', TESTNET_DIFFICULTY)
        print(f"📦 Loaded {len(self.blocks)} blocks from disk (difficulty: {self.current_difficulty})")


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

        elif path == '/api/v1/mining/work':
            # Return block template for ESP32 miners
            height = len(self.chain.blocks)
            prev_block = self.chain.blocks[-1]
            miner_reward = get_miner_reward(height)

            # Track miner from query params or header
            qs = parse_qs(parsed.query)
            miner_addr = qs.get('address', [''])[0]
            hashrate = int(qs.get('hashrate', ['0'])[0])

            if miner_addr:
                miner_id = miner_addr.encode()[:6]
                key = miner_id.hex()
                if not hasattr(self.chain, 'miner_stats'):
                    self.chain.miner_stats = {}
                if key not in self.chain.miner_stats:
                    self.chain.miner_stats[key] = {
                        'address': miner_addr,
                        'hashrate': hashrate or 28,
                        'blocks_found': 0,
                        'last_submit': time.time(),
                        'first_seen': time.time(),
                    }
                else:
                    self.chain.miner_stats[key]['last_submit'] = time.time()
                    if hashrate:
                        self.chain.miner_stats[key]['hashrate'] = hashrate

            header = BlockHeader(
                version=1,
                prev_block_hash=prev_block.header.hash,
                timestamp=int(time.time()),
                difficulty_target=self.chain.current_difficulty,
            )
            header_bytes = struct.pack('<I32s32sIII',
                header.version,
                header.prev_block_hash,
                b'\x00' * 32,
                header.timestamp,
                header.difficulty_target,
                0,
            )

            self.json_response({
                "job_id": f"job_{height}_{int(time.time())}",
                "height": height,
                "header": header_bytes.hex(),
                "target_bits": self.chain.current_difficulty,
                "reward_qz": miner_reward / 1e8,
                "prev_hash": prev_block.header.hash.hex()[:16],
            })

        elif path == '/api/v1/messages':
            # Recent messages from recent blocks + pending
            messages = []

            # Pending messages (not yet in a block)
            if hasattr(self.chain, '_pending_msgs'):
                for msg in self.chain._pending_msgs:
                    messages.append({
                        "txid": msg['txid'],
                        "block": None,
                        "from": msg.get('from', ''),
                        "to": msg.get('to', ''),
                        "data_text": msg['text'],
                        "timestamp": int(msg['timestamp']),
                        "confirmed": False,
                    })

            # Confirmed messages from recent blocks
            for i, block in enumerate(reversed(self.chain.blocks[-50:])):
                block_height = len(self.chain.blocks) - 1 - i
                for tx in block.transactions:
                    if hasattr(tx, 'data') and tx.data and len(tx.data) > 0:
                        messages.append({
                            "txid": tx.txid.hex()[:16],
                            "block": block_height,
                            "data_hex": tx.data.hex(),
                            "data_text": tx.data.decode('utf-8', errors='replace')[:160],
                            "timestamp": block.header.timestamp,
                            "confirmed": True,
                        })

            self.json_response({"messages": messages, "count": len(messages)})

        elif path.startswith('/api/v1/address/'):
            # Check if this is a txs sub-path: /api/v1/address/<addr>/txs
            parts = path.split('/')
            address = parts[4] if len(parts) > 4 else ''

            if len(parts) > 5 and parts[5] == 'txs':
                # Payment check endpoint for ESP32
                # Returns simplified tx list for pay.c polling
                addr_info = self.chain.get_address(address)

                # Check for query params (min_amount)
                qs = parse_qs(parsed.query)
                min_amount = int(qs.get('min_amount', [0])[0])

                matching_txs = []
                for tx in addr_info.get('transactions', []):
                    if tx['amount_sats'] >= min_amount:
                        matching_txs.append({
                            'txid': tx['txid'],
                            'amount': tx['amount_sats'],
                            'confirmations': max(1, len(self.chain.blocks) - tx.get('block', 0)),
                        })

                self.json_response({
                    'address': address,
                    'balance_sats': addr_info['balance_sats'],
                    'txs': matching_txs,
                    'count': len(matching_txs),
                })
            else:
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

        elif path == '/api/v1/miners/active':
            """Real hardware miners with live stats. Stale miners (>10 min) are pruned."""
            stats = getattr(self.chain, 'miner_stats', {})
            now = time.time()
            STALE_TIMEOUT = 600  # 10 minutes
            # Prune stale miners from stats
            stale_keys = [k for k, v in stats.items()
                          if (now - v.get('last_submit', 0)) > STALE_TIMEOUT]
            for k in stale_keys:
                del stats[k]
            miners = []
            for key, v in stats.items():
                miners.append({
                    'id': key,
                    'address': v.get('address', ''),
                    'hashrate': v.get('hashrate', 0),
                    'blocks_found': v.get('blocks_found', 0),
                    'last_submit_ago_s': int(now - v.get('last_submit', 0)),
                    'uptime_s': int(now - v.get('first_seen', now)),
                })
            total_hps = sum(m['hashrate'] for m in miners)
            self.json_response({
                'miners': miners,
                'count': len(miners),
                'total_hashrate': total_hps,
            })

        else:
            self.json_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        content_length = int(self.headers.get('Content-Length', 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except:
                pass

        if path == '/api/v1/messages/send':
            # Send a message via on-chain transaction
            sender = body.get('from', 'anonymous')
            recipient = body.get('to', '')
            text = body.get('text', '')

            if not text:
                self.json_error(400, "Missing text")
                return
            if len(text) > 160:
                self.json_error(400, "Message too long (160 chars max)")
                return

            # Create a transaction with data carrier
            tx = Transaction(
                version=1,
                inputs=[],
                outputs=[],
                data=text.encode('utf-8'),
            )

            # Add transaction to mempool (actual Transaction object)
            self.chain.mempool.append(tx)

            # Store the message transaction directly on next block
            # (simplified: store in mempool and include in next mined block)
            if not hasattr(self.chain, '_pending_msgs'):
                self.chain._pending_msgs = []
            self.chain._pending_msgs.append({
                "txid": tx.txid.hex()[:16],
                "from": sender,
                "to": recipient,
                "text": text,
                "timestamp": time.time(),
            })

            self.json_response({
                "status": "queued",
                "txid": tx.txid.hex()[:16],
                "text": text,
                "confirmations": 0,
                "est_confirm_seconds": 30,
            })

        elif path == '/api/v1/mining/submit':
            # ESP32 miner submitting a found nonce
            job_id = body.get('job_id', '')
            nonce = body.get('nonce', 0)
            header_hex = body.get('header', '')
            miner_addr = body.get('address', '')
            hashrate = body.get('hashrate', 0)
            puf_attestation = body.get('puf_attestation', '')

            # Use wallet address as miner identifier (unique per device)
            if miner_addr:
                miner_id = miner_addr.encode()[:6]
            else:
                miner_id = bytes([0xAA, 0xBB, 0xCC, 0x01, 0x00, 0x06])

            # Track real-time stats per miner
            if not hasattr(self.chain, 'miner_stats'):
                self.chain.miner_stats = {}
            key = miner_id.hex()
            if key not in self.chain.miner_stats:
                self.chain.miner_stats[key] = {
                    'address': miner_addr,
                    'hashrate': hashrate,
                    'blocks_found': 0,
                    'last_submit': time.time(),
                    'first_seen': time.time(),
                }
            self.chain.miner_stats[key]['hashrate'] = hashrate or self.chain.miner_stats[key].get('hashrate', 28)
            self.chain.miner_stats[key]['last_submit'] = time.time()
            self.chain.miner_stats[key]['blocks_found'] = self.chain.miner_stats[key].get('blocks_found', 0) + 1

            # Accept the share and mine a real block
            block = self.chain.mine_block(miner_id, f"ESP32-{job_id[:8]}", miner_addr=miner_addr or None)

            self.json_response({
                "status": "accepted",
                "job_id": job_id,
                "block_height": len(self.chain.blocks) - 1,
                "reward": get_miner_reward(len(self.chain.blocks) - 1) / 1e8,
            })

        elif path == '/api/v1/faucet':
            # Testnet faucet — send test QZ to an address
            # body already parsed at top of do_POST
            address = body.get('address', '').strip()
            if not address:
                self.json_error(400, "Missing address")
                return
            # Credit the address directly in the chain state
            # This is testnet-only — no real signing needed
            if address not in self.chain.balances:
                self.chain.balances[address] = 0
            self.chain.balances[address] += 100 * 10**8  # 100 test QZ
            # Save state
            self.chain.save()
            self.json_response({"status": "ok", "amount": "100 QZ", "address": address})

        elif path == '/api/v1/send':
            # Broadcast a signed transaction
            # Body: { "from": "Qk...", "to": "Qk...", "amount": 1.5, "signature": "hex", "public_key": "hex" }
            from_addr = body.get('from', '')
            to_addr = body.get('to', '')
            amount_qz = float(body.get('amount', 0))
            signature_hex = body.get('signature', '')
            public_key_hex = body.get('public_key', '')
            message_hex = body.get('message', '')  # pre-serialized tx message that was signed

            if not from_addr or not to_addr or amount_qz <= 0:
                self.json_error(400, "Missing from/to/amount")
                return
            if not signature_hex or not public_key_hex:
                self.json_error(400, "Missing signature or public_key")
                return

            amount_sats = int(amount_qz * 1e8)

            # Check sender has enough balance
            sender_balance = self.chain.balances.get(from_addr, 0)
            if sender_balance < amount_sats:
                self.json_error(400, f"Insufficient balance: {sender_balance / 1e8} QZ < {amount_qz} QZ")
                return

            # Verify the signature
            try:
                from quartz.crypto import verify_signature, validate_address, public_key_to_address
                pub_key = bytes.fromhex(public_key_hex)
                sig = bytes.fromhex(signature_hex)
                
                # Verify the public key matches the sender address
                derived_addr = public_key_to_address(pub_key)
                if derived_addr != from_addr:
                    self.json_error(400, "Public key does not match sender address")
                    return
                
                if message_hex:
                    msg = bytes.fromhex(message_hex)
                else:
                    # Reconstruct the message that was signed
                    msg = f"{from_addr}{to_addr}{amount_sats}".encode()

                if not verify_signature(pub_key, msg, sig):
                    self.json_error(400, "Invalid signature")
                    return
            except Exception as e:
                self.json_error(400, f"Signature verification failed: {e}")
                return

            # Move balance immediately (testnet simplified TX)
            self.chain.balances[from_addr] = sender_balance - amount_sats
            if to_addr not in self.chain.balances:
                self.chain.balances[to_addr] = 0
            self.chain.balances[to_addr] += amount_sats

            # Create transaction record
            tx = Transaction(
                version=1,
                inputs=[],
                outputs=[(amount_sats, to_addr.encode()[:32])],
                data=sig + pub_key,
            )

            # Save state
            self.chain.save()

            self.json_response({
                "status": "sent",
                "txid": tx.txid.hex()[:16],
                "from": from_addr,
                "to": to_addr,
                "amount_qz": amount_qz,
                "est_confirm_seconds": 30,
            })

        elif path == '/api/v1/mine':
            # Manual mine trigger (demo)
            miner_idx = int(body.get('miner', 0)) if 'body' in dir() else 0
            miner = DEMO_MINERS[miner_idx % len(DEMO_MINERS)]
            block = self.chain.mine_block(miner['id'], miner['name'])
            self.json_response({"status": "mined", "height": len(self.chain.blocks) - 1})

        elif path == '/api/v1/agent/decide':
            # LLM-backed agent decision endpoint
            # ESP32 POSTs its state, node proxies to LLM, returns action
            self.handle_agent_decide(body)

        else:
            self.json_error(404, "Not found")

    def handle_agent_decide(self, body):
        """
        LLM-backed agent decision endpoint.

        ESP32 sends its state, node proxies to an OpenAI-compatible LLM,
        parses the response, and returns a structured action.

        Request body:
            {
                "device_id": "a4:cf:12:6d:a2:5c",
                "balance": 2.3,           // QZ
                "block_height": 1234,
                "hashrate": 28,           // H/s
                "temperature": 44,       // Celsius
                "voltage_mv": 3300,      // millivolts
                "vibration": 1200,       // raw ADC
                "light": 2048,           // raw ADC
                "mining_active": true,
                "uptime_sec": 3600,
                "blocks_found": 3,
                "relay_state": "idle"
            }

        Response:
            {
                "action": "message|relay|alert|stop_mining|restart_mining|none",
                "params": { ... },
                "reasoning": "LLM explanation"
            }
        """
        import urllib.request

        # Config from environment (set in systemd / .env)
        llm_url = os.environ.get(
            'QUARTZ_LLM_URL',
            'http://localhost:18789/v1/chat/completions'
        )
        llm_key = os.environ.get('QUARTZ_LLM_API_KEY', '')
        llm_model = os.environ.get('QUARTZ_LLM_MODEL', 'openclaw')

        device_state = body

        # Build the system prompt
        system_prompt = (
            "You are the brain of a Quartz mining device (ESP32). "
            "You receive the device's current state and decide what action it should take. "
            "You must respond with ONLY valid JSON, no markdown, no explanation outside JSON.\n\n"
            "Available actions:\n"
            '  {"action": "none"} — do nothing, keep mining\n'
            '  {"action": "relay", "params": {"duration_ms": 3000}} — trigger GPIO relay\n'
            '  {"action": "message", "params": {"text": "hello world"}} — broadcast on-chain message (max 160 chars)\n'
            '  {"action": "alert", "params": {"text": "warning text"}} — show alert on screen\n'
            '  {"action": "stop_mining"} — stop mining\n'
            '  {"action": "restart_mining"} — resume mining\n\n'
            "Rules:\n"
            "- Be conservative. Default to 'none' unless something needs attention.\n"
            "- Temperature above 75°C is concerning. Above 85°C is critical.\n"
            "- Voltage below 3000mV is low. Below 2800mV is critical.\n"
            "- Don't send messages more than once per hour for the same reason.\n"
            "- If the device has mined a block recently, a brief congratulatory message is fine.\n"
            "- Be creative but safe. This is a demo of autonomous device agency.\n"
        )

        user_prompt = (
            f"Device state:\n"
            f"  Device ID: {device_state.get('device_id', 'unknown')}\n"
            f"  Balance: {device_state.get('balance', 0)} QZ\n"
            f"  Block height: {device_state.get('block_height', 0)}\n"
            f"  Hashrate: {device_state.get('hashrate', 0)} H/s\n"
            f"  Temperature: {device_state.get('temperature', 0)}°C\n"
            f"  Voltage: {device_state.get('voltage_mv', 0)} mV\n"
            f"  Vibration: {device_state.get('vibration', 0)}\n"
            f"  Light level: {device_state.get('light', 0)}\n"
            f"  Mining active: {device_state.get('mining_active', True)}\n"
            f"  Uptime: {device_state.get('uptime_sec', 0)} seconds\n"
            f"  Blocks found: {device_state.get('blocks_found', 0)}\n"
            f"  Relay state: {device_state.get('relay_state', 'idle')}\n\n"
            "What should this device do? Respond with JSON only."
        )

        # Call the LLM
        llm_payload = {
            "model": llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 200,
        }

        headers = {"Content-Type": "application/json"}
        if llm_key:
            headers["Authorization"] = f"Bearer {llm_key}"

        try:
            req = urllib.request.Request(
                llm_url,
                data=json.dumps(llm_payload).encode(),
                headers=headers,
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                llm_result = json.loads(resp.read())

            # Extract the text from OpenAI-compatible response
            content = llm_result.get('choices', [{}])[0].get(
                'message', {}
            ).get('content', '').strip()

            # Parse JSON from LLM response (handle markdown fences)
            if content.startswith('```'):
                # Strip markdown code fences
                lines = content.split('\n')
                lines = [l for l in lines if not l.startswith('```')]
                content = '\n'.join(lines)

            try:
                action = json.loads(content)
            except json.JSONDecodeError:
                # LLM didn't return clean JSON — return none with reasoning
                action = {
                    "action": "none",
                    "reasoning": f"LLM returned non-JSON: {content[:100]}",
                }

            self.json_response({
                "action": action.get("action", "none"),
                "params": action.get("params", {}),
                "reasoning": action.get("reasoning", content[:200]),
            })

        except urllib.error.URLError as e:
            self.json_response({
                "action": "none",
                "params": {},
                "reasoning": f"LLM unavailable: {str(e)[:100]}",
            })
        except Exception as e:
            self.json_response({
                "action": "none",
                "params": {},
                "reasoning": f"Error: {str(e)[:100]}",
            })

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
    server = HTTPServer(('0.0.0.0', QUARTZ_PORT), QuartzAPIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")


QUARTZ_PORT = 21100

if __name__ == '__main__':
    main()
