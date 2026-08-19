// Quartz Wallet — BLE Hardware Wallet Protocol
//
// When paired with an ESP32 miner, the wallet becomes WATCH-ONLY.
// It can view balance and construct transactions, but signing
// is delegated to the ESP32 over BLE. Private key NEVER on phone.
//
// Protocol:
// 1. Pair via Web Bluetooth (bonded, encrypted link)
// 2. Read PUBKEY characteristic → derive address
// 3. Watch balance via node API
// 4. To send: build tx → write to SIGN char → receive signature
// 5. Broadcast signed tx to network
//
// If no ESP32 paired, wallet falls back to software keys (PWA mode).
// Software keys are encrypted with user PIN via AES-GCM (Web Crypto).

const QUARTZ_BLE_SERVICE      = '0000qz01-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_PUBKEY       = '0000qz02-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_ADDRESS      = '0000qz03-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_SIGN         = '0000qz04-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_SIGN_RX      = '0000qz05-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_SETUP        = '0000qz06-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_WIPE         = '0000qz07-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_HASHRATE     = '0000qz08-0000-1000-8000-00805f9b34fb';
const QUARTZ_BLE_STATS        = '0000qz09-0000-1000-8000-00805f9b34fb';

class QuartzHardwareWallet {
    constructor() {
        this.device = null;
        this.server = null;
        this.characteristics = {};
        this.publicKey = null;
        this.address = null;
        this.connected = false;
        this.onSignature = null;
    }

    /**
     * Pair with ESP32 over BLE.
     * Requires Web Bluetooth (Chrome on Android, Chrome/Edge on desktop).
     */
    async pair() {
        if (!navigator.bluetooth) {
            throw new Error('Web Bluetooth not supported. Use Chrome on Android.');
        }

        this.device = await navigator.bluetooth.requestDevice({
            filters: [{ namePrefix: 'Quartz' }],
            optionalServices: [QUARTZ_BLE_SERVICE]
        });

        this.device.addEventListener('gattserverdisconnected', () => {
            this.connected = false;
            this.onDisconnect?.();
        });

        this.server = await this.device.gatt.connect();
        const service = await this.server.getPrimaryService(QUARTZ_BLE_SERVICE);

        // Cache all characteristics
        const charIds = [
            ['pubkey', QUARTZ_BLE_PUBKEY],
            ['address', QUARTZ_BLE_ADDRESS],
            ['sign', QUARTZ_BLE_SIGN],
            ['signRx', QUARTZ_BLE_SIGN_RX],
            ['setup', QUARTZ_BLE_SETUP],
            ['wipe', QUARTZ_BLE_WIPE],
            ['hashrate', QUARTZ_BLE_HASHRATE],
            ['stats', QUARTZ_BLE_STATS],
        ];

        for (const [name, uuid] of charIds) {
            try {
                this.characteristics[name] = await service.getCharacteristic(uuid);
            } catch (e) {
                console.warn(`Characteristic ${name} not found`);
            }
        }

        // Subscribe to signature notifications
        if (this.characteristics.signRx) {
            await this.characteristics.signRx.startNotifications();
            this.characteristics.signRx.addEventListener('characteristicvaluechanged', (event) => {
                const sig = new Uint8Array(event.target.value.buffer);
                this.onSignature?.(sig);
            });
        }

        // Read public key and address
        if (this.characteristics.pubkey) {
            const val = await this.characteristics.pubkey.readValue();
            this.publicKey = new Uint8Array(val.buffer);
        }

        if (this.characteristics.address) {
            const val = await this.characteristics.address.readValue();
            const decoder = new TextDecoder();
            this.address = decoder.decode(val.buffer);
        }

        this.connected = true;
        return {
            publicKey: this.publicKey,
            address: this.address,
        };
    }

    /**
     * Sign a transaction via ESP32.
     * Writes the tx hash to the SIGN characteristic.
     * ESP32 signs on-device and returns signature via notify.
     * Private key NEVER touches the phone.
     *
     * @param txHash 32-byte transaction hash
     * @returns Promise<Uint8Array> 64-byte Ed25519 signature
     */
    async signTransaction(txHash) {
        if (!this.connected || !this.characteristics.sign) {
            throw new Error('ESP32 not connected');
        }

        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Signing timeout — tap button on ESP32?'));
            }, 30000);

            this.onSignature = (sig) => {
                clearTimeout(timeout);
                this.onSignature = null;
                resolve(sig);
            };

            // Write tx hash to ESP32 — it signs and notifies result
            this.characteristics.sign.writeValue(txHash).catch((err) => {
                clearTimeout(timeout);
                this.onSignature = null;
                reject(err);
            });
        });
    }

    /**
     * Read live mining stats from ESP32.
     */
    async readStats() {
        if (!this.characteristics.stats) return null;
        const val = await this.characteristics.stats.readValue();
        const data = new DataView(val.buffer);
        return {
            hashrate: data.getUint32(0, true),
            blocksFound: data.getUint32(4, true),
            uptimeSec: data.getUint32(8, true),
            tempC: data.getInt8(12),
            powerMw: data.getUint16(13, true),
        };
    }

    /**
     * Initial setup — trigger key generation on ESP32.
     * Returns the one-time seed phrase for backup.
     */
    async setup() {
        if (!this.characteristics.setup) {
            throw new Error('Setup characteristic not available');
        }

        // Writing to SETUP triggers key generation on ESP32
        // ESP32 returns the BIP39 mnemonic via the signRx notify channel
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Setup timeout')), 10000);

            this.onSignature = (data) => {
                clearTimeout(timeout);
                this.onSignature = null;
                // Data is actually the mnemonic encoded as UTF-8 bytes
                const mnemonic = new TextDecoder().decode(data);
                resolve(mnemonic.split(' '));
            };

            this.characteristics.setup.writeValue(new Uint8Array([0x01])).catch((err) => {
                clearTimeout(timeout);
                this.onSignature = null;
                reject(err);
            });
        });
    }

    /**
     * Factory reset — wipe ESP32 keys.
     * Requires physical button hold on ESP32 (3 seconds).
     */
    async wipe() {
        if (!this.characteristics.wipe) return;
        // ESP32 will only wipe if button is held — writing alone does nothing
        // Button hold + write = confirmed wipe
        await this.characteristics.wipe.writeValue(new Uint8Array([0xDE, 0xAD, 0xBE, 0xEF]));
        this.disconnect();
    }

    disconnect() {
        if (this.device && this.device.gatt.connected) {
            this.device.gatt.disconnect();
        }
        this.connected = false;
        this.publicKey = null;
        this.address = null;
    }
}
