# Quartz Pi Node — full testnet node

A full Quartz testnet node that runs on any Raspberry Pi (or any Linux box,
ARM or x86). Pure Python 3 stdlib — zero dependencies, low RAM/CPU.
A Pi Zero 2 W is enough; Pi 4/5 is luxury.

## What this is

A **real node**, not a mirror: it holds a full copy of the chain and keeps
it current by itself — incremental peer sync (kilobytes per batch, every
block fully validated: PoW, linkage, transaction rules), no cron, no
babysitting. It never mines (`QUARTZ_NO_MINER=1`), so it can never fork
the chain. If the seed goes down, your Pi keeps serving its last verified
state; when the seed returns, sync resumes automatically.

Port 21100 is open on LAN — other Quartz nodes and ESP32 miners on your
network can use your Pi as their gateway.

## Install (on the Pi)

```bash
tar xzf quartz-pi-node.tar.gz
cd quartz-pi-node
sudo bash install.sh
```

The installer copies node + chain snapshot to `/opt/quartz-node`, installs
and starts the `quartz-node` systemd service (auto-restart, enabled on
boot), and opens port 21100/tcp in ufw if present.

Re-running `install.sh` over an existing install is safe — it replaces
the code, drops stale overrides, and restarts the service.

Verify: `curl http://<pi-ip>:21100/api/v1/info` → JSON with chain height
matching https://quartzchain.net/api/v1/info.

## Testnet resets — automatic

The Quartz testnet gets reset occasionally (e.g. consensus upgrades before
mainnet). Your node handles this by itself: if it detects a peer running a
different genesis, it archives its old copy, and re-syncs the new chain
from scratch. No action needed on your side. (Old chain is kept as
`/opt/quartz-node/testnet-data/chain.json.stale-*` — delete those whenever.)

## Manual snapshot refresh (optional, not needed)

Sync is automatic. If you ever want to force a full re-pull:

```bash
curl -o /opt/quartz-node/testnet-data/chain.json \
  https://quartzchain.net/api/v1/snapshot
sudo systemctl restart quartz-node
```

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
