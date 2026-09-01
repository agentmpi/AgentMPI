"""AgentMPI error classes.

Two properties distinguish this catalogue from MPI's.

First, the default error behaviour is *return*, not abort.  ``MPI_ERRORS_ARE_FATAL``
is defensible when an error means a bug in a deterministic program; here errors are
routine events with well-defined meanings and a caller almost always has something
useful to do about them.

Second, every error carries a ``hint``: a sentence naming the concrete next action.
Errors in this system are read by language models.  An error that says what to *do*
is acted on; one that merely says what went wrong is frequently not.  This is not
cosmetic --- section S10 of the specification makes it normative --- and the
``retryable`` flag exists for the same reason: an executor deciding whether to
re-issue a call should not have to infer the answer from prose.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AmpiError",
    "ERROR_CLASSES",
    "RETRYABLE",
    "TERMINAL",
    "err",
]


class AmpiError(Exception):
    """Base class for every AgentMPI error.

    Attributes:
        cls_name: the stable error class, e.g. ``AMPI_ERR_TIMEOUT``.  Harnesses
            branch on this; the message is for humans and models.
        hint: the concrete next action.
        detail: structured context (rank numbers, labels, token counts).
    """

    def __init__(
        self,
        cls_name: str,
        message: str,
        *,
        hint: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if cls_name not in ERROR_CLASSES:
            raise KeyError(f"unknown AgentMPI error class {cls_name!r}")
        self.cls_name = cls_name
        self.message = message
        self.hint = hint or ERROR_CLASSES[cls_name].hint
        self.detail = detail or {}

    @property
    def retryable(self) -> bool:
        return self.cls_name in RETRYABLE

    @property
    def terminal(self) -> bool:
        return self.cls_name in TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.cls_name,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "terminal": self.terminal,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.cls_name}: {self.message}"


class _Class:
    __slots__ = ("name", "meaning", "hint", "retryable", "terminal")

    def __init__(
        self,
        name: str,
        meaning: str,
        hint: str,
        *,
        retryable: bool = False,
        terminal: bool = False,
    ) -> None:
        self.name = name
        self.meaning = meaning
        self.hint = hint
        self.retryable = retryable
        self.terminal = terminal


def _c(name: str, meaning: str, hint: str, **kw: bool) -> tuple[str, _Class]:
    return name, _Class(name, meaning, hint, **kw)


ERROR_CLASSES: dict[str, _Class] = dict(
    [
        _c("AMPI_SUCCESS", "no error", ""),
        # --- argument and naming -------------------------------------------------
        _c(
            "AMPI_ERR_ARG",
            "malformed or missing argument",
            "Re-read the operation's arguments; do not retry with the same values.",
        ),
        _c(
            "AMPI_ERR_RANK",
            "rank out of range, or not a member of the communicator",
            "Run 'ampi comm list' to see the communicator's membership.",
        ),
        _c(
            "AMPI_ERR_TAG",
            "tag out of range, or inside the reserved implementation range",
            "Use a tag in 0..AMPI_TAG_UB, or a symbolic tag.",
        ),
        _c(
            "AMPI_ERR_COMM",
            "no such communicator, or a communicator of the wrong kind",
            "Run 'ampi comm list'.",
        ),
        _c("AMPI_ERR_OP", "no such reduction operator", "Run 'ampi op list'."),
        _c("AMPI_ERR_WIN", "no such window", "Run 'ampi win list'."),
        _c(
            "AMPI_ERR_REQUEST",
            "no such request, or the request was cancelled",
            "Run 'ampi inbox' to see what is actually pending.",
        ),
        _c(
            "AMPI_ERR_TYPE",
            "a payload did not satisfy the contract declared on the operation",
            "Fix the payload to match the declared contract, then re-send.",
        ),
        _c(
            "AMPI_ERR_TRUNCATE",
            "a payload did not fit the declared shape",
            "Declare a larger shape or send a view.",
        ),
        # --- lifecycle -----------------------------------------------------------
        _c(
            "AMPI_ERR_NOT_INIT",
            "the caller has not called AMPI_Init",
            "Run 'ampi init' first.",
        ),
        _c(
            "AMPI_ERR_ALREADY_INIT",
            "duplicate initialisation at the same epoch",
            "You are already initialised; continue with your assigned work.",
        ),
        _c(
            "AMPI_ERR_NO_JOB",
            "no job state was found at the given root",
            "Check AMPI_ROOT, or ask the launcher for the job root.",
        ),
        _c(
            "AMPI_ERR_RUN_EXISTS",
            "a live job already occupies this root",
            "Use a fresh job root, or pass --force to reclaim it.",
        ),
        _c(
            "AMPI_ERR_VERSION",
            "the runtime version pinned by the job differs from this runtime",
            "Install the pinned runtime version; do not edit a runtime under a live job.",
        ),
        # --- identity ------------------------------------------------------------
        _c(
            "AMPI_ERR_IDENTITY",
            "the asserted identity, or the launch token, disagrees with the ambient identity",
            "Your environment is wrong, not your command. Re-read your rank card "
            "and pass --expect-rank explicitly.",
        ),
        _c(
            "AMPI_ERR_FENCED",
            "the caller's epoch is stale; this rank has been replaced",
            "Stop. You have been replaced; a successor holds this rank. Report and exit.",
            terminal=True,
        ),
        # --- flow control --------------------------------------------------------
        _c(
            "AMPI_ERR_CTX_EXCEEDED",
            "delivering the payload would exceed the caller's context budget",
            "Re-issue with --view (for example --view head:400) or --out to save to disk.",
        ),
        _c(
            "AMPI_ERR_CTX_CREDIT",
            "the destination's unexpected-message budget is full",
            "Send by rendezvous, or wait for the destination to consume its inbox.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_BUDGET",
            "a cost limit was reached",
            "Ask the harness for more budget, or reduce the assignment.",
        ),
        # --- progress and failure ------------------------------------------------
        _c(
            "AMPI_ERR_TIMEOUT",
            "the operation's deadline was reached; its state is preserved",
            "Re-issue the identical command; it resumes the same wait.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_PROC_FAILED",
            "a peer required to complete this operation has failed",
            "Run 'ampi failed', then 'ampi ack' and re-issue, or shrink.",
        ),
        _c(
            "AMPI_ERR_PROC_FAILED_PENDING",
            "a peer failed; a wildcard receive may still be completable",
            "Run 'ampi ack' to re-enable wildcard receives, then re-issue.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_REVOKED",
            "the communicator has been revoked",
            "Run 'ampi shrink' to obtain a communicator over the survivors.",
        ),
        _c(
            "AMPI_ERR_LATE",
            "a quorum collective closed without the caller",
            "Read the published result; you are late but not wrong.",
        ),
        _c(
            "AMPI_ERR_COLL_MISMATCH",
            "ranks disagree about which collective is in progress",
            "Run 'ampi doctor'. It names the ranks that disagree and the labels they used.",
        ),
        _c(
            "AMPI_ERR_DEADLOCK",
            "a cycle was detected in the wait-for graph",
            "Run 'ampi doctor' for the cycle. One participant must abandon its wait.",
        ),
        # --- shared state --------------------------------------------------------
        _c(
            "AMPI_ERR_CONFLICT",
            "a versioned write or compare-and-swap lost a race",
            "Re-read the cell and retry; someone else wrote it first.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_LOCK_BUSY",
            "the lock is held by another rank",
            "Retry after the reported lease expiry, or use accumulate instead of lock.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_STALE_LEASE",
            "the caller's lock lease expired and was reclaimed",
            "Re-acquire the lock. Any write you attempted was rejected.",
        ),
        # --- operators -----------------------------------------------------------
        _c(
            "AMPI_ERR_OP_FAILED",
            "an agent operator step was abandoned or returned a malformed result",
            "Re-issue the merge directive; the schedule position is preserved.",
            retryable=True,
        ),
        _c(
            "AMPI_ERR_OP_UNSOUND",
            "the requested algorithm is not sound for the operator's declared algebra",
            "Either declare the operator associative, or ask for the chain schedule.",
        ),
        _c(
            "AMPI_ERR_INVARIANT",
            "a declared post-reduction invariant does not hold on the result",
            "Run 'ampi op conflicts' to see the lifted conflicts the root must decide.",
        ),
        # --- implementation ------------------------------------------------------
        _c(
            "AMPI_ERR_UNSUPPORTED",
            "an optional operation is absent from this implementation",
            "Consult 'ampi conformance' for the levels this implementation provides.",
        ),
        _c("AMPI_ERR_INTERN", "internal error in the implementation", "File a bug."),
    ]
)

RETRYABLE: frozenset[str] = frozenset(n for n, c in ERROR_CLASSES.items() if c.retryable)
TERMINAL: frozenset[str] = frozenset(n for n, c in ERROR_CLASSES.items() if c.terminal)


def err(
    cls_name: str,
    message: str,
    *,
    hint: str = "",
    **detail: Any,
) -> AmpiError:
    """Construct an :class:`AmpiError`; ``detail`` keywords become structured context."""
    return AmpiError(cls_name, message, hint=hint, detail=detail)
