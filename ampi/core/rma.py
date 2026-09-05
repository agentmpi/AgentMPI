"""Windows: shared state with the discipline MPI-3 gives one-sided operations.

Every multi-agent system grows a shared scratchpad --- a design document, a
glossary, an interface file, a task board --- and every one gets the concurrency
wrong the same way: two agents read it, both edit a private copy, and the second
write discards the first's work.  MPI-3's one-sided chapter supplies both the
vocabulary for that bug and the tools to avoid it, and the distinction it draws is
exactly the one such a harness needs and never articulates:

* ``put`` is a blind overwrite --- the operation that loses work;
* ``accumulate`` combines, so concurrent contributions cannot clobber;
* ``compare_and_swap`` is optimistic concurrency, and is how work is claimed.

Two departures from MPI.

**Locks are leased and carry a fencing token.**  An MPI process holding a window
lock cannot wander off; an executor can.  The lease stops a dead holder wedging the
job and the token stops a revived holder corrupting state after its lease expired.
Without both, the standard lease-expiry race is merely made unlikely.

**Enumeration is free and reading is not.**  ``win ls`` reports what exists, with
sizes and writers, for a few tokens per key.  That is what makes a blackboard
usable by an executor with a bounded context: it can see the shape of the shared
state and then spend its budget deliberately, instead of paying to discover that
it did not want the document.

The memory model is ``SEPARATE``, and for a reason that is not an implementation
artefact: an agent's private copy is the copy of the document sitting in its
context from ten minutes ago, and it *is* stale, because peers have edited it
since.  ``sync`` means "re-read before you edit", and a ``put`` based on a version
that is no longer current is recorded as a staleness violation --- counted and
attributed rather than prevented, because a harness that legitimately overwrites
must still be able to.
"""

from __future__ import annotations

import time
from typing import Any

from ..constants import DEFAULT_LOCK_TTL_S, DEFAULT_TIMEOUT_S
from ..errors import err
from ..tokens import count_tokens
from .ops import get_op
from .payload import canonical, summarise

__all__ = ["RmaMixin"]


class RmaMixin:
    # -- window lifecycle ---------------------------------------------------
    def _space(self, win: str, comm: str = "world") -> str:
        """Namespace a window by its communicator's private context.

        Two independently written sub-protocols must not be able to alias each
        other's shared state, for the same reason their messages must not: a
        library that cannot get a private name space is a library nobody can
        safely compose.
        """
        return f"win/{self.comm_context(comm)}/{win}"

    def win_create(self, win: str, *, comm: str = "world") -> dict[str, Any]:
        self.assert_identity()
        ok, _ = self.device.cas(
            "winreg",
            self._space(win, comm),
            0,
            {"name": win, "comm": comm, "created_by": self.rank, "epoch_no": 0},
            writer=self.rank,
        )
        self.trace("win.create", rank=self.rank, win=win, comm=comm, created=ok)
        return {"win": win, "comm": comm, "created": ok}

    def _require_win(self, win: str, comm: str) -> str:
        space = self._space(win, comm)
        if self.device.read("winreg", space) is None:
            raise err(
                "AMPI_ERR_WIN",
                f"no window {win!r} on communicator {comm!r}",
                hint="Run 'ampi win list', or create it with 'ampi win create'.",
                win=win,
            )
        return space

    def win_list_windows(self, *, comm: str = "world") -> list[dict[str, Any]]:
        prefix = f"win/{self.comm_context(comm)}/"
        return [
            self.device.read("winreg", c.key).value
            for c in self.device.keys("winreg", prefix=prefix)
        ]

    # -- data operations -----------------------------------------------------
    def put(
        self,
        win: str,
        key: str,
        value: Any,
        *,
        comm: str = "world",
        expect_version: int | None = None,
        lock_token: int | None = None,
    ) -> dict[str, Any]:
        """Write a cell.  With ``expect_version`` this is a conditional write."""
        self.assert_identity()
        self._fence_check()
        space = self._require_win(win, comm)
        self._check_fencing(space, key, lock_token)
        current = self.device.read(space, key)
        stale = (
            expect_version is None
            and current is not None
            and current.writer != self.rank
        )
        ok, cell = self.device.cas(
            space,
            key,
            expect_version,
            value,
            writer=self.rank,
            epoch=self._rankview().epoch,
            meta={"tokens": count_tokens(canonical(value)), "summary": summarise(value)},
        )
        if not ok:
            raise err(
                "AMPI_ERR_CONFLICT",
                f"{win}/{key} is at version {cell.version} (written by rank {cell.writer}), "
                f"not the {expect_version} you expected",
                hint="Re-read the cell and retry. Someone else wrote it first.",
                win=win,
                key=key,
                current_version=cell.version,
                current_writer=cell.writer,
            )
        if stale:
            self.trace("win.stale", rank=self.rank, win=win, key=key,
                       overwrote=current.writer, version=current.version)
        self.trace("win.put", rank=self.rank, win=win, key=key, version=cell.version,
                   tokens=cell.meta.get("tokens"))
        return {"win": win, "key": key, "version": cell.version, "overwrote": stale}

    def get(
        self,
        win: str,
        key: str,
        *,
        comm: str = "world",
        version: int | None = None,
        view: str = "",
        budget: int | None = None,
        out: str = "",
    ) -> dict[str, Any]:
        self.assert_identity()
        space = self._require_win(win, comm)
        cell = self.device.read(space, key, version=version)
        if cell is None:
            return {"win": win, "key": key, "present": False, "charged": 0}
        from .payload import apply_view

        value = cell.value
        if view:
            value = apply_view(value, view)
        if budget is not None:
            value = apply_view(value, f"headtail:{budget}")
        result: dict[str, Any] = {
            "win": win,
            "key": key,
            "present": True,
            "version": cell.version,
            "writer": cell.writer,
            "epoch": cell.epoch,
        }
        if out:
            import json
            from pathlib import Path

            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(
                value if isinstance(value, str) else json.dumps(value, indent=2), encoding="utf-8"
            )
            result.update(saved_to=out, charged=0)
            return result
        charged, degraded = self.charge(count_tokens(canonical(value)), what=f"win:{win}/{key}")
        if degraded:
            value = apply_view(value, degraded)
            result["degraded_to"] = degraded
        result.update(value=value, charged=charged)
        return result

    def accumulate(
        self, win: str, key: str, value: Any, *, op: str = "union", comm: str = "world"
    ) -> dict[str, Any]:
        """Atomically apply a runtime operator to a cell.

        The load-bearing operation.  It replaces read-modify-write --- three round
        trips and a race --- with one atomic step, so "union this finding into the
        shared findings" needs no lock at all.

        Lossy operators are refused: the read-modify-write happens inside an
        atomic section and an implementation cannot hold one across a model call.
        A harness needing judgement in the combine must lock, get, reason, put with
        an expected version, and unlock --- and accept the serialisation that
        implies.  Making that explicit is the point: a harness that puts a semantic
        critical section on its critical path has built a sequential program, and
        the trace will show it as lock wait time.
        """
        self.assert_identity()
        operator = get_op(op)
        if operator.evaluator != "runtime" or operator.fn is None:
            raise err(
                "AMPI_ERR_OP",
                f"accumulate needs a runtime operator; {op!r} is evaluated by an executor",
                hint="Use win lock/get/put with --expect-version, and accept the serialisation.",
                op=op,
            )
        space = self._require_win(win, comm)
        for attempt in range(64):
            cell = self.device.read(space, key)
            combined = value if cell is None else operator.fn(cell.value, value)
            ok, _ = self.device.cas(
                space, key, 0 if cell is None else cell.version, combined,
                writer=self.rank, epoch=self._rankview().epoch,
                meta={"tokens": count_tokens(canonical(combined)), "summary": summarise(combined)},
            )
            if ok:
                self.trace("win.accumulate", rank=self.rank, win=win, key=key, op=op,
                           attempts=attempt + 1)
                return {"win": win, "key": key, "op": op, "value": combined, "attempts": attempt + 1}
            time.sleep(0.01 * (attempt + 1))
        raise err("AMPI_ERR_CONFLICT", f"accumulate on {win}/{key} lost 64 races", win=win, key=key)

    def compare_and_swap(
        self, win: str, key: str, expect: Any, value: Any, *, comm: str = "world"
    ) -> dict[str, Any]:
        """Conditional write on the cell's *content*.

        How work is claimed: a task cell holds ``unclaimed`` and whichever executor
        swaps it wins.  One operation eliminates an entire class of duplicated-work
        bug, and unlike a lock it cannot be held by a dead executor.
        """
        self.assert_identity()
        space = self._require_win(win, comm)
        cell = self.device.read(space, key)
        current = cell.value if cell else None
        if current != expect:
            return {"win": win, "key": key, "swapped": False, "current": current}
        ok, new = self.device.cas(
            space, key, 0 if cell is None else cell.version, value,
            writer=self.rank, epoch=self._rankview().epoch,
            meta={"tokens": count_tokens(canonical(value)), "summary": summarise(value)},
        )
        self.trace("win.cas", rank=self.rank, win=win, key=key, swapped=ok)
        return {"win": win, "key": key, "swapped": ok, "version": new.version,
                "current": new.value if ok else self.device.read(space, key).value}

    def claim(self, win: str, key: str, *, comm: str = "world", note: str = "") -> dict[str, Any]:
        """Claim an unclaimed work item.  Sugar over compare-and-swap.

        An absent cell counts as unclaimed, so nobody has to post a cell per item
        before the population may claim them: at 128 ranks over four machines,
        posting forty-eight cells one push at a time from the root held everyone
        else at a barrier for most of an hour.
        """
        space = self._require_win(win, comm)
        expect = None if self.device.read(space, key) is None else "unclaimed"
        got = self.compare_and_swap(
            win, key, expect, {"claimed_by": self.rank, "at": self.device.clock(), "note": note},
            comm=comm,
        )
        return {"win": win, "key": key, "claimed": got["swapped"], "holder": got.get("current")}

    def fetch_and_op(
        self, win: str, key: str, *, op: str = "sum", value: Any = 1, comm: str = "world"
    ) -> dict[str, Any]:
        space = self._require_win(win, comm)
        operator = get_op(op)
        for _ in range(64):
            cell = self.device.read(space, key)
            before = cell.value if cell else operator.identity
            after = operator.fn(before, value)  # type: ignore[misc]
            ok, _ = self.device.cas(
                space, key, 0 if cell is None else cell.version, after, writer=self.rank,
                meta={"tokens": count_tokens(canonical(after)), "summary": summarise(after)},
            )
            if ok:
                return {"win": win, "key": key, "before": before, "after": after}
        raise err("AMPI_ERR_CONFLICT", f"fetch_and_op on {win}/{key} lost 64 races")

    # -- enumeration and history ---------------------------------------------
    def win_ls(self, win: str, *, prefix: str = "", comm: str = "world") -> dict[str, Any]:
        """Enumerate keys with sizes and provenance, without reading bodies."""
        self.assert_identity()
        space = self._require_win(win, comm)
        cells = self.device.keys(space, prefix=prefix)
        items = [
            {
                "key": c.key,
                "version": c.version,
                "writer": c.writer,
                "epoch": c.epoch,
                "tokens": c.meta.get("tokens", 0),
                "summary": c.meta.get("summary", ""),
            }
            for c in cells
        ]
        charged, _ = self.charge(min(40 * len(items), 2000), what="win.ls")
        return {"win": win, "keys": len(items), "items": items,
                "total_tokens": sum(i["tokens"] for i in items), "charged": charged}

    def win_history(self, win: str, key: str, *, comm: str = "world", limit: int = 20) -> dict[str, Any]:
        space = self._require_win(win, comm)
        return {
            "win": win,
            "key": key,
            "versions": [
                {"version": c.version, "writer": c.writer, "epoch": c.epoch, "ts": c.ts,
                 "tokens": c.meta.get("tokens", 0), "summary": c.meta.get("summary", "")}
                for c in self.device.history(space, key, limit=limit)
            ],
        }

    # -- synchronisation ------------------------------------------------------
    def win_fence(
        self, win: str, label: str, *, comm: str = "world", timeout: float = DEFAULT_TIMEOUT_S,
        quorum: float = 1.0,
    ) -> dict[str, Any]:
        """Close an epoch: a barrier plus the guarantee that the phase's writes are in.

        This is what turns a blackboard --- notoriously hard to reason about ---
        into a sequence of bulk-synchronous supersteps.  It is Valiant's BSP
        superstep boundary, and it is the cheapest way to make shared agent state
        explicable.
        """
        self._require_win(win, comm)
        out = self.barrier(f"winfence:{win}:{label}", comm=comm, timeout=timeout, quorum=quorum)
        reg = self.device.read("winreg", self._space(win, comm))
        self.device.cas(
            "winreg", self._space(win, comm), None,
            {**reg.value, "epoch_no": reg.value.get("epoch_no", 0) + 1}, writer=self.rank,
        )
        self.trace("win.fence", rank=self.rank, win=win, label=label)
        return {"win": win, "label": label, "epoch_no": reg.value.get("epoch_no", 0) + 1, **out}

    def win_lock(
        self,
        win: str,
        key: str,
        *,
        comm: str = "world",
        mode: str = "exclusive",
        ttl: float = DEFAULT_LOCK_TTL_S,
        timeout: float = 0.0,
    ) -> dict[str, Any]:
        self.assert_identity()
        space = self._require_win(win, comm)
        deadline = time.time() + timeout
        while True:
            lease = self.device.lease(space, key, holder=self.rank, mode=mode, ttl=ttl)
            if lease is not None:
                self.trace("win.lock", rank=self.rank, win=win, key=key, token=lease.token)
                return {"win": win, "key": key, "lock_id": lease.lock_id, "token": lease.token,
                        "expires_at": lease.expires_at, "mode": mode}
            held = [lk for lk in self.device.leases(space) if lk.key == key]
            if time.time() >= deadline:
                holder = held[0] if held else None
                raise err(
                    "AMPI_ERR_LOCK_BUSY",
                    f"{win}/{key} is locked by rank {holder.holder if holder else '?'} "
                    f"until {holder.expires_at if holder else 0:.0f}",
                    hint="Retry after the lease expires, or use accumulate, which needs no lock.",
                    win=win, key=key,
                    holder=holder.holder if holder else None,
                    expires_at=holder.expires_at if holder else None,
                )
            self.touch()
            time.sleep(0.1)

    def win_unlock(self, lock_id: str) -> dict[str, Any]:
        ok = self.device.release(lock_id, self.rank)
        self.trace("win.unlock", rank=self.rank, lock_id=lock_id, released=ok)
        if not ok:
            raise err(
                "AMPI_ERR_STALE_LEASE",
                f"lock {lock_id} is no longer held by rank {self.rank}",
                hint="Your lease expired and was reclaimed. Re-acquire before writing.",
            )
        return {"lock_id": lock_id, "released": True}

    def _check_fencing(self, space: str, key: str, token: int | None) -> None:
        """Reject a write bearing a stale fencing token."""
        held = [lk for lk in self.device.leases(space) if lk.key == key]
        if not held:
            return
        current = max(lk.token for lk in held)
        if token is not None and token < current:
            raise err(
                "AMPI_ERR_STALE_LEASE",
                f"write to {key} carries fencing token {token} but the current lock holds "
                f"{current}: your lease expired and someone else has the cell",
                hint="Re-acquire the lock and re-read before writing. Your write was rejected.",
                token=token, current=current,
            )
        if token is None and any(lk.holder != self.rank for lk in held):
            other = next(lk for lk in held if lk.holder != self.rank)
            raise err(
                "AMPI_ERR_LOCK_BUSY",
                f"{key} is locked by rank {other.holder}",
                hint="Acquire the lock, or pass --lock-token if you hold it.",
                holder=other.holder,
            )
