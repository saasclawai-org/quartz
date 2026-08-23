#!/usr/bin/env python3
"""
Quartz Parking Demo — pay-per-park using QZ.

Two modes:
  1. Pay-per-park: ESP32 + relay controls a parking gate.
     Pay QZ → gate opens → park for purchased duration.
  2. Occupancy logger: ESP32 + sensor detects if a spot is occupied.
     Logs status on-chain for tamper-proof city parking data.

On-chain messages:
    PARK:START|STATION:pk-001|SPOT:1|DURATION:3600|AMOUNT:1.00|PLATE:ABC123
    PARK:END|STATION:pk-001|SPOT:1|TS:1787059000
    PARK:OCCUPANCY|STATION:pk-001|SPOT:1|STATUS:OCCUPIED|TS:1787055400
    PARK:OCCUPANCY|STATION:pk-001|SPOT:1|STATUS:FREE|TS:1787055700

Usage:
  # Pay-per-park (terminal demo):
  python3 parking_demo.py --node https://quartzchain.net \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --rate 0.50 --mode pay --terminal

  # Occupancy logger (simulated):
  python3 parking_demo.py --node https://quartzchain.net \\
      --mode occupancy --spots 4 --simulated --interval 30

License: MIT
"""

import argparse
import json
import signal
import sys
import time
import urllib.request
import logging
import random

logger = logging.getLogger("quartz.parking")


class ParkingStation:
    """Parking station with pay-per-park and occupancy logging."""

    def __init__(self, node_url: str, wallet_address: str = "",
                 station_id: str = "pk-001", rate_per_hour: float = 0.50,
                 spot_count: int = 1, relay_pin: int = None,
                 poll_interval: int = 5):
        self.node_url = node_url.rstrip('/')
        self.wallet_address = wallet_address
        self.station_id = station_id
        self.rate_per_hour = rate_per_hour
        self.spot_count = spot_count
        self.relay_pin = relay_pin
        self.poll_interval = poll_interval
        self.running = True
        self.spots = [{"occupied": False, "start_time": 0, "duration_s": 0,
                       "amount_qz": 0, "plate": ""} for _ in range(spot_count)]
        self.sessions_completed = 0
        self.total_revenue = 0

        if relay_pin is not None:
            try:
                import RPi.GPIO as GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(relay_pin, GPIO.OUT)
                GPIO.output(relay_pin, GPIO.LOW)
                self.gpio = GPIO
            except ImportError:
                self.gpio = None
        else:
            self.gpio = None

    def calc_price(self, hours: float) -> float:
        return round(hours * self.rate_per_hour, 2)

    def _send_message(self, text: str):
        if len(text) > 160:
            return
        payload = json.dumps({
            "from": f"station:{self.station_id}",
            "to": "log:parking",
            "text": text,
        }).encode('utf-8')
        url = f"{self.node_url}/api/v1/messages/send"
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result.get("status") == "queued":
                    logger.info(f"On-chain: {text}")
        except Exception as e:
            logger.error(f"Log failed: {e}")

    def check_for_payment(self, amount_sats: int) -> bool:
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

    def _open_gate(self):
        if self.gpio and self.relay_pin is not None:
            self.gpio.output(self.relay_pin, self.gpio.HIGH)
            time.sleep(2)
            self.gpio.output(self.relay_pin, self.gpio.LOW)
        else:
            print("  🚧 Gate opens...")

    def pay_per_park_mode(self):
        """Interactive pay-per-park loop."""
        print()
        print("=" * 60)
        print(f"  🅿️ Quartz Parking Station")
        print(f"  Station: {self.station_id} | Spots: {self.spot_count}")
        print(f"  Rate: {self.rate_per_hour} QZ/hour")
        print(f"  Wallet: {self.wallet_address[:20]}...{self.wallet_address[-12:]}")
        print("=" * 60)

        while self.running:
            free = [i for i, s in enumerate(self.spots) if not s["occupied"]]
            if not free:
                print("\n⚠️ All spots occupied. Waiting...")
                time.sleep(10)
                continue

            spot = free[0]
            print(f"\n📋 Spot {spot} available.")
            print(f"  1) 1 hour — {self.calc_price(1)} QZ")
            print(f"  2) 2 hours — {self.calc_price(2)} QZ")
            print(f"  3) 4 hours — {self.calc_price(4)} QZ")
            print(f"  4) All day (8h) — {self.calc_price(8)} QZ")
            print(f"  5) Quit")
            print()

            try:
                choice = input("  Choice: ").strip()
            except EOFError:
                break

            durations = {"1": 1, "2": 2, "3": 4, "4": 8}
            if choice == "5":
                break
            elif choice not in durations:
                print("  Invalid choice")
                continue

            hours = durations[choice]
            amount = self.calc_price(hours)
            duration_s = int(hours * 3600)

            # For demo: compress time (1 hour = 10 seconds)
            demo_duration = min(duration_s, 30) if hours <= 1 else min(duration_s, 60)

            qr_str = (f"quartz:{self.wallet_address}?amount={amount:.8f}"
                      f"&label=Parking-{self.station_id}-Spot{spot}")
            print()
            print("─" * 60)
            print(f"  💰 Pay {amount} QZ for {hours}h parking")
            print(f"  📍 Spot {spot}")
            print(f"  {qr_str}")
            print()
            print("  Waiting for payment... (Ctrl-C to cancel)")
            print("─" * 60)

            amount_sats = int(amount * 1e8)
            paid = False
            timeout_start = time.time()

            while self.running and (time.time() - timeout_start) < 300:
                tx = self.check_for_payment(amount_sats)
                if tx:
                    print(f"\n✅ Payment received: {tx.get('amount', 0)/1e8} QZ")
                    paid = True
                    break
                time.sleep(self.poll_interval)

            if not paid:
                print("\n⏰ Payment timeout.")
                continue

            # Open gate
            self._open_gate()

            # Log start
            plate = f"DEMO{random.randint(100,999)}"
            self._send_message(
                f"PARK:START|STATION:{self.station_id}|SPOT:{spot}|"
                f"DURATION:{duration_s}|AMOUNT:{amount:.2f}|PLATE:{plate}")

            # Mark occupied
            s = self.spots[spot]
            s["occupied"] = True
            s["start_time"] = time.time()
            s["duration_s"] = demo_duration
            s["amount_qz"] = amount
            s["plate"] = plate

            print(f"\n  🅿️ Spot {spot} occupied — plate {plate}")
            print(f"  ⏱️ Time remaining: {demo_duration}s (demo compressed)")

            # Wait for duration
            elapsed = 0
            while elapsed < demo_duration and self.running:
                time.sleep(min(5, demo_duration - elapsed))
                elapsed += 5
                remaining = demo_duration - elapsed
                if remaining > 0:
                    print(f"  ⏱️ {remaining}s remaining on spot {spot}")

            # Session end
            s["occupied"] = False
            self.sessions_completed += 1
            self.total_revenue += amount

            self._send_message(
                f"PARK:END|STATION:{self.station_id}|SPOT:{spot}|TS:{int(time.time())}")

            print(f"\n  ✅ Session ended. Spot {spot} freed.")
            print(f"  📊 Total: {self.sessions_completed} sessions, {self.total_revenue} QZ")

    def occupancy_mode(self, simulated: bool = True, interval: int = 30):
        """Log spot occupancy periodically."""
        print()
        print("=" * 60)
        print(f"  🅿️ Quartz Parking Occupancy Logger")
        print(f"  Station: {self.station_id} | Spots: {self.spot_count}")
        print(f"  Interval: {interval}s | Mode: {'simulated' if simulated else 'sensor'}")
        print("=" * 60)

        # Initialize random occupancy states
        for s in self.spots:
            s["occupied"] = random.choice([True, False])

        while self.running:
            for i, s in enumerate(self.spots):
                # Simulate occupancy changes
                if simulated:
                    if random.random() < 0.15:  # 15% chance of state change per interval
                        s["occupied"] = not s["occupied"]

                status = "OCCUPIED" if s["occupied"] else "FREE"
                self._send_message(
                    f"PARK:OCCUPANCY|STATION:{self.station_id}|SPOT:{i}|"
                    f"STATUS:{status}|TS:{int(time.time())}")

                indicator = "🔴" if s["occupied"] else "🟢"
                print(f"  {indicator} Spot {i}: {status}")

            occupied_count = sum(1 for s in self.spots if s["occupied"])
            print(f"  ── {occupied_count}/{self.spot_count} occupied ──\n")

            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

    def stop(self):
        self.running = False
        if self.gpio:
            self.gpio.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Quartz Parking demo — pay-per-park and occupancy logging",
        epilog="""
Examples:
  # Pay-per-park (terminal):
  python3 parking_demo.py --mode pay --terminal \\
      --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U --rate 0.50

  # Occupancy logger (simulated, 4 spots):
  python3 parking_demo.py --mode occupancy --spots 4 --simulated --interval 30
        """)
    parser.add_argument('--node', default='https://quartzchain.net')
    parser.add_argument('--address', default='')
    parser.add_argument('--station', default='pk-001')
    parser.add_argument('--rate', type=float, default=0.50,
                        help='Price per hour in QZ (default: 0.50)')
    parser.add_argument('--spots', type=int, default=1)
    parser.add_argument('--mode', choices=['pay', 'occupancy'], default='pay')
    parser.add_argument('--simulated', action='store_true')
    parser.add_argument('--relay-pin', type=int, default=None)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--terminal', action='store_true')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

    station = ParkingStation(
        node_url=args.node,
        wallet_address=args.address,
        station_id=args.station,
        rate_per_hour=args.rate,
        spot_count=args.spots,
        relay_pin=args.relay_pin)

    def handler(sig, frame):
        print("\n\nShutting down...")
        station.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    if args.mode == 'pay':
        if not args.address:
            print("Error: --address required for pay mode")
            sys.exit(1)
        station.pay_per_park_mode()
    else:
        station.occupancy_mode(simulated=args.simulated, interval=args.interval)


if __name__ == '__main__':
    main()