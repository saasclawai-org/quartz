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

## Continuous chain sync

The service runs with `QUARTZ_SYNC_URL=https://quartz.preview.saasclaw.ai`, so
the node **syncs continuously** — no cron needed:

- polls the seed's `/api/v1/info` every 30 s (`QUARTZ_SYNC_INTERVAL`)
- pulls a fresh snapshot when ≥5 blocks behind (`QUARTZ_SYNC_MIN_LAG`) or when
  ≥1 block behind for >5 min (`QUARTZ_SYNC_MAX_LAG_SECONDS`)
- validates, atomically replaces `chain.json`, rebuilds state, and hot-swaps
  the serving chain — zero downtime, never serves partial state
- if the seed is unreachable it keeps serving its last good snapshot and retries

Tuning (optional, in the unit file): lower `QUARTZ_SYNC_MIN_LAG` to 1 for a
tight 1-block-follow; raise the intervals if the Pi's bandwidth matters more
than freshness.

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
