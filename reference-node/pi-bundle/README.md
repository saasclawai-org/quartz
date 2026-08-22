# Quartz Pi Node — standby testnet node

A full Quartz testnet node that runs on any Raspberry Pi (or any Linux box,
ARM or x86). Pure Python 3 stdlib — zero dependencies, ~80 MB RAM, <1% CPU.
A Pi Zero 2 W is enough; Pi 4/5 is luxury.

## What this is

A **read-only standby node**: it serves the chain snapshot (API, block
explorer data, balances) but does **not** mine (`QUARTZ_NO_MINER=1`), so it
can never fork away from the seed node. If the seed node goes down, point
DNS at the Pi and it serves the chain from its last snapshot.

## Install (on the Pi)

```bash
tar xzf quartz-pi-node.tar.gz
cd quartz-pi-node
sudo bash install.sh
```

That's it. The installer:
- copies node + chain snapshot to `/opt/quartz-node`
- installs + starts the `quartz-node` systemd service (auto-restart, enabled on boot)
- opens port 21100/tcp in ufw if present

Verify: `curl http://<pi-ip>:21100/api/v1/info` → JSON with chain height.

## Continuous chain sync (p2p)

The service syncs continuously via `QUARTZ_PEERS` — **no cron, no seed
monopoly**. List any number of peers (comma-separated): the seed, your
friends' Quartz Pis, anything running this node software:

```
Environment=QUARTZ_PEERS=https://quartz.preview.saasclaw.ai,http://192.168.1.42:21100
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

## Mine through your own node

This bundle runs in **gateway mode**: it is a standby node (continuously
synced) *plus* a mining relay. ESP32s point at the Pi's LAN address
(`NODE_HOST` in `quartz_wifi.c`, then rebuild firmware) — their work fetches
and block submits are relayed to the upstream consensus node, while all
reads (explorer, balances, messages) are served locally from the synced
chain. Miners on your LAN keep working even if your internet is slow;
if the upstream is unreachable, submits return a clear 502 and local
serving continues.

Configure via the unit file:

- `QUARTZ_SYNC_URL` — chain sync source (continuous, hot-swap)
- `QUARTZ_RELAY_URL` — where mining submits are forwarded
  (remove to run a pure read-only standby node)

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
