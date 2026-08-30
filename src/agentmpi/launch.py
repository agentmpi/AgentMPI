"""Launch plans: the process-manager boundary.

MPI draws a sharp line between the standard and the thing that starts the
processes.  ``mpiexec`` is specified only loosely; the real work of
launching, wiring up and exchanging endpoint information belongs to a
process manager (Hydra, PRRTE) talking a separate protocol (PMI, PMIx).  The
separation is what lets the same MPI library run under Slurm, under a
laptop, and under a batch scheduler nobody has written yet.

AgentMPI needs the same boundary, and needs it more, because the ways to
start an agent are far more varied than the ways to start a process: a
subagent API, a container, a serverless function, a human.  So the protocol
specifies the *wire-up* -- what a rank must be told in order to join -- and
declines to specify the *launch*.

A rank needs exactly three things:

``AMPI_ROOT``
    where the run lives.
``AMPI_RANK``
    who it is.
``AMPI_SIZE``
    how many peers there are (also discoverable from the manifest).

Everything else -- the group, the rank table, the peers' capabilities, the
epoch -- is *discovered* from the run directory at ``AMPI_Init``.  That is
deliberately the PMIx design: put a key-value store in front of the job and
let ranks read what they need, rather than passing an ever-growing argument
list through the launcher.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .runtime import RunManifest


@dataclass
class LaunchSpec:
    """Everything one rank must be told."""

    rank: int
    root: str
    size: int
    role: str
    model: str
    program: str
    env: dict[str, str]

    def shell_prelude(self) -> str:
        exports = "\n".join(f'export {k}="{v}"' for k, v in sorted(self.env.items()))
        return exports


PROMPT_TEMPLATE = """\
You are **rank {rank}** of {size} in an AgentMPI job.

AgentMPI is a message-passing protocol.  You are one process in a parallel
program: you have an identity, you have peers, and you coordinate with them
by running `ampi` commands in your shell.  Do not try to do the whole job
yourself, and do not try to talk to other ranks except through `ampi`.

## Your identity

- rank: {rank}   (ranks are numbered 0..{last})
- size: {size}
- role: {role}

**Run this first, in every shell you open:**

```
export PATH="{bindir}:$PATH"
export AMPI_ROOT="{root}"
export AMPI_RANK="{rank}"
export AMPI_SIZE="{size}"
```

## How to communicate

Every command blocks until it completes, which is what you want: if you run
`ampi recv`, you are waiting for a peer, and the command returns when the
message arrives.

```
ampi rank                                  # confirm who you are
ampi recv --source 0 --tag 1 --out task.md # block until rank 0 sends you work
ampi send --dest 0 --tag 2 --file out.md   # send your result to rank 0
ampi barrier                               # wait for every rank to arrive here
ampi bcast --root 0 --out spec.md          # receive a broadcast
ampi gather --root 0 --file out.md         # contribute to a gather
ampi allreduce --op ampi_union --json '{{"k":["v"]}}' --out merged.json
ampi scan --exclusive --op ampi_union --file terms.json --out prefix.json
ampi win put --key findings/{rank} --file notes.md      # publish to the blackboard
ampi win query --question "..." --budget 1500           # read what fits
ampi progress                              # tell the job you finished a turn
ampi status                                # your state and your peers'
```

Run `ampi <command> --help` for the full option list.

## Rules

1. **Follow your program exactly.**  Collectives are collective: if your
   program says to call `ampi barrier`, every rank calls it, in the same
   order.  Skipping one hangs the whole job.
2. **Call `ampi progress` after each turn.**  The job's failure detector
   uses it to tell "still working" from "stuck"; if you go quiet, you will
   be declared failed and replaced.
3. **Do not read more than you need.**  Your context is a budget shared with
   your reasoning.  Prefer `ampi win query` over reading everything.
4. **If a command fails, read the error.**  It is JSON on stderr with an
   `error` field: `AMPI_ERR_TIMEOUT` (peer is slow or dead),
   `AMPI_ERR_REVOKED` (job is being torn down -- stop),
   `AMPI_ERR_CONTRACT` (your payload was malformed -- fix and resend).
5. **Never edit anything under `{root}`** except through `ampi`.  That
   directory is the transport.

## Your program

{program}
"""


def render_prompt(spec: LaunchSpec, program_text: str) -> str:
    return PROMPT_TEMPLATE.format(
        rank=spec.rank, size=spec.size, last=spec.size - 1, role=spec.role,
        root=spec.root, program=program_text,
        bindir=spec.env.get("AMPI_BIN", "/workspace/bin"),
    )


def build_specs(
    root: str | os.PathLike[str], manifest: RunManifest, program: str
) -> list[LaunchSpec]:
    root = str(Path(root).resolve())
    specs: list[LaunchSpec] = []
    for entry in manifest.ranks:
        rank = int(entry["rank"])
        specs.append(LaunchSpec(
            rank=rank, root=root, size=manifest.size,
            role=entry.get("role", "worker"), model=entry.get("model", "unknown"),
            program=program,
            env={"AMPI_ROOT": root, "AMPI_RANK": str(rank),
                 "AMPI_SIZE": str(manifest.size),
                 "AMPI_BIN": os.environ.get("AMPI_BIN", "/workspace/bin")},
        ))
    return specs


def write_launch_plan(
    root: str | os.PathLike[str],
    manifest: RunManifest,
    program: str,
    out_dir: Path | None = None,
) -> str:
    """Write one prompt file per rank, plus a machine-readable plan."""
    program_path = Path(program)
    program_text = program_path.read_text(encoding="utf-8") if program_path.exists() else program
    out = Path(out_dir or (Path(root) / "launch"))
    out.mkdir(parents=True, exist_ok=True)
    specs = build_specs(root, manifest, program)
    plan: list[dict[str, Any]] = []
    for spec in specs:
        text = render_prompt(spec, _specialise(program_text, spec))
        path = out / f"rank-{spec.rank:03d}.md"
        path.write_text(text, encoding="utf-8")
        plan.append({"rank": spec.rank, "role": spec.role, "prompt": str(path),
                     "env": spec.env})
    (out / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return str(out)


def _specialise(program_text: str, spec: LaunchSpec) -> str:
    """Substitute a rank's identity into its program text."""
    return (program_text
            .replace("{{RANK}}", str(spec.rank))
            .replace("{{SIZE}}", str(spec.size))
            .replace("{{ROLE}}", spec.role)
            .replace("{{ROOT}}", spec.root)
            .replace("{{LAST}}", str(spec.size - 1)))
