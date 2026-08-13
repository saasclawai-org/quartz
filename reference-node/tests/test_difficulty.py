"""Tests for difficulty retargeting logic."""

import pytest
from quartz.blockchain import (
    Block, BlockHeader, RETARGET_PERIOD,
    retarget_difficulty_bits, adjust_difficulty,
    DIFFICULTY_BITS, BLOCK_TIME,
)


def _make_block(height: int, timestamp: int, difficulty: int = 12) -> Block:
    """Create a minimal block for testing."""
    header = BlockHeader(
        version=1,
        prev_block_hash=b'\x00' * 32,
        timestamp=timestamp,
        difficulty_target=difficulty,
        nonce=height,
    )
    return Block(header=header)


class TestRetargetDifficultyBits:
    """Test the retarget_difficulty_bits function."""

    def test_no_retarget_before_period(self):
        """Should not retarget with fewer than RETARGET_PERIOD + 1 blocks."""
        blocks = [_make_block(i, i * 30) for i in range(10)]
        result = retarget_difficulty_bits(12, blocks, 30)
        assert result == 12  # unchanged

    def test_exact_period_fast_blocks(self):
        """Blocks found too fast should increase difficulty."""
        # RETARGET_PERIOD blocks found in half the expected time
        # Expected: 144 * 30s = 4320s. Actual: 144 * 15s = 2160s (50% of target)
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, i * 15))  # 15s blocks instead of 30s
        result = retarget_difficulty_bits(12, blocks, 30)
        # ratio ~0.5, should increase by at least 1 bit
        assert result >= 13

    def test_exact_period_slow_blocks(self):
        """Blocks found too slow should decrease difficulty."""
        # RETARGET_PERIOD blocks found in double the expected time
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, i * 60))  # 60s blocks instead of 30s
        result = retarget_difficulty_bits(12, blocks, 30)
        # ratio ~2.0, should decrease by 2 bits
        assert result == 10

    def test_on_target_no_change(self):
        """Blocks found at target rate should not change difficulty."""
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, i * 30))  # exactly 30s
        result = retarget_difficulty_bits(12, blocks, 30)
        assert result == 12  # unchanged

    def test_slightly_fast_no_change(self):
        """Within 25% of target should not change difficulty."""
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, int(i * 28)))  # 28s vs 30s target = 0.93 ratio
        result = retarget_difficulty_bits(12, blocks, 30)
        assert result == 12  # within 25%, no change

    def test_clamp_at_minimum(self):
        """Difficulty should not go below 1."""
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, i * 300))  # 10x too slow
        result = retarget_difficulty_bits(2, blocks, 30)
        assert result >= 1

    def test_clamp_at_maximum(self):
        """Difficulty should not go above 32."""
        blocks = []
        for i in range(RETARGET_PERIOD + 1):
            blocks.append(_make_block(i, i * 3))  # 10x too fast
        result = retarget_difficulty_bits(31, blocks, 30)
        assert result <= 32

    def test_zero_actual_time(self):
        """Should handle zero or negative time spans gracefully."""
        blocks = [_make_block(i, 1000) for i in range(RETARGET_PERIOD + 1)]
        result = retarget_difficulty_bits(12, blocks, 30)
        # All same timestamp → actual_time = 0 → treated as 1s → very fast → increase
        assert result >= 12


class TestAdjustDifficulty:
    """Test the original adjust_difficulty function (target-based)."""

    def test_basic_increase(self):
        """Slow blocks → target increases (easier)."""
        target = 1000
        new = adjust_difficulty(target, actual_time=200, expected_time=100)
        assert new > target  # target went up = easier

    def test_basic_decrease(self):
        """Fast blocks → target decreases (harder)."""
        target = 1000
        new = adjust_difficulty(target, actual_time=50, expected_time=100)
        assert new < target  # target went down = harder

    def test_clamp_25pct(self):
        """Should clamp to ±25% change."""
        target = 1000
        # Way too slow: 10x expected
        new = adjust_difficulty(target, actual_time=1000, expected_time=100)
        assert new <= int(target * 1.25)

        # Way too fast: 0.1x expected
        new = adjust_difficulty(target, actual_time=10, expected_time=100)
        assert new >= int(target * 0.75)

    def test_zero_time(self):
        """Zero actual time should not crash."""
        new = adjust_difficulty(1000, actual_time=0, expected_time=100)
        assert new >= 1
