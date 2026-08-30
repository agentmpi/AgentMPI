"""AgentMPI error classes.

MPI reports errors through integer error codes grouped into error classes, and
by default installs ``MPI_ERRORS_ARE_FATAL``.  AgentMPI keeps the error-class
taxonomy (it is genuinely useful for a harness to branch on *why* a call
failed) but inverts the default: because agent ranks fail routinely rather
than exceptionally, the default error handler is ``ERRORS_RETURN``.  See
``docs/spec/agentmpi-spec.md`` section 9.
"""

from __future__ import annotations


class AmpiError(Exception):
    """Base class for every AgentMPI error.

    ``cls_name`` mirrors MPI's error *class* (a coarse, stable identifier that
    harnesses may branch on) while the message carries the instance detail.
    """

    cls_name = "ERR_OTHER"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message)
        self.context = context

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        if not self.context:
            return base
        detail = " ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"{base} [{detail}]" if base else detail


class AmpiUsageError(AmpiError):
    """The harness called the API incorrectly (analogue of MPI_ERR_ARG)."""

    cls_name = "ERR_ARG"


class AmpiRankError(AmpiError):
    cls_name = "ERR_RANK"


class AmpiTagError(AmpiError):
    cls_name = "ERR_TAG"


class AmpiCommError(AmpiError):
    cls_name = "ERR_COMM"


class AmpiTruncateError(AmpiError):
    """Receive buffer (context budget) too small for the incoming payload.

    The analogue of ``MPI_ERR_TRUNCATE``: in MPI the receive buffer is too
    short for the message; in AgentMPI the receiving rank's *context budget*
    cannot admit the payload.  Raised by an eager receive; avoided by a
    rendezvous receive plus an explicit view.
    """

    cls_name = "ERR_TRUNCATE"


class AmpiContextOverflow(AmpiError):
    """A rank exceeded its context budget (failure mode F5, "fail-greedy")."""

    cls_name = "ERR_CONTEXT_OVERFLOW"


class AmpiTypeError(AmpiError):
    """Contract (datatype) mismatch between sender and receiver."""

    cls_name = "ERR_TYPE"


class AmpiTimeout(AmpiError):
    cls_name = "ERR_TIMEOUT"


class AmpiRevoked(AmpiError):
    """The communicator has been revoked (ULFM ``MPIX_ERR_REVOKED``)."""

    cls_name = "ERR_REVOKED"


class AmpiProcFailed(AmpiError):
    """A peer rank in the communicator has failed (``MPIX_ERR_PROC_FAILED``)."""

    cls_name = "ERR_PROC_FAILED"

    def __init__(self, message: str = "", failed: tuple[int, ...] = (), **context: object) -> None:
        super().__init__(message, **context)
        self.failed = tuple(failed)


class AmpiProcFailedPending(AmpiError):
    cls_name = "ERR_PROC_FAILED_PENDING"


class AmpiLockError(AmpiError):
    cls_name = "ERR_RMA_CONFLICT"


class AmpiFabricError(AmpiError):
    cls_name = "ERR_FABRIC"


class AmpiValidationError(AmpiError):
    """A payload failed its structural or semantic contract (failure F3/F4)."""

    cls_name = "ERR_VALIDATION"


ERROR_CLASSES: tuple[type[AmpiError], ...] = (
    AmpiUsageError,
    AmpiRankError,
    AmpiTagError,
    AmpiCommError,
    AmpiTruncateError,
    AmpiContextOverflow,
    AmpiTypeError,
    AmpiTimeout,
    AmpiRevoked,
    AmpiProcFailed,
    AmpiProcFailedPending,
    AmpiLockError,
    AmpiFabricError,
    AmpiValidationError,
)
