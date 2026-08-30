"""Quartz C3 one-shot flasher (v077) - no miniterm, no COM juggling.

Put this file NEXT to quartz-c3-merged.bin, then run:  python flash_c3.py
Follow the on-screen steps: unplug C3, hold BOOT, plug in, release.
"""
import os
import subprocess
import sys
import time

import serial.tools.list_ports as lp

BIN = "quartz-c3-merged.bin"

# Work from the folder this script lives in (where the bin should be)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if not os.path.exists(BIN):
    print("ERROR: %s not found next to flash_c3.py" % BIN)
    sys.exit(1)


def flash(port, before):
    cmd = [sys.executable, "-m", "esptool",
           "--chip", "esp32c3", "--port", port, "--baud", "115200",
           "--before", before, "--after", "hard-reset",
           "write-flash", "0x0", BIN]
    print("\n>>> esptool (%s) on %s\n" % (before, port))
    return subprocess.run(cmd).returncode


baseline = {p.device for p in lp.comports()}
print("Ports now: " + (", ".join(sorted(baseline)) or "none"))
print()
print("1) Unplug the C3 (if plugged in).")
print("2) HOLD its BOOT button, plug USB back in, keep holding 2s, release.")
print("3) Standby - I detect the port and flash automatically.")
print()
print("Waiting for the C3...")

waited = 0.0
while True:
    new = {p.device for p in lp.comports()} - baseline
    if new:
        port = sorted(new)[0]
        print("\nDetected %s - flashing v077 (~2-3 min). DO NOT unplug!" % port)
        rc = flash(port, "no-reset")
        if rc != 0:
            print("\nNo sync while parked - retrying with auto-reset...")
            rc = flash(port, "default-reset")
        print()
        if rc == 0:
            print("SUCCESS - v077 written and verified.")
            print("Replug the C3 once, then run:")
            print("  python -m serial.tools.miniterm %s 115200" % port)
        else:
            print("FAILED (exit %d) - paste the output above back to me." % rc)
        sys.exit(rc)
    time.sleep(0.5)
    waited += 0.5
    if waited % 15 == 0:
        print("  still waiting (%ds)... if the C3 is already plugged in: "
              "unplug it, then do the BOOT plug-in" % int(waited))
