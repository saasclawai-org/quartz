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
# LLM node (optional flavor): script only; service/key handled below.
# A missing llm_node.py (bare bundle) just means no LLM flavor — not an error.
[ -f "$DIR/llm_node.py" ] && cp "$DIR/llm_node.py" "$DEST/"
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

# ---------------------------------------------------------------------------
# 6. Optional: Quartz LLM node — pay-per-request local inference for QZ
#    (spike 001, validated on live testnet: payment → claim → signed receipt)
#
#      INSTALL_LLM=ollama  install Ollama + pull model (real inference)
#      INSTALL_LLM=mock    demo backend, no model (works on any Pi)
#      INSTALL_LLM=1       auto: ollama if already present, else mock
#      unset + interactive tty → ask; unset + no tty → skip silently
#
#    Price and model: PRICE_QZ=0.25 LLM_MODEL=qwen2.5:1.5b sudo bash install.sh
#    The node's wallet key (/opt/quartz-node/llm-node-key.json) is generated
#    on first start and NEVER clobbered by reinstalls.
# ---------------------------------------------------------------------------
INSTALL_LLM="${INSTALL_LLM:-}"
if [ -z "$INSTALL_LLM" ] && [ -t 0 ]; then
  read -r -p "Install the Quartz LLM node (sells inference for QZ on :8788)? [y/N] " _yn
  case "$_yn" in y|Y|yes|YES) INSTALL_LLM=1 ;; esac
fi

if [ -n "$INSTALL_LLM" ] && [ -f "$DEST/llm_node.py" ]; then
  echo
  echo "== LLM node =="
  # Signed Ed25519 receipts need PyNaCl
  if ! python3 -c "import nacl" 2>/dev/null; then
    ( apt-get install -y python3-pynacl 2>/dev/null \
        || pip3 install --break-system-packages pynacl 2>/dev/null \
        || pip3 install pynacl 2>/dev/null ) \
      || { echo "PyNaCl unavailable — LLM node needs it for signed receipts; skipping."; INSTALL_LLM=""; }
  fi
fi

if [ -n "$INSTALL_LLM" ]; then
  LLM_BACKEND_SEL="mock"
  LLM_MODEL_SEL="${LLM_MODEL:-qwen2.5:1.5b}"
  PRICE_QZ_SEL="${PRICE_QZ:-0.5}"

  case "$INSTALL_LLM" in
    ollama) command -v ollama >/dev/null 2>&1 || {
              echo "Installing Ollama (can take a few minutes)…"
              curl -fsSL https://ollama.com/install.sh | sh \
                || echo "Ollama install failed — falling back to mock backend"
            } ;;
  esac
  if command -v ollama >/dev/null 2>&1; then
    echo "Pulling model $LLM_MODEL_SEL …"
    ollama pull "$LLM_MODEL_SEL" \
      && LLM_BACKEND_SEL="ollama" \
      || echo "Model pull failed — staying on mock backend"
  fi

  cp "$DIR/quartz-llm.service" /etc/systemd/system/
  mkdir -p /etc/systemd/system/quartz-llm.service.d
  cat > /etc/systemd/system/quartz-llm.service.d/10-config.conf <<EOF
[Service]
Environment=LLM_BACKEND=$LLM_BACKEND_SEL
Environment=LLM_MODEL=$LLM_MODEL_SEL
Environment=PRICE_QZ=$PRICE_QZ_SEL
EOF
  systemctl daemon-reload
  systemctl enable quartz-llm >/dev/null 2>&1 || true
  systemctl restart quartz-llm
  command -v ufw >/dev/null && ufw allow 8788/tcp >/dev/null 2>&1 || true
  sleep 2

  LLM_ADDR=$(python3 -c "import json;print(json.load(open('$DEST/llm-node-key.json'))['address'])" 2>/dev/null || true)
  echo "✅ LLM node on port 8788 (backend=$LLM_BACKEND_SEL model=$LLM_MODEL_SEL price=$PRICE_QZ_SEL QZ)"
  if [ -n "$LLM_ADDR" ]; then
    echo "   Pay-to: $LLM_ADDR"
  fi
  echo "   Price:  curl http://localhost:8788/price"
  echo "   Logs:   journalctl -u quartz-llm -f"
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
echo "  curl -s https://quartzchain.net/api/v1/info   (hardware miners, hashrate)"
