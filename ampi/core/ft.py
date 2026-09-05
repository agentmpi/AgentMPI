"""Fault tolerance: ULFM's primitives, and recovery by replay rather than restart.

ULFM's stance, adopted wholesale: expose failure rather than hide it, and provide
mechanisms rather than policy.  The three primitives are the ones ULFM identified,
and the least obvious of them is the necessary one.

**Revoke.**  When a rank fails, the *survivors* are the problem.  They are blocked
inside collectives that can never complete, and each discovers the failure only if
it happens to be waiting on the dead rank directly.  Revocation makes every
survivor fail fast, everywhere, which is what lets them all reach the recovery path
together.  It is irreversible, because a communicator that could be un-revoked
would let two ranks disagree about whether it is usable.

**Shrink.**  A new communicator over an *agreed* survivor set.  Agreed, not locally
computed: two ranks that computed different survivor sets would obtain
different-sized communicators and every subsequent collective would mismatch.  The
protocol also offers FT-MPI's ``BLANK`` mode, which keeps the numbering and leaves
a hole, because renumbering invalidates a harness's rank-to-work mapping --- and
for agents that mapping is baked into prompts and artifacts, so it is expensive in
a way it is not for an MPI program.

**Agree.**  Fault-tolerant agreement, which must work on a *revoked* communicator,
since that is how survivors coordinate recovery.

And one thing ULFM does not have, because MPI does not need it.

**Recovery by briefing, not by checkpoint.**  There is no memory image to restore,
so process checkpointing has no analogue.  What does have an analogue is durable
execution: replay the record of externally visible commitments rather than a memory
snapshot.  A replacement gets the answers to the five questions it must know and
cannot guess --- what was I assigned, what did I publish, what did I receive, what
did I promise that is still outstanding, and what did I record for myself.
"""

from __future__ import annotations

import time
from typing import Any

from ..constants import (
    DEFAULT_TIMEOUT_S,
    MAX_RESTARTS_PER_RANK,
    STATE_FAILED,
    STATE_FENCED,
    STATE_REQUESTED,
)
from ..errors import err
from .context import Ledger
from .payload import summarise

#: A memo note is a phase name or a short progress line, not a payload.  Bounding
#: it keeps the trace readable and keeps a harness from using the event log as a
#: side channel for data that belongs in a window.
MEMO_NOTE_CHARS = 120

__all__ = ["FaultMixin"]


class FaultMixin:
    # -- revoke ---------------------------------------------------------------
    def comm_revoke(self, comm: str = "world", *, reason: str = "") -> dict[str, Any]:
        info = self.comm_info(comm)
        self.device.cas("comm", comm, None, {**info, "state": "revoked", "reason": reason},
                        writer=self.rank if self._rank is not None else -1)
        self.trace("comm.revoke", comm=comm, reason=reason,
                   rank=self._rank if self._rank is not None else -1)
        return {"comm": comm, "state": "revoked", "reason": reason}

    # -- agree ----------------------------------------------------------------
    def comm_agree(
        self,
        label: str,
        value: Any,
        *,
        comm: str = "world",
        quorum: float = 1.0,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Agree over the live members, on a possibly-revoked communicator.

        The conjunction over live ranks, returned consistently: either every
        survivor gets the same answer or all of them raise.  An allreduce over
        logical AND would simply hang, which is why this is a separate primitive.
        The quorum exists so that a straggler cannot hold the survivors hostage.
        """
        self.assert_identity()
        ctx = self.comm_context(comm)
        self.device.append(
            "coll",
            {"comm": ctx, "label": f"agree:{label}", "rank": self.rank, "state": "joined",
             "run": self.manifest.job_id, "kind": "agree", "value": value,
             "gen": self.comm_info(comm)["gen"]},
        )
        members = set(self.comm_members(comm))
        deadline = time.time() + timeout
        while True:
            self.touch()
            self.detect_failures()
            live = {r for r in members if self._rankview(r).state not in (STATE_FAILED, STATE_FENCED)}
            votes = {
                r["rank"]: r.get("value")
                for r in self.device.scan("coll", {"comm": ctx, "label": f"agree:{label}"})
                if r["rank"] in live
            }
            need = max(1, int(quorum * len(live))) if live else 0
            if len(votes) >= need:
                agreed = all(bool(v) for v in votes.values())
                self.trace("comm.agree", comm=comm, label=label, agreed=agreed,
                           voters=len(votes), rank=self.rank)
                return {"label": label, "agreed": agreed, "votes": votes,
                        "live": sorted(live), "quorum": quorum}
            if time.time() >= deadline:
                raise err(
                    "AMPI_ERR_TIMEOUT",
                    f"agreement {label!r}: {len(votes)}/{len(live)} live members voted",
                    hint="Re-issue to resume, or lower the quorum.",
                    missing=sorted(live - set(votes)),
                )
            time.sleep(0.1)

    # -- shrink ---------------------------------------------------------------
    def comm_shrink(
        self,
        comm: str = "world",
        *,
        name: str | None = None,
        in_place: bool = False,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Derive a communicator over the agreed survivors.

        ``in_place`` is FT-MPI's ``BLANK``: keep the numbering and leave a hole.
        Renumbering makes subsequent collectives cheap again but invalidates the
        harness's rank-to-work mapping, and "rank 7 owns the parser" is baked into
        prompts here in a way it is not in an MPI program, so the hole is often the
        better trade.

        The survivor set is *agreed* rather than locally computed, and the derived
        communicator's name is a function of that set, so two ranks shrinking
        concurrently converge on one communicator instead of fragmenting into
        ``world#s0``, ``world#s1``, ``world#s2``.
        """
        self.assert_identity()
        info = self.comm_info(comm)
        survivors = sorted(self.live_ranks(comm))
        agreement = self.comm_agree(
            f"shrink:{info['gen']}", survivors, comm=comm, quorum=1.0, timeout=timeout
        )
        # Intersect every voter's survivor list: a rank that any survivor believes
        # is dead is excluded, which is the conservative choice and the one that
        # guarantees identical membership.
        agreed = set(survivors)
        for v in agreement["votes"].values():
            if isinstance(v, list):
                agreed &= set(v)
        members = sorted(agreed) if not in_place else list(info["members"])
        gen = info["gen"] + 1
        derived = name or f"{comm}/g{gen}+{'-'.join(map(str, members))}"[:120]
        self.device.cas(
            "comm", derived, None,
            {"name": derived, "members": members, "gen": gen, "state": "live",
             "parent": comm, "in_place": in_place,
             "absent": sorted(set(info["members"]) - set(members)) if in_place else []},
            writer=self.rank,
        )
        self.trace("comm.shrink", comm=comm, derived=derived, members=members, gen=gen,
                   rank=self.rank, in_place=in_place)
        return {"name": derived, "members": members, "gen": gen, "parent": comm,
                "dropped": sorted(set(info["members"]) - set(members))}

    # -- failure inspection ---------------------------------------------------
    def failures(self, *, comm: str = "world") -> dict[str, Any]:
        self.detect_failures()
        failed = self.failed_ranks(comm)
        return {
            "failed": [
                {"rank": v.rank, "kind": v.failure_kind, "epoch": v.epoch, "role": v.role,
                 "context_used": (v.ctx or {}).get("used", 0), "restarts": v.restarts}
                for v in failed
            ],
            "live": self.live_ranks(comm),
        }

    def failure_ack(self, *, comm: str = "world") -> dict[str, Any]:
        """Acknowledge the currently known failures, re-enabling wildcard receives.

        Without an acknowledgement step a wildcard receive keeps returning
        ``AMPI_ERR_PROC_FAILED_PENDING`` forever, because there is always a failure
        the caller has not been told about.  Acking draws a line.
        """
        self.assert_identity()
        acked = []
        for rec in self.device.scan("fail", {"state": "unacked", "run": self.manifest.job_id}):
            self.device.update("fail", rec["seq"], {"state": f"acked:{self.rank}"})
            acked.append(rec["rank"])
        self.trace("failure.ack", rank=self.rank, acked=acked)
        return {"acked": acked}

    def kill(self, rank: int, *, reason: str = "injected") -> dict[str, Any]:
        """Administratively declare a rank failed.

        A confirmed kill is a decision the rank may not overrule.  If it were
        retractable by the victim's next heartbeat, fault injection would be
        unobservable and an experiment measuring recovery would measure nothing.
        """
        view = self._rankview(rank)
        view.state = STATE_FAILED
        view.failure_kind = "killed"
        self._write_rank(view)
        self.device.append(
            "fail", {"rank": rank, "kind": "killed", "state": "unacked",
                     "run": self.manifest.job_id, "reason": reason}
        )
        self.trace("failure.kill", rank=rank, reason=reason,
                   by=self._rank if self._rank is not None else -1)
        return {"rank": rank, "state": STATE_FAILED, "kind": "killed", "reason": reason}

    # -- replacement ----------------------------------------------------------
    def respawn(self, rank: int, *, max_restarts: int = MAX_RESTARTS_PER_RANK) -> dict[str, Any]:
        """Allocate a fresh epoch for a rank and release its predecessor's holds.

        The predecessor's *messages* are not deleted: a survivor may still need
        what it sent.  Its locks are broken, its posted receives are left for the
        successor to inherit, and it is marked absent in any open collective so
        that the collective can still close.

        The restart bound is OTP's max restart intensity.  An executor that fails
        because its assignment is impossible will fail again, and an unbounded
        supervisor turns that into an expensive infinite loop.
        """
        view = self._rankview(rank)
        if view.restarts >= max_restarts:
            raise err(
                "AMPI_ERR_BUDGET",
                f"rank {rank} has already been replaced {view.restarts} times",
                hint="The assignment is probably impossible. Shrink instead of respawning.",
                rank=rank, restarts=view.restarts,
            )
        for lease in self.device.leases():
            if lease.holder == rank:
                self.device.release(lease.lock_id, rank)
        view.epoch += 1
        view.state = STATE_REQUESTED
        view.restarts += 1
        view.suspect_since = None
        # A successor is a new executor with an empty context.  It inherits the
        # budget, not the predecessor's consumption: the root of the first
        # completed 128-rank run was respawned with 184,000 of its 200,000
        # tokens already spent by a process that no longer existed, and died on
        # its first delivery.
        old = Ledger.from_dict(view.ctx)
        view.ctx = Ledger(budget=old.budget, unexpected_budget=old.unexpected_budget).to_dict()
        now = self.device.clock()
        view.last_seen = now
        view.join_deadline = now + 600
        view.lease_until = now + 600
        self._write_rank(view)
        self.trace("respawn", rank=rank, epoch=view.epoch, restarts=view.restarts)
        return {"rank": rank, "epoch": view.epoch, "restarts": view.restarts,
                "state": view.state, "role": view.role}

    def fence_rank(self, rank: int) -> dict[str, Any]:
        """Mark an executor a zombie: it may still be running, but may not act."""
        view = self._rankview(rank)
        view.state = STATE_FENCED
        self._write_rank(view)
        self.trace("fence", rank=rank, epoch=view.epoch)
        return {"rank": rank, "epoch": view.epoch, "state": STATE_FENCED}

    # -- the recovery briefing --------------------------------------------------
    def recover(self, rank: int | None = None) -> dict[str, Any]:
        """Answer the five questions a replacement must know and cannot guess.

        This is the whole of recovery.  There is no memory image, so the useful
        move is durable-execution replay: reconstruct the rank's externally
        visible commitments from the journal.  The fifth question --- what did I
        record for myself --- is why harnesses must instruct executors to write a
        memo after each phase: one cheap call per phase is the difference between a
        recoverable job and a lost one.
        """
        r = self.rank if rank is None else rank
        view = self._rankview(r)
        job = self.manifest.job_id

        assigned = [
            {"label": c["label"], "kind": c.get("kind"), "root": c.get("root")}
            for c in self.device.scan("coll", {"rank": r, "run": job})
        ]
        published = [
            {"win": c.key.split("/")[-1], "key": k.key, "version": k.version}
            for c in self.device.keys("winreg")
            for k in self.device.keys(c.key)
            if k.writer == r
        ]
        sent = [
            {"dst": m["dst"], "tag": m["tag"], "handle": m.get("handle"),
             "state": m["state"], "tokens": (m.get("envelope") or {}).get("tokens", 0)}
            for m in self.device.scan("msg", {"src": r, "run": job})
        ]
        received = [
            {"src": m["src"], "tag": m["tag"], "handle": m.get("handle")}
            for m in self.device.scan("msg", {"dst": r, "state": "claimed", "run": job})
        ]
        outstanding = {
            "unmatched_sends": [s for s in sent if s["state"] == "posted"],
            "posted_receives": [
                {"src": q["src_want"], "tag": q["tag_want"], "reqid": q["reqid"]}
                for q in self.device.scan("recvq", {"dst": r, "state": "open", "run": job})
            ],
            "open_collectives": [
                c["label"]
                for c in self.device.scan("coll", {"rank": r, "run": job})
                if self.device.read("collresult", f"{c['comm']}#{c['label']}") is None
            ],
            "held_locks": [lk.to_dict() for lk in self.device.leases() if lk.holder == r],
            "pending_merges": [
                {"label": s["label"], "step": s["step"]}
                for s in self.device.scan("opstep", {"assignee": r, "state": "assigned", "run": job})
            ],
        }
        memos = [
            {"key": c.key, "value": self.device.read(f"memo/{job}", c.key).value}
            for c in self.device.keys(f"memo/{job}", prefix=f"{r}/")
        ]
        briefing = {
            "rank": r,
            "epoch": view.epoch,
            "role": view.role,
            "restarts": view.restarts,
            "previous_failure": view.failure_kind,
            "assigned": assigned,
            "published": published,
            "sent": sent,
            "received": received,
            "outstanding": outstanding,
            "memos": memos,
            "advice": _advice(outstanding),
        }
        self.trace("recover", rank=r, epoch=view.epoch)
        return briefing

    def memo(self, key: str, value: Any = None) -> dict[str, Any]:
        """Record, or read, the executor's own continuation state."""
        self.assert_identity()
        space = f"memo/{self.manifest.job_id}"
        full = f"{self.rank}/{key}"
        if value is None:
            cell = self.device.read(space, full)
            return {"key": key, "value": cell.value if cell else None}
        self.device.cas(space, full, None, value, writer=self.rank)
        # The memo's *value* goes into the trace, bounded, not just its key.  A
        # memo is the one event a harness author writes deliberately, so it is the
        # only place the trace learns what the harness thought it was doing; the
        # phase segmentation in ``ampi.analysis`` is built from it.  Recording only
        # the key made every phase of every run indistinguishable from every other.
        note = value if isinstance(value, str) else summarise(value)
        self.trace("memo", rank=self.rank, key=key, note=note[:MEMO_NOTE_CHARS])
        return {"key": key, "written": True}

    # -- supervision -----------------------------------------------------------
    def supervise(self, *, max_restarts: int = MAX_RESTARTS_PER_RANK) -> dict[str, Any]:
        """One supervision pass: detect, then replace within the restart bound."""
        failed = self.detect_failures()
        actions = []
        for view in failed:
            if view.restarts >= max_restarts:
                actions.append({"rank": view.rank, "action": "give-up", "restarts": view.restarts})
                continue
            actions.append({"rank": view.rank, "action": "respawn", **self.respawn(view.rank)})
        return {"detected": [v.rank for v in failed], "actions": actions}


def _advice(outstanding: dict[str, Any]) -> list[str]:
    out = []
    if outstanding["open_collectives"]:
        out.append(
            "Enter these collectives again with the same labels: "
            + ", ".join(outstanding["open_collectives"])
            + ". Peers are blocked inside them. Contribute a degraded value if you must, "
            "but do not skip them."
        )
    if outstanding["pending_merges"]:
        out.append(
            "You owe merge results for: "
            + ", ".join(f"{m['label']}#{m['step']}" for m in outstanding["pending_merges"])
            + ". Re-issue the reduce command to receive the directive again."
        )
    if outstanding["posted_receives"]:
        out.append(
            f"{len(outstanding['posted_receives'])} receive(s) are posted and unmatched; "
            "they are yours to complete."
        )
    if outstanding["held_locks"]:
        out.append(
            "Your predecessor's locks were broken on replacement. Re-acquire before writing."
        )
    if not out:
        out.append("Nothing is outstanding. Start your assignment from the beginning.")
    return out
