// Quartz Wallet — Real Ed25519 cryptography via tweetnacl + BIP39
// Self-custody: keys generated client-side, never sent to any server

// Dependencies bundled locally — no CDN needed (works offline, no CSP issues)

let nacl = null;
let wordlist = null;

async function loadCrypto() {
    if (!nacl) {
        nacl = window.nacl;
        if (!nacl) throw new Error('tweetnacl failed to load');
    }
    if (!wordlist) {
        const resp = await fetch('bip39-english.json');
        if (!resp.ok) throw new Error('Failed to load BIP-39 wordlist');
        wordlist = await resp.json();
    }
}

// --- SHA-256 via SubtleCrypto (async) ---

async function sha256(data) {
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    return new Uint8Array(hashBuffer);
}

// --- BIP39 ---

async function generateMnemonic() {
    await loadCrypto();
    const entropy = nacl.randomBytes(16);
    return await entropyToMnemonic(entropy);
}

async function entropyToMnemonic(entropy) {
    const entropyBits = bytesToBinary(entropy);
    const hash = await sha256(entropy);
    const checksumBits = bytesToBinary(hash).slice(0, 4);
    const allBits = entropyBits + checksumBits;
    
    const words = [];
    for (let i = 0; i < allBits.length; i += 11) {
        const index = parseInt(allBits.slice(i, i + 11), 2);
        words.push(wordlist[index]);
    }
    return words;
}

function bytesToBinary(bytes) {
    return bytes.reduce((acc, byte) => acc + byte.toString(2).padStart(8, '0'), '');
}

async function mnemonicToEntropy(words) {
    let bits = '';
    for (const word of words) {
        const index = wordlist.indexOf(word);
        if (index === -1) throw new Error(`Invalid word: ${word}`);
        bits += index.toString(2).padStart(11, '0');
    }
    const entropyBits = bits.slice(0, 128);
    const checksumBits = bits.slice(128);
    
    const entropy = new Uint8Array(16);
    for (let i = 0; i < 16; i++) {
        entropy[i] = parseInt(entropyBits.slice(i * 8, (i + 1) * 8), 2);
    }
    
    const entropyHash = await sha256(entropy);
    const expectedChecksum = bytesToBinary(entropyHash).slice(0, 4);
    if (checksumBits !== expectedChecksum) {
        throw new Error('Invalid mnemonic checksum');
    }
    return entropy;
}

// --- Seed derivation (PBKDF2) ---

async function mnemonicToSeed(words, passphrase = '') {
    const mnemonicStr = words.join(' ');
    const salt = new TextEncoder().encode('mnemonic' + passphrase);
    const keyMaterial = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(mnemonicStr),
        { name: 'PBKDF2' }, false, ['deriveBits']
    );
    const seed = await crypto.subtle.deriveBits(
        { name: 'PBKDF2', salt, iterations: 2048, hash: 'SHA-512' },
        keyMaterial, 512
    );
    return new Uint8Array(seed);
}

// --- SLIP-0010 Ed25519 key derivation ---

async function deriveQuartzKeypair(words) {
    const seed = await mnemonicToSeed(words);
    
    // Master key: HMAC-SHA512("ed25519 seed", seed)
    const masterKey = await hmacSHA512('ed25519 seed', seed);
    let key = masterKey.slice(0, 32);
    let chain = masterKey.slice(32, 64);
    
    // Derive path: m/44'/789'/0'/0'/0'
    const path = [44, 789, 0, 0, 0];
    for (const index of path) {
        const hardenedIndex = index | 0x80000000;
        const data = new Uint8Array(1 + 32 + 4);
        data[0] = 0;
        data.set(key, 1);
        data.set(uint32BE(hardenedIndex), 33);
        
        const result = await hmacSHA512Bytes(chain, data);
        key = result.slice(0, 32);
        chain = result.slice(32, 64);
    }
    
    // Ed25519 keypair from the 32-byte seed
    const keyPair = nacl.sign.keyPair.fromSeed(key);
    return { privateKey: key, publicKey: keyPair.publicKey };
}

async function hmacSHA512(key, data) {
    const cryptoKey = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(key),
        { name: 'HMAC', hash: 'SHA-512' }, false, ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', cryptoKey, data);
    return new Uint8Array(sig);
}

async function hmacSHA512Bytes(keyBytes, data) {
    const cryptoKey = await crypto.subtle.importKey(
        'raw', keyBytes,
        { name: 'HMAC', hash: 'SHA-512' }, false, ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', cryptoKey, data);
    return new Uint8Array(sig);
}

function uint32BE(n) {
    return new Uint8Array([(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff]);
}

// --- Address derivation ---

const BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function base58Encode(data) {
    let num = BigInt('0');
    for (const byte of data) {
        num = (num << 8n) | BigInt(byte);
    }
    let result = '';
    while (num > 0n) {
        const [q, r] = [num / 58n, num % 58n];
        result = BASE58[Number(r)] + result;
        num = q;
    }
    for (const byte of data) {
        if (byte === 0) result = '1' + result;
        else break;
    }
    return result;
}

function base58Decode(str) {
    let num = BigInt('0');
    for (const char of str) {
        const idx = BASE58.indexOf(char);
        if (idx === -1) throw new Error(`Invalid Base58 char: ${char}`);
        num = num * 58n + BigInt(idx);
    }
    const bytes = [];
    while (num > 0n) {
        bytes.unshift(Number(num & 0xffn));
        num = num >> 8n;
    }
    for (const char of str) {
        if (char === '1') bytes.unshift(0);
        else break;
    }
    return new Uint8Array(bytes);
}

async function publicKeyToAddress(publicKey, mainnet = true) {
    const prefix = mainnet ? 0x3B : 0x7F;
    
    // SHA-256(pubkey) → first 20 bytes
    const hash = await sha256(publicKey);
    const pubkeyHash = hash.slice(0, 20);
    
    // payload = prefix || hash
    const payload = new Uint8Array(21);
    payload[0] = prefix;
    payload.set(pubkeyHash, 1);
    
    // Checksum = SHA-256(SHA-256(payload))[:4]
    const hash1 = await sha256(payload);
    const hash2 = await sha256(hash1);
    const checksum = hash2.slice(0, 4);
    
    // address = payload || checksum
    const address = new Uint8Array(25);
    address.set(payload, 0);
    address.set(checksum, 21);
    
    return base58Encode(address);
}

async function validateAddress(address) {
    try {
        const decoded = base58Decode(address);
        if (decoded.length !== 25) return false;
        const payload = decoded.slice(0, 21);
        const checksum = decoded.slice(21);
        const hash1 = await sha256(payload);
        const hash2 = await sha256(hash1);
        const expected = hash2.slice(0, 4);
        return arraysEqual(checksum, expected);
    } catch (e) {
        return false;
    }
}

function arraysEqual(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
}

// --- Transaction signing ---

function signTransaction(privateKey, message) {
    const keyPair = nacl.sign.keyPair.fromSeed(privateKey);
    const signature = nacl.sign.detached(message, keyPair.secretKey);
    return signature;
}

function verifyTxSignature(publicKey, message, signature) {
    return nacl.sign.detached.verify(message, signature, publicKey);
}

// --- Full wallet creation ---

async function createQuartzWallet() {
    const words = await generateMnemonic();
    const { privateKey, publicKey } = await deriveQuartzKeypair(words);
    const address = await publicKeyToAddress(publicKey);
    return { words, privateKey, publicKey, address };
}

async function importQuartzWallet(words) {
    mnemonicToEntropy(words);
    const { privateKey, publicKey } = await deriveQuartzKeypair(words);
    const address = await publicKeyToAddress(publicKey);
    return { words, privateKey, publicKey, address };
}
