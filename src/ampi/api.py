"""The AgentMPI Python binding.

MPI is specified once and bound many times --- C, Fortran, C++ (historically),
and then mpi4py, MPI.NET, and others outside the standard.  AgentMPI follows
the same pattern with two bindings that matter: this one, for harness code that
runs as ordinary software, and the ``ampi`` command-line binding in
``cli.py``, for the agents themselves.  The CLI binding is not a convenience
wrapper: an LLM agent's only way to take an action is to call a tool, so a
protocol that agents are meant to speak must have a binding whose calling
convention is "run a command and read the JSON".
"""

from __future__ import annotations

import os
from typing import Any

from .constants import (
    ALGO_AUTO,
    AMPI_ANY_SOURCE,
    AMPI_ANY_TAG,
    AMPI_COMM_WORLD,
    DEFAULT_CTX_LIMIT,
    LOCK_EXCLUSIVE,
)
from .core import collectives as _coll
from .core.runtime import Runtime
from .device import open_device


class Ampi:
    """One rank's handle on an AgentMPI job."""

    def __init__(self, job_dir: str, rank: int | None = None, *, comm: str = AMPI_COMM_WORLD):
        self.job_dir = os.path.abspath(job_dir)
        self.job_id = os.path.basename(self.job_dir.rstrip("/"))
        self.device = open_device(os.path.join(self.job_dir, "job.db"))
        self.rt = Runtime(self.device, self.job_id, rank)
        self.comm = comm

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def create(cls, job_dir: str, world_size: int, *, ctx_limit: int = DEFAULT_CTX_LIMIT,
               meta: dict[str, Any] | None = None) -> "Ampi":
        os.makedirs(job_dir, exist_ok=True)
        device = open_device(os.path.join(job_dir, "job.db"))
        Runtime.create_job(device, os.path.basename(os.path.abspath(job_dir)), world_size,
                           ctx_limit=ctx_limit, meta=meta)
        return cls(job_dir)

    def init(self, rank: int, **kw: Any) -> dict[str, Any]:
        return self.rt.init(rank, **kw)

    def finalize(self, note: str | None = None) -> dict[str, Any]:
        return self.rt.finalize(note)

    @property
    def rank(self) -> int | None:
        return self.rt.rank

    def size(self, comm: str | None = None) -> int:
        return self.rt.comms.get(comm or self.comm).size

    # -- point to point ----------------------------------------------------
    def send(self, dst: int, tag: int, payload: Any, *, comm: str | None = None,
             **kw: Any) -> dict[str, Any]:
        return self.rt.send(comm or self.comm, dst, tag, payload, **kw)

    def recv(self, src: int = AMPI_ANY_SOURCE, tag: int = AMPI_ANY_TAG, *,
             comm: str | None = None, **kw: Any) -> dict[str, Any] | None:
        return self.rt.recv(comm or self.comm, src, tag, **kw)

    def sendrecv(self, dst: int, send_tag: int, payload: Any, src: int, recv_tag: int, *,
                 comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return self.rt.sendrecv(comm or self.comm, dst, send_tag, payload, src, recv_tag, **kw)

    def probe(self, src: int = AMPI_ANY_SOURCE, tag: int = AMPI_ANY_TAG,
              comm: str | None = None) -> dict[str, Any] | None:
        return self.rt.probe(comm or self.comm, src, tag)

    # -- collectives -------------------------------------------------------
    def barrier(self, comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return _coll.barrier(self.rt, comm or self.comm, **kw)

    def bcast(self, root: int, payload: Any = None, *, comm: str | None = None,
              **kw: Any) -> dict[str, Any]:
        return _coll.bcast(self.rt, comm or self.comm, root, payload, **kw)

    def reduce(self, root: int, payload: Any, op: str, *, comm: str | None = None,
               **kw: Any) -> dict[str, Any]:
        return _coll.reduce_(self.rt, comm or self.comm, root, payload, op, **kw)

    def allreduce(self, payload: Any, op: str, *, comm: str | None = None,
                  **kw: Any) -> dict[str, Any]:
        return _coll.allreduce(self.rt, comm or self.comm, payload, op, **kw)

    def allgather(self, payload: Any, *, comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return _coll.allgather(self.rt, comm or self.comm, payload, **kw)

    def alltoall(self, payload: Any, *, comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return _coll.alltoall(self.rt, comm or self.comm, payload, **kw)

    def gather(self, root: int, payload: Any, *, comm: str | None = None,
               **kw: Any) -> dict[str, Any]:
        return _coll.gather(self.rt, comm or self.comm, root, payload, **kw)

    def scatter(self, root: int, blocks: dict[str, Any] | None, *, comm: str | None = None,
                **kw: Any) -> dict[str, Any]:
        return _coll.scatter(self.rt, comm or self.comm, root, blocks, **kw)

    def scan(self, payload: Any, op: str, *, comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return _coll.scan(self.rt, comm or self.comm, payload, op, **kw)

    # -- one-sided ---------------------------------------------------------
    def win_create(self, name: str, *, comm: str | None = None) -> dict[str, Any]:
        return self.rt.win_create(comm or self.comm, name)

    def put(self, win: str, key: str, value: Any, **kw: Any) -> dict[str, Any]:
        return self.rt.win_put(win, key, value, **kw)

    def get(self, win: str, key: str, **kw: Any) -> dict[str, Any]:
        return self.rt.win_get(win, key, **kw)

    def accumulate(self, win: str, key: str, value: Any, op: str) -> dict[str, Any]:
        return self.rt.win_accumulate(win, key, value, op)

    def fetch_and_op(self, win: str, key: str, delta: float = 1.0) -> dict[str, Any]:
        return self.rt.win_fetch_and_op(win, key, delta)

    def claim(self, win: str, key: str, note: str = "") -> dict[str, Any]:
        return self.rt.win_claim(win, key, note=note)

    def lock(self, win: str, key: str, *, mode: str = LOCK_EXCLUSIVE, **kw: Any) -> dict[str, Any]:
        return self.rt.win_lock(win, key, mode=mode, **kw)

    def unlock(self, lock_id: str) -> dict[str, Any]:
        return self.rt.win_unlock(lock_id)

    def fence(self, win: str, *, comm: str | None = None) -> dict[str, Any]:
        return self.rt.win_fence(win, comm or self.comm)

    # -- failure mitigation ------------------------------------------------
    def revoke(self, comm: str | None = None) -> dict[str, Any]:
        return self.rt.comm_revoke(comm or self.comm)

    def shrink(self, comm: str | None = None, new_name: str | None = None) -> dict[str, Any]:
        return self.rt.comm_shrink(comm or self.comm, new_name)

    def agree(self, value: bool, comm: str | None = None, **kw: Any) -> dict[str, Any]:
        return self.rt.comm_agree(comm or self.comm, value, **kw)

    def respawn(self, rank: int) -> dict[str, Any]:
        return self.rt.respawn(rank)

    def checkpoint(self, state: Any, label: str | None = None) -> dict[str, Any]:
        return self.rt.checkpoint(state, label)

    def restore(self, **kw: Any) -> dict[str, Any] | None:
        return self.rt.restore(**kw)

    # -- introspection -----------------------------------------------------
    def ranks(self) -> list[dict[str, Any]]:
        return self.rt.all_ranks()

    def ctx(self) -> dict[str, Any]:
        row = self.rt.rank_row()
        return {"rank": row["rank"], "used": row["ctx_used"], "limit": row["ctx_limit"],
                "peak": row["ctx_peak"], "free": row["ctx_limit"] - row["ctx_used"]}


__all__ = ["Ampi", "Runtime", "ALGO_AUTO"]
