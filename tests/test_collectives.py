from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentmpi.comm import Communicator
from agentmpi.types import Op


def _run(home: Path, size: int, fn, timeout_s: float = 20.0):
    Communicator(home, rank=0, size=size, bootstrap=True)

    def worker(rank: int):
        comm = Communicator(home, rank=rank, size=size, failure_timeout_s=8.0, poll_s=0.005)
        try:
            return fn(comm)
        finally:
            comm.finalize()

    with ThreadPoolExecutor(max_workers=size) as pool:
        return list(pool.map(worker, range(size)))


def test_barrier_and_bcast(tmp_path: Path):
    def fn(comm: Communicator):
        comm.barrier(timeout_s=10)
        msg = comm.bcast({"hello": comm.size} if comm.rank == 0 else None, root=0, timeout_s=10)
        return msg

    out = _run(tmp_path / "b", 5, fn)
    assert all(o == {"hello": 5} for o in out)


def test_scatter_gather_roundtrip(tmp_path: Path):
    size = 7
    payload = [f"c{i}" for i in range(size)]

    def fn(comm: Communicator):
        mine = comm.scatter(payload if comm.rank == 0 else None, root=0, timeout_s=10)
        gathered = comm.gather(mine.upper(), root=0, timeout_s=10)
        return mine, gathered

    out = _run(tmp_path / "sg", size, fn)
    mines = [m for m, _ in out]
    assert sorted(mines) == payload
    gathered = out[0][1]
    assert gathered == [c.upper() for c in payload]


def test_reduce_sum_and_allreduce(tmp_path: Path):
    def fn(comm: Communicator):
        s = comm.reduce(comm.rank + 1, op=Op.SUM, root=0, timeout_s=10)
        a = comm.allreduce(1, op=Op.SUM, timeout_s=10)
        return s, a

    out = _run(tmp_path / "r", 8, fn)
    assert out[0][0] == sum(range(1, 9))
    assert all(a == 8 for _, a in out)


def test_allgather_and_alltoall(tmp_path: Path):
    def fn(comm: Communicator):
        g = comm.allgather(comm.rank * 10, timeout_s=10)
        a = comm.alltoall([100 * comm.rank + i for i in range(comm.size)], timeout_s=10)
        return g, a

    out = _run(tmp_path / "aa", 4, fn)
    assert all(g == [0, 10, 20, 30] for g, _ in out)
    for rank, (_, a) in enumerate(out):
        assert a == [100 * src + rank for src in range(4)]


def test_scan_and_split(tmp_path: Path):
    def fn(comm: Communicator):
        prefix = comm.scan(1, op=Op.SUM, timeout_s=10)
        color = comm.rank % 2
        sub = comm.comm_split(color)
        return prefix, sub.size, sub.rank

    out = _run(tmp_path / "sc", 6, fn)
    prefixes = [p for p, _, _ in out]
    assert prefixes == [1, 2, 3, 4, 5, 6]
    # even color 0: ranks 0,2,4 -> size 3
    assert out[0][1] == 3 and out[0][2] == 0
    assert out[1][1] == 3 and out[1][2] == 0


def test_rma_lock_and_context(tmp_path: Path):
    def fn(comm: Communicator):
        comm.win_create("board", {"n": 0})
        comm.win_lock("board")
        try:
            val = comm.get("board")
            val["n"] = val["n"] + 1
            comm.put("board", val)
        finally:
            comm.win_unlock("board")
        comm.barrier(timeout_s=10)
        return comm.get("board")["n"]

    out = _run(tmp_path / "rma", 4, fn)
    assert out[0] == 4


def test_eager_vs_rendezvous(tmp_path: Path):
    home = tmp_path / "e"
    big = "x" * 20_000

    def fn(comm: Communicator):
        if comm.rank == 0:
            comm.send({"tiny": 1}, dest=1, tag=1)
            comm.send(big, dest=1, tag=2)
            return None
        a = comm.recv(source=0, tag=1, timeout_s=10)
        b = comm.recv(source=0, tag=2, timeout_s=10)
        events = (comm.home / "comms" / "world" / "logs" / "events.jsonl").read_text()
        return a, b, events

    out = _run(home, 2, fn)
    a, b, events = out[1]
    assert a == {"tiny": 1}
    assert b == big
    parsed = [json.loads(line) for line in events.splitlines() if line]
    sends = [e for e in parsed if e.get("event") == "send"]
    assert any(e["eager"] is True and e["tag"] == 1 for e in sends)
    assert any(e["eager"] is False and e["tag"] == 2 for e in sends)


def test_synthesize_reduce(tmp_path: Path):
    def fn(comm: Communicator):
        return comm.reduce(f"p{comm.rank}", op=Op.SYNTHESIZE, root=0, timeout_s=10)

    out = _run(tmp_path / "sy", 4, fn)
    assert out[0] is not None
    assert "p0" in out[0] and "p3" in out[0]
