from agentmpi.algorithms.doubling import bruck_partners, doubling_partners, is_pow2
from agentmpi.algorithms.trees import binomial_children, binomial_parent, binomial_steps


def test_binomial_root_has_no_parent():
    for size in range(1, 17):
        for root in range(size):
            assert binomial_parent(root, root, size) is None


def test_binomial_every_nonroot_has_one_parent():
    for size in range(1, 17):
        for root in range(size):
            parents = [binomial_parent(r, root, size) for r in range(size) if r != root]
            assert all(p is not None for p in parents)
            children_flat = [c for r in range(size) for c in binomial_children(r, root, size)]
            assert sorted(children_flat) == sorted([r for r in range(size) if r != root])


def test_binomial_steps_is_ceil_log():
    assert binomial_steps(1) == 0
    assert binomial_steps(2) == 1
    assert binomial_steps(3) == 2
    assert binomial_steps(8) == 3
    assert binomial_steps(9) == 4


def test_doubling_xor():
    assert doubling_partners(0, 8) == [1, 2, 4]
    assert doubling_partners(3, 8) == [2, 1, 7]
    assert doubling_partners(0, 5) == [1, 2, 4]


def test_bruck_wraps():
    assert bruck_partners(0, 5) == [1, 2, 4]
    assert bruck_partners(4, 5) == [0, 1, 3]


def test_is_pow2():
    assert is_pow2(8)
    assert not is_pow2(6)
    assert not is_pow2(0)
