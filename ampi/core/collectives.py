"""Collective operations: labelled, quorum-capable, and fault-tolerant.

Three departures from MPI, each forced by something an executor does that a
process does not.

**Collectives are identified by label, not by program order.**  MPI identifies
the k-th collective on a communicator by the k-th call, which is safe when the
caller is a compiled program.  An executor's program order is not reliable: it
retries a command, skips a step, or reorders two independent calls.  Relying on
order silently mismatches ranks, and named collectives were the single largest
robustness improvement in the whole interface.  A retried call rejoins the
caller's still-open collective rather than starting a new one.

**Gather returns a manifest, not a concatenation.**  This is where naive harnesses
die.  At ``p=128`` with 4000-token contributions, an inlining allgather charges one
rank 508,000 tokens; a handle-based one charges 5,080.  Concatenation is not a
reasonable default at any interesting ``p``, so the default is one entry per
contributor --- rank, handle, token count, summary --- and the caller materialises
what it needs.

**A collective may carry a quorum.**  Executor completion times are heavy-tailed,
so a strict barrier over ``p`` executors waits for the maximum of ``p`` heavy-tailed
samples.  Reaching quorum *releases* a barrier but must not *close* it: a straggler
arriving afterwards still passes through, because a quorum barrier that closed
would guarantee that precisely the slowest ranks fail.  This is the trade
stale-synchronous parameter servers make, expressed as a collective rather than as
a training-loop hack.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..constants import DEFAULT_QUORUM, DEFAULT_TIMEOUT_S
from ..errors import AmpiError, err
from ..tokens import count_tokens
from .algorithms import select_algorithm
from .ops import (
    LIFT,
    Op,
    arbitrate,
    check_invariant,
    conflicts_of,
    finalise_vote,
    fold,
    get_op,
    identity_like,
    serial_fold,
)
from .payload import Contract, apply_view, canonical, check_contract

__all__ = ["CollectiveMixin"]

_POLL_S = 0.05
_POLL_MAX_S = 1.0


class CollectiveMixin:
    # -- participation ------------------------------------------------------
    def _coll_key(self, comm: str, label: str) -> str:
        return f"{self.comm_context(comm)}#{label}"

    def _join_collective(
        self,
        comm: str,
        label: str,
        kind: str,
        *,
        payload: Any = None,
        root: int | None = None,
        schema: str = "",
    ) -> dict[str, Any]:
        """Register the caller's arrival, idempotently.

        Write-ahead: the participation record lands *before* any dependent work,
        so a crash between arriving and contributing leaves evidence a peer can
        act on rather than a silence indistinguishable from never having called.
        """
        ctx = self.comm_context(comm)
        mine = self.device.scan(
            "coll",
            {"comm": ctx, "label": label, "rank": self.rank, "run": self.manifest.job_id},
            limit=1,
        )
        handle = ""
        tokens = 0
        if payload is not None:
            p = self.put_payload(payload, schema=schema)
            handle, tokens = p.envelope.handle, p.envelope.tokens

        if mine:
            rec = mine[0]
            if rec.get("kind") != kind:
                raise err(
                    "AMPI_ERR_COLL_MISMATCH",
                    f"rank {self.rank} already joined collective {label!r} as {rec.get('kind')!r} "
                    f"and is now calling it as {kind!r}",
                    hint="Give each collective a distinct label. Run 'ampi doctor'.",
                    label=label,
                )
            if payload is not None and not rec.get("handle"):
                self.device.update("coll", rec["seq"], {"handle": handle, "tokens": tokens})
                rec["handle"], rec["tokens"] = handle, tokens
            # A re-entry: the same rank, in a later process or a retried command,
            # arriving at a collective it already joined.  Its wait is measured
            # from *this* arrival, not from the original one.  Whether the
            # completion is a *replay* is decided when it completes: only if the
            # collective had already released before the rank came back.  A rank
            # retrying an active timeout while its peers are still arriving is a
            # participant, and its completion counts.
            rec["reentered_at"] = self.device.clock()
            return rec

        rec = {
            "comm": ctx,
            "label": label,
            "rank": self.rank,
            "state": "joined",
            "run": self.manifest.job_id,
            "gen": self.comm_info(comm)["gen"],
            "kind": kind,
            "root": root,
            "handle": handle,
            "tokens": tokens,
            "joined_at": self.device.clock(),
        }
        seq = self.device.append("coll", rec)
        rec["seq"] = seq
        self.trace("coll.join", rank=self.rank, label=label, kind=kind, comm=comm, tokens=tokens)
        return rec

    def _coll_done(
        self,
        kind: str,
        rec: dict[str, Any] | None,
        *,
        comm: str,
        label: str,
        **fields: Any,
    ) -> None:
        """Record a collective's completion with the fields an analysis needs.

        Every collective traced its own ad-hoc subset before this existed, and the
        omissions were not distributed randomly: ``bcast`` recorded nothing at all,
        and no collective recorded how long its caller had been blocked.  Both
        gaps are invisible in the trace --- a collective that emits no event is
        indistinguishable from one that never ran --- and both defeat the analysis
        that a long agent run exists to produce.  Coordination cost *is* the
        measurement, and it cannot be reconstructed from a completion timestamp
        alone: what a reader needs is the interval between a rank arriving and the
        collective releasing it, which only the runtime knows.

        ``waited_s`` is per rank and additive across ranks; the spread between the
        first and last rank to record a call is the synchronisation skew, and is
        recovered by the analysis from the timestamps rather than recorded here,
        because no single rank can observe it.
        """
        waited = None
        if rec is not None and rec.get("joined_at") is not None:
            since = float(rec.get("reentered_at") or rec["joined_at"])
        waited = round(max(0.0, self.device.clock() - since), 4)
        if rec is not None and rec.get("reentered_at") is not None:
            # A replay is a re-entry into a collective that had closed before the
            # rank came back, which is the case exactly when every arrival predates
            # the re-entry.  An analysis must not read a restarted rank's instant
            # re-entry into a long-closed barrier as hours of blocking, or as a
            # second invocation; nor must it drop the completion of a rank whose
            # retried call waited for peers that arrived after it.
            try:
                arrivals = self.device.scan(
                    "coll", {"comm": self.comm_context(comm), "label": label,
                             "run": self.manifest.job_id})
                last_arrival = max((float(a.get("joined_at") or 0) for a in arrivals),
                                   default=0.0)
            except AmpiError:  # pragma: no cover - a communicator mid-teardown
                last_arrival = 0.0
            if last_arrival <= float(rec["reentered_at"]):
                fields = {**fields, "replayed": True}
        try:
            size = len(self.comm_members(comm))
        except AmpiError:  # pragma: no cover - a revoked communicator mid-teardown
            size = None
        self.trace(
            kind,
            rank=self.rank,
            label=label,
            comm=comm,
            size=size,
            waited_s=waited,
            **fields,
        )

    def _participants(self, comm: str, label: str) -> list[dict[str, Any]]:
        return self.device.scan(
            "coll",
            {"comm": self.comm_context(comm), "label": label, "run": self.manifest.job_id},
            order_by="rank",
        )

    def _await_participation(
        self,
        comm: str,
        label: str,
        *,
        kind: str,
        quorum: float,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], list[int]]:
        """Wait for enough members, dropping ranks that fail while we wait.

        Returns the arrived participants and the dropped ranks.  A failed rank's
        subtree is dropped and the omission recorded, rather than the collective
        hanging: the survivors' contributions are worth more than a perfect one.
        """
        members = set(self.comm_members(comm))
        deadline = time.time() + timeout
        wait = _POLL_S
        while True:
            self.touch()
            self.detect_failures()
            self._check_revoked(comm)
            dropped = sorted(
                r for r in members if self._rankview(r).state in ("failed", "fenced")
            )
            live = members - set(dropped)
            arrived = [p for p in self._participants(comm, label) if p["rank"] in live]
            need = max(1, math.ceil(quorum * len(live))) if live else 0
            mismatched = {p["rank"]: p.get("kind") for p in arrived if p.get("kind") != kind}
            if mismatched:
                raise err(
                    "AMPI_ERR_COLL_MISMATCH",
                    f"ranks {sorted(mismatched)} joined {label!r} as {sorted(set(mismatched.values()))} "
                    f"while rank {self.rank} calls it as {kind!r}",
                    hint="Run 'ampi doctor'. Every member must call the same collective.",
                    label=label,
                    mismatched=mismatched,
                )
            if len(arrived) >= need:
                if dropped:
                    self.trace("coll.dropped", label=label, dropped=dropped, rank=self.rank)
                return arrived, dropped
            if time.time() >= deadline:
                missing = sorted(live - {p["rank"] for p in arrived})
                raise err(
                    "AMPI_ERR_TIMEOUT",
                    f"collective {label!r} has {len(arrived)}/{len(live)} live members after "
                    f"{timeout:.0f}s; rank(s) {missing} have not arrived",
                    hint="Re-issue the identical command to resume the wait. "
                    "'ampi doctor' names what each missing rank is doing.",
                    label=label,
                    missing=missing,
                    arrived=[p["rank"] for p in arrived],
                )
            time.sleep(wait)
            wait = min(_POLL_MAX_S, wait * 1.6)

    @staticmethod
    def _manifest_of(participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "rank": p["rank"],
                "handle": p.get("handle", ""),
                "tokens": p.get("tokens", 0),
                "summary": (p.get("summary") or ""),
            }
            for p in participants
        ]

    # -- barrier ------------------------------------------------------------
    def barrier(
        self,
        label: str,
        *,
        comm: str = "world",
        quorum: float = DEFAULT_QUORUM,
        timeout: float = DEFAULT_TIMEOUT_S,
        policy: str = "wait",
    ) -> dict[str, Any]:
        """Synchronise.  ``policy`` declares what a missing participant *means*.

        An unconditional barrier is a liveness bug waiting to happen: the
        probability that all ``p`` executors arrive within a fixed window falls off
        with ``p``, and "the agents are waiting for each other" is the most
        frequently reported pathology in multi-agent postmortems.  The policy is
        the harness author's declaration --- a missing chapter degrades a
        translation, whereas a missing module kills a build.
        """
        self.assert_identity()
        self._fence_check()
        joined = self._join_collective(comm, label, "barrier")
        try:
            arrived, dropped = self._await_participation(
                comm, label, kind="barrier", quorum=quorum, timeout=timeout
            )
        except AmpiError as exc:
            if exc.cls_name != "AMPI_ERR_TIMEOUT" or policy == "wait":
                raise
            missing = exc.detail.get("missing", [])
            if policy == "raise":
                raise
            if policy == "proceed":
                self.trace("barrier.proceed", label=label, absent=missing, rank=self.rank)
                return {"label": label, "released": True, "absent": missing, "policy": policy}
            if policy in ("shrink", "revoke"):
                for r in missing:
                    view = self._rankview(r)
                    if view.state not in ("failed", "fenced"):
                        self._convict(view, "lease_expired")
                if policy == "revoke":
                    self.comm_revoke(comm)
                    raise
                new = self.comm_shrink(comm)
                return {"label": label, "released": True, "absent": missing,
                        "policy": policy, "comm": new["name"]}
            raise
        self._coll_done(
            "barrier", joined, comm=comm, label=label,
            arrived=len(arrived), dropped=dropped, quorum=quorum, policy=policy,
        )
        return {
            "label": label,
            "released": True,
            "arrived": [p["rank"] for p in arrived],
            "dropped": dropped,
            "quorum": quorum,
            "late": self.rank not in [p["rank"] for p in arrived],
        }

    # -- broadcast / scatter -------------------------------------------------
    def bcast(
        self,
        label: str,
        *,
        payload: Any = None,
        root: int = 0,
        comm: str = "world",
        timeout: float = DEFAULT_TIMEOUT_S,
        materialize: bool = False,
        view: str = "",
        out: str = "",
    ) -> dict[str, Any]:
        """Root publishes once; every member reads the same handle.

        A tree broadcast forwards *handles*, never regenerated content.  An
        implementation in which an interior rank retransmits text it has restated
        does not conform: it degrades with depth like a game of telephone, which
        would confine the protocol to flat, root-centred patterns --- precisely the
        ones that make the root a bottleneck.  Immutability turns depth from a
        quality risk into a pure latency win.
        """
        self.assert_identity()
        me = self.comm_rank(comm)
        if me == root:
            if payload is None:
                raise err("AMPI_ERR_ARG", "the root of a broadcast must supply a payload")
            joined = self._join_collective(comm, label, "bcast", payload=payload, root=root)
        else:
            joined = self._join_collective(comm, label, "bcast", root=root)
        world_root = self.comm_members(comm)[root]
        self._await(
            lambda: any(
                p["rank"] == world_root and p.get("handle")
                for p in self._participants(comm, label)
            ),
            timeout=timeout,
            what=f"the root of broadcast {label!r} to publish",
        )
        rec = next(p for p in self._participants(comm, label) if p["rank"] == world_root)
        out_rec = self._take(rec, materialize=materialize or me == root, view=view, out=out,
                             extra={"label": label, "root": root})
        self._coll_done(
            "bcast", joined, comm=comm, label=label, root=root,
            tokens=rec.get("tokens", 0), charged=out_rec.get("charged", 0),
            materialized=bool(out_rec.get("body") is not None),
        )
        return out_rec

    def scatter(
        self,
        label: str,
        *,
        payload: list[Any] | None = None,
        root: int = 0,
        comm: str = "world",
        timeout: float = DEFAULT_TIMEOUT_S,
        materialize: bool = True,
        view: str = "",
        out: str = "",
        contract: Contract | dict | str | None = None,
    ) -> dict[str, Any]:
        """Root distributes one slice per member.

        The contract is checked against the *kept slice*, never against the block
        an interior node forwards.  That distinction matters: a slice that
        identifies which rank it is for turns a misrouted block into a loud error
        at the receiver rather than a plausible wrong answer three phases later.
        """
        self.assert_identity()
        members = self.comm_members(comm)
        me = self.comm_rank(comm)
        if me == root:
            if payload is None or len(payload) != len(members):
                raise err(
                    "AMPI_ERR_ARG",
                    f"scatter root must supply exactly {len(members)} slices, "
                    f"got {0 if payload is None else len(payload)}",
                    hint="Use scatterv semantics by padding with nulls if sizes differ.",
                )
            joined = self._join_collective(comm, label, "scatter", payload=payload, root=root)
        else:
            joined = self._join_collective(comm, label, "scatter", root=root)
        world_root = members[root]
        self._await(
            lambda: any(
                p["rank"] == world_root and p.get("handle")
                for p in self._participants(comm, label)
            ),
            timeout=timeout,
            what=f"the root of scatter {label!r} to publish",
        )
        rec = next(p for p in self._participants(comm, label) if p["rank"] == world_root)
        whole = self.get_body(rec["handle"])
        slice_ = whole[me]
        tokens = count_tokens(canonical(slice_))
        if view:
            slice_ = apply_view(slice_, view)
            tokens = count_tokens(canonical(slice_))
        ctr = Contract.parse(contract)
        violations = check_contract(slice_, ctr, subs={"rank": me, "world_rank": self.rank})
        if violations:
            raise err(
                "AMPI_ERR_TYPE",
                f"the slice delivered to rank {me} violates the declared contract: {violations[0]}",
                hint="A self-identifying slice that fails here has been misrouted; "
                "do not proceed as if it were yours.",
                violations=violations,
            )
        out_rec: dict[str, Any] = {"label": label, "root": root, "index": me}
        if out:
            import json
            from pathlib import Path

            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(
                slice_ if isinstance(slice_, str) else json.dumps(slice_, indent=2), encoding="utf-8"
            )
            out_rec.update(saved_to=out, charged=0)
            self._coll_done("scatter", joined, comm=comm, label=label, root=root, tokens=tokens)
            return out_rec
        if materialize:
            charged, degraded = self.charge(tokens, what="scatter")
            if degraded:
                slice_ = apply_view(slice_, degraded)
                out_rec["degraded_to"] = degraded
            out_rec.update(body=slice_, charged=charged)
        else:
            out_rec.update(handle=self.put_payload(slice_).envelope.handle, tokens=tokens, charged=0)
        self._coll_done(
            "scatter", joined, comm=comm, label=label, root=root,
            tokens=tokens, charged=out_rec.get("charged", 0),
        )
        return out_rec

    def _take(
        self, rec: dict[str, Any], *, materialize: bool, view: str, out: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        handle = rec["handle"]
        result: dict[str, Any] = {"handle": handle, "tokens": rec.get("tokens", 0), **extra}
        if out:
            import json
            from pathlib import Path

            body = self.get_body(handle)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(
                body if isinstance(body, str) else json.dumps(body, indent=2), encoding="utf-8"
            )
            result.update(saved_to=out, charged=0)
            return result
        if not materialize and not view:
            result["charged"] = self.charge(40, what="envelope")[0]
            result["next"] = f"ampi obj get {handle} --view head:400"
            return result
        body = self.get_body(handle)
        if view:
            body = apply_view(body, view)
        charged, degraded = self.charge(count_tokens(canonical(body)), what="bcast")
        if degraded:
            body = apply_view(body, degraded)
            result["degraded_to"] = degraded
        result.update(body=body, charged=charged)
        return result

    # -- gather / allgather ---------------------------------------------------
    def gather(
        self,
        label: str,
        *,
        payload: Any = None,
        root: int = 0,
        comm: str = "world",
        quorum: float = DEFAULT_QUORUM,
        timeout: float = DEFAULT_TIMEOUT_S,
        materialize: bool = False,
        view: str = "",
        budget: int | None = None,
        everyone: bool = False,
    ) -> dict[str, Any]:
        """Collect contributions.  Returns a manifest unless asked otherwise.

        ``everyone`` makes it an allgather.  ``view`` applies a per-contribution
        projection, which is the middle ground between a manifest and a
        concatenation and the one most harnesses actually want.
        """
        self.assert_identity()
        kind = "allgather" if everyone else "gather"
        joined = self._join_collective(comm, label, kind, payload=payload, root=root)
        me = self.comm_rank(comm)
        if not everyone and me != root:
            # A non-root contributor to a gather is done the moment it has
            # contributed.  It still records the event, because a rank that
            # contributed and a rank that never arrived must not look the same in
            # the trace --- that distinction is the whole of what a gather's
            # ``absent`` list means.
            self._coll_done(
                kind, joined, comm=comm, label=label, root=root, contributed=True,
            )
            return {"label": label, "contributed": True, "root": root}
        arrived, dropped = self._await_participation(
            comm, label, kind=kind, quorum=quorum, timeout=timeout
        )
        manifest = self._manifest_of(arrived)
        result: dict[str, Any] = {
            "label": label,
            "contributors": len(manifest),
            "dropped": dropped,
            "manifest": manifest,
            "total_tokens": sum(m["tokens"] for m in manifest),
        }
        if materialize or view:
            bodies = []
            for m in manifest:
                if not m["handle"]:
                    continue
                b = self.get_body(m["handle"])
                if view:
                    b = apply_view(b, view)
                if budget is not None:
                    b = apply_view(b, f"headtail:{budget}")
                bodies.append({"rank": m["rank"], "body": b})
            charged, degraded = self.charge(count_tokens(canonical(bodies)), what=kind)
            result.update(bodies=bodies, charged=charged)
            if degraded:
                result["degraded_to"] = degraded
        else:
            result["charged"] = self.charge(40 * len(manifest), what="manifest")[0]
            result["next"] = "ampi obj get HANDLE --view head:400  # per contribution"
        self._coll_done(
            kind, joined, comm=comm, label=label, root=root,
            contributors=len(manifest), dropped=dropped, quorum=quorum,
            tokens=result["total_tokens"], charged=result.get("charged", 0),
        )
        return result

    def allgather(self, label: str, **kw: Any) -> dict[str, Any]:
        kw["everyone"] = True
        return self.gather(label, **kw)

    def alltoall(
        self,
        label: str,
        *,
        payload: list[Any],
        comm: str = "world",
        timeout: float = DEFAULT_TIMEOUT_S,
        quorum: float = DEFAULT_QUORUM,
    ) -> dict[str, Any]:
        """Every rank sends one item to every rank.

        Costs ``p(p-1)`` messages and is the natural expression of all-way peer
        review.  Harnesses should prefer a neighbourhood collective on a review
        topology unless the full cross-product genuinely carries information: a
        full group conversation over ``p`` agents costs ``O(p^2 n)`` tokens where a
        review graph costs ``O(pn)``, and refusing to make that restriction is why
        group-chat architectures do not scale.
        """
        self.assert_identity()
        members = self.comm_members(comm)
        if len(payload) != len(members):
            raise err("AMPI_ERR_ARG", f"alltoall needs exactly {len(members)} items")
        joined = self._join_collective(comm, label, "alltoall", payload=payload)
        arrived, dropped = self._await_participation(
            comm, label, kind="alltoall", quorum=quorum, timeout=timeout
        )
        me = self.comm_rank(comm)
        received = []
        for p in arrived:
            block = self.get_body(p["handle"])
            received.append({"from": p["rank"], "item": block[me]})
        charged, _ = self.charge(count_tokens(canonical(received)), what="alltoall")
        self._coll_done(
            "alltoall", joined, comm=comm, label=label,
            received=len(received), dropped=dropped, charged=charged,
        )
        return {"label": label, "received": received, "dropped": dropped, "charged": charged}

    # -- reductions -----------------------------------------------------------
    def reduce(
        self,
        label: str,
        *,
        payload: Any = None,
        op: str = "concat",
        root: int = 0,
        comm: str = "world",
        everyone: bool = False,
        algorithm: str | None = None,
        quorum: float = DEFAULT_QUORUM,
        timeout: float = DEFAULT_TIMEOUT_S,
        operand_budget: int | None = None,
        materialize: bool = True,
    ) -> dict[str, Any]:
        """Reduce contributions with ``op``.

        For a runtime operator the fold happens in the journal in one round with
        no messages --- the in-network aggregation regime, and the right answer
        whenever the operator is one the implementation can apply.  For an agent
        operator this returns a *merge directive*: the operator is the caller, so
        it cannot complete inside one call, and the schedule is driven by
        :meth:`op_commit`.
        """
        self.assert_identity()
        operator = get_op(op)
        kind = "allreduce" if everyone else "reduce"
        joined = self._join_collective(comm, label, kind, payload=payload, root=root)
        arrived, dropped = self._await_participation(
            comm, label, kind=kind, quorum=quorum, timeout=timeout
        )
        me = self.comm_rank(comm)
        if not everyone and me != root and operator.evaluator == "runtime":
            self._coll_done(
                kind, joined, comm=comm, label=label, root=root, op=op, contributed=True,
            )
            return {"label": label, "contributed": True, "root": root}

        decision = select_algorithm(
            kind,
            len(arrived),
            tokens=max((p.get("tokens", 0) for p in arrived), default=0),
            op=operator,
            root=root,
            override=algorithm,
        )

        if operator.evaluator == "agent":
            out = self._agent_reduce(
                comm, label, operator, arrived, dropped, decision, root=root,
                everyone=everyone, timeout=timeout, operand_budget=operand_budget,
            )
            # A merge directive is not a completion: the caller still owes an
            # operator application.  The stored result is, and without this the
            # agent-evaluated reductions --- the expensive ones --- were missing
            # from the trace's collective record entirely.
            if out.get("status") == "done":
                self._coll_done(
                    kind, joined, comm=comm, label=label, root=root, op=op,
                    algorithm=decision.chosen, rule=decision.rule,
                    contributors=len(arrived), dropped=dropped,
                    conflicts=len(out.get("conflicts") or {}) or None,
                )
            return out

        values = [self.get_body(p["handle"]) for p in arrived if p.get("handle")]
        folded = fold(
            operator,
            values,
            algorithm="chain" if decision.chosen == "flat" else "binomial",
            root=0,
        )
        value = folded["value"]
        if op == "vote":
            value = finalise_vote(value)
        violations = check_invariant(operator, values, value)
        result: dict[str, Any] = {
            "label": label,
            "op": op,
            "algorithm": decision.chosen,
            "rule": decision.rule,
            "contributors": len(values),
            "dropped": dropped,
            "fold_depth": folded["depth"],
            "applications": folded["applications"],
        }
        conflicts = conflicts_of(value)
        if conflicts:
            result["conflicts"] = conflicts
            result["next"] = f"ampi op arbitrate --label {label}"
        if violations:
            self.trace("op.invariant", label=label, violations=violations)
            result["invariant_violations"] = violations
        handle = self.put_payload(value).envelope.handle
        result["handle"] = handle
        self.device.cas(
            "collresult", self._coll_key(comm, label), None,
            {"handle": handle, "op": op, "algorithm": decision.chosen}, writer=self.rank,
        )
        if materialize:
            charged, degraded = self.charge(count_tokens(canonical(value)), what="reduce")
            result["value"] = apply_view(value, degraded) if degraded else value
            result["charged"] = charged
        self._coll_done(
            kind, joined, comm=comm, label=label, root=root, op=op,
            algorithm=decision.chosen, rule=decision.rule,
            depth=folded["depth"], applications=folded["applications"],
            contributors=len(values), dropped=dropped,
            conflicts=len(conflicts) or None, charged=result.get("charged", 0),
        )
        if violations:
            raise err(
                "AMPI_ERR_INVARIANT",
                f"the reduction's declared invariant does not hold: {violations[0]}",
                hint="Local merges cannot maintain a global property. Inspect the result "
                f"at handle {handle} and re-run the affected subtree.",
                label=label,
                violations=violations,
                handle=handle,
            )
        return result

    def allreduce(self, label: str, **kw: Any) -> dict[str, Any]:
        kw["everyone"] = True
        return self.reduce(label, **kw)

    def scan(
        self,
        label: str,
        *,
        payload: Any,
        op: str = "concat",
        comm: str = "world",
        exclusive: bool = False,
        timeout: float = DEFAULT_TIMEOUT_S,
        quorum: float = DEFAULT_QUORUM,
    ) -> dict[str, Any]:
        """Prefix reduction over ranks ``0..i`` (inclusive) or ``0..i-1``.

        The most under-used collective for agent work.  A task with sequential
        dependence but parallel bulk --- translate chapter *i* consistently with
        the names established in ``0..i-1``, write a section that must not
        contradict earlier ones --- *is* a prefix computation.  A harness that runs
        it strictly sequentially pays ``p`` executor latencies; one that ignores
        the dependence produces inconsistent output.
        """
        self.assert_identity()
        operator = get_op(op)
        if operator.evaluator == "agent":
            raise err(
                "AMPI_ERR_UNSUPPORTED",
                "agent-evaluated scan is not provided in AgentMPI/1.0",
                hint="Use a chain of agent reductions, or a runtime operator.",
            )
        kind = "exscan" if exclusive else "scan"
        joined = self._join_collective(comm, label, kind, payload=payload)
        arrived, dropped = self._await_participation(
            comm, label, kind=kind, quorum=quorum, timeout=timeout
        )
        members = self.comm_members(comm)
        me = self.comm_rank(comm)
        by_rank = {p["rank"]: p for p in arrived}
        prefix = [
            self.get_body(by_rank[w]["handle"])
            for w in members[: me + (0 if exclusive else 1)]
            if w in by_rank and by_rank[w].get("handle")
        ]
        value = serial_fold(operator, prefix) if prefix else identity_like(operator, payload)
        charged, _ = self.charge(count_tokens(canonical(value)), what=kind)
        self._coll_done(
            kind, joined, comm=comm, label=label, op=op,
            prefix=len(prefix), dropped=dropped, charged=charged,
        )
        return {
            "label": label,
            "op": op,
            "value": value,
            "prefix_size": len(prefix),
            "dropped": dropped,
            "charged": charged,
        }

    def exscan(self, label: str, **kw: Any) -> dict[str, Any]:
        kw["exclusive"] = True
        return self.scan(label, **kw)

    # -- agent operators: the continuation protocol ---------------------------
    def _agent_reduce(
        self,
        comm: str,
        label: str,
        operator: Op,
        arrived: list[dict[str, Any]],
        dropped: list[int],
        decision: Any,
        *,
        root: int,
        everyone: bool,
        timeout: float,
        operand_budget: int | None,
    ) -> dict[str, Any]:
        """Drive an agent-evaluated reduction by merge directives.

        An agent operator cannot complete inside one call, because the operator
        *is* the caller.  The runtime therefore owns the schedule and the user owns
        the operator --- MPI's division exactly --- and the schedule position is
        checkpointed so a timeout, crash, or replacement resumes at the same step
        rather than restarting the tree.
        """
        key = self._coll_key(comm, label)
        done = self.device.read("collresult", key)
        if done is not None:
            return self._agent_reduce_result(comm, label, operator, arrived, done.value)

        if self.device.read("opplan", key) is None:
            self._plan_agent_reduce(comm, label, operator, arrived, decision, root=root)

        while True:
            step = self.device.match(
                "opstep",
                {"label": label, "comm": self.comm_context(comm), "state": "ready",
                 "run": self.manifest.job_id},
                {"state": "assigned", "assignee": self.rank},
                order_by="step",
            )
            if step is not None:
                return self._directive(comm, label, step, operator, operand_budget)
            mine = self.device.scan(
                "opstep",
                {"label": label, "comm": self.comm_context(comm), "state": "assigned",
                 "assignee": self.rank, "run": self.manifest.job_id},
                limit=1,
            )
            if mine:
                return self._directive(comm, label, mine[0], operator, operand_budget)
            done = self.device.read("collresult", key)
            if done is not None:
                return self._agent_reduce_result(comm, label, operator, arrived, done.value)
            try:
                self._await(
                    lambda: self.device.read("collresult", key) is not None
                    or bool(
                        self.device.scan(
                            "opstep",
                            {"label": label, "comm": self.comm_context(comm), "state": "ready",
                             "run": self.manifest.job_id},
                            limit=1,
                        )
                    ),
                    timeout=timeout,
                    what=f"agent reduction {label!r} to need this rank or to finish",
                )
            except AmpiError:
                raise

    def _plan_agent_reduce(
        self, comm: str, label: str, operator: Op, arrived: list[dict[str, Any]], decision: Any,
        *, root: int
    ) -> None:
        """Materialise the merge schedule once, durably."""
        key = self._coll_key(comm, label)
        ok, _ = self.device.cas("opplan", key, 0, {"planning": self.rank}, writer=self.rank)
        if not ok:
            return
        ranks = [p["rank"] for p in arrived]
        handles = {p["rank"]: p.get("handle", "") for p in arrived}
        algorithm = "chain" if decision.chosen == "chain" else "binomial"
        level = [(r, handles[r]) for r in ranks]
        step_no = 0
        prev_steps: dict[int, int] = {}
        ctx = self.comm_context(comm)
        while len(level) > 1:
            nxt: list[tuple[int, str]] = []
            pairs = (
                [(level[0], level[1])] if algorithm == "chain"
                else [(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
            )
            rest = level[2:] if algorithm == "chain" else (
                [level[-1]] if len(level) % 2 else []
            )
            for (lr, lh), (rr, rh) in pairs:
                step_no += 1
                deps = [d for d in (prev_steps.get(lr), prev_steps.get(rr)) if d]
                self.device.append(
                    "opstep",
                    {
                        "label": label, "comm": ctx, "step": step_no,
                        "state": "ready" if not deps else "blocked",
                        "assignee": None, "run": self.manifest.job_id,
                        "left_rank": lr, "right_rank": rr,
                        "left": lh, "right": rh, "deps": deps, "owner": lr,
                    },
                )
                prev_steps[lr] = step_no
                nxt.append((lr, ""))
            level = nxt + rest
        self.device.cas("opplan", key, 1, {"steps": step_no, "algorithm": algorithm}, writer=self.rank)
        self.trace("op.plan", label=label, steps=step_no, algorithm=algorithm, op=operator.name)

    def _directive(
        self, comm: str, label: str, step: dict[str, Any], operator: Op, operand_budget: int | None
    ) -> dict[str, Any]:
        """Hand an executor two operands and the exact command to commit the merge.

        Operands are never clipped when the payload is structured.  An operand is
        the *input to the operator*, so removing part of it does not degrade the
        result, it corrupts it --- and for JSON it is worse: clipping mid-string
        yields a document the operator cannot parse at all.  We shipped the naive
        behaviour and it bit immediately, with agents inventing recovery hacks to
        get the bytes back.  A *result* may be summarised, because its consumer is
        a reader; an operand may not, because its consumer is a function.
        """
        left = self.get_body(step["left"]) if step["left"] else None
        right = self.get_body(step["right"]) if step["right"] else None
        structured = isinstance(left, (dict, list)) or isinstance(right, (dict, list))
        clipped = False
        if operand_budget is not None and not structured:
            left = apply_view(left, f"head:{operand_budget}")
            right = apply_view(right, f"head:{operand_budget}")
            clipped = True
        charged, _ = self.charge(
            count_tokens(canonical(left)) + count_tokens(canonical(right)), what="operands"
        )
        note = (
            "Both operands are structured, so they are delivered whole regardless of any "
            "operand budget: clipping an operand corrupts the merge."
            if structured and operand_budget is not None
            else ""
        )
        guidance = (
            "If the two operands disagree about something you cannot settle from these two "
            f"alone, DO NOT decide it: put it under \"{'_ampi_conflicts'}\" as "
            '{"key": [candidate, candidate]} and leave the key out of your result. '
            "The root will decide it once, for everybody."
            if operator.conflict_policy == LIFT
            else ""
        )
        self.trace("op.directive", rank=self.rank, label=label, step=step["step"])
        return {
            "status": "merge",
            "label": label,
            "step": step["step"],
            "op": operator.name,
            "operands": {"left": left, "right": right},
            "operand_handles": [step["left"], step["right"]],
            "clipped": clipped,
            "note": note,
            "conflict_guidance": guidance,
            "charged": charged,
            "commit": (
                f"ampi op commit --label {label} --step {step['step']} --result-file RESULT.json"
            ),
        }

    def op_commit(
        self, label: str, step: int, result: Any, *, comm: str = "world"
    ) -> dict[str, Any]:
        """Record a merge and advance the schedule."""
        self.assert_identity()
        ctx = self.comm_context(comm)
        found = self.device.scan(
            "opstep", {"label": label, "comm": ctx, "step": step, "run": self.manifest.job_id},
            limit=1,
        )
        if not found:
            raise err("AMPI_ERR_OP_FAILED", f"no step {step} in reduction {label!r}")
        rec = found[0]
        handle = self.put_payload(result).envelope.handle
        self.device.update("opstep", rec["seq"], {"state": "done", "result": handle})
        self.trace("op.commit", rank=self.rank, label=label, step=step, handle=handle)

        remaining = self.device.scan(
            "opstep", {"label": label, "comm": ctx, "run": self.manifest.job_id}, order_by="step"
        )
        done = {r["step"]: r.get("result") for r in remaining if r["state"] == "done"}
        for r in remaining:
            if r["state"] == "blocked" and all(d in done for d in (r.get("deps") or [])):
                patch: dict[str, Any] = {"state": "ready"}
                # A step's operands are the results of its dependencies, in order.
                deps = sorted(r.get("deps") or [])
                if len(deps) >= 1 and done.get(deps[0]):
                    patch["left"] = done[deps[0]]
                if len(deps) >= 2 and done.get(deps[1]):
                    patch["right"] = done[deps[1]]
                self.device.update("opstep", r["seq"], patch)

        if all(r["state"] == "done" or r["seq"] == rec["seq"] for r in remaining):
            last = max(remaining, key=lambda r: r["step"])
            final = handle if last["step"] == step else last.get("result", handle)
            self.device.cas(
                "collresult", self._coll_key(comm, label), None,
                {"handle": final, "op": "agent", "algorithm": "agent"}, writer=self.rank,
            )
            self.trace("op.complete", label=label, handle=final)
            return {"status": "done", "label": label, "handle": final}
        return {"status": "committed", "label": label, "step": step, "next": f"ampi reduce --label {label}"}

    def _agent_reduce_result(
        self, comm: str, label: str, operator: Op, arrived: list[dict[str, Any]], stored: dict
    ) -> dict[str, Any]:
        value = self.get_body(stored["handle"])
        leaves = [self.get_body(p["handle"]) for p in arrived if p.get("handle")]
        out: dict[str, Any] = {
            "status": "done",
            "label": label,
            "op": operator.name,
            "handle": stored["handle"],
            "value": value,
        }
        conflicts = conflicts_of(value)
        if conflicts:
            out["conflicts"] = conflicts
            out["next"] = f"ampi op arbitrate --label {label}"
        violations = check_invariant(operator, leaves, value)
        if violations:
            out["invariant_violations"] = violations
        return out

    def op_arbitrate(
        self, label: str, *, comm: str = "world", rulings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Decide every lifted conflict once, at the root.

        This is the step that buys back the invariant a locally-deciding merge
        cannot maintain.  Because the conflict set is a semilattice fold, the set
        arriving here is the same for every tree shape, so arbitrating it once
        makes the whole reduction's contested decisions shape-independent too.
        """
        self.assert_identity()
        stored = self.device.read("collresult", self._coll_key(comm, label))
        if stored is None:
            raise err("AMPI_ERR_OP_FAILED", f"reduction {label!r} has no result to arbitrate")
        value = self.get_body(stored.value["handle"])
        decide = (lambda k, c: rulings[k]) if rulings else None
        if rulings:
            missing = set(conflicts_of(value)) - set(rulings)
            if missing:
                raise err(
                    "AMPI_ERR_ARG",
                    f"no ruling supplied for conflict(s) {sorted(missing)}",
                    hint="Arbitration must decide every lifted conflict; that is the point.",
                    missing=sorted(missing),
                )
        resolved, decided = arbitrate(value, decide)
        handle = self.put_payload(resolved).envelope.handle
        self.device.cas(
            "collresult", self._coll_key(comm, label), None,
            {"handle": handle, "op": stored.value.get("op"), "arbitrated": True}, writer=self.rank,
        )
        self.trace("op.arbitrate", rank=self.rank, label=label, decided=len(decided))
        return {"label": label, "handle": handle, "value": resolved, "rulings": decided}

    def coll_status(self, *, comm: str = "world") -> list[dict[str, Any]]:
        """Every collective on a communicator and who has not arrived."""
        ctx = self.comm_context(comm)
        members = set(self.comm_members(comm))
        out: dict[str, dict[str, Any]] = {}
        for rec in self.device.scan("coll", {"comm": ctx, "run": self.manifest.job_id}):
            entry = out.setdefault(
                rec["label"], {"label": rec["label"], "kind": rec.get("kind"), "arrived": []}
            )
            entry["arrived"].append(rec["rank"])
        for entry in out.values():
            entry["missing"] = sorted(members - set(entry["arrived"]))
            entry["arrived"] = sorted(entry["arrived"])
            entry["closed"] = self.device.read("collresult", f"{ctx}#{entry['label']}") is not None
        return sorted(out.values(), key=lambda e: e["label"])
