# CrystalHash v2 — Hardware-Bound Proof of Work

## Design Goal

Make it **impossible** to compute a valid hash without physical access to an ESP32's eFuse HMAC engine. Not just a signature at the end — the hardware key is baked into every iteration of the hash function.

## Algorithm

```
Input: 80-byte block header H, nonce N (8 bytes), device_hmac_key K (32 bytes, eFuse)

1. INIT: state = SHA-256(H || N)          # Standard init

2. SCRATCHPAD INIT:
   scratchpad[0..255] = AES-256-CTR(state, key=K)
   # eFuse HMAC key used as AES key — GPU can't do this without ESP32

3. MIXING (64 rounds, HMAC every 8 rounds):
   FOR round = 0 TO 63:
     # Memory-hard mixing
     idx = state[0..3] % 256
     state = SHA-256(state XOR scratchpad[idx])
     scratchpad[idx] = AES-256-ECB(scratchpad[idx], key=K)

     # Every 8th round: inject hardware HMAC
     IF round % 8 == 7:
       state = state XOR HMAC-SHA256(K, state || round)
       # ^^^ This call goes through esp_hmac_calculate()
       # ^^^ eFuse key never enters RAM — GPU physically cannot compute this

4. FINALIZE:
   hash = SHA-256(state || SHA-256(H || N))

5. Difficulty check:
   valid IF hash < target (interpreted as 256-bit big-endian)
```

## Why GPU Can't Cheat

Each nonce attempt requires **8 hardware HMAC calls** (rounds 7, 15, 23, 31, 39, 47, 55, 63).

A GPU could compute steps 1-6 of each round batch instantly. But round 7 requires `HMAC-SHA256(K, ...)` where K lives only in eFuse. The GPU must:
1. Send state to ESP32
2. Wait for ESP32 to compute HMAC via hardware engine
3. Receive result
4. Continue rounds 8-14
5. Send again for round 15
6. Repeat ×8

**Round-trip cost dominates.** Even if the GPU computes the non-HMAC rounds in 0ms, each HMAC call over USB/SPI takes ~0.5-1ms (bus latency + HMAC computation). 8 calls = 4-8ms per nonce — the ESP32's speed, not the GPU's.

## Performance on ESP32

| Operation | Time |
|-----------|------|
| SHA-256 (hardware) | ~5μs |
| AES-256 (hardware) | ~8μs |
| HMAC-SHA256 (eFuse) | ~100μs |
| Full nonce attempt (64 rounds) | ~8ms |
| Hash rate | ~125 H/s |

At difficulty 20: ~8 hours average per block (solo).

## Multi-Device Attack Analysis

| Setup | Hash Rate | Advantage |
|-------|-----------|-----------|
| 1 honest ESP32 | 125 H/s | 1× |
| 1 GPU + 1 ESP32 oracle | ~125 H/s | **1×** (bottlenecked by ESP32 HMAC) |
| 1 GPU + 10 ESP32 oracles | ~1,250 H/s | 10× (parallel, but 10× hardware cost) |
| 1 honest ESP32 farm (10 boards) | 1,250 H/s | 10× |

The GPU advantage is **eliminated**. Attacker needs the same number of physical ESP32s as honest miners. The GPU just adds cost (power, complexity) without adding hash rate.
