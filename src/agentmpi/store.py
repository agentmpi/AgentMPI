"""Content-addressed artifact store.

Every payload that crosses an AgentMPI communicator is stored once, keyed by
the SHA-256 of its canonical serialisation, and referred to thereafter by that
digest.  Three properties follow, and all three are load-bearing:

1. **Broadcast is drift-free.**  A tree broadcast forwards *handles*, so an
   intermediate rank cannot paraphrase the payload on its way through.  This is
   the single most important reason AgentMPI can use logarithmic-depth
   broadcast algorithms at all; a protocol in which agents retransmit text they
   have re-generated degrades like a game of telephone with depth.
2. **Deduplication is automatic.**  A specification broadcast to 64 ranks and
   quoted back by all 64 costs one blob, and the harness can see that the 64
   replies are byte-identical without reading them.
3. **Replay is exact.**  The event log plus the store is a complete record of
   the run, so protocol-level debugging does not require re-invoking a model.

The store is a plain directory (``blobs/aa/bbbb...``) with fsync-on-write and
atomic rename, so it is safe under concurrent writers and across crashes
without any coordination.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tokens as _tokens
from .errors import AmpiFabricError

#: Payloads are stored as UTF-8.  ``kind`` records how to interpret them.
KIND_TEXT = "text"
KIND_JSON = "json"


def canonical_bytes(payload: Any) -> tuple[bytes, str]:
    """Serialise ``payload`` canonically and report its kind.

    JSON is serialised with sorted keys and no whitespace variation so that two
    structurally equal payloads always hash equal.  Without canonicalisation,
    deduplication and the "did all ranks agree?" check in
    :func:`agentmpi.ft.agree` would be defeated by key ordering.
    """
    if isinstance(payload, str):
        return payload.encode("utf-8"), KIND_TEXT
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), KIND_TEXT
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return data.encode("utf-8"), KIND_JSON


def digest_of(payload: Any) -> str:
    raw, _ = canonical_bytes(payload)
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Blob:
    """A stored artifact."""

    digest: str
    kind: str
    n_bytes: int
    tokens: int

    @property
    def short(self) -> str:
        return self.digest[:12]


class BlobStore:
    """Append-only content-addressed store rooted at ``root``."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64:
            raise AmpiFabricError("malformed digest", digest=digest)
        return self.root / digest[:2] / digest[2:]

    def put(self, payload: Any, *, tokens: int | None = None) -> Blob:
        """Store ``payload`` and return its handle.

        Writing an already-present blob is a no-op, which is what makes the
        store safe to call from many ranks at once with no locking.
        """
        raw, kind = canonical_bytes(payload)
        digest = hashlib.sha256(raw).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        if tokens is None:
            tokens = _tokens.count(payload)
        return Blob(digest=digest, kind=kind, n_bytes=len(raw), tokens=tokens)

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def get_bytes(self, digest: str) -> bytes:
        path = self._path(digest)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise AmpiFabricError("blob not found", digest=digest) from exc

    def get(self, digest: str, kind: str = KIND_JSON) -> Any:
        raw = self.get_bytes(digest)
        if kind == KIND_JSON:
            return json.loads(raw.decode("utf-8"))
        return raw.decode("utf-8")

    def get_text(self, digest: str) -> str:
        return self.get_bytes(digest).decode("utf-8")

    def size(self, digest: str) -> int:
        return self._path(digest).stat().st_size
