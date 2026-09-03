# Spike 001 — LLM Node: pay-per-request local inference with signed receipts

**Question:** Can a Pi-class gateway sell local-LLM completions for QZ — payment
as a real on-chain transaction, verified against the chain's own API, answered
with an Ed25519-signed receipt — using only rails Quartz already has? (The
"solar-powered village LLM".)

## Design

```
customer wallet ──0.5 QZ──▶ LLM node address        (ordinary /api/v1/send tx)
       │                            │
       └── POST /request {prompt}   │  job_id + pay_to + price
                                      ▼
                              POST /claim {job_id, txid}
                                      │ verify tx on-chain (address txs API):
                                      │   to == node, amount ≥ price, confirmed
                                      │ txid single-use (replay-proof)
                                      ▼
                        backend (mock | ollama | OpenAI-compatible)
                                      ▼
                     completion + receipt {prompt_sha256, completion_sha256,
                     model, price_sats, txid, ts, node} + Ed25519 signature
```

No escrow, no trusted third party. The node's key (`llm-node-key.json`,
gitignored) is a standard Quartz wallet — the receipt is verifiable by anyone
via `POST /verify`.

## Files

- `llm_node.py` — the service (stdlib + `quartz.crypto` only)
- `test_spike.py` — end-to-end test against the live testnet on this host

## Run

Local (mock backend, what was tested here):

    python3 llm_node.py                 # PORT=8788, NODE_URL=127.0.0.1:21100

On a Pi with a real model:

    ollama pull qwen2.5:1.5b
    LLM_BACKEND=ollama LLM_MODEL=qwen2.5:1.5b PRICE_QZ=0.5 python3 llm_node.py

Any OpenAI-compatible server (llama.cpp `llama-server`, etc.):

    LLM_BACKEND=openai LLM_URL=http://127.0.0.1:8080 python3 llm_node.py

## Evidence (live testnet, 2026-09-03)

    PASS  price served  (Qrzz4rskNvaJE2CN…)
    PASS  faucet drip  ({'status': 'sent', 'amount': '10 QZ', …})
    PASS  drip confirmed on-chain  (10.0 QZ)
    PASS  job created  (job_202032b311ca)
    PASS  payment broadcast  (e32557ab8644a26e…)
    PASS  claim: completion + receipt
    PASS  receipt signature valid
    PASS  stress: bogus txid rejected  (payment not verified: txid not found)
    PASS  stress: txid replay rejected  (txid already used)
    PASS  stress: tampered receipt invalid
    10/10 checks passed

## Verdict: VALIDATED

**Question:** pay-per-request local inference with on-chain payment + signed
receipts, using existing Quartz rails only.

**Evidence:** 10/10 checks on the live chain — faucet-funded wallet → real
0.5 QZ payment → on-chain verification → completion → Ed25519 receipt
verifies; bogus txid, replayed txid, and tampered receipt all rejected.

**What worked:** everything except the model itself — the mock backend stood
in for Ollama (no LLM runtime on the spike host). Payment, verification,
replay protection, and receipts are real and live.

**What surprised us:** the model backend is one env var; the hard part was
never the LLM — it's the payment rails, and those already existed.

**Recommendation:** ship as a build-pi-bundle.sh option ("LLM node" flavor:
node + llama.cpp/Ollama + model file). Point the device agent's LLM endpoint
at it. Fleet mode (N boards compute, majority receipt gets paid) is the
natural next spike and reuses this receipt format verbatim.
