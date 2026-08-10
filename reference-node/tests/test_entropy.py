"""
Tests for Quartz entropy health checks.

Validates the five-layer defense against the Coldcard RNG bug class:
1. NIST SP 800-90B health checks detect bad entropy
2. Chi-square catches non-uniform distributions
3. Repetition count catches stuck values
4. Adaptive proportion catches biased bits
5. Min-entropy estimate catches low-entropy sources
"""

import os
import math
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from quartz.entropy import (
    repetition_count_test, adaptive_proportion_test,
    chi_square_test, min_entropy_estimate,
    health_check, triple_mix, generate_secure_key,
    EntropyStatus, SAMPLE_SIZE, MIN_BITS_PER_BYTE,
    CHISQUARE_THRESHOLD,
)


class TestRepetitionCount:
    def test_good_entropy_passes(self):
        data = os.urandom(SAMPLE_SIZE)
        assert repetition_count_test(data) is True

    def test_stuck_value_fails(self):
        data = b'\x42' * SAMPLE_SIZE
        assert repetition_count_test(data) is False

    def test_51_consecutive_fails(self):
        """Exactly 51 same bytes = fail (threshold is 50)."""
        data = os.urandom(SAMPLE_SIZE)
        data = bytearray(data)
        for i in range(50, 101):
            data[i] = 0xFF  # 51 consecutive
        assert repetition_count_test(bytes(data)) is False

    def test_50_consecutive_passes(self):
        """Exactly 50 same bytes = pass (at the limit)."""
        data = bytearray(os.urandom(SAMPLE_SIZE))
        for i in range(200, 250):
            data[i] = 0xFF  # 50 consecutive
        assert repetition_count_test(bytes(data)) is True

    def test_short_streaks_pass(self):
        """Normal data has streaks of 2-4, that's fine."""
        data = os.urandom(SAMPLE_SIZE)
        assert repetition_count_test(data) is True


class TestAdaptiveProportion:
    def test_good_entropy_passes(self):
        data = os.urandom(SAMPLE_SIZE)
        assert adaptive_proportion_test(data) is True

    def test_all_zeros_fails(self):
        data = b'\x00' * SAMPLE_SIZE
        assert adaptive_proportion_test(data) is False

    def test_all_ones_fails(self):
        data = b'\xFF' * SAMPLE_SIZE
        assert adaptive_proportion_test(data) is False

    def test_heavily_biased_fails(self):
        """Every byte has bit 0 set → 100% ones."""
        data = bytes([b | 0x01 for b in os.urandom(SAMPLE_SIZE)])
        assert adaptive_proportion_test(data) is False


class TestChiSquare:
    def test_good_entropy_passes(self):
        data = os.urandom(SAMPLE_SIZE)
        ok, val = chi_square_test(data)
        assert ok is True

    def test_uniform_distribution_low_chi(self):
        """Perfectly uniform distribution has low chi-square."""
        data = bytes([i % 256 for i in range(SAMPLE_SIZE)])
        ok, val = chi_square_test(data)
        assert ok is True
        assert val < 300  # Should be very low for uniform

    def test_degenerate_distribution_high_chi(self):
        """All same byte = maximum chi-square."""
        data = b'\x00' * SAMPLE_SIZE
        ok, val = chi_square_test(data)
        assert ok is False
        # chi = 255 * (1024-4)^2/4 = huge
        assert val > CHISQUARE_THRESHOLD


class TestMinEntropy:
    def test_good_entropy(self):
        data = os.urandom(SAMPLE_SIZE)
        h = min_entropy_estimate(data)
        # For 1024 samples over 256 values, max_count is typically 6-12
        # H_min = -log2(10/1024) ≈ 6.68
        # With 6.0 threshold, genuine entropy passes reliably
        assert h > 5.5  # Should be well above threshold

    def test_stuck_value(self):
        data = b'\x42' * SAMPLE_SIZE
        h = min_entropy_estimate(data)
        assert h == 0.0  # log2(1) = 0

    def test_two_values(self):
        """Half 0x00, half 0xFF → p_max=0.5 → H=1.0"""
        data = bytes([0x00 if i < SAMPLE_SIZE // 2 else 0xFF
                      for i in range(SAMPLE_SIZE)])
        h = min_entropy_estimate(data)
        assert abs(h - 1.0) < 0.01


class TestHealthCheck:
    def test_good_entropy_passes(self):
        data = os.urandom(SAMPLE_SIZE)
        status, details = health_check(data)
        assert status == EntropyStatus.OK
        assert details['repetition_ok'] is True
        assert details['proportion_ok'] is True
        assert details['chi_square_ok'] is True
        assert details['min_entropy_ok'] is True

    def test_stuck_fails_with_details(self):
        data = b'\x00' * SAMPLE_SIZE
        status, details = health_check(data)
        assert status == EntropyStatus.REPETITION_FAIL
        assert details['repetition_ok'] is False

    def test_biased_fails_with_details(self):
        data = bytes([0x01] * SAMPLE_SIZE)
        status, details = health_check(data)
        # 0x01 has 1 one-bit per byte = 12.5% ones → proportion fail
        assert status in (EntropyStatus.PROPORTION_FAIL, EntropyStatus.REPETITION_FAIL)

    def test_returns_chi_square_value(self):
        data = os.urandom(SAMPLE_SIZE)
        _, details = health_check(data)
        assert 'chi_square' in details
        assert details['chi_square'] > 0

    def test_returns_min_entropy(self):
        data = os.urandom(SAMPLE_SIZE)
        _, details = health_check(data)
        assert 'min_entropy_bits' in details
        assert details['min_entropy_bits'] > 0


class TestTripleMix:
    def test_xor_combines_correctly(self):
        a = b'\x0F' * 16
        b_ = b'\xF0' * 16
        c = b'\xFF' * 16
        result = triple_mix(a, b_, c)
        # 0x0F ^ 0xF0 ^ 0xFF = 0x00
        assert result == b'\x00' * 16

    def test_one_random_source_is_enough(self):
        """If one source is truly random, the mix is truly random."""
        import random
        random.seed(42)
        rf = os.urandom(SAMPLE_SIZE)        # Truly random
        adc = bytes([42] * SAMPLE_SIZE)      # Stuck
        sram = bytes([i % 256 for i in range(SAMPLE_SIZE)])  # Deterministic
        mixed = triple_mix(rf, adc, sram)
        status, _ = health_check(mixed)
        assert status == EntropyStatus.OK

    def test_all_stuck_is_bad(self):
        rf = bytes([0x42] * SAMPLE_SIZE)
        adc = bytes([0x42] * SAMPLE_SIZE)
        sram = bytes([0x42] * SAMPLE_SIZE)
        mixed = triple_mix(rf, adc, sram)
        status, _ = health_check(mixed)
        assert status != EntropyStatus.OK


class TestSecureKeyGeneration:
    def test_generates_32_byte_key(self):
        key, _ = generate_secure_key()
        assert len(key) == 32

    def test_generates_unique_keys(self):
        key1, _ = generate_secure_key()
        key2, _ = generate_secure_key()
        assert key1 != key2

    def test_sample_hash_for_audit(self):
        key, sample_hash = generate_secure_key(sample_hash_out=True)
        assert len(sample_hash) == 32
        assert sample_hash != key  # Hash ≠ key

    def test_no_sample_hash_by_default(self):
        key, sample_hash = generate_secure_key()
        assert sample_hash is None

    def test_key_has_good_distribution(self):
        """Generated key bytes should have reasonable distribution."""
        key, _ = generate_secure_key()
        # Count unique bytes — 32 bytes should have ~25+ unique values
        unique = len(set(key))
        assert unique >= 20, f"Poor distribution: only {unique} unique bytes in 32"


class TestColdcardScenario:
    """Test against the Coldcard bug class.

    IMPORTANT: Statistical tests (chi-square, min-entropy, repetition)
    can detect HARDWARE FAULTS (stuck bits, biased ADC, broken radio)
    but CANNOT detect a well-designed software PRNG that happens to be
    seeded from a small space. The Coldcard Yasmarang PRNG produced
    well-distributed bytes — the weakness was that the entire output
    was predictable from ~40 bits of seed (chip serial + timer).

    No statistical test on output bytes can distinguish a cryptographically
    strong PRNG from a cryptographically weak one if both produce uniform
    distributions. The Coldcard bug was invisible for 5 years precisely
    BECAUSE the output passed statistical checks.

    Quartz's defense against this specific bug is NOT statistical testing —
    it's the five-layer architecture:
      1. Radio-first init (RF noise ≠ software PRNG)
      2. Triple-mix (attacker must control all 3 sources)
      3. No compile-time switches (prevents the #ifdef bug)
      4. Reproducible builds (anyone can audit the code path)
      5. Health checks (catch hardware faults, not crypto weakness)
    """

    def test_coldcard_lcg_passes_statistical_tests(self):
        """A weak PRNG can produce well-distributed bytes.

        This is WHY the Coldcard bug was invisible for 5 years.
        Statistical tests alone cannot detect all RNG weaknesses.
        The defense is the architecture (radio-first, triple-mix, no #ifdef),
        not just the health checks.
        """
        bad_samples = bytearray()
        state = 0x12345678 ^ 50000
        for _ in range(SAMPLE_SIZE):
            state = (state * 1103515245 + 12345) & 0xFFFFFFFF
            bad_samples.append(state & 0xFF)

        status, details = health_check(bytes(bad_samples))

        # The LCG output is well-distributed — it passes statistical tests.
        # This is a fundamental limitation of black-box testing.
        assert status == EntropyStatus.OK, \
            "LCG output should pass statistical tests — this is WHY the Coldcard bug was invisible"

        # But the min-entropy estimate should be available
        assert details['min_entropy_bits'] > 0

    def test_stuck_hardware_fails(self):
        """What health checks DO catch: hardware faults.

        If the ESP32's radio is off and esp_random() falls back to
        a software path that produces stuck/biased output, the health
        check will catch it.
        """
        # Simulate stuck radio → degraded RNG output
        bad_samples = bytearray()
        for i in range(SAMPLE_SIZE):
            # Simulate weak entropy: mostly zeros with occasional values
            if i % 10 == 0:
                bad_samples.append((i * 37) & 0xFF)
            else:
                bad_samples.append(0x00)

        status, details = health_check(bytes(bad_samples))
        assert status != EntropyStatus.OK

    def test_biased_hardware_fails(self):
        """Simulate ADC stuck high → bit bias detectable."""
        bad_samples = bytes([0xFF] * SAMPLE_SIZE)
        status, _ = health_check(bad_samples)
        assert status != EntropyStatus.OK

    def test_five_layer_defense_overview(self):
        """Document that health checks are necessary but not sufficient.

        The Coldcard bug required FIVE things to go wrong simultaneously:
        1. #ifdef logic error (compiled out hardware RNG)
        2. Software fallback used non-crypto PRNG
        3. PRNG seeded from small space (chip serial + timer)
        4. No runtime health checks
        5. No independent audit of entropy source

        Quartz addresses each:
        1. No compile-time RNG switches (#ifdef defense)
        2. Uses esp_fill_random (crypto-grade) + triple-mix
        3. RF noise = true physical entropy, not seed-derived
        4. NIST 800-90B health checks on every key generation
        5. Birth certificate includes entropy sample hash for audit
        """
        # This test exists to document the defense-in-depth model
        assert MIN_BITS_PER_BYTE == 6.0
        assert SAMPLE_SIZE == 1024
