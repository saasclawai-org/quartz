# Quartz Node — Tor Hidden Service

The seed node is also reachable as a Tor v3 onion service:

    http://wa54mvjwubvjkry3xuxb7mc3w3gsk25tduioxu6mblckyabwtumralid.onion

## What it does

- Miners/clients with Tor can reach the node API without revealing their IP
- ESP32 can't run Tor, but the Pi node/bridge can (SOCKS proxy at 127.0.0.1:9050)
- Coexists with the Cloudflare-fronted HTTPS endpoint — both serve the same node

## Setup (already done on seed node)

```bash
sudo apt install tor
# Add to /etc/tor/torrc:
#   HiddenServiceDir /var/lib/tor/quartz-node/
#   HiddenServicePort 80 127.0.0.1:21100
sudo systemctl restart tor
cat /var/lib/tor/quartz-node/hostname  # → your .onion address
```

UFW: open 9001/tcp (ORPort) for inbound Tor relay connections.

## Testing

```bash
curl --socks5-hostname 127.0.0.1:9050 \
  http://wa54mvjwubvjkry3xuxb7mc3w3gsk25tduioxu6mblckyabwtumralid.onion/api/v1/info
```

## ESP32 via Pi bridge (future)

The Pi node runs Tor + a small proxy that forwards ESP32 HTTP requests
through the SOCKS port to the .onion address. ESP32 sees only the Pi's
LAN IP; the node sees only a Tor exit — no real IP on either end.
