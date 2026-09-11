# Relay over BLE — Spec (v089.12 target)

Arm, configure, and test the pay-to-trigger relay from the Android app over
BLE. Parity: C3 + classic ESP32. S3 deferred (no v089 build).

Companion to [RELAY.md](RELAY.md) (v079 serial CLI feature).

## GATT additions — append-only

New characteristics are **appended after 0A0B** (end of `QUARTZ_IDX_*` enum,
end of `s_attr_db[]`). Never insert mid-table — handle shifts are how the
+1 handle bug happened. Existing handles stay identical; old apps ignore the
new chars, new apps probe for them.

### `00000A0C` — Relay status (READ)

JSON snapshot, built with snprintf (same style as `quartz_ble_update_stats`,
quartz_ble.c:718). Refresh on read + after every command write.

    {"v":1,"state":"idle|armed|fired","price_qz":1.50,"pulse_s":3,
     "fast":true,"invert":false,"auto":true,"pin":5,
     "addr":"R7yQ…","uri":"quartz:R7yQ…?amount=1.5"}

Source: `quartz_pay_get_state()`, `quartz_pay_get_request()`,
`quartz_pay_get_{duration_ms,fast,invert,auto,pin}()`,
`quartz_pay_build_qr_string()`. All exist — no firmware API gaps except a
`quartz_pay_set_invert(bool)` convenience (today only toggle; add a setter
so BLE can set absolute state).

`uri` present only while armed — lets the app render the payment QR with no
local formatting logic.

### `00000A0D` — Relay command (WRITE, ENCRYPTED)

`ESP_GATT_PERM_WRITE_ENCRYPTED` — bonded sessions only, same gate as the
WiFi SSID/pass chars (0A09/0A0A). One command per write, plain text,
**identical syntax to the serial CLI**:

    arm 1.5 [pulse_s]   e.g. "arm 1.5 3"
    cancel
    test [sec]
    fast 1|0
    invert 1|0
    auto 1|0
    pin <gpio>           (reboots, same as CLI)

Implementation: extract the arg parsing from the `relay` CLI handler in
`main.c` into `quartz_pay_relay_cmd(const char *line, char *reply, size_t n)`
returning a one-line human reply ("armed 1.50 QZ · pulse 3s"). Serial CLI and
BLE write handler both call it — one parser, two transports, no JSON parser
needed in firmware. App re-reads 0A0C on write ack for structured state.

## App (QuartzWallet)

- `QuartzBLEManager`: add `RELAY_STATE_UUID` (0A0C), `RELAY_CMD_UUID`
  (0A0D) — read/write via the existing `writeCharacteristic` path used for
  PIN/WiFi.
- Board screen: new **Relay card** — state chip (Idle/Armed/Fired), price +
  pulse inputs, fast/safe · invert · auto toggles, **Test** button,
  **Arm/Cancel**. While armed: payment QR from `uri` (tap to open wallet).
- Probe on connect: 0A0C missing → card hidden (classic v089.4 and older C3
  builds). No error surface for old firmware.

## Security decisions

- Relay writes: bonded/encrypted only. Reads: plain (pricing config is not
  secret; stats/address already plain).
- PIN lock continues to gate seed/signing only — relay is deliberately not
  PIN-gated (a kid-facing vending board must not lock out buyers' operator).

## Rollout

1. C3 + classic same commit (code is shared `quartz_pay.c` + mirrored
   `quartz_ble.c` handler blocks).
2. C3: ship in next release; publish touchpoints = download.html cards +
   unversioned aliases + **flasher manifests** (all three — flash.html was
   the one that slipped on v089.11).
3. Classic: rides the pending v089.11 verification wave — lands as v089.12
   after the two boards (classic + LilyGo T3) confirm.
4. S3: only after an S3 v089 port exists.

## Tests

- Bench E2E C3: pair → arm 0.1 → pay from web wallet → relay LED fires →
  0A0C shows fired.
- Parser: `quartz_pay_relay_cmd` unit cases (arm/cancel/test/flags/bad args).
- Classic: same E2E on Norman's classic + T3.
