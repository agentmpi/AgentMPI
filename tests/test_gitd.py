"""The gitd device: wire encoding, group commit, pipelining, and stale daemons."""

from __future__ import annotations

import threading

import pytest

from ampi.device.base import Ge, In, IsNull, Ne, NotNull
from ampi.device.gitd import GitdDevice, decode_predicate, encode_predicate, socket_path
from ampi.runtime import Ampi

pytestmark = pytest.mark.slow


def test_predicates_survive_the_wire():
    pred = {"a": In([1, "x"]), "b": Ge(3), "c": Ne(None), "d": IsNull(), "e": NotNull(), "f": 7}
    back = decode_predicate(encode_predicate(pred))
    assert back["a"] == In([1, "x"]) and back["b"] == Ge(3) and back["c"] == Ne(None)
    assert isinstance(back["d"], IsNull) and isinstance(back["e"], NotNull) and back["f"] == 7


def test_socket_path_is_short_and_stable(tmp_path):
    p = socket_path(tmp_path / "job")
    assert p == socket_path(tmp_path / "job") and len(p) < 60 and p.startswith("/tmp/ampi-gitd-")


def test_concurrent_writers_are_group_committed(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPI_GITD_IDLE_S", "5")
    root = tmp_path / "job"
    dev = GitdDevice(root)
    dev.initialize()
    try:
        errors: list[str] = []

        def writer(i: int) -> None:
            try:
                for j in range(5):
                    dev_i = GitdDevice(root)
                    dev_i.append("event", {"kind": "t", "rank": i, "j": j, "run": "r"})
                    dev_i.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        rows = dev.scan("event", {"kind": "t"})
        assert len(rows) == 30 and len({r["seq"] for r in rows}) == 30
        stats = dev.stats()["daemon"]
        assert stats["batched_ops"] >= 30
        # thirty operations from six concurrent writers took fewer than thirty pushes
        assert stats["batches"] < 30 and stats["largest_batch"] >= 2
    finally:
        dev.shutdown_daemon()


def test_pipelined_creation_and_a_job_on_top(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPI_GITD_IDLE_S", "5")
    root = tmp_path / "job"
    job = Ampi.create(str(root), 12, device="gitd", force=True, allow_volatile=True)
    try:
        stats = job.device.stats()["daemon"]
        # 12 ranks -> 24 cells plus a manifest, a comm and a trace: pipelined into few pushes
        assert stats["batched_ops"] >= 26 and stats["batches"] < stats["batched_ops"]
        for r in range(12):
            assert job.device.read("rank", str(r)) is not None
        assert job.device.read("comm", "world") is not None
    finally:
        job.close()
    small = Ampi.create(str(root), 2, device="gitd", force=True, allow_volatile=True)
    small.close()
    a = Ampi(str(root), rank=0, allow_volatile=True)
    b = Ampi(str(root), rank=1, allow_volatile=True)
    try:
        a.init()
        b.init()
        out: dict[int, dict] = {}
        t = threading.Thread(target=lambda: out.__setitem__(1, b.allreduce("s", payload=1, op="sum", timeout=60)))
        t.start()
        out[0] = a.allreduce("s", payload=2, op="sum", timeout=60)
        t.join()
        assert out[0]["value"] == 3 and out[1]["value"] == 3
    finally:
        a.device.shutdown_daemon()
        a.close()
        b.close()


def test_a_stale_daemon_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPI_GITD_IDLE_S", "30")
    root = tmp_path / "job"
    first = GitdDevice(root)
    first.initialize()
    pid = first._call("hello")["pid"]
    first.close()
    # the working tree vanishes underneath the daemon, as when a run is recreated
    import shutil

    shutil.rmtree(root)
    second = GitdDevice(root)
    second.initialize()
    try:
        assert second._call("hello")["pid"] != pid
        assert second._call("hello")["root_exists"]
        from pathlib import Path

        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        second.shutdown_daemon()
