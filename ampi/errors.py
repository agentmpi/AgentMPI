"""AgentMPI error classes.

The AgentMPI error taxonomy mirrors MPI's split between *error codes* (opaque,
implementation-defined) and *error classes* (a small, standardised set that
portable programs may branch on). As in ULFM, the classes that matter most are
the ones that tell a surviving rank *what it may still assume* about the
communicator it was using.

Every class has a stable string name. Agents are text-mode consumers, so the
string is the contract: the CLI prints ``AMPI_ERR_PROC_FAILED`` and the agent's
harness prompt tells it what to do about that token. Numeric values exist for
programmatic harnesses and for the exit code of the CLI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ErrClass:
    """Namespace of AgentMPI error class names.

    The values are deliberately the same strings the CLI emits, so that a
    harness written in any language can string-match them.
    """

    SUCCESS = "AMPI_SUCCESS"

    # --- Classical MPI-style usage errors -------------------------------
    ARG = "AMPI_ERR_ARG"
    RANK = "AMPI_ERR_RANK"
    TAG = "AMPI_ERR_TAG"
    COMM = "AMPI_ERR_COMM"
    OP = "AMPI_ERR_OP"
    WIN = "AMPI_ERR_WIN"
    REQUEST = "AMPI_ERR_REQUEST"
    TRUNCATE = "AMPI_ERR_TRUNCATE"
    NO_JOB = "AMPI_ERR_NO_JOB"
    NOT_INIT = "AMPI_ERR_NOT_INIT"
    ALREADY_INIT = "AMPI_ERR_ALREADY_INIT"
    INTERN = "AMPI_ERR_INTERN"
    UNSUPPORTED = "AMPI_ERR_UNSUPPORTED"

    # --- Timeouts and progress -----------------------------------------
    #: A blocking call reached its deadline without completing. Unlike MPI,
    #: AgentMPI makes this a *normal, expected* outcome (see spec S5.3): agent
    #: latency is heavy-tailed, so every blocking call is deadline-bounded and
    #: idempotently retryable.
    TIMEOUT = "AMPI_ERR_TIMEOUT"

    # --- ULFM-style failure classes ------------------------------------
    #: A peer required to complete this operation has been declared failed.
    PROC_FAILED = "AMPI_ERR_PROC_FAILED"
    #: A wildcard receive cannot complete because *some* peer failed, but the
    #: request itself remains valid (MPIX_ERR_PROC_FAILED_PENDING analogue).
    PROC_FAILED_PENDING = "AMPI_ERR_PROC_FAILED_PENDING"
    #: The communicator was revoked; all non-local operations on it now fail
    #: until it is shrunk.
    REVOKED = "AMPI_ERR_REVOKED"
    #: The caller's lease expired and it was declared failed by its peers; a
    #: replacement may already be running. The caller must stop (zombie fence).
    FENCED = "AMPI_ERR_FENCED"

    # --- AgentMPI-specific classes -------------------------------------
    #: Delivering/materialising this payload would exceed the caller's context
    #: budget. The runtime returns a handle or a view instead of the payload.
    CTX_EXCEEDED = "AMPI_ERR_CTX_EXCEEDED"
    #: The job or rank exhausted its monetary/token budget.
    BUDGET_EXHAUSTED = "AMPI_ERR_BUDGET_EXHAUSTED"
    #: A semantic (agent-evaluated) reduction step was abandoned or produced
    #: output that failed its declared schema check.
    OP_FAILED = "AMPI_ERR_OP_FAILED"
    #: A lock could not be acquired within the deadline.
    LOCK_BUSY = "AMPI_ERR_LOCK_BUSY"
    #: A compare-and-swap or versioned put lost a race.
    CONFLICT = "AMPI_ERR_CONFLICT"
    #: A quorum collective closed without the caller, which arrived late.
    LATE = "AMPI_ERR_LATE"


#: Numeric codes, used as CLI exit statuses. Keep them below 64 and stable.
_EXIT_CODES: Dict[str, int] = {
    ErrClass.SUCCESS: 0,
    ErrClass.ARG: 2,
    ErrClass.RANK: 3,
    ErrClass.TAG: 4,
    ErrClass.COMM: 5,
    ErrClass.OP: 6,
    ErrClass.WIN: 7,
    ErrClass.REQUEST: 8,
    ErrClass.TRUNCATE: 9,
    ErrClass.NO_JOB: 10,
    ErrClass.NOT_INIT: 11,
    ErrClass.ALREADY_INIT: 12,
    ErrClass.INTERN: 13,
    ErrClass.UNSUPPORTED: 14,
    ErrClass.TIMEOUT: 20,
    ErrClass.PROC_FAILED: 21,
    ErrClass.PROC_FAILED_PENDING: 22,
    ErrClass.REVOKED: 23,
    ErrClass.FENCED: 24,
    ErrClass.CTX_EXCEEDED: 30,
    ErrClass.BUDGET_EXHAUSTED: 31,
    ErrClass.OP_FAILED: 32,
    ErrClass.LOCK_BUSY: 33,
    ErrClass.CONFLICT: 34,
    ErrClass.LATE: 35,
}


def exit_code(cls_name: str) -> int:
    return _EXIT_CODES.get(cls_name, 1)


class AmpiError(Exception):
    """Base class for all AgentMPI errors surfaced to callers."""

    err_class: str = ErrClass.INTERN

    def __init__(
        self,
        message: str,
        *,
        err_class: Optional[str] = None,
        hint: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        if err_class is not None:
            self.err_class = err_class
        self.message = message
        #: Free-text next-action guidance. Agents read this, so it must say what
        #: to *do*, not merely what went wrong.
        self.hint = hint
        self.detail: Dict[str, Any] = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": False,
            "err_class": self.err_class,
            "message": self.message,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = self.detail
        return out


def _mkerr(name: str, cls_value: str) -> type:
    return type(name, (AmpiError,), {"err_class": cls_value})


ArgError = _mkerr("ArgError", ErrClass.ARG)
RankError = _mkerr("RankError", ErrClass.RANK)
CommError = _mkerr("CommError", ErrClass.COMM)
OpError = _mkerr("OpError", ErrClass.OP)
WinError = _mkerr("WinError", ErrClass.WIN)
RequestError = _mkerr("RequestError", ErrClass.REQUEST)
NoJobError = _mkerr("NoJobError", ErrClass.NO_JOB)
NotInitError = _mkerr("NotInitError", ErrClass.NOT_INIT)
AlreadyInitError = _mkerr("AlreadyInitError", ErrClass.ALREADY_INIT)
UnsupportedError = _mkerr("UnsupportedError", ErrClass.UNSUPPORTED)
TimeoutError_ = _mkerr("TimeoutError_", ErrClass.TIMEOUT)
ProcFailedError = _mkerr("ProcFailedError", ErrClass.PROC_FAILED)
ProcFailedPendingError = _mkerr("ProcFailedPendingError", ErrClass.PROC_FAILED_PENDING)
RevokedError = _mkerr("RevokedError", ErrClass.REVOKED)
FencedError = _mkerr("FencedError", ErrClass.FENCED)
CtxExceededError = _mkerr("CtxExceededError", ErrClass.CTX_EXCEEDED)
BudgetExhaustedError = _mkerr("BudgetExhaustedError", ErrClass.BUDGET_EXHAUSTED)
OpFailedError = _mkerr("OpFailedError", ErrClass.OP_FAILED)
LockBusyError = _mkerr("LockBusyError", ErrClass.LOCK_BUSY)
ConflictError = _mkerr("ConflictError", ErrClass.CONFLICT)
LateError = _mkerr("LateError", ErrClass.LATE)

#: Error classes for which retrying the identical command is both safe and the
#: recommended action. The CLI advertises this in its output so agents do not
#: have to reason about it.
RETRYABLE = frozenset(
    {
        ErrClass.TIMEOUT,
        ErrClass.LOCK_BUSY,
        ErrClass.CONFLICT,
        ErrClass.PROC_FAILED_PENDING,
    }
)
