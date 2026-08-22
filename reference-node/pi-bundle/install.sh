#!/usr/bin/env bash
# Quartz Pi Node installer — standby/read-only testnet node
# Run on the Pi:  bash install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/quartz-node

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash install.sh"; exit 1; }

echo "== Quartz node install =="

# 1. Python check (any 3.8+ works; stdlib only, nothing to pip-install)
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 --version

# 2. Optional: full wallet/signing support (PyNaCl). The standby node
#    boots and syncs fine WITHOUT it (watch-only) — this is best-effort.
if python3 -c "import nacl" 2>/dev/null; then
  echo "PyNaCl: already present"
else
  ( apt-get install -y python3-pynacl 2>/dev/null \
      || pip3 install --break-system-packages pynacl 2>/dev/null \
      || pip3 install pynacl 2>/dev/null ) \
    && echo "PyNaCl: installed (full wallet support)" \
    || echo "PyNaCl: not installable — node runs watch-only (fine for standby)"
fi

# 3. Install files. Keep an existing chain.json — a reinstall must
#    not clobber live state with the (older) bundle-baked snapshot.
mkdir -p "$DEST"
cp -r "$DIR/quartz" "$DIR/testnet.py" "$DEST/"
mkdir -p "$DEST/testnet-data"
if [ ! -f "$DEST/testnet-data/chain.json" ] \
    && [ -f "$DIR/testnet-data/chain.json" ]; then
  cp "$DIR/testnet-data/chain.json" "$DEST/testnet-data/"
fi
if [ -f "$DIR/testnet-data/dev-wallet.json" ]; then
  cp "$DIR/testnet-data/dev-wallet.json" "$DEST/testnet-data/"
fi

# 4. systemd service (QUARTZ_NO_MINER=1 = no simulated fleet miner on user
#    nodes — real hardware mining still works; QUARTZ_PEERS keeps the
#    chain continuously fresh via incremental p2p sync)
cp "$DIR/quartz-node.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable quartz-node >/dev/null 2>&1 || true
systemctl restart quartz-node   # ALWAYS restart: pick up new code/env

# 5. Local firewall (if ufw present): allow the node port on LAN
if command -v ufw >/dev/null; then
  ufw allow 21100/tcp >/dev/null 2>&1 || true
fi

sleep 2
echo
systemctl --no-pager -l status quartz-node | head -8
echo
echo "✅ Node running on port 21100 (standby + mining gateway)"
echo "   Logs:     journalctl -u quartz-node -f  → '🔄 Synced +N blocks' = p2p mode"
echo "   Test:     curl http://$(hostname -I 2>/dev/null | awk '{print $1}'):21100/api/v1/info"
echo "   Mine:     ESP32 captive portal → Node field = this Pi's LAN IP (no rebuild)"
echo
echo "Miners/stats live on whichever node builds the blocks — check:"
echo "  curl -s https://quartz.preview.saasclaw.ai/api/v1/info   (hardware miners, hashrate)"
