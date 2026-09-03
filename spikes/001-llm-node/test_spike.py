#!/usr/bin/env python3
"""End-to-end spike test for the LLM node — real chain, real payment.

Runs against the live testnet node on this host (QUARTZ_PORT 21100):
  faucet-drip a throwaway customer wallet -> pay the LLM node 0.5 QZ via
  /api/v1/send -> claim -> completion + Ed25519 receipt -> verify.
Then three stress cases: unpaid txid, txid replay, tampered receipt.

Usage: python3 test_spike.py   (starts llm_node.py itself on port 8788)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "reference-node"))
from quartz.crypto import create_new_wallet, sign_message  # noqa: E402

NODE = "http://127.0.0.1:21100"
LLM = "http://127.0.0.1:8788"
PRICE_QZ = 0.5
PRICE_SATS = int(PRICE_QZ * 1e8)

RESULTS = []

def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)

def http(url, body=None, timeout=30):
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0, {}   # not up yet / unreachable — caller decides

def addr_txs(addr, min_amount=0):
    _, body = http(f"{NODE}/api/v1/address/{addr}/txs?min_amount={min_amount}")
    return body

def wait_for(pred, timeout_s, what):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(3)
    print(f"timeout waiting for {what}", flush=True)
    return False

def main():
    # start the LLM node (mock backend)
    env = dict(os.environ, PORT="8788", NODE_URL=NODE, LLM_BACKEND="mock")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "llm_node.py")],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)
    try:
        assert wait_for(lambda: http(f"{LLM}/price")[0] == 200, 15, "llm node up")

        # 1. price + identity
        _, price = http(f"{LLM}/price")
        pay_to = price["pay_to"]
        check("price served", price["price_sats"] == PRICE_SATS, pay_to[:16] + "…")

        # 2. throwaway customer, faucet drip
        w = create_new_wallet()
        cust_addr = w["address"]
        code, drip = http(f"{NODE}/api/v1/faucet", {"address": cust_addr})
        check("faucet drip", code == 200, str(drip)[:80])

        ok = wait_for(lambda: addr_txs(cust_addr).get("balance_sats", 0) >= 10**9,
                      180, "drip mined (10 QZ)")
        check("drip confirmed on-chain", ok, f"{addr_txs(cust_addr).get('balance_sats', 0)/1e8} QZ")

        # 3. request a job
        prompt = "Explain the Quartz chain in one sentence."
        code, job = http(f"{LLM}/request", {"prompt": prompt})
        check("job created", code == 200 and "job_id" in job, job.get("job_id", ""))

        # 4. pay from the customer wallet via /api/v1/send
        msg = f"{cust_addr}{pay_to}{PRICE_SATS}".encode()
        sig = sign_message(bytes.fromhex(w["private_key"]), msg)
        code, sent = http(f"{NODE}/api/v1/send", {
            "from": cust_addr, "to": pay_to, "amount": PRICE_QZ,
            "signature": sig.hex(), "public_key": w["public_key"]})
        txid = sent.get("txid", "")
        check("payment broadcast", code == 200 and bool(txid), txid[:16] + "…")

        # 5. claim — retry until the payment is mined & verified
        code, claim = 0, {}
        for _ in range(60):
            code, claim = http(f"{LLM}/claim", {"job_id": job["job_id"], "txid": txid})
            if code == 200:
                break
            time.sleep(3)
        check("claim: completion + receipt", code == 200 and "receipt" in claim,
              claim.get("error", "")[:80])
        receipt = claim.get("receipt", {})

        # 6. receipt signature verifies
        code, v = http(f"{LLM}/verify", {"receipt": receipt})
        check("receipt signature valid", v.get("valid") is True)

        # stress 1 — unknown txid
        _, j2 = http(f"{LLM}/request", {"prompt": "stress: unpaid"})
        code, r2 = http(f"{LLM}/claim", {"job_id": j2["job_id"],
                                         "txid": "ab" * 32})
        check("stress: bogus txid rejected", code == 402, r2.get("error", "")[:60])

        # stress 2 — replay the used txid on a fresh job
        _, j3 = http(f"{LLM}/request", {"prompt": "stress: replay"})
        code, r3 = http(f"{LLM}/claim", {"job_id": j3["job_id"], "txid": txid})
        check("stress: txid replay rejected", code == 409, r3.get("error", "")[:60])

        # stress 3 — tampered receipt fails verification
        bad = dict(receipt)
        bad["completion_sha256"] = "0" * 64
        _, v3 = http(f"{LLM}/verify", {"receipt": bad})
        check("stress: tampered receipt invalid", v3.get("valid") is False)

    finally:
        proc.terminate()

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed", flush=True)
    sys.exit(0 if all(RESULTS) else 1)

if __name__ == "__main__":
    main()
