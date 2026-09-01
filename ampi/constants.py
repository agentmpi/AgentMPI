"""Protocol constants, sentinels and defaults.

Every default here is a policy choice, and the ones that matter carry the reason.
Numbers that were derived from measurement cite the experiment that produced them.
"""

from __future__ import annotations

from typing import Final

PROTOCOL_VERSION: Final[str] = "AgentMPI/1.0"
RUNTIME_VERSION: Final[str] = "1.0.0"


def runtime_fingerprint() -> str:
    """A content hash of the runtime's own source.

    A version string does not pin a runtime.  During our experiments an executor
    called into the package mid-edit and got an ``ImportError`` from a module
    another session was in the middle of fixing; the version string had not
    changed, because the thing that changed was the code.  Appendix D of the
    specification says the runtime must be pinned per job, and pinning by version
    is not pinning.

    Hashing the source catches it.  This is advisory rather than fatal --- a
    developer iterating on the runtime should not be locked out of their own job,
    and a long agent run should not die because a comment was reflowed --- but the
    mismatch is recorded, so a run whose behaviour changed halfway through says so
    in its own journal instead of leaving a reader to wonder.
    """
    import hashlib
    from pathlib import Path

    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for src in sorted(here.rglob("*.py")):
        if "__pycache__" in src.parts:
            continue
        h.update(src.relative_to(here).as_posix().encode())
        h.update(src.read_bytes())
    return h.hexdigest()[:16]

# -- sentinels ---------------------------------------------------------------
ANY_SOURCE: Final[int] = -1
ANY_TAG: Final[int] = -1
PROC_NULL: Final[int] = -2
UNDEFINED: Final[int] = -3
ROOT_UNDEFINED: Final[int] = -4

COMM_WORLD: Final[str] = "world"
COMM_SELF: Final[str] = "self"

# -- tags --------------------------------------------------------------------
# User tags occupy 0..TAG_UB.  Everything above is reserved for the runtime's own
# traffic, which is how a collective's internal messages stay invisible to a
# harness using ANY_TAG.  MPI achieves the same isolation with a duplicated
# communicator context; a reserved range costs one fewer round trip and can be
# enforced mechanically, so a user cannot violate it silently.
TAG_UB: Final[int] = 1_000_000
TAG_INTERNAL_BASE: Final[int] = TAG_UB + 1

# -- flow control ------------------------------------------------------------
# The eager threshold is MPI's eager limit with the unit changed from bytes to
# tokens.  Below it, pushing the body into the receiver's context unsolicited is
# cheaper than a handshake; above it, the receiver's attention is too precious to
# spend without permission.  700 is the measured knee for the reference corpus
# (experiments/e0_micro): the point at which the envelope-plus-materialise round
# trip costs less than the tokens it saves.
EAGER_THRESHOLD_TOKENS: Final[int] = 700

# A rank's default context budget.  Deliberately far below any real model's window:
# the ledger's purpose is to make exhaustion a *reported* flow-control event during
# development rather than a silent quality collapse in production.
DEFAULT_CTX_BUDGET: Final[int] = 120_000

# Bound on the total volume of unmatched eager messages a rank will accept.  This
# is MPI's unexpected-message buffer, and it is what makes "context-safe program"
# (S5.6) a checkable property rather than a hope.
DEFAULT_UNEXPECTED_BUDGET: Final[int] = 24_000

# Payload bodies above this size are never inlined into a command's stdout even
# when the ledger would permit it; the binding spills them to disk and prints a
# handle.  Agents reliably read a file; they unreliably read a wall of text.
INLINE_LIMIT_TOKENS: Final[int] = 800

# -- time --------------------------------------------------------------------
# A lease must be longer than the longest legitimate pause, and shorter than the
# time a blocked peer is willing to wait.  Those two requirements conflict, which
# is why AMPI_Heartbeat(extend) exists: the executor supplies the information a
# timeout cannot infer.
DEFAULT_LEASE_S: Final[float] = 180.0

# Detection is two-phase.  A rank silent for SUSPECT_S is *suspected*; only after a
# further CONFIRM_S is it *convicted*.  A single-phase detector convicted 1091
# times in twenty minutes on a real agent host, because executor turn latency is
# heavy-tailed and a thinking rank looks exactly like a dead one.
SUSPECT_S: Final[float] = 120.0
CONFIRM_S: Final[float] = 120.0

# A rank's lease starts when the rank is *requested*, not when its executor first
# calls in.  Without this, a rank whose executor never starts has no lease to
# expire, so it is neither alive nor failed and every peer waits for it forever.
# We hit exactly this: a launcher that could start 6 of 22 requested ranks left 16
# no-shows permanently pending and no operation could detect it.
JOIN_DEADLINE_S: Final[float] = 600.0

# Blocking operations are deadline-bounded and resumable.  The default deadline is
# long because the failure it guards against (a wedged peer) is rarer than the
# false positive it would otherwise cause (a peer that is merely thinking).
DEFAULT_TIMEOUT_S: Final[float] = 900.0

# The binding retries a timed-out blocking call internally before returning.  This
# is not a convenience.  An executor instructed to retry up to twenty times gave up
# after two and stalled its entire reduction tree.  A protocol that depends on an
# executor's persistence is not a protocol.
DEFAULT_RETRIES: Final[int] = 2

DEFAULT_LOCK_TTL_S: Final[float] = 300.0

# -- collectives -------------------------------------------------------------
# Measured crossover between a counting barrier (which can name absentees) and a
# dissemination barrier (which cannot, but costs O(log p) rounds).  Below this the
# counting barrier's diagnostics are worth its serialisation.
BARRIER_CENTRAL_MAX_P: Final[int] = 32

DEFAULT_QUORUM: Final[float] = 1.0

# -- supervision -------------------------------------------------------------
# OTP's max restart intensity.  An executor that fails because its assignment is
# impossible will fail again; an unbounded supervisor turns that into an expensive
# infinite loop.
MAX_RESTARTS_PER_RANK: Final[int] = 3

# -- environment -------------------------------------------------------------
ENV_ROOT: Final[str] = "AMPI_ROOT"
ENV_RANK: Final[str] = "AMPI_RANK"
ENV_JOB: Final[str] = "AMPI_JOB"
ENV_TOKEN: Final[str] = "AMPI_TOKEN"
ENV_COMM: Final[str] = "AMPI_COMM"
ENV_DEVICE: Final[str] = "AMPI_DEVICE"

# -- rank states -------------------------------------------------------------
STATE_REQUESTED: Final[str] = "requested"
STATE_RUNNING: Final[str] = "running"
STATE_SUSPECT: Final[str] = "suspect"
STATE_FAILED: Final[str] = "failed"
STATE_FENCED: Final[str] = "fenced"
STATE_FINALISED: Final[str] = "finalised"

RANK_STATES: Final[tuple[str, ...]] = (
    STATE_REQUESTED,
    STATE_RUNNING,
    STATE_SUSPECT,
    STATE_FAILED,
    STATE_FENCED,
    STATE_FINALISED,
)

# -- failure kinds -----------------------------------------------------------
# The kinds are distinguished because the appropriate recovery differs.  Retrying a
# rank that died of context exhaustion with the same assignment will exhaust it
# again; the recovery for `ctx_exhausted` is a *smaller* assignment.
FAILURE_KINDS: Final[dict[str, str]] = {
    "crash": "the launcher observed the executor exit",
    "lease_expired": "the lease deadline passed without a heartbeat",
    "no_show": "the join deadline passed and the rank never initialised",
    "abort": "the executor called AMPI_Abort",
    "ctx_exhausted": "the context ledger reached its budget without completion",
    "budget_exhausted": "a cost limit was reached",
    "protocol_violation": "output failed its declared contract repeatedly",
    "wrong_answer": "a verifier rejected the result",
    "killed": "an administrative kill, or fault injection",
    "zombie": "an operation arrived at a stale epoch",
}

# -- delivery ----------------------------------------------------------------
DELIVERY_EAGER: Final[str] = "eager"
DELIVERY_RENDEZVOUS: Final[str] = "rendezvous"
DELIVERY_AUTO: Final[str] = "auto"

SEND_STANDARD: Final[str] = "standard"
SEND_SYNCHRONOUS: Final[str] = "synchronous"
SEND_READY: Final[str] = "ready"

# -- conformance levels ------------------------------------------------------
# MPI-1 was small enough that every vendor shipped it within two years.  The levels
# exist so that a minimal implementation is a real one: a harness can ask what it
# may rely on rather than discovering an absence at run time.
CONFORMANCE_LEVELS: Final[dict[str, str]] = {
    "L1": "lifecycle, identity, point-to-point, barrier, context ledger, tracing",
    "L2": "L1 plus the full collective catalogue and runtime reduction operators",
    "L3": "L2 plus windows, leased locks, and topologies",
    "L4": "L3 plus fault tolerance: revoke, shrink, agree, respawn, recovery briefing",
    "L5": "L4 plus agent operators, views, contracts, and interface declaration",
}
