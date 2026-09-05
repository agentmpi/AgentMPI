"""Job state, rank identity, leases, failure detection, and tracing.

This is the layer immediately above the device.  Everything here is expressed in
terms of the six device capabilities, so it is transport independent by
construction, and the conformance suite runs it against all three devices.

Three design decisions dominate the file.

**A rank is a role, not a process.**  In MPI a rank *is* a process and the
identification is harmless because MPI processes live for the whole job.  An agent
session does not: it ends on a timeout, on context exhaustion, or because the host
decided.  So a rank here is a durable role --- a number, a mailbox, a context
budget, an accumulated ledger --- and its physical embodiment is an *epoch*: one
executor bound to the role for a bounded interval.  Session termination becomes an
ordinary recoverable event rather than a communicator rebuild.

**The epoch is a fencing token.**  Leases alone are insufficient: an executor
cannot know that its lease expired while it was mid-step, so between expiry and
its next call there is a window in which two executors believe they own the rank.
A monotone token checked on every operation closes that window, which is what
makes a zombie harmless rather than corrupting.

**Detection is lazy, local, and two-phase.**  It runs when some rank blocks on
something a failure would prevent, and never otherwise, so the failure-free cost
is one timestamp update per call and there is no daemon to deploy.  It is
two-phase because a single-phase lease detector convicted 1091 times in twenty
minutes on a real agent host: executor turn latency is heavy-tailed, and a
thinking rank looks exactly like a dead one.  Suspicion is retractable;
conviction, once a peer has acted on it, is not.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..constants import (
    CONFIRM_S,
    DEFAULT_CTX_BUDGET,
    DEFAULT_LEASE_S,
    DEFAULT_UNEXPECTED_BUDGET,
    ENV_RANK,
    ENV_TOKEN,
    JOIN_DEADLINE_S,
    PROTOCOL_VERSION,
    RUNTIME_VERSION,
    STATE_FAILED,
    STATE_FENCED,
    STATE_FINALISED,
    STATE_REQUESTED,
    STATE_RUNNING,
    STATE_SUSPECT,
    runtime_fingerprint,
)
from ..device import Device, open_device
from ..errors import AmpiError, err
from ..tokens import counter_name
from .context import Ledger
from .payload import Payload, canonical

__all__ = ["RuntimeBase", "RankView", "JobManifest"]


@dataclass
class JobManifest:
    job_id: str
    size: int
    device: str
    created_at: float
    protocol_version: str = PROTOCOL_VERSION
    runtime_version: str = RUNTIME_VERSION
    ctx_budget: int = DEFAULT_CTX_BUDGET
    unexpected_budget: int = DEFAULT_UNEXPECTED_BUDGET
    token_counter: str = ""
    eager_threshold: int = 0
    runtime_fingerprint: str = ""
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["meta"] = self.meta or {}
        return d


@dataclass
class RankView:
    rank: int
    state: str
    epoch: int
    lease_until: float
    join_deadline: float
    last_seen: float
    role: str = ""
    restarts: int = 0
    failure_kind: str = ""
    ctx: dict[str, Any] | None = None
    suspect_since: float | None = None
    #: The lease length this rank asked for at init.  Renewals use it: a lease
    #: renewed to the default 180 s on a transport whose batch lands every minute
    #: or two is a rank convicted for waiting, whatever it asked for.
    lease_s: float = DEFAULT_LEASE_S

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["ctx"] = self.ctx or {}
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RankView:
        """Parse a rank row, ignoring fields this version does not know.

        A job is upgraded one process at a time: a respawned rank runs newer code
        than its peers.  A row written by a newer runtime carried a field an older
        one had never heard of, and seventy-five ranks on three machines died
        parsing it in the middle of a production run.  Unknown fields are the
        newer version's business; the older one reads what it understands.
        """
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in row.items() if k in known})


class RuntimeBase:
    """Shared state and identity handling for every AgentMPI operation."""

    # -- construction ------------------------------------------------------
    def __init__(
        self,
        root: str,
        *,
        rank: int | None = None,
        device: str | None = None,
        job_id: str | None = None,
        expect_rank: int | None = None,
        expect_job: str | None = None,
        token: str | None = None,
        allow_volatile: bool = False,
    ) -> None:
        self.root = str(root)
        manifest_device = self._peek_device(self.root)
        name = device or manifest_device or os.environ.get("AMPI_DEVICE") or "sqlite"
        self.device: Device = open_device(name, self.root)
        if not self.device.durable and not allow_volatile:
            raise err(
                "AMPI_ERR_ARG",
                f"device {name!r} is not durable, so ranks in separate processes cannot see "
                "each other's state",
                hint="Use the sqlite or journal device, or pass allow_volatile for a test.",
            )
        self._manifest: JobManifest | None = None
        self._rank = rank
        self._expect_rank = expect_rank
        self._expect_job = expect_job
        self._token = token
        self._checked_identity = False

    @staticmethod
    def _peek_device(root: str) -> str | None:
        """Read the device name a job was created with, before opening anything.

        A job's device is part of its durable identity: opening a SQLite job with
        the journal device would silently produce an empty job rather than an
        error, which is the worst possible outcome.
        """
        import json
        from pathlib import Path

        p = Path(root) / "job.json"
        if p.exists():
            try:
                return json.loads(p.read_text())["device"]
            except Exception:  # pragma: no cover - corrupt manifest
                return None
        return None

    # -- job ---------------------------------------------------------------
    @classmethod
    def create(
        cls,
        root: str,
        size: int,
        *,
        device: str = "sqlite",
        ctx_budget: int = DEFAULT_CTX_BUDGET,
        unexpected_budget: int = DEFAULT_UNEXPECTED_BUDGET,
        eager_threshold: int | None = None,
        join_deadline_s: float = JOIN_DEADLINE_S,
        meta: dict[str, Any] | None = None,
        force: bool = False,
        allow_volatile: bool = False,
        roles: dict[int, str] | None = None,
    ) -> RuntimeBase:
        """Create a job and request every rank.

        Requesting the ranks here, rather than when their executors first call in,
        is what makes a launch failure detectable.  A rank whose executor never
        starts has no lease to expire, so without a join deadline granted at
        request time it is neither alive nor failed and every peer waits for it
        forever.  We hit exactly that: a launcher that could start 6 of 22
        requested ranks left 16 no-shows permanently pending and no operation
        could see it.
        """
        import json
        from pathlib import Path

        from ..constants import EAGER_THRESHOLD_TOKENS

        Path(root).mkdir(parents=True, exist_ok=True)
        marker = Path(root) / "job.json"
        if marker.exists() and not force:
            existing = json.loads(marker.read_text())
            raise err(
                "AMPI_ERR_RUN_EXISTS",
                f"a job ({existing.get('job_id')}) already occupies {root}",
                hint="Use a fresh job root, or pass --force. Reusing a live root poisons "
                "the inbox with the previous run's messages.",
                job_id=existing.get("job_id"),
            )

        self = cls.__new__(cls)
        self.root = str(root)
        self.device = open_device(device, str(root))
        if force:
            self.device.wipe()
            self.device.initialize()
        if not self.device.durable and not allow_volatile:
            raise err("AMPI_ERR_ARG", f"device {device!r} is not durable")

        job_id = uuid.uuid4().hex[:12]
        manifest = JobManifest(
            job_id=job_id,
            size=size,
            device=device,
            created_at=self.device.clock(),
            ctx_budget=ctx_budget,
            unexpected_budget=unexpected_budget,
            token_counter=counter_name(),
            eager_threshold=eager_threshold or EAGER_THRESHOLD_TOKENS,
            runtime_fingerprint=runtime_fingerprint(),
            meta=meta or {},
        )
        marker.write_text(json.dumps(manifest.to_dict(), indent=2))
        self.device.cas("job", "manifest", None, manifest.to_dict(), writer=-1)

        now = self.device.clock()
        # A device that can pipeline (the git daemon) turns the per-rank writes
        # below into a few group commits; the others get a no-op envelope.
        import contextlib

        pipeline = getattr(self.device, "pipeline", contextlib.nullcontext)
        with pipeline():
            self._request_ranks(size, now, join_deadline_s, roles, ctx_budget, unexpected_budget)

        self.device.cas(
            "comm",
            "world",
            None,
            {"name": "world", "members": list(range(size)), "gen": 0, "state": "live", "parent": ""},
            writer=-1,
        )
        self._manifest = manifest
        self._rank = None
        self._expect_rank = None
        self._expect_job = None
        self._token = None
        self._checked_identity = True
        self.trace("job.create", size=size, device=device, job_id=job_id)
        return self

    def _request_ranks(self, size: int, now: float, join_deadline_s: float,
                       roles: dict[int, str] | None, ctx_budget: int,
                       unexpected_budget: int) -> None:
        for r in range(size):
            self.device.cas(
                "rank",
                str(r),
                None,
                RankView(
                    rank=r,
                    state=STATE_REQUESTED,
                    epoch=0,
                    lease_until=now + join_deadline_s,
                    join_deadline=now + join_deadline_s,
                    last_seen=now,
                    role=(roles or {}).get(r, ""),
                    ctx=Ledger(budget=ctx_budget, unexpected_budget=unexpected_budget).to_dict(),
                ).to_dict(),
                writer=-1,
            )
            # A per-rank launch secret, recorded here and placed in the rank's
            # environment by the launcher.  If the ambient rank and the token
            # disagree the runtime can say so, which is the difference between an
            # executor passing the wrong rank (rare) and an executor silently
            # *being* the wrong rank (common, and much worse).
            self.device.cas("token", str(r), None, uuid.uuid4().hex[:16], writer=-1)

    @property
    def manifest(self) -> JobManifest:
        if self._manifest is None:
            cell = self.device.read("job", "manifest")
            if cell is None:
                raise err(
                    "AMPI_ERR_NO_JOB",
                    f"no AgentMPI job at {self.root}",
                    hint="Check AMPI_ROOT, or ask the launcher for the job root.",
                    root=self.root,
                )
            self._manifest = JobManifest(**cell.value)
            if self._manifest.runtime_version != RUNTIME_VERSION:
                # Protocol state is durable and lives outside the executors, which
                # is the design's central move.  The runtime *code* is shared
                # mutable state that the design says nothing about, and we once
                # hot-patched a package while a live population executed against
                # it.  Pinning is the cheap defence.
                raise err(
                    "AMPI_ERR_VERSION",
                    f"job was created by runtime {self._manifest.runtime_version}, "
                    f"this is {RUNTIME_VERSION}",
                    hint="Install the pinned runtime. Never edit a runtime under a live job.",
                    pinned=self._manifest.runtime_version,
                    running=RUNTIME_VERSION,
                )
            pinned = self._manifest.runtime_fingerprint
            if pinned and pinned != runtime_fingerprint():
                # Advisory, not fatal: a developer iterating should not be locked
                # out, and a two-hour agent run should not die because a docstring
                # moved.  But the run's own journal now says its runtime changed
                # underneath it, which is the difference between an inexplicable
                # result and an explained one.
                self.trace(
                    "runtime.changed", pinned=pinned, running=runtime_fingerprint(),
                    note="the runtime's source changed after this job was created",
                )
        return self._manifest

    @property
    def size(self) -> int:
        return self.manifest.size

    # -- identity ----------------------------------------------------------
    @property
    def rank(self) -> int:
        """The acting rank, from the constructor or the environment.

        Ambient identity is right --- an executor should not have to thread its
        rank through every call, and will make mistakes if forced to --- but
        ambient identity *alone* assumes the environment is trustworthy.  On a
        host where shell sessions are shared between concurrent agents it is not:
        we recorded one rank accumulating nine ``Init`` events, one from nearly
        every other agent in the job, because ``AMPI_RANK`` was overwritten
        between one call and the next.  Hence :meth:`assert_identity`.
        """
        if self._rank is None:
            env = os.environ.get(ENV_RANK)
            if env is None or env == "":
                raise err(
                    "AMPI_ERR_IDENTITY",
                    "no rank identity: neither an explicit rank nor AMPI_RANK",
                    hint="Pass --rank N, or read your rank card for the value of AMPI_RANK.",
                )
            try:
                self._rank = int(env)
            except ValueError:
                raise err("AMPI_ERR_IDENTITY", f"AMPI_RANK is not an integer: {env!r}") from None
        if not 0 <= self._rank < self.size:
            raise err(
                "AMPI_ERR_RANK",
                f"rank {self._rank} is outside 0..{self.size - 1}",
                rank=self._rank,
                size=self.size,
            )
        return self._rank

    def assert_identity(self) -> None:
        """Check the caller is who it says it is, *before* the operation runs.

        Three independent checks, because the failure they guard against is a
        silent one.  An explicitly asserted rank must match the ambient rank; an
        asserted job id must match the job; and the launch token in the
        environment must belong to the acting rank.  When the token disagrees the
        error names the rank it *does* belong to, because that is the fact the
        executor cannot otherwise discover.
        """
        if self._checked_identity:
            return
        if self._expect_job is not None and self._expect_job != self.manifest.job_id:
            raise err(
                "AMPI_ERR_IDENTITY",
                f"asserted job {self._expect_job!r} but this root holds {self.manifest.job_id!r}",
                hint="You are pointed at the wrong job root. The operation did not run.",
            )
        if self._expect_rank is not None and self._expect_rank != self.rank:
            raise err(
                "AMPI_ERR_IDENTITY",
                f"asserted rank {self._expect_rank} but the ambient identity is rank {self.rank}",
                hint="Your environment is wrong, not your command. Pass --rank explicitly "
                "and tell the harness that AMPI_RANK drifted.",
                asserted=self._expect_rank,
                ambient=self.rank,
            )
        token = self._token or os.environ.get(ENV_TOKEN)
        if token:
            mine = self.device.read("token", str(self.rank))
            if mine is not None and mine.value != token:
                owner = next(
                    (
                        c.key
                        for c in self.device.keys("token")
                        if (v := self.device.read("token", c.key)) and v.value == token
                    ),
                    None,
                )
                raise err(
                    "AMPI_ERR_IDENTITY",
                    f"the launch token in this environment belongs to rank {owner}, "
                    f"but the ambient rank is {self.rank}",
                    hint=f"You are almost certainly rank {owner}. Re-issue with --rank {owner}.",
                    ambient=self.rank,
                    token_owner=owner,
                )
        self._checked_identity = True

    def _rankview(self, r: int | None = None) -> RankView:
        r = self.rank if r is None else r
        cell = self.device.read("rank", str(r))
        if cell is None:
            raise err("AMPI_ERR_RANK", f"no such rank {r}", rank=r)
        return RankView.from_row(cell.value)

    def _write_rank(self, view: RankView, *, expect: int | None = None) -> bool:
        ok, _ = self.device.cas("rank", str(view.rank), expect, view.to_dict(), writer=view.rank)
        return ok

    def _fence_check(self, view: RankView | None = None) -> RankView:
        """Refuse to act for an executor that has been replaced or killed.

        A confirmed kill is a decision the rank may not overrule.  If the victim's
        next heartbeat could retract it, fault injection would be unobservable and
        an experiment measuring recovery would be measuring nothing.  A rank that
        merely lost its lease is different: it may re-initialise and continue, and
        the whole two-phase detector exists so that being slow is survivable.
        """
        view = view or self._rankview()
        if view.state == STATE_FAILED and view.failure_kind in ("killed", "abort"):
            raise err(
                "AMPI_ERR_FENCED",
                f"rank {view.rank} was {view.failure_kind}; this executor may no longer act",
                hint="Stop. This was an administrative decision. Report and exit.",
                rank=view.rank, kind=view.failure_kind,
            )
        if view.state == STATE_FENCED:
            raise err(
                "AMPI_ERR_FENCED",
                f"rank {view.rank} was replaced; this executor is a zombie at epoch {view.epoch}",
                hint="Stop. A successor holds this rank. Report what you completed and exit.",
                rank=view.rank,
                epoch=view.epoch,
            )
        return view

    # -- lifecycle ---------------------------------------------------------
    def init(
        self,
        *,
        role: str = "",
        ctx_budget: int | None = None,
        lease_s: float = DEFAULT_LEASE_S,
        reinit: bool = False,
    ) -> dict[str, Any]:
        """``AMPI_Init``.  Idempotent at the same epoch; agents retry commands.

        Re-initialising a rank that is already *running* is treated as a
        heartbeat rather than a new epoch.  That rule is what stopped a stray
        ``init`` issued by a confused peer from fencing the rank's real occupant
        in a live run, and it costs nothing.
        """
        self.assert_identity()
        view = self._rankview()
        now = self.device.clock()
        briefing: dict[str, Any] | None = None

        if view.state == STATE_RUNNING and not reinit:
            view.last_seen = now
            view.lease_s = lease_s
            view.lease_until = now + lease_s
            self._write_rank(view)
            self.trace("init.heartbeat", rank=view.rank, epoch=view.epoch)
            return {
                "rank": view.rank,
                "epoch": view.epoch,
                "size": self.size,
                "already_running": True,
                "job": self.manifest.job_id,
            }

        if view.state in (STATE_FAILED, STATE_FENCED) or reinit:
            view.epoch += 1
            briefing = {"reason": view.failure_kind or "reinit", "previous_epoch": view.epoch - 1}
        elif view.state == STATE_REQUESTED and view.epoch == 0:
            view.epoch = 1

        view.state = STATE_RUNNING
        view.last_seen = now
        view.lease_s = lease_s
        view.lease_until = now + lease_s
        view.suspect_since = None
        view.failure_kind = ""
        if role:
            view.role = role
        ledger = Ledger.from_dict(view.ctx)
        if ctx_budget is not None:
            ledger.budget = ctx_budget
        view.ctx = ledger.to_dict()
        self._write_rank(view)
        self.trace("init", rank=view.rank, epoch=view.epoch, role=view.role)
        out = {
            "rank": view.rank,
            "epoch": view.epoch,
            "size": self.size,
            "job": self.manifest.job_id,
            "role": view.role,
            "protocol": PROTOCOL_VERSION,
            "device": self.device.name,
            "token_counter": self.manifest.token_counter,
            "ctx": ledger.to_dict(),
        }
        if briefing is not None:
            out["recovery"] = self.recover()
        return out

    def finalize(self, note: str = "") -> dict[str, Any]:
        self.assert_identity()
        view = self._fence_check()
        view.state = STATE_FINALISED
        view.last_seen = self.device.clock()
        self._write_rank(view)
        # The ledger goes into the trace at the one moment it is final.  Context is
        # the scarce resource in this protocol, so a run whose trace does not
        # record what each rank spent cannot answer the question the protocol
        # exists to answer, and the ledger is gone once the session ends.
        ledger = self.ledger()
        self.trace(
            "finalize",
            rank=view.rank,
            note=note,
            state=view.state,
            epoch=view.epoch,
            used=ledger.used,
            budget=ledger.budget,
            high_water=ledger.peak,
            releases=ledger.releases,
            degradations=ledger.degradations,
        )
        return {"rank": view.rank, "state": view.state}

    def heartbeat(self, *, extend: float = 0.0, note: str = "") -> dict[str, Any]:
        """``AMPI_Heartbeat``.  Renew the lease, optionally for longer.

        A lease-based detector cannot distinguish a thinking executor from a dead
        one, and making the lease longer than the longest legitimate pause is the
        wrong fix because the lease also bounds how long a blocked peer waits.
        ``extend`` lets the executor supply the information a timeout cannot
        infer: "I am about to spend ten minutes on one step."
        """
        self.assert_identity()
        view = self._fence_check()
        now = self.device.clock()
        view.last_seen = now
        view.lease_until = now + max(view.lease_s or DEFAULT_LEASE_S, extend)
        if view.state == STATE_SUSPECT:
            # Retraction.  Without it, a heavy-tailed executor is convicted for
            # the crime of thinking, and the job cascades.
            view.state = STATE_RUNNING
            view.suspect_since = None
            self.trace("failure.retract", rank=view.rank)
        self._write_rank(view)
        return {"rank": view.rank, "lease_until": view.lease_until, "note": note}

    #: A blocked rank renews its lease on every poll, and a poll loop runs many
    #: times a second.  Writing the rank row each time is what turned a 100-rank
    #: job's write-ahead log into 264 MB: the lease only needs renewing often
    #: enough that the detector does not convict, so once per this interval is
    #: three orders of magnitude more than sufficient and three orders of
    #: magnitude cheaper.
    _TOUCH_INTERVAL_S = 5.0

    def touch(self) -> None:
        """Renew the caller's own lease as a side effect of any operation.

        A blocked rank must keep doing this.  Omitting it is catastrophic and not
        obvious: in an early version a rank waiting inside a barrier made no
        runtime calls, so the detector declared it dead for the crime of waiting,
        and every rank that arrived first was declared failed.  Blocking is not
        evidence of death.

        Rate limited, because "on every operation" and "on every poll iteration"
        are very different write volumes and only the first is required.
        """
        now = time.time()
        interval = max(self._TOUCH_INTERVAL_S, getattr(self.device, "touch_interval_s", 0.0))
        if now - getattr(self, "_last_touch", 0.0) < interval:
            return
        self._last_touch = now
        try:
            view = self._rankview()
        except AmpiError:
            return
        if view.state in (STATE_RUNNING, STATE_SUSPECT):
            now = self.device.clock()
            view.last_seen = now
            view.lease_until = max(view.lease_until, now + (view.lease_s or DEFAULT_LEASE_S))
            if view.state == STATE_SUSPECT:
                view.state = STATE_RUNNING
                view.suspect_since = None
            self._write_rank(view)

    # -- failure detection -------------------------------------------------
    def detect_failures(self, *, confirm_s: float = CONFIRM_S) -> list[RankView]:
        """Two-phase, lazy, local detection.

        Called from inside blocking operations, never from a daemon.  Detection
        runs exactly when somebody cares and never otherwise, which is also why
        its failure-free cost is one timestamp update per call.

        The view is deliberately *local*: two ranks may disagree about who has
        failed, and no operation assumes a globally consistent view.  Where
        agreement is required --- shrinking a communicator --- it is obtained
        explicitly through ``AMPI_Comm_agree``.
        """
        now = self.device.clock()
        failed: list[RankView] = []
        for cell in self.device.keys("rank"):
            full = self.device.read("rank", cell.key)
            if full is None:
                continue
            view = RankView.from_row(full.value)
            if view.state in (STATE_FAILED, STATE_FENCED, STATE_FINALISED):
                if view.state == STATE_FAILED:
                    failed.append(view)
                continue
            if view.state == STATE_REQUESTED and now > view.join_deadline:
                self._convict(view, "no_show", full.version)
                failed.append(view)
                continue
            if now <= view.lease_until:
                continue
            if view.state == STATE_RUNNING:
                view.state = STATE_SUSPECT
                view.suspect_since = now
                self._write_rank(view, expect=full.version)
                self.trace("failure.suspect", rank=view.rank, silent_for=now - view.last_seen)
                continue
            if view.state == STATE_SUSPECT and now - (view.suspect_since or now) >= confirm_s:
                self._convict(view, "lease_expired", full.version)
                failed.append(view)
        return failed

    def _convict(self, view: RankView, kind: str, expect: int | None = None) -> None:
        view.state = STATE_FAILED
        view.failure_kind = kind
        self._write_rank(view, expect=expect)
        self.device.append("fail", {"rank": view.rank, "kind": kind, "state": "unacked", "run": self.manifest.job_id})
        self.trace("failure.convict", rank=view.rank, kind=kind, epoch=view.epoch)

    def live_ranks(self, comm: str = "world") -> list[int]:
        members = self.comm_members(comm)
        out = []
        for r in members:
            view = self._rankview(r)
            if view.state not in (STATE_FAILED, STATE_FENCED):
                out.append(r)
        return out

    def failed_ranks(self, comm: str = "world") -> list[RankView]:
        return [
            v
            for r in self.comm_members(comm)
            if (v := self._rankview(r)).state in (STATE_FAILED, STATE_FENCED)
        ]

    # -- communicators -----------------------------------------------------
    def comm_info(self, name: str = "world") -> dict[str, Any]:
        cell = self.device.read("comm", name)
        if cell is None:
            raise err(
                "AMPI_ERR_COMM",
                f"no such communicator {name!r}",
                hint="Run 'ampi comm list'.",
                known=[c.key for c in self.device.keys("comm")],
            )
        return cell.value

    def comm_members(self, name: str = "world") -> list[int]:
        return list(self.comm_info(name)["members"])

    def comm_rank(self, name: str = "world", world_rank: int | None = None) -> int:
        r = self.rank if world_rank is None else world_rank
        members = self.comm_members(name)
        if r not in members:
            raise err(
                "AMPI_ERR_RANK",
                f"rank {r} is not a member of communicator {name!r}",
                members=members,
            )
        return members.index(r)

    def comm_context(self, name: str) -> str:
        """The private context that keeps one library's traffic out of another's.

        MPI's designers retrospectively identify this as the reason MPI could
        support libraries at all: a reusable component operating on a subset of
        ranks gets a local name space and cannot collide with its caller's tags.
        The context is derived from the communicator's identity and generation
        and is never a value the application can observe or forge.
        """
        info = self.comm_info(name)
        return f"{self.manifest.job_id}/{name}/{info['gen']}"

    # -- context ledger ----------------------------------------------------
    def ledger(self, r: int | None = None) -> Ledger:
        return Ledger.from_dict(self._rankview(r).ctx)

    def charge(self, tokens: int, *, what: str = "", degrade_ok: bool = True) -> tuple[int, str]:
        """Charge the caller's ledger, degrading to a view rather than failing.

        Returns ``(charged, view_spec)`` where an empty ``view_spec`` means the
        full body was delivered.  Degrading is preferred to failing because an
        agent that receives a truncated message can continue while one that
        receives an error usually cannot.
        """
        from .context import MIN_DEGRADE_TOKENS, degrade_allowance, degrade_spec

        view = self._rankview()
        ledger = Ledger.from_dict(view.ctx)
        spec = ""
        if ledger.would_exceed(tokens):
            if ledger.remaining < MIN_DEGRADE_TOKENS:
                # Degradation is preferred to failure, but it has a floor: below a
                # few dozen tokens no projection says anything, and charging a
                # minimum would break the very budget the ledger exists to keep.
                raise err(
                    "AMPI_ERR_CTX_EXCEEDED",
                    f"rank {view.rank} has {ledger.remaining} tokens left and cannot "
                    f"accept a {tokens}-token delivery, even degraded",
                    hint="Use --out FILE to save the body to disk without charging "
                    "context, or start a fresh executor turn with 'ampi ctx-release'.",
                    tokens=tokens,
                    **ledger.to_dict(),
                )
            if not degrade_ok:
                ledger_error = err(
                    "AMPI_ERR_CTX_EXCEEDED",
                    f"delivering {tokens} tokens would exceed this rank's budget "
                    f"({ledger.used}/{ledger.budget} used)",
                    tokens=tokens,
                    **ledger.to_dict(),
                )
                raise ledger_error
            spec = degrade_spec(tokens, ledger.remaining)
            tokens = min(tokens, degrade_allowance(ledger.remaining))
            ledger.degradations += 1
            self.trace("ctx.degrade", rank=view.rank, spec=spec, what=what)
        ledger.used += tokens
        ledger.peak = max(ledger.peak, ledger.used)
        view.ctx = ledger.to_dict()
        self._write_rank(view)
        return tokens, spec

    def ctx_release(self, tokens: int | None = None) -> dict[str, Any]:
        view = self._rankview()
        ledger = Ledger.from_dict(view.ctx)
        freed = ledger.release(ledger.used if tokens is None else tokens)
        view.ctx = ledger.to_dict()
        self._write_rank(view)
        self.trace("ctx.release", rank=view.rank, freed=freed)
        return ledger.to_dict()

    # -- object store ------------------------------------------------------
    def put_payload(self, value: Any, *, schema: str = "") -> Payload:
        p = Payload.of(value, schema=schema)
        self.device.put_object(p.envelope.handle, canonical(value))  # type: ignore[attr-defined]
        return p

    def get_body(self, handle: str) -> Any:
        raw = self.device.get_object(handle)  # type: ignore[attr-defined]
        if raw is None:
            raise err(
                "AMPI_ERR_ARG",
                f"no payload with handle {handle}",
                hint="Handles are content addresses; check you copied it whole.",
            )
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # -- tracing -----------------------------------------------------------
    def trace(self, _kind: str, /, **fields: Any) -> None:
        """Append an event.  Unconditional, and part of the protocol.

        MPI's tooling interfaces are opt-in and out-of-band, which is reasonable
        when a run can be repeated cheaply.  An agent run cannot: it is expensive
        and it is not reproducible, so a bug that was not traced is a bug that
        cannot be investigated.
        """
        try:
            job = self._manifest.job_id if self._manifest else ""
        except AmpiError:  # pragma: no cover - during creation
            job = ""
        rec = {"kind": _kind, "run": job, "ts": self.device.clock()}
        # Field names that collide with the event's own columns are prefixed
        # rather than dropped, because a silently missing trace field is a debugging
        # session nobody can have.
        for k, v in fields.items():
            if v is None:
                continue
            rec["arg_" + k if k in ("kind", "run", "ts", "seq") else k] = v
        rec.setdefault("rank", self._rank if self._rank is not None else -1)
        self.device.append("event", rec)

    def events(self, *, kind: str | None = None, rank: int | None = None, limit: int | None = None):
        pred: dict[str, Any] = {}
        if kind is not None:
            pred["kind"] = kind
        if rank is not None:
            pred["rank"] = rank
        return self.device.scan("event", pred, limit=limit)

    # -- housekeeping ------------------------------------------------------
    def status(self) -> dict[str, Any]:
        ranks = []
        for r in range(self.size):
            try:
                ranks.append(self._rankview(r).to_dict())
            except AmpiError:  # pragma: no cover
                continue
        return {
            "job": self.manifest.to_dict(),
            "device": self.device.stats(),
            "ranks": ranks,
            "communicators": [self.device.read("comm", c.key).value for c in self.device.keys("comm")],
        }

    def close(self) -> None:
        self.device.close()

    def __enter__(self) -> RuntimeBase:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
