#!/usr/bin/env python3
"""Quartz LLM Node spike — pay-per-request local inference with signed receipts.

The "solar-powered village LLM": a gateway-class box (Pi) runs a local model
(Ollama / llama.cpp / any OpenAI-compatible server) and sells completions for
QZ. No escrow, no trusted third party — every request is paid with a real
on-chain transaction, verified against the chain's own API, and answered with
an Ed25519-signed receipt anyone can verify.

Flow:
  1. GET  /price                -> price + pay_to address
  2. POST /request  {prompt}    -> {job_id, pay_to, price_sats, expires_in_s}
  3. Customer pays pay_to via /api/v1/send (any Quartz wallet)
  4. POST /claim   {job_id, txid} -> {completion, receipt}
  5. POST /verify  {receipt}    -> signature check (anyone, offline)

Receipt = canonical JSON payload + Ed25519 signature by the LLM node's key.
Txids are single-use (replay protection). Jobs expire.

Env:
  NODE_URL    chain API (default http://127.0.0.1:21100)
  LLM_BACKEND mock | ollama | openai      (default mock)
  LLM_URL     backend base URL
  LLM_MODEL   model name (default "qwen2.5:1.5b")
  PRICE_QZ    price per request (default 0.5)
  PORT        listen port    (default 8788)
"""
import hashlib
import json
import os
import sys
import time
import secrets
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# Portable layout: in the Pi bundle, quartz/ sits NEXT TO this file;
# in the repo spike, it lives two dirs up in reference-node/.
for _cand in (HERE, os.path.join(HERE, "..", "..", "reference-node")):
    if os.path.isdir(os.path.join(_cand, "quartz")):
        sys.path.insert(0, _cand)
        break
from quartz.crypto import create_new_wallet, sign_message, verify_signature  # noqa: E402

NODE_URL = os.environ.get("NODE_URL", "http://127.0.0.1:21100")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "mock")
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
PRICE_QZ = float(os.environ.get("PRICE_QZ", "0.5"))
PRICE_SATS = int(PRICE_QZ * 1e8)
PORT = int(os.environ.get("PORT", "8788"))
JOB_TTL_S = 600

# ── node identity ──────────────────────────────────────────────────────
KEY_FILE = os.path.join(HERE, "llm-node-key.json")

def load_identity():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return json.load(f)
    w = create_new_wallet()
    w = {"address": w["address"],
         "public_key": w["public_key"],      # already hex strings
         "private_key": w["private_key"]}
    with open(KEY_FILE, "w") as f:
        json.dump(w, f, indent=2)
    return w

IDENTITY = load_identity()
PAY_TO = IDENTITY["address"]

JOBS = {}            # job_id -> {prompt, created, claimed_txid}
USED_TXIDS = set()   # replay protection

def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

def http_json(url, body=None, timeout=30):
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def check_payment(txid: str):
    """Verify txid paid PAY_TO at least PRICE_SATS, confirmed, on-chain.

    Returns (ok, detail)."""
    try:
        info = http_json(
            f"{NODE_URL}/api/v1/address/{PAY_TO}/txs?min_amount={PRICE_SATS}")
    except Exception as e:
        return False, f"node query failed: {e}"
    for tx in info.get("txs", []):
        chain_txid = str(tx.get("txid", ""))
        if not (chain_txid == txid or chain_txid.startswith(txid)
                or txid.startswith(chain_txid)):
            continue
        if tx.get("direction", "in") != "in":
            return False, "tx is outgoing, not a payment"
        if tx.get("confirmed") is False or tx.get("pending") is True:
            return False, "tx not confirmed yet"
        if int(tx.get("amount", 0)) < PRICE_SATS:
            return False, f"tx underpaid ({tx.get('amount')} < {PRICE_SATS})"
        return True, tx
    return False, "txid not found for this address"

def run_backend(prompt: str) -> str:
    if LLM_BACKEND == "mock":
        h = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return (f"[mock:{LLM_MODEL}] Quartz demo completion for prompt "
                f"sha {h}. On a Pi this would be Ollama/llama.cpp output.")
    if LLM_BACKEND == "ollama":
        r = http_json(f"{LLM_URL}/api/generate",
                      {"model": LLM_MODEL, "prompt": prompt, "stream": False},
                      timeout=300)
        return r.get("response", "").strip()
    if LLM_BACKEND == "openai":
        r = http_json(f"{LLM_URL}/chat/completions",
                      {"model": LLM_MODEL, "messages": [{"role": "user",
                                                         "content": prompt}]},
                      timeout=300)
        return r["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"unknown backend {LLM_BACKEND}")

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, obj):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[llm-node] {self.command} {self.path} {fmt % args}", flush=True)

    def do_GET(self):
        if self.path == "/price":
            self.reply(200, {"price_qz": PRICE_QZ, "price_sats": PRICE_SATS,
                             "pay_to": PAY_TO, "model": LLM_MODEL,
                             "backend": LLM_BACKEND})
        elif self.path == "/identity":
            self.reply(200, {"address": PAY_TO,
                             "public_key": IDENTITY["public_key"]})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return self.reply(400, {"error": "bad json"})

        if self.path == "/request":
            prompt = (body.get("prompt") or "").strip()
            if not prompt or len(prompt) > 4000:
                return self.reply(400, {"error": "prompt 1..4000 chars"})
            job_id = "job_" + secrets.token_hex(6)
            JOBS[job_id] = {"prompt": prompt, "created": time.time(),
                            "claimed_txid": None}
            print(f"[llm-node] job {job_id} created", flush=True)
            return self.reply(200, {"job_id": job_id, "pay_to": PAY_TO,
                                    "price_qz": PRICE_QZ,
                                    "price_sats": PRICE_SATS,
                                    "expires_in_s": JOB_TTL_S})

        if self.path == "/claim":
            job_id, txid = body.get("job_id", ""), body.get("txid", "").strip()
            job = JOBS.get(job_id)
            if not job:
                return self.reply(404, {"error": "unknown job"})
            if time.time() - job["created"] > JOB_TTL_S:
                return self.reply(410, {"error": "job expired"})
            if txid in USED_TXIDS:
                return self.reply(409, {"error": "txid already used"})
            ok, detail = check_payment(txid)
            if not ok:
                return self.reply(402, {"error": f"payment not verified: {detail}"})
            try:
                completion = run_backend(job["prompt"])
            except Exception as e:
                return self.reply(502, {"error": f"backend failed: {e}"})
            USED_TXIDS.add(txid)
            JOBS[job_id]["claimed_txid"] = txid
            receipt = {
                "job_id": job_id,
                "prompt_sha256": hashlib.sha256(
                    job["prompt"].encode()).hexdigest(),
                "completion_sha256": hashlib.sha256(
                    completion.encode()).hexdigest(),
                "model": LLM_MODEL,
                "price_sats": PRICE_SATS,
                "txid": txid,
                "ts": int(time.time()),
                "node": PAY_TO,
            }
            sig = sign_message(bytes.fromhex(IDENTITY["private_key"]),
                               canonical(receipt))
            receipt["signature"] = sig.hex()
            print(f"[llm-node] job {job_id} claimed via {txid[:16]}…",
                  flush=True)
            return self.reply(200, {"completion": completion,
                                    "receipt": receipt})

        if self.path == "/verify":
            receipt = dict(body.get("receipt", {}))
            sig_hex = receipt.pop("signature", "")
            try:
                ok = verify_signature(bytes.fromhex(IDENTITY["public_key"]),
                                      canonical(receipt),
                                      bytes.fromhex(sig_hex))
            except Exception:
                ok = False
            return self.reply(200, {"valid": ok})

        return self.reply(404, {"error": "not found"})

if __name__ == "__main__":
    print(f"[llm-node] address {PAY_TO}")
    print(f"[llm-node] backend={LLM_BACKEND} model={LLM_MODEL} "
          f"price={PRICE_QZ} QZ node={NODE_URL}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
