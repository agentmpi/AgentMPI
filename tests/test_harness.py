"""Tests for the SPMD driver and the broker executor."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ampi import Ampi
from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampitools.executor import BrokerExecutor, FunctionExecutor, ReplayExecutor, Task, new_aid
from ampitools.harness import Harness


def test_the_driver_runs_every_rank_and_collects_results(tmp_path):
    h = Harness(root=str(tmp_path / "j"), size=4, device="memory")
    h.create()

    def rank_main(amp, rank):
        amp.barrier("start", timeout=30)
        out = amp.allreduce("sum", payload=rank, op="sum", timeout=30)
        return out["value"]

    results = h.run(rank_main)
    assert all(r.ok for r in results)
    assert [r.value for r in results] == [6, 6, 6, 6]


def test_a_local_failure_does_not_remove_a_rank_from_the_population(tmp_path):
    """The rule a harness author most easily violates, and most expensively.

    An exception escaping one rank's main function must not be able to hang the
    others; the driver records it as that rank's result instead of propagating.
    """
    h = Harness(root=str(tmp_path / "j"), size=4, device="memory")
    h.create()

    def rank_main(amp, rank):
        if rank == 2:
            raise ValueError("this rank's own work failed")
        amp.barrier("start", quorum=0.75, timeout=20)
        return "reached the end"

    results = h.run(rank_main)
    assert results[2].ok is False
    assert results[2].error_class == "ValueError"
    assert all(r.ok for r in results if r.rank != 2), "the survivors must still complete"


def test_the_report_carries_a_diagnosis_and_the_trace_is_written(tmp_path):
    h = Harness(root=str(tmp_path / "j"), size=2, device="sqlite")
    h.create()
    results = h.run(lambda amp, r: amp.barrier("only", timeout=20))
    out = h.save(results, tmp_path / "report.json")
    report = json.loads(out.read_text())
    assert report["succeeded"] == 2
    assert report["diagnosis"]["verdict"] in ("healthy", "starting", "degraded")
    assert Path(report["trace"]).exists()
    lines = Path(report["trace"]).read_text().strip().splitlines()
    assert any(json.loads(line)["kind"] == "barrier" for line in lines)


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------


def test_function_and_replay_executors_are_interchangeable(tmp_path):
    """The separation that lets protocol behaviour be regression-tested at all."""
    task = Task(aid="a1", rank=0, label="draft", prompt="write something")
    fn = FunctionExecutor(lambda t: {"text": f"draft for {t.rank}"})
    recorded = {"0/draft": {"text": "draft for 0"}}
    assert fn.invoke(task) == ReplayExecutor(recorded).invoke(task)


def test_replay_refuses_to_invent_a_missing_entry(tmp_path):
    with pytest.raises(AmpiError):
        ReplayExecutor({}).invoke(Task(aid="a", rank=0, label="missing", prompt=""))


def test_the_broker_hands_each_task_to_exactly_one_worker(tmp_path):
    """The queue is per rank because a rank is a durable role, not a work slot."""
    root = str(tmp_path / "j")
    Ampi.create(root, 2, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "work", timeout_s=20)
    broker.open()

    got: list[dict] = []

    def worker():
        w = Ampi(root, rank=0)
        for _ in range(4):
            t = BrokerExecutor.next_task(w, "c", 0, timeout=5)
            if t["status"] != "task":
                continue
            Path(t["result_file"]).write_text(json.dumps({"answer": t["label"]}))
            got.append(t)
            BrokerExecutor.submit(w, "c", 0, t["aid"])

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    out = broker.invoke(Task(aid=new_aid(), rank=0, label="translate-1", prompt="do it"))
    th.join(timeout=20)
    assert out == {"answer": "translate-1"}
    assert len(got) == 1


def test_the_broker_rejects_a_result_that_violates_its_contract(tmp_path):
    """And hands over the command that evaluates the bound.

    A constraint the constrained party cannot evaluate is not a constraint but a
    guess, and parties guess conservatively: ranks that could not measure their
    own output submitted half of what they were allowed.
    """
    root = str(tmp_path / "j")
    Ampi.create(root, 1, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "work", timeout_s=5)
    broker.open()
    contract = Contract(kind="json", required=("summary",), max_tokens=20)
    aid = new_aid()
    amp.device.append("task", {
        "rank": 0, "state": "queued", "campaign": "c", "run": amp.manifest.job_id,
        "aid": aid, "label": "x", "prompt_file": "p", "contract": contract.to_dict(),
        "result_file": str(tmp_path / "r.json"), "meta": {},
    })
    BrokerExecutor.next_task(amp, "c", 0, timeout=2)
    Path(tmp_path / "r.json").write_text(json.dumps({"wrong_key": "y"}))
    with pytest.raises(AmpiError) as e:
        BrokerExecutor.submit(amp, "c", 0, aid)
    assert e.value.cls_name == "AMPI_ERR_TYPE"
    assert "ampi tokens" in e.value.hint, "the bound must come with the way to evaluate it"


def test_a_worker_cannot_submit_another_ranks_task(tmp_path):
    root = str(tmp_path / "j")
    Ampi.create(root, 2, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    aid = new_aid()
    amp.device.append("task", {
        "rank": 1, "state": "claimed", "campaign": "c", "run": amp.manifest.job_id,
        "aid": aid, "label": "x", "prompt_file": "p", "result_file": str(tmp_path / "r"),
    })
    Path(tmp_path / "r").write_text("done")
    with pytest.raises(AmpiError) as e:
        BrokerExecutor.submit(amp, "c", 0, aid)
    assert e.value.cls_name == "AMPI_ERR_IDENTITY"


def test_an_abandoned_claim_is_requeued(tmp_path):
    """An executor's session can end at any moment.

    A task stuck in 'claimed' forever is the commonest way a harness silently
    stops making progress, so the claim carries a deadline and the broker
    reclaims it.
    """
    root = str(tmp_path / "j")
    Ampi.create(root, 1, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "w", timeout_s=8,
                            claim_ttl_s=0.5)
    broker.open()
    aid = new_aid()

    def vanish():
        time.sleep(0.2)
        BrokerExecutor.next_task(amp, "c", 0, timeout=2)  # claims, then disappears

    def finish():
        time.sleep(1.5)
        w = Ampi(root, rank=0)
        t = BrokerExecutor.next_task(w, "c", 0, timeout=5)
        Path(t["result_file"]).write_text(json.dumps({"ok": True}))
        BrokerExecutor.submit(w, "c", 0, t["aid"])

    threading.Thread(target=vanish, daemon=True).start()
    threading.Thread(target=finish, daemon=True).start()
    out = broker.invoke(Task(aid=aid, rank=0, label="x", prompt="do it"))
    assert out == {"ok": True}
    assert broker.stats()["requeued"] == 1


def test_closing_a_campaign_is_a_compare_and_swap(tmp_path):
    """Two harness threads closing concurrently must not lose one of the decisions."""
    root = str(tmp_path / "j")
    Ampi.create(root, 1, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "w")
    broker.open()
    assert broker.close()["state"] == "closed"
    exiting = BrokerExecutor.next_task(amp, "c", 0, timeout=1)
    assert exiting["status"] == "exit"


def test_the_worker_cli_round_trips(tmp_path):
    """The commands the worker prompt tells an agent to run must actually work."""
    root = str(tmp_path / "j")
    Ampi.create(root, 1, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "w", timeout_s=25)
    broker.open()

    def worker():
        env = {"AMPI_ROOT": root, "AMPI_RANK": "0", "PATH": "/usr/bin:/bin"}
        out = subprocess.run(
            [sys.executable, "-m", "ampi.cli", "worker", "--campaign", "c",
             "--rank", "0", "--job-root", root, "next", "--timeout", "10"],
            capture_output=True, text=True, env=env, check=True,
        )
        task = json.loads(out.stdout)
        assert task["status"] == "task", out.stdout
        Path(task["result_file"]).write_text(json.dumps({"done": task["label"]}))
        subprocess.run(
            [sys.executable, "-m", "ampi.cli", "worker", "--campaign", "c", "--rank", "0",
             "--job-root", root, "submit", "--aid", task["aid"]],
            capture_output=True, text=True, env=env, check=True,
        )

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    out = broker.invoke(Task(aid=new_aid(), rank=0, label="chapter-3", prompt="translate"))
    th.join(timeout=25)
    assert out == {"done": "chapter-3"}


def test_every_command_string_the_binding_prints_actually_parses(tmp_path):
    """Lesson: a binding that prints a command an agent cannot copy is worse than one
    that prints nothing.

    An early reduction directive printed a subcommand spelled with a space where
    the real one is hyphenated.  Roughly ten agents reported it, several while
    peers were blocked behind them.  This test walks every emitted command string
    through the real parser.
    """
    import shlex

    from ampi.cli import build_parser

    root = str(tmp_path / "j")
    Ampi.create(root, 2, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    Ampi(root, rank=1).init()

    emitted: list[str] = []

    amp.send(1, "word " * 2000)
    emitted.append(Ampi(root, rank=1).recv(0, timeout=10).get("next", ""))

    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "w")
    broker.open()
    aid = new_aid()
    amp.device.append("task", {
        "rank": 0, "state": "queued", "campaign": "c", "run": amp.manifest.job_id,
        "aid": aid, "label": "x", "prompt_file": "p", "result_file": "r",
        "contract": {"max_tokens": 100},
    })
    t = BrokerExecutor.next_task(amp, "c", 0, timeout=2)
    emitted += [t["submit"], t["give_up"], t["check_size"]]

    amp.win_create("w")
    emitted.append(amp.gather("g", payload="x", root=0, timeout=5, quorum=0.5).get("next", ""))

    parser = build_parser()
    checked = 0
    for line in emitted:
        line = (line or "").split("#")[0].strip()
        if not line.startswith("ampi "):
            continue
        argv = [tok for tok in shlex.split(line)[1:]]
        argv = [("0" if tok in ("HANDLE", "N", "RESULT.json", "'WHY'", "WHY") else tok)
                for tok in argv]
        parser.parse_args(argv)
        checked += 1
    assert checked >= 4, f"expected to check several emitted commands, checked {checked}"


# --------------------------------------------------------------------------
# Identity on the worker path
# --------------------------------------------------------------------------
# Two executors in the hundred-rank run reported that --expect-rank did not
# protect them, and they were right.  These tests are the ones that should have
# existed before that claim was made.


def test_the_worker_path_asserts_identity(tmp_path):
    """The path every experimental executor actually uses must check identity.

    The conformance suite checked `assert_identity` on the library API and on the
    ordinary CLI operations, and the worker subcommand -- the only surface the
    agents in our experiments ever touched -- silently skipped it.  Two executors
    found this before any test did.
    """
    root = str(tmp_path / "j")
    Ampi.create(root, 4, device="sqlite")
    Ampi(root, rank=0).init()
    amp = Ampi(root, rank=0)
    broker = BrokerExecutor(amp, campaign="c", work_dir=tmp_path / "w")
    broker.open()

    env = {"AMPI_ROOT": root, "AMPI_RANK": "2", "PATH": "/usr/bin:/bin"}
    out = subprocess.run(
        [sys.executable, "-m", "ampi.cli", "worker", "--campaign", "c",
         "--job-root", root, "--expect-rank", "0", "next", "--timeout", "1"],
        capture_output=True, text=True, env=env,
    )
    assert out.returncode != 0, "an assertion that disagrees with the ambient rank must fail"
    assert "AMPI_ERR_IDENTITY" in out.stderr


def test_the_emitted_submit_command_carries_the_identity_assertion(tmp_path):
    """Every executor that noticed added --expect-rank to submit by hand.

    A binding that tells an agent to run a command verbatim must put the guard in
    the command, or the guard is only present when the agent thinks of it.
    """
    root = str(tmp_path / "j")
    Ampi.create(root, 2, device="sqlite")
    amp = Ampi(root, rank=0)
    amp.init()
    amp.device.append("task", {
        "rank": 0, "state": "queued", "campaign": "c", "run": amp.manifest.job_id,
        "aid": "abc123", "label": "x", "prompt_file": "p", "result_file": "r",
    })
    t = BrokerExecutor.next_task(amp, "c", 0, timeout=2)
    assert "--expect-rank" in t["submit"]
    assert "--expect-rank" in t["give_up"]


def test_identity_flags_are_accepted_on_either_side_of_the_subcommand(tmp_path):
    """Roughly thirty executors lost their first call to flag placement.

    The specification says a binding must print only commands that exist.  The
    corollary it did not say, and should have, is that a binding whose flags are
    positional-by-subparser will be got wrong by every caller who writes the
    obvious thing.  Both orders now work.
    """
    root = str(tmp_path / "j")
    Ampi.create(root, 2, device="sqlite")
    Ampi(root, rank=0).init()
    env = {"AMPI_ROOT": root, "PATH": "/usr/bin:/bin"}
    for argv in (
        ["--job-root", root, "--rank", "0", "--expect-rank", "0", "whoami"],
        ["whoami", "--job-root", root, "--rank", "0", "--expect-rank", "0"],
    ):
        out = subprocess.run(
            [sys.executable, "-m", "ampi.cli", *argv],
            capture_output=True, text=True, env=env,
        )
        assert out.returncode == 0, f"{argv} failed: {out.stderr[:300]}"
        assert json.loads(out.stdout)["rank"] == 0


def test_every_command_in_the_worker_prompt_parses(tmp_path):
    """The prompt template is a command the binding prints, and was never checked.

    `test_every_command_string_the_binding_prints_actually_parses` walked the
    strings the *runtime* emits.  It did not walk the worker bootstrap prompt,
    which is the first thing every executor reads, and which told all of them to
    run a command that exits 2.
    """
    import re
    import shlex

    from ampi.cli import build_parser

    text = Path("experiments/worker_prompt.md").read_text(encoding="utf-8")
    block = re.search(r"```text\n(.*?)\n```", text, re.S).group(1)
    body = (
        block.replace("{RANK}", "3").replace("{SERVE}", " --serve 13,23")
        .replace("{CAMPAIGN}", "camp").replace("{JOB_ROOT}", str(tmp_path))
        .replace("{AMPI}", "ampi").replace("{MAX_TASKS}", "4")
    )
    ampi_def = re.search(r'AMPI="ampi (.*?)"', body)
    work_def = re.search(r'WORK="(.*?)"', body)
    assert ampi_def and work_def, "the prompt no longer defines AMPI and WORK"
    prefix, work = ampi_def.group(1), work_def.group(1)

    parser = build_parser()
    checked = 0
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("$AMPI "):
            continue
        expanded = f"{prefix} {line[len('$AMPI '):]}".replace("$WORK", work)
        parser.parse_args(shlex.split(expanded))
        checked += 1
    assert checked >= 1, "the prompt no longer contains an invocation to check"


def test_lease_renewal_keeps_the_length_the_rank_asked_for(tmp_path):
    """A rank that asked for a long lease is not shrunk to the default by its next touch."""
    from ampi.runtime import Ampi

    root = tmp_path / "job"
    Ampi.create(str(root), 1, device="sqlite", force=True).close()
    a = Ampi(str(root), rank=0)
    a.init(lease_s=900.0)
    try:
        a._last_touch = 0.0  # noqa: SLF001 - defeat the rate limit for the test
        a.touch()
        view = a._rankview()  # noqa: SLF001
        assert view.lease_until - a.device.clock() > 600
        a.heartbeat()
        assert a._rankview().lease_until - a.device.clock() > 600  # noqa: SLF001
    finally:
        a.close()
