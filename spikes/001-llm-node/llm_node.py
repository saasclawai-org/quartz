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
  LLM_MODELS  optional menu "name=price,name=price" — customers pick the
              model per request (/request {"model": ...}); prices per model.
              LLM_MODEL remains the default. Example:
              LLM_MODELS="qwen3.5:0.8b=0.5,qwen2.5:3b=2"
  PRICE_QZ    price per request (default 0.5)
  PORT        listen port    (default 8788)
"""
import hashlib
import json
import os
import sys
import threading
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
# Well-connected node used for the 0-conf fast path: the local node's
# mempool may never see a tx that was broadcast elsewhere, but the seed
# sees it within ~2 s. Local node stays the confirmation authority.
# Set NODE_FALLBACK_URL="" to disable (fully offline operation).
NODE_FALLBACK_URL = os.environ.get(
    "NODE_FALLBACK_URL", "https://quartzchain.net")
# Payments up to this cap may claim on 0-conf (mempool) — retail risk
# model: tiny exposures don't wait ~30 s for a block. Above it: a
# confirmation is required.
ZEROCONF_MAX_SATS = int(float(os.environ.get("ZEROCONF_MAX_QZ", "10")) * 1e8)
LLM_BACKEND = os.environ.get("LLM_BACKEND", "mock")
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:1.5b")
PRICE_QZ = float(os.environ.get("PRICE_QZ", "0.5"))
PRICE_SATS = int(PRICE_QZ * 1e8)
LLM_MODELS_SPEC = os.environ.get("LLM_MODELS", "")
PORT = int(os.environ.get("PORT", "8788"))
JOB_TTL_S = 600


def _parse_models(spec, default_model, default_price):
    """'name=price,name=price' -> ordered {name: price_qz}."""
    models = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, price = item.rsplit("=", 1)
            try:
                models[name.strip()] = float(price)
            except ValueError:
                pass
        else:
            models[item] = default_price
    models.setdefault(default_model, default_price)
    return models


# The model menu: per-request selection, per-model pricing. The first
# entry of LLM_MODELS wins the /price headline; LLM_MODEL is the default
# when a request doesn't name one.
MODELS = _parse_models(LLM_MODELS_SPEC, LLM_MODEL, PRICE_QZ)
if not LLM_MODELS_SPEC:
    LLM_MODEL = next(iter(MODELS))          # normalize default to the menu
elif LLM_MODEL not in MODELS:
    MODELS[LLM_MODEL] = PRICE_QZ            # default must be servable

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
    headers = {"User-Agent": "Mozilla/5.0 (compatible; quartz-llm-node/1.0)"}
    # ^ Cloudflare 403s bare Python-urllib UAs; the fallback node sits
    # behind CF, so identify as a real client or the fast path dies.
    if body is None:
        req = urllib.request.Request(url, headers=headers)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _find_tx(base_url, txid, price_sats):
    """Search PAY_TO's tx list on one node. Returns (status, tx, detail):
    ok = confirmed · pending = in mempool · no = seen but rejected."""
    try:
        info = http_json(
            f"{base_url}/api/v1/address/{PAY_TO}/txs?min_amount={price_sats}")
    except Exception as e:
        return "error", None, f"node query failed: {e}"
    for tx in info.get("txs", []):
        chain_txid = str(tx.get("txid", ""))
        if not (chain_txid == txid or chain_txid.startswith(txid)
                or txid.startswith(chain_txid)):
            continue
        if tx.get("direction", "in") != "in":
            return "no", tx, "tx is outgoing, not a payment"
        if int(tx.get("amount", 0)) < price_sats:
            return "no", tx, f"tx underpaid ({tx.get('amount')} < {price_sats})"
        if tx.get("confirmed") is False or tx.get("pending") is True:
            return "pending", tx, "tx not confirmed yet (in mempool)"
        return "ok", tx, "confirmed"
    return "missing", None, "txid not found for this address"


def check_payment(txid: str, price_sats: int):
    """Verify txid paid PAY_TO at least PRICE_SATS.

    Confirmed on the local node always works. Small payments may also claim
    0-conf — from the local mempool or, failing that, the seed node's: the
    local node often never sees a tx that was broadcast elsewhere, but the
    seed's mempool has it within ~2 s. Exposure is capped by
    ZEROCONF_MAX_SATS; anything above the cap waits for a real block.
    Returns (ok, detail)."""
    detail = "txid not found for this address"
    for base in (NODE_URL, NODE_FALLBACK_URL):
        if not base:
            continue
        status, tx, d = _find_tx(base, txid, price_sats)
        if status == "ok":
            return True, tx
        if status == "pending" and tx \
                and int(tx.get("amount", 0)) <= ZEROCONF_MAX_SATS:
            return True, {**tx, "zero_conf": True}
        if status == "no":
            return False, d          # seen and rejected: definitive
        if d:
            detail = d
    return False, detail

def run_backend(prompt: str, model: str = LLM_MODEL) -> str:
    if LLM_BACKEND == "mock":
        h = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return (f"[mock:{model}] Quartz demo completion for prompt "
                f"sha {h}. On a Pi this would be Ollama/llama.cpp output.")
    if LLM_BACKEND == "ollama":
        r = http_json(f"{LLM_URL}/api/generate",
                      {"model": model, "prompt": prompt, "stream": False},
                      timeout=300)
        return r.get("response", "").strip()
    if LLM_BACKEND == "openai":
        r = http_json(f"{LLM_URL}/chat/completions",
                      {"model": model, "messages": [{"role": "user",
                                                         "content": prompt}]},
                      timeout=300)
        return r["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"unknown backend {LLM_BACKEND}")

_warm_lock = threading.Lock()


def warm_model(model: str = LLM_MODEL):
    """Preload the model while the customer pays — Ollama's cold load is
    ~15-20 s on a Pi, so overlap it with the payment window instead of
    paying it afterwards. Best-effort: failures are ignored."""
    if LLM_BACKEND != "ollama" or not _warm_lock.acquire(blocking=False):
        return

    def _go():
        try:
            http_json(f"{LLM_URL}/api/generate",
                      {"model": model, "prompt": "hi", "stream": False,
                       "options": {"num_predict": 1}}, timeout=180)
        except Exception:
            pass                      # warm-up is best-effort
        finally:
            _warm_lock.release()

    threading.Thread(target=_go, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def route(self):
        """Path with query stripped, tolerant of a /llm prefix.

        Behind a reverse proxy or a path-based Cloudflare Origin Rule the
        service may live at /llm/price etc. — route those identically to
        /price so browser demos can reach it without a dedicated port.
        """
        p = self.path.split("?", 1)[0]
        if p == "/llm":
            return "/"
        if p.startswith("/llm/"):
            return p[4:]
        return p

    def reply(self, code, obj):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Browser demos: any origin may talk to the LLM node (payments are
        # enforced on-chain, not by origin policy)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[llm-node] {self.command} {self.path} {fmt % args}", flush=True)

    def do_GET(self):
        path = self.route()
        if path == "/price":
            self.reply(200, {"models": [{"model": m, "price_qz": p}
                                        for m, p in MODELS.items()],
                             "price_qz": MODELS[LLM_MODEL],
                             "price_sats": int(MODELS[LLM_MODEL] * 1e8),
                             "pay_to": PAY_TO, "model": LLM_MODEL,
                             "backend": LLM_BACKEND,
                             "zeroconf_max_qz": ZEROCONF_MAX_SATS / 1e8})
        elif path == "/identity":
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

        path = self.route()
        if path == "/request":
            prompt = (body.get("prompt") or "").strip()
            if not prompt or len(prompt) > 4000:
                return self.reply(400, {"error": "prompt 1..4000 chars"})
            model = body.get("model") or LLM_MODEL
            if model not in MODELS:
                return self.reply(400, {"error": f"unknown model {model!r}",
                                        "available": list(MODELS)})
            price_qz = MODELS[model]
            price_sats = int(price_qz * 1e8)
            job_id = "job_" + secrets.token_hex(6)
            JOBS[job_id] = {"prompt": prompt, "created": time.time(),
                            "claimed_txid": None,
                            "model": model, "price_sats": price_sats}
            print(f"[llm-node] job {job_id} created ({model})", flush=True)
            warm_model(model)          # hide the model's cold load in the pay window
            return self.reply(200, {"job_id": job_id, "pay_to": PAY_TO,
                                    "model": model,
                                    "price_qz": price_qz,
                                    "price_sats": price_sats,
                                    "expires_in_s": JOB_TTL_S})

        if path == "/claim":
            job_id, txid = body.get("job_id", ""), body.get("txid", "").strip()
            job = JOBS.get(job_id)
            if not job:
                return self.reply(404, {"error": "unknown job"})
            if time.time() - job["created"] > JOB_TTL_S:
                return self.reply(410, {"error": "job expired"})
            if txid in USED_TXIDS:
                return self.reply(409, {"error": "txid already used"})
            price_sats = job.get("price_sats", PRICE_SATS)
            model = job.get("model", LLM_MODEL)
            ok, detail = check_payment(txid, price_sats)
            if not ok:
                return self.reply(402, {"error": f"payment not verified: {detail}"})
            try:
                completion = run_backend(job["prompt"], model)
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
                "model": model,
                "price_sats": price_sats,
                "txid": txid,
                "ts": int(time.time()),
                "node": PAY_TO,
            }
            sig = sign_message(bytes.fromhex(IDENTITY["private_key"]),
                               canonical(receipt))
            receipt["signature"] = sig.hex()
            print(f"[llm-node] job {job_id} claimed via {txid[:16]}… "
                  f"({'0-conf' if isinstance(detail, dict) and detail.get('zero_conf') else 'confirmed'})",
                  flush=True)
            return self.reply(200, {"completion": completion,
                                    "receipt": receipt})

        if path == "/verify":
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
