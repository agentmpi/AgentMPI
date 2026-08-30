"""``ampi run``: the launcher, AgentMPI's ``mpirun``.

``mpirun`` does three things: it decides how many processes to start, it starts
them with an environment that lets each discover its rank and its peers, and it
watches them, reporting exits. AgentMPI's launcher does the same three things,
but the "processes" are LLM agents, so two of the three change character.

*Starting* a rank means materialising a prompt: the rank's identity, its role,
its slice of the work, and the protocol manual it needs to participate. That
prompt is written to disk as a launch artefact rather than executed directly,
because the actual spawning mechanism differs by host (a Cursor subagent, an
API-driven agent loop, a human at a terminal). Keeping the launcher's output
declarative is what makes AgentMPI portable across agent hosts, in the same way
that keeping MPI's process-startup out of the standard let it run on every
machine that ever shipped.

*Watching* a rank matters much more than in MPI, because agents fail
individually and often. The launcher is therefore also the supervisor: it
observes lease expiry, applies a restart policy in the spirit of OTP supervision
trees, and records every replacement so the evaluation can attribute time and
tokens to recovery.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import ft
from .core import Config, create_job
from .errors import ArgError
from .journal import STATE_DIR, Journal, now_ns
from .version import PROTOCOL_VERSION, __version__


@dataclass
class RankSpec:
    rank: int
    role: str = "worker"
    #: Free-form task text spliced into the rank's prompt.
    task: str = ""
    #: Extra key/values exposed to the rank (and recorded in the journal).
    env: Dict[str, str] = field(default_factory=dict)


def _manual_path(root: Path) -> Path:
    """Locate the agent-facing protocol manual to embed in rank prompts."""
    for cand in (
        root / "bindings" / "AGENT_GUIDE.md",
        Path(__file__).resolve().parent.parent / "bindings" / "AGENT_GUIDE.md",
    ):
        if cand.exists():
            return cand
    raise ArgError("cannot find bindings/AGENT_GUIDE.md; run from the repository root")


def create(
    root: Path,
    *,
    np: int,
    label: str,
    ranks: Optional[Sequence[RankSpec]] = None,
    cfg: Optional[Config] = None,
    preamble: str = "",
    fresh: bool = True,
    join_grace_s: float = 900.0,
) -> Dict[str, Any]:
    """Create a job directory, journal, world communicator and rank prompts.

    ``join_grace_s`` is how long a requested rank has to call ``AMPI_Init``
    before the failure detector may declare it failed. It is a lease granted at
    request time rather than at arrival time, which is what makes "the launcher
    could not start this rank" a detectable failure rather than an eternal wait.
    """
    root = Path(root).resolve()
    if fresh and (root / STATE_DIR).exists():
        shutil.rmtree(root / STATE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    j = Journal(root, create=True)
    specs = list(ranks) if ranks else [RankSpec(rank=r) for r in range(np)]
    if len(specs) != np:
        raise ArgError(f"got {len(specs)} rank specs for np={np}")
    job_id = create_job(
        j, world_size=np, label=label, cfg=cfg, roles=[s.role for s in specs]
    )
    prompts_dir = root / STATE_DIR / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    manual = _manual_path(Path(__file__).resolve().parent.parent).read_text(encoding="utf-8")
    (root / STATE_DIR / "AGENT_GUIDE.md").write_text(manual, encoding="utf-8")

    # A per-rank launch token, in the environment alongside the rank number.
    # The runtime checks it against the journal, so an agent whose AMPI_RANK has
    # been overwritten by a peer is told which rank it actually holds credentials
    # for instead of silently acting as that peer. This is what a scheduler does
    # when it hands a task its credentials rather than trusting it to name itself.
    tokens_by_rank = {s.rank: "t-" + uuid.uuid4().hex[:16] for s in specs}

    manifest: Dict[str, Any] = {
        "job": job_id,
        "label": label,
        "protocol": PROTOCOL_VERSION,
        "runtime": __version__,
        "root": str(root),
        "world_size": np,
        "created_ns": now_ns(),
        "config": (cfg or Config()).to_dict(),
        "ranks": [],
    }
    for s in specs:
        text = render_prompt(root, job_id, s, np, preamble=preamble, manual_ref=str(root / STATE_DIR / "AGENT_GUIDE.md"))
        p = prompts_dir / f"rank{s.rank:04d}.md"
        p.write_text(text, encoding="utf-8")
        with j.tx() as c:
            # A **join deadline**. Without one, a rank that never starts at all is
            # invisible to the failure detector: it has no lease to expire, so it
            # is neither alive nor failed, and every peer waits for it forever.
            # We found this the hard way -- a launcher that could only start 10 of
            # 22 requested ranks produced a job in which the 12 no-shows were
            # permanently pending. Giving every rank a lease from the moment it is
            # *requested* rather than from the moment it first calls in makes
            # launch failure a detectable failure like any other.
            c.execute(
                "UPDATE rank SET state='spawned', meta=?,"
                " lease_expires_ns=?, last_hb_ns=? WHERE job=? AND rank=?",
                (
                    json.dumps({"prompt": str(p), "env": s.env, "task_chars": len(s.task),
                                "token": tokens_by_rank[s.rank]}),
                    now_ns() + int(join_grace_s * 1_000_000_000),
                    now_ns(),
                    job_id,
                    s.rank,
                ),
            )
        manifest["ranks"].append(
            {"rank": s.rank, "role": s.role, "prompt": str(p),
             "env": {"AMPI_ROOT": str(root), "AMPI_RANK": str(s.rank),
                     "AMPI_TOKEN": tokens_by_rank[s.rank], **s.env}}
        )
    (root / STATE_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    j.close()
    return manifest


HEADER = """\
# AgentMPI rank {rank} of {np}  (job {job})

You are **rank {rank}** in an AgentMPI job of {np} ranks. AgentMPI is a message
passing interface for multi-agent systems: you coordinate with the other ranks
*only* through the `ampi` command-line tool. There is no shared conversation and
no orchestrator reading your output -- if you do not send it, nobody sees it.

## Non-negotiable rules

1. Your identity is already in the environment (`AMPI_RANK={rank}`, `AMPI_ROOT={root}`).
   Never pass `--rank`; never act as another rank.
2. Run `ampi init` first and `ampi fini` last. Between them, call `ampi` often:
   every call renews your lease, and if you go quiet for too long the job will
   declare you dead and replace you.
3. Blocking calls have deadlines and return `AMPI_ERR_TIMEOUT`. That is normal,
   not an error: **re-run the identical command** to resume the same wait. Your
   place in the queue is durable.
4. Never invent a peer rank, a tag, or a window key that was not given to you.
5. Watch your context. `ampi ctx` shows your budget. Large payloads arrive as
   handles (`o:...`), not text; read them with `ampi view <handle> --budget N`
   and only read what you actually need.
6. If a command tells you `action_required`, do that action before anything else.

## Your role

**{role}**

## Your task

{task}
"""


def render_prompt(
    root: Path,
    job_id: str,
    spec: RankSpec,
    np: int,
    *,
    preamble: str = "",
    manual_ref: str = "",
) -> str:
    parts = [
        HEADER.format(
            rank=spec.rank, np=np, job=job_id, role=spec.role, task=spec.task.strip(),
            root=str(root),
        )
    ]
    if preamble.strip():
        parts.append("## Job briefing\n\n" + preamble.strip() + "\n")
    if spec.env:
        parts.append(
            "## Parameters\n\n"
            + "\n".join(f"- `{k}` = `{v}`" for k, v in spec.env.items())
            + "\n"
        )
    parts.append(
        "## The AgentMPI manual\n\n"
        f"The full protocol manual is at `{manual_ref}`. Read it now with `ampi man` "
        "(which prints the same text) before you start, then keep it in mind. "
        "The quick reference is:\n\n"
        "```\n" + QUICKREF + "```\n"
    )
    return "\n".join(parts)


QUICKREF = """\
ampi init                      join the job (do this first)
ampi info                      who am I, how big is the world, what comm
ampi man                       print the full protocol manual
ampi ctx                       my context budget: used / remaining

ampi send --to R --tag T --in @file|TEXT        point-to-point send
ampi recv [--from R|any] [--tag T|any] [--timeout S] [--materialize]
ampi probe                     what is waiting for me, and what would it cost
ampi inbox                     list all pending messages (cheap)

ampi barrier --label NAME [--quorum 0.9]
ampi bcast --root 0 [--in @file] --label NAME
ampi scatter --root 0 [--parts @dir] --label NAME
ampi gather  --root 0 --in @file --label NAME [--budget N]
ampi allgather --in @file --label NAME [--budget N]
ampi reduce  --op OP --in @file --root 0 --label NAME
ampi allreduce --op OP --in @file --label NAME
ampi exscan  --op OP --in @file --label NAME
ampi reduce commit --step S --in @file          finish an agent merge step

ampi win create --name W                        shared state (blackboard)
ampi win put   --win W --key K --in @file       write a cell
ampi win get   --win W --key K [--budget N]     read a cell
ampi win acc   --win W --key K --op union --in @file    atomic merge
ampi win cas   --win W --key K --expect X --value Y     claim work atomically
ampi win ls    --win W [--prefix P]             list keys without reading them
ampi win lock/unlock --win W [--key K]          leased exclusive access
ampi win fence --win W --label NAME             close a shared-state epoch

ampi comm split --color C [--key K]             form sub-teams
ampi comm shrink                                rebuild after failures
ampi comm revoke                                unblock everyone after a failure
ampi agree --label NAME [--flag true|false]     fault-tolerant agreement
ampi failed                                     who has died

ampi view HANDLE [--op head:800|keys:a,b|grep:PAT|outline] [--budget N]
ampi memo put/get KEY [VALUE]                   durable notes for my replacement
ampi status                                     job-wide progress
ampi fini                                       leave the job (do this last)

Payload arguments: `--in TEXT` inlines text, `--in @path` reads a file.
Every command takes `--json` for machine-readable output.
"""


# --------------------------------------------------------------------------
# Supervision
# --------------------------------------------------------------------------


def status(j: Journal, *, comm: str = "world") -> Dict[str, Any]:
    job = j.job_row()
    ranks = j.q("SELECT * FROM rank WHERE job=? ORDER BY rank", (j.job_id,))
    ts = now_ns()
    states: Dict[str, int] = {}
    rows = []
    for r in ranks:
        st = str(r["state"])
        exp = int(r["lease_expires_ns"] or 0)
        if st in ("running", "init") and exp and ts > exp:
            st = "suspect"
        states[st] = states.get(st, 0) + 1
        rows.append(
            {
                "rank": int(r["rank"]),
                "epoch": int(r["epoch"]),
                "state": st,
                "role": r["role"],
                "calls": int(r["calls"]),
                "ctx_used": int(r["ctx_used"]),
                "ctx_budget": int(r["ctx_budget"]),
                "last_call_s_ago": round((ts - int(r["last_hb_ns"])) / 1e9, 1) if r["last_hb_ns"] else None,
            }
        )
    counters = {
        f"{c['name']}": int(c["value"])
        for c in j.q("SELECT name, SUM(value) AS value FROM counter WHERE job=? GROUP BY name", (j.job_id,))
    }
    colls = j.q(
        "SELECT op, algo, state, COUNT(*) AS n FROM coll WHERE job=? GROUP BY op, algo, state",
        (j.job_id,),
    )
    return {
        "job": j.job_id,
        "label": job["label"],
        "world_size": int(job["world_size"]),
        "elapsed_s": round((ts - int(job["created_ns"])) / 1e9, 1),
        "rank_states": states,
        "ranks": rows,
        "counters": counters,
        "collectives": [
            {"op": str(c["op"]), "algo": str(c["algo"]), "state": str(c["state"]), "n": int(c["n"])}
            for c in colls
        ],
        "failures": [
            {"rank": int(f["rank"]), "epoch": int(f["epoch"]), "kind": str(f["kind"]),
             "s_ago": round((ts - int(f["detected_ns"])) / 1e9, 1)}
            for f in j.q("SELECT * FROM failure WHERE job=? ORDER BY id", (j.job_id,))
        ],
        "messages": {
            "posted": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=? AND status='posted'", (j.job_id,), 0)),
            "delivered": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=? AND status='delivered'", (j.job_id,), 0)),
            "total": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=?", (j.job_id,), 0)),
        },
    }


def supervise(
    j: Journal,
    *,
    policy: str = "restart",
    max_restarts: int = 2,
) -> Dict[str, Any]:
    """One pass of the supervisor: detect, then apply the restart policy.

    Policies, named after OTP's restart strategies:

    * ``none`` -- detect and report only; the harness decides.
    * ``restart`` -- prepare a replacement for each failed rank (one-for-one),
      up to ``max_restarts`` per rank, and emit the replacement's prompt.
    * ``shrink`` -- do not replace; report that the survivors should shrink.

    Bounding restarts per rank matters: an agent that fails because its
    assignment is impossible will fail again, and an unbounded supervisor turns
    that into an expensive infinite loop. This is OTP's max-restart-intensity
    idea, and it is the difference between resilience and a bill.
    """
    from .core import detect_failures

    with j.tx() as c:
        newly = detect_failures(j, by=None, conn=c)
    failed = [
        int(r["rank"])
        for r in j.q("SELECT rank FROM rank WHERE job=? AND state='failed' ORDER BY rank", (j.job_id,))
    ]
    actions: List[Dict[str, Any]] = []
    if policy == "restart":
        manifest_p = j.dir / "manifest.json"
        manifest = json.loads(manifest_p.read_text()) if manifest_p.exists() else {"ranks": []}
        by_rank = {int(r["rank"]): r for r in manifest.get("ranks", [])}
        for rk in failed:
            n_prev = int(
                j.scalar("SELECT COUNT(*) FROM failure WHERE job=? AND rank=?", (j.job_id, rk), 0)
            )
            if n_prev > max_restarts:
                actions.append({"rank": rk, "action": "give_up",
                                "reason": f"{n_prev} failures exceeds max_restarts={max_restarts}"})
                continue
            info = ft.respawn(j, rk)
            brief = ft.recover(j, rk)
            brief_p = j.dir / "prompts" / f"rank{rk:04d}.recovery.json"
            brief_p.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
            actions.append(
                {
                    "rank": rk,
                    "action": "respawn",
                    "new_epoch": info["new_epoch"],
                    "prompt": by_rank.get(rk, {}).get("prompt"),
                    "recovery_brief": str(brief_p),
                    "env": {"AMPI_ROOT": str(j.root), "AMPI_RANK": str(rk), "AMPI_REINIT": "1"},
                }
            )
    elif policy == "shrink":
        for rk in failed:
            actions.append({"rank": rk, "action": "exclude"})
    return {"newly_detected": newly, "failed": failed, "policy": policy, "actions": actions}
