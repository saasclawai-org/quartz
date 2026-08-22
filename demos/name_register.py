#!/usr/bin/env python3
"""
Quartz Name Registry — human-readable names for on-chain addresses.

Names are registered ON-CHAIN as messages:
    from:  <the address being named>
    to:    name:registry
    text:  NAME:REGISTER|LABEL:<label>|KIND:<kind>

The address being named is the message sender, so on mainnet (where
senders are signature-verified) a name can only be set by the address
owner. Self-certifying, no central authority.

Resolution rules (client-side convention):
  - Latest NAME:REGISTER per address wins (renames allowed)
  - Same label on two addresses = conflict; clients should show
    the address alongside the name

Usage:
  # Register a name for your address:
  python3 name_register.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --label "Quartz Dev Fund" --kind wallet

  # Look up an address's on-chain name:
  python3 name_register.py --lookup Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U

  # Dump the whole registry:
  python3 name_register.py --list

License: MIT
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

DEFAULT_NODE = "https://quartz.preview.saasclaw.ai"
REGISTRY_TO = "name:registry"
KINDS = ("miner", "sensor", "station", "wallet", "dev", "exchange", "other")


def post_message(node: str, from_addr: str, to: str, text: str) -> dict:
    """POST /api/v1/messages/send"""
    payload = json.dumps({"from": from_addr, "to": to, "text": text}).encode()
    req = urllib.request.Request(
        f"{node.rstrip('/')}/api/v1/messages/send",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_messages(node: str) -> list:
    """GET /api/v1/messages"""
    with urllib.request.urlopen(f"{node.rstrip('/')}/api/v1/messages", timeout=10) as resp:
        return json.loads(resp.read().decode()).get("messages", [])


def build_registry(messages: list) -> dict:
    """Build address -> {label, kind, timestamp, txid} from on-chain NAME:REGISTER messages.

    Latest registration per address wins.
    """
    registry = {}
    for m in messages:
        text = m.get("data_text", "")
        if not text.startswith("NAME:REGISTER"):
            continue
        sender = m.get("from", "")
        if not sender:
            continue
        parts = {}
        for chunk in text.split("|"):
            if ":" in chunk:
                k, _, v = chunk.partition(":")
                parts[k.strip()] = v.strip()
        label = parts.get("LABEL", "")
        if not label:
            continue
        entry = {
            "address": sender,
            "label": label,
            "kind": parts.get("KIND", "other"),
            "timestamp": m.get("timestamp", 0),
            "txid": m.get("txid", ""),
        }
        prev = registry.get(sender)
        if not prev or entry["timestamp"] >= prev["timestamp"]:
            registry[sender] = entry
    return registry


def find_conflicts(registry: dict) -> set:
    """Labels claimed by more than one address."""
    by_label = {}
    for entry in registry.values():
        key = entry["label"].lower()
        by_label.setdefault(key, set()).add(entry["address"])
    return {label for label, addrs in by_label.items() if len(addrs) > 1}


def main():
    parser = argparse.ArgumentParser(description="Quartz on-chain name registry")
    parser.add_argument("--node", default=DEFAULT_NODE, help="Node URL")
    parser.add_argument("--address", help="Address to name (must be the sender)")
    parser.add_argument("--label", help="Human-readable name (max ~120 chars)")
    parser.add_argument("--kind", choices=KINDS, default="other", help="Address type")
    parser.add_argument("--lookup", help="Look up one address's on-chain name")
    parser.add_argument("--list", action="store_true", help="Dump the whole registry")
    args = parser.parse_args()

    if args.lookup:
        registry = build_registry(get_messages(args.node))
        entry = registry.get(args.lookup)
        if entry:
            print(f"{entry['address']}\n  name: {entry['label']}\n  kind: {entry['kind']}")
        else:
            print(f"No on-chain name for {args.lookup}")
            sys.exit(1)
        return

    if args.list:
        registry = build_registry(get_messages(args.node))
        conflicts = find_conflicts(registry)
        if not registry:
            print("Registry is empty. Register the first name!")
            return
        for entry in sorted(registry.values(), key=lambda e: e["timestamp"]):
            flag = "  ⚠ CONFLICT" if entry["label"].lower() in conflicts else ""
            print(f"{entry['address']}  {entry['label']}  ({entry['kind']}){flag}")
        return

    if not (args.address and args.label):
        parser.error("--address and --label are required to register (or use --lookup / --list)")

    text = f"NAME:REGISTER|LABEL:{args.label}|KIND:{args.kind}"
    if len(text) > 160:
        print(f"Message too long ({len(text)} > 160). Shorten the label.")
        sys.exit(1)

    result = post_message(args.node, args.address, REGISTRY_TO, text)
    if result.get("status") == "queued":
        print(f"✓ Name registered on-chain")
        print(f"  address: {args.address}")
        print(f"  name:    {args.label}")
        print(f"  kind:    {args.kind}")
        print(f"  txid:    {result.get('txid', '?')}")
    else:
        print(f"Unexpected response: {result}")
        sys.exit(1)


if __name__ == "__main__":
    main()
