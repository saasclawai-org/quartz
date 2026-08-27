#!/usr/bin/env bash
# Quartz Pi Node installer — full syncing testnet node
# Run on the Pi:  bash install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/quartz-node

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash install.sh"; exit 1; }

echo "== Quartz node install =="

# 1. Python check (any 3.8+ works; stdlib only, nothing to pip-install)
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 --version

# 2. Install files
mkdir -p "$DEST"
cp -r "$DIR/quartz" "$DIR/testnet.py" "$DEST/"
if [ -f "$DIR/testnet-data/chain.json" ]; then
  mkdir -p "$DEST/testnet-data"
  cp "$DIR/testnet-data/chain.json" "$DEST/testnet-data/"
fi

# Drop old-fork leftovers: a stale dev-wallet.json makes /info report a
# dead dev-fund address (harmless — 0 balance — but confusing).
rm -f "$DEST/testnet-data/dev-wallet.json" "$DEST"/dev-wallet*.json

# 3. systemd service (QUARTZ_NO_MINER=1 + QUARTZ_PEERS inside the unit:
#    never mines, so it can never fork the chain; syncs continuously from
#    the seed node — kilobyte batches, every block fully validated)
cp "$DIR/quartz-node.service" /etc/systemd/system/
# Drop the old fork's override drop-in (if present) — the unit above is
# self-contained and canonical. NOTE: deliberately targeted: wiping the
# whole service.d/ dir would delete user drop-ins like relay.conf
# (QUARTZ_RELAY_URL gateway mode).
rm -f /etc/systemd/system/quartz-node.service.d/override.conf
systemctl daemon-reload
systemctl enable quartz-node >/dev/null 2>&1 || true
# Restart, not just enable --now: if an old node is already running,
# enable --now is a no-op and the old code/chain would keep serving.
systemctl restart quartz-node

# 4. Local firewall (if ufw present): allow the node port on LAN
if command -v ufw >/dev/null; then
  ufw allow 21100/tcp >/dev/null 2>&1 || true
fi

sleep 3
echo
systemctl --no-pager -l status quartz-node | head -8
echo
echo "✅ Node running on port 21100 — syncing from https://quartzchain.net"
echo "   Test:     curl http://$(hostname -I 2>/dev/null | awk '{print $1}'):21100/api/v1/info"
echo "   Logs:     journalctl -u quartz-node -f"
echo
echo "Seed status: curl -s https://quartzchain.net/api/v1/info | head -c 200"
