"""E6 command line: create a job, be one rank, collect the evidence.

Subcommands, in the order a cloud run uses them:

``create``          make the job on the shared git branch and write the launch
                    plan.  Run once, anywhere with push access.
``session-prompt``  print the instruction a Claude Code cloud session is given
                    to be one rank.  The launcher creates one session per rank
                    with exactly this text.
``run``             be one rank on this machine: join the job over the git
                    device, open a machine-local task queue, and execute the
                    rank program.  The session serving the queue is the agent.
``status``          what every rank is doing, from the shared branch.
``kill``            fault injection: convict a rank administratively.
``collect``         read the branch back and produce the evidence: the trace,
                    the assembled book, the glossary, a report, the analysis.

And one for a machine that has all the ranks:

``local``           run every rank in one process against SQLite, with the stub
                    or with headless ``claude -p`` sessions as executors.  How
                    the harness is debugged and how the prompts were validated.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
WORK = ROOT / "work" / "e6"
DEFAULT_REMOTE = "https://github.com/agentmpi/AgentMPI"
DEFAULT_CODE_BRANCH = "claude/production_exp"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent))

from e6_book import corpus as corpus_mod  # noqa: E402
from e6_book.executors import ClaudeCliExecutor, stub_executor  # noqa: E402
from e6_book.harness import (  # noqa: E402
    PAGES_WIN,
    REGISTRY_WIN,
    RESEARCH_WIN,
    BookHarness,
    Config,
    assemble_cells,
    run_one,
)


def identity(rank: int) -> dict[str, Any]:
    boot = Path("/proc/sys/kernel/random/boot_id")
    return {
        "hostname": socket.gethostname(),
        "boot_id": boot.read_text().strip() if boot.exists() else None,
        "kernel": platform.release(),
        "pid": os.getpid(),
        "session": os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID"),
        "container": os.environ.get("CLAUDE_CODE_CONTAINER_ID"),
        "cpus": os.cpu_count(),
    }


def _git_env(a: argparse.Namespace) -> None:
    os.environ["AMPI_DEVICE"] = "git"
    if getattr(a, "remote", None):
        os.environ["AMPI_GIT_REMOTE"] = a.remote
    os.environ["AMPI_GIT_BRANCH"] = getattr(a, "branch", None) or f"ampi-jobs/{a.name}"
    if getattr(a, "read_interval", None):
        os.environ["AMPI_GIT_READ_INTERVAL"] = str(a.read_interval)


def _config(a: argparse.Namespace) -> Config:
    cfg = Config(name=a.name, size=a.size)
    for k in cfg.__dataclass_fields__:
        v = getattr(a, k, None)
        if v is not None and k not in ("name", "size"):
            setattr(cfg, k, v)
    if isinstance(cfg.languages, str):
        cfg.languages = [c.strip() for c in cfg.languages.split(",") if c.strip()]
    return cfg


def _job_root(a: argparse.Namespace, rank: int | None = None) -> Path:
    """Where this machine's copy of the shared job lives.

    On the git device a job root is a clone, and a machine that holds several
    ranks needs one clone per rank: each rank is its own writer with its own
    lease and its own view of the branch, exactly as it would be on its own
    machine.
    """
    base = Path(a.root) if getattr(a, "root", None) else WORK / a.name / "job"
    return base if rank is None else base / f"r{rank}"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def cmd_create(a: argparse.Namespace) -> dict[str, Any]:
    _git_env(a)
    from ampi.runtime import Ampi

    cfg = _config(a)
    corpus = corpus_mod.build(WORK, cfg.size, corpus=cfg.corpus, legacy_dir=a.legacy_dir, pages=cfg.pages)
    root = _job_root(a)
    job = Ampi.create(
        str(root), cfg.size, device=a.device, force=True, join_deadline_s=a.join_deadline,
        ctx_budget=cfg.ctx_budget,
        meta={"experiment": "e6_book", "e6": cfg.to_dict(), "remote": a.remote,
              "branch": os.environ.get("AMPI_GIT_BRANCH"), "source_commit": corpus.origin_commit},
    )
    run_dir = RUNS / a.name
    run_dir.mkdir(parents=True, exist_ok=True)
    corpus_mod.write_manifest(corpus, run_dir / "corpus_manifest.json")
    plan = {
        "name": a.name, "job": job.manifest.job_id, "size": cfg.size, "device": a.device,
        "remote": a.remote, "branch": os.environ.get("AMPI_GIT_BRANCH"),
        "config": cfg.to_dict(), "requested_ranks": list(range(cfg.size)),
        "segments": [list(s) for s in corpus.segments],
        "created_at": time.time(), "created_on": identity(-1),
    }
    (run_dir / "launch_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({k: plan[k] for k in ("name", "job", "size", "branch")}, indent=2))
    return plan


# ---------------------------------------------------------------------------
# session-prompt
# ---------------------------------------------------------------------------


SESSION_PROMPT = """\
You are one machine of a distributed book-translation job named "{name}" with {size} ranks. This machine's rank harness was started automatically when this session began (a SessionStart hook runs it, and restarts it if the session is resumed after a pause): it claimed one or more rank numbers, joined the job, and does every bit of coordination with the other machines (assignment, shared terminology, research claims, review, assembly, failure handling). You do the language work it hands you, one task at a time, and nothing else. Do not read the repository's code, do not investigate the protocol, do not try to contact other machines, do not translate anything you were not handed, and do not start or restart the harness yourself.

SETUP, once:

1. Find your ranks. Run: cat /home/user/AgentMPI/work/e6/{name}/slot.json
   It contains a JSON object whose "ranks" field lists the rank numbers this machine holds. If the file does not exist yet, wait 30 seconds and try again, for up to ten minutes. If after ten minutes it still does not exist, or "ranks" is empty, run: tail -n 40 /home/user/AgentMPI/work/e6/{name}/autostart.log ; report that output verbatim and stop.
2. Check the harness joined the job: for each rank number R in that list, run tail -n 2 /home/user/AgentMPI/work/e6/{name}/rankR.log

WORK LOOP. Repeat until the runtime tells you to exit:

1. Ask for work. Run this exact command, giving the Bash tool a timeout of 600000 milliseconds (ten minutes; the command blocks server-side for up to 540 seconds, and asking less often is what keeps the run within the account's budget):

   bash /home/user/AgentMPI/work/e6/{name}/next.sh

   It prints one JSON object. Read its "status":
     - "task": go to step 2.
     - "idle": nothing was available during the wait. Ask again. Long idle stretches are normal: the harness is waiting for other machines. Only if you have been idle for more than 90 minutes in a row, check whether the harness has finished: the file /home/user/AgentMPI/work/e6/{name}/done.json exists once it has. If it exists, print it and stop.
     - "exit": the job is over. Go to WHEN YOU FINISH.
   If you ever see "error": "AMPI_ERR_IDENTITY", stop and report it verbatim.

2. Do the task with a fresh subagent, so that each task starts with an empty context. Use the Agent tool (general-purpose) with this instruction, substituting the values from the task JSON:

   "Read the file <prompt_file> and do exactly what it says. Write your answer to <result_file> using the Write tool: only the JSON object the task asks for, with no prose before it and no markdown fence around it. If the task asks you to research, use WebSearch and WebFetch and cite the URLs you used. Never invent content to fill a requirement you cannot meet. <If the task JSON has a non-empty check_size: 'Then run this command and, if it exits non-zero, shorten the file until it passes: <check_size>'> Reply with the single word DONE when the file is written."

3. Submit by running the exact command in the task's "submit" field. Confirm the output says "status": "done". If it reports AMPI_ERR_TYPE, read its "violations" list, hand the violations and the two file paths to a new subagent to fix the result file, and submit again; give up after three rejections by running the exact "give_up" command with a one-line reason. If it reports that there is no such task (AMPI_ERR_ARG), the harness was restarted while you worked: discard the task and go back to step 1.

4. Return to step 1.

EFFICIENCY. One task is: one shell call to get it, one subagent, one shell call to submit. Do not read the prompt or result files yourself, do not poll in a tight loop, do not explore the repository.

IF YOU ARE RESUMED after a pause with a message telling you to continue, simply return to step 1 of the work loop.

WHEN YOU FINISH. After "exit", wait for the harness process to end: check once a minute, for up to 15 minutes, whether /home/user/AgentMPI/work/e6/{name}/done.json exists. Then print it, and report how many tasks you completed with their "label" values and anything that went wrong.
"""


def session_prompt(name: str, rank: int, size: int, remote: str, code_branch: str) -> str:
    return SESSION_PROMPT.format(name=name, size=size)


BOOTSTRAP_PROMPT = """\
You are one machine of the book-translation job "{name}" ({size} ranks). Your full instruction is printed by a command in the repository this session was started in; get it and follow it exactly, and do nothing else:

python3 experiments/e6_book/rank.py session-prompt --name {name} --size {size}

Read the printed instruction in full before acting. It tells you how to find the rank this machine claimed, how to fetch each task, how to do it with a fresh subagent, and how to submit it.
"""


def bootstrap_prompt(name: str, rank: int, size: int, remote: str, code_branch: str) -> str:
    return BOOTSTRAP_PROMPT.format(name=name, size=size)


def cmd_session_prompt(a: argparse.Namespace) -> str:
    fn = bootstrap_prompt if a.bootstrap else session_prompt
    text = fn(a.name, a.rank, a.size, a.remote, a.code_branch)
    print(text)
    return text


# ---------------------------------------------------------------------------
# run: one rank, this machine
# ---------------------------------------------------------------------------


def _open_local_queue(a: argparse.Namespace, size: int, *, fresh: bool) -> Any:
    """The machine-local task queue every rank on this machine publishes to.

    One SQLite job for the whole machine: the session serving it claims tasks for
    every rank the machine holds (``ampi worker --serve``), so K ranks cost one
    session, not K.  ``fresh`` wipes it; a restart keeps it so that a task the
    session is in the middle of can still be submitted.
    """
    from ampi.runtime import Ampi

    local_root = WORK / a.name / "local"
    if fresh or not (local_root / "job.json").exists():
        return Ampi.create(str(local_root), size, device="sqlite", force=True,
                           meta={"role": "local-broker", "job": a.name})
    return Ampi(str(local_root), allow_volatile=True)


def run_rank(a: argparse.Namespace, rank: int, local: Any | None, work: Path,
             cfg_override: Config | None = None) -> dict[str, Any]:
    """Be one rank: join the shared job, execute the rank program, leave a record.

    Called once per rank on this machine, in its own thread when the machine
    holds several.  The rank's own git clone, lease, executor and log are all
    per rank; only the local task queue is shared.
    """
    from ampi.executor import BrokerExecutor
    from ampi.runtime import Ampi

    root = _job_root(a, rank)
    log = {"rank": rank, "name": a.name, "started_at": time.time(), "identity": identity(rank)}
    out_log = open(work / f"rank{rank}.log", "a", encoding="utf-8")  # noqa: SIM115

    def say(obj: dict[str, Any]) -> None:
        line = json.dumps(obj, default=str)
        out_log.write(line + "\n")
        out_log.flush()
        print(line, flush=True)

    say({"joining": str(root), "rank": rank, "branch": os.environ["AMPI_GIT_BRANCH"]})
    amp = Ampi(str(root), rank=rank, expect_rank=rank)
    meta = amp.manifest.meta or {}
    cfg = cfg_override or Config.from_dict(meta.get("e6") or {"name": a.name, "size": amp.size})
    say({"joined": amp.manifest.job_id, "size": amp.size, "config": cfg.to_dict()})
    corpus = corpus_mod.build(WORK, cfg.size, corpus=cfg.corpus, legacy_dir=a.legacy_dir, pages=cfg.pages)

    broker_dir = work / "broker"
    executor: Any
    if a.executor == "broker":
        # The broker mirrors its events into the shared trace so the analysis
        # sees the work spans in the one log that survives the machine.
        assert local is not None
        executor = BrokerExecutor(
            local, campaign=a.name, work_dir=broker_dir, timeout_s=cfg.task_timeout_s,
            claim_ttl_s=cfg.claim_ttl_s, claim_wait_s=cfg.claim_wait_s, trace_to=amp,
            keepalive=lambda: amp.heartbeat(extend=cfg.lease_s),
            keepalive_every_s=a.keepalive or cfg.lease_s / 3,
        )
        executor.open()
    elif a.executor == "claude":
        executor = ClaudeCliExecutor(broker_dir, model=a.model, timeout_s=cfg.task_timeout_s,
                                     worker_id=f"claude-cli:{a.name}:r{rank}", effort=a.effort)
    else:
        executor = stub_executor(corpus, cfg.languages, latency_s=a.stub_latency)
        if getattr(a, "stub_die_after", None):
            # Fault injection for the restart path: the machine "pauses" (the
            # process ends without a word) after this many tasks, and a later
            # ``--resume`` must pick the rank up where it stopped.
            executor = _dying(executor, int(a.stub_die_after), say)

    harness = BookHarness(cfg, corpus, executor, work)
    out = run_one(amp, rank, harness, lease_s=cfg.lease_s, identity=log["identity"])
    log.update(out, finished_at=time.time(), device=amp.device.stats())
    if hasattr(executor, "stats"):
        log["executor"] = executor.stats()
    (work / f"rank{rank}.json").write_text(json.dumps(log, indent=2, default=str),
                                            encoding="utf-8")
    say({k: log.get(k) for k in ("rank", "ok", "error", "seconds", "device")})
    amp.close()
    out_log.close()
    return log


def _dying(executor: Any, after: int, say: Any) -> Any:
    from ampi.executor import FunctionExecutor

    count = {"n": 0}

    def fn(task: Any) -> Any:
        count["n"] += 1
        if count["n"] > after:
            say({"paused": True, "after_tasks": after, "at": task.label})
            os._exit(0)
        return executor.invoke(task)

    return FunctionExecutor(fn)


def run_ranks(a: argparse.Namespace, ranks: list[int], *, fresh: bool) -> dict[str, Any]:
    """Run this machine's ranks, each in a thread, over one local queue.

    The campaign closes only when every rank on the machine is finished, so the
    session serving the queue is told to exit exactly once.  A rank that fails
    is a result, not a crash: the other ranks on the machine carry on, as they
    would if it had been on another machine.
    """
    import threading

    from ampi.runtime import Ampi

    work = WORK / a.name
    work.mkdir(parents=True, exist_ok=True)
    probe = Ampi(str(_job_root(a, ranks[0])), allow_volatile=True)
    size = probe.size
    probe.close()
    local = _open_local_queue(a, size, fresh=fresh) if a.executor == "broker" else None
    results: dict[int, dict[str, Any]] = {}

    def body(r: int) -> None:
        try:
            results[r] = run_rank(a, r, local, work)
        except Exception as exc:  # noqa: BLE001 - recorded, the other ranks continue
            results[r] = {"rank": r, "ok": False, "error": str(exc)[:300],
                          "error_class": type(exc).__name__}
            (work / f"rank{r}.json").write_text(json.dumps(results[r], indent=2), encoding="utf-8")
            with open(work / f"rank{r}.log", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(results[r]) + "\n")

    threads = [threading.Thread(target=body, args=(r,), name=f"rank{r}", daemon=True) for r in ranks]
    for t in threads:
        t.start()
        time.sleep(2.0)  # stagger the clones so they do not all fetch the same second
    for t in threads:
        t.join()
    if local is not None:
        from ampi.executor import BrokerExecutor

        if getattr(a, "leave_open", False):
            # Another harness process on this machine still serves ranks
            # through the same queue; closing the campaign would send its
            # worker away.
            print(json.dumps({"campaign": a.name, "left_open": True}), flush=True)
        else:
            try:
                BrokerExecutor(local, campaign=a.name, work_dir=work / "broker").close()
            except Exception as exc:  # noqa: BLE001 - closing is best effort
                print(json.dumps({"close_failed": str(exc)}), flush=True)
        with open(work / "local.trace.jsonl", "w", encoding="utf-8") as fh:
            for e in local.events():
                fh.write(json.dumps(e, default=str) + "\n")
        local.close()
    summary = {"name": a.name, "ranks": ranks, "ok": all(results.get(r, {}).get("ok") for r in ranks),
               "results": {str(r): {k: results.get(r, {}).get(k) for k in ("ok", "error", "seconds")}
                           for r in ranks}, "finished_at": time.time()}
    if all(_finished(work / f"rank{r}.json") for r in ranks):
        (work / "done.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, default=str), flush=True)
    return summary


def cmd_run(a: argparse.Namespace) -> dict[str, Any]:
    _git_env(a)
    ranks = [int(x) for x in str(a.rank).split(",") if x.strip()]
    return run_ranks(a, ranks, fresh=not getattr(a, "resume", False))


# ---------------------------------------------------------------------------
# autostart: a machine claims a rank
# ---------------------------------------------------------------------------

LAUNCH_FILE = HERE / "LAUNCH.json"
ENV_LAUNCH_FILE = "E6_LAUNCH_FILE"


def launch_spec() -> dict[str, Any] | None:
    """The job this branch launches into, if any.

    A machine that starts a session on this branch reads this file and joins
    the job it names.  Committed beside the harness so that every machine sees
    the same job, and so that the launcher's whole act of launching is one push.
    """
    path = Path(os.environ.get(ENV_LAUNCH_FILE) or LAUNCH_FILE)
    if not path.exists():
        return None
    spec = json.loads(path.read_text(encoding="utf-8"))
    return spec if spec.get("enabled") else None


def claim_slot(root: Path, size: int, me: dict[str, Any]) -> int | None:
    """Take the lowest free rank, by compare-and-swap on a cell that does not exist.

    Ranks are claimed, not assigned.  MPI's process manager hands each process
    its rank; here the launcher cannot address a machine before it exists, so the
    machine takes the first free slot when it arrives.  The swap is on version 0
    of a cell in a private space, which succeeds exactly once per slot however
    many machines arrive at the same moment; the scan starts at a random offset
    so that sixteen simultaneous arrivals do not all lose fifteen swaps on slot 0.
    """
    from ampi.runtime import Ampi

    amp = Ampi(str(root), allow_volatile=True)
    try:
        start = int.from_bytes(os.urandom(2), "big") % max(1, size)
        for i in range(size):
            r = (start + i) % size
            ok, _cell = amp.device.cas("launch", f"slot/{r}", 0, me, writer=-1)
            if ok:
                amp.trace("launch.claim", rank=r, session=me.get("session"),
                          boot_id=me.get("boot_id"))
                return r
        return None
    finally:
        amp.close()


def write_next_script(work: Path, name: str, ranks: list[int]) -> Path:
    """The one command the session runs to ask for work, written out verbatim.

    The session substitutes nothing: every value an agent might mistype (the
    rank, the ranks it serves, the queue's path, the identity assertion) is in
    the file the harness wrote.
    """
    others = ",".join(str(r) for r in ranks[1:])
    serve = f" --serve {others}" if others else ""
    script = (
        "#!/bin/bash\n"
        f"# Written by the rank harness of job {name}: claim the next task for ranks {ranks}.\n"
        f'exec env AMPI_WORKER_ID="cloud:${{CLAUDE_CODE_REMOTE_SESSION_ID:-$(hostname)}}" '
        f"ampi worker --job-root {WORK / name / 'local'} --rank {ranks[0]} --expect-rank {ranks[0]} "
        f"--campaign {name}{serve} next --timeout 540\n"
    )
    path = work / "next.sh"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def _same_session(a: str | None, b: str | None) -> bool:
    """Session ids appear as ``session_<id>`` in the API and ``cse_<id>`` in the
    environment; compare the id."""
    if not a or not b:
        return False
    return a.split("_", 1)[-1] == b.split("_", 1)[-1]


def _finished(marker: Path) -> bool:
    """A rank is finished when it finalised, or when it was fenced or killed.

    A rank whose thread died of anything else (a lapsed lock lease, a lost
    executor, a transport error) is not finished: it is a rank to resume, and
    the runtime lets it re-initialise and replay.
    """
    if not marker.exists():
        return False
    try:
        rec = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(rec.get("ok")) or rec.get("error_class") == "AMPI_ERR_FENCED"


def cmd_autostart(a: argparse.Namespace) -> dict[str, Any] | None:
    spec = launch_spec()
    if spec is None:
        print(json.dumps({"autostart": "no enabled launch spec"}), flush=True)
        return None
    me = identity(-1)
    if spec.get("launcher_session") and _same_session(me.get("session"), spec["launcher_session"]):
        # The conversation that created the job also starts sessions on this
        # branch; it must not become a rank of its own experiment.
        print(json.dumps({"autostart": "this is the launcher's own session"}), flush=True)
        return None
    name = spec["name"]
    work = WORK / name
    work.mkdir(parents=True, exist_ok=True)
    a.name = name
    a.remote = spec.get("remote", DEFAULT_REMOTE)
    a.branch = spec.get("branch")
    a.root = None
    a.read_interval = spec.get("read_interval")
    a.executor = spec.get("executor", "broker")
    a.model = spec.get("model")
    a.effort = spec.get("effort")
    a.keepalive = float(spec.get("keepalive", 60.0))
    a.stub_latency = 0.0
    a.legacy_dir = None
    _git_env(a)

    slot_file = work / "slot.json"
    if getattr(a, "resume", False):
        if not slot_file.exists():
            print(json.dumps({"autostart": "nothing to resume"}), flush=True)
            return None
        slot = json.loads(slot_file.read_text(encoding="utf-8"))
        ranks = [r for r in slot.get("ranks") or [] if not _finished(work / f"rank{r}.json")]
        if not ranks:
            print(json.dumps({"autostart": "every rank on this machine is finished"}), flush=True)
            return None
        print(json.dumps({"autostart": "resuming", "ranks": ranks}), flush=True)
        return run_ranks(a, ranks, fresh=False)

    from ampi.runtime import Ampi

    root = _job_root(a)
    me["claimed_at"] = time.time()
    try:
        probe = Ampi(str(root), allow_volatile=True)
        size = probe.size
        probe.close()
    except Exception as exc:  # noqa: BLE001 - the job may not exist yet
        print(json.dumps({"autostart": "no job", "error": str(exc)[:200]}), flush=True)
        return None
    want = max(1, int(spec.get("ranks_per_machine", 1)))
    ranks: list[int] = []
    for _ in range(want):
        r = claim_slot(root, size, me)
        if r is None:
            break
        ranks.append(r)
    ranks.sort()
    if not ranks:
        print(json.dumps({"autostart": "every slot is taken", "size": size}), flush=True)
        slot_file.write_text(json.dumps({"rank": None, "ranks": [], "name": name, "size": size,
                                         "note": "every slot is taken"}), encoding="utf-8")
        return None
    write_next_script(work, name, ranks)
    slot_file.write_text(json.dumps({"rank": ranks[0], "ranks": ranks, "name": name, "size": size,
                                     "session": me.get("session")}), encoding="utf-8")
    print(json.dumps({"autostart": "claimed", "ranks": ranks, "size": size}), flush=True)
    return run_ranks(a, ranks, fresh=True)


def cmd_slot(a: argparse.Namespace) -> dict[str, Any]:
    p = WORK / a.name / "slot.json"
    out = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"rank": None, "name": a.name}
    print(json.dumps(out))
    return out


# ---------------------------------------------------------------------------
# local: every rank in one process
# ---------------------------------------------------------------------------


def cmd_local(a: argparse.Namespace) -> dict[str, Any]:
    from ampi.harness import Harness

    cfg = _config(a)
    work = WORK / a.name
    run_dir = RUNS / a.name
    for d in (work, run_dir):
        d.mkdir(parents=True, exist_ok=True)
    corpus = corpus_mod.build(WORK, cfg.size, corpus=cfg.corpus, legacy_dir=a.legacy_dir, pages=cfg.pages)
    corpus_mod.write_manifest(corpus, run_dir / "corpus_manifest.json")
    h = Harness(root=str(work / "job"), size=cfg.size, device=a.device, ctx_budget=cfg.ctx_budget,
                force=True, meta={"experiment": "e6_book", "e6": cfg.to_dict(), "executor": a.executor})
    job = h.create()
    if a.executor == "claude":
        executor: Any = ClaudeCliExecutor(work / "broker", model=a.model,
                                          timeout_s=cfg.task_timeout_s, effort=a.effort)
    else:
        executor = stub_executor(corpus, cfg.languages, latency_s=a.stub_latency)
    harness = BookHarness(cfg, corpus, executor, work)
    plan = {"name": a.name, "job": job.manifest.job_id, "size": cfg.size, "device": a.device,
            "executor": a.executor, "config": cfg.to_dict(),
            "segments": [list(s) for s in corpus.segments], "created_at": time.time()}
    (run_dir / "launch_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    if a.kill_rank is not None:
        # Fault injection for the local series: convict one rank after a delay,
        # from outside the population, as a machine loss would.
        import threading

        def assassin() -> None:
            time.sleep(a.kill_after)
            victim = h.attach(a.kill_rank)
            try:
                victim.kill(a.kill_rank, reason="injected")
            finally:
                victim.close()

        threading.Thread(target=assassin, daemon=True).start()

    started = time.time()
    results = h.run(harness.rank_main, timeout=cfg.phase_timeout_s * 4)
    report = h.report(results)
    report.update(experiment="e6_book", name=a.name, executor=a.executor,
                  wall_s=round(time.time() - started, 2), config=cfg.to_dict())
    if hasattr(executor, "stats"):
        report["executor_stats"] = executor.stats()
    h.save(results, run_dir / "harness.json")
    root = h.attach(0)
    try:
        cells = {}
        space = root._space(PAGES_WIN)
        for c in root.device.keys(space):
            cell = root.device.read(space, c.key)
            if cell is not None:
                cells[c.key] = cell.value
        book = assemble_cells(cells, run_dir / "out", corpus)
        report["book"] = book
        _write_glossary(root, run_dir)
    finally:
        root.close()
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("succeeded", "failed", "wall_s", "book")},
                     indent=2, default=str))
    return report


def _write_glossary(amp: Any, run_dir: Path) -> None:
    space = amp._space(RESEARCH_WIN)
    findings = {}
    for c in amp.device.keys(space, prefix="finding/"):
        cell = amp.device.read(space, c.key)
        if cell is not None and isinstance(cell.value, dict):
            findings[cell.value.get("term", c.key)] = cell.value
    reg_space = amp._space(REGISTRY_WIN)
    reg = amp.device.read(reg_space, "registry")
    (run_dir / "glossary.json").write_text(
        json.dumps({"findings": findings, "registry": reg.value if reg else None},
                   indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# status, kill, collect: from the branch
# ---------------------------------------------------------------------------


def _state(a: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    _git_env(a)
    from ampi.device.gitlog import GitDevice

    dev = GitDevice(str(_job_root(a)))
    dev.initialize()
    return dev, dev._sync()


def cmd_status(a: argparse.Namespace) -> dict[str, Any]:
    _dev, state = _state(a)
    now = time.time()
    ranks = {}
    for k, v in state["cells"].items():
        if k.startswith("rank\t"):
            view = v[-1]["value"]
            ranks[int(k.split("\t")[1])] = view
    events = state["streams"].get("event", [])
    last_memo: dict[int, str] = {}
    last_ts: dict[int, float] = {}
    for e in events:
        if e.get("kind") == "memo" and e.get("key") == "phase":
            last_memo[e["rank"]] = e.get("note", "")
        last_ts[e.get("rank", -1)] = max(last_ts.get(e.get("rank", -1), 0), e.get("ts", 0))
    pages = [k for k in state["cells"] if "\tpage/" in k]
    out = {
        "ranks": {r: {"state": v["state"], "epoch": v["epoch"], "phase": last_memo.get(r, "-"),
                      "lease_in_s": round(v["lease_until"] - now), "silent_s": round(now - last_ts.get(r, now))}
                  for r, v in sorted(ranks.items())},
        "states": dict(Counter(v["state"] for v in ranks.values())),
        "phases": dict(Counter(last_memo.values())),
        "pages_done": len(pages),
        "events": len(events),
        "last_event_age_s": round(now - max((e.get("ts", 0) for e in events), default=now)),
    }
    if a.brief:
        out.pop("ranks")
    print(json.dumps(out, indent=2))
    return out


def cmd_kill(a: argparse.Namespace) -> dict[str, Any]:
    _git_env(a)
    from ampi.runtime import Ampi

    amp = Ampi(str(_job_root(a)), allow_volatile=True)
    out = amp.kill(a.rank, reason=a.reason)
    amp.close()
    print(json.dumps(out))
    return out


def cmd_collect(a: argparse.Namespace) -> dict[str, Any]:
    dev, state = _state(a)
    root = _job_root(a)
    run_dir = RUNS / a.name
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    events = sorted(state["streams"].get("event", []), key=lambda e: e.get("seq", 0))
    with open(run_dir / "harness.trace.jsonl", "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, default=str) + "\n")
    with gzip.open(evidence / "state.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(state, fh)
    gitlog = subprocess.run(["git", "log", "--format=%H %ct %s", "--reverse"], cwd=str(root),
                            capture_output=True, text=True).stdout
    (evidence / "git-log.txt").write_text(gitlog, encoding="utf-8")

    # Cells by window.
    def cells_of(suffix: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, versions in state["cells"].items():
            space, key = k.split("\t", 1)
            if space.startswith("win/") and space.endswith(suffix) and versions:
                out[key] = versions[-1]["value"]
        return out

    manifest_cell = state["cells"].get("job\tmanifest")
    manifest = manifest_cell[-1]["value"] if manifest_cell else {}
    cfg = Config.from_dict((manifest.get("meta") or {}).get("e6") or {"name": a.name, "size": 0})
    corpus = None
    try:
        corpus = corpus_mod.build(WORK, cfg.size, corpus=cfg.corpus, legacy_dir=a.legacy_dir, pages=cfg.pages) if cfg.size else None
    except Exception as exc:  # noqa: BLE001 - the corpus is not needed for the evidence
        print(json.dumps({"corpus_unavailable": str(exc)}), flush=True)
    pages_cells = cells_of("/pages")
    book = assemble_cells(pages_cells, run_dir / "out", corpus)
    findings = {v.get("term", k): v for k, v in cells_of("/research").items()
                if k.startswith("finding/") and isinstance(v, dict)}
    registry = cells_of("/registry").get("registry")
    binding = None
    for rec in state["streams"].get("coll", []):
        if rec.get("label") == "binding-glossary" and rec.get("handle"):
            raw = state["obj"].get(rec["handle"])
            try:
                binding = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                binding = None
            break
    (run_dir / "glossary.json").write_text(
        json.dumps({"binding": binding, "findings": findings, "registry": registry},
                   indent=2, ensure_ascii=False), encoding="utf-8")

    ranks = {int(k.split("\t")[1]): v[-1]["value"] for k, v in state["cells"].items()
             if k.startswith("rank\t")}
    identities = {e["rank"]: e for e in events if e.get("kind") == "rank.identity"}
    colls: dict[str, dict[str, int]] = {}
    for rec in state["streams"].get("coll", []):
        colls.setdefault(rec["label"], {}).setdefault(rec.get("kind") or "?", 0)
        colls[rec["label"]][rec.get("kind") or "?"] += 1
    commits = [line.split() for line in gitlog.splitlines() if line.strip()]
    kinds = Counter(" ".join(c[2:4]) for c in commits if len(c) > 3)
    by_kind = Counter(e["kind"] for e in events)
    tasks = Counter(e["kind"] for e in events if e["kind"].startswith("broker."))
    report = {
        "name": a.name, "job": manifest.get("job_id"), "size": manifest.get("size"),
        "branch": dev.branch, "remote": dev._remote, "config": cfg.to_dict(),
        "rank_states": dict(Counter(v["state"] for v in ranks.values())),
        "ranks": {r: {"state": v["state"], "epoch": v["epoch"], "failure": v.get("failure_kind", ""),
                      "machine": identities.get(r, {}).get("boot_id"),
                      "session": identities.get(r, {}).get("session")}
                  for r, v in sorted(ranks.items())},
        "distinct_machines": len({i.get("boot_id") for i in identities.values() if i.get("boot_id")}),
        "book": book,
        "findings": len(findings),
        "binding_terms": len(binding) if isinstance(binding, dict) else None,
        "collectives": colls,
        "events": len(events), "events_by_kind": dict(by_kind), "tasks": dict(tasks),
        "commits_on_branch": len(commits),
        "commit_kinds": dict(kinds.most_common(12)),
        "wall_s": round(events[-1]["ts"] - events[0]["ts"], 1) if events else None,
        "collected_at": time.time(),
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("name", "size", "rank_states", "book", "findings",
                                              "commits_on_branch", "wall_s")}, indent=2, default=str))
    if not a.no_analysis:
        from ampi.analysis import analyse, load_events
        from ampi.analysis.report import write_all

        an = analyse(load_events(run_dir / "harness.trace.jsonl"), name=a.name)
        written = write_all(an, run_dir / "analysis", tex_prefix="", fmt="pdf")
        print(json.dumps({"analysis": {k: str(v) for k, v in written.items()}}, indent=2))
    return report


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _config_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--corpus", default=None, choices=sorted(corpus_mod.SOURCES),
                   help="which book; default chairs (Ilf and Petrov, public domain)")
    p.add_argument("--languages", default=None, help="comma-separated; default en,zh,ja")
    p.add_argument("--arm", default=None,
                   choices=["full", "noglossary", "noresearch", "noreview", "noseams"])
    p.add_argument("--phase-timeout", dest="phase_timeout_s", type=float, default=None)
    p.add_argument("--task-timeout", dest="task_timeout_s", type=float, default=None)
    p.add_argument("--claim-ttl", dest="claim_ttl_s", type=float, default=None)
    p.add_argument("--claim-wait", dest="claim_wait_s", type=float, default=None)
    p.add_argument("--lease", dest="lease_s", type=float, default=None)
    p.add_argument("--quorum", type=float, default=None)
    p.add_argument("--barrier-policy", default=None, choices=["wait", "proceed", "shrink", "revoke"])
    p.add_argument("--research-cap", type=int, default=None)
    p.add_argument("--research-budget", type=int, default=None)
    p.add_argument("--review-cap", type=int, default=None)
    p.add_argument("--retries", type=int, default=None)
    p.add_argument("--no-review", dest="review", action="store_false", default=None)
    p.add_argument("--no-seam", dest="seam", action="store_false", default=None)
    p.add_argument("--ctx-budget", type=int, default=None)
    p.add_argument("--pages", default=None, help='a page subset such as "13-16", for smoke tests')
    p.add_argument("--legacy-dir", default=None, help="a checkout of the legacy repository")


def _git_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--remote", default=DEFAULT_REMOTE)
    p.add_argument("--branch", default=None, help="job branch; default ampi-jobs/<name>")
    p.add_argument("--root", default=None, help="job working tree; default work/e6/<name>/job")
    p.add_argument("--read-interval", type=float, default=None,
                   help="seconds between a reader's fetches (AMPI_GIT_READ_INTERVAL)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="E6: book translation across cloud machines")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create")
    p.add_argument("--name", required=True)
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--device", default="git", choices=["git", "sqlite"])
    p.add_argument("--join-deadline", type=float, default=3600.0)
    _git_args(p)
    _config_args(p)

    p = sub.add_parser("session-prompt")
    p.add_argument("--name", required=True)
    p.add_argument("--rank", type=int, default=-1, help="unused: a machine claims its rank")
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--remote", default=DEFAULT_REMOTE)
    p.add_argument("--code-branch", default=DEFAULT_CODE_BRANCH)
    p.add_argument("--bootstrap", action="store_true",
                   help="print the short prompt a session is created with, which fetches the full one")

    p = sub.add_parser("autostart", help="claim rank slots from LAUNCH.json and run them")
    p.add_argument("--resume", action="store_true",
                   help="re-run the ranks this machine already claimed (after a pause)")
    p = sub.add_parser("slot", help="print the rank this machine claimed")
    p.add_argument("--name", required=True)

    p = sub.add_parser("run")
    p.add_argument("--name", required=True)
    p.add_argument("--rank", required=True, help="a rank, or several comma-separated ranks")
    p.add_argument("--resume", action="store_true", help="keep the local queue; replay memoised work")
    p.add_argument("--leave-open", action="store_true",
                   help="do not close the local campaign at the end (another process still serves it)")
    p.add_argument("--executor", default="broker", choices=["broker", "claude", "stub"])
    p.add_argument("--model", default=None)
    p.add_argument("--effort", default=None)
    p.add_argument("--keepalive", type=float, default=0.0,
                   help="seconds between lease renewals while an executor works; default lease/3")
    p.add_argument("--stub-latency", type=float, default=0.0)
    p.add_argument("--stub-die-after", type=int, default=None,
                   help="fault injection: the stub process exits silently after N tasks")
    p.add_argument("--legacy-dir", default=None)
    _git_args(p)

    p = sub.add_parser("local")
    p.add_argument("--name", required=True)
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--executor", default="stub", choices=["stub", "claude"])
    p.add_argument("--device", default="sqlite")
    p.add_argument("--model", default=None)
    p.add_argument("--effort", default=None)
    p.add_argument("--stub-latency", type=float, default=0.0)
    p.add_argument("--kill-rank", type=int, default=None, help="fault injection: convict this rank")
    p.add_argument("--kill-after", type=float, default=2.0)
    _config_args(p)

    for name in ("status", "kill", "collect"):
        p = sub.add_parser(name)
        p.add_argument("--name", required=True)
        _git_args(p)
    sub.choices["status"].add_argument("--brief", action="store_true")
    sub.choices["kill"].add_argument("--rank", type=int, required=True)
    sub.choices["kill"].add_argument("--reason", default="injected: machine lost")
    sub.choices["collect"].add_argument("--legacy-dir", default=None)
    sub.choices["collect"].add_argument("--no-analysis", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> Any:
    a = build_parser().parse_args(argv)
    return {
        "create": cmd_create, "session-prompt": cmd_session_prompt, "run": cmd_run,
        "autostart": cmd_autostart, "slot": cmd_slot,
        "local": cmd_local, "status": cmd_status, "kill": cmd_kill, "collect": cmd_collect,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
