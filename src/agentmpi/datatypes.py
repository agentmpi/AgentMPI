"""AgentMPI datatypes.

An MPI datatype answers one question: *how are these bytes laid out?*  That
is sufficient because the sender and the receiver are the same deterministic
program compiled for the same machine.  An AgentMPI datatype must answer
three questions instead:

1. **Layout** -- how is the payload rendered into a prompt (its *view*)?
2. **Contract** -- what may the receiver assume about the content, and how
   is that assumption checked on arrival?
3. **Bound** -- how many tokens may an instance of this type cost, and how
   is an oversized instance reduced?

Question 3 is the reason AgentMPI cannot simply borrow MPI's type system.
In MPI, ``count`` and ``datatype`` let the *receiver* size a buffer; a
message that does not fit is a program error (``MPI_ERR_TRUNCATE``).  In
AgentMPI the receiver's buffer is its context window, it is small, it is
consumed permanently, and it is shared with everything else the agent is
doing.  So the bound travels with the type, and the runtime is empowered to
*reduce* an oversized payload rather than reject it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .constants import Datatype
from .errors import ContractError
from .tokens import DigestFn, count_tokens, truncate_to_tokens

Validator = Callable[[Any], Sequence[str]]
"""A validator returns a (possibly empty) sequence of violation strings."""


@dataclass(frozen=True)
class TypeDescriptor:
    """A concrete AgentMPI datatype handle.

    Attributes
    ----------
    base:
        The wire-level kind of the payload.
    name:
        Human-readable name used in traces and prompts.
    max_tokens:
        Token bound.  ``None`` means unbounded, which is legal but means the
        runtime cannot do admission control for this type.
    schema:
        For ``JSON`` payloads, a minimal JSON-Schema-like description used by
        the built-in validator.  Only ``type``, ``required``, ``properties``
        and ``items`` are interpreted; the goal is a cheap, dependency-free
        contract check, not full schema compliance.
    validators:
        Extra validators run after the schema check.
    digest:
        Reduction function ``(text, budget) -> text`` used when an instance
        exceeds ``max_tokens``.  Defaults to hard truncation.
    lossy:
        Whether the runtime may apply ``digest``.  A type that is not lossy
        raises :class:`~agentmpi.errors.ContextOverflowError` instead.
    """

    base: Datatype
    name: str
    max_tokens: int | None = None
    schema: dict[str, Any] | None = None
    validators: tuple[Validator, ...] = ()
    digest: DigestFn | None = None
    lossy: bool = True
    extent_hint: int = 0
    """Nominal token extent, the analogue of ``MPI_Type_extent``.  Used by
    collective algorithm selection to predict peak ingest before any data
    exists."""

    # -- rendering ---------------------------------------------------------
    def render(self, value: Any) -> str:
        """Serialise ``value`` into the textual form an agent will read."""
        if value is None:
            return ""
        if self.base is Datatype.JSON:
            return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        if self.base is Datatype.TEXT or self.base is Datatype.PATCH:
            return value if isinstance(value, str) else str(value)
        if self.base is Datatype.ARTIFACT:
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            return str(value)
        if self.base is Datatype.TOOLCALL:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        if self.base is Datatype.DIGEST:
            return str(value)
        return "" if self.base is Datatype.NULL else str(value)

    def parse(self, text: str) -> Any:
        """Inverse of :meth:`render` (``MPI_Unpack``)."""
        if self.base in (Datatype.JSON, Datatype.TOOLCALL):
            if text.strip() == "":
                return None
            return json.loads(text)
        if self.base is Datatype.ARTIFACT:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text

    def size_tokens(self, value: Any) -> int:
        return count_tokens(self.render(value))

    # -- contract ----------------------------------------------------------
    def check(self, value: Any) -> tuple[str, ...]:
        """Return the contract violations of ``value`` (empty if conforming)."""
        problems: list[str] = []
        if self.base is Datatype.JSON and self.schema is not None:
            problems.extend(_check_schema(value, self.schema, path="$"))
        if self.base is Datatype.PATCH and isinstance(value, str):
            if value.strip() and not any(
                line.startswith(("--- ", "+++ ", "@@", "diff --git"))
                for line in value.splitlines()
            ):
                problems.append("payload declared as PATCH is not a unified diff")
        for validator in self.validators:
            problems.extend(validator(value))
        return tuple(problems)

    def validate(self, value: Any) -> None:
        problems = self.check(value)
        if problems:
            raise ContractError(
                f"payload does not satisfy datatype {self.name!r}",
                violations=tuple(problems),
                datatype=self.name,
            )

    # -- bound -------------------------------------------------------------
    def fit(self, value: Any) -> tuple[Any, bool]:
        """Reduce ``value`` to fit ``max_tokens``.

        Returns ``(value, was_reduced)``.  Raises when the type forbids loss.
        """
        if self.max_tokens is None:
            return value, False
        text = self.render(value)
        if count_tokens(text) <= self.max_tokens:
            return value, False
        if not self.lossy:
            from .errors import ContextOverflowError

            raise ContextOverflowError(
                f"payload of {count_tokens(text)} tokens exceeds the "
                f"{self.max_tokens}-token bound of non-lossy type {self.name!r}",
                datatype=self.name,
            )
        digest = self.digest or (lambda t, b: truncate_to_tokens(t, b))
        reduced_text = digest(text, self.max_tokens)
        if self.base is Datatype.JSON:
            # A digested JSON document is no longer guaranteed to parse;
            # demote it to a DIGEST-typed string, which is honest about the
            # loss rather than producing an invalid document.
            return {"_ampi_digest": reduced_text}, True
        return self.parse(reduced_text) if self.base is not Datatype.TEXT else reduced_text, True


def _check_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    problems: list[str] = []
    expected = schema.get("type")
    kinds = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    if expected and expected in kinds and not isinstance(value, kinds[expected]):
        problems.append(f"{path}: expected {expected}, got {type(value).__name__}")
        return problems
    if expected == "object" and isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}: missing required property {key!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                problems.extend(_check_schema(value[key], sub, f"{path}.{key}"))
    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            problems.append(f"{path}: expected at least {min_items} items, got {len(value)}")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                problems.extend(_check_schema(item, item_schema, f"{path}[{i}]"))
    if isinstance(value, str):
        min_len = schema.get("minLength")
        if isinstance(min_len, int) and len(value) < min_len:
            problems.append(f"{path}: string shorter than minLength {min_len}")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        problems.append(f"{path}: value {value!r} not in enum {enum}")
    return problems


# --------------------------------------------------------------------------
# Predefined datatypes (the AgentMPI analogue of MPI_INT, MPI_DOUBLE, ...)
# --------------------------------------------------------------------------

TEXT = TypeDescriptor(Datatype.TEXT, "AMPI_TEXT")
JSON_ = TypeDescriptor(Datatype.JSON, "AMPI_JSON")
PATCH = TypeDescriptor(Datatype.PATCH, "AMPI_PATCH")
ARTIFACT = TypeDescriptor(Datatype.ARTIFACT, "AMPI_ARTIFACT", max_tokens=256)
TOOLCALL = TypeDescriptor(Datatype.TOOLCALL, "AMPI_TOOLCALL")
DIGEST = TypeDescriptor(Datatype.DIGEST, "AMPI_DIGEST", max_tokens=512)
NULL = TypeDescriptor(Datatype.NULL, "AMPI_NULL", max_tokens=0)

_PREDEFINED = {
    "text": TEXT,
    "json": JSON_,
    "patch": PATCH,
    "artifact": ARTIFACT,
    "toolcall": TOOLCALL,
    "digest": DIGEST,
    "null": NULL,
}


def lookup(name: str) -> TypeDescriptor:
    key = name.lower().removeprefix("ampi_")
    if key not in _PREDEFINED:
        raise ContractError(f"unknown datatype {name!r}")
    return _PREDEFINED[key]


# --------------------------------------------------------------------------
# Type constructors (MPI_Type_contiguous / _struct / _create_resized ...)
# --------------------------------------------------------------------------

def type_bounded(
    base: TypeDescriptor,
    max_tokens: int,
    digest: DigestFn | None = None,
    *,
    lossy: bool = True,
    name: str | None = None,
) -> TypeDescriptor:
    """``AMPI_Type_bounded`` -- attach a token bound to an existing type.

    This constructor has no MPI counterpart and is the single most
    load-bearing piece of the AgentMPI type system: it is what lets the
    runtime reason about whether a collective will fit in its participants'
    context windows *before* running it.
    """
    return TypeDescriptor(
        base=base.base,
        name=name or f"{base.name}<={max_tokens}",
        max_tokens=max_tokens,
        schema=base.schema,
        validators=base.validators,
        digest=digest or base.digest,
        lossy=lossy,
        extent_hint=max_tokens,
    )


def type_contract(
    base: TypeDescriptor,
    schema: dict[str, Any] | None = None,
    validators: Sequence[Validator] = (),
    *,
    name: str | None = None,
) -> TypeDescriptor:
    """``AMPI_Type_contract`` -- attach a checkable contract to a type."""
    return TypeDescriptor(
        base=base.base,
        name=name or f"{base.name}/contract",
        max_tokens=base.max_tokens,
        schema=schema if schema is not None else base.schema,
        validators=tuple(base.validators) + tuple(validators),
        digest=base.digest,
        lossy=base.lossy,
        extent_hint=base.extent_hint,
    )


def type_struct(
    fields: dict[str, TypeDescriptor],
    *,
    required: Sequence[str] | None = None,
    name: str = "AMPI_STRUCT",
) -> TypeDescriptor:
    """``AMPI_Type_struct`` -- a record of named, individually typed fields."""
    schema: dict[str, Any] = {
        "type": "object",
        "required": list(required if required is not None else fields.keys()),
        "properties": {k: (v.schema or {}) for k, v in fields.items()},
    }
    bounds = [v.max_tokens for v in fields.values()]
    total = sum(b for b in bounds if b is not None) if all(b is not None for b in bounds) else None
    return TypeDescriptor(
        base=Datatype.JSON,
        name=name,
        max_tokens=total,
        schema=schema,
        extent_hint=total or 0,
    )


def type_contiguous(count: int, base: TypeDescriptor, *, name: str | None = None) -> TypeDescriptor:
    """``AMPI_Type_contiguous`` -- ``count`` consecutive instances of ``base``."""
    bound = None if base.max_tokens is None else base.max_tokens * count
    return TypeDescriptor(
        base=Datatype.JSON,
        name=name or f"{count}x{base.name}",
        max_tokens=bound,
        schema={"type": "array", "items": base.schema or {}, "minItems": count},
        extent_hint=(base.extent_hint or 0) * count,
    )


@dataclass
class TypeRegistry:
    """Per-run registry so that datatypes can be named in the CLI and traces."""

    types: dict[str, TypeDescriptor] = field(default_factory=lambda: dict(_PREDEFINED))

    def register(self, key: str, descriptor: TypeDescriptor) -> TypeDescriptor:
        self.types[key.lower()] = descriptor
        return descriptor

    def get(self, key: str) -> TypeDescriptor:
        k = key.lower().removeprefix("ampi_")
        if k not in self.types:
            raise ContractError(f"unknown datatype {key!r}")
        return self.types[k]
