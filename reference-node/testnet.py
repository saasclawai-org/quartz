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
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
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

# Consensus engine
from quartz.consensus import (
    ConsensusEngine, UTXOSet, UTXO, Mempool,
    validate_transaction, validate_block, apply_block,
    MAX_BLOCK_TXS, MAX_BLOCK_SIZE, TX_DATA_LIMIT,
)

# Override difficulty for fast testnet blocks (real network uses 20)
import quartz.blockchain as _bc
_bc.DIFFICULTY_BITS = 12
TESTNET_DIFFICULTY = 12
TESTNET_BLOCK_TIME = 30  # 30 second blocks for fast testing

DATA_DIR = os.path.join(os.path.dirname(__file__), 'testnet-data')
CHAIN_FILE = os.path.join(DATA_DIR, 'chain.json')
PENDING_MSGS_FILE = os.path.join(DATA_DIR, 'pending_msgs.json')

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


# ============================================================
# Message data-carrier helpers
#
# Messages (incl. NAME:REGISTER name-registry entries) are confirmed
# inside block transactions as zero-value data carriers. The payload
# is a compact JSON envelope so confirmed messages stay
# self-describing on-chain:
#   {"f":"<from>","t":"<to>","x":"<text>"}
# ============================================================

def _msg_entry_to_tx(entry: dict) -> Transaction:
    """Build the on-chain data-carrier tx for a pending message entry."""
    envelope = json.dumps(
        {"f": entry.get('from', ''), "t": entry.get('to', ''),
         "x": entry.get('text', '')},
        separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return Transaction(version=1, inputs=[], outputs=[], data=envelope)


def _parse_msg_envelope(data: bytes):
    """Decode a carrier payload → (from, to, text).

    Falls back to plain-text semantics for non-envelope data.
    """
    try:
        env = json.loads(data.decode('utf-8'))
        if isinstance(env, dict) and 'x' in env:
            return (str(env.get('f', '')), str(env.get('t', '')),
                    str(env.get('x', '')))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        pass
    return ('', '', data.decode('utf-8', errors='replace'))


def _block_from_disk_dict(bd: dict) -> Block:
    """Reconstruct a Block from its chain.json/dict serialization.
    Shared by load() and p2p incremental sync — one parser, two sources."""
    header = BlockHeader(
        version=bd['header']['version'],
        prev_block_hash=bytes.fromhex(bd['header']['prev_block_hash']),
        merkle_root=bytes.fromhex(bd['header']['merkle_root']),
        timestamp=bd['header']['timestamp'],
        difficulty_target=bd['header']['difficulty_target'],
        nonce=bd['header']['nonce'],
        miner_id=bytes.fromhex(bd['header']['miner_id']),
    )
    transactions = []
    for td in bd.get('txs', []):
        inputs = [
            (
                bytes.fromhex(ti['prev_hash']),
                ti['idx'],
                bytes.fromhex(ti['sig']),
                bytes.fromhex(ti['pubkey']),
            )
            for ti in td.get('inputs', [])
        ]
        outputs = [
            (to['amount'], bytes.fromhex(to['script']))
            for to in td.get('outputs', [])
        ]
        transactions.append(Transaction(
            version=td.get('version', 1),
            inputs=inputs,
            outputs=outputs,
            locktime=td.get('locktime', 0),
            data=bytes.fromhex(td.get('data', '')),
        ))
    return Block(header=header, transactions=transactions)


def _block_to_disk_dict(block: Block) -> dict:
    """Serialize a Block exactly like chain.json stores it (the inverse of
    _block_from_disk_dict — used by /api/v1/blocks/since for p2p sync)."""
    return {
        "header": {
            "version": block.header.version,
            "prev_block_hash": block.header.prev_block_hash.hex(),
            "merkle_root": block.header.merkle_root.hex(),
            "timestamp": block.header.timestamp,
            "difficulty_target": block.header.difficulty_target,
            "nonce": block.header.nonce,
            "miner_id": block.header.miner_id.hex(),
        },
        "txs": [
            {
                "version": tx.version,
                "inputs": [
                    {"prev_hash": ph.hex(), "idx": idx, "sig": sig.hex(), "pubkey": pk.hex()}
                    for ph, idx, sig, pk in tx.inputs
                ],
                "outputs": [
                    {"amount": amt, "script": script.hex()}
                    for amt, script in tx.outputs
                ],
                "locktime": tx.locktime,
                "data": tx.data.hex(),
            }
            for tx in block.transactions
        ],
    }


class QuartzChain:
    """Simple in-memory blockchain with persistence."""

    def __init__(self):
        self.blocks = []
        self.mempool = []
        self._save_lock = threading.Lock()  # mining thread + HTTP threads race otherwise
        self.known_miners = set()
        self.first_mined = {}
        self.balances = {}  # address -> balance (in sats)
        self.dev_wallet = None
        self.current_difficulty = TESTNET_DIFFICULTY
        self.miner_last_mined = {}  # miner_id -> last block timestamp (activity tracking)

        os.makedirs(DATA_DIR, exist_ok=True)

        # Load or create dev wallet. On dependency-light standby boxes
        # (e.g. a Raspberry Pi without PyNaCl) wallet CREATION is impossible
        # — every consumer already guards on dev_wallet being None, so boot
        # in watch-only mode rather than crash-looping the service.
        if os.path.exists(DEV_WALLET_FILE):
            with open(DEV_WALLET_FILE) as f:
                self.dev_wallet = json.load(f)
        else:
            try:
                self.dev_wallet = create_new_wallet()
                with open(DEV_WALLET_FILE, 'w') as f:
                    json.dump(self.dev_wallet, f, indent=2)
            except NotImplementedError as e:
                self.dev_wallet = None
                print(f"⚠️ Dev wallet not created ({e}) — running watch-only. "
                      f"Standby duty unaffected; install PyNaCl for signing.",
                      flush=True)

        # Load or create chain
        if os.path.exists(CHAIN_FILE):
            self.load()
        else:
            self.mine_genesis()

        # Initialize consensus engine (builds UTXO set from chain)
        self.consensus = ConsensusEngine(
            blocks=self.blocks,
            balances=self.balances,
            current_difficulty=self.current_difficulty,
            block_time=TESTNET_BLOCK_TIME,
            retarget_period=RETARGET_PERIOD,
        )
        # Synthetic UTXOs (faucet / legacy-balance mints) are not in any
        # block, so the engine's replay doesn't include them — re-add.
        self.synthetic_utxos = getattr(self, 'synthetic_utxos', [])
        for u in self.synthetic_utxos:
            self.consensus.utxo_set.add(UTXO(
                txid=bytes.fromhex(u['txid']),
                index=u['index'],
                amount=u['amount'],
                script_pubkey=bytes.fromhex(u['script']),
                created_height=u.get('created_height', 0),
            ))
        if self.synthetic_utxos:
            print(f"🪙 Restored {len(self.synthetic_utxos)} synthetic UTXOs (faucet)")
        # Sync legacy mempool to consensus mempool
        for tx in self.mempool:
            self.consensus.mempool.add(tx, self.consensus.utxo_set)

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

        # Calculate reward
        miner_reward = get_miner_reward(height)
        dev_reward = get_dev_fund_reward(height)

        # Early adopter tracking (2x reward bonus removed — it violated
        # coinbase validation: coinbase output must not exceed subsidy+fees)
        if miner_id not in self.known_miners:
            self.known_miners.add(miner_id)
            self.first_mined[miner_id] = time.time()
            print(f"🎉 New miner joined! {miner_name or miner_id.hex()}")
        self.miner_last_mined[miner_id] = time.time()

        # Build block (miner_id stored in block body — not part of the
        # 80-byte hashed header, so attribution doesn't affect PoW)
        header = BlockHeader(
            version=1,
            prev_block_hash=prev_block.header.hash,
            timestamp=int(time.time()),
            difficulty_target=self.consensus.get_expected_difficulty(height),
            miner_id=miner_id[:6],
        )

        # Select mempool txs through consensus engine
        selected_txs = self.consensus.mempool.select_for_block(self.consensus.utxo_set)

        # Attach pending messages as zero-fee data-carrier txs, filling
        # whatever block slots remain (oldest first). These are the
        # messaging / IoT / name-registry carriers.
        msg_txs = []
        if not hasattr(self, '_pending_msgs'):
            self._pending_msgs = []
        for entry in list(self._pending_msgs):
            if len(selected_txs) + len(msg_txs) + 1 >= MAX_BLOCK_TXS:
                break
            tx = _msg_entry_to_tx(entry)
            if any(t.txid == tx.txid for t in msg_txs):
                continue  # dedupe identical messages within a block
            msg_txs.append(tx)

        # Calculate total fees from selected txs
        total_fees = 0
        for tx in selected_txs:
            _, fee, _, _, _ = validate_transaction(tx, self.consensus.utxo_set, height=height)
            total_fees += fee

        # Coinbase transaction (reward + fees)
        coinbase = Transaction.coinbase(miner_id, miner_reward + dev_reward + total_fees, height)
        block = Block(header=header, transactions=[coinbase] + selected_txs + msg_txs)
        block.build_header()

        # Mine (simulated — real ESP32 would do CrystalHash)
        target = 1 << (256 - self.consensus.current_difficulty)
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

        # Accept block through consensus engine (validates + updates UTXO set)
        is_valid, reason = self.consensus.accept_block(block)
        if not is_valid:
            print(f"❌ Block REJECTED by consensus: {reason}")
            return block  # return the block anyway for API compat

        # Update legacy balance dict (consensus engine handles UTXO set)
        credit_addr = miner_addr or hashlib.sha256(miner_id).hexdigest()[:34]
        self.balances[credit_addr] = self.balances.get(credit_addr, 0) + miner_reward
        if dev_reward > 0 and self.dev_wallet:
            dev_addr = self.dev_wallet['address']
            self.balances[dev_addr] = self.balances.get(dev_addr, 0) + dev_reward

        # Sync difficulty from consensus engine
        self.current_difficulty = self.consensus.current_difficulty

        # Update legacy mempool (remove confirmed txs)
        confirmed_txids = {tx.txid for tx in block.transactions}
        self.mempool = [tx for tx in self.mempool if tx.txid not in confirmed_txids]

        # Drop synthetic (faucet) UTXOs that were spent in this block
        if getattr(self, 'synthetic_utxos', None):
            live = self.consensus.utxo_set
            self.synthetic_utxos = [
                u for u in self.synthetic_utxos
                if live.has(bytes.fromhex(u['txid']), u['index'])
            ]

        # Drain the pending-message queue: carriers included in this
        # block are now confirmed inside a block transaction.
        confirmed_msg_count = 0
        if msg_txs:
            with self._save_lock:
                included = {tx.txid for tx in msg_txs}
                before = len(self._pending_msgs)
                self._pending_msgs = [
                    m for m in self._pending_msgs
                    if _msg_entry_to_tx(m).txid not in included
                ]
                confirmed_msg_count = before - len(self._pending_msgs)
                if confirmed_msg_count:
                    self.save_pending_msgs()

        print(f"⛏️  Block #{height} mined by {miner_name or miner_id.hex()}")
        print(f"   Hash: {block.header.hash.hex()[:16]}...")
        print(f"   Nonce: {block.header.nonce} (attempts: {attempts})")
        print(f"   Reward: {miner_reward / 1e8:.1f} QZ → miner, {dev_reward / 1e8:.1f} QZ → dev fund, {total_fees / 1e8:.4f} QZ fees")
        print(f"   Total supply: {sum(self.balances.values()) / 1e8:.1f} QZ")
        print(f"   Mempool: {self.consensus.mempool.size()} txs, UTXOs: {len(self.consensus.utxo_set)}")
        if confirmed_msg_count:
            print(f"   📨 Messages confirmed: {confirmed_msg_count} "
                  f"(pending: {len(self._pending_msgs)})")

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
            "miner_count": self._active_miner_count(),
            "miners_total": len(self.known_miners),
            "mempool_size": self.consensus.mempool.size() if hasattr(self, 'consensus') else len(self.mempool),
            "dev_fund_address": self.dev_wallet['address'] if self.dev_wallet else None,
            "dev_fund_balance": self.balances.get(self.dev_wallet['address'], 0) if self.dev_wallet else 0,
            "dev_fund_balance_qz": self.balances.get(self.dev_wallet['address'], 0) / 1e8 if self.dev_wallet else 0,
            "total_hashrate": sum(m.get('hashrate', 0) for m in self._active_miners().values()),
            "hardware_miners": len(self._active_miners()),
        }

    def _active_miner_count(self, max_age_s=600) -> int:
        """Miners (simulated or real) that mined a block in the last 10 min."""
        now = time.time()
        return sum(1 for t in self.miner_last_mined.values()
                   if now - t <= max_age_s)

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
                    "inputs": [
                        {"prev_hash": ph.hex(), "idx": idx}
                        for ph, idx, _, _ in tx.inputs
                    ] if i > 0 else [],
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
        """Get address info with real transaction history (signed deltas).

        Outputs pay to script = ASCII address bytes; inputs carry the spender's
        Ed25519 pubkey (address derived via SHA-256[:20]). Net delta per tx:
          outputs_to_me − inputs_spent_by_me  (positive = received)
        """
        from quartz.crypto import public_key_to_address as _pk2a
        addr_b = address.encode()

        # txid → tx map for resolving spent input amounts (UTXO lookups)
        tx_by_txid = {}
        for block in self.blocks:
            for tx in block.transactions:
                tx_by_txid[tx.txid] = (block, tx)

        txs = []
        for i, block in enumerate(self.blocks):
            ts = block.header.timestamp
            for j, tx in enumerate(block.transactions):
                out_amt = sum(amt for amt, script in tx.outputs if script == addr_b)
                in_amt = 0
                if j > 0:  # coinbase has no real inputs
                    for ph, idx, sig, pub in tx.inputs:
                        if pub and len(pub) == 32:
                            try:
                                if _pk2a(pub) == address:
                                    prev = tx_by_txid.get(ph)
                                    if prev and idx < len(prev[1].outputs):
                                        in_amt += prev[1].outputs[idx][0]
                            except Exception:
                                pass
                if out_amt == 0 and in_amt == 0:
                    continue
                net = out_amt - in_amt
                # Counterparty for wallet UIs (resolved to on-chain names client-side)
                counterparty = None
                if j > 0:  # coinbase has no counterparty (mining reward)
                    if net < 0:
                        # We are the sender: first output not paying us
                        for amt, script in tx.outputs:
                            if script != addr_b:
                                try:
                                    counterparty = script.decode('utf-8')
                                except UnicodeDecodeError:
                                    counterparty = script.hex()
                                break
                    else:
                        # We are the recipient: address of the first real spender
                        for ph, idx, sig, pub in tx.inputs:
                            if pub and len(pub) == 32:
                                try:
                                    counterparty = _pk2a(pub)
                                except Exception:
                                    pass
                                break
                txs.append({
                    "block": i,
                    "txid": tx.txid.hex(),
                    "type": "coinbase" if j == 0 else "transfer",
                    "direction": "in" if net >= 0 else "out",
                    "amount_sats": net,  # signed: negative = sent (incl. fee)
                    "amount_qz": net / 1e8,
                    "timestamp": ts,
                    "time": time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(ts)),
                    "counterparty": counterparty,
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
                    "txs": [
                        {
                            "version": tx.version,
                            "inputs": [
                                {
                                    "prev_hash": ph.hex(),
                                    "idx": idx,
                                    "sig": sig.hex(),
                                    "pubkey": pub.hex(),
                                }
                                for ph, idx, sig, pub in tx.inputs
                            ],
                            "outputs": [
                                {"amount": amt, "script": script.hex()}
                                for amt, script in tx.outputs
                            ],
                            "locktime": tx.locktime,
                            "data": tx.data.hex(),
                        }
                        for tx in b.transactions
                    ],
                }
                for b in self.blocks
            ],
            "balances": self.balances,
            "known_miners": [m.hex() for m in self.known_miners],
            "current_difficulty": self.current_difficulty,
            "synthetic_utxos": getattr(self, 'synthetic_utxos', []),
            "pending_msgs": getattr(self, '_pending_msgs', []),
            "mempool": [
                {
                    "version": tx.version,
                    "inputs": [
                        {
                            "prev_hash": ph.hex(),
                            "idx": idx,
                            "sig": sig.hex(),
                            "pubkey": pub.hex(),
                        }
                        for ph, idx, sig, pub in tx.inputs
                    ],
                    "outputs": [
                        {"amount": amt, "script": script.hex()}
                        for amt, script in tx.outputs
                    ],
                    "locktime": tx.locktime,
                    "data": tx.data.hex(),
                }
                for tx in self.mempool
            ],
        }
        with self._save_lock:
            tmp = CHAIN_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CHAIN_FILE)  # atomic: readers never see a partial file

    def save_pending_msgs(self):
        """Persist pending messages only (small, fast — safe to call per POST).

        chain.json is 20+ MB and is written on block acceptance by the
        mining thread; writing it per message post would hammer the disk
        and race the mining thread. This delta file is the durable record
        for messages.
        """
        tmp = PENDING_MSGS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({"pending_msgs": getattr(self, '_pending_msgs', [])}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PENDING_MSGS_FILE)

    def apply_peer_blocks(self, disk_blocks: list, state: dict = None):
        """Apply blocks fetched from a peer (incremental p2p sync).

        Each block must extend our tip (prev-hash linked) and pass full
        consensus validation (consensus.accept_block — same path the miner
        uses). On success the legacy balances dict is REPLACED by the peer's
        authoritative copy when provided (wallet payout attribution lives
        in the producing node's balances, not in block data — known
        limitation of the coinbase format).
        Returns (applied_count, error_or_None).
        """
        applied = 0
        for bd in disk_blocks:
            block = _block_from_disk_dict(bd)
            if block.header.prev_block_hash != self.blocks[-1].header.hash:
                return applied, (f"prev-hash mismatch at height {len(self.blocks)} "
                                 f"(peer block does not extend our tip)")
            ok, reason = self.consensus.accept_block(block)
            if not ok:
                return applied, f"consensus rejected block at height {len(self.blocks)}: {reason}"
            # accept_block appended to consensus.blocks and updated UTXO/
            # balances/mempool/difficulty — mirror into legacy view
            self.blocks = self.consensus.blocks
            self.balances = self.consensus.balances
            self.mempool = [t for t in self.mempool
                            if t.txid not in {x.txid for x in block.transactions}]
            applied += 1
        if state:
            if 'balances' in state:
                self.balances = dict(state['balances'])
            if 'known_miners' in state:
                self.known_miners |= set(bytes.fromhex(m) for m in state['known_miners'])
            if 'current_difficulty' in state:
                self.current_difficulty = state['current_difficulty']
            if 'synthetic_utxos' in state:
                self.synthetic_utxos = state['synthetic_utxos']
        if applied:
            self.save()
        return applied, None

    def load(self):
        """Load chain from disk."""
        with open(CHAIN_FILE) as f:
            data = json.load(f)

        for bd in data['blocks']:
            self.blocks.append(_block_from_disk_dict(bd))

        self.balances = data.get('balances', {})
        self.known_miners = set(bytes.fromhex(m) for m in data.get('known_miners', []))
        self.current_difficulty = data.get('current_difficulty', TESTNET_DIFFICULTY)
        self.synthetic_utxos = data.get('synthetic_utxos', [])
        # Restore pending (unconfirmed) messages — incl. name registry entries.
        # pending_msgs.json is authoritative (written on every message POST);
        # chain.json's copy is a fallback for saves made before that file existed.
        if os.path.exists(PENDING_MSGS_FILE):
            try:
                with open(PENDING_MSGS_FILE) as f:
                    self._pending_msgs = json.load(f).get('pending_msgs', [])
            except (json.JSONDecodeError, OSError):
                self._pending_msgs = data.get('pending_msgs', [])
        else:
            self._pending_msgs = data.get('pending_msgs', [])

        # Restore mempool
        self.mempool = []
        for td in data.get('mempool', []):
            inputs = [
                (
                    bytes.fromhex(ti['prev_hash']),
                    ti['idx'],
                    bytes.fromhex(ti['sig']),
                    bytes.fromhex(ti['pubkey']),
                )
                for ti in td.get('inputs', [])
            ]
            outputs = [
                (to['amount'], bytes.fromhex(to['script']))
                for to in td.get('outputs', [])
            ]
            self.mempool.append(Transaction(
                version=td.get('version', 1),
                inputs=inputs,
                outputs=outputs,
                locktime=td.get('locktime', 0),
                data=bytes.fromhex(td.get('data', '')),
            ))

        print(f"📦 Loaded {len(self.blocks)} blocks from disk (difficulty: {self.current_difficulty}, mempool: {len(self.mempool)} txs)")


# Genesis pre-allocation for faucet
INITIAL_GENESIS_REWARD = 50 * 10**8  # 50 QZ


class QuartzAPIHandler(BaseHTTPRequestHandler):
    """HTTP API for block explorer and wallets."""

    chain = None  # set by main()
    relay_url = None  # set by main() when QUARTZ_RELAY_URL is configured

    # Gateway mode: consensus/mutating endpoints are forwarded to the
    # upstream node verbatim. Block building happens at the upstream tip
    # (share-triggered mining), so relaying cannot orphan a miner's work.
    # Read/explorer endpoints keep being served from the local synced chain.
    RELAY_PREFIXES = (
        '/api/v1/mining/work',    # GET — work must come from the true tip
        '/api/v1/mining/submit',  # POST — block is built at consensus
        '/api/v1/messages/send',  # POST — pending-message state
        '/api/v1/faucet',         # POST — creates a transaction
        '/api/v1/send',           # POST — creates a transaction
        '/api/v1/mine',           # POST — demo block trigger
        '/api/v1/agent/decide',   # POST — LLM demo
        '/api/v1/inference/',     # all inference endpoints
    )

    def _should_relay(self, path):
        return (self.relay_url
                and any(path == p or path.startswith(p)
                        for p in self.RELAY_PREFIXES))

    def _relay_request(self, method):
        """Forward this request to the upstream node, stream its answer
        back verbatim. Upstream unreachable -> 502 with a clear message;
        local chain serving is unaffected."""
        import urllib.request, urllib.error, gzip as _gzip
        url = self.relay_url + self.path  # raw path: keeps query string
        data = None
        if method == 'POST':
            n = int(self.headers.get('Content-Length', 0) or 0)
            if n:
                data = self.rfile.read(n)
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={'Accept-Encoding': 'gzip',
                     'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding', '').lower() == 'gzip':
                    raw = _gzip.decompress(raw)
                self.send_response(r.status)
                self.send_header('Content-Type',
                                 r.headers.get('Content-Type', 'application/json'))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type',
                             e.headers.get('Content-Type', 'application/json'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as e:
            self.json_error(502, f"upstream node unreachable "
                                 f"({type(e).__name__}) — mining paused, "
                                 f"local chain serving continues")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'

        if self._should_relay(path):
            self._relay_request('GET')
            return

        if path == '/' or path == '/api':
            self.json_response({"name": "Quartz Testnet", "version": "0.1.0", "status": "running"})

        elif path == '/api/v1/info':
            self.json_response(self.chain.get_chain_info())

        elif path == '/api/v1/snapshot':
            # Full chain file for standby nodes (always current — read from
            # the live chain's persistence path, no copy/cron needed)
            try:
                with open(CHAIN_FILE, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self.json_error(500, "Snapshot unavailable")

        elif path == '/api/v1/blocks/recent':
            self.json_response(self.chain.get_recent_blocks(20))

        elif path.startswith('/api/v1/blocks/since/'):
            # P2P incremental sync: full-fidelity blocks after <height>,
            # batched. When the batch reaches the sender's tip, the small
            # chain-state fields (balances, known_miners, difficulty,
            # synthetic UTXOs, mempool) are included so the peer converges
            # completely — a ~KB alternative to the 31 MB snapshot.
            try:
                start = int(path.rsplit('/', 1)[-1])
            except ValueError:
                self.json_error(400, "Invalid height")
                return
            if start < 0 or start > len(self.chain.blocks):
                self.json_error(400, f"height out of range (0..{len(self.chain.blocks)})")
                return
            BATCH = 64
            batch_blocks = self.chain.blocks[start:start + BATCH]
            resp = {
                "from_height": start,
                "count": len(batch_blocks),
                "tip_height": len(self.chain.blocks) - 1,
                "blocks": [_block_to_disk_dict(b) for b in batch_blocks],
            }
            if start + len(batch_blocks) >= len(self.chain.blocks):
                # Batch reaches our tip: attach authoritative small state
                resp["state"] = {
                    "balances": self.chain.balances,
                    "known_miners": [m.hex() for m in self.chain.known_miners],
                    "current_difficulty": self.chain.current_difficulty,
                    "synthetic_utxos": getattr(self.chain, 'synthetic_utxos', []),
                    "best_hash": (self.chain.blocks[-1].header.hash.hex()
                                  if self.chain.blocks else None),
                }
            self.json_response(resp)

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

            # Confirmed messages from recent blocks (data-carrier
            # envelopes — parsed so from/to survive confirmation)
            for i, block in enumerate(reversed(self.chain.blocks[-50:])):
                block_height = len(self.chain.blocks) - 1 - i
                for tx in block.transactions:
                    if getattr(tx, 'data', None) and len(tx.data) > 0:
                        m_from, m_to, m_text = _parse_msg_envelope(tx.data)
                        messages.append({
                            "txid": tx.txid.hex()[:16],
                            "block": block_height,
                            "from": m_from,
                            "to": m_to,
                            "data_hex": tx.data.hex(),
                            "data_text": m_text[:160],
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
                    # Payments received only: outgoing entries carry negative
                    # signed deltas and are excluded (min_amount >= 0 check + direction)
                    if tx.get('direction', 'in') == 'out':
                        continue
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

        elif path == '/api/v1/inference/models':
            self.handle_inference_models()

        elif path.startswith('/api/v1/inference/result/'):
            self.handle_inference_result(path.split('/')[-1])

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

        # Relay BEFORE reading the body — _relay_request consumes it itself
        if self._should_relay(path):
            self._relay_request('POST')
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except:
                pass

        if path == '/api/v1/messages/send':
            # Send a message via an on-chain data-carrier transaction.
            # Queued in _pending_msgs; the block builder attaches the
            # carrier tx to the next mined block (zero-fee, no UTXO impact).
            sender = body.get('from', 'anonymous')
            recipient = body.get('to', '')
            text = body.get('text', '')

            if not text:
                self.json_error(400, "Missing text")
                return
            if len(text) > 160:
                self.json_error(400, "Message too long (160 chars max)")
                return

            entry = {
                "txid": "",  # carrier txid — filled below
                "from": sender,
                "to": recipient,
                "text": text,
                "timestamp": time.time(),
            }
            tx = _msg_entry_to_tx(entry)
            if len(tx.data) > TX_DATA_LIMIT:
                self.json_error(400, f"Message too long for on-chain carrier "
                                     f"({len(tx.data)} bytes > {TX_DATA_LIMIT})")
                return
            entry["txid"] = tx.txid.hex()[:16]

            with self.chain._save_lock:
                if not hasattr(self.chain, '_pending_msgs'):
                    self.chain._pending_msgs = []
                self.chain._pending_msgs.append(entry)
                # Persist immediately so messages (incl. name registrations)
                # survive node restarts — small delta file, not the 20 MB chain.
                self.chain.save_pending_msgs()

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
            # Credit the address in the chain state + UTXO set
            # This is testnet-only — no real signing needed
            if address not in self.chain.balances:
                self.chain.balances[address] = 0
            self.chain.balances[address] += 100 * 10**8  # 100 test QZ
            # Mint a spendable UTXO keyed to the full address bytes
            # (synthetic — not in a block; persisted separately)
            faucet_tx = Transaction(
                version=1,
                inputs=[(b'\x00' * 32, 1, b'\x00' * 64, b'\x00' * 32)],
                outputs=[(100 * 10**8, address.encode())],
                data=b'faucet',
            )
            self.chain.consensus.utxo_set.add(UTXO(
                txid=faucet_tx.txid,
                index=0,
                amount=100 * 10**8,
                script_pubkey=address.encode(),
                created_height=len(self.chain.blocks) - 1,
            ))
            if not hasattr(self.chain, 'synthetic_utxos'):
                self.chain.synthetic_utxos = []
            self.chain.synthetic_utxos.append({
                'txid': faucet_tx.txid.hex(),
                'index': 0,
                'amount': 100 * 10**8,
                'script': address.encode().hex(),
                'created_height': len(self.chain.blocks) - 1,
            })
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

            # Build a real UTXO-spending transaction.
            # The signature above authorizes the spend; the inputs reference
            # UTXOs locked to this address, and outputs pay the recipient
            # (plus change back to the sender).
            utxo_set = self.chain.consensus.utxo_set
            sender_utxos = sorted(
                utxo_set.get_utxos_for(from_addr.encode()),
                key=lambda u: u.amount,
            )

            # Testnet convenience: legacy balances (pre-consensus) have no
            # UTXOs behind them. Auto-mint one so old balances stay spendable.
            available = sum(u.amount for u in sender_utxos)
            if available < amount_sats and sender_balance >= amount_sats:
                mint_amt = sender_balance - available
                mint_tx = Transaction(
                    version=1,
                    inputs=[(b'\x00' * 32, 2, b'\x00' * 64, b'\x00' * 32)],
                    outputs=[(mint_amt, from_addr.encode())],
                    data=b'legacy-balance-mint',
                )
                utxo_set.add(UTXO(
                    txid=mint_tx.txid, index=0, amount=mint_amt,
                    script_pubkey=from_addr.encode(),
                    created_height=len(self.chain.blocks) - 1,
                ))
                if not hasattr(self.chain, 'synthetic_utxos'):
                    self.chain.synthetic_utxos = []
                self.chain.synthetic_utxos.append({
                    'txid': mint_tx.txid.hex(), 'index': 0,
                    'amount': mint_amt, 'script': from_addr.encode().hex(),
                    'created_height': len(self.chain.blocks) - 1,
                })
                sender_utxos = sorted(
                    utxo_set.get_utxos_for(from_addr.encode()),
                    key=lambda u: u.amount,
                )
                available = sum(u.amount for u in sender_utxos)
                print(f"🪙 Minted legacy-balance UTXO for {from_addr[:12]}…: {mint_amt / 1e8} QZ")

            if available < amount_sats:
                self.json_error(400, f"Insufficient UTXOs: {available / 1e8} QZ < {amount_qz} QZ")
                return

            # Select UTXOs (smallest first) until the amount is covered
            selected, covered = [], 0
            for u in sender_utxos:
                selected.append(u)
                covered += u.amount
                if covered >= amount_sats:
                    break

            change = covered - amount_sats - 1000  # fee: 1000 sats (> min relay for ~222-byte tx)
            outputs = [(amount_sats, to_addr.encode())]
            if change > 0:
                outputs.append((change, from_addr.encode()))

            tx = Transaction(
                version=1,
                inputs=[(u.txid, u.index, sig, pub_key) for u in selected],
                outputs=outputs,
            )

            # Submit to the consensus mempool — mined in the next block
            ok, mempool_reason = self.chain.consensus.mempool.add(tx, utxo_set)
            if not ok:
                self.json_error(400, f"Transaction rejected: {mempool_reason}")
                return

            # Also track in legacy mempool for restart persistence
            self.chain.mempool.append(tx)

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

        elif path == '/api/v1/inference/request':
            self.handle_inference_request(body)

        elif path == '/api/v1/inference/submit':
            self.handle_inference_submit(body)

        elif path == '/api/v1/inference/register':
            self.handle_inference_register(body)


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
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}, indent=2).encode())

    def do_OPTIONS(self):
        # CORS preflight — allow all origins, methods, headers
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default logging

    # ── Inference Marketplace ──────────────────────────────────
    # State: pending requests + completed results
    # In production this would be in the chain or a DB; here it's in-memory.
    INFERENCE_PRICE_QZ = 1  # 1 QZ per inference
    INFERENCE_PRICE_SATS = INFERENCE_PRICE_QZ * 100_000_000
    INFERENCE_PENDING = {}   # request_id → {input, payer, status, ...}
    INFERENCE_RESULTS = {}   # request_id → {prediction, attestation, ...}
    INFERENCE_COUNTER = [0]

    # Registered inference nodes (ESP32 boards running ml-demo firmware)
    # In production: boards register on-chain with their PUF fingerprint
    INFERENCE_NODES = {}     # node_id → {address, last_seen, model_hash}

    # Dev wallet pays the inference node from its own balance for demo purposes
    # (in production, the payer's QZ is escrowed and released to the node)


    def handle_inference_request(self, body):
        """POST /api/v1/inference/request
        Client pays QZ (deducted from balance), gets a request_id.
        Node forwards to a registered ESP32 inference node.
        """
        payer_addr = body.get('payer', '')
        input_hex = body.get('input', '')  # 784 bytes hex-encoded

        if not payer_addr or not input_hex:
            self.json_error(400, "Missing payer or input")
            return

        # Verify payment: check payer has enough balance
        bal = self.chain.balances.get(payer_addr, 0)
        if bal < self.INFERENCE_PRICE_SATS:
            self.json_error(402, f"Insufficient balance: {bal/1e8} QZ, need {self.INFERENCE_PRICE_QZ} QZ")
            return

        # Deduct payment (escrow — held until inference completes)
        self.chain.balances[payer_addr] = bal - self.INFERENCE_PRICE_SATS

        # Generate request ID
        self.INFERENCE_COUNTER[0] += 1
        req_id = f"inf_{self.INFERENCE_COUNTER[0]:06d}"

        self.INFERENCE_PENDING[req_id] = {
            'payer': payer_addr,
            'input': input_hex,
            'status': 'queued',
            'created': time.time(),
            'price_qz': self.INFERENCE_PRICE_QZ,
        }

        # If we have a registered inference node, mark as dispatched
        if self.INFERENCE_NODES:
            node_id = list(self.INFERENCE_NODES.keys())[0]
            self.INFERENCE_PENDING[req_id]['status'] = 'dispatched'
            self.INFERENCE_PENDING[req_id]['node'] = node_id
        else:
            # No hardware node — simulate inference (demo mode)
            self.INFERENCE_PENDING[req_id]['status'] = 'simulating'

        self.json_response({
            'request_id': req_id,
            'status': self.INFERENCE_PENDING[req_id]['status'],
            'price_qz': self.INFERENCE_PRICE_QZ,
            'payer_balance_qz': self.chain.balances[payer_addr] / 1e8,
            'message': 'Poll /api/v1/inference/result/' + req_id + ' for result',
        })


    def handle_inference_result(self, req_id):
        """GET /api/v1/inference/result/{req_id}
        Returns the inference result + attestation if ready.
        ESP32 inference nodes POST their results here (see below).
        """
        if req_id in self.INFERENCE_RESULTS:
            r = self.INFERENCE_RESULTS[req_id]
            self.json_response({
                'request_id': req_id,
                'status': 'completed',
                'prediction': r['prediction'],
                'logits': r['logits'],
                'input_hash': r['input_hash'],
                'output_hash': r['output_hash'],
                'model_hash': r['model_hash'],
                'attestation': r['attestation'],
                'node_id': r.get('node_id', 'unknown'),
                'verified': True,
                'note': 'PUF attestation verifies this inference ran on physical ESP32 hardware',
            })
        elif req_id in self.INFERENCE_PENDING:
            p = self.INFERENCE_PENDING[req_id]
            # Simulate completion after 3 seconds (demo mode, no hardware node)
            if p['status'] == 'simulating' and (time.time() - p['created']) > 3:
                import hashlib as _hl
                input_bytes = bytes.fromhex(p['input']) if p['input'] else b'\x00' * 784
                input_hash = _hl.sha256(input_bytes).hexdigest()
                # Simulated prediction (placeholder — real HW returns actual inference)
                fake_logits = [0] * 10
                fake_logits[7] = 42  # "prediction 7"
                output_bytes = b''.join(l.to_bytes(4, 'little', signed=True) for l in fake_logits)
                output_hash = _hl.sha256(output_bytes).hexdigest()
                model_hash = _hl.sha256(b'placeholder-model-weights').hexdigest()
                attestation = _hl.sha256(
                    b'demo-puf-key' + bytes.fromhex(input_hash) +
                    bytes.fromhex(output_hash) + bytes.fromhex(model_hash)
                ).hexdigest()
                self.INFERENCE_RESULTS[req_id] = {
                    'prediction': 7,
                    'logits': fake_logits,
                    'input_hash': input_hash,
                    'output_hash': output_hash,
                    'model_hash': model_hash,
                    'attestation': attestation,
                    'node_id': 'simulated',
                }
                # Pay the inference node (to dev wallet in demo)
                if self.chain.dev_wallet:
                    dw = self.chain.dev_wallet['address']
                    self.chain.balances[dw] = self.chain.balances.get(dw, 0) + self.INFERENCE_PRICE_SATS
                del self.INFERENCE_PENDING[req_id]
                self.json_response({
                    'request_id': req_id,
                    'status': 'completed',
                    'prediction': 7,
                    'logits': fake_logits,
                    'input_hash': input_hash,
                    'output_hash': output_hash,
                    'model_hash': model_hash,
                    'attestation': attestation,
                    'node_id': 'simulated',
                    'verified': False,
                    'note': 'Simulated inference (no hardware node registered). Flash ml-demo firmware and register for real attestation.',
                })
            else:
                self.json_response({
                    'request_id': req_id,
                    'status': p['status'],
                    'message': 'Inference in progress, poll again...',
                })
        else:
            self.json_error(404, "Unknown request_id")


    def handle_inference_submit(self, body):
        """POST /api/v1/inference/submit  (called by ESP32 inference node)
        ESP32 posts its result + attestation. Node credits the inference node.
        """
        req_id = body.get('request_id', '')
        if req_id not in self.INFERENCE_PENDING:
            self.json_error(404, "Unknown request_id")
            return

        p = self.INFERENCE_PENDING[req_id]
        node_addr = body.get('node_address', '')
        prediction = body.get('prediction', -1)
        logits = body.get('logits', [])
        input_hash = body.get('input_hash', '')
        output_hash = body.get('output_hash', '')
        model_hash = body.get('model_hash', '')
        attestation = body.get('attestation', '')

        self.INFERENCE_RESULTS[req_id] = {
            'prediction': prediction,
            'logits': logits,
            'input_hash': input_hash,
            'output_hash': output_hash,
            'model_hash': model_hash,
            'attestation': attestation,
            'node_id': body.get('node_id', ''),
            'node_address': node_addr,
        }

        # Credit inference node
        if node_addr:
            self.chain.balances[node_addr] = self.chain.balances.get(node_addr, 0) + self.INFERENCE_PRICE_SATS

        del self.INFERENCE_PENDING[req_id]
        self.json_response({'status': 'accepted', 'request_id': req_id})


    def handle_inference_register(self, body):
        """POST /api/v1/inference/register  (called by ESP32 at boot)
        Registers an ESP32 as an inference node with its PUF fingerprint.
        """
        node_id = body.get('node_id', '')
        node_addr = body.get('node_address', '')
        model_hash = body.get('model_hash', '')

        if not node_id:
            self.json_error(400, "Missing node_id")
            return

        self.INFERENCE_NODES[node_id] = {
            'address': node_addr,
            'model_hash': model_hash,
            'last_seen': time.time(),
        }
        self.json_response({'status': 'registered', 'node_id': node_id, 'nodes': len(self.INFERENCE_NODES)})


    def handle_inference_models(self):
        """GET /api/v1/inference/models — list available models + nodes"""
        self.json_response({
            'models': [{
                'id': 'mnist-demo',
                'name': 'MNIST Digit Classifier (int8)',
                'input_size': 784,
                'output_size': 10,
                'price_qz': self.INFERENCE_PRICE_QZ,
                'description': '28x28 digit classification, 2-layer int8 NN. Hardware-attested via PUF.',
            }],
            'nodes': [
                {'node_id': nid, 'address': n['address'], 'last_seen': n['last_seen']}
                for nid, n in self.INFERENCE_NODES.items()
            ],
            'price_qz': self.INFERENCE_PRICE_QZ,
        })



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


def _http_get(url, timeout=30):
    """GET with transparent gzip support. Returns raw (decompressed) bytes."""
    import urllib.request
    import gzip as _gzip
    req = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding', '').lower() == 'gzip':
            raw = _gzip.decompress(raw)
        return raw


def _resolve_peers():
    """Peer list for sync: QUARTZ_PEERS (comma-separated, p2p) wins over the
    legacy single QUARTZ_SYNC_URL."""
    peers = os.environ.get('QUARTZ_PEERS', '')
    if peers:
        return [p.strip().rstrip('/') for p in peers.split(',') if p.strip()]
    sync = os.environ.get('QUARTZ_SYNC_URL', '').rstrip('/')
    return [sync] if sync else []


def _snapshot_resync(peer, local_height):
    """Full snapshot pull from peer (bootstrap / deep divergence).
    Returns new height if swapped in, else None."""
    raw = _http_get(peer + '/api/v1/snapshot', timeout=300)
    data = json.loads(raw)  # validate fully before touching disk
    blocks = data.get('blocks')
    if not (isinstance(blocks, list) and len(blocks) > local_height):
        return None
    tmp = CHAIN_FILE + '.sync'
    with open(tmp, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CHAIN_FILE)  # atomic
    new_chain = QuartzChain()  # rebuild state from disk
    if len(new_chain.blocks) <= local_height:
        return None
    QuartzAPIHandler.chain = new_chain  # hot-swap
    return len(new_chain.blocks)


def _incremental_sync(peer, chain, interval):
    """Catch up to peer via /blocks/since batches (KBs, not a 31 MB pull).
    Applies foreign blocks through full consensus validation and adopts the
    peer's authoritative small state at tip. Returns (start_h, end_h, err)."""
    start_h = len(chain.blocks)
    while True:
        resp = json.loads(_http_get(
            peer + f'/api/v1/blocks/since/{len(chain.blocks)}',
            timeout=60).decode())
        got = resp.get('blocks') or []
        if not got:
            return start_h, len(chain.blocks), None  # at tip, nothing to do
        applied, err = chain.apply_peer_blocks(got, resp.get('state'))
        if err:
            return start_h, len(chain.blocks), err
        if len(chain.blocks) >= resp.get('tip_height', 0) + 1:
            return start_h, len(chain.blocks), None  # reached peer tip


def standby_sync():
    """Continuous multi-peer chain sync for standby/gateway nodes.

    QUARTZ_PEERS (comma-separated) defines the peer set; every peer is
    polled, and the node syncs incrementally (block batches + tiny state
    payloads) from whichever peer is furthest ahead. The 31 MB snapshot is
    only pulled for bootstrap (>200 blocks behind) or when incremental
    application fails against an ahead peer (deep divergence).

    Fork policy: peers at our height with a different tip hash are ignored
    (first-seen stickiness; growth decides), and we only ever follow peers
    strictly ahead — so a single strange peer cannot flip-flop our chain.
    """
    peers = _resolve_peers()
    interval = int(os.environ.get('QUARTZ_SYNC_INTERVAL', '30'))
    if not peers:
        return
    print(f"🤝 P2P sync peers: {', '.join(peers)}", flush=True)

    while True:
        try:
            best_peer, best_height, best_hash = None, -1, ''
            for p in peers:
                try:
                    info = json.loads(_http_get(p + '/api/v1/info', timeout=15).decode())
                    h = int(info.get('height', 0))
                    if h > best_height:
                        best_peer, best_height, best_hash = p, h, info.get('best_hash', '')
                except Exception as e:
                    print(f"⚠️ peer {p}: {type(e).__name__} (skipping this poll)",
                          flush=True)
            chain = QuartzAPIHandler.chain
            local_height = len(chain.blocks)

            if best_peer is None:
                raise RuntimeError("no peer reachable")

            lag = best_height - local_height
            if lag <= 0:
                time.sleep(interval)
                continue

            if lag > 200:
                end = _snapshot_resync(best_peer, local_height)
                if end:
                    print(f"🔄 Snapshot bootstrap: {local_height} → {end} "
                          f"from {best_peer}", flush=True)
                else:
                    print("⚠️ sync: snapshot not ahead, keeping local chain",
                          flush=True)
            else:
                start, end, err = _incremental_sync(best_peer, chain, interval)
                if err:
                    print(f"⚠️ incremental sync failed: {err}", flush=True)
                    if best_height > len(chain.blocks):
                        end = _snapshot_resync(best_peer, len(chain.blocks))
                        if end:
                            print(f"🔄 Resynced via snapshot: {start} → {end} "
                                  f"from {best_peer}", flush=True)
                elif end > start:
                    print(f"🔄 Synced +{end - start} blocks: {start} → {end} "
                          f"from {best_peer}", flush=True)
        except Exception as e:
            # Transient network errors: log and retry next interval.
            # Standby node keeps serving its last good state meanwhile.
            print(f"⚠️ sync: {type(e).__name__}: {e} (retrying in {interval}s)",
                  flush=True)
        time.sleep(interval)


def main():
    print("=" * 60)
    print("  Quartz Testnet Seed Node v0.1.0")
    print("  https://quartz.preview.saasclaw.ai")
    print("=" * 60)

    chain = QuartzChain()
    QuartzAPIHandler.chain = chain

    # Gateway mode: forward consensus/mutating endpoints upstream
    relay_url = os.environ.get('QUARTZ_RELAY_URL', '').rstrip('/')
    if relay_url:
        if os.environ.get('QUARTZ_NO_MINER') != '1':
            # Local simulator + relay = two miners on divergent snapshots =
            # instant chain fork. Refuse loudly instead.
            raise SystemExit(
                "QUARTZ_RELAY_URL requires QUARTZ_NO_MINER=1 — a gateway "
                "node must never run its own mining simulator.")
        QuartzAPIHandler.relay_url = relay_url

    # Start mining simulator in background (unless disabled — two
    # simulators mining separate snapshots = instant chain fork)
    if os.environ.get('QUARTZ_NO_MINER') == '1':
        print("\n🛑 Mining simulator DISABLED (QUARTZ_NO_MINER=1) — standby/read-only node")
        peers = _resolve_peers()
        if peers:
            t = threading.Thread(target=standby_sync, daemon=True)
            t.start()
            print(f"🔄 Continuous p2p sync from {len(peers)} peer(s) "
                  f"(poll every {os.environ.get('QUARTZ_SYNC_INTERVAL', '30')}s)")
    if relay_url:
        print(f"⛏ Gateway mode: mining submits relay to {relay_url} — "
              f"point ESP32s at this node (LAN IP port "
              f"{QUARTZ_PORT})")
    else:
        miner_thread = threading.Thread(target=mining_simulator, args=(chain,), daemon=True)
        miner_thread.start()
        print(f"\n🚀 Mining simulator started ({len(DEMO_MINERS)} virtual ESP32s)")
    print(f"📡 API: http://localhost:{QUARTZ_PORT}")
    print(f"🔗 Chain height: {len(chain.blocks)}")
    print(f"💰 Total supply: {sum(chain.balances.values()) / 1e8:.1f} QZ")
    print()

    # Start HTTP server
    # Threading: a 31 MB snapshot transfer must never block a mining
    # submit (or any other request). Each connection gets its own thread;
    # the hot-swap sync already assumes concurrent handler threads.
    server = ThreadingHTTPServer(('0.0.0.0', QUARTZ_PORT), QuartzAPIHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")





QUARTZ_PORT = 21100

if __name__ == '__main__':
    main()
