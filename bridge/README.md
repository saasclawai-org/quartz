# Quartz Bridge — Helium Hotspot to LoRa Mesh Relay

Turns a Helium LoRaWAN hotspot (Bobcat Miner 300, RAK Wireless, SenseCAP) into a
**Quartz bridge node** — relaying Quartz blockchain data between the online
gateway and offline ESP32 leaf miners via LoRa.

## What It Does

```
Quartz Gateway (VPS)  ←──TCP P2P──→  Bridge (Helium hotspot)  ←──LoRa──→  ESP32 miners
                                         │
                                    SX130x concentrator
                                    (8-channel LoRa radio)
```

The bridge:
- Connects to a Quartz gateway over TCP (P2P gossip protocol)
- Receives new blocks and work templates from the gateway
- Relays them to ESP32 miners over LoRa (2-15km range)
- Receives found blocks and transactions from ESP32s via LoRa
- Submits them to the gateway

**The bridge does NOT mine.** It has no ESP32 PUF. It's a relay node.

## Why Helium Hotspots?

Hundreds of thousands of Helium LoRaWAN hotspots are deployed worldwide,
sitting on rooftops earning $0.05–$5/month. They're perfect Quartz bridge
hardware:

| Component | Helium Hotspot | Quartz Bridge Need |
|-----------|---------------|-------------------|
| Linux SoC | ✅ (Pi CM4, ARM) | ✅ Runs Python daemon |
| LoRa radio | ✅ SX1302 (8-ch) | ✅ Better than ESP32 SX1276 |
| Storage | ✅ SD/eMMC | ✅ Full chain on disk |
| WiFi/Eth | ✅ | ✅ Gateway backhaul |
| Solar/power | ✅ Already deployed | ✅ No new install needed |
| Cost | Already paid for | $0 (reuse) |

The SX1302 concentrator is actually *better* for bridging than the ESP32's
SX1276 — it can listen on 8+ channels simultaneously, serving many more
leaf miners.

## Requirements

- Helium hotspot with SSH access (Bobcat, RAK, SenseCAP, or any SX130x-based gateway)
- Python 3.8+ (most hotspots have this)
- The hotspot's original Semtech packet forwarder binary (we reconfigure it, not replace it)
- A Quartz gateway to connect to (default: `quartzchain.net`)

## Install

```bash
# Copy the bridge files to the hotspot (via SSH/scp)
scp -r bridge/ root@hotspot-ip:/tmp/

# SSH in and run the installer
ssh root@hotspot-ip
cd /tmp/bridge
sudo bash install.sh
```

The installer:
1. Detects your hotspot model (Bobcat, RAK, SenseCAP)
2. Backs up the Helium packet forwarder config
3. Reconfigures the packet forwarder to send to the bridge (UDP 1738)
4. Installs the bridge daemon to `/opt/quartz-bridge/`
5. Creates systemd services for both the bridge and the packet forwarder
6. Starts everything up

### Environment Variables (optional)

```bash
# Override defaults during install
GATEWAY_HOST=quartzchain.net LORA_REGION=US915 sudo bash install.sh
```

| Variable | Default | Options |
|----------|---------|---------|
| `GATEWAY_HOST` | `quartzchain.net` | Any Quartz gateway hostname |
| `LORA_REGION` | `US915` | `US915`, `EU868`, `AS923`, `CN470`, `IN865` |
| `LORA_UDP_PORT` | `1738` | UDP port for packet forwarder |

## How It Works

### Packet Forwarder Reconfiguration

Helium hotspots run a Semtech packet forwarder (lora_pkt_fwd or basicstation)
that talks to the SX130x concentrator over SPI and sends/receives LoRa
packets over UDP. Normally it sends packets to Helium's servers.

The installer reconfigures it to send to `127.0.0.1:1738` (the bridge)
instead of Helium's servers. The bridge then:

1. Receives raw LoRa packets from ESP32 miners
2. Parses Quartz mesh protocol packets (beacons, block headers, transactions)
3. Relays valid data to the Quartz gateway over TCP
4. Receives new blocks/work from the gateway
5. Sends them out over LoRa via the packet forwarder

### LoRa Mesh Protocol

The bridge uses the same LoRa mesh protocol as the ESP32 firmware:

| Packet Type | Size | Direction | Purpose |
|-------------|------|-----------|---------|
| `BEACON` | 12B payload | Bridge → miners | "I'm here, chain height = N" |
| `BLOCK_HDR` | 80B payload | Both | Block header announcement |
| `TX` | ≤200B payload | Both | Transaction relay |
| `BLOCK_REQ` | 4B payload | Miner → bridge | "Send me block #N" |
| `PING`/`PONG` | 0B | Both | Mesh connectivity test |

All packets use a 12-byte mesh header with TTL-based gossip flooding.

### Gateway Connection

The bridge connects to the Quartz gateway using the P2P protocol from
`reference-node/quartz/p2p.py`:

- Length-prefixed JSON messages over TCP
- HELLO handshake with `capabilities: ["bridge_node"]`
- Receives `new_block` and `new_tx` messages (gossiped from the gateway)
- Submits blocks found by leaf miners via `new_block` messages
- Also uses the HTTP API (`/api/v1/mining/work`) for work templates

## Monitoring

```bash
# Bridge logs
journalctl -u quartz-bridge -f

# LoRa packet forwarder logs
journalctl -u quartz-lora-fwd -f

# Check if bridge is connected to gateway
journalctl -u quartz-bridge | grep "Connected to gateway"
```

## Reverting to Helium

If you want to go back to mining Helium:

```bash
sudo systemctl stop quartz-bridge quartz-lora-fwd
sudo systemctl disable quartz-bridge quartz-lora-fwd

# Restore original packet forwarder config
sudo cp /opt/quartz-bridge/backup/*.bak.* /opt/<hotspot>/

# Restart original Helium services
sudo systemctl start helium-miner  # or lora-pkt-fwd, depending on model
```

Your Helium backup configs are in `/opt/quartz-bridge/backup/`.

## Supported Hardware

| Hotspot | SoC | LoRa Chip | Status |
|---------|-----|-----------|--------|
| Bobcat Miner 300 | Pi CM4 | SX1302 | ✅ Auto-detected |
| RAK Wireless | Pi Zero / ARM | SX1302 | ✅ Auto-detected |
| SenseCAP M1 | Pi CM4 | SX1302 | ✅ Auto-detected |
| Milesight UG65 | ARM | SX1302 | ✅ Auto-detected |
| Fineoffset | ARM | SX1301 | ✅ Generic fallback |
| Any SX130x gateway | Any Linux | SX130x | ✅ Generic fallback |

## Architecture Notes

### Why UDP packet forwarder (not direct SPI)?

The bridge talks to the SX130x through the existing Semtech packet forwarder
binary (which is already compiled for the exact hardware/SPI config of each
hotspot model). This avoids needing board-specific SPI drivers — the packet
forwarder handles that. We just redirect its UDP traffic to our bridge
process instead of Helium's servers.

### LoRa configuration

The bridge uses the same LoRa parameters as the ESP32 firmware:

- **Frequency**: Region-dependent (US915, EU868, etc.)
- **Bandwidth**: 125 kHz
- **Spreading Factor**: SF7 (~2km range, fastest data rate)
- **Coding Rate**: 4/5
- **Sync Word**: 0x51 ('Q' for Quartz)

### Future: Direct SX130x driver

For hotspots where we can access the SPI device directly, a future version
will bypass the packet forwarder entirely and drive the SX130x through a
Python SPI library. This would allow:

- Multi-channel reception (SX130x can listen on 8 channels)
- Precise timestamping
- Better TX scheduling
- No dependency on the original packet forwarder binary

## License

MIT — same as the rest of Quartz.

## Links

- [Quartz README](../README.md)
- [Quartz Whitepaper](../docs/WHITEPAPER.md)
- [LoRa Mesh Protocol (firmware)](../firmware-c3/main/quartz_lora.h)
- [P2P Protocol (reference node)](../reference-node/quartz/p2p.py)
- [Website](https://quartzchain.net)