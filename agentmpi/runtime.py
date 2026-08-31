"""Init / Finalize and the ampi-run launcher.

MPI's mpirun/mpiexec is the hidden half of the programming model: it creates
COMM_WORLD, assigns ranks, and starts processes. AgentMPI does the same for
agent executors, whether they are OS processes or Cursor subagents that join
an already-bootstrapped communicator.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from agentmpi.comm import Communicator
from agentmpi.constants import (
    COMM_WORLD_NAME,
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_FAILURE_TIMEOUT_S,
)
from agentmpi.types import Lifecycle
from agentmpi.util import atomic_write_json, now

COMM_WORLD: Communicator | None = None

ENV_HOME = "AMPI_HOME"
ENV_RANK = "AMPI_RANK"
ENV_SIZE = "AMPI_SIZE"
ENV_COMM = "AMPI_COMM"
ENV_ROLE = "AMPI_ROLE"
ENV_BUDGET = "AMPI_CONTEXT_BUDGET"
ENV_FAIL_T = "AMPI_FAILURE_TIMEOUT"


def Init(home: str | Path | None = None, rank: int | None = None, size: int | None = None) -> Communicator:
    global COMM_WORLD
    home_s = str(home or os.environ.get(ENV_HOME) or (Path.cwd() / ".ampi"))
    rank_i = int(os.environ[ENV_RANK] if rank is None else rank)
    size_i = int(os.environ[ENV_SIZE] if size is None else size)
    name = os.environ.get(ENV_COMM, COMM_WORLD_NAME)
    role = os.environ.get(ENV_ROLE, "worker")
    budget = int(os.environ.get(ENV_BUDGET, DEFAULT_CONTEXT_BUDGET))
    fail_t = float(os.environ.get(ENV_FAIL_T, DEFAULT_FAILURE_TIMEOUT_S))
    comm = Communicator(
        home_s,
        rank=rank_i,
        size=size_i,
        name=name,
        role=role,
        context_budget=budget,
        failure_timeout_s=fail_t,
        bootstrap=(rank_i == 0),
    )
    comm.heartbeat(Lifecycle.ACTIVE)
    COMM_WORLD = comm
    return comm


def attach(home: str | Path, rank: int, size: int, **kwargs) -> Communicator:
    """Join an existing communicator (used by Cursor subagents)."""
    os.environ[ENV_HOME] = str(home)
    os.environ[ENV_RANK] = str(rank)
    os.environ[ENV_SIZE] = str(size)
    return Init(home, rank, size)


def Finalize() -> None:
    global COMM_WORLD
    if COMM_WORLD is not None:
        COMM_WORLD.finalize()
        COMM_WORLD = None


def launch(
    command: Sequence[str],
    n: int,
    home: Path | str,
    extra_env: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> list[subprocess.CompletedProcess[str]]:
    """SPMD launcher: start n copies of command with AMPI_RANK/SIZE/HOME set."""
    home_p = Path(home)
    home_p.mkdir(parents=True, exist_ok=True)
    # Rank 0 bootstraps the communicator before anyone else starts.
    bootstrap = Communicator(home_p, rank=0, size=n, bootstrap=True)
    bootstrap.heartbeat(Lifecycle.INIT, note="launcher")
    atomic_write_json(
        home_p / "job.json",
        {"n": n, "command": list(command), "home": str(home_p), "ts": now()},
    )
    procs: list[subprocess.Popen[str]] = []
    for rank in range(n):
        env = os.environ.copy()
        env.update(
            {
                ENV_HOME: str(home_p),
                ENV_RANK: str(rank),
                ENV_SIZE: str(n),
                ENV_COMM: COMM_WORLD_NAME,
            }
        )
        if extra_env:
            env.update(extra_env)
        procs.append(
            subprocess.Popen(
                list(command),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    results: list[subprocess.CompletedProcess[str]] = []
    for rank, proc in enumerate(procs):
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        results.append(
            subprocess.CompletedProcess(proc.args, proc.returncode or 0, out, err)
        )
        (home_p / "logs").mkdir(exist_ok=True)
        (home_p / "logs" / f"rank{rank}.out").write_text(out or "")
        (home_p / "logs" / f"rank{rank}.err").write_text(err or "")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ampi-run", description="Launch an AgentMPI SPMD job")
    parser.add_argument("-n", "--np", type=int, required=True, help="number of ranks")
    parser.add_argument("--home", default=None, help="communicator home directory")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        parser.error("missing command")
    home = Path(args.home or (Path.cwd() / ".ampi" / f"job-{os.getpid()}"))
    results = launch(command, args.np, home, timeout_s=args.timeout)
    failed = [i for i, r in enumerate(results) if r.returncode != 0]
    if failed:
        print(f"ranks failed: {failed}", file=sys.stderr)
        for i in failed:
            print(f"--- rank {i} stderr ---", file=sys.stderr)
            print(results[i].stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
