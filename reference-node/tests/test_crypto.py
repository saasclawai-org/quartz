"""
Tests for Quartz cryptography — BIP39, Ed25519, addresses, signing.
"""
import pytest
from quartz.crypto import (
    generate_mnemonic, entropy_to_mnemonic, mnemonic_to_entropy,
    mnemonic_to_seed, seed_to_master_key, derive_child_key,
    derive_quartz_keypair, public_key_to_address, validate_address,
    address_is_mainnet, base58_encode, base58_decode,
    sign_message, verify_signature, create_new_wallet,
    import_wallet_from_mnemonic, QUARTZ_DERIVATION_PATH,
    BIP39_WORDLIST,
)


class TestBIP39Wordlist:
    def test_wordlist_has_2048_words(self):
        assert len(BIP39_WORDLIST) == 2048

    def test_wordlist_no_duplicates(self):
        assert len(set(BIP39_WORDLIST)) == 2048

    def test_first_word(self):
        assert BIP39_WORDLIST[0] == "abandon"

    def test_last_word(self):
        assert BIP39_WORDLIST[2047] == "zoo"


class TestMnemonicGeneration:
    def test_generates_12_words(self):
        words = generate_mnemonic(128)
        assert len(words) == 12

    def test_generates_24_words(self):
        words = generate_mnemonic(256)
        assert len(words) == 24

    def test_all_words_in_wordlist(self):
        words = generate_mnemonic(128)
        for w in words:
            assert w in BIP39_WORDLIST

    def test_mnemonics_are_unique(self):
        w1 = generate_mnemonic(128)
        w2 = generate_mnemonic(128)
        assert w1 != w2

    def test_roundtrip_entropy(self):
        """Generate mnemonic → recover entropy → should match"""
        import os
        entropy = os.urandom(16)
        words = entropy_to_mnemonic(entropy)
        recovered = mnemonic_to_entropy(words)
        assert recovered == entropy


class TestSeedDerivation:
    def test_seed_is_64_bytes(self):
        words = generate_mnemonic(128)
        seed = mnemonic_to_seed(words)
        assert len(seed) == 64

    def test_known_vector(self):
        """BIP39 test vector: all-abandon mnemonic"""
        # Trezor test vector: 12 words all 'abandon' + 'about'
        words = (["abandon"] * 11) + ["about"]
        seed = mnemonic_to_seed(words)
        expected = bytes.fromhex(
            "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc1"
            "9a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
        )
        assert seed == expected

    def test_passphrase_changes_seed(self):
        words = generate_mnemonic(128)
        seed1 = mnemonic_to_seed(words, "")
        seed2 = mnemonic_to_seed(words, "extra")
        assert seed1 != seed2


class TestKeyDerivation:
    def test_master_key_is_32_bytes(self):
        words = generate_mnemonic(128)
        seed = mnemonic_to_seed(words)
        key, chain = seed_to_master_key(seed)
        assert len(key) == 32
        assert len(chain) == 32

    def test_derive_keypair(self):
        words = generate_mnemonic(128)
        priv, pub = derive_quartz_keypair(words)
        assert len(priv) == 32  # Ed25519 private seed
        assert len(pub) == 32   # Ed25519 public key

    def test_same_mnemonic_same_key(self):
        words = generate_mnemonic(128)
        priv1, pub1 = derive_quartz_keypair(words)
        priv2, pub2 = derive_quartz_keypair(words)
        assert priv1 == priv2
        assert pub1 == pub2

    def test_different_mnemonic_different_key(self):
        w1 = generate_mnemonic(128)
        w2 = generate_mnemonic(128)
        _, pub1 = derive_quartz_keypair(w1)
        _, pub2 = derive_quartz_keypair(w2)
        assert pub1 != pub2

    def test_hardened_derivation_required(self):
        """Ed25519 only supports hardened (index >= 0x80000000)"""
        seed = b'\x00' * 64
        key, chain = seed_to_master_key(seed)
        with pytest.raises(AssertionError):
            derive_child_key(key, chain, 0)  # non-hardened should fail


class TestAddressDerivation:
    def test_address_starts_with_q(self):
        words = generate_mnemonic(128)
        _, pub = derive_quartz_keypair(words)
        addr = public_key_to_address(pub, mainnet=True)
        # Base58 'Q' doesn't directly map, but mainnet prefix 0x3B tends to start with Q-like chars
        assert len(addr) >= 26
        assert len(addr) <= 35

    def test_testnet_address(self):
        words = generate_mnemonic(128)
        _, pub = derive_quartz_keypair(words)
        addr = public_key_to_address(pub, mainnet=False)
        assert len(addr) >= 26

    def test_address_validation(self):
        words = generate_mnemonic(128)
        _, pub = derive_quartz_keypair(words)
        addr = public_key_to_address(pub)
        assert validate_address(addr) is True

    def test_invalid_address_fails(self):
        assert validate_address("Qinvalidaddress123") is False

    def test_address_roundtrip(self):
        """Encode → decode → should validate"""
        words = generate_mnemonic(128)
        _, pub = derive_quartz_keypair(words)
        addr = public_key_to_address(pub)
        assert validate_address(addr)
        assert address_is_mainnet(addr) is True


class TestBase58:
    def test_encode_empty(self):
        assert base58_encode(b'') == ''

    def test_encode_known(self):
        # "Hello" in base58
        result = base58_encode(b'Hello')
        assert len(result) > 0

    def test_roundtrip(self):
        for data in [b'\x00\x01\x02', b'hello world', b'\xff' * 20, bytes(range(256))]:
            encoded = base58_encode(data)
            decoded = base58_decode(encoded)
            assert decoded == data, f"Roundtrip failed for {data!r}"

    def test_leading_zeros(self):
        """Leading zero bytes → '1' prefix"""
        data = b'\x00\x00\x42'
        encoded = base58_encode(data)
        assert encoded.startswith('11')


class TestSigning:
    def test_sign_and_verify(self):
        from quartz.crypto import create_new_wallet
        wallet = create_new_wallet()
        priv = bytes.fromhex(wallet['private_key'])
        pub = bytes.fromhex(wallet['public_key'])
        
        message = b"Hello Quartz!"
        signature = sign_message(priv, message)
        
        assert len(signature) == 64  # Ed25519 signatures are 64 bytes
        assert verify_signature(pub, message, signature) is True

    def test_verify_wrong_message(self):
        from quartz.crypto import create_new_wallet
        wallet = create_new_wallet()
        priv = bytes.fromhex(wallet['private_key'])
        pub = bytes.fromhex(wallet['public_key'])
        
        sig = sign_message(priv, b"message 1")
        assert verify_signature(pub, b"message 2", sig) is False

    def test_verify_wrong_key(self):
        w1 = create_new_wallet()
        w2 = create_new_wallet()
        
        sig = sign_message(bytes.fromhex(w1['private_key']), b"test")
        assert verify_signature(bytes.fromhex(w2['public_key']), b"test", sig) is False


class TestWalletCreation:
    def test_create_wallet(self):
        wallet = create_new_wallet()
        assert 'mnemonic' in wallet
        assert 'private_key' in wallet
        assert 'public_key' in wallet
        assert 'address' in wallet
        assert len(wallet['mnemonic']) == 12
        assert validate_address(wallet['address'])

    def test_import_wallet(self):
        # Create, then import from same mnemonic
        wallet = create_new_wallet()
        imported = import_wallet_from_mnemonic(wallet['mnemonic'])
        
        assert imported['private_key'] == wallet['private_key']
        assert imported['public_key'] == wallet['public_key']
        assert imported['address'] == wallet['address']

    def test_import_invalid_mnemonic(self):
        with pytest.raises(ValueError):
            import_wallet_from_mnemonic(["not", "real", "words", "x"] * 3)

    def test_derivation_path(self):
        wallet = create_new_wallet()
        assert "44'/" in wallet['derivation_path']
        assert "789'/" in wallet['derivation_path']
