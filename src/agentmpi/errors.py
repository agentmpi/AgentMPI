"""Exception hierarchy and error handlers.

MPI reports errors through return codes plus a per-communicator error
handler, defaulting to ``MPI_ERRORS_ARE_FATAL``.  AgentMPI keeps the
error-handler concept -- it is what makes fault-tolerant code writable --
but defaults to ``ERRORS_RETURN`` because agent failure is expected rather
than exceptional.
"""

from __future__ import annotations

from typing import Any, Callable

from .constants import ERROR_STRINGS, ErrorClass


class AmpiError(Exception):
    """Base class for all AgentMPI errors."""

    error_class: ErrorClass = ErrorClass.ERR_OTHER

    def __init__(self, message: str = "", **context: Any) -> None:
        base = ERROR_STRINGS.get(self.error_class, "error")
        text = f"[{self.error_class.name}] {base}"
        if message:
            text += f": {message}"
        if context:
            details = ", ".join(f"{k}={v!r}" for k, v in sorted(context.items()))
            text += f" ({details})"
        super().__init__(text)
        self.message = message
        self.context = context

    def error_string(self) -> str:
        return str(self)


def _mk(name: str, cls: ErrorClass) -> type[AmpiError]:
    return type(name, (AmpiError,), {"error_class": cls})


ArgError = _mk("ArgError", ErrorClass.ERR_ARG)
CommError = _mk("CommError", ErrorClass.ERR_COMM)
RankError = _mk("RankError", ErrorClass.ERR_RANK)
TagError = _mk("TagError", ErrorClass.ERR_TAG)
TypeError_ = _mk("TypeError_", ErrorClass.ERR_TYPE)
OpError = _mk("OpError", ErrorClass.ERR_OP)
RootError = _mk("RootError", ErrorClass.ERR_ROOT)
RequestError = _mk("RequestError", ErrorClass.ERR_REQUEST)
TruncateError = _mk("TruncateError", ErrorClass.ERR_TRUNCATE)
WinError = _mk("WinError", ErrorClass.ERR_WIN)
RmaSyncError = _mk("RmaSyncError", ErrorClass.ERR_RMA_SYNC)
RmaConflictError = _mk("RmaConflictError", ErrorClass.ERR_RMA_CONFLICT)
FileError = _mk("FileError", ErrorClass.ERR_FILE)
InternalError = _mk("InternalError", ErrorClass.ERR_INTERN)
UnsupportedError = _mk("UnsupportedError", ErrorClass.ERR_UNSUPPORTED)
TimeoutError_ = _mk("TimeoutError_", ErrorClass.ERR_TIMEOUT)
TopologyError = _mk("TopologyError", ErrorClass.ERR_TOPOLOGY)


class ProcFailedError(AmpiError):
    """A peer rank was detected as failed (ULFM ``MPIX_ERR_PROC_FAILED``)."""

    error_class = ErrorClass.ERR_PROC_FAILED

    def __init__(self, message: str = "", failed: tuple[int, ...] = (), **ctx: Any):
        super().__init__(message, failed=list(failed), **ctx)
        self.failed = tuple(failed)


class ProcFailedPendingError(ProcFailedError):
    error_class = ErrorClass.ERR_PROC_FAILED_PENDING


class RevokedError(AmpiError):
    """The communicator was revoked (ULFM ``MPIX_ERR_REVOKED``)."""

    error_class = ErrorClass.ERR_REVOKED


class StalledError(ProcFailedError):
    """A peer is alive but has made no protocol progress within its deadline."""

    error_class = ErrorClass.ERR_STALLED


class ContractError(AmpiError):
    """A payload did not satisfy the contract declared by its datatype.

    This class has no MPI analogue.  MPI can check that a receive buffer is
    large enough; it cannot check that the *content* is what the receiver
    was promised, because in MPI the content is produced by deterministic
    code.  With agents the content is produced by a sampler, so contract
    violation is a first-class, recoverable transport-level error.
    """

    error_class = ErrorClass.ERR_CONTRACT

    def __init__(self, message: str = "", violations: tuple[str, ...] = (), **ctx: Any):
        super().__init__(message, **ctx)
        self.violations = tuple(violations)


class BudgetError(AmpiError):
    """A rank exhausted its token or currency budget."""

    error_class = ErrorClass.ERR_BUDGET


class ContextOverflowError(AmpiError):
    """A message cannot be ingested because it does not fit the receiver.

    The AgentMPI analogue of ``MPI_ERR_TRUNCATE``.  In MPI the receiver
    declares a buffer size and an oversized message is an application bug.
    In AgentMPI the receiver declares a *context capacity*, and the runtime
    is allowed to repair the situation by digesting the payload rather than
    failing -- overflow is only raised when no lossy path is permitted.
    """

    error_class = ErrorClass.ERR_CONTEXT_OVERFLOW


class DriftError(ContractError):
    error_class = ErrorClass.ERR_DRIFT


class NondeterminismError(AmpiError):
    error_class = ErrorClass.ERR_NONDETERMINISM


class CollectiveMismatchError(AmpiError):
    """Peers disagree about which collective is which.

    MPI requires every rank to issue collectives on a communicator in the
    same order, and every MPI implementation relies on it: the n-th
    collective's traffic is separated from the (n+1)-th by a counter that is
    assumed to be replicated.  A rank that skips one desynchronises the
    counter, and from then on it labels its messages with tags nobody is
    listening for.  The job does not fail, it *hangs*, and no rank can see
    why, because each one is correctly waiting for a message that will never
    be sent.

    In MPI this is a programmer error caught by inspection or by a tool such
    as MUST.  With agents it is an ordinary runtime event -- the executor is
    a language model that may decide a step looks unnecessary -- so the
    protocol has to detect it in band.  We do, by carrying the collective's
    name and sequence number in every internal envelope and comparing them
    against what this rank actually executed at that sequence number.  A
    permanent hang becomes an error that names both parties and both
    operations.
    """

    error_class = ErrorClass.ERR_COLL_MISMATCH


# --------------------------------------------------------------------------
# Error handlers (MPI_Comm_set_errhandler)
# --------------------------------------------------------------------------

Errhandler = Callable[[Any, AmpiError], None]


def ERRORS_ARE_FATAL(obj: Any, err: AmpiError) -> None:  # noqa: N802
    """Abort the whole job.  MPI's default; AgentMPI keeps it available."""
    from .runtime import abort  # local import: runtime imports errors

    abort(getattr(obj, "name", "?"), int(err.error_class), str(err))


def ERRORS_RETURN(obj: Any, err: AmpiError) -> None:  # noqa: N802
    """Raise the error to the caller.  AgentMPI's default."""
    raise err


def ERRORS_ABORT(obj: Any, err: AmpiError) -> None:  # noqa: N802
    """Abort only the ranks of the offending communicator (MPI-4)."""
    raise err
