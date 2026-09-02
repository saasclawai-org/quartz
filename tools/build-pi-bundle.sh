#!/usr/bin/env bash
# Build the Raspberry Pi node bundle from the current repo + live chain snapshot.
# Usage: bash tools/build-pi-bundle.sh [output-dir]
# Publish: cp result to /srv/saasclaw/projects/quartz/repo/downloads/
#          (keep the dated name AND overwrite unversioned quartz-pi-node.tar.gz)
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-/tmp}"
STAMP="$(date +%Y%m%d)"
STAGE_PARENT="$(mktemp -d)"
STAGE="$STAGE_PARENT/quartz-pi-node"
mkdir -p "$STAGE"

cp -r reference-node/quartz "$STAGE/"
cp reference-node/testnet.py "$STAGE/"
cp reference-node/pi-bundle/install.sh \
   reference-node/pi-bundle/quartz-node.service \
   reference-node/pi-bundle/README.md "$STAGE/"

# Chain snapshot: bake the live node's current chain so fresh installs start at
# the tip. (Reinstalls never clobber existing on-Pi chain data — install.sh.)
mkdir -p "$STAGE/testnet-data"
if [ -f /srv/quartz-node/testnet-data/chain.json ]; then
  cp /srv/quartz-node/testnet-data/chain.json "$STAGE/testnet-data/"
else
  echo "WARNING: no live chain.json found — bundle will have an empty snapshot"
fi

find "$STAGE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

tar czf "$OUT/quartz-pi-node-$STAMP.tar.gz" -C "$STAGE_PARENT" quartz-pi-node
echo "Built $OUT/quartz-pi-node-$STAMP.tar.gz ($(stat -c%s "$OUT/quartz-pi-node-$STAMP.tar.gz") bytes)"
