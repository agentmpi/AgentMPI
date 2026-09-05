"""Point-to-point communication: matching, send modes, and bounded waits.

MPI's matching rules transfer almost verbatim, and the two that matter are stated
as tests in the conformance suite rather than as prose here: a message is
delivered to the *first posted receive that matches it*, and among messages that
match the same receive they are matched in the order they were sent.  AgentMPI
provides no other ordering guarantee and no fairness guarantee, exactly as MPI
does not.

Three departures, each forced by the executor model.

**Blocking is deadline-bounded and resumable.**  ``MPI_Recv`` blocks forever,
which is tolerable when a peer's failure kills the whole job.  An agent peer may
be slow, wedged, or dead, and no timeout distinguishes them, so the only workable
primitive is a bounded deadline plus state that lets an identical re-issue resume
the same wait rather than start a new one.  A re-issued receive adopts its own
still-open queue entry; it does not post a second.

**The binding retries internally.**  Not a convenience.  An executor instructed to
retry a timed-out call up to twenty times gave up after two and stalled its entire
reduction tree.  A protocol that depends on an executor's persistence is not a
protocol, so the default behaviour is "keep waiting" and abandoning a wait
requires the executor to do something rather than nothing.

**Posted receives are durable.**  An executor may post receives, be replaced, and
have its successor complete them.  This is what makes replacement cheap: the
successor inherits obligations rather than rediscovering them.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from ..constants import (
    ANY_SOURCE,
    ANY_TAG,
    DEFAULT_TIMEOUT_S,
    DELIVERY_AUTO,
    DELIVERY_EAGER,
    DELIVERY_RENDEZVOUS,
    PROC_NULL,
    SEND_READY,
    SEND_STANDARD,
    SEND_SYNCHRONOUS,
    TAG_UB,
)
from ..errors import AmpiError, err
from .context import Ledger, choose_delivery
from .payload import Contract, apply_view, check_contract, contracts_match

__all__ = ["P2PMixin"]

_POLL_S = 0.05
_POLL_MAX_S = 1.0


class P2PMixin:
    """Point-to-point operations.  Mixed into :class:`ampi.Ampi`."""

    # -- helpers -----------------------------------------------------------
    def _check_tag(self, tag: int) -> int:
        if not 0 <= tag <= TAG_UB:
            raise err(
                "AMPI_ERR_TAG",
                f"tag {tag} is outside 0..{TAG_UB}",
                hint=f"Tags above {TAG_UB} are reserved for the runtime's own traffic.",
                tag=tag,
            )
        return tag

    @staticmethod
    def symbolic_tag(name: str) -> int:
        """Map a word onto the user tag range, deterministically.

        Agents use names far more reliably than integers, and a symbolic tag costs
        nothing: it is hashed into the same space the harness would otherwise pick
        from by hand.
        """
        return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % TAG_UB

    def _resolve_tag(self, tag: int | str) -> int:
        if isinstance(tag, str):
            return self.symbolic_tag(tag) if tag not in ("*", "any") else ANY_TAG
        return tag

    def _peer_world_rank(self, comm: str, peer: int) -> int:
        members = self.comm_members(comm)
        if peer in (ANY_SOURCE, PROC_NULL):
            return peer
        if not 0 <= peer < len(members):
            raise err(
                "AMPI_ERR_RANK",
                f"rank {peer} is outside 0..{len(members) - 1} on communicator {comm!r}",
                comm=comm,
                size=len(members),
            )
        return members[peer]

    # -- send --------------------------------------------------------------
    def send(
        self,
        dst: int,
        payload: Any,
        *,
        tag: int | str = 0,
        comm: str = "world",
        mode: str = SEND_STANDARD,
        delivery: str = DELIVERY_AUTO,
        contract: Contract | dict | str | None = None,
        schema: str = "",
        label: str = "",
        idempotency_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Send a payload.

        ``mode`` is MPI's send mode: ``standard`` completes when the message is
        durably enqueued, ``synchronous`` when it has been matched, ``ready``
        immediately but errors unless a matching receive is already posted.
        ``delivery`` is orthogonal and is the agent-specific axis: whether the
        *body* travels into the receiver's context or only an envelope and handle.
        Both are visible to the caller because, unlike in MPI, they differ in what
        arrives and therefore in what the receiver pays.
        """
        self.assert_identity()
        self._fence_check()
        tag = self._check_tag(self._resolve_tag(tag))
        if dst == PROC_NULL:
            return {"ok": True, "proc_null": True}
        world_dst = self._peer_world_rank(comm, dst)
        ctr = Contract.parse(contract)

        p = self.put_payload(payload, schema=schema)
        env = p.envelope

        idem = idempotency_key or f"{self.rank}:{comm}:{world_dst}:{tag}:{env.handle}"
        prior = self.device.scan("msg", {"idem": idem, "run": self.manifest.job_id}, limit=1)
        if prior:
            # A retried send must not duplicate.  Agents retry commands, and a
            # duplicated contribution silently doubles a reduction's input.
            self.trace("send.duplicate-suppressed", rank=self.rank, dst=world_dst, tag=tag)
            return {"ok": True, "duplicate": True, **prior[0].get("envelope", {})}

        violations = check_contract(payload, ctr, subs={"rank": dst, "world_rank": world_dst})
        if violations:
            raise err(
                "AMPI_ERR_TYPE",
                f"payload does not satisfy the declared contract: {violations[0]}",
                hint="Fix the payload to match the contract, then re-send.",
                violations=violations,
            )

        dst_ledger = self.ledger(world_dst)
        chosen = choose_delivery(
            env.tokens,
            requested=delivery,
            eager_threshold=self.manifest.eager_threshold,
            remaining=dst_ledger.remaining,
        )

        if mode == SEND_READY:
            posted = self.device.scan(
                "recvq",
                {
                    "comm": self.comm_context(comm),
                    "dst": world_dst,
                    "state": "open",
                    "run": self.manifest.job_id,
                },
            )
            if not any(self._recvq_accepts(r, self.rank, tag) for r in posted):
                raise err(
                    "AMPI_ERR_ARG",
                    f"ready-mode send to rank {dst} but no matching receive is posted",
                    hint="Ready mode exists to catch schedule bugs early. Use standard mode.",
                )

        if chosen == DELIVERY_EAGER:
            self._await_eager_credit(world_dst, env.tokens, timeout=timeout)

        record = {
            "comm": self.comm_context(comm),
            "src": self.rank,
            "dst": world_dst,
            "tag": tag,
            "state": "posted",
            "run": self.manifest.job_id,
            "epoch": self._rankview().epoch,
            "handle": env.handle,
            "idem": idem,
            "delivery": chosen,
            "mode": mode,
            "label": label,
            "envelope": {**env.to_dict(), "source": self.rank, "tag": tag, "comm": comm},
            "contract": ctr.to_dict() if ctr else None,
        }
        seq = self.device.append("msg", record)
        if chosen == DELIVERY_EAGER:
            self._reserve_eager(world_dst, env.tokens)
        self.trace(
            "send",
            rank=self.rank,
            dst=world_dst,
            tag=tag,
            tokens=env.tokens,
            delivery=chosen,
            mode=mode,
            handle=env.handle,
            comm=comm,
            label=label,
        )

        if mode == SEND_SYNCHRONOUS:
            self._await(
                lambda: not self.device.scan("msg", {"seq": seq, "state": "posted"}, limit=1),
                timeout=timeout,
                what=f"synchronous send to rank {dst} to be matched",
            )
        return {
            "ok": True, "seq": seq, "delivery": chosen,
            **{**env.to_dict(), "source": self.rank, "tag": tag, "comm": comm},
        }

    def isend(self, dst: int, payload: Any, **kw: Any) -> dict[str, Any]:
        """Nonblocking send.  Returns a request handle."""
        kw.setdefault("mode", SEND_STANDARD)
        out = self.send(dst, payload, **kw)
        return {"request": f"s{out.get('seq', 0)}", **out}

    # -- eager credit ------------------------------------------------------
    def _reserve_eager(self, dst: int, tokens: int) -> None:
        view = self._rankview(dst)
        ledger = Ledger.from_dict(view.ctx)
        ledger.unexpected_used += tokens
        view.ctx = ledger.to_dict()
        self._write_rank(view)

    def _release_eager(self, dst: int, tokens: int) -> None:
        view = self._rankview(dst)
        ledger = Ledger.from_dict(view.ctx)
        ledger.unexpected_used = max(0, ledger.unexpected_used - tokens)
        view.ctx = ledger.to_dict()
        self._write_rank(view)

    def _await_eager_credit(self, dst: int, tokens: int, *, timeout: float) -> None:
        """Block until the destination has room for an unmatched eager message.

        This is what turns an invisible quality failure into a reported,
        attributable stall.  The stall is traced, because a harness author needs
        to know that their harness is buffering-dependent even when it happens to
        complete.
        """
        ledger = self.ledger(dst)
        if ledger.unexpected_used + tokens <= ledger.unexpected_budget:
            return
        self.trace("ctx.stall", rank=self.rank, dst=dst, tokens=tokens, held=ledger.unexpected_used)
        started = time.time()
        try:
            self._await(
                lambda: self.ledger(dst).unexpected_used + tokens
                <= self.ledger(dst).unexpected_budget,
                timeout=timeout,
                what=f"rank {dst} to make room for a {tokens}-token eager message",
            )
        except AmpiError as exc:
            if exc.cls_name == "AMPI_ERR_TIMEOUT":
                raise err(
                    "AMPI_ERR_CTX_CREDIT",
                    f"rank {dst} has {ledger.unexpected_used}/{ledger.unexpected_budget} tokens "
                    f"of unmatched eager traffic and cannot accept {tokens} more",
                    hint="Send by rendezvous (--delivery rendezvous). This harness depends on "
                    "the receiver having spare context, which is what context-safety forbids.",
                    dst=dst,
                    tokens=tokens,
                ) from exc
            raise
        finally:
            self.trace("ctx.stall.end", rank=self.rank, dst=dst, waited=time.time() - started)

    # -- receive -----------------------------------------------------------
    @staticmethod
    def _recvq_accepts(entry: dict[str, Any], src: int, tag: int) -> bool:
        return (entry.get("src_want") in (ANY_SOURCE, src)) and (
            entry.get("tag_want") in (ANY_TAG, tag)
        )

    def _post_receive(self, comm: str, src: int, tag: int, *, contract: Contract | None) -> str:
        """Post a durable receive, adopting an identical open one if it exists.

        Adoption is what makes a re-issued receive resume rather than start a new
        wait.  It also means an executor that is replaced mid-wait leaves an
        obligation its successor can discover and complete.
        """
        ctx = self.comm_context(comm)
        existing = self.device.scan(
            "recvq",
            {
                "comm": ctx,
                "dst": self.rank,
                "src_want": src,
                "tag_want": tag,
                "state": "open",
                "run": self.manifest.job_id,
            },
            limit=1,
        )
        if existing:
            return existing[0]["reqid"]
        reqid = uuid.uuid4().hex[:12]
        self.device.append(
            "recvq",
            {
                "comm": ctx,
                "dst": self.rank,
                "src_want": src,
                "tag_want": tag,
                "state": "open",
                "run": self.manifest.job_id,
                "reqid": reqid,
                "posted_at": self.device.clock(),
                "epoch": self._rankview().epoch,
                "contract": contract.to_dict() if contract else None,
            },
        )
        return reqid

    def recv(
        self,
        src: int = ANY_SOURCE,
        *,
        tag: int | str = ANY_TAG,
        comm: str = "world",
        timeout: float = DEFAULT_TIMEOUT_S,
        materialize: bool | None = None,
        view: str = "",
        budget: int | None = None,
        contract: Contract | dict | str | None = None,
        out: str = "",
    ) -> dict[str, Any]:
        """Blocking receive, deadline-bounded and resumable.

        Returns the envelope always, and the body only when it was delivered
        eagerly or the caller asked for materialisation.  ``view`` takes a bounded
        projection; ``out`` writes the body to a file and charges nothing, which
        every operation that hands back a payload must offer --- agents that
        lacked it reached into the object store by hand rather than pay to see
        what was already on disk.
        """
        self.assert_identity()
        self._fence_check()
        tag_i = self._resolve_tag(tag)
        if tag_i != ANY_TAG:
            self._check_tag(tag_i)
        if src == PROC_NULL:
            return {"ok": True, "proc_null": True}
        world_src = self._peer_world_rank(comm, src) if src != ANY_SOURCE else ANY_SOURCE
        ctr = Contract.parse(contract)
        reqid = self._post_receive(comm, world_src, tag_i, contract=ctr)
        ctx = self.comm_context(comm)

        pred: dict[str, Any] = {
            "comm": ctx,
            "dst": self.rank,
            "state": "posted",
            "run": self.manifest.job_id,
        }
        if world_src != ANY_SOURCE:
            pred["src"] = world_src
        if tag_i != ANY_TAG:
            pred["tag"] = tag_i

        rec = self._match_with_deadline(pred, reqid, timeout=timeout, comm=comm)
        return self._deliver(
            rec,
            reqid=reqid,
            materialize=materialize,
            view=view,
            budget=budget,
            contract=ctr,
            out=out,
        )

    def _match_with_deadline(
        self, pred: dict[str, Any], reqid: str, *, timeout: float, comm: str
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        wait = _POLL_S
        while True:
            rec = self.device.match(
                "msg", pred, {"state": "claimed", "claimed_by": self.rank, "reqid": reqid}
            )
            if rec is not None:
                return rec
            self.touch()
            failures = self.detect_failures()
            self._check_revoked(comm)
            if pred.get("src") is not None and any(f.rank == pred["src"] for f in failures):
                raise err(
                    "AMPI_ERR_PROC_FAILED",
                    f"rank {pred['src']} has failed and cannot send the message you are waiting for",
                    hint="Run 'ampi failed' for detail, then 'ampi ack' and re-issue, or shrink.",
                    src=pred["src"],
                )
            if "src" not in pred:
                # Only failures the caller has not yet acknowledged may mask a
                # wildcard receive.  Without the acknowledgement step the error is
                # permanent, because there is always some failure the caller has
                # not been told about, and the receive can never return a timeout.
                unacked = sorted({
                    rec["rank"]
                    for rec in self.device.scan(
                        "fail", {"state": "unacked", "run": self.manifest.job_id}
                    )
                })
                if unacked:
                    raise err(
                        "AMPI_ERR_PROC_FAILED_PENDING",
                        f"rank(s) {unacked} failed while a wildcard receive was posted",
                        hint="Run 'ampi ack' to re-enable wildcard receives, then re-issue.",
                        failed=unacked,
                    )
            if time.time() >= deadline:
                waiting_on = "any peer" if "src" not in pred else f"rank {pred['src']}"
                raise err(
                    "AMPI_ERR_TIMEOUT",
                    f"no matching message from {waiting_on} within {timeout:.0f}s",
                    hint="Re-issue the identical command; it resumes this same wait. "
                    "Run 'ampi doctor' if it times out again.",
                    reqid=reqid,
                    waiting_on=waiting_on,
                )
            time.sleep(wait)
            wait = min(_POLL_MAX_S, wait * 1.6)

    def _deliver(
        self,
        rec: dict[str, Any],
        *,
        reqid: str,
        materialize: bool | None,
        view: str,
        budget: int | None,
        contract: Contract | None,
        out: str,
    ) -> dict[str, Any]:
        env = dict(rec.get("envelope") or {})
        sender_contract = Contract.parse(rec.get("contract"))
        ok, why = contracts_match(sender_contract, contract)
        if not ok:
            raise err("AMPI_ERR_TYPE", why, hint="The endpoints declared incompatible contracts.")

        self.device.match(
            "recvq", {"reqid": reqid, "state": "open"}, {"state": "satisfied"}
        )
        if rec.get("delivery") == "eager":
            self._release_eager(self.rank, int(env.get("tokens", 0)))

        result: dict[str, Any] = {
            "ok": True,
            "source": rec["src"],
            "tag": rec["tag"],
            "envelope": env,
            "delivery": rec.get("delivery"),
            "label": rec.get("label", ""),
        }

        want_body = materialize if materialize is not None else rec.get("delivery") == "eager"
        if out:
            from pathlib import Path

            body = self.get_body(env["handle"])
            text = body if isinstance(body, str) else __import__("json").dumps(body, indent=2)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text, encoding="utf-8")
            result["saved_to"] = out
            result["charged"] = 0
            self.trace("recv", rank=self.rank, src=rec["src"], tag=rec["tag"], charged=0, saved=out)
            return result

        if not want_body and not view:
            result["charged"] = self.charge(min(40, int(env.get("tokens", 0))), what="envelope")[0]
            result["next"] = (
                f"ampi recv --materialize  # or: ampi obj get {env['handle']} --view head:400"
            )
            self.trace(
                "recv", rank=self.rank, src=rec["src"], tag=rec["tag"], charged=result["charged"]
            )
            return result

        body = self.get_body(env["handle"])
        if view:
            body = apply_view(body, view)
        from ..tokens import count_tokens
        from .payload import canonical

        tokens = count_tokens(canonical(body))
        if budget is not None and tokens > budget:
            body = apply_view(body, f"headtail:{budget}")
            tokens = count_tokens(canonical(body))
        charged, degraded = self.charge(tokens, what="body")
        if degraded:
            body = apply_view(body, degraded)
            result["degraded_to"] = degraded
        if contract is not None:
            violations = check_contract(body, contract, subs={"rank": self.comm_rank()})
            if violations:
                result["violations"] = violations
        result["body"] = body
        result["charged"] = charged
        self.trace("recv", rank=self.rank, src=rec["src"], tag=rec["tag"], charged=charged)
        return result

    def irecv(self, src: int = ANY_SOURCE, *, tag: int | str = ANY_TAG, comm: str = "world") -> dict[str, Any]:
        """Post a receive without waiting.  Returns a request handle."""
        self.assert_identity()
        tag_i = self._resolve_tag(tag)
        world_src = self._peer_world_rank(comm, src) if src != ANY_SOURCE else ANY_SOURCE
        reqid = self._post_receive(comm, world_src, tag_i, contract=None)
        return {"request": reqid, "src": world_src, "tag": tag_i, "comm": comm}

    def test(self, request: str) -> dict[str, Any]:
        if request.startswith("c|"):
            return self.coll_test(request)  # type: ignore[attr-defined]
        entries = self.device.scan("recvq", {"reqid": request}, limit=1)
        if not entries:
            raise err("AMPI_ERR_REQUEST", f"no such request {request!r}")
        entry = entries[0]
        if entry["state"] == "satisfied":
            return {"complete": True, "request": request}
        matched = self.device.scan(
            "msg", {"reqid": request, "state": "claimed"}, limit=1
        )
        return {"complete": bool(matched), "request": request}

    def wait(self, request: str, *, timeout: float = DEFAULT_TIMEOUT_S, **kw: Any) -> dict[str, Any]:
        if request.startswith("c|"):
            return self.coll_wait(request, timeout=timeout, **kw)  # type: ignore[attr-defined]
        entries = self.device.scan("recvq", {"reqid": request}, limit=1)
        if not entries:
            raise err("AMPI_ERR_REQUEST", f"no such request {request!r}")
        entry = entries[0]
        pred: dict[str, Any] = {
            "comm": entry["comm"],
            "dst": self.rank,
            "state": "posted",
            "run": self.manifest.job_id,
        }
        if entry["src_want"] != ANY_SOURCE:
            pred["src"] = entry["src_want"]
        if entry["tag_want"] != ANY_TAG:
            pred["tag"] = entry["tag_want"]
        rec = self._match_with_deadline(pred, request, timeout=timeout, comm="world")
        return self._deliver(
            rec, reqid=request, materialize=kw.get("materialize"), view=kw.get("view", ""),
            budget=kw.get("budget"), contract=None, out=kw.get("out", ""),
        )

    def cancel(self, request: str) -> dict[str, Any]:
        ok = self.device.match("recvq", {"reqid": request, "state": "open"}, {"state": "cancelled"})
        if ok is None:
            raise err("AMPI_ERR_REQUEST", f"request {request!r} is not open")
        return {"cancelled": request}

    # -- combined and inspection -------------------------------------------
    def sendrecv(
        self,
        dst: int,
        payload: Any,
        src: int,
        *,
        send_tag: int | str = 0,
        recv_tag: int | str = ANY_TAG,
        comm: str = "world",
        timeout: float = DEFAULT_TIMEOUT_S,
        **kw: Any,
    ) -> dict[str, Any]:
        """Exchange in one call.

        Must not deadlock when every rank in a shift uses it symmetrically, which
        is why it exists: it is the primitive that makes a ring exchange safe
        without the harness author having to reason about odd and even ranks.
        """
        self.irecv(src, tag=recv_tag, comm=comm)
        sent = self.send(dst, payload, tag=send_tag, comm=comm, delivery=DELIVERY_RENDEZVOUS)
        got = self.recv(src, tag=recv_tag, comm=comm, timeout=timeout, **kw)
        return {"sent": sent, "received": got}

    def probe(
        self,
        src: int = ANY_SOURCE,
        *,
        tag: int | str = ANY_TAG,
        comm: str = "world",
        blocking: bool = False,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Inspect the next matching message's envelope without receiving it.

        In MPI a probe mainly sizes a buffer.  Here it lets an executor see what is
        waiting *and what it would cost* before committing context, which is the
        basis of every context-aware scheduling decision a harness can make.
        """
        self.assert_identity()
        tag_i = self._resolve_tag(tag)
        pred: dict[str, Any] = {
            "comm": self.comm_context(comm),
            "dst": self.rank,
            "state": "posted",
            "run": self.manifest.job_id,
        }
        if src != ANY_SOURCE:
            pred["src"] = self._peer_world_rank(comm, src)
        if tag_i != ANY_TAG:
            pred["tag"] = tag_i
        deadline = time.time() + (timeout if blocking else 0)
        wait = _POLL_S
        while True:
            found = self.device.scan("msg", pred, limit=1)
            if found:
                rec = found[0]
                return {"available": True, "source": rec["src"], "tag": rec["tag"],
                        "envelope": rec.get("envelope", {}), "delivery": rec.get("delivery")}
            if not blocking or time.time() >= deadline:
                return {"available": False}
            self.touch()
            time.sleep(wait)
            wait = min(_POLL_MAX_S, wait * 1.6)

    def inbox(self, *, comm: str = "world", limit: int | None = None) -> dict[str, Any]:
        """Everything pending for the caller, with its token cost."""
        self.assert_identity()
        pending = self.device.scan(
            "msg",
            {
                "comm": self.comm_context(comm),
                "dst": self.rank,
                "state": "posted",
                "run": self.manifest.job_id,
            },
            limit=limit,
        )
        ledger = self.ledger()
        items = [
            {
                "source": r["src"],
                "tag": r["tag"],
                "tokens": (r.get("envelope") or {}).get("tokens", 0),
                "summary": (r.get("envelope") or {}).get("summary", ""),
                "handle": r.get("handle"),
                "delivery": r.get("delivery"),
                "label": r.get("label", ""),
            }
            for r in pending
        ]
        return {
            "rank": self.rank,
            "pending": len(items),
            "total_tokens": sum(i["tokens"] for i in items),
            "context_remaining": ledger.remaining,
            "items": items,
        }

    # -- shared waiting ----------------------------------------------------
    def _await(self, predicate: Any, *, timeout: float, what: str) -> None:
        """Poll a predicate while discharging the obligations of a blocked rank.

        A blocked rank must renew its own lease, run the failure detector, and
        observe revocation.  Skipping the first is what made a barrier convict
        everyone who arrived early.
        """
        deadline = time.time() + timeout
        wait = _POLL_S
        while not predicate():
            self.touch()
            self.detect_failures()
            if time.time() >= deadline:
                raise err(
                    "AMPI_ERR_TIMEOUT",
                    f"timed out after {timeout:.0f}s waiting for {what}",
                    hint="Re-issue the identical command to resume the wait, "
                    "or run 'ampi doctor'.",
                )
            time.sleep(wait)
            wait = min(_POLL_MAX_S, wait * 1.6)

    def _check_revoked(self, comm: str) -> None:
        info = self.comm_info(comm)
        if info.get("state") == "revoked":
            raise err(
                "AMPI_ERR_REVOKED",
                f"communicator {comm!r} has been revoked",
                hint="Run 'ampi shrink' to obtain a communicator over the survivors.",
                comm=comm,
            )
