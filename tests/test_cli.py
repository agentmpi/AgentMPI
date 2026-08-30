"""The command surface exposed to agent ranks.

The tests here exist because of a specific failure. The runtime hands each worker
the exact command to run when it finishes, on the principle that a protocol
surface exposed to an agent should require recognition rather than recall. In the
first experimental campaign the emitted command was itself malformed — ``--rank``
is an option of the ``worker`` subcommand and was being placed after ``done`` —
and every one of the nine workers hit the parse error.

They all recovered, because they could read the error and retry. That is not
reassurance: a worker without the ability to retry, or one that had treated the
submission as fire-and-forget, would have silently lost completed work. The lesson
is that a generated command string is executable code and must be tested like it,
so :func:`test_emitted_commands_parse` round-trips both strings through the real
parser with the placeholders filled in.
"""

from __future__ import annotations

import json
import shlex

import pytest

import agentmpi as ampi
from agentmpi.cli import _done_cmd, _fail_cmd, _lenient_json, build_parser, main


def _argv(command: str, **subs: str) -> list[str]:
    for key, value in subs.items():
        command = command.replace(key, value)
    argv = shlex.split(command)
    assert argv[0] == "ampi"
    return argv[1:]


def test_emitted_commands_parse(tmp_path):
    """The submit and give-up strings must be accepted by the real parser."""
    parser = build_parser()
    root = tmp_path / "job"

    done = _argv(_done_cmd(root, 3, 17), **{"<RESULT_FILE>": str(tmp_path / "r.json")})
    args = parser.parse_args(done)
    assert args.cmd == "worker" and args.worker_cmd == "done"
    assert args.rank == 3 and args.aid == 17
    assert args.root == str(root)

    fail = _argv(_fail_cmd(root, 5, 21), **{"<REASON>": "model refused"})
    args = parser.parse_args(fail)
    assert args.cmd == "worker" and args.worker_cmd == "fail"
    assert args.rank == 5 and args.aid == 21
    assert args.error == "model refused"


def test_worker_round_trip(tmp_path, capsys):
    """A full claim/do/submit cycle through the command surface only.

    This is the agent-side path: nothing here touches the Python API, so it is the
    same sequence a subagent with shell access executes.
    """
    root = tmp_path / "job"
    assert main(["--root", str(root), "init", "--size", "2"]) == 0
    capsys.readouterr()

    fabric = ampi.Fabric(root)
    blob = fabric.blobs.put("Do the thing and return {\"ok\": true}")
    import time

    with fabric.write() as cur:
        cur.execute(
            "INSERT INTO agent_calls(rank, ctx, kind, label, state, prompt_digest, prompt_tokens,"
            " created_at, incarnation, attempt) VALUES(0,0,'task','unit',?,?,?,?,1,1)",
            ("pending", blob.digest, blob.tokens, time.time()),
        )

    assert main(["--root", str(root), "worker", "--rank", "0", "next", "--timeout", "2", "--poll", "0.1"]) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "task"
    assert task["label"] == "unit"

    # The worker follows the emitted command verbatim, as the bootstrap prompt says.
    result_path = task["result_file"]
    with open(result_path, "w", encoding="utf-8") as fh:
        fh.write('```json\n{"ok": true, "detail": "done"}\n```\n')
    argv = _argv(task["submit"])
    assert main(argv) == 0
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["status"] == "done"
    assert submitted["parsed"] is True, "a fenced JSON result must still be parsed"

    row = fabric.query_one("SELECT state, result_digest FROM agent_calls WHERE aid=1")
    assert row["state"] == "done"
    assert fabric.blobs.get(row["result_digest"], "json") == {"ok": True, "detail": "done"}


def test_worker_idle_and_exit(tmp_path, capsys):
    root = tmp_path / "job"
    assert main(["--root", str(root), "init", "--size", "1"]) == 0
    capsys.readouterr()
    # No work: the worker reports idle with a distinguishable exit code so the
    # bootstrap loop can count consecutive idles without parsing prose.
    assert main(["--root", str(root), "worker", "--rank", "0", "next", "--timeout", "0.2", "--poll", "0.1"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "idle"

    assert main(["--root", str(root), "worker", "--rank", "0", "stop"]) == 0
    capsys.readouterr()
    assert main(["--root", str(root), "worker", "--rank", "0", "next", "--timeout", "0.2", "--poll", "0.1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "exit"


def test_campaign_pointer_accepts_a_directory_of_fabrics(tmp_path, capsys):
    """A benchmark that sweeps a parameter creates one fabric per point."""
    campaign = tmp_path / "camp"
    campaign.mkdir()
    parent = tmp_path / "sweep"
    for name in ("point-a", "point-b"):
        assert main(["--root", str(parent / name), "init", "--size", "1"]) == 0
    capsys.readouterr()
    (campaign / "active").write_text(str(parent), encoding="utf-8")

    assert main(["worker", "--campaign", str(campaign), "--rank", "0", "hello"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"

    # Work queued in the *second* fabric must still be found.
    fabric = ampi.Fabric(parent / "point-b")
    blob = fabric.blobs.put("second point")
    import time

    with fabric.write() as cur:
        cur.execute(
            "INSERT INTO agent_calls(rank, ctx, kind, label, state, prompt_digest, prompt_tokens,"
            " created_at, incarnation, attempt) VALUES(0,0,'task','b',?,?,?,?,1,1)",
            ("pending", blob.digest, blob.tokens, time.time()),
        )
    assert main(["worker", "--campaign", str(campaign), "--rank", "0", "next", "--timeout", "2", "--poll", "0.1"]) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "task" and task["label"] == "b"
    assert task["root"].endswith("point-b")

    (campaign / "stop").write_text("1", encoding="utf-8")
    assert main(["worker", "--campaign", str(campaign), "--rank", "0", "next", "--timeout", "0.2"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "exit"


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Here is the answer:\n{"a": 1}\nHope that helps.', {"a": 1}),
        ('{"a": "a } brace in a string"}', {"a": "a } brace in a string"}),
        ("not json at all", "not json at all"),
    ],
)
def test_lenient_json(text, expected):
    """A worker that produced a good artifact must not lose it to a fence."""
    assert _lenient_json(text) == expected


def test_doctor_reports_an_incomplete_collective(tmp_path, capsys):
    """`doctor` must name the ranks that did not arrive."""

    def rank_main(comm):
        if comm.rank == 2:
            return "left early"
        comm.barrier(timeout=1.0, policy="proceed", label="phase")
        return "ok"

    job = ampi.launch(rank_main, size=4, root=tmp_path / "d", timeout=60)
    assert job.ok, [o.error for o in job.outcomes if not o.ok]
    capsys.readouterr()
    main(["--root", str(tmp_path / "d"), "doctor"])
    report = json.loads(capsys.readouterr().out)
    kinds = {f["issue"] for f in report["findings"]}
    assert any("fail_stop" in k or "collective" in k for k in kinds), report


def test_acceptance_report_parsing():
    """The suite's report must survive the trip back into the harness.

    An earlier version sliced from the *last* brace in the output, which lands inside
    a nested per-case object and never parses, so every run was reported as
    unimportable with zero passes. That report is what the harness scatters back to
    the population as the definition of done, so the agents were told a passing build
    had failed and spent a repair round on nothing. A bug in the plumbing around an
    oracle is as damaging as a bug in the oracle, and neither is visible without a
    test like this one.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
    from software import parse_report  # noqa: PLC0415 - path set above

    report = {
        "importable": True,
        "n_total": 2,
        "n_passed": 1,
        "cases": [
            {"name": "a", "passed": True},
            {"name": "b", "passed": False, "reason": "expected {'x': 1}, got {'x': 2}"},
        ],
    }
    payload = json.dumps(report)
    assert parse_report(payload)["n_passed"] == 1
    assert parse_report(payload + "\n")["n_total"] == 2
    assert parse_report("warning: something\n" + payload)["importable"] is True
    # Nested braces in the trailing case must not confuse it.
    assert parse_report(payload)["cases"][1]["reason"].endswith("got {'x': 2}")
    # Genuine failures still report as failures.
    assert parse_report("")["importable"] is False
    assert parse_report("", "Traceback ...")["import_error"] == "Traceback ..."
    assert parse_report("total garbage")["importable"] is False


def test_review_excerpt_never_cuts_mid_line():
    """Source sent for review must be truncated at a line boundary, and say so.

    Two reviewer ranks reported that the code they were given stopped mid-function,
    because an earlier version sliced at a fixed character count. A reviewer handed a
    syntactically incomplete file cannot distinguish a real defect from the cut, and
    both of them correctly flagged the truncation instead of the code -- a wasted
    review round. Marking the elision costs nothing.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
    from software import _excerpt  # noqa: PLC0415 - path set above

    short = "def f():\n    return 1\n"
    assert _excerpt(short, 9000) == short, "short files must pass through untouched"

    code = "".join(f"def f{i}():\n    return {i}\n" for i in range(500))
    out = _excerpt(code, 200)
    assert len(out) < len(code)
    body = out.split("# ...")[0]
    # Every retained line must be whole: no line of the excerpt may be a prefix of a
    # source line without being the whole line.
    source_lines = set(code.splitlines())
    assert all(line in source_lines for line in body.splitlines() if line), body
    assert "further lines of this file were not included" in out
    # The stated count must be right, counted in lines.
    stated = int(out.split("# ... ")[1].split(" further")[0])
    kept_lines = [line for line in body.splitlines() if line]
    assert stated == len(code.splitlines()) - len(kept_lines), out[-140:]

    # A file with no newline at all still truncates without crashing.
    assert _excerpt("x" * 500, 100).startswith("x")


def test_campaign_deactivate_does_not_clobber_another_campaign(tmp_path):
    """Clearing the pointer must be a compare-and-swap, not a blind write.

    Two campaigns sharing a campaign directory ran concurrently during our own
    experiments: the second activated its job, then the first finished and cleared the
    pointer, stranding the second's worker pool with nothing to poll. A reduction sat
    waiting on a rank that could no longer find its work.

    That is a lost update on a shared cell with two writers and no synchronisation --
    the bug AgentMPI's own `Window.compare_and_swap` exists to prevent, committed in
    the harness that runs the experiments.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
    from campaign import Campaign  # noqa: PLC0415 - path set above

    shared = tmp_path / "camp"
    first = Campaign(shared)
    second = Campaign(shared)

    first.activate(tmp_path / "job-a")
    assert (shared / "active").read_text().endswith("job-a")

    # The second campaign takes over the pointer.
    second.activate(tmp_path / "job-b")
    assert (shared / "active").read_text().endswith("job-b")

    # The first campaign finishing must not strand the second.
    first.deactivate()
    assert (shared / "active").read_text().endswith("job-b"), "first campaign clobbered the second"

    # The owner may still clear its own pointer.
    second.deactivate()
    assert (shared / "active").read_text().strip() == ""

    # A campaign that never activated anything clears unconditionally, which keeps
    # `--stop`-style cleanup working.
    third = Campaign(shared)
    (shared / "active").write_text("/somewhere", encoding="utf-8")
    third.deactivate()
    assert (shared / "active").read_text().strip() == ""


def test_campaign_recipes_contain_the_named_steps():
    """The `--only` filter matches on step name, so the names are an interface.

    A campaign invoked with `--only` that matches nothing runs zero steps and exits
    successfully, which is indistinguishable from a completed run in the log. That
    happened: a recipe edit silently failed to apply, the queued ablation selected two
    steps that did not exist, and the campaign reported "0 steps -> []" and declared
    itself complete. Pinning the names here makes the mismatch a test failure instead
    of a missing experiment.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
    from campaign import microbench_steps, software_steps, translation_steps  # noqa: PLC0415

    sw = {s.name for s in software_steps(ranks=8, prefix="x", rounds=2)}
    assert {"p8-full", "p8-noshared", "p8-vague-shared", "p8-vague-noshared", "p1-full"} <= sw, sw

    tr = {s.name for s in translation_steps(ranks=8, words=600, prefix="x")}
    assert {"p8-full", "p8-noglossary", "p8-nohalo", "p1-full"} <= tr, tr

    mb = {s.name for s in microbench_steps(ranks=8, prefix="x")}
    assert {"fidelity", "collectives", "faults", "pingpong"} <= mb, mb

    # Every step must carry a distinct output root, or one run overwrites another.
    for steps in (software_steps(ranks=8, prefix="x", rounds=2), translation_steps(ranks=8, words=600, prefix="x")):
        roots = [str(s.root) for s in steps]
        assert len(roots) == len(set(roots)), [r for r in roots if roots.count(r) > 1]


def test_tokens_command_lets_a_rank_measure_its_own_budget(tmp_path, capsys):
    """A budget the producer cannot measure is a budget it must guess at.

    Two ranks reported guessing low: one submitted 25 of 50 items where the budget
    allowed more, and one reverse-engineered the counter from reported prompt sizes to
    fit 34 instead of 27. The runtime had the counter all along and did not expose it,
    so the measured retention was a lower bound on what the budget permitted rather than
    a measurement of the operator's judgement.
    """
    payload = {"findings": [f"[F-0-{i}] serial {100000 + i} checksum ABCD" for i in range(20)]}
    path = tmp_path / "cand.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["tokens", "--file", str(path), "--json", "--budget", "10000"]) == 0
    ok = json.loads(capsys.readouterr().out)
    assert ok["fits"] is True and ok["headroom"] == 10000 - ok["tokens"]

    # Exceeding the budget is a non-zero exit, so a worker can branch on it in a shell.
    assert main(["tokens", "--file", str(path), "--json", "--budget", "5"]) == 1
    bad = json.loads(capsys.readouterr().out)
    assert bad["fits"] is False and bad["headroom"] < 0

    # And the count must agree with what a Contract would enforce, or the rank and the
    # checker disagree and the exercise is pointless.
    contract = ampi.Contract(name="X", kind="json", required=("findings",), max_tokens=ok["tokens"])
    assert contract.check(payload) == []
    tighter = ampi.Contract(name="X", kind="json", required=("findings",), max_tokens=ok["tokens"] - 1)
    assert tighter.check(payload), "the CLI count and the contract check must be the same measure"


def test_task_json_offers_a_size_check_when_a_budget_exists(tmp_path, capsys):
    """The worker is handed the exact command that evaluates its candidate."""
    root = tmp_path / "job"
    assert main(["--root", str(root), "init", "--size", "1"]) == 0
    capsys.readouterr()

    fabric = ampi.Fabric(root)
    blob = fabric.blobs.put("do the thing")
    bounded = ampi.Contract(name="Bounded", kind="json", required=("x",), max_tokens=450)
    import time

    with fabric.write() as cur:
        cur.execute(
            "INSERT INTO agent_calls(rank, ctx, kind, label, state, prompt_digest, contract,"
            " prompt_tokens, created_at, incarnation, attempt) VALUES(0,0,'task','t','pending',?,?,?,?,1,1)",
            (blob.digest, json.dumps(bounded.to_json()), blob.tokens, time.time()),
        )
    assert main(["--root", str(root), "worker", "--rank", "0", "next", "--timeout", "2", "--poll", "0.1"]) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["check_size"] and "--budget 450" in task["check_size"]
    assert task["result_file"] in task["check_size"]

    # No budget on the contract means no size check to offer.
    with fabric.write() as cur:
        cur.execute(
            "INSERT INTO agent_calls(rank, ctx, kind, label, state, prompt_digest, prompt_tokens,"
            " created_at, incarnation, attempt) VALUES(0,0,'task','u','pending',?,?,?,1,1)",
            (blob.digest, blob.tokens, time.time()),
        )
    assert main(["--root", str(root), "worker", "--rank", "0", "next", "--timeout", "2", "--poll", "0.1"]) == 0
    task2 = json.loads(capsys.readouterr().out)
    assert task2["check_size"] is None
