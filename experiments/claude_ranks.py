"""Start Claude Code sessions as AgentMPI executors.

``launch.py`` renders the worker prompts and records the launch plan; it does not
start agents, because starting them is the agent host's business.  This file is
one such host: it starts one headless Claude Code session per executor in the
plan, on the machine it runs on, and records which session served which ranks.

What a session is given is exactly the rendered worker prompt and nothing else.
No protocol knowledge reaches the model: the session reads a task file, writes a
result file, and runs the submit command the runtime printed for it.  Everything
the harness decides --- which collective, which algorithm, what happens when a
peer dies --- stays in the harness, which is the point of the design.

Concurrency is a host property, not a protocol one.  A session is a Node process
of roughly 160 MB that spends nearly all of its life blocked on the API or on
``ampi worker next``, so a 4-vCPU, 16 GB sandbox runs thirty-two of them at once
without strain (measured: 32/32 succeeded, median 7.8 s each, 5.2 GB resident).
The binding limit is the account's API rate limit, which ``--concurrency`` caps;
ranks beyond it are served by oversubscription, exactly as ``mpirun -np 100`` on
eight cores runs a hundred ranks.

Usage, alongside a broker-executor harness that owns the job::

    python experiments/e1_translate/harness.py --name demo --size 8 --executor broker &
    python experiments/claude_ranks.py --name demo --size 8 --executors 4

The launcher waits for the job root to appear before starting any session, so the
two commands can be started in either order.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from launch import RUNS, plan_run  # noqa: E402

HOST = "claude-code-cli"

# The tools a rank needs and no more: a shell for `ampi`, and file tools because
# the worker prompt insists results are written with a file-writing tool, not
# echoed through shell quoting.
DEFAULT_TOOLS = "Bash,Read,Write,Edit"


def find_ampi() -> str:
    found = shutil.which("ampi")
    if found:
        return found
    raise SystemExit("no `ampi` on PATH: run `pip install -e .` first; the runtime's "
                     "printed submit commands call it by name")


def wait_for_job(job_root: Path, ampi: str, timeout: float) -> None:
    """Block until the harness has created the job.

    A worker whose first `next` fails with AMPI_ERR_NO_JOB tends to stop, as the
    prompt tells it to stop on errors it does not understand.  Waiting here costs
    nothing and removes a startup-order dependency between two processes that
    have no other reason to know about each other.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job_root.exists():
            r = subprocess.run([ampi, "--job-root", str(job_root), "info"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                return
        time.sleep(1.0)
    raise SystemExit(f"job root {job_root} did not appear within {timeout:.0f}s; "
                     "is the harness running with --executor broker?")


def run_executor(
    entry: dict[str, Any],
    *,
    run_dir: Path,
    model: str | None,
    tools: str,
    max_turns: int,
    effort: str | None,
    extra: list[str],
    sem: threading.Semaphore,
    record: dict[str, Any],
    lock: threading.Lock,
) -> None:
    n = entry["executor"]
    worker_id = f"{HOST}:exec{n}"
    prompt = Path(entry["prompt"]).read_text(encoding="utf-8")
    exec_dir = run_dir / "executors" / f"exec{n}"
    exec_dir.mkdir(parents=True, exist_ok=True)
    stream = exec_dir / "stream.jsonl"
    errlog = exec_dir / "stderr.log"

    # One fresh session id per executor.  Left to the CLI, a session started from
    # inside another Claude Code session inherits that session's id, and two
    # executors reporting the same id is exactly the provenance failure the
    # record exists to rule out.
    session_id = str(uuid.uuid4())
    cmd = [
        "claude", "-p", prompt,
        "--session-id", session_id,
        "--output-format", "stream-json", "--verbose",
        "--allowedTools", tools,
        "--add-dir", str(run_dir),
    ]
    if model:
        cmd += ["--model", model]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if effort:
        cmd += ["--effort", effort]
    cmd += extra

    env = dict(os.environ)
    # Provenance: the broker stamps this onto every task the session claims, so
    # the sealed journal says which session did which piece of work.
    env["AMPI_WORKER_ID"] = worker_id
    # A rank must never inherit ambient identity from the shell that launched it.
    env.pop("AMPI_RANK", None)
    env.pop("AMPI_ROOT", None)

    with sem:
        started = time.time()
        with open(stream, "w", encoding="utf-8") as out, open(errlog, "w") as err:
            proc = subprocess.Popen(cmd, cwd=exec_dir, env=env, stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, text=True)
            with lock:
                record[n] = {"executor": n, "worker_id": worker_id, "serves": entry["serves"],
                             "session_id": session_id, "pid": proc.pid, "started_at": started,
                             "state": "running", "stream": str(stream)}
            rc = proc.wait()
    result, cost, turns = None, None, None
    for line in stream.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev.get("result")
            cost = ev.get("total_cost_usd")
            turns = ev.get("num_turns")
    with lock:
        record[n].update(
            state="exited", exit_code=rc, finished_at=time.time(),
            wall_s=round(time.time() - started, 1),
            num_turns=turns, cost_usd=cost, final_report=result,
        )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(description="start Claude Code sessions as AgentMPI executors")
    ap.add_argument("--name", required=True, help="run name; must match the harness's --name")
    ap.add_argument("--size", type=int, required=True, help="number of ranks")
    ap.add_argument("--executors", type=int, default=0,
                    help="sessions to start; below --size means oversubscription")
    ap.add_argument("--concurrency", type=int, default=0,
                    help="sessions alive at once (default: all executors)")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--max-tasks", type=int, default=4)
    ap.add_argument("--model", default=None, help="claude --model; default is the CLI's")
    ap.add_argument("--effort", default=None, help="claude --effort")
    ap.add_argument("--max-turns", type=int, default=0, help="claude --max-turns; 0 = unlimited")
    ap.add_argument("--tools", default=DEFAULT_TOOLS, help="claude --allowedTools")
    ap.add_argument("--wait", type=float, default=600.0,
                    help="seconds to wait for the harness to create the job")
    ap.add_argument("--dry-run", action="store_true", help="render prompts, start nothing")
    ap.add_argument("claude_args", nargs="*", help="extra arguments after `--` go to claude")
    a = ap.parse_args(argv)

    ampi = find_ampi()
    plan = plan_run(name=a.name, size=a.size, campaign=a.campaign, max_tasks=a.max_tasks,
                    executors=a.executors, ampi=ampi)
    run_dir = RUNS / a.name
    job_root = Path(plan["job_root"])
    n_exec = plan["executors"]
    concurrency = a.concurrency or n_exec

    record: dict[str, Any] = {}
    lock = threading.Lock()
    manifest = {
        "note": "Claude Code session identifiers for every executor launched against this "
                "run, recorded at launch time. The set requested is therefore independent of "
                "the set that answered; the broker's per-task worker_id says which session "
                "actually did each piece of work.",
        "host": HOST,
        "host_constraint": (
            f"One sandbox VM ({os.cpu_count()} vCPU) running headless `claude -p` sessions as "
            f"OS processes; {concurrency} alive at once, serving {a.size} ranks over {n_exec} "
            "executors by oversubscription."
        ),
        "claude_version": subprocess.run(["claude", "--version"], capture_output=True,
                                         text=True).stdout.strip(),
        "model": a.model or "cli-default",
        "tools": a.tools,
        "plan": plan,
        "executors": record,
    }
    out_path = run_dir / "executors.json"

    def flush() -> None:
        with lock:
            snapshot = dict(manifest, executors=[record[k] for k in sorted(record)])
        out_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")

    if a.dry_run:
        manifest["executors"] = []
        print(json.dumps({k: manifest[k] for k in ("host", "host_constraint", "plan")}, indent=2))
        return manifest

    wait_for_job(job_root, ampi, a.wait)

    sem = threading.Semaphore(concurrency)
    threads = [
        threading.Thread(
            target=run_executor, args=(entry,),
            kwargs=dict(run_dir=run_dir, model=a.model, tools=a.tools, max_turns=a.max_turns,
                        effort=a.effort, extra=a.claude_args, sem=sem, record=record, lock=lock),
            daemon=True,
        )
        for entry in plan["assignment"]
    ]
    for t in threads:
        t.start()
    print(f"started {len(threads)} Claude Code executor(s), {concurrency} concurrent, "
          f"for {a.size} rank(s); log under {run_dir / 'executors'}", flush=True)
    while any(t.is_alive() for t in threads):
        flush()
        time.sleep(5)
    flush()
    exited = [r for r in record.values() if r.get("state") == "exited"]
    bad = [r for r in exited if r.get("exit_code")]
    print(json.dumps({
        "executors": len(exited),
        "nonzero_exit": [r["executor"] for r in bad],
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in exited), 4),
        "record": str(out_path),
    }, indent=2))
    return manifest


if __name__ == "__main__":
    main()
