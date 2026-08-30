# Quartz Pay-to-Trigger Relay (v079)

Turn any miner into pay-per-use hardware: a payment to the board's own wallet
fires a GPIO relay. Vending machine, door latch, USB port, irrigation valve.

## Console commands (serial, any state)

    relay 5          watch for a 5 QZ payment, fire on confirm (~30s)
    relay 5 30       same, hold relay 30 s (persisted for next time)
    relay test       fire immediately — bench test, no payment
    relay            show pin / pulse settings + usage
    relay pin <gpio> move the relay pin (NVS, reboots)
    relay off        cancel the active watch

The board prints a `quartz:<address>?amount=5` payment URI to the console —
pay it from the Android or web wallet. On-chain confirmation triggers the
relay (1 confirmation, 300 s request timeout).

## Wiring

Any opto-isolated relay module (common blue 5 V boards):
VCC→5V · GND→GND · IN→relay GPIO.

Default pins: **GPIO26** (classic ESP32 / M5Stack / LilyGO), **GPIO5**
(C3 Super Mini — GPIO26 does not exist on the C3). Active-high.

⚠️ Switch low-voltage loads only. Mains wiring is your responsibility.

## Internals

- `quartz_pay.c` (dormant since the Aug 10 M5Stack era, wired live in v079)
- Payment watch polls `/api/v1/address/<addr>/txs` every ~5 s while active
- Config persisted in NVS namespace `qz_relay` (keys: pin u8, dur u32)
- `relay test` blocks the caller ~3 s (the pulse itself) — normal
- Pi alternative without firmware: `demos/pay_demo.py --relay-pin N`

Docs: quartzchain.net/setup.html#relay · Demo: quartzchain.net/paywall.html
