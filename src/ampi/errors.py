"""Exception hierarchy mirroring the AgentMPI error classes."""

from __future__ import annotations

from typing import Any

from .constants import (
    AMPI_ERR_ARG,
    AMPI_ERR_BUDGET_EXCEEDED,
    AMPI_ERR_COLLECTIVE_MISMATCH,
    AMPI_ERR_COMM,
    AMPI_ERR_CONTEXT_EXHAUSTED,
    AMPI_ERR_DEADLOCK,
    AMPI_ERR_PROC_FAILED,
    AMPI_ERR_PROTOCOL_VIOLATION,
    AMPI_ERR_RANK,
    AMPI_ERR_REVOKED,
    AMPI_ERR_STALE_INCARNATION,
    AMPI_ERR_STALE_RUN,
    AMPI_ERR_TIMEOUT,
    ERROR_NAMES,
)


class AmpiError(Exception):
    """Base class.  Carries an MPI-style integer error class."""

    error_class: int = AMPI_ERR_ARG

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    @property
    def error_name(self) -> str:
        return ERROR_NAMES.get(self.error_class, "AMPI_ERR_UNKNOWN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error_class": self.error_class,
            "error": self.error_name,
            "message": self.message,
            "detail": self.detail,
        }


class AmpiArgError(AmpiError):
    error_class = AMPI_ERR_ARG


class AmpiCommError(AmpiError):
    error_class = AMPI_ERR_COMM


class AmpiRankError(AmpiError):
    error_class = AMPI_ERR_RANK


class AmpiTimeout(AmpiError):
    error_class = AMPI_ERR_TIMEOUT


class AmpiRevoked(AmpiError):
    """The communicator was revoked; every subsequent operation on it fails.

    Direct analogue of MPI_ERR_REVOKED from ULFM.  Revocation is what makes it
    possible to unstick peers that are blocked on a rank that will never reply.
    """

    error_class = AMPI_ERR_REVOKED


class AmpiProcFailed(AmpiError):
    """A peer required to complete this operation has been declared failed."""

    error_class = AMPI_ERR_PROC_FAILED


class AmpiContextExhausted(AmpiError):
    """Delivering this payload would exceed the receiver's context budget.

    No MPI counterpart: MPI receive buffers are sized by the application and
    overflow is a truncation error.  Here the buffer is a finite, shared,
    monotonically filling context window, so the runtime must refuse the
    transfer and offer a projection instead.
    """

    error_class = AMPI_ERR_CONTEXT_EXHAUSTED


class AmpiCollectiveMismatch(AmpiError):
    """Ranks issued different collectives, or the same collective out of order.

    MPI declares this undefined behaviour.  AgentMPI diagnoses it, because an
    LLM rank misreading its instructions is an expected event rather than a
    programmer bug that will be found once and fixed forever.
    """

    error_class = AMPI_ERR_COLLECTIVE_MISMATCH


class AmpiDeadlock(AmpiError):
    error_class = AMPI_ERR_DEADLOCK


class AmpiProtocolViolation(AmpiError):
    error_class = AMPI_ERR_PROTOCOL_VIOLATION


class AmpiBudgetExceeded(AmpiError):
    error_class = AMPI_ERR_BUDGET_EXCEEDED


class AmpiStaleIncarnation(AmpiError):
    """Another process has since taken over this rank.

    No MPI counterpart, because an MPI rank is a process and cannot be
    impersonated.  An AgentMPI rank is a name that any process holding the job
    directory can claim, so a blocked call left over from an abandoned attempt
    will happily consume the next attempt's messages.  We observed exactly
    that: a stale root from an earlier run matched the new run's contributions
    and completed a reduction that mixed two generations of ranks.
    """

    error_class = AMPI_ERR_STALE_INCARNATION


class AmpiStaleRun(AmpiError):
    """The job store now belongs to a different run.

    Paths are deployment details and may be reused.  A runtime that captured
    one run identity must never act on state belonging to a later run at the
    same path.
    """

    error_class = AMPI_ERR_STALE_RUN
