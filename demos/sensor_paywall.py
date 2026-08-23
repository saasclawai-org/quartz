#!/usr/bin/env python3
"""
Quartz Sensor Paywall — pay-per-read sensor data demo.

The inverse of sensor_logger: instead of publishing every reading
on-chain for free, this device SELLs readings. Payment is the trigger,
and the reading is delivered on-chain as a message addressed only to
the payer.

Flow:
  1. Device displays a payment request (QR / quartz: URI) for one reading
  2. Customer pays QZ to the device address (web/Android wallet)
  3. Device polls the node; on a confirmed incoming payment it
     identifies the PAYER from the transaction (spender's address)
  4. Device reads the sensor (DS18B20 or simulated)
  5. Reading is posted on-chain as a message TO the payer:
         TEMP:3.4C|HUM:51%|DEV:cold-01|PAID:0.1QZ|TS:...
     Only the payer receives it — the data itself never hits the chain
     until someone pays for it.

Works with:
  - Real DS18B20 on Raspberry Pi GPIO4 (1-Wire)
  - Simulated readings (no hardware needed)

Usage:
  # Simulated sensor, terminal demo:
  python3 sensor_paywall.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \
      --price 0.1 --device-id cold-01

  # Real DS18B20, serve one reading then exit (scripted/CI mode):
  python3 sensor_paywall.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \
      --price 0.1 --device-id cold-01 --sensor ds18b20 --once

License: MIT
"""

import argparse
import json
import random
import signal
import sys
import time
import urllib.request
import urllib.error
import logging

logger = logging.getLogger("quartz.paywall")


class SensorPaywall:
    """Sells sensor readings for QZ. Payment detection is on-chain;
    delivery is an on-chain message to the payer."""

    def __init__(self, node_url: str, address: str, price_qz: float,
                 device_id: str = "sensor-01", sensor_type: str = "sim",
                 poll_interval: int = 5, timeout: int = 0):
        self.node_url = node_url.rstrip('/')
        self.address = address
        self.price_qz = price_qz
        self.price_sats = int(price_qz * 1e8)
        self.device_id = device_id
        self.sensor_type = sensor_type
        self.poll_interval = poll_interval
        self.timeout = timeout          # 0 = wait forever
        self.running = True
        self.served_txids = set()       # one payment -> exactly one reading
        self.readings_served = 0
        self.start_time = time.time()

    # ── Sensor ────────────────────────────────────────────────
    def _read_ds18b20(self) -> dict:
        import glob
        devices = glob.glob("/sys/bus/w1/devices/28-*")
        if not devices:
            logger.warning("No DS18B20 found — falling back to simulated")
            return self._read_sim()
        with open(f"{devices[0]}/w1_slave") as f:
            raw = f.read()
        if "YES" not in raw:
            raise RuntimeError("DS18B20 CRC failed")
        temp = int(raw.split("t=")[-1]) / 1000.0
        return {"temp": round(temp, 1)}

    def _read_sim(self) -> dict:
        if not hasattr(self, "_sim_temp"):
            self._sim_temp = 3.5
        self._sim_temp += random.uniform(-0.3, 0.3)
        self._sim_temp = max(1.5, min(6.0, self._sim_temp))
        return {"temp": round(self._sim_temp, 1),
                "humid": round(random.uniform(40, 60), 1)}

    # ── Node API ──────────────────────────────────────────────
    def _get(self, path: str) -> dict:
        try:
            req = urllib.request.Request(self.node_url + path)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"Poll failed: {e}")
            return {}

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.node_url + path, data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"error": e.read().decode("utf-8", "replace")}
        except Exception as e:
            return {"error": str(e)}

    def paid_readings_waiting(self) -> list:
        """Confirmed incoming payments we haven't served yet.
        Returns [(txid, payer_address, amount_sats), ...]."""
        info = self._get(f"/api/v1/address/{self.address}")
        out = []
        for tx in info.get("transactions", []):
            if tx.get("direction") != "in":
                continue
            txid = tx.get("txid", "")
            if txid in self.served_txids:
                continue
            # Only payments that arrived after we started (don't re-serve
            # ancient history on first boot)
            if tx.get("timestamp", 0) < self.start_time:
                self.served_txids.add(txid)
                continue
            payer = tx.get("counterparty")
            if not payer:
                # Coinbase / synthetic credit — no payer to deliver to; skip
                self.served_txids.add(txid)
                continue
            if tx.get("amount_sats", 0) >= self.price_sats:
                out.append((txid, payer, tx["amount_sats"]))
        return out

    # ── The sale ──────────────────────────────────────────────
    def serve_reading(self, payer: str, amount_sats: int) -> bool:
        reading = self._read_ds18b20() if self.sensor_type == "ds18b20" \
            else self._read_sim()
        text = "|".join([
            f"TEMP:{reading['temp']}C",
            f"HUM:{reading['humid']}%" if "humid" in reading else "",
            f"DEV:{self.device_id}",
            f"PAID:{amount_sats / 1e8:g}QZ",
            f"TS:{int(time.time())}",
        ]).replace("||", "|")

        resp = self._post("/api/v1/messages/send", {
            "from": self.device_id,
            "to": payer,
            "text": text,
        })
        ok = "txid" in resp
        if ok:
            self.readings_served += 1
            print(f"  🌡 {text}")
            print(f"  📨 delivered on-chain to {payer[:16]}... "
                  f"(txid {resp['txid']}, ~30s to confirm)")
        else:
            print(f"  ❌ delivery failed: {resp.get('error', resp)}")
        return ok

    def display_request(self):
        uri = f"quartz:{self.address}?amount={self.price_qz:.8f}"
        if self.device_id:
            uri += f"&label={self.device_id}%20reading"
        print()
        print("=" * 60)
        print(f"  🌡 Sensor reading: {self.price_qz} QZ")
        print(f"  Device: {self.device_id} ({self.sensor_type})")
        print()
        print(f"  {uri}")
        print()
        print(f"  Pay from the Quartz wallet, then wait —")
        print(f"  the reading arrives as an on-chain message to you.")
        print("=" * 60)
        print()

    def run(self):
        self.display_request()
        print(f"  Waiting for payments... (Ctrl-C to stop)")
        while self.running:
            if self.timeout and (time.time() - self.start_time) > self.timeout:
                print("\n⏰ Timed out with no payment.")
                return
            for txid, payer, amount in self.paid_readings_waiting():
                print(f"\n✅ Payment: {amount / 1e8:g} QZ from {payer[:16]}...")
                self.serve_reading(payer, amount)
                self.served_txids.add(txid)
            time.sleep(self.poll_interval)

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(
        description="Quartz pay-per-read sensor demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simulated sensor, serve forever:
  python3 sensor_paywall.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --price 0.1 --device-id cold-01

  # Real DS18B20 (Pi, GPIO4, dtoverlay=w1-gpio):
  python3 sensor_paywall.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --price 0.1 --device-id cold-01 --sensor ds18b20
        """,
    )
    parser.add_argument('--node', default='https://quartzchain.net',
                        help='Quartz node URL')
    parser.add_argument('--address', required=True,
                        help='Device wallet address (receives payments)')
    parser.add_argument('--price', type=float, default=0.1,
                        help='Price per reading in QZ (default: 0.1)')
    parser.add_argument('--device-id', default='sensor-01',
                        help='Device name shown to the payer')
    parser.add_argument('--sensor', choices=['sim', 'ds18b20'], default='sim',
                        help='Sensor type (default: sim)')
    parser.add_argument('--poll-interval', type=int, default=5,
                        help='Payment poll interval in seconds (default: 5)')
    parser.add_argument('--timeout', type=int, default=0,
                        help='Give up after N seconds (0 = forever)')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    paywall = SensorPaywall(
        node_url=args.node,
        address=args.address,
        price_qz=args.price,
        device_id=args.device_id,
        sensor_type=args.sensor,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )

    def handler(signum, frame):
        print("\nStopping...")
        paywall.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    paywall.run()


if __name__ == "__main__":
    main()
