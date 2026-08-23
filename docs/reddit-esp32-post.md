# r/ESP32 launch post — Quartz
# One post per quarter (sub rule). Post Monday 13:00-16:00 ET (17:00-20:00 UTC) for best window.
# Post from u/Lower_Map8829. Immediately reply with FIRST COMMENT below.

## TITLE
I built a blockchain a $5 ESP32 can actually mine — it's been running on my desk for weeks (testnet, full disclosure inside)

## BODY

I've been building a small cryptocurrency called **Quartz** with one design constraint that shaped everything: **a $5 ESP32 must be able to win blocks.** Not "theoretically SHA-256 works on microcontrollers" — actually earn, solo, no pool, no vendor, on hardware you already own. My test board has been mining for weeks through a node running on a Raspberry Pi on my LAN. Full disclosure up front: I built this, it's a testnet, the tokens have no monetary value, and I'm not selling anything. This post is about the ESP32 engineering and I'd genuinely like this sub to try to break it.

**What runs on the chip:**
- SHA-256 nonce grinding at a difficulty set so a stock ESP32 finds a block in minutes, not millennia — the chain deliberately targets microcontroller-class hardware instead of ASICs
- Ed25519 key generated on first boot, stored in NVS (never leaves the chip; that's also why the flash instructions say *never run erase_flash* — you'd burn your own wallet)
- Captive portal for config: plug in, it makes its own AP, phone pops the portal, you set WiFi + mining address + node address — no app, no cloud account
- JSON transactions over plain HTTP to whatever node you point it at. Mine through my seed node, or through your own Pi. On your LAN it never touches the internet at all
- WiFi recovery: survives router reboots, power cycles, hotel portals — it re-captives itself

**To try it:**
- Flash the merged image at 0x0: https://quartzchain.net/downloads/quartz-esp32-merged.bin (esptool write_flash 0x0 ...). Source and C3 build are on GitHub
- Or run your own node first: one tarball, 15 minutes on a Raspberry Pi — https://github.com/saasclawai-org/quartz
- Setup guide: https://quartzchain.net/setup.html — web wallet with QR codes: https://quartzchain.net/wallet/

**Honest state of the network:** it's my board and my nodes today. The node software is pure-Python stdlib on GitHub, so you can run the whole stack yourself and verify blocks aren't coming from me. Mainnet only happens after published bars (20–50 independent miners, founder has to clear the same hardware-cost bar as everyone else): https://github.com/saasclawai-org/quartz/blob/main/MAINNET.md

If you flash a board, post a photo of the portal with your address — I want to see boards I don't own on the network. Protocol questions, difficulty math, "why not just a database", "isn't this just a premine" — first comment has the FAQ, and I'll answer everything else in the thread.

## FIRST COMMENT (post immediately after)

**FAQ / disclosures:**

- **Why?** The engineering question that hooked me: what does a blockchain look like if the target miner is a $5 microcontroller instead of a GPU farm? Everything downstream — difficulty target, JSON txs instead of binary, HTTP instead of stratum, NVS key custody — falls out of that constraint.
- **Device identity:** boards enroll with a PUF-derived identity at first boot. If you can spoof enrollment for a board you don't physically have, **I want to hear about it** — seriously, that's the part I most want attacked.
- **"Why not just a database?"** Fair. The answer is the node story: you can run the verifier yourself on a Pi and don't have to trust my server. If that's not worth anything to you, the database wins — I accept that.
- **Is this a premine?** Testnet rewards reset before mainnet; mainnet bars (in MAINNET.md) include founder share < 40% and difficulty floors from launch. Judge the repo, not the promise.
- **Tokens have no value.** Nothing is for sale. No token sale ever — the mainnet doc says so.
- Bonus if you made it this far: the wallet key also logs you into websites — challenge/signature login demo (MetaMask-style): https://quartzchain.net/demos/login.html

I'm monitoring this thread — ask anything.
