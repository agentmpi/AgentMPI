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
