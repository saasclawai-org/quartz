#!/usr/bin/env bash
# Quartz Bridge Installer for Helium Hotspots
#
# Works on: Bobcat Miner 300, RAK Wireless, SenseCAP, and other
# Linux-based Helium hotspots with SX130x LoRa concentrator.
#
# What it does:
#   1. Checks for Python 3 and SX130x packet forwarder
#   2. Installs quartz_bridge.py to /opt/quartz-bridge/
#   3. Backs up the Helium packet forwarder config
#   4. Reconfigures the packet forwarder to send to the bridge
#   5. Installs + starts the quartz-bridge systemd service
#
# License: MIT
set -euo pipefail

# The bridge files (quartz_bridge.py, etc.) should be in the same
# directory as this script. They're either:
#   - Downloaded by bridge-install.sh (the curl one-liner), or
#   - Copied manually via scp
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="/opt/quartz-bridge"
FORWARDER_BACKUP=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Must be root
[ "$(id -u)" -eq 0 ] || { error "Run with sudo: sudo bash install.sh"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Quartz Bridge Installer for Helium    ║"
echo "║         Hotspot Hardware                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. Detect hotspot model
# ---------------------------------------------------------------------------
info "Detecting hotspot model..."

HOTSPOT_MODEL="unknown"
PACKET_FORWARDER_PATH=""
PACKET_FORWARDER_CONFIG=""

# Bobcat Miner 300
if [ -d "/opt/bobcat" ] || ls /opt/bobcat* 2>/dev/null | grep -q .; then
    HOTSPOT_MODEL="Bobcat Miner 300"
    PACKET_FORWARDER_PATH="/opt/bobcat/packet_forwarder"
    # Bobcat uses a custom config in /opt/bobcat/
    for f in /opt/bobcat/*.json /opt/bobcat/*/local_conf.json /opt/bobcat/*/global_conf.json; do
        if [ -f "$f" ]; then
            PACKET_FORWARDER_CONFIG="$f"
            break
        fi
    done

# RAK Wireless
elif [ -d "/opt/rak" ] || ls /opt/rak* 2>/dev/null | grep -q .; then
    HOTSPOT_MODEL="RAK Wireless"
    for f in /opt/rak/lora_pkt_fwd/*global*conf* /opt/rak/packet_forwarder/*global*conf*; do
        if [ -f "$f" ]; then
            PACKET_FORWARDER_CONFIG="$f"
            PACKET_FORWARDER_PATH="$(dirname "$f")/lora_pkt_fwd"
            break
        fi
    done

# SenseCAP / Milesight
elif [ -d "/opt/sensecap" ] || [ -d "/opt/milesight" ]; then
    HOTSPOT_MODEL="SenseCAP/Milesight"
    for f in /opt/sensecap/*/global_conf.json /opt/milesight/*/global_conf.json; do
        if [ -f "$f" ]; then
            PACKET_FORWARDER_CONFIG="$f"
            break
        fi
    done

# Generic: look for any packet forwarder
else
    for path in /opt/*/lora_pkt_fwd /opt/*/packet_forwarder /usr/local/bin/lora_pkt_fwd; do
        if [ -d "$path" ] || [ -x "$path" ]; then
            PACKET_FORWARDER_PATH="$path"
            for f in "$path"/*global*conf*; do
                if [ -f "$f" ]; then
                    PACKET_FORWARDER_CONFIG="$f"
                    break
                fi
            done
            break
        fi
    done
fi

if [ "$HOTSPOT_MODEL" != "unknown" ]; then
    ok "Detected: $HOTSPOT_MODEL"
else
    warn "Could not auto-detect hotspot model. Continuing with generic setup..."
fi

if [ -n "$PACKET_FORWARDER_CONFIG" ] && [ -f "$PACKET_FORWARDER_CONFIG" ]; then
    ok "Packet forwarder config: $PACKET_FORWARDER_CONFIG"
else
    warn "Packet forwarder config not found. You'll need to reconfigure it manually."
    warn "See: https://github.com/saasclawai-org/quartz/blob/main/bridge/README.md"
fi

# ---------------------------------------------------------------------------
# 2. Check Python
# ---------------------------------------------------------------------------
info "Checking Python 3..."
if command -v python3 >/dev/null 2>&1; then
    PYVER=$(python3 --version 2>&1)
    ok "Python 3 found: $PYVER"
else
    error "Python 3 not found. Install it first:"
    error "  apt-get install python3  (Debian/Ubuntu/OpenWrt)"
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Stop Helium packet forwarder (if running)
# ---------------------------------------------------------------------------
if [ -n "$PACKET_FORWARDER_PATH" ]; then
    info "Checking for running packet forwarder..."
    PF_PID=$(pgrep -f "lora_pkt_fwd\|packet_forwarder\|basicstation" 2>/dev/null | head -1 || true)
    if [ -n "$PF_PID" ]; then
        info "Stopping Helium packet forwarder (PID $PF_PID)..."
        # Try systemd first, then kill directly
        if systemctl is-active --quiet helium-miner 2>/dev/null; then
            systemctl stop helium-miner
            systemctl disable helium-miner
            ok "Stopped + disabled helium-miner service"
        elif systemctl is-active --quiet lora-pkt-fwd 2>/dev/null; then
            systemctl stop lora-pkt-fwd
            systemctl disable lora-pkt-fwd
            ok "Stopped + disabled lora-pkt-fwd service"
        else
            kill "$PF_PID" 2>/dev/null || true
            ok "Killed packet forwarder process"
        fi
    else
        info "No packet forwarder currently running"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Backup packet forwarder config
# ---------------------------------------------------------------------------
BACKUP_DIR="/opt/quartz-bridge/backup"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -n "$PACKET_FORWARDER_CONFIG" ] && [ -f "$PACKET_FORWARDER_CONFIG" ]; then
    info "Backing up packet forwarder config..."
    cp "$PACKET_FORWARDER_CONFIG" "$BACKUP_DIR/$(basename "$PACKET_FORWARDER_CONFIG").bak.$TIMESTAMP"
    ok "Backup saved to $BACKUP_DIR"

    # Also grab any local_conf.json in the same directory
    PF_DIR=$(dirname "$PACKET_FORWARDER_CONFIG")
    if [ -f "$PF_DIR/local_conf.json" ]; then
        cp "$PF_DIR/local_conf.json" "$BACKUP_DIR/local_conf.json.bak.$TIMESTAMP"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Reconfigure packet forwarder for Quartz
# ---------------------------------------------------------------------------
LORA_UDP_PORT=${LORA_UDP_PORT:-1738}
LORA_REGION=${LORA_REGION:-US915}

if [ -n "$PACKET_FORWARDER_CONFIG" ] && [ -f "$PACKET_FORWARDER_CONFIG" ]; then
    info "Reconfiguring packet forwarder for Quartz (UDP port $LORA_UDP_PORT)..."

    # The Semtech packet forwarder uses a "server_address" and "serv_port_up"
    # in global_conf.json. We point it at localhost instead of Helium servers.
    python3 -c "
import json, sys
config_path = '$PACKET_FORWARDER_CONFIG'
with open(config_path) as f:
    config = json.load(f)

# Change gateway server to our local bridge
config['gateway_conf'] = config.get('gateway_conf', {})
config['gateway_conf']['server_address'] = '127.0.0.1'
config['gateway_conf']['serv_port_up'] = $LORA_UDP_PORT
config['gateway_conf']['serv_port_down'] = $LORA_UDP_PORT

# Keep SX130x radio config (freq, gain, etc) — we need the radio hardware
# but we change the server destination

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
print('Done')
" 2>/dev/null && ok "Packet forwarder reconfigured" || warn "Could not auto-reconfigure. Manual edit needed."
else
    warn "No packet forwarder config to reconfigure."
    warn "You'll need to manually configure your SX130x to send to 127.0.0.1:$LORA_UDP_PORT"
fi

# ---------------------------------------------------------------------------
# 6. Install bridge files
# ---------------------------------------------------------------------------
info "Installing bridge to $DEST..."
mkdir -p "$DEST"
cp "$SCRIPT_DIR/quartz_bridge.py" "$DEST/"
chmod +x "$DEST/quartz_bridge.py"

# Determine LoRa frequency from region
case "$LORA_REGION" in
    US915) LORA_FREQ=915000000 ;;
    EU868) LORA_FREQ=868100000 ;;
    AS923) LORA_FREQ=923200000 ;;
    CN470) LORA_FREQ=470000000 ;;
    IN865) LORA_FREQ=865000000 ;;
    *)     LORA_FREQ=915000000 ;;
esac

# ---------------------------------------------------------------------------
# 7. Create systemd service
# ---------------------------------------------------------------------------
GATEWAY_HOST=${GATEWAY_HOST:-quartzchain.net}

info "Creating systemd service..."
cat > /etc/systemd/system/quartz-bridge.service << EOF
[Unit]
Description=Quartz Bridge — LoRa mesh relay for Quartz network
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$DEST/quartz_bridge.py \\
    --gateway $GATEWAY_HOST \\
    --region $LORA_REGION \\
    --lora-port $LORA_UDP_PORT \\
    --log-level info
Restart=always
RestartSec=10
KillMode=control-group
TimeoutStopSec=10
FinalKillSignal=SIGKILL

# Run as root (needs access to packet forwarder + SPI)
User=root

# Environment
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable quartz-bridge
ok "Service installed: quartz-bridge.service"

# ---------------------------------------------------------------------------
# 8. Restart packet forwarder (pointed at our bridge now)
# ---------------------------------------------------------------------------
if [ -n "$PACKET_FORWARDER_PATH" ] && [ -d "$PACKET_FORWARDER_PATH" ]; then
    info "Starting packet forwarder (now pointing at bridge)..."

    # Try to start via the original binary
    PF_BINARY=""
    for bin in lora_pkt_fwd packet_forwarder basicstation; do
        if [ -x "$PACKET_FORWARDER_PATH/$bin" ]; then
            PF_BINARY="$PACKET_FORWARDER_PATH/$bin"
            break
        fi
    done

    if [ -z "$PF_BINARY" ] && [ -x "$PACKET_FORWARDER_PATH" ]; then
        PF_BINARY="$PACKET_FORWARDER_PATH"
    fi

    if [ -n "$PF_BINARY" ]; then
        # Create a systemd service for the packet forwarder
        cat > /etc/systemd/system/quartz-lora-fwd.service << EOF
[Unit]
Description=Quartz LoRa Packet Forwarder (SX130x)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$(dirname "$PF_BINARY")
ExecStart=$PF_BINARY
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=10
FinalKillSignal=SIGKILL

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable quartz-lora-fwd
        systemctl start quartz-lora-fwd
        ok "Packet forwarder started"
    else
        warn "Could not find packet forwarder binary. Start it manually."
    fi
fi

# ---------------------------------------------------------------------------
# 9. Start bridge
# ---------------------------------------------------------------------------
info "Starting Quartz Bridge..."
systemctl start quartz-bridge
sleep 2

# ---------------------------------------------------------------------------
# 10. Status report
# ---------------------------------------------------------------------------
echo ""
systemctl --no-pager -l status quartz-bridge 2>&1 | head -12
echo ""

ok "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Verify bridge is connected: journalctl -u quartz-bridge -f"
echo "  2. Check LoRa radio: journalctl -u quartz-lora-fwd -f"
echo "  3. Flash an ESP32 with Quartz firmware: https://quartzchain.net/download.html"
echo "  4. Power on ESP32 near the hotspot — it should pick up the LoRa mesh"
echo ""
echo "To change gateway or LoRa settings:"
echo "  Edit /etc/systemd/system/quartz-bridge.service"
echo "  Then: systemctl daemon-reload && systemctl restart quartz-bridge"
echo ""
echo "To revert to Helium (if you change your mind):"
echo "  systemctl stop quartz-bridge quartz-lora-fwd"
echo "  systemctl disable quartz-bridge quartz-lora-fwd"
echo "  cp $BACKUP_DIR/*.bak.* <original locations>"
echo "  (Restart original Helium services)"
echo ""