# Wallet Recovery Design — Quartz

## Problem

WOTS+ one-time signatures mean **reusing a signature index reveals the private key**. If a device is lost, wiped, or reset, the user must recover not just their address (from seed) but also **which signature index to resume from**. Getting this wrong is catastrophic.

## Threat Scenarios Requiring Recovery

1. **Device lost/stolen** — user has seed phrase, needs new device
2. **NVS corruption** — ESP32 flash wore out or got wiped
3. **Factory reset** — user forgot PIN, wiped device
4. **Hardware upgrade** — moving from WROOM to M5Stack Core
5. **Device dies** — ESP32 bricked, RMA

## Design: Chain-Sync Recovery

The node already validates every transaction. Recovery = ask the node "how many signatures has this address used?"

### Recovery Flow (Firmware)

```
┌─────────────────────────────────────────────────────┐
│  NEW DEVICE (or wiped NVS)                          │
│                                                     │
│  1. User enters 12-word seed phrase                │
│     (via serial terminal or BLE from phone app)     │
│                                                     │
│  2. Device derives seed → 256 WOTS+ keypairs       │
│     → Merkle root = same address as before          │
│                                                     │
│  3. Device connects to node (WiFi or LoRa)         │
│     GET /api/v1/wallet/{address}/state              │
│                                                     │
│  4. Node returns:                                   │
│     {                                               │
│       "address": "QknaLzvRCWqy...",                 │
│       "balance": 1250.0,                            │
│       "last_sig_index": 17,    ← resume here        │
│       "last_sig_tx": "abc123", ← tx hash proof      │
│       "rotation_count": 0,     ← address rotations  │
│       "current_address_index": 0  ← derivation path │
│     }                                               │
│                                                     │
│  5. Device sets next_ots_index = last_sig_index + 1 │
│     Resumes mining and signing normally             │
│                                                     │
│  6. If address was rotated (index > 0):             │
│     Derive address #1, #2, ... until current        │
│     Each rotation burns 1 seed from derivation      │
│     chain and 256 WOTS+ keys                        │
└─────────────────────────────────────────────────────┘
```

### What the Node Must Track

For each address that has ever sent a transaction:

```json
{
  "address": "QknaLzvRCWqyfWWJvXSqS5p1s6YBYYXkAW",
  "derivation_index": 0,
  "wots_used": 17,
  "wots_max": 256,
  "rotated_to": null,
  "first_seen_height": 1234,
  "last_tx_height": 5678
}
```

When a key-rotation self-transfer happens (signature #256), the node:
1. Marks old address as `rotated_to: <new_address>`
2. Creates entry for new address with `derivation_index + 1`
3. New address starts at `wots_used: 1` (the rotation tx itself counts)

### API Endpoint

```
GET /api/v1/wallet/{address}/state
```

**Response (active address):**
```json
{
  "address": "QknaLzvRCWqyfWWJvXSqS5p1s6YBYYXkAW",
  "derivation_index": 0,
  "balance": 1250.0,
  "wots_used": 17,
  "wots_remaining": 238,
  "rotation_status": "ok",
  "last_sig_index": 17,
  "last_sig_tx_hash": "a3f2...",
  "mining_rewards": 1247.0,
  "sent_total": 3.0,
  "first_seen": 1234,
  "last_active": 5678
}
```

**Response (rotated address):**
```json
{
  "address": "QknaLzvRCWqyfWWJvXSqS5p1s6YBYYXkAW",
  "derivation_index": 0,
  "balance": 0,
  "rotated": true,
  "rotated_to": "QnewAddr...",
  "current_address": "QnewAddr...",
  "current_derivation_index": 1
}
```

Device follows `rotated_to` chain to find current active address, then syncs from there.

### Firmware Recovery Mode

New menu state: `QZ_SCREEN_RECOVERY`

```
┌────────────────────────────┐
│  🔮 Quartz Recovery        │
│                            │
│  Enter seed phrase:        │
│  1. aban___                │
│  2. aban___                │
│  ...                       │
│  12. aban___               │
│                            │
│  [Enter when done]         │
└────────────────────────────┘
```

Triggered by:
- Serial command: `recover`
- BLE command from app: `POST /sessions/{id}/recover`
- Button A hold for 10s during boot (factory reset → recovery)

### Recovery Sequence (Step by Step)

1. **Seed entry** — 12 words via serial or BLE (never WiFi)
2. **Seed validation** — checksum verify, derive master key
3. **Address derivation** — derive address #0, compute Merkle root
4. **Node query** — `GET /api/v1/wallet/{addr0}/state`
5. **Follow rotation chain** — if rotated, derive #1, query, repeat
6. **Set signature index** — `next_ots_index = last_sig_index + 1`
7. **Restore balance** — synced from chain state (device doesn't store balance)
8. **Resume mining** — blocks reward current active address
9. **Clear recovery flag** — write recovered state to NVS

### Edge Cases

**Node unreachable (offline recovery):**
- Device derives address but CANNOT determine signature index
- Shows WARNING: "Cannot verify signature index. Enter manually?"
- Advanced users can enter last known index (risky)
- Safe default: refuse to sign until node synced

**Empty address (never sent tx):**
- `wots_used = 0`, `next_ots_index = 0`
- Fresh start, same as new wallet

**Partially synced node:**
- Node returns `last_sig_index` based on its best chain
- If node is behind, index could be stale
- Device should query multiple nodes if available (LoRa mesh helps)

**Recovery during chain reorg:**
- `last_sig_index` could decrease if a tx gets orphaned
- Device should wait for confirmations (6 blocks) before trusting index
- If recent tx is unconfirmed, show warning

**Multiple seed entries (wrong seed):**
- Derived address won't match any on-chain address
- Node returns 404 → "Address not found, check seed phrase"
- No harm done, can retry

## PIN Protection Design

### Threat Model
- Physical theft of powered-on device
- BLE bond hijack (paired phone stolen)
- Unauthorized seed display

### Implementation

**PIN stored as SHA-256 hash in NVS** (key: `PIN_HASH`):
```
pin_hash = sha256(pin_string)
```

**NVS keys added:**
- `PIN_HASH` — 32 bytes, empty = no PIN set
- `PIN_SALT` — 16 bytes random (prevents rainbow tables)

**Boot sequence with PIN:**
```
1. Load wallet from NVS
2. If PIN_HASH set:
   a. Show "Enter PIN" screen
   b. M5Stack: 3 buttons navigate digits (A=up, B=next, C=confirm)
   c. Serial: type PIN + Enter
   d. BLE: app sends PIN via new characteristic
   e. 3 wrong attempts → 60s lockout (exponential backoff)
3. PIN correct → normal boot
4. Mining starts regardless of PIN (only seed access gated)
```

**What PIN gates:**
| Action | Requires PIN? |
|---|---|
| Mining | ❌ No |
| Viewing stats (BLE) | ❌ No |
| Viewing address (BLE) | ❌ No |
| Reading seed phrase | ✅ Yes |
| Displaying QR code | ✅ Yes |
| Signing transactions | ✅ Yes |
| Key rotation | ✅ Yes |
| Changing PIN | ✅ Yes (old PIN) |
| Recovery mode | ❌ No (wipes first) |

**Set PIN flow:**
1. First boot (no PIN): "Set PIN? (recommended)" → 4-6 digits
2. Can skip → device works but seed is ungated
3. Can set later via serial: `setpin 1234`
4. Can change: `changepin old new`

**Forgot PIN flow:**
1. 10 wrong attempts → device wipes NVS (excluding wallet seed? No — wipes everything)
2. Must recover from seed phrase via recovery mode
3. This is intentional — PIN is the gate, seed is the recovery

**Why wipe on 10 attempts:**
- Brute force 4-digit PIN = 10,000 combos → 10 tries = 0.1% chance
- Brute force 6-digit PIN = 1,000,000 combos → 10 tries = 0.001% chance
- Wipe forces physical reset, attacker can't keep trying
- Legitimate user with seed can always recover

## Implementation Priority

1. **Node endpoint** — `GET /api/v1/wallet/{address}/state` with `last_sig_index`
2. **Firmware recovery mode** — seed entry → node sync → resume
3. **PIN protection** — NVS hash, boot gate, BLE/serial entry
4. **Android recovery UI** — seed phrase entry screen, node sync status
5. **Wipe on failed PIN** — factory reset after 10 attempts

## File Impact

### Node (`reference-node/`)
- `quartz/storage.py` — track `wots_used` per address
- `quartz/node.py` — new `/wallet/{address}/state` endpoint
- `quartz/blockchain.py` — record signature index in tx validation

### Firmware (`firmware/main/`)
- `quartz_wots.c` — `quartz_qwallet_recover(seed, last_sig_index)` function
- `quartz_wallet.c` — PIN hash storage, PIN gate functions
- `quartz_wallet.h` — `quartz_wallet_set_pin()`, `quartz_wallet_check_pin()`
- `main.c` — recovery mode screen, PIN entry screen
- `quartz_ble.c` — PIN characteristic, recovery characteristic
- `quartz.h` — `QZ_SCREEN_PIN_ENTRY`, `QZ_SCREEN_RECOVERY` states

### Android (`android/`)
- New: `RecoveryScreen.kt` — 12-word seed entry
- New: `PinEntryScreen.kt` — PIN pad for device unlock
- `QuartzBLEManager.kt` — `sendPin()`, `recoverFromSeed()` methods
