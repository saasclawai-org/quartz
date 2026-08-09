"""
Quartz (QZ) — Python Reference Node

A desktop implementation of the Quartz protocol for testing,
block exploration, and wallet operations. NOT for real mining —
real mining requires ESP32 hardware.
"""

__version__ = "0.1.0"

from .blockchain import Block, Transaction, BlockHeader
from .crystal_hash import crystal_hash_verify, check_difficulty
from .node import QuartzNode
from .wallet import Wallet

__all__ = [
    "Block", "Transaction", "BlockHeader",
    "crystal_hash_verify", "check_difficulty",
    "QuartzNode", "Wallet",
]
