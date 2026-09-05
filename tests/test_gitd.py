"""The gitd device: wire encoding, group commit, pipelining, and stale daemons."""

from __future__ import annotations

import threading
from typing import Any

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


def test_readers_do_not_wait_for_a_busy_writer(tmp_path):
    """A reader that has a fresh copy must not queue behind the writer's lock."""
    import time

    from ampi.device.gitlog import GitDevice

    dev = GitDevice(tmp_path / "job", read_interval=30.0)
    dev.initialize()
    dev.append("event", {"kind": "t", "rank": 0, "run": "r"})   # fetched just now
    held = threading.Event()

    def hold_the_lock() -> None:
        with dev._locked():
            held.set()
            time.sleep(2.0)

    t = threading.Thread(target=hold_the_lock)
    t.start()
    held.wait()
    t0 = time.time()
    rows = dev.scan("event", {"kind": "t"})
    assert time.time() - t0 < 0.5 and len(rows) == 1
    # even a reader whose copy is stale does not wait for the writer's lock
    dev._last_fetch = 0.0
    t0 = time.time()
    rows = dev.scan("event", {"kind": "t"})
    assert time.time() - t0 < 0.5 and len(rows) == 1
    # the parsed copy is reused while the file is unchanged, and refreshed when it is not
    assert dev._cached is not None
    dev.append("event", {"kind": "t", "rank": 1, "run": "r"})
    assert len(dev.scan("event", {"kind": "t"})) == 2
    t.join()


def test_clients_survive_a_daemon_that_dies_mid_call(tmp_path, monkeypatch):
    """The daemon is replaced from under a connected client; the client reconnects."""
    import os
    import signal
    import time

    monkeypatch.setenv("AMPI_GITD_IDLE_S", "30")
    root = tmp_path / "job"
    dev = GitdDevice(root)
    dev.initialize()
    dev.append("event", {"kind": "t", "rank": 0, "run": "r"})
    pid = dev._call("hello")["pid"]
    results: list[Any] = []

    def poll() -> None:
        for _ in range(40):
            try:
                results.append(len(dev.scan("event", {"kind": "t"})))
            except Exception as exc:  # noqa: BLE001
                results.append(exc)
            time.sleep(0.05)

    t = threading.Thread(target=poll)
    t.start()
    time.sleep(0.3)
    os.kill(pid, signal.SIGKILL)
    t.join()
    try:
        assert all(r == 1 for r in results), results
        assert dev._call("hello")["pid"] != pid
    finally:
        dev.shutdown_daemon()


def test_batch_window_widens_under_rejection(tmp_path, monkeypatch):
    """The worker doubles its window while pushes are rejected and relaxes after."""

    from ampi.device.gitd import MAX_BATCH_S, GitDaemon

    d = GitDaemon(tmp_path / "job", batch_window=0.05)
    rejected = {"per_batch": 1}

    def commit(batch):
        d.dev.rejections += rejected["per_batch"]
        return [("ok", 1)] * len(batch)

    monkeypatch.setattr(d, "_commit", commit)
    worker = threading.Thread(target=d._worker, daemon=True)
    worker.start()
    try:
        windows = []
        for per_batch in (1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0):
            rejected["per_batch"] = per_batch
            ev, slot = d.enqueue("append", {})
            assert ev.wait(5) and slot[0] == ("ok", 1)
            windows.append(d.batch_window)
        assert MAX_BATCH_S in windows[:8], windows
        assert windows[-1] == pytest.approx(0.05), windows
        assert d.batches == 16
    finally:
        d._stop.set()
        worker.join(2)


def test_a_request_resent_after_an_ambiguous_reply_is_applied_once(tmp_path, monkeypatch):
    """The daemon remembers (client, id): a resend gets the first outcome, not a second row."""
    import json
    import socket

    monkeypatch.setenv("AMPI_GITD_IDLE_S", "30")
    root = tmp_path / "job"
    dev = GitdDevice(root)
    dev.initialize()
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(dev.sock_path)
        f = s.makefile("rb")
        req = json.dumps({"id": 7, "client": "c-test", "op": "append",
                          "args": {"stream": "event", "record": {"kind": "t", "rank": 0, "run": "r"}}})
        s.sendall((req + "\n").encode())
        first = json.loads(f.readline())
        s.sendall((req + "\n").encode())          # the client never saw `first`; it resends
        second = json.loads(f.readline())
        assert first["ok"] and second["ok"] and first["result"] == second["result"]
        assert len(dev.scan("event", {"kind": "t"})) == 1
        s.close()
    finally:
        dev.shutdown_daemon()
