"""Static analysis of harnesses: context safety and collective agreement.

MPI declines to guarantee buffering.  A program that only completes because the
implementation happened to buffer a message is called *unsafe*, and the standard's
advice is to test a program by replacing every standard-mode send with a
synchronous one: if it still completes, it does not depend on buffering.  That
test is mechanical, and almost nobody runs it, because in MPI the penalty for an
unsafe program is a deadlock on somebody else's machine.

The agent version of the penalty is worse, and that is why this module exists.
The buffer whose capacity a harness is implicitly relying on is the *receiving
executor's context window*.  Exceeding it does not deadlock; it silently degrades
the receiver's output.  So the failure is not "your program hangs on a different
implementation" but "your program produces slightly worse answers as you scale,
for reasons that never appear in any log".

This module makes the test cheap enough to run on every harness:

* :func:`check_context_safety` executes the zero-buffer semantics and reports
  either that the harness completes, or the exact set of ranks that wedge and
  what each of them is waiting for.
* It then re-runs with every eager send converted to rendezvous, so the report can
  say whether the harness is *repairable by transport choice alone* --- which is
  the common case and the useful advice.
* :func:`check_collective_agreement` catches the other structural bug: ranks that
  do not agree about which collective they are in.  In MPI this is undefined
  behaviour that manifests as a hang; here it is a static error with a rank list.
* :func:`peak_residency` reports the worst-case context each rank accumulates, so
  a harness can be rejected for infeasibility before an executor is paid for.

A harness declares its shape by building a :class:`Program`.  It does not have to:
a recorded trace can be replayed into one, so the analysis also works after the
fact on a run that already happened.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from ..constants import ANY_SOURCE, ANY_TAG, DELIVERY_EAGER, DELIVERY_RENDEZVOUS

__all__ = [
    "Program",
    "Send",
    "Recv",
    "Coll",
    "Local",
    "SafetyReport",
    "check_context_safety",
    "check_collective_agreement",
    "peak_residency",
    "analyse",
]


# --------------------------------------------------------------------------
# The program model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Send:
    dst: int
    tag: int = 0
    mode: str = DELIVERY_EAGER
    tokens: int = 0
    comm: str = "world"

    def describe(self) -> str:
        return f"send(dst={self.dst}, tag={self.tag}, {self.mode}, {self.tokens}tok)"


@dataclass(frozen=True)
class Recv:
    src: int = ANY_SOURCE
    tag: int = ANY_TAG
    comm: str = "world"

    def describe(self) -> str:
        s = "ANY_SOURCE" if self.src == ANY_SOURCE else self.src
        t = "ANY_TAG" if self.tag == ANY_TAG else self.tag
        return f"recv(src={s}, tag={t})"


@dataclass(frozen=True)
class Coll:
    label: str
    kind: str = "barrier"
    root: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    comm: str = "world"

    def describe(self) -> str:
        return f"{self.kind}(label={self.label!r})"


@dataclass(frozen=True)
class Local:
    note: str = ""
    tokens: int = 0

    def describe(self) -> str:
        return f"local({self.note})" if self.note else "local"


Op = Send | Recv | Coll | Local


@dataclass
class Program:
    """A harness's communication skeleton, per rank.

    Only the *shape* matters: which ranks talk to which, in what order, with what
    delivery mode and roughly what volume.  Nothing here needs an executor, which
    is the point --- the analysis costs milliseconds and the run it prevents costs
    hours.
    """

    size: int
    ops: dict[int, list[Op]] = field(default_factory=dict)
    members: dict[str, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for r in range(self.size):
            self.ops.setdefault(r, [])
        self.members.setdefault("world", list(range(self.size)))

    def rank(self, r: int, *ops: Op) -> Program:
        self.ops.setdefault(r, []).extend(ops)
        return self

    def all_ranks(self, make: Any) -> Program:
        """Append ``make(r)`` --- one op or a sequence --- to every rank."""
        for r in range(self.size):
            got = make(r)
            if isinstance(got, (list, tuple)):
                self.ops[r].extend(got)
            elif got is not None:
                self.ops[r].append(got)
        return self

    def comm(self, name: str, ranks: list[int]) -> Program:
        self.members[name] = list(ranks)
        return self

    def members_of(self, comm: str) -> list[int]:
        return self.members.get(comm, list(range(self.size)))


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@dataclass
class SafetyReport:
    safe: bool
    kind: Literal["context-safety", "collective-agreement"]
    completed: int = 0
    size: int = 0
    blocked: dict[int, str] = field(default_factory=dict)
    diagnosis: str = ""
    repair: str = ""
    cycle: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "kind": self.kind,
            "completed": self.completed,
            "size": self.size,
            "blocked": {str(k): v for k, v in self.blocked.items()},
            "diagnosis": self.diagnosis,
            "repair": self.repair,
            "cycle": self.cycle,
        }

    def __str__(self) -> str:
        if self.safe:
            return f"{self.kind}: safe ({self.completed}/{self.size} ranks complete)"
        lines = [f"{self.kind}: UNSAFE", f"  {self.diagnosis}"]
        for r, what in sorted(self.blocked.items()):
            lines.append(f"    rank {r} is blocked at {what}")
        if self.repair:
            lines.append(f"  repair: {self.repair}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The zero-buffer simulator
# --------------------------------------------------------------------------


def _tag_ok(want: int, have: int) -> bool:
    return want == ANY_TAG or want == have


def _simulate(prog: Program, *, force_rendezvous: bool = False) -> tuple[dict[int, int], list[dict]]:
    """Run the program under zero-buffer semantics.

    An eager send does not complete until a matching receive is posted, which is
    exactly MPI's synchronous-send substitution.  A rendezvous send completes
    immediately: it deposits an envelope, whose size is O(1) tokens and therefore
    bounded independently of the payload, so no amount of it can exhaust a
    receiver.  That asymmetry is the whole content of the theorem, and it is what
    makes "declare it rendezvous" a real repair rather than a workaround.
    """
    pc = {r: 0 for r in range(prog.size)}
    # Envelopes deposited by rendezvous sends, awaiting a matching receive.
    pending: dict[int, list[tuple[int, int]]] = defaultdict(list)  # dst -> [(src, tag)]
    events: list[dict] = []

    def current(r: int) -> Op | None:
        return prog.ops[r][pc[r]] if pc[r] < len(prog.ops[r]) else None

    progress = True
    while progress:
        progress = False

        # Local work and rendezvous sends always advance.
        for r in range(prog.size):
            op = current(r)
            if isinstance(op, Local):
                pc[r] += 1
                progress = True
            elif isinstance(op, Send):
                mode = DELIVERY_RENDEZVOUS if force_rendezvous else op.mode
                if mode == DELIVERY_RENDEZVOUS:
                    pending[op.dst].append((r, op.tag))
                    events.append({"t": len(events), "kind": "send-rz", "src": r, "dst": op.dst})
                    pc[r] += 1
                    progress = True

        # Receives satisfied by an already-deposited envelope.
        for r in range(prog.size):
            op = current(r)
            if isinstance(op, Recv):
                for i, (src, tag) in enumerate(pending[r]):
                    if (op.src in (ANY_SOURCE, src)) and _tag_ok(op.tag, tag):
                        pending[r].pop(i)
                        events.append({"t": len(events), "kind": "recv-rz", "src": src, "dst": r})
                        pc[r] += 1
                        progress = True
                        break

        # Eager send meets posted receive: both advance together.
        for r in range(prog.size):
            op = current(r)
            if not isinstance(op, Send):
                continue
            peer_op = current(op.dst)
            if (
                isinstance(peer_op, Recv)
                and peer_op.src in (ANY_SOURCE, r)
                and _tag_ok(peer_op.tag, op.tag)
            ):
                events.append({"t": len(events), "kind": "rendezvous", "src": r, "dst": op.dst})
                pc[r] += 1
                pc[op.dst] += 1
                progress = True

        # Collectives: every live member must be at the same label.
        for label, group in _pending_collectives(prog, pc).items():
            members, arrived = group
            if arrived == set(members):
                for r in members:
                    pc[r] += 1
                events.append({"t": len(events), "kind": "collective", "label": label})
                progress = True

    return pc, events


def _pending_collectives(
    prog: Program, pc: dict[int, int]
) -> dict[str, tuple[list[int], set[int]]]:
    out: dict[str, tuple[list[int], set[int]]] = {}
    for r in range(prog.size):
        if pc[r] >= len(prog.ops[r]):
            continue
        op = prog.ops[r][pc[r]]
        if isinstance(op, Coll):
            members = prog.members_of(op.comm)
            entry = out.setdefault(op.label, (members, set()))
            entry[1].add(r)
    return out


def check_context_safety(prog: Program) -> SafetyReport:
    """Does this harness complete without depending on buffering?

    A ``safe`` verdict means the harness completes for *every* assignment of
    unexpected-message budgets, however small --- so it will behave the same on an
    implementation with a generous eager limit and on one with none, and it cannot
    silently overrun a receiver's window through unmatched eager traffic.
    """
    pc, _ = _simulate(prog)
    done = sum(1 for r in range(prog.size) if pc[r] >= len(prog.ops[r]))
    if done == prog.size:
        return SafetyReport(True, "context-safety", done, prog.size)

    blocked = {
        r: prog.ops[r][pc[r]].describe()
        for r in range(prog.size)
        if pc[r] < len(prog.ops[r])
    }

    # Would declaring every send rendezvous fix it?  If so, the harness is not
    # structurally deadlocked; it is merely relying on buffering, and the repair
    # is a transport declaration rather than a redesign.
    pc_rz, _ = _simulate(prog, force_rendezvous=True)
    fixable = all(pc_rz[r] >= len(prog.ops[r]) for r in range(prog.size))

    cycle = _send_cycle(prog, pc)
    if fixable:
        offenders = sorted(
            r for r, op in ((r, prog.ops[r][pc[r]]) for r in blocked) if isinstance(op, Send)
        )
        diagnosis = (
            f"{len(blocked)} ranks wedge under zero buffering, but the harness completes "
            "when every send is declared rendezvous. This harness depends on the receiver "
            "having spare context to absorb an unmatched message."
        )
        repair = (
            f"Declare the sends at rank(s) {offenders} as rendezvous "
            "(mode='rendezvous'), or post the matching receives before sending."
        )
    else:
        diagnosis = (
            f"{len(blocked)} ranks wedge and remain wedged even with every send declared "
            "rendezvous, so this is a structural deadlock, not a buffering dependence."
        )
        repair = _structural_repair(prog, pc, blocked, cycle)

    return SafetyReport(False, "context-safety", done, prog.size, blocked, diagnosis, repair, cycle)


def _send_cycle(prog: Program, pc: dict[int, int]) -> list[int]:
    """Find a cycle in the blocked-send graph, if there is one.

    A ring exchange in which every rank sends before it receives is the textbook
    unsafe program, and naming the cycle is far more useful than naming the set.
    """
    waits: dict[int, int] = {}
    for r in range(prog.size):
        if pc[r] >= len(prog.ops[r]):
            continue
        op = prog.ops[r][pc[r]]
        if isinstance(op, Send):
            waits[r] = op.dst
    seen: set[int] = set()
    for start in sorted(waits):
        path: list[int] = []
        node = start
        local: set[int] = set()
        while node in waits and node not in local:
            local.add(node)
            path.append(node)
            node = waits[node]
        if node in local:
            return path[path.index(node) :]
        seen |= local
    return []


def _structural_repair(
    prog: Program, pc: dict[int, int], blocked: dict[int, str], cycle: list[int]
) -> str:
    # A rank stuck at a collective while its peers are at a different one is the
    # single most common structural bug in an agent harness, because an executor's
    # program order is not reliable.
    at_coll = {
        r: prog.ops[r][pc[r]]
        for r in blocked
        if isinstance(prog.ops[r][pc[r]], Coll)
    }
    if at_coll:
        labels = defaultdict(list)
        for r, op in at_coll.items():
            labels[op.label].append(r)  # type: ignore[union-attr]
        if len(labels) > 1:
            parts = "; ".join(f"{sorted(v)} at {k!r}" for k, v in sorted(labels.items()))
            return (
                f"Ranks disagree about which collective they are in: {parts}. "
                "Give every collective an explicit label and make every member call it."
            )
        (label, present), = labels.items()
        members = prog.members_of(at_coll[present[0]].comm)  # type: ignore[union-attr]
        absent = sorted(set(members) - set(present))
        return (
            f"Collective {label!r} is open but rank(s) {absent} never reach it. "
            "A rank that cannot compute its contribution must still enter the collective "
            "with a degraded value; a local failure must not remove a rank from a collective."
        )
    if cycle:
        return (
            f"Ranks {cycle} form a send cycle. Break it with sendrecv, with an "
            "odd/even ordering, or by declaring the sends rendezvous."
        )
    waiting_recv = [r for r in blocked if isinstance(prog.ops[r][pc[r]], Recv)]
    if waiting_recv:
        return (
            f"Rank(s) {sorted(waiting_recv)} wait for a message nobody sends. "
            "A conditional send paired with an unconditional receive is a deadlock: "
            "send unconditionally, possibly empty."
        )
    return "No repair inferred; inspect the blocked set."


def check_collective_agreement(prog: Program) -> SafetyReport:
    """Do all members of a communicator call the same collectives in the same order?

    MPI states this as a requirement and leaves violation undefined, which is
    tolerable when the caller is a compiler-checked program.  An executor's
    program order is not reliable --- it may retry a command, skip a step, or
    reorder two independent calls --- so here the requirement is checked, and the
    protocol additionally identifies collectives by explicit label rather than by
    position, so that a retry rejoins rather than mismatches.
    """
    sequences: dict[str, dict[int, list[str]]] = defaultdict(dict)
    for r in range(prog.size):
        for op in prog.ops[r]:
            if isinstance(op, Coll):
                sequences[op.comm].setdefault(r, []).append(f"{op.kind}:{op.label}")

    for comm, per_rank in sorted(sequences.items()):
        members = prog.members_of(comm)
        participating = {r: per_rank.get(r, []) for r in members}
        distinct = {tuple(v) for v in participating.values()}
        if len(distinct) > 1:
            reference = max(distinct, key=lambda s: len(s))
            offenders = {
                r: " -> ".join(v) or "(no collectives)"
                for r, v in participating.items()
                if tuple(v) != reference
            }
            return SafetyReport(
                False,
                "collective-agreement",
                0,
                prog.size,
                offenders,
                diagnosis=(
                    f"On communicator {comm!r}, members call different collective sequences. "
                    f"The majority sequence is: {' -> '.join(reference) or '(none)'}."
                ),
                repair=(
                    "Every member of a communicator must call the same collectives in the "
                    "same order. If a rank has nothing to contribute it must still call, "
                    "with the identity element."
                ),
            )
    return SafetyReport(True, "collective-agreement", prog.size, prog.size)


def peak_residency(prog: Program) -> dict[int, int]:
    """Worst-case cumulative context per rank, in tokens.

    Cumulative rather than live, because an executor's window is a transcript that
    only grows.  A harness whose peak exceeds a rank's budget is infeasible, and
    knowing that costs nothing here and an entire run otherwise.
    """
    used: dict[int, int] = {r: 0 for r in range(prog.size)}
    for r in range(prog.size):
        for op in prog.ops[r]:
            if isinstance(op, Local):
                used[r] += op.tokens
            elif isinstance(op, Coll):
                used[r] += op.tokens_out
        for src in range(prog.size):
            for op in prog.ops[src]:
                if isinstance(op, Send) and op.dst == r:
                    used[r] += op.tokens if op.mode == DELIVERY_EAGER else 40
    return used


def analyse(prog: Program, *, budget: int | None = None) -> dict[str, Any]:
    """Run every static check and return one report."""
    agreement = check_collective_agreement(prog)
    safety = check_context_safety(prog)
    residency = peak_residency(prog)
    out: dict[str, Any] = {
        "size": prog.size,
        "collective_agreement": agreement.to_dict(),
        "context_safety": safety.to_dict(),
        "peak_residency": residency,
        "peak_residency_max": max(residency.values()) if residency else 0,
    }
    if budget is not None:
        over = {r: v for r, v in residency.items() if v > budget}
        out["over_budget"] = over
        out["feasible"] = not over
    out["ok"] = agreement.safe and safety.safe and out.get("feasible", True)
    return out
