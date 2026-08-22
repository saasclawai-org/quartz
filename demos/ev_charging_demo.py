#!/usr/bin/env python3
"""
Quartz EV Charging Demo — pay-per-charge using QZ.

Simulates an EV charging station that:
  1. Displays QR code for payment (e.g., 5 QZ for 30 minutes of charging)
  2. Waits for QZ payment to the station's wallet address
  3. Activates a relay (GPIO or simulated) for the purchased duration
  4. Logs the charging session on-chain as an immutable receipt

On-chain message format:
    SESSION:START|STATION:ev-001|DURATION:1800|AMOUNT:5.00|PORT:1
    SESSION:END|STATION:ev-001|KWH:7.5|COST:5.00|TS:1787055400

Usage:
  # Terminal demo (no hardware):
  python3 ev_charging_demo.py --node https://quartz.preview.saasclaw.ai \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --rate 0.10 --minutes 30 --terminal

  # Real relay on Raspberry Pi:
  python3 ev_charging_demo.py --node https://quartz.preview.saasclaw.ai \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --rate 0.10 --minutes 30 --relay-pin 26

License: MIT
"""

import argparse
import json
import signal
import sys
import time
import urllib.request
import urllib.error
import logging
import random

logger = logging.getLogger("quartz.ev")


class EVChargingStation:
    """EV charging station that accepts QZ payments."""

    def __init__(self, node_url: str, wallet_address: str,
                 station_id: str = "ev-001",
                 rate_per_kwh: float = 0.10,
                 relay_pin: int = None,
                 poll_interval: int = 5,
                 port_count: int = 2):
        self.node_url = node_url.rstrip('/')
        self.wallet_address = wallet_address
        self.station_id = station_id
        self.rate_per_kwh = rate_per_kwh
        self.relay_pin = relay_pin
        self.poll_interval = poll_interval
        self.port_count = port_count
        self.running = True
        self.ports = [{"active": False, "start_time": 0, "duration_s": 0,
                       "amount_qz": 0, "kwh": 0} for _ in range(port_count)]
        self.sessions_completed = 0
        self.total_revenue_qz = 0
        self.total_kwh = 0

        # GPIO setup
        if relay_pin is not None:
            try:
                import RPi.GPIO as GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(relay_pin, GPIO.OUT)
                GPIO.output(relay_pin, GPIO.LOW)
                self.gpio = GPIO
                logger.info(f"Charging relay on GPIO{relay_pin}")
            except ImportError:
                logger.warning("RPi.GPIO not available — terminal mode")
                self.gpio = None
        else:
            self.gpio = None

    def calc_price(self, minutes: int) -> float:
        """Calculate price in QZ for given duration."""
        # Assume ~15kW charging rate → kWh = minutes/60 * 15
        kwh = minutes / 60.0 * 15.0
        return round(kwh * self.rate_per_kwh, 2)

    def build_qr_string(self, amount: float, port: int) -> str:
        """Build payment QR string."""
        return (f"quartz:{self.wallet_address}?amount={amount:.8f}"
                f"&label=EV-{self.station_id}-Port{port}")

    def log_session_start(self, port: int, duration_s: int, amount_qz: float):
        """Log charging session start on-chain."""
        msg = (f"SESSION:START|STATION:{self.station_id}|PORT:{port}|"
               f"DURATION:{duration_s}|AMOUNT:{amount_qz:.2f}")
        self._send_message(msg)

    def log_session_end(self, port: int, kwh: float, cost: float):
        """Log charging session end on-chain."""
        msg = (f"SESSION:END|STATION:{self.station_id}|PORT:{port}|"
               f"KWH:{kwh:.1f}|COST:{cost:.2f}|TS:{int(time.time())}")
        self._send_message(msg)

    def _send_message(self, text: str):
        """Send an on-chain message."""
        if len(text) > 160:
            logger.warning(f"Message too long: {text}")
            return
        payload = json.dumps({
            "from": f"station:{self.station_id}",
            "to": "log:ev-charging",
            "text": text,
        }).encode('utf-8')
        url = f"{self.node_url}/api/v1/messages/send"
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result.get("status") == "queued":
                    logger.info(f"On-chain: {text} (txid={result.get('txid','?')})")
        except Exception as e:
            logger.error(f"Failed to log on-chain: {e}")

    def check_for_payment(self, amount_sats: int) -> dict:
        """Poll for incoming payment."""
        url = f"{self.node_url}/api/v1/address/{self.wallet_address}/txs"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                for tx in data.get("txs", []):
                    if tx.get("amount", 0) >= amount_sats and tx.get("confirmations", 0) >= 1:
                        return tx
        except Exception:
            pass
        return None

    def activate_charging(self, port: int, duration_s: int, amount_qz: float):
        """Activate charging on a port."""
        p = self.ports[port]
        p["active"] = True
        p["start_time"] = time.time()
        p["duration_s"] = duration_s
        p["amount_qz"] = amount_qz

        # Log session start on-chain
        self.log_session_start(port, duration_s, amount_qz)

        if self.gpio and self.relay_pin is not None:
            self.gpio.output(self.relay_pin, self.gpio.HIGH)

        # Simulate charging
        print(f"\n{'⚡' * 3} CHARGING ACTIVE {'⚡' * 3}")
        print(f"  Station: {self.station_id} | Port: {port}")
        print(f"  Duration: {duration_s // 60}min {duration_s % 60}s")
        print(f"  Paid: {amount_qz} QZ")
        print(f"  Rate: {self.rate_per_kwh} QZ/kWh")
        print()

        # For demo: simulate charging in real-time with kWh accumulation
        # Real station would read from the charger's meter
        sim_kwh_rate = 15.0 / 3600  # 15kW per second
        elapsed = 0
        while elapsed < duration_s and self.running:
            time.sleep(min(5, duration_s - elapsed))
            elapsed += 5
            p["kwh"] = sim_kwh_rate * elapsed
            remaining = duration_s - elapsed
            print(f"  ⚡ Charging... {elapsed}s elapsed, "
                  f"{p['kwh']:.1f} kWh delivered, "
                  f"{remaining}s remaining")

        # Deactivate
        p["kwh"] = sim_kwh_rate * duration_s
        if self.gpio and self.relay_pin is not None:
            self.gpio.output(self.relay_pin, self.gpio.LOW)

        p["active"] = False
        self.sessions_completed += 1
        self.total_revenue_qz += amount_qz
        self.total_kwh += p["kwh"]

        # Log session end on-chain
        self.log_session_end(port, p["kwh"], amount_qz)

        print(f"\n{'✅' * 3} CHARGING COMPLETE {'✅' * 3}")
        print(f"  Energy delivered: {p['kwh']:.1f} kWh")
        print(f"  Cost: {amount_qz} QZ")
        print(f"  Session logged on-chain (immutable receipt)")
        print(f"  View at: {self.node_url}/explorer/")
        print()

    def run(self):
        """Main station loop."""
        print()
        print("=" * 60)
        print(f"  ⚡ Quartz EV Charging Station")
        print(f"  Station ID: {self.station_id}")
        print(f"  Ports: {self.port_count}")
        print(f"  Rate: {self.rate_per_kwh} QZ/kWh")
        print(f"  Wallet: {self.wallet_address[:20]}...{self.wallet_address[-12:]}")
        print(f"  Node: {self.node_url}")
        print("=" * 60)

        while self.running:
            # Show available ports
            free_ports = [i for i, p in enumerate(self.ports) if not p["active"]]
            if not free_ports:
                print("\n⚠️ All ports in use. Waiting for a port to free up...")
                time.sleep(10)
                continue

            port = free_ports[0]

            # Offer charging duration options
            print(f"\n📋 Port {port} available.")
            print("  Select charging duration:")
            print("  1) 15 minutes — {:.2f} QZ".format(self.calc_price(15)))
            print("  2) 30 minutes — {:.2f} QZ".format(self.calc_price(30)))
            print("  3) 60 minutes — {:.2f} QZ".format(self.calc_price(60)))
            print("  4) Custom duration")
            print("  5) Quit")
            print()

            try:
                choice = input("  Choice: ").strip()
            except EOFError:
                break

            if choice == "5" or choice.lower() == "q":
                break
            elif choice == "1":
                minutes = 15
            elif choice == "2":
                minutes = 30
            elif choice == "3":
                minutes = 60
            elif choice == "4":
                try:
                    minutes = int(input("  Minutes: ").strip())
                except ValueError:
                    print("  Invalid input")
                    continue
            else:
                print("  Invalid choice")
                continue

            amount = self.calc_price(minutes)
            duration_s = minutes * 60

            # Display QR / payment info
            qr_str = self.build_qr_string(amount, port)
            print()
            print("─" * 60)
            print(f"  💰 Pay {amount} QZ to start charging")
            print(f"  📝 Port {port} · {minutes} minutes · ~{minutes/60*15:.1f} kWh")
            print()
            print(f"  Payment URI:")
            print(f"  {qr_str}")
            print()
            print(f"  Or send manually:")
            print(f"    To: {self.wallet_address}")
            print(f"    Amount: {amount} QZ")
            print()
            print("  Waiting for payment... (Ctrl-C to cancel)")
            print("─" * 60)

            # Poll for payment
            amount_sats = int(amount * 1e8)
            paid = False
            timeout_start = time.time()

            while self.running and (time.time() - timeout_start) < 300:
                tx = self.check_for_payment(amount_sats)
                if tx:
                    print(f"\n✅ Payment received: {tx.get('amount', 0) / 1e8} QZ")
                    print(f"   TXID: {tx.get('txid', '?')}")
                    paid = True
                    break
                time.sleep(self.poll_interval)

            if not paid:
                print("\n⏰ Payment timeout. Session cancelled.")
                continue

            # Run charging session
            self.activate_charging(port, duration_s, amount)

            # Station stats
            print(f"  📊 Station Stats: {self.sessions_completed} sessions | "
                  f"{self.total_kwh:.1f} kWh delivered | "
                  f"{self.total_revenue_qz} QZ revenue")

    def stop(self):
        self.running = False
        if self.gpio:
            self.gpio.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Quartz EV Charging Station demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Terminal demo:
  python3 ev_charging_demo.py \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --rate 0.10 --terminal

  # Real relay on Raspberry Pi:
  python3 ev_charging_demo.py \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --rate 0.10 --relay-pin 26
        """,
    )
    parser.add_argument('--node', default='https://quartz.preview.saasclaw.ai')
    parser.add_argument('--address', required=True)
    parser.add_argument('--station', default='ev-001')
    parser.add_argument('--rate', type=float, default=0.10,
                        help='Price per kWh in QZ (default: 0.10)')
    parser.add_argument('--ports', type=int, default=2)
    parser.add_argument('--relay-pin', type=int, default=None)
    parser.add_argument('--terminal', action='store_true')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

    station = EVChargingStation(
        node_url=args.node,
        wallet_address=args.address,
        station_id=args.station,
        rate_per_kwh=args.rate,
        relay_pin=args.relay_pin,
        port_count=args.ports)

    def handler(sig, frame):
        print("\n\nShutting down station...")
        station.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    station.run()


if __name__ == '__main__':
    main()