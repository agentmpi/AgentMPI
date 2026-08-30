"""Recursive-doubling / Bruck partner sequences.

Recursive doubling (hypercube): at step k a rank exchanges with rank XOR 2^k.
This is optimal for short-message allgather, allreduce, and barrier when p is
a power of two: ⌈log2 p⌉ steps, full payload each step.

Bruck's algorithm (1997) handles non-power-of-two allgather/barrier with the
same logarithmic step count by rotating ranks and exchanging prefix blocks.

Rabenseifner's long-message allreduce is reduce-scatter (recursive halving)
followed by allgather (recursive doubling), cutting the bandwidth term from
n log p to ~2n.
"""

from __future__ import annotations


def is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def doubling_partners(rank: int, size: int) -> list[int | None]:
    """Partner at each XOR-doubling step. None if the partner is out of range."""
    partners: list[int | None] = []
    mask = 1
    while mask < size:
        peer = rank ^ mask
        partners.append(peer if peer < size else None)
        mask <<= 1
    return partners


def bruck_partners(rank: int, size: int) -> list[int]:
    """Distance-doubling partners used by Bruck allgather/barrier (always in range)."""
    partners: list[int] = []
    dist = 1
    while dist < size:
        partners.append((rank + dist) % size)
        dist <<= 1
    return partners


def reduce_scatter_partners(rank: int, size: int) -> list[tuple[int, int]]:
    """Recursive-halving partners and the block count exchanged at each step.

    Returns (peer, blocks_this_step) for a power-of-two communicator. Used by
    Rabenseifner reduce-scatter.
    """
    if not is_pow2(size):
        raise ValueError("Rabenseifner reduce-scatter requires power-of-two size")
    out: list[tuple[int, int]] = []
    mask = size >> 1
    blocks = size
    while mask > 0:
        peer = rank ^ mask
        out.append((peer, blocks // 2))
        blocks //= 2
        mask >>= 1
    return out
