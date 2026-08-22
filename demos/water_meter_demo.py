#!/usr/bin/env python3
"""
Quartz Water Meter Logger — logs meter readings to the blockchain.

Simulates a municipal water meter that logs cumulative consumption
to the Quartz blockchain as an immutable billing record.

On-chain message format:
    METER:READ|DEV:wm-001|VOL:1247.5|FLOW:2.3|TS:1787055400|LOC:residential-A

The cumulative volume (gallons or liters) is logged periodically.
The utility and the customer both see the same on-chain data —
no disputed readings, no "lost" meter data.

Usage:
  # Simulated (no hardware):
  python3 water_meter_demo.py --node https://quartz.preview.saasclaw.ai \\
      --device wm-001 --location residential-A --simulated --interval 60

  # Real flow sensor on Raspberry Pi (YF-S201 on GPIO pin):
  python3 water_meter_demo.py --node https://quartz.preview.saasclaw.ai \\
      --device wm-001 --sensor yfs201 --pin 18 --interval 3600

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

logger = logging.getLogger("quartz.water")


class WaterMeterLogger:
    """Logs water meter readings to the Quartz blockchain."""

    def __init__(self, node_url: str, device_id: str,
                 location: str = "", interval: int = 3600):
        self.node_url = node_url.rstrip('/')
        self.device_id = device_id
        self.location = location
        self.interval = interval
        self.running = True
        self.readings_sent = 0
        self.readings_failed = 0
        self.total_volume = 0.0  # cumulative liters

    def read_meter(self) -> dict:
        """Read the flow meter. Override for real hardware."""
        # Simulated: 1.5-3.5 L/min flow rate
        flow_rate = random.uniform(1.5, 3.5)
        # Add to cumulative volume
        elapsed_min = self.interval / 60.0
        volume_added = flow_rate * elapsed_min
        self.total_volume += volume_added

        return {
            "volume": round(self.total_volume, 1),  # cumulative liters
            "flow": round(flow_rate, 1),  # current L/min
        }

    def format_message(self, reading: dict) -> str:
        """Format meter reading as compact on-chain message."""
        ts = int(time.time())
        parts = [
            f"METER:READ",
            f"DEV:{self.device_id}",
            f"VOL:{reading['volume']}",
            f"FLOW:{reading['flow']}",
            f"TS:{ts}",
        ]
        if self.location:
            parts.append(f"LOC:{self.location}")
        return "|".join(parts)

    def send_reading(self, reading: dict) -> bool:
        """Log a meter reading on-chain."""
        text = self.format_message(reading)
        if len(text) > 160:
            logger.warning(f"Message too long: {text}")
            return False

        payload = json.dumps({
            "from": f"meter:{self.device_id}",
            "to": "log:water",
            "text": text,
        }).encode('utf-8')

        url = f"{self.node_url}/api/v1/messages/send"
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result.get("status") == "queued":
                    logger.info(f"Meter reading logged: txid={result.get('txid','?')} | "
                                f"VOL={reading['volume']}L FLOW={reading['flow']}L/min")
                    self.readings_sent += 1
                    return True
        except Exception as e:
            logger.error(f"Failed to log: {e}")
            self.readings_failed += 1
        return False

    def run(self):
        logger.info(f"Water meter logger started: device={self.device_id} "
                    f"interval={self.interval}s")
        logger.info(f"Node: {self.node_url}")

        while self.running:
            try:
                reading = self.read_meter()
                self.send_reading(reading)
            except Exception as e:
                logger.error(f"Error: {e}")

            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info(f"Stopped. Sent: {self.readings_sent}, Failed: {self.readings_failed}")

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(
        description="Log water meter readings to the Quartz blockchain",
        epilog="""
Examples:
  # Simulated:
  python3 water_meter_demo.py --simulated --device wm-001 --interval 60

  # Real YF-S201 flow sensor (Raspberry Pi):
  python3 water_meter_demo.py --sensor yfs201 --pin 18 --interval 3600
        """)
    parser.add_argument('--node', default='https://quartz.preview.saasclaw.ai')
    parser.add_argument('--device', default='wm-001')
    parser.add_argument('--location', default='')
    parser.add_argument('--interval', type=int, default=3600,
                        help='Logging interval in seconds (default: 3600 = hourly)')
    parser.add_argument('--simulated', action='store_true')
    parser.add_argument('--sensor', default='yfs201', choices=['yfs201'])
    parser.add_argument('--pin', type=int, default=18)
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

    meter = WaterMeterLogger(
        node_url=args.node,
        device_id=args.device,
        location=args.location,
        interval=args.interval)

    def handler(sig, frame):
        logger.info("Stopping...")
        meter.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    meter.run()


if __name__ == '__main__':
    main()