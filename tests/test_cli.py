"""The command-line binding.

These tests are the ones that matter most for the protocol's central claim:
that a rank can be *any process that runs shell commands*, holding no state
between operations.  Every rank here is a sequence of independent `ampi`
subprocesses, exactly as an agent's ranks are.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

AMPI = [sys.executable, "-m", "agentmpi.cli"]


def clean_env(**overrides: str) -> dict[str, str]:
    """A child environment with no inherited AgentMPI identity.

    A rank's identity lives in environment variables, which makes it easy to
    inherit one by accident -- from a parent shell, a CI runner, or another
    rank's session. Tests must never do that: a leaked AMPI_SIZE turns a
    four-rank job into a thirteen-rank one that waits forever for peers that
    do not exist.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    env.update(overrides)
    return env


def run_ampi(root: Path, rank: int, *args: str, timeout: float = 120,
             check: bool = True) -> subprocess.CompletedProcess:
    env = clean_env(AMPI_ROOT=str(root), AMPI_RANK=str(rank))
    proc = subprocess.run(AMPI + list(args), capture_output=True, text=True,
                          env=env, timeout=timeout)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"rank {rank} `ampi {' '.join(args)}` failed ({proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    proc = subprocess.run(AMPI + ["init", "--root", str(root), "--ranks", "4",
                                  "--label", "clitest"],
                          capture_output=True, text=True, timeout=60,
                          env=clean_env())
    assert proc.returncode == 0, proc.stderr
    return root


def test_init_and_info(run_dir: Path):
    proc = subprocess.run(AMPI + ["info", "--root", str(run_dir)],
                          capture_output=True, text=True, timeout=60,
                          env=clean_env())
    info = json.loads(proc.stdout)
    assert info["size"] == 4
    assert info["label"] == "clitest"


def test_rank_identity(run_dir: Path):
    out = json.loads(run_ampi(run_dir, 2, "rank").stdout)
    assert out["rank"] == 2 and out["size"] == 4


def test_send_recv_between_processes(run_dir: Path, tmp_path: Path):
    out_file = tmp_path / "got.txt"

    with ThreadPoolExecutor(max_workers=2) as pool:
        receiver = pool.submit(run_ampi, run_dir, 1, "recv", "--source", "0",
                               "--tag", "5", "--out", str(out_file), "--timeout", "60")
        sender = pool.submit(run_ampi, run_dir, 0, "send", "--dest", "1",
                             "--tag", "5", "--text", "hello over the wire")
        sender.result()
        receiver.result()

    assert out_file.read_text() == "hello over the wire"


def test_message_ordering_across_separate_processes(run_dir: Path, tmp_path: Path):
    """Non-overtaking must hold even though each send is a fresh process."""
    for i in range(6):
        run_ampi(run_dir, 0, "send", "--dest", "1", "--tag", "1",
                 "--json", json.dumps({"i": i}), "--type", "json")
    got = []
    for _ in range(6):
        proc = run_ampi(run_dir, 1, "recv", "--source", "0", "--tag", "1",
                        "--type", "json", "--timeout", "60")
        got.append(json.loads(proc.stdout)["i"])
    assert got == list(range(6))


def test_barrier_across_processes(run_dir: Path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_ampi, run_dir, r, "barrier", "--timeout", "90")
                   for r in range(4)]
        results = [json.loads(f.result().stdout) for f in futures]
    assert all(r["barrier"] == "released" for r in results)


def test_bcast_scatter_gather_across_processes(run_dir: Path, tmp_path: Path):
    chunks = ["alpha", "beta", "gamma", "delta"]

    def worker(rank: int) -> dict:
        spec_out = tmp_path / f"spec{rank}.txt"
        args = ["bcast", "--root", "0", "--out", str(spec_out), "--timeout", "120"]
        if rank == 0:
            args += ["--text", "translate into French"]
        run_ampi(run_dir, rank, *args)

        chunk_out = tmp_path / f"chunk{rank}.txt"
        args = ["scatter", "--root", "0", "--out", str(chunk_out), "--timeout", "120"]
        if rank == 0:
            args += ["--json", json.dumps(chunks), "--type", "json"]
        run_ampi(run_dir, rank, *args)
        mine = chunk_out.read_text().strip('"')

        gather_out = tmp_path / f"gathered{rank}.json"
        run_ampi(run_dir, rank, "gather", "--root", "0", "--text", mine.upper(),
                 "--out", str(gather_out), "--timeout", "120")
        return {"rank": rank, "spec": spec_out.read_text(), "chunk": mine,
                "gathered": gather_out.read_text() if rank == 0 else None}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))

    for r in results:
        assert "French" in r["spec"]
    assert sorted(r["chunk"] for r in results) == sorted(chunks)
    gathered = json.loads(results[0]["gathered"])
    assert gathered == [c.upper() for c in chunks]


def test_exscan_glossary_across_processes(run_dir: Path, tmp_path: Path):
    def worker(rank: int) -> dict:
        out = tmp_path / f"prefix{rank}.json"
        run_ampi(run_dir, rank, "scan", "--exclusive", "--op", "ampi_union",
                 "--json", json.dumps({f"term{rank}": [f"gloss{rank}"]}),
                 "--type", "json", "--out", str(out), "--timeout", "120")
        return json.loads(out.read_text())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))

    for rank, prefix in enumerate(results):
        expected = {f"term{i}": [f"gloss{i}"] for i in range(rank)}
        assert (prefix or {}) == expected, f"rank {rank}"


def test_window_put_get_query(run_dir: Path, tmp_path: Path):
    run_ampi(run_dir, 0, "win", "put", "--key", "findings/0",
             "--text", "the parser rejects unicode identifiers")
    run_ampi(run_dir, 1, "win", "put", "--key", "findings/1",
             "--text", "the scheduler deadlocks on cyclic graphs")

    ref = json.loads(run_ampi(run_dir, 2, "win", "get", "--key", "findings/0").stdout)
    assert ref["tokens"] > 0 and ref["version"] == 1

    materialized = run_ampi(run_dir, 2, "win", "get", "--key", "findings/0",
                            "--materialize").stdout
    assert "unicode" in materialized

    result = json.loads(run_ampi(run_dir, 2, "win", "query", "--question",
                                 "what is wrong with the scheduler",
                                 "--budget", "200").stdout)
    assert result["entries_total"] == 2
    assert any("scheduler" in e["content"] for e in result["returned"])


def test_window_atomic_counter_is_race_free(run_dir: Path):
    """Concurrent fetch-and-add must hand out distinct slots."""

    def bump(rank: int) -> float:
        proc = run_ampi(run_dir, rank % 4, "win", "fetch-add", "--key", "cursor",
                        "--delta", "1")
        return json.loads(proc.stdout)["previous"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(bump, range(8)))
    assert sorted(got) == list(range(8)), f"lost updates: {sorted(got)}"


def test_collective_file_write_has_one_writer(run_dir: Path, tmp_path: Path):
    def worker(rank: int) -> str:
        proc = run_ampi(run_dir, rank, "file", "write-at-all", "--path", "REPORT.md",
                        "--text", f"section from rank {rank}", "--timeout", "120")
        return proc.stdout

    with ThreadPoolExecutor(max_workers=4) as pool:
        outs = [json.loads(o) for o in pool.map(worker, range(4))]

    published = [o for o in outs if o["published"]]
    assert len(published) == 1, "exactly one aggregator must publish"

    content = run_ampi(run_dir, 0, "file", "read", "--path", "REPORT.md").stdout
    for rank in range(4):
        assert f"section from rank {rank}" in content


def test_structured_errors_on_timeout(run_dir: Path):
    proc = run_ampi(run_dir, 0, "recv", "--source", "1", "--tag", "99",
                    "--timeout", "1", check=False)
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err["error"] == "ERR_TIMEOUT"


def test_progress_and_status(run_dir: Path):
    run_ampi(run_dir, 0, "progress")
    run_ampi(run_dir, 0, "progress")
    status = json.loads(run_ampi(run_dir, 0, "status").stdout)
    assert status["turn"] == 2
    assert "peers" in status and len(status["peers"]) == 4

    observed = json.loads(subprocess.run(
        AMPI + ["peers", "--root", str(run_dir)], capture_output=True, text=True,
        timeout=60, env=clean_env()).stdout)
    assert observed["size"] == 4
    assert observed["turns_total"] >= 2


def test_checkpoint_and_restore(run_dir: Path):
    run_ampi(run_dir, 3, "checkpoint", "--json", json.dumps({"chapter": 7, "done": True}))
    out = json.loads(run_ampi(run_dir, 3, "checkpoint", "--restore").stdout)
    assert out["restored"] is True
    assert out["state"]["chapter"] == 7


def test_trace_summary(run_dir: Path):
    run_ampi(run_dir, 0, "send", "--dest", "1", "--tag", "1", "--text", "x")
    run_ampi(run_dir, 1, "recv", "--source", "0", "--tag", "1", "--timeout", "30")
    proc = subprocess.run(AMPI + ["trace", "summary", "--root", str(run_dir)],
                          capture_output=True, text=True, timeout=60,
                          env=clean_env())
    report = json.loads(proc.stdout)
    assert report["messages"] >= 1
    assert report["tokens_sent"] > 0
    assert len(report["matrix"]) == 4


def test_launchplan_emits_one_prompt_per_rank(run_dir: Path, tmp_path: Path):
    program = tmp_path / "program.md"
    program.write_text("Rank {{RANK}} of {{SIZE}}: receive, work, send.")
    out = tmp_path / "launch"
    proc = subprocess.run(AMPI + ["launchplan", "--root", str(run_dir),
                                  "--program", str(program), "--out", str(out)],
                          capture_output=True, text=True, timeout=60,
                          env=clean_env())
    assert proc.returncode == 0, proc.stderr
    plan = json.loads((out / "plan.json").read_text())
    assert len(plan) == 4
    text = (out / "rank-002.md").read_text()
    assert "Rank 2 of 4" in text
    assert 'export AMPI_RANK="2"' in text
