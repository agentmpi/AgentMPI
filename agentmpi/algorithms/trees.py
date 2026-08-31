"""Binomial-tree neighborhoods used by broadcast, scatter, gather, and reduce.

The relative-rank formulation is the one used in MPICH (Thakur, Rabenseifner,
Gropp, IJHPCA 2005): rank r is rewritten as (r - root) mod p so the logical
root is always 0. A process's parent is obtained by clearing its lowest set
bit; children are obtained by setting higher zero bits that stay in range.

Latency is ⌈log2 p⌉ steps. Bandwidth per step is the full payload n, so
T_tree = ⌈log2 p⌉ (α + nβ). This is the short-message algorithm.
"""

from __future__ import annotations


def _rel(rank: int, root: int, size: int) -> int:
    return (rank - root) % size


def _abs(rel: int, root: int, size: int) -> int:
    return (rel + root) % size


def binomial_parent(rank: int, root: int, size: int) -> int | None:
    if size <= 0:
        raise ValueError("size must be positive")
    rel = _rel(rank, root, size)
    if rel == 0:
        return None
    parent_rel = rel - (rel & -rel)
    return _abs(parent_rel, root, size)


def binomial_children(rank: int, root: int, size: int) -> list[int]:
    if size <= 0:
        raise ValueError("size must be positive")
    rel = _rel(rank, root, size)
    children: list[int] = []
    mask = 1
    while mask < size:
        if rel & mask == 0:
            child_rel = rel | mask
            if child_rel < size:
                children.append(_abs(child_rel, root, size))
        else:
            break
        mask <<= 1
    return children


def binomial_steps(size: int) -> int:
    if size <= 1:
        return 0
    steps = 0
    n = 1
    while n < size:
        n <<= 1
        steps += 1
    return steps
