"""``ampirun``: the process manager, and the AgentMPI analogue of ``mpirun``.

``mpirun`` is not part of the MPI standard, and that is deliberate: how
processes are started, where they land, and what a "node" is are all outside
the portable semantics.  The standard specifies only that when a rank calls
MPI_Init it finds itself in a world of a known size with a known rank.  The
same separation applies here, and it matters more: an AgentMPI rank might be a
Cursor subagent, a container running an agent loop, an OS process running a
scripted harness, or a thread in a test.  None of that belongs in the protocol.

``ampirun`` therefore does exactly three things:

1. creates a job and its per-rank scratch directories;
2. emits a **rank card** for each rank --- the launch contract, containing the
   rank's identity, its environment, and the protocol calls it is expected to
   make.  For an LLM rank the card *is* the program, in the same sense that an
   SPMD binary is the program for an MPI rank, so we materialise it on disk and
   commit it: an experiment whose agent instructions are not archived is not
   reproducible;
3. optionally launches OS processes for ranks that are scripted rather than
   model-driven, which is what the microbenchmarks use.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

from .constants import DEFAULT_CTX_LIMIT, DEFAULT_ROLL_CALL_TIMEOUT
from .core.runtime import Runtime
from .device import open_device

RANK_CARD_TEMPLATE = """# AgentMPI rank card --- rank {rank} of {world_size}

You are **rank {rank}** in an AgentMPI job of {world_size} ranks. AgentMPI is a
message-passing protocol: you coordinate with the other ranks *only* through the
`ampi` command-line tool. Do not read or write another rank's scratch directory,
and do not try to contact another rank by any other means. Everything you need
arrives through the protocol.

## How to invoke `ampi`

**Always pass `--job` and `--rank` explicitly, on every single call.** Do not
rely on environment variables: shell state may not survive between your tool
invocations, and a call that silently picks up the wrong rank will corrupt the
run in ways that are hard to see. Every command looks like this:

```
{bindir}/ampi --job {job_dir} --rank {rank} <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="{bindir}/ampi --job {job_dir} --rank {rank}"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `{scratch}`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "{role}"      # join the job. Do this first.
ampi status                      # who else is here and what state they are in
ampi hb --expect-idle 300        # "I am about to think for 5 minutes, do not
                                 #  declare me dead"

ampi send --to R --tag T --file PATH        # point to point
ampi recv --source R --tag T --deref        # blocking receive; --source -1 is
                                            # ANY_SOURCE, --tag -1 is ANY_TAG
ampi probe                                  # is anything waiting for me?

ampi barrier                                # everyone waits for everyone
ampi bcast --root R --file PATH             # one to all
ampi scatter --root R --json-file PATH      # root splits a block map
ampi gather --root R --json-file PATH       # all to one
ampi allgather --json-file PATH             # all to all
ampi allreduce --op OP --json-file PATH     # all to all, combined by OP
ampi reduce --root R --op OP --json-file PATH

ampi win-create --name W                    # a shared artifact space
ampi win-put --win W --key K --file PATH    # write a cell
ampi win-get --win W --key K                # read a cell
ampi win-claim --win W --key K              # atomically claim a work item
ampi win-lock --win W --key K               # take an exclusive lease
ampi win-unlock --lock-id L
ampi win-fetch-add --win W --key K          # atomic counter

ampi ctx                                    # how much context budget you have
ampi finalize --note "..."                  # leave cleanly. Do this last.
```

## Five rules that matter

1. **Collectives are collective.** If the instructions say to call `ampi
   barrier`, every rank must call it, the same number of times, in the same
   order. Skipping one, or calling `bcast` where others call `barrier`, is
   reported as `AMPI_ERR_COLLECTIVE_MISMATCH` and stalls your peers.
2. **Large payloads are passed by reference.** A big message arrives with a
   `handle` and a short `digest` instead of the body. Read the digest first and
   only run `ampi deref --handle H` if you actually need the full text. Your
   context is a budget; `ampi ctx` shows it.
3. **Heartbeat before long work.** Before any step that will take more than a
   couple of minutes without an `ampi` call, run `ampi hb --expect-idle
   SECONDS`, and over-estimate rather than under-estimate. A declared period
   can only lengthen your lease, never shorten it, so guessing high is free.
   A blocking call such as `recv` or a collective heartbeats for you while it
   waits, so you do not need to declare anything before one of those.
4. **Claim before you work.** When picking up a shared work item, use `ampi
   win-claim`. If it returns `"claimed": false` somebody else already has it;
   take a different item. Never assume an item is yours.
5. **Retry, do not improvise.** If a command fails, read the `message` field.
   It usually tells you the remedy (`--projection digest`, re-read and retry a
   compare-and-swap, and so on). If a collective returns `"status":
   "op_required"`, that is the library asking *you* to evaluate a reduction
   operator; follow the `next` field exactly.

## Your task

{task}
"""


def create_job(job_dir: str, world_size: int, *, ctx_limit: int = DEFAULT_CTX_LIMIT,
               roll_call_timeout: float = DEFAULT_ROLL_CALL_TIMEOUT,
               meta: dict[str, Any] | None = None) -> dict[str, Any]:
    job_dir = os.path.abspath(job_dir)
    os.makedirs(job_dir, exist_ok=True)
    device = open_device(os.path.join(job_dir, "job.db"))
    job_id = os.path.basename(job_dir.rstrip("/"))
    runtime = Runtime.create_job(
        device,
        job_id,
        world_size,
        ctx_limit=ctx_limit,
        roll_call_timeout=roll_call_timeout,
        meta=meta or {},
    )
    for rank in range(world_size):
        os.makedirs(os.path.join(job_dir, "ranks", str(rank)), exist_ok=True)
    device.close()
    return {
        "job_id": job_id,
        "run_id": runtime.run_id,
        "job_dir": job_dir,
        "world_size": world_size,
        "ctx_limit": ctx_limit,
        "roll_call_timeout": roll_call_timeout,
    }


def default_bindir() -> str:
    """Directory holding the ``ampi`` executable this installation provides."""
    candidate = os.path.join(sys.prefix, "bin")
    return candidate if os.path.exists(os.path.join(candidate, "ampi")) else os.path.dirname(
        os.path.abspath(sys.executable))


def rank_card(job_dir: str, rank: int, world_size: int, task: str, role: str = "worker",
              bindir: str | None = None) -> str:
    return RANK_CARD_TEMPLATE.format(
        rank=rank,
        world_size=world_size,
        job_dir=os.path.abspath(job_dir),
        scratch=os.path.join(os.path.abspath(job_dir), "ranks", str(rank)),
        bindir=bindir or default_bindir(),
        role=role,
        task=task.strip(),
    )


def write_rank_cards(job_dir: str, world_size: int, tasks: dict[int, str],
                     roles: dict[int, str] | None = None) -> list[str]:
    """Materialise one launch contract per rank and return the paths."""
    roles = roles or {}
    paths: list[str] = []
    cards_dir = os.path.join(job_dir, "cards")
    os.makedirs(cards_dir, exist_ok=True)
    for rank in range(world_size):
        text = rank_card(job_dir, rank, world_size, tasks.get(rank, tasks.get(-1, "")),
                         roles.get(rank, "worker"))
        path = os.path.join(cards_dir, f"rank{rank}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        paths.append(path)
    return paths


def rank_env(job_dir: str, rank: int, comm: str = "world") -> dict[str, str]:
    return {
        **os.environ,
        "AMPI_JOB_DIR": os.path.abspath(job_dir),
        "AMPI_RANK": str(rank),
        "AMPI_COMM": comm,
    }


def launch_processes(job_dir: str, world_size: int, command: list[str],
                     ranks: list[int] | None = None) -> list[subprocess.Popen]:
    """Start one OS process per rank, the SPMD launch model.

    Used by the microbenchmarks, where the point is to measure protocol
    overhead rather than agent behaviour and a scripted rank is the right
    instrument: a model-driven rank's turn latency would swamp the signal.
    """
    procs: list[subprocess.Popen] = []
    for rank in (ranks if ranks is not None else range(world_size)):
        log = open(os.path.join(job_dir, "ranks", str(rank), "stdout.log"), "w",
                   encoding="utf-8")
        procs.append(subprocess.Popen(command, env=rank_env(job_dir, rank), stdout=log,
                                      stderr=subprocess.STDOUT))
    return procs


def wait_for(procs: list[subprocess.Popen], timeout: float = 3600.0) -> list[int]:
    deadline = time.time() + timeout
    codes: list[int] = []
    for proc in procs:
        remaining = max(1.0, deadline - time.time())
        try:
            codes.append(proc.wait(timeout=remaining))
        except subprocess.TimeoutExpired:
            proc.kill()
            codes.append(-9)
    return codes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ampirun", description="Create AgentMPI jobs and launch ranks.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a job and its rank scratch directories")
    p.add_argument("--job", required=True)
    p.add_argument("-n", type=int, required=True)
    p.add_argument("--ctx-limit", type=int, default=DEFAULT_CTX_LIMIT)
    p.add_argument("--roll-call-timeout", type=float, default=DEFAULT_ROLL_CALL_TIMEOUT)
    p.add_argument("--meta")

    p = sub.add_parser("cards", help="write rank cards from a JSON task map")
    p.add_argument("--job", required=True)
    p.add_argument("-n", type=int, required=True)
    p.add_argument("--tasks", required=True,
                   help="JSON file mapping rank (or '-1' for all) to task text")
    p.add_argument("--roles", help="JSON file mapping rank to role name")

    p = sub.add_parser("exec", help="launch one OS process per rank")
    p.add_argument("--job", required=True)
    p.add_argument("-n", type=int, required=True)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("cmd", nargs=argparse.REMAINDER)

    p = sub.add_parser("env", help="print the environment for one rank")
    p.add_argument("--job", required=True)
    p.add_argument("--rank", type=int, required=True)

    args = parser.parse_args(argv)

    if args.command == "new":
        info = create_job(
            args.job,
            args.n,
            ctx_limit=args.ctx_limit,
            roll_call_timeout=args.roll_call_timeout,
            meta=json.loads(args.meta) if args.meta else None,
        )
        print(json.dumps(info, indent=2))
        return 0

    if args.command == "cards":
        with open(args.tasks, encoding="utf-8") as fh:
            tasks = {int(k): v for k, v in json.load(fh).items()}
        roles = {}
        if args.roles:
            with open(args.roles, encoding="utf-8") as fh:
                roles = {int(k): v for k, v in json.load(fh).items()}
        paths = write_rank_cards(os.path.abspath(args.job), args.n, tasks, roles)
        print(json.dumps({"cards": paths}, indent=2))
        return 0

    if args.command == "exec":
        cmd = [c for c in args.cmd if c != "--"]
        if not cmd:
            parser.error("exec needs a command after --")
        started = time.time()
        procs = launch_processes(os.path.abspath(args.job), args.n, cmd)
        codes = wait_for(procs, args.timeout)
        print(json.dumps({"ranks": args.n, "exit_codes": codes,
                          "wall_seconds": round(time.time() - started, 3),
                          "ok": all(c == 0 for c in codes)}, indent=2))
        return 0 if all(c == 0 for c in codes) else 1

    if args.command == "env":
        env = rank_env(os.path.abspath(args.job), args.rank)
        for key in ("AMPI_JOB_DIR", "AMPI_RANK", "AMPI_COMM"):
            print(f"export {key}={env[key]}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
