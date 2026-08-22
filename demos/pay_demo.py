#!/usr/bin/env python3
"""
Quartz Pay Demo — demonstrates pay-per-use hardware using QZ.

This script demonstrates the same flow as the ESP32 firmware's
quartz_pay.c, but runs on a regular computer (or Raspberry Pi)
so you can test the payment → relay flow without flashing firmware.

Flow:
  1. Display a QR code in the terminal (or generate a PNG)
  2. User pays QZ to the displayed address
  3. Script polls the node for incoming transactions
  4. On confirmation, triggers a GPIO relay (or prints "RELAY ON")

Works with:
  - Real relay on Raspberry Pi GPIO
  - Terminal output only (for demo without hardware)

Usage:
  # Terminal demo (no hardware):
  python3 pay_demo.py --node https://quartz.preview.saasclaw.ai \\
      --amount 0.5 --label "USB Charge 30min" --terminal

  # Real relay on Raspberry Pi (GPIO26):
  python3 pay_demo.py --node https://quartz.preview.saasclaw.ai \\
      --amount 1.0 --label "Water Valve" --relay-pin 26 --duration 10000

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

logger = logging.getLogger("quartz.pay")


class QuartzPayDemo:
    """Pay-per-use hardware demo using QZ payments."""

    def __init__(self, node_url: str, wallet_address: str,
                 amount_qz: float, label: str = "",
                 relay_pin: int = None, relay_duration_ms: int = 3000,
                 poll_interval: int = 5, timeout: int = 300):
        self.node_url = node_url.rstrip('/')
        self.wallet_address = wallet_address
        self.amount_qz = amount_qz
        self.amount_sats = int(amount_qz * 1e8)
        self.label = label
        self.relay_pin = relay_pin
        self.relay_duration_ms = relay_duration_ms
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.running = True
        self.state = "IDLE"
        self.relay_active = False

        # Setup GPIO if relay_pin is specified
        if relay_pin is not None:
            try:
                import RPi.GPIO as GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(relay_pin, GPIO.OUT)
                GPIO.output(relay_pin, GPIO.LOW)
                self.gpio = GPIO
                logger.info(f"Relay configured on GPIO{relay_pin}")
            except ImportError:
                logger.warning("RPi.GPIO not available — running in terminal-only mode")
                self.gpio = None
        else:
            self.gpio = None

    def build_qr_string(self) -> str:
        """Build a quartz: payment URI (same format as firmware)."""
        s = f"quartz:{self.wallet_address}?amount={self.amount_qz:.8f}"
        if self.label:
            s += f"&label={self.label}"
        return s

    def display_qr_terminal(self):
        """Display QR code in terminal using ASCII art."""
        qr_str = self.build_qr_string()
        print()
        print("=" * 60)
        print(f"  💰 Pay {self.amount_qz} QZ")
        if self.label:
            print(f"  📝 {self.label}")
        print()
        print(f"  Address: {self.wallet_address[:20]}...{self.wallet_address[-12:]}")
        print()
        print("  Scan this with the Quartz wallet app:")
        print()
        print(f"  {qr_str}")
        print()
        print("  Or send manually:")
        print(f"    To: {self.wallet_address}")
        print(f"    Amount: {self.amount_qz} QZ")
        print()
        print(f"  Waiting for payment... (timeout: {self.timeout}s)")
        print("=" * 60)
        print()

    def check_for_payment(self) -> dict:
        """Poll the node for incoming transactions to our address."""
        url = (f"{self.node_url}/api/v1/address/{self.wallet_address}/txs")

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data
        except urllib.error.URLError as e:
            logger.debug(f"Poll failed: {e}")
            return {"txs": []}
        except Exception as e:
            logger.debug(f"Poll error: {e}")
            return {"txs": []}

    def trigger_relay(self):
        """Trigger the relay (GPIO or terminal output)."""
        self.relay_active = True
        duration_s = self.relay_duration_ms / 1000

        if self.gpio and self.relay_pin is not None:
            logger.info(f"🔴 RELAY ON (GPIO{self.relay_pin}) for {duration_s}s")
            self.gpio.output(self.relay_pin, self.gpio.HIGH)
            time.sleep(duration_s)
            self.gpio.output(self.relay_pin, self.gpio.LOW)
            logger.info(f"⚫ RELAY OFF")
        else:
            print()
            print(f"{'=' * 40}")
            print(f"  🔴 RELAY ACTIVATED for {duration_s}s")
            if self.label:
                print(f"  📝 {self.label}")
            print(f"{'=' * 40}")
            print()
            time.sleep(duration_s)
            print(f"  ⚫ RELAY OFF")
            print()

        self.relay_active = False

    def run(self):
        """Main payment loop."""
        self.state = "WAITING"
        self.display_qr_terminal()

        start_time = time.time()

        while self.running:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                print(f"\n⏰ Payment request expired after {self.timeout}s")
                self.state = "EXPIRED"
                return

            # Poll for payment
            result = self.check_for_payment()
            txs = result.get("txs", [])

            for tx in txs:
                amount = tx.get("amount", 0)
                confirmations = tx.get("confirmations", 0)
                txid = tx.get("txid", "?")

                if amount >= self.amount_sats and confirmations >= 1:
                    print(f"\n✅ Payment received: {amount / 1e8} QZ")
                    print(f"   TXID: {txid}")
                    print(f"   Confirmations: {confirmations}")
                    self.state = "CONFIRMED"

                    # Trigger relay
                    self.trigger_relay()

                    print("\n✅ Transaction complete. Thank you!")
                    return
                elif amount >= self.amount_sats:
                    print(f"\n⏳ Payment detected ({amount / 1e8} QZ), "
                          f"waiting for confirmation ({confirmations}/1)...")

            time.sleep(self.poll_interval)

        self.state = "CANCELLED"

    def stop(self):
        """Stop the demo."""
        self.running = False
        if self.gpio:
            self.gpio.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Quartz pay-per-use hardware demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Terminal demo (no hardware, just shows the flow):
  python3 pay_demo.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --amount 0.5 --label "USB Charge" --terminal

  # Real relay on Raspberry Pi GPIO26:
  python3 pay_demo.py --address Qk6QSGe2TtBc3cDLtBHmZMg5CHAbGm4n4U \\
      --amount 1.0 --label "Water Valve" --relay-pin 26 --duration 10000
        """,
    )
    parser.add_argument('--node', default='https://quartz.preview.saasclaw.ai',
                        help='Quartz node URL')
    parser.add_argument('--address', required=True,
                        help='Wallet address to receive payments')
    parser.add_argument('--amount', type=float, default=0.5,
                        help='Amount in QZ to charge (default: 0.5)')
    parser.add_argument('--label', default='',
                        help='Label for the payment request')
    parser.add_argument('--relay-pin', type=int, default=None,
                        help='GPIO pin for relay (Raspberry Pi only)')
    parser.add_argument('--duration', type=int, default=3000,
                        help='Relay activation duration in ms (default: 3000)')
    parser.add_argument('--poll-interval', type=int, default=5,
                        help='Payment poll interval in seconds (default: 5)')
    parser.add_argument('--timeout', type=int, default=300,
                        help='Payment timeout in seconds (default: 300)')
    parser.add_argument('--terminal', action='store_true',
                        help='Terminal-only mode (no GPIO)')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    demo = QuartzPayDemo(
        node_url=args.node,
        wallet_address=args.address,
        amount_qz=args.amount,
        label=args.label,
        relay_pin=args.relay_pin,
        relay_duration_ms=args.duration,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )

    def handler(signum, frame):
        print("\nStopping...")
        demo.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    demo.run()


if __name__ == '__main__':
    main()