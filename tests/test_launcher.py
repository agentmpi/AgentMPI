"""``ampirun``: one process per rank, supervised, with restart."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from ampi.runtime import Ampi
from ampitools.launcher import EXIT_EXECUTOR_DIED, export, launch, main

PROGRAM = textwrap.dedent(
    """
    import json, os, sys
    from ampi.runtime import Ampi
    root, rank = os.environ["AMPI_ROOT"], int(os.environ["AMPI_RANK"])
    amp = Ampi(root, rank=rank, expect_rank=rank)
    info = amp.init()
    # Die once, on the first epoch, if told to: the launcher must respawn us and
    # the runtime must hand the new process a new epoch.
    if os.environ.get("DIE") == str(rank) and info["epoch"] == 1:
        amp.memo("died", "on purpose")
        sys.exit(75)
    total = amp.allreduce("sum", payload=rank, op="sum", timeout=60)
    amp.barrier("done", timeout=60)
    amp.memo("result", {"epoch": info["epoch"], "sum": total["value"],
                        "worker": os.environ.get("AMPI_WORKER_ID")})
    amp.finalize()
    print(json.dumps({"rank": rank, "sum": total["value"]}))
    """
)


def _program(tmp_path: Path) -> list[str]:
    p = tmp_path / "prog.py"
    p.write_text(PROGRAM)
    return [sys.executable, str(p)]


def test_launch_runs_one_process_per_rank(tmp_path):
    rec = launch(_program(tmp_path), size=4, root=tmp_path / "job", log_dir=tmp_path / "log",
                 quiet=True, timeout_s=120)
    assert rec["exited"] == 4 and rec["failed"] == 0 and not rec["timed_out"]
    assert rec["ranks"] == [0, 1, 2, 3]
    pids = {s["pid"] for s in rec["rank_states"].values()}
    assert len(pids) == 4, "each rank must be its own operating-system process"
    amp = Ampi(str(tmp_path / "job"), rank=0, allow_volatile=True)
    try:
        memo = amp.memo("result")
        assert memo["value"]["sum"] == 6
        assert memo["value"]["worker"].endswith(":r0")
        finals = [e for e in amp.events() if e["kind"] == "finalize"]
        assert {e["rank"] for e in finals} == {0, 1, 2, 3}
    finally:
        amp.close()
    # the launch record names every rank before any of them ran, and the outcome after
    saved = json.loads((tmp_path / "log" / "launch-node0.json").read_text())
    assert saved["rank_states"]["3"]["state"] == "exited"
    assert (tmp_path / "log" / "rank2.out").read_text().strip().endswith('"sum": 6}')


def test_a_dead_rank_is_respawned_with_a_new_epoch(tmp_path):
    rec = launch(_program(tmp_path), size=3, root=tmp_path / "job", log_dir=tmp_path / "log",
                 env={"DIE": "1"}, respawn=1, quiet=True, timeout_s=120)
    assert rec["exited"] == 3 and rec["restarts"] == 1
    st = rec["rank_states"]["1"]
    assert st["restarts"] == 1 and st["state"] == "exited"
    kinds = [e["event"] for e in rec["events"] if e["rank"] == 1]
    assert kinds == ["start", "exit", "respawn", "start", "exit"]
    assert [e for e in rec["events"] if e["rank"] == 1 and e["event"] == "exit"][0]["code"] \
        == EXIT_EXECUTOR_DIED
    amp = Ampi(str(tmp_path / "job"), rank=1, allow_volatile=True)
    try:
        assert amp.memo("result")["value"]["epoch"] == 2
        assert amp.memo("result")["value"]["sum"] == 3
    finally:
        amp.close()


def test_without_respawn_a_dead_rank_is_reported_and_peers_time_out(tmp_path):
    rec = launch(_program(tmp_path), size=2, root=tmp_path / "job", log_dir=tmp_path / "log",
                 env={"DIE": "0"}, respawn=0, quiet=True, timeout_s=90)
    assert rec["rank_states"]["0"]["state"] == "failed"
    assert rec["failed"] >= 1


def test_multi_node_launches_share_one_job(tmp_path):
    # Two launchers on one machine standing in for two nodes: node 0 creates the
    # job, node 1 joins it, and the sixteen ranks form one population.
    import threading

    root = tmp_path / "job"
    out: dict[int, dict] = {}

    def node(k: int) -> None:
        out[k] = launch(_program(tmp_path), size=6, root=root, nodes=2, node=k,
                        log_dir=tmp_path / f"log{k}", quiet=True, timeout_s=120)

    t0 = threading.Thread(target=node, args=(0,))
    t0.start()
    import time
    time.sleep(0.5)
    t1 = threading.Thread(target=node, args=(1,))
    t1.start()
    t0.join()
    t1.join()
    assert out[0]["ranks"] == [0, 1, 2] and out[1]["ranks"] == [3, 4, 5]
    assert out[0]["created_here"] and not out[1]["created_here"]
    assert out[0]["exited"] == 3 and out[1]["exited"] == 3
    rep = export(root, tmp_path / "evidence", name="two-node")
    assert rep["size"] == 6 and rep["events"]["finalize"] == 6
    assert (tmp_path / "evidence" / "harness.trace.jsonl").exists()


def test_cli(tmp_path, capsys):
    code = main(["-np", "2", "--root", str(tmp_path / "job"), "--log-dir", str(tmp_path / "log"),
                 "--timeout", "120", "-q", "--export", str(tmp_path / "ev"), "--",
                 *_program(tmp_path)])
    assert code == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["exited"] == 2 and summary["export"]["verdict"]
