#!/usr/bin/env python3
"""
Quartz Sensor Logger — logs sensor readings to the Quartz blockchain.

Posts temperature readings as on-chain messages using the
POST /api/v1/messages/send endpoint. Each message is a compact
pipe-delimited string that fits in the 160-char limit:

    TEMP:3.2C|DEV:cold-001|TS:1723976400|LOC:truck-A

Works with:
  - Real DS18B20 sensor on a Raspberry Pi GPIO
  - Simulated readings (for demo/testing without hardware)

Usage:
  # Real sensor (Raspberry Pi + DS18B20 on GPIO4):
  python3 sensor_logger.py --node https://quartzchain.net \
      --device cold-001 --sensor ds18b20 --pin 4 --interval 300

  # Simulated (no hardware needed):
  python3 sensor_logger.py --node https://quartzchain.net \
      --device cold-001 --simulated --interval 60

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

logger = logging.getLogger("quartz.sensor")


class SensorLogger:
    """Logs sensor readings to the Quartz blockchain as on-chain messages."""

    def __init__(self, node_url: str, device_id: str, location: str = "",
                 sensor_type: str = "temp", interval: int = 300):
        self.node_url = node_url.rstrip('/')
        self.device_id = device_id
        self.location = location
        self.sensor_type = sensor_type
        self.interval = interval
        self.running = True
        self.readings_sent = 0
        self.readings_failed = 0

    def read_sensor(self) -> dict:
        """Read the sensor. Override in subclasses for real hardware."""
        # Simulated: 2-6°C with small random walk (cold chain sim)
        if not hasattr(self, '_sim_temp'):
            self._sim_temp = 3.5
        self._sim_temp += random.uniform(-0.3, 0.3)
        self._sim_temp = max(1.5, min(6.0, self._sim_temp))
        return {
            "temp": round(self._sim_temp, 1),
            "humid": round(random.uniform(40, 60), 1),
        }

    def format_message(self, reading: dict) -> str:
        """Format sensor reading as a compact on-chain message."""
        ts = int(time.time())
        parts = [
            f"TEMP:{reading['temp']}C",
            f"DEV:{self.device_id}",
            f"TS:{ts}",
        ]
        if "humid" in reading:
            parts.append(f"HUM:{reading['humid']}%")
        if self.location:
            parts.append(f"LOC:{self.location}")
        return "|".join(parts)

    def send_reading(self, reading: dict) -> bool:
        """Send a sensor reading as an on-chain message."""
        text = self.format_message(reading)
        if len(text) > 160:
            logger.warning(f"Message too long ({len(text)} chars): {text}")
            return False

        payload = json.dumps({
            "from": f"sensor:{self.device_id}",
            "to": f"log:{self.sensor_type}",
            "text": text,
        }).encode('utf-8')

        url = f"{self.node_url}/api/v1/messages/send"
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result.get("status") == "queued":
                    txid = result.get("txid", "?")
                    logger.info(f"Reading logged on-chain: txid={txid} | {text}")
                    self.readings_sent += 1
                    return True
                else:
                    logger.error(f"Unexpected response: {result}")
                    self.readings_failed += 1
                    return False
        except urllib.error.URLError as e:
            logger.error(f"Failed to send reading: {e}")
            self.readings_failed += 1
            return False
        except Exception as e:
            logger.error(f"Error sending reading: {e}")
            self.readings_failed += 1
            return False

    def run(self):
        """Main loop — read sensor and log on-chain at each interval."""
        logger.info(f"Sensor logger started: device={self.device_id} "
                    f"sensor={self.sensor_type} interval={self.interval}s")
        logger.info(f"Node: {self.node_url}")

        while self.running:
            try:
                reading = self.read_sensor()
                self.send_reading(reading)
            except Exception as e:
                logger.error(f"Error in logging loop: {e}")

            # Wait for next interval
            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info(f"Logger stopped. Sent: {self.readings_sent}, "
                    f"Failed: {self.readings_failed}")

    def stop(self):
        """Stop the logger."""
        self.running = False


class DS18B20Logger(SensorLogger):
    """Real DS18B20 temperature sensor on Raspberry Pi GPIO.

    The DS18B20 uses the 1-Wire protocol. On Raspberry Pi:
    - Connect DS18B20 data pin to GPIO4 (pin 7)
    - Add 4.7kΩ pull-up resistor between data and 3.3V
    - Enable 1-Wire: sudo raspi-config → Interfacing → 1-Wire → Enable

    The sensor appears as /sys/bus/w1/devices/28-*/w1_slave
    """

    def __init__(self, node_url: str, device_id: str, w1_path: str = None,
                 location: str = "", interval: int = 300):
        super().__init__(node_url, device_id, location, "temp", interval)
        import glob
        if w1_path:
            self.w1_path = w1_path
        else:
            # Auto-detect DS18B20 on 1-Wire bus
            devices = glob.glob("/sys/bus/w1/devices/28-*/w1_slave")
            if not devices:
                raise FileNotFoundError(
                    "No DS18B20 found. Enable 1-Wire and check wiring. "
                    "Or use --simulated for testing without hardware.")
            self.w1_path = devices[0]
        logger.info(f"DS18B20 sensor: {self.w1_path}")

    def read_sensor(self) -> dict:
        """Read temperature from DS18B20 via 1-Wire sysfs interface."""
        with open(self.w1_path, 'r') as f:
            data = f.read()

        # Parse: "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        #        "72 01 4b 46 7f ff 0e 10 57 t=23125\n"
        if "YES" not in data:
            raise IOError("CRC check failed — bad sensor reading")

        # Extract temperature
        temp_line = [l for l in data.strip().split('\n') if 't=' in l]
        if not temp_line:
            raise IOError("No temperature in sensor output")

        temp_raw = temp_line[0].split('t=')[1]
        temp_c = int(temp_raw) / 1000.0

        return {"temp": round(temp_c, 1)}


def main():
    parser = argparse.ArgumentParser(
        description="Log sensor readings to the Quartz blockchain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simulated cold-chain logging (no hardware):
  python3 sensor_logger.py --simulated --interval 60

  # Real DS18B20 on Raspberry Pi:
  python3 sensor_logger.py --sensor ds18b20 --interval 300

  # Soil moisture (simulated):
  python3 sensor_logger.py --simulated --device field-A-01 \\
      --sensor soil --location "north-field" --interval 600
        """,
    )
    parser.add_argument('--node', default='https://quartzchain.net',
                        help='Quartz node URL (default: testnet)')
    parser.add_argument('--device', default='sensor-001',
                        help='Device ID (default: sensor-001)')
    parser.add_argument('--location', default='',
                        help='Location label (e.g., "truck-A", "north-field")')
    parser.add_argument('--sensor', default='temp',
                        choices=['temp', 'soil', 'humid'],
                        help='Sensor type (default: temp)')
    parser.add_argument('--interval', type=int, default=300,
                        help='Logging interval in seconds (default: 300)')
    parser.add_argument('--simulated', action='store_true',
                        help='Use simulated readings (no hardware needed)')
    parser.add_argument('--log-level', default='info',
                        choices=['debug', 'info', 'warning', 'error'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    # Build logger
    if args.simulated:
        logger_instance = SensorLogger(
            node_url=args.node,
            device_id=args.device,
            location=args.location,
            sensor_type=args.sensor,
            interval=args.interval,
        )
    elif args.sensor == 'temp':
        try:
            logger_instance = DS18B20Logger(
                node_url=args.node,
                device_id=args.device,
                location=args.location,
                interval=args.interval,
            )
        except FileNotFoundError as e:
            logger.error(str(e))
            logger.info("Use --simulated for testing without hardware")
            sys.exit(1)
    else:
        logger.error(f"Sensor type '{args.sensor}' requires hardware support "
                     f"not yet implemented. Use --simulated.")
        sys.exit(1)

    # Handle Ctrl-C
    def handler(signum, frame):
        logger.info("Stopping...")
        logger_instance.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    logger_instance.run()


if __name__ == '__main__':
    main()