# Quartz Wallet — Android

Native Android wallet for Quartz (QZ) cryptocurrency.

## Tech Stack
- **Kotlin + Jetpack Compose** — Modern declarative UI
- **Material 3** — Dark theme matching quartz.saasclaw.ai
- **EncryptedSharedPreferences** — Keys stored in Android Keystore
- **ZXing** — QR code scanning for address input
- **OkHttp** — Node communication
- **Biometric** — Fingerprint/face unlock for transactions

## Build

```bash
cd android/
./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`

## Features
- [x] Wallet creation (12-word seed)
- [x] Balance display
- [x] Send/receive with QR codes
- [x] Transaction history
- [x] Encrypted key storage
- [ ] BLE miner pairing
- [ ] Biometric auth
- [ ] Mainnet support
