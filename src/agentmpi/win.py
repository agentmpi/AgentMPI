"""One-sided operations: the shared blackboard.

MPI-2 added one-sided communication because two-sided messaging forces the
*receiver* to participate in every transfer, and there are algorithms where
only the initiator knows what it needs.  A rank exposes a memory *window*;
peers ``Put``, ``Get`` and ``Accumulate`` into it inside synchronisation
epochs.

The single most common complaint about multi-agent systems is that agents
cannot see what other agents learned.  Every framework's answer is some
variant of a shared scratchpad, and every one of them is an ad-hoc,
unsynchronised, silently-racy shared-memory system: no epochs, no
consistency model, no atomicity, no bound on how large the scratchpad may
grow before it stops fitting in anybody's context.  Those are exactly the
problems MPI-2's RMA chapter is about, and its answers transfer.

What does *not* transfer is the assumption that reading is free.  An MPI
``Get`` of a megabyte costs microseconds and no lasting resource; an agent
reading a megabyte of blackboard has spent a third of its working life.  So
AgentMPI's window operations differ from MPI's in one deliberate way:

    **A ``Get`` returns a reference by default, not content.**

The caller decides, with full knowledge of the size, whether to
:meth:`Window.materialize` it into context.  And because a window can be far
larger than any single agent's capacity, the protocol adds an operation MPI
has no need for -- :meth:`Window.query`, a *retrieval* read that returns the
part of the window relevant to a question within a token budget.  That is
the operation that makes a shared memory usable by a reader who cannot read
all of it, and it is, in our view, the correct systems framing of what the
retrieval-augmented-generation literature has been building.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .constants import ErrorClass
from .errors import ArgError, RmaConflictError, RmaSyncError, WinError
from .ops import Op, lookup_op
from .tokens import count_tokens
from .trace import Event


@dataclass
class Reference:
    """A handle to window content that has not been read into context."""

    key: str
    address: str
    tokens: int
    version: int
    owner: int
    updated_at: float
    preview: str = ""

    def __str__(self) -> str:
        return (f"<ref {self.key}@v{self.version} {self.tokens}tok "
                f"by r{self.owner}: {self.preview[:60]}>")

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key, "address": self.address, "tokens": self.tokens,
            "version": self.version, "owner": self.owner,
            "updated_at": self.updated_at, "preview": self.preview,
        }


class EpochError(RmaSyncError):
    pass


class Window:
    """``AMPI_Win`` -- a named, versioned, shared key-value region."""

    #: Synchronisation modes, matching MPI-2/3.
    NONE = "none"
    FENCE = "fence"          # active target, collective
    PSCW = "pscw"            # active target, scoped (post/start/complete/wait)
    LOCK = "lock"            # passive target

    def __init__(self, comm, name: str, *, capacity_tokens: int | None = None) -> None:
        self.comm = comm
        self.name = name
        self.capacity_tokens = capacity_tokens
        self.epoch_mode = self.NONE
        self.epoch_id = 0
        self._locked: set[str] = set()
        self._exposure: set[int] = set()
        self._access: set[int] = set()
        self._local_cache: dict[str, Reference] = {}
        self.stats = {"put": 0, "get": 0, "acc": 0, "query": 0, "materialized_tokens": 0,
                      "conflicts": 0, "cas": 0}

    # -- naming ------------------------------------------------------------
    def _k(self, key: str) -> str:
        return f"win/{self.comm.context}/{self.name}/{key}"

    def _meta_key(self) -> str:
        return f"win/{self.comm.context}/{self.name}/__index__"

    @property
    def device(self):
        return self.comm.runtime.device

    # -- synchronisation ---------------------------------------------------
    def fence(self, timeout: float | None = None) -> None:
        """``AMPI_Win_fence`` -- collective epoch boundary.

        Everything written before the fence is visible to everyone after it,
        and nothing may be written across it.  For a harness this is the
        "everyone publish, then everyone read" barrier that most shared
        scratchpads need and none of them have; without it, an agent that
        reads the board while its peers are still writing sees a torn state
        and reasons confidently about half an answer.
        """
        self.comm.barrier(timeout=timeout)
        self.epoch_id += 1
        self.epoch_mode = self.FENCE
        self._local_cache.clear()

    def start(self, group: Iterable[int]) -> None:
        """``AMPI_Win_start`` -- begin an access epoch toward ``group``."""
        self._access = set(group)
        self.epoch_mode = self.PSCW

    def post(self, group: Iterable[int]) -> None:
        """``AMPI_Win_post`` -- expose this rank's region to ``group``."""
        self._exposure = set(group)
        self.epoch_mode = self.PSCW

    def complete(self) -> None:
        self._access.clear()

    def wait(self) -> None:
        self._exposure.clear()
        self.epoch_mode = self.NONE

    def lock(self, key: str = "*", *, exclusive: bool = True,
             lease_s: float = 120.0, timeout_s: float = 600.0):
        """``AMPI_Win_lock`` -- passive-target exclusion on one key.

        Key granularity, not window granularity.  A whole-window lock in an
        agent harness serialises the entire team behind whichever agent is
        currently thinking, which can be minutes.  Locking the key you are
        about to modify keeps the critical section short even though the
        thinking is long -- the same argument that motivates fine-grained
        locking everywhere, made much sharper by how slow the holders are.

        The lock is leased, so an agent that dies holding it does not stop
        the run; the lease expiry is recorded in the trace as a stolen lock,
        which is a much better outcome than an unexplained hang.
        """
        handle = self.device.lock(self._k(key), lease_s=lease_s, timeout_s=timeout_s)
        self._locked.add(key)
        return handle

    def unlock(self, key: str = "*") -> None:
        self._locked.discard(key)

    def flush(self, key: str | None = None) -> None:
        """``AMPI_Win_flush`` -- make prior operations remotely complete.

        With a filesystem or key-value device every operation is already
        durable when it returns, so this is a no-op that exists to keep
        harness code portable to devices where it is not.
        """
        self._local_cache.pop(key, None) if key else self._local_cache.clear()

    def _require_epoch(self, what: str) -> None:
        if self.epoch_mode == self.NONE and not self._locked:
            # MPI would make this an error.  We make it a *recorded* warning:
            # refusing the operation outright would break the many harnesses
            # that legitimately use a window as an eventually consistent
            # notice board, and the trace still shows every unsynchronised
            # access so a reviewer can find the races.
            self.comm.runtime.profiler.note(
                "RMA outside a synchronisation epoch", window=self.name, op=what
            )

    # -- data movement -----------------------------------------------------
    def put(self, key: str, value: Any, *, tag: str = "") -> Reference:
        """``AMPI_Put`` -- write ``value`` at ``key``.

        Last writer wins, and the version counter makes the race visible.
        A harness that needs read-modify-write must use :meth:`accumulate`
        or :meth:`compare_and_swap`, exactly as in MPI.
        """
        self._require_epoch("put")
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                               default=str)
        address = self.device.put_blob(text)
        tokens = count_tokens(text)
        version = self._bump_version(key)
        ref = Reference(key=key, address=address, tokens=tokens, version=version,
                        owner=self.comm.rank, updated_at=time.time(),
                        preview=_preview(text))
        self.device.kv_put(self._k(key), json.dumps(ref.to_json()))
        self._index_add(key, tag)
        self.stats["put"] += 1
        self.comm.runtime.profiler.emit(
            Event(kind="state", ts=time.time(), rank=self.comm.runtime.world_rank,
                  op="win_put", context=self.comm.context, tokens=tokens,
                  detail={"window": self.name, "key": key, "version": version})
        )
        return ref

    def get(self, key: str, *, materialize: bool = False,
            budget: int | None = None) -> Reference | Any:
        """``AMPI_Get`` -- read ``key``.

        Returns a :class:`Reference` unless ``materialize`` is set.  The
        default is the important part: it lets an agent hold a working set of
        hundreds of window entries for a few hundred tokens and pay only for
        the ones it actually reads.
        """
        self._require_epoch("get")
        raw = self.device.kv_get(self._k(key))
        if raw is None:
            return None
        ref = _ref_from_json(json.loads(raw))
        self.stats["get"] += 1
        if not materialize:
            return ref
        return self.materialize(ref, budget=budget)

    def materialize(self, ref: Reference, budget: int | None = None) -> Any:
        """Pull a reference into context, charging the budget."""
        text = self.device.get_blob(ref.address)
        tokens = count_tokens(text)
        if budget is not None and tokens > budget:
            from .context import DIGESTS

            text = DIGESTS["head_tail"](text, budget)
            tokens = count_tokens(text)
        self.comm.runtime.budget.admit(tokens)
        self.stats["materialized_tokens"] += tokens
        self.comm.runtime.profiler.emit(
            Event(kind="state", ts=time.time(), rank=self.comm.runtime.world_rank,
                  op="win_materialize", context=self.comm.context, tokens=tokens,
                  detail={"window": self.name, "key": ref.key})
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def accumulate(self, key: str, value: Any, op: Op | str = "ampi_union") -> Reference:
        """``AMPI_Accumulate`` -- atomic read-modify-write with a reduction op.

        Atomicity is what makes a shared blackboard safe for concurrent
        writers, and it is the property every hand-rolled agent scratchpad
        lacks: two agents that both append to a findings list with
        read-then-write will lose one of the findings, intermittently, and
        the harness will look like a model quality problem.
        """
        operation = lookup_op(op)
        self._require_epoch("accumulate")

        def _apply(current: str | None) -> str:
            if current is None:
                base = None
                version = 0
            else:
                ref = _ref_from_json(json.loads(current))
                base = self.materialize_raw(ref)
                version = ref.version
            merged = value if base is None else operation.apply([base, value])
            text = merged if isinstance(merged, str) else json.dumps(
                merged, ensure_ascii=False, default=str)
            address = self.device.put_blob(text)
            new = Reference(key=key, address=address, tokens=count_tokens(text),
                            version=version + 1, owner=self.comm.rank,
                            updated_at=time.time(), preview=_preview(text))
            return json.dumps(new.to_json())

        result = self.device.kv_update(self._k(key), _apply)
        self._index_add(key, "")
        self.stats["acc"] += 1
        return _ref_from_json(json.loads(result))

    def get_accumulate(self, key: str, value: Any, op: Op | str = "ampi_union") -> Any:
        """``AMPI_Get_accumulate`` -- accumulate and return the previous value."""
        before = self.get(key)
        self.accumulate(key, value, op)
        return before

    def fetch_and_op(self, key: str, value: float, op: Op | str = "ampi_sum") -> float:
        """``AMPI_Fetch_and_op`` -- the atomic counter.

        This is how a harness implements a shared work queue: every worker
        atomically increments a cursor and takes the item it was handed.  It
        is also, notably, the *right* way to implement dynamic load balancing
        among agents with wildly different per-item costs, and it needs no
        orchestrator at all.
        """
        operation = lookup_op(op)
        previous: list[float] = [0.0]

        def _apply(current: str | None) -> str:
            base = 0.0 if current is None else float(json.loads(current)["value"])
            previous[0] = base
            return json.dumps({"value": operation.apply([base, value])})

        self.device.kv_update(self._k(f"atomic:{key}"), _apply)
        self.stats["acc"] += 1
        return previous[0]

    def compare_and_swap(self, key: str, expected: Any, desired: Any) -> tuple[bool, Any]:
        """``AMPI_Compare_and_swap`` -- the basis of lock-free agent protocols."""
        path = self._k(f"cas:{key}")
        current = self.device.kv_get(path)
        parsed = None if current is None else json.loads(current)
        self.stats["cas"] += 1
        if parsed != expected:
            self.stats["conflicts"] += 1
            return False, parsed
        ok = self.device.kv_cas(path, current, json.dumps(desired, default=str))
        return ok, parsed

    def materialize_raw(self, ref: Reference) -> Any:
        """Read a reference *without* charging context (runtime-internal)."""
        text = self.device.get_blob(ref.address)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- listing and retrieval --------------------------------------------
    def keys(self, prefix: str = "") -> list[str]:
        base = self._k(prefix)
        return [k.removeprefix(self._k("")) for k in self.device.kv_list(base)
                if not k.endswith("__index__")]

    def index(self) -> list[dict[str, Any]]:
        """A cheap catalogue: every key with its size and version, no content.

        Reading the index costs a few tokens per entry, so an agent can know
        *what exists* even when the window holds far more than it could read.
        Having this separation -- catalogue on the context plane, content on
        the payload plane -- is what keeps a shared memory usable as the run
        grows.
        """
        raw = self.device.kv_get(self._meta_key())
        entries = json.loads(raw) if raw else []
        out = []
        for entry in entries:
            ref_raw = self.device.kv_get(self._k(entry["key"]))
            if ref_raw:
                ref = _ref_from_json(json.loads(ref_raw))
                out.append({**entry, "tokens": ref.tokens, "version": ref.version,
                            "owner": ref.owner, "preview": ref.preview})
        return out

    def query(
        self, question: str, *, budget: int = 2000,
        scorer: Callable[[str, str], float] | None = None,
    ) -> dict[str, Any]:
        """``AMPI_Win_query`` -- a bounded, retrieval-style read.

        No MPI analogue, and necessary here for a structural reason: the
        window may be orders of magnitude larger than any participant's
        context, so "read the shared state" cannot mean "read all of it".
        The operation returns the highest-scoring entries that fit in
        ``budget`` tokens, together with an explicit statement of what was
        left out, so the caller knows it received a projection rather than
        the truth.

        The default scorer is lexical overlap, deliberately: it is
        deterministic, free, and adequate, and a harness that wants embedding
        retrieval passes its own ``scorer``.  Keeping the default free is
        what makes windows usable inside collectives, where a model call per
        access would be ruinous.
        """
        entries = self.index()
        score = scorer or _lexical_score
        ranked = sorted(entries, key=lambda e: -score(question, _entry_text(e)))
        chosen: list[dict[str, Any]] = []
        used = 0
        for entry in ranked:
            ref_raw = self.device.kv_get(self._k(entry["key"]))
            if not ref_raw:
                continue
            ref = _ref_from_json(json.loads(ref_raw))
            if used + ref.tokens > budget:
                continue
            content = self.device.get_blob(ref.address)
            chosen.append({"key": ref.key, "version": ref.version, "owner": ref.owner,
                           "tokens": ref.tokens, "content": content})
            used += ref.tokens
        self.stats["query"] += 1
        self.comm.runtime.budget.admit(min(used, self.comm.runtime.budget.headroom))
        return {
            "question": question,
            "returned": chosen,
            "tokens": used,
            "budget": budget,
            "entries_total": len(entries),
            "entries_returned": len(chosen),
            "omitted": [e["key"] for e in ranked if e["key"] not in
                        {c["key"] for c in chosen}],
        }

    # -- bookkeeping -------------------------------------------------------
    def _bump_version(self, key: str) -> int:
        vkey = self._k(f"__ver__/{key}")

        def _inc(current: str | None) -> str:
            return str(int(current or 0) + 1)

        return int(self.device.kv_update(vkey, _inc))

    def _index_add(self, key: str, tag: str) -> None:
        def _add(current: str | None) -> str:
            entries = json.loads(current) if current else []
            if not any(e["key"] == key for e in entries):
                entries.append({"key": key, "tag": tag, "created": time.time()})
            return json.dumps(entries)

        self.device.kv_update(self._meta_key(), _add)

    def total_tokens(self) -> int:
        return sum(int(e.get("tokens", 0)) for e in self.index())

    def free(self) -> None:
        for key in self.keys():
            self.device.kv_delete(self._k(key))
        self.device.kv_delete(self._meta_key())


def win_create(comm, name: str = "blackboard", **kw: Any) -> Window:
    """``AMPI_Win_create`` -- collectively create a window over ``comm``."""
    win = Window(comm, name, **kw)
    comm.attrs.setdefault("windows", {})[name] = win
    return win


def win_allocate_shared(comm, name: str = "shared", **kw: Any) -> Window:
    """``AMPI_Win_allocate_shared`` -- a window over ranks that share a store.

    MPI-3's shared-memory windows let ranks on the same node bypass the
    network entirely.  The agent analogue is ranks that share a context
    store: they can exchange references without either of them paying to
    re-ingest the content, which is the cheapest possible communication in
    this cost model.
    """
    from .constants import CommSplitType

    sub = comm.split_type(CommSplitType.STORE)
    return win_create(sub or comm, name, **kw)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _preview(text: str, n: int = 160) -> str:
    flat = " ".join(text.split())
    return flat[:n]


def _ref_from_json(d: dict[str, Any]) -> Reference:
    return Reference(
        key=d["key"], address=d["address"], tokens=int(d["tokens"]),
        version=int(d["version"]), owner=int(d["owner"]),
        updated_at=float(d["updated_at"]), preview=d.get("preview", ""),
    )


def _entry_text(entry: dict[str, Any]) -> str:
    return f"{entry.get('key', '')} {entry.get('tag', '')} {entry.get('preview', '')}"


_WORD = re.compile(r"[A-Za-z0-9_]+")


def _lexical_score(question: str, text: str) -> float:
    q = set(w.lower() for w in _WORD.findall(question))
    t = set(w.lower() for w in _WORD.findall(text))
    if not q or not t:
        return 0.0
    return len(q & t) / (len(q) ** 0.5 * len(t) ** 0.5)
