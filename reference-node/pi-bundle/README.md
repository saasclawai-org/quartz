# Quartz Pi Node — your own gateway on the Quartz network

A full Quartz node + mining gateway that runs on any Raspberry Pi (or any
Linux box, ARM or x86). Pure Python 3 stdlib — zero dependencies, ~80 MB RAM,
<1% CPU. A Pi Zero 2 W is enough; Pi 4/5 is luxury.

## What this is

Three roles in one systemd service:

1. **Standby node** — continuously synced copy of the whole chain. Serves
   API, balances, block data to anyone on your LAN (or the internet if you
   expose it). No simulated miner runs here (`QUARTZ_NO_MINER=1`) — only
   real hardware mines through it.
2. **Mining gateway** — your ESP32s mine against THIS node on your LAN.
   Submits are relay-first: while your peers are reachable, blocks are
   built at the strongest tip in the network; rewards pay your miner's
   wallet address directly **in the coinbase** — the balance is carried by
   the block itself and recognized by every node.
3. **Autonomous failover** — if the upstream network is unreachable, the
   node builds blocks LOCALLY on its own synced chain. Your miners keep
   mining through an outage; the mesh reconverges when connectivity
   returns (longest chain wins, the loser resyncs).

## Install (on the Pi)

```bash
curl -LO https://quartzchain.net/downloads/quartz-pi-node.tar.gz
tar xzf quartz-pi-node.tar.gz
cd quartz-pi-node
sudo bash install.sh
```

That's it. The installer:
- copies node + chain snapshot to `/opt/quartz-node`
- installs + starts the `quartz-node` systemd service (auto-restart, enabled on boot)
- opens port 21100/tcp in ufw if present
- always restarts the service on reinstall (picks up new code) and
  preserves your existing chain data

Verify: `curl http://localhost:21100/api/v1/info` → JSON with chain height.

## Continuous chain sync (p2p)

The service syncs continuously via `QUARTZ_PEERS` — **no cron, no seed
monopoly**. List any number of peers (comma-separated): the seed, your
friends' Quartz Pis, anything running this node software:

```
Environment=QUARTZ_PEERS=https://quartzchain.net,http://192.168.1.42:21100
```

Every peer is polled; the node syncs **incrementally** (batched blocks + a
tiny state payload — kilobytes, not the 31 MB snapshot) from whichever peer
is furthest ahead. Foreign blocks pass full consensus validation
(prev-hash linkage + PoW + UTXO rules) before being applied. The 31 MB
snapshot is only pulled when bootstrapping (>200 blocks behind) or on deep
divergence. If a peer is unreachable it is skipped for that poll; if all
are, the node keeps serving its last good state.

Add YOUR node to other people's peer lists — any node can serve the chain
to any other. That's the mesh: N sources, no single point of failure.

Log signature of healthy p2p sync: `🔄 Synced +N blocks: X → Y` lines in
`journalctl -u quartz-node -f` every minute or so.

## Mine through your own node

Point your ESP32 at the Pi — **no firmware rebuild needed**:

1. Flash the latest firmware (single merged image, no toolchain needed):
   `https://quartzchain.net/downloads/` →
   `quartz-esp32-merged.bin` (classic ESP32) or `quartz-s3-merged.bin` (S3)
   → `esptool write_flash 0x0 <file>`. Wallet and settings in NVS survive
   a re-flash; do NOT `erase_flash` (that wipes the device's private key).
2. On first boot the miner opens a captive portal (`Quartz-XXXX` AP →
   http://192.168.4.1). Enter WiFi credentials **and the Node field**:
   `192.168.1.142` (your Pi's LAN IP; `host:port` also works — port 21100
   if you changed it). Re-check this field any time by re-opening the
   portal (serial command `wifi` resets provisioning).
3. The miner now works + submits through YOUR node. Check the box score:

```bash
# local: chain height + peers (miner stats live where blocks are built)
curl -s localhost:21100/api/v1/info | python3 -m json.tool
journalctl -u quartz-node -f          # 🔄 Synced lines, ⛏ local blocks on failover

# network view: hardware miner count, hashrate, recent blocks by miner id
curl -s https://quartzchain.net/api/v1/info
curl -s https://quartzchain.net/api/v1/blocks/since/40750

# the receipts: rewards pay the device wallet directly in the coinbase
curl -s https://quartzchain.net/api/v1/address/<DEVICE_ADDRESS>
```

While relaying (normal mode), `hardware_miners`/`total_hashrate` read ~0
locally — the stats live on whichever node built the block. That's
expected, not a bug.

Set `QUARTZ_LOCAL_MINE=1` to always build locally (fully seedless mesh).
If the upstream is unreachable, non-mining relays (faucet, sends) return
a clear 502; local chain serving always continues.

## WiFi provisioning recovery (firmware)

If a miner fails to connect after a bad SSID/password: serial terminal
(COM port, 115200 baud) → type `wifi` + Enter → it wipes provisioning and
re-opens the captive portal. Notes: ESP32 is 2.4 GHz only; portal fields
are case-sensitive. Never use `erase_flash` — NVS holds the wallet key.

## Making it public (optional)

Behind a home router the Pi is LAN-only by default. Options:
- **Cloudflare Tunnel** (recommended, no port-forward): `cloudflared tunnel`
  → exposes `quartz-pi.yourdomain` to the node port
- **Port-forward** 21100 (or reverse-proxy 443 → 21100 with nginx/caddy)
- **LAN-only** is fine too: miners/bridge on your LAN use it directly

## Future

This same box is the natural **mesh gateway**: USB-attach an ESP32 (or a
LoRa HAT) and it bridges radio traffic to the chain — the Helium-gateway
topology. The service runs as-is today; gateway duty is added later without
reinstalling.
