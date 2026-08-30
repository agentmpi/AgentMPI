"""PAMPI: the AgentMPI profiling interface.

MPI mandates that every MPI_Xxx entry point also be reachable as PMPI_Xxx, so
that a tool can interpose on the whole interface without recompiling the
application.  That single decision is why the HPC community has Vampir,
Score-P, TAU, mpiP and Darshan, and why performance claims about MPI programs
are checkable by third parties.

AgentMPI mandates the same thing, and extends the record with the two
quantities that actually matter for agents: tokens moved and context occupancy.
Every measurement in this paper's evaluation is derived from this table and
nothing else, which means any reader can recompute our numbers from the shipped
job databases.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .. import util


class Tracer:
    """Emits enter/exit event pairs into the device's event stream."""

    def __init__(self, device: Any, job_id: str, rank: int | None) -> None:
        self.device = device
        self.job_id = job_id
        self.rank = rank
        self.enabled = os.environ.get("AMPI_TRACE", "1") not in ("0", "false", "off")

    def emit(
        self,
        op: str,
        phase: str,
        *,
        comm_id: str | None = None,
        peer: int | None = None,
        tag: int | None = None,
        tokens: int = 0,
        dur: float | None = None,
        ok: bool = True,
        err: str | None = None,
        **meta: Any,
    ) -> None:
        if not self.enabled:
            return
        self.device.execute(
            "INSERT INTO event (job_id, rank, ts, op, phase, comm_id, peer, tag, tokens, "
            "dur, ok, err, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.job_id,
                self.rank,
                util.now(),
                op,
                phase,
                comm_id,
                peer,
                tag,
                tokens,
                dur,
                1 if ok else 0,
                err,
                util.dumps(meta),
            ),
        )

    @contextmanager
    def span(self, op: str, **kw: Any) -> Iterator[dict[str, Any]]:
        """Wrap a protocol call in an enter/exit pair.

        The mutable dict yielded to the body lets the operation report how many
        tokens it actually moved, which is only known once matching completes.
        """
        started = time.time()
        self.emit(op, "enter", **kw)
        state: dict[str, Any] = {"tokens": 0, "meta": {}}
        try:
            yield state
        except Exception as exc:  # noqa: BLE001 - the tracer must see every failure
            self.emit(
                op,
                "exit",
                dur=time.time() - started,
                ok=False,
                err=type(exc).__name__,
                message=str(exc)[:400],
                **kw,
            )
            raise
        else:
            merged = dict(kw)
            merged.pop("tokens", None)
            self.emit(
                op,
                "exit",
                dur=time.time() - started,
                tokens=int(state.get("tokens", 0)),
                **merged,
                **state.get("meta", {}),
            )


class NullTracer(Tracer):
    def __init__(self) -> None:  # noqa: D107 - trivial
        self.enabled = False

    def emit(self, *a: Any, **kw: Any) -> None:  # noqa: D102
        return
