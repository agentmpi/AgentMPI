"""``ampirun``: the process launcher, which MPI keeps out of the standard and we do too.

``mpirun -np 64 ./a.out`` starts sixty-four copies of one program, tells each its
rank through the environment, distributes them over the hosts in a hostfile, and
reports how they exited.  Nothing in the MPI standard says how; Hydra, Slurm's
``srun`` and a shell script all conform.  This module is the same thing for
AgentMPI: it creates the job, starts one operating-system process per rank with
``AMPI_ROOT``, ``AMPI_RANK`` and ``AMPI_SIZE`` set, supervises them, and writes a
launch record that names every rank *requested* before any of them ran.

The multi-node form is the one modern HPC uses: a job of ``-np 256`` over
``--nodes 8`` runs thirty-two ranks on each of eight machines, and each machine
runs its own ``ampirun --node k`` against the same shared device.  Ranks are
block-distributed --- node ``k`` hosts ``k*32 .. k*32+31`` --- because the
harnesses that use this launcher partition contiguous work by rank, and a
contiguous rank block on one machine keeps a segment's neighbours local.

A supervisor is included because an executor's death is the fault this protocol
was designed around.  A rank whose process exits before it finalised is restarted
up to ``--respawn`` times; the runtime gives the restarted process a new epoch, and
a harness written to re-enter its collectives --- which is what the runtime's
``recover`` advises --- resumes from the last closed one.  That is checkpoint and
restart, and here it costs nothing beyond the discipline of keeping a rank's state
in the device rather than in its process.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .constants import DEFAULT_CTX_BUDGET

__all__ = ["launch", "main", "node_identity", "ranks_of_node"]

ENV_ROOT = "AMPI_ROOT"
ENV_RANK = "AMPI_RANK"
ENV_SIZE = "AMPI_SIZE"
ENV_DEVICE = "AMPI_DEVICE"
ENV_WORKER_ID = "AMPI_WORKER_ID"
ENV_NODE = "AMPI_NODE"
ENV_NODES = "AMPI_NODES"
#: The exit status a rank uses to say "my executor died"; used by fault injection.
EXIT_EXECUTOR_DIED = 75


def node_identity(node: int) -> dict[str, Any]:
    boot = Path("/proc/sys/kernel/random/boot_id")
    return {
        "node": node,
        "hostname": socket.gethostname(),
        "boot_id": boot.read_text().strip() if boot.exists() else None,
        "kernel": platform.release(),
        "pid": os.getpid(),
        "session": os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID"),
        "container": os.environ.get("CLAUDE_CODE_CONTAINER_ID"),
        "cpus": os.cpu_count(),
    }


def ranks_of_node(size: int, nodes: int, node: int) -> list[int]:
    """Block distribution: node ``k`` of ``M`` hosts a contiguous slice of the ranks."""
    if nodes < 1 or not 0 <= node < nodes:
        raise ValueError(f"node {node} is not in 0..{nodes - 1}")
    per, extra = divmod(size, nodes)
    start = node * per + min(node, extra)
    count = per + (1 if node < extra else 0)
    return list(range(start, start + count))


def _wait_for_job(root: Path, device: str, timeout: float) -> None:
    """Block until the job exists on the shared device.

    On a shared filesystem that is a file appearing.  On the git device it is a
    branch appearing on the remote, so the wait opens the device and asks it.
    """
    from .device import open_device

    deadline = time.time() + timeout
    while True:
        # On a shared filesystem the marker file is the job.  On the git
        # transports it merely travels with the first push, ahead of the rank
        # cells, so there the device itself is asked.
        if device not in ("git", "gitd") and (root / "job.json").exists():
            return
        if device in ("git", "gitd"):
            dev = None
            try:
                dev = open_device(device, str(root))
                # The world communicator is the last cell ``create`` writes, so
                # its presence means every rank cell is there too.  Waiting for
                # the manifest alone once admitted a node to a job whose ranks
                # were still being created, one push at a time.
                if dev.read("comm", "world") is not None:
                    return
            except Exception:  # noqa: BLE001 - the branch may not exist yet
                pass
            finally:
                if dev is not None:
                    dev.close()
        if time.time() >= deadline:
            raise SystemExit(f"no job appeared at {root} within {timeout:.0f}s")
        time.sleep(2.0)


def launch(
    command: list[str],
    *,
    size: int,
    root: str | Path,
    device: str = "sqlite",
    nodes: int = 1,
    node: int = 0,
    ranks: list[int] | None = None,
    create: bool | None = None,
    force: bool = True,
    ctx_budget: int = DEFAULT_CTX_BUDGET,
    join_deadline_s: float = 3600.0,
    meta: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
    respawn: int = 0,
    timeout_s: float = 6 * 3600.0,
    stagger_s: float = 0.05,
    poll_s: float = 1.0,
    worker_prefix: str = "",
    quiet: bool = False,
) -> dict[str, Any]:
    """Create (or join) a job and run ``command`` once per local rank.

    Returns the launch record: per-rank exit codes, restarts, wall time, and the
    node's identity.  The record is also written to ``log_dir/launch.json``, and
    is written *first* --- with every rank marked pending --- so a launch that
    dies leaves evidence of what it intended.
    """
    root = Path(root)
    log_dir = Path(log_dir) if log_dir else root.parent / "launch"
    log_dir.mkdir(parents=True, exist_ok=True)
    mine = ranks if ranks is not None else ranks_of_node(size, nodes, node)
    should_create = create if create is not None else (node == 0)
    me = node_identity(node)

    record: dict[str, Any] = {
        "size": size, "nodes": nodes, "node": node, "device": device, "root": str(root),
        "command": command, "ranks": mine, "created_here": should_create,
        "respawn": respawn, "started_at": time.time(), "node_identity": me,
        "rank_states": {str(r): {"state": "pending", "restarts": 0} for r in mine},
    }
    launch_path = log_dir / f"launch-node{node}.json"
    launch_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    from .runtime import Ampi

    if should_create:
        job = Ampi.create(str(root), size, device=device, force=force, ctx_budget=ctx_budget,
                          join_deadline_s=join_deadline_s, allow_volatile=True,
                          meta={"launcher": "ampirun", "nodes": nodes, **(meta or {})})
        record["job"] = job.manifest.job_id
        job.close()
    else:
        _wait_for_job(root, device, join_deadline_s)
    # The supervisor's own handle on the job: no rank, because it is not a
    # participant.  It is what lets a restart be a protocol event rather than a
    # process-table accident: the runtime allocates the successor's epoch, breaks
    # the predecessor's locks and marks it absent in open collectives.
    supervisor = Ampi(str(root), allow_volatile=True)
    record["job"] = supervisor.manifest.job_id
    if not should_create:
        # Rejoining a job this node's processes left --- a machine that was
        # recycled under a running population.  A rank the peers have already
        # convicted needs a new epoch before its process can take the identity;
        # a rank still inside its lease is simply resumed.  The machine's death
        # is not the rank's fault, so it does not spend the rank's own budget.
        for r in mine:
            try:
                state = supervisor._rankview(r).state  # noqa: SLF001 - the supervisor's view
            except Exception:  # noqa: BLE001 - no row yet: a fresh join
                continue
            if state == "failed":
                spawned = supervisor.respawn(r, max_restarts=respawn + 1)
                record["rank_states"][str(r)]["rejoined_epoch"] = spawned["epoch"]
    # The node announces itself in the job's own trace.  On a multi-node run the
    # only durable place every node can reach is the device, and a launch
    # record that stays on a machine about to be reclaimed is not evidence.
    supervisor.trace("launch.node", node=node, nodes=nodes, ranks=mine, identity=me,
                     device=device, created_here=should_create)

    base_env = dict(os.environ)
    base_env.update(env or {})
    base_env.update({ENV_ROOT: str(root), ENV_SIZE: str(size), ENV_DEVICE: device,
                     ENV_NODE: str(node), ENV_NODES: str(nodes)})
    base_env.pop(ENV_RANK, None)

    procs: dict[int, subprocess.Popen[bytes]] = {}
    handles: dict[int, tuple[Any, Any]] = {}
    events: list[dict[str, Any]] = []

    def start(rank: int) -> None:
        e = dict(base_env)
        e[ENV_RANK] = str(rank)
        e[ENV_WORKER_ID] = f"{worker_prefix or 'proc'}:{me['hostname']}:n{node}:r{rank}"
        out = open(log_dir / f"rank{rank}.out", "ab")  # noqa: SIM115 - closed on exit
        errf = open(log_dir / f"rank{rank}.err", "ab")  # noqa: SIM115 - closed on exit
        p = subprocess.Popen(command, env=e, stdout=out, stderr=errf, cwd=os.getcwd(),
                             start_new_session=True)
        procs[rank] = p
        handles[rank] = (out, errf)
        st = record["rank_states"][str(rank)]
        st.update(state="running", pid=p.pid, started_at=time.time())
        events.append({"ts": time.time(), "rank": rank, "event": "start", "pid": p.pid,
                       "restart": st["restarts"]})
        if not quiet:
            print(f"[ampirun] node {node}: rank {rank} pid {p.pid} started", file=sys.stderr)

    for r in mine:
        start(r)
        if stagger_s:
            time.sleep(stagger_s)

    deadline = time.time() + timeout_s
    timed_out = False

    # A terminated launcher must take its ranks with it.  Python's default
    # SIGTERM disposition skips ``finally``, which orphaned sixteen rank
    # processes --- each still calling a paid endpoint --- when a run was
    # stopped by hand.
    def _terminate(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    try:
        previous = signal.signal(signal.SIGTERM, _terminate)
    except ValueError:  # not the main thread: a test driving two nodes at once
        previous = None
    try:
        while procs:
            for rank, p in list(procs.items()):
                code = p.poll()
                if code is None:
                    continue
                del procs[rank]
                for fh in handles.pop(rank):
                    fh.close()
                st = record["rank_states"][str(rank)]
                st.update(exit_code=code, finished_at=time.time())
                events.append({"ts": time.time(), "rank": rank, "event": "exit", "code": code})
                if code == 0:
                    st["state"] = "exited"
                elif st["restarts"] < respawn:
                    st["restarts"] += 1
                    st["state"] = "respawning"
                    try:
                        spawned = supervisor.respawn(rank, max_restarts=respawn)
                    except Exception as exc:  # noqa: BLE001 - the runtime refused
                        st["state"] = "failed"
                        st["respawn_refused"] = str(exc)
                        continue
                    events.append({"ts": time.time(), "rank": rank, "event": "respawn",
                                   "restart": st["restarts"], "epoch": spawned["epoch"]})
                    if not quiet:
                        print(f"[ampirun] rank {rank} exited {code}; respawning as epoch "
                              f"{spawned['epoch']} ({st['restarts']}/{respawn})", file=sys.stderr)
                    start(rank)
                else:
                    st["state"] = "failed"
                    # Tell the runtime now.  Otherwise the peers blocked in a
                    # collective with this rank wait for its lease to expire ---
                    # fifteen minutes in a production run --- to learn what the
                    # process table already knows.
                    try:
                        supervisor.kill(rank, reason=f"process exited {code}")
                    except Exception as exc:  # noqa: BLE001 - already finalised, or gone
                        st["kill_refused"] = str(exc)
                    if not quiet:
                        print(f"[ampirun] rank {rank} exited {code}", file=sys.stderr)
            launch_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
            if procs and time.time() >= deadline:
                timed_out = True
                break
            if procs:
                time.sleep(poll_s)
    except KeyboardInterrupt:
        timed_out = True
    finally:
        for rank, p in procs.items():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGTERM)
            record["rank_states"][str(rank)].update(state="killed")
        for _rank, p in procs.items():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        for fhs in handles.values():
            for fh in fhs:
                fh.close()
        supervisor.close()
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)

    try:
        record["device_stats"] = supervisor.device.stats()
    except Exception as exc:  # noqa: BLE001
        record["device_stats"] = {"error": str(exc)}
    with contextlib.suppress(Exception):
        supervisor.trace(
            "launch.exit", node=node, nodes=nodes, ranks=mine, identity=me,
            exited=sum(1 for st in record["rank_states"].values() if st["state"] == "exited"),
            failed=sum(1 for st in record["rank_states"].values() if st["state"] != "exited"),
            restarts=sum(st["restarts"] for st in record["rank_states"].values()),
            wall_s=round(time.time() - record["started_at"], 3), timed_out=timed_out,
            device_stats=record["device_stats"],
        )
    record.update(
        finished_at=time.time(),
        wall_s=round(time.time() - record["started_at"], 3),
        timed_out=timed_out,
        exited=sum(1 for s in record["rank_states"].values() if s["state"] == "exited"),
        failed=sum(1 for s in record["rank_states"].values() if s["state"] != "exited"),
        restarts=sum(s["restarts"] for s in record["rank_states"].values()),
        events=events,
    )
    launch_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return record


def export(root: str | Path, out_dir: str | Path, *, name: str = "") -> dict[str, Any]:
    """Export the job's trace and a diagnosis, the way :meth:`Harness.save` does.

    Run once, after every node's ranks have exited, from any machine that can open
    the device.  Kept separate from :func:`launch` because on a multi-node job no
    single launcher knows when the others are done.
    """
    from .doctor import diagnose
    from .runtime import Ampi

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    amp = Ampi(str(root), allow_volatile=True)
    try:
        events = amp.events()
        by_kind: dict[str, int] = {}
        for e in events:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        trace = out / "harness.trace.jsonl"
        with open(trace, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, default=str) + "\n")
        states = {}
        for r in range(amp.size):
            try:
                states[str(r)] = amp._rankview(r).state  # noqa: SLF001 - the doctor's view
            except Exception:  # noqa: BLE001
                states[str(r)] = "unknown"
        try:
            device_stats = amp.device.stats()
        except Exception as exc:  # noqa: BLE001 - stats are evidence, not a requirement
            device_stats = {"error": str(exc)}
        report = {
            "name": name, "job": amp.manifest.job_id, "size": amp.size,
            "device": amp.device.name, "device_stats": device_stats,
            "events": by_kind, "event_count": len(events),
            "rank_states": states, "trace": str(trace),
            "diagnosis": diagnose(amp),
        }
        (out / "harness.json").write_text(json.dumps(report, indent=2, default=str),
                                          encoding="utf-8")
        return report
    finally:
        amp.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ampirun",
        description="run one process per AgentMPI rank; the process manager MPI leaves unspecified",
    )
    ap.add_argument("-np", "--np", dest="size", type=int, required=True, help="total ranks")
    ap.add_argument("--root", required=True, help="job root on the shared device")
    ap.add_argument("--device", default=os.environ.get(ENV_DEVICE, "sqlite"),
                    choices=["sqlite", "journal", "memory", "git", "gitd"])
    ap.add_argument("--nodes", type=int, default=1, help="machines sharing this job")
    ap.add_argument("--node", type=int, default=0, help="which machine this is, 0-based")
    ap.add_argument("--ranks", default=None, help="explicit local ranks, e.g. 0,1,4-7")
    ap.add_argument("--create", action="store_true", help="create the job here (default: node 0)")
    ap.add_argument("--join", action="store_true", help="never create; wait for the job")
    ap.add_argument("--no-force", action="store_true", help="refuse to overwrite a job at --root")
    ap.add_argument("--ctx-budget", type=int, default=DEFAULT_CTX_BUDGET)
    ap.add_argument("--join-deadline", type=float, default=3600.0)
    ap.add_argument("--respawn", type=int, default=0, help="restarts allowed per rank")
    ap.add_argument("--timeout", type=float, default=6 * 3600.0)
    ap.add_argument("--stagger", type=float, default=0.05, help="seconds between starts")
    ap.add_argument("--log-dir", default=None, help="per-rank stdout/stderr and the launch record")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE for every rank")
    ap.add_argument("--meta", default="{}", help="JSON recorded in the job manifest")
    ap.add_argument("--worker-prefix", default="", help="prefix for AMPI_WORKER_ID")
    ap.add_argument("--export", default=None,
                    help="after the ranks exit, export trace and report to this directory")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="-- program and arguments")
    a = ap.parse_args(argv)

    cmd = [c for c in a.command if c != "--"] if a.command else []
    if not cmd:
        ap.error("a program to run is required after --")
    ranks = None
    if a.ranks:
        ranks = []
        for part in a.ranks.split(","):
            if "-" in part:
                lo, hi = part.split("-", 1)
                ranks.extend(range(int(lo), int(hi) + 1))
            elif part.strip():
                ranks.append(int(part))
    env = dict(kv.split("=", 1) for kv in a.env if "=" in kv)
    create = True if a.create else (False if a.join else None)
    rec = launch(
        cmd, size=a.size, root=a.root, device=a.device, nodes=a.nodes, node=a.node,
        ranks=ranks, create=create, force=not a.no_force, ctx_budget=a.ctx_budget,
        join_deadline_s=a.join_deadline, meta=json.loads(a.meta), log_dir=a.log_dir,
        env=env, respawn=a.respawn, timeout_s=a.timeout, stagger_s=a.stagger,
        worker_prefix=a.worker_prefix, quiet=a.quiet,
    )
    summary = {k: rec[k] for k in ("size", "node", "nodes", "ranks", "exited", "failed",
                                   "restarts", "wall_s", "timed_out")}
    if a.export:
        summary["export"] = export(a.root, a.export)["diagnosis"]
    print(json.dumps(summary))
    return 0 if rec["failed"] == 0 and not rec["timed_out"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


def render_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in argv)
