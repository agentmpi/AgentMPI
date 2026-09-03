"""The command binding: the surface an LLM executor calls.

For an LLM executor the binding is a command-line tool, because that is the only
interface an agent reliably has to a stateful library.  It cannot hold a handle
across turns, cannot link a shared object, and its "function calls" are invocations
whose output lands in its context window.

Eight properties are normative for a conforming binding, and every one of them is
here because its absence cost us a run.

1. *Identity is ambient but assertable.*  ``AMPI_RANK`` by default, ``--expect-rank``
   to say "I intend to be rank 5" and be told loudly when the environment says
   otherwise.
2. *Every operation echoes the acting identity.*  When identity is ambient, the
   only defence against it having changed is being told, on every call, who you
   just were.
3. *Errors prescribe.*  Every error carries a concrete next action and a
   ``retryable`` flag.  Errors here are read by language models; one that says what
   to *do* is acted on, one that merely says what went wrong often is not.
4. *Blocking calls retry internally.*  An executor told to retry up to twenty times
   gave up after two and stalled its reduction tree.
5. *Sizes are in tokens, always.*  A budget the constrained party cannot evaluate
   is a guess, and parties guess conservatively.
6. *Everything that hands back a payload offers ``--out``.*  A free path to disk,
   charging no context.  Agents that lacked it reached into the object store by
   hand rather than pay to see what was already a file.
7. *Print only commands that exist.*  An early reduction directive printed a
   subcommand spelled with a space where the real one is hyphenated; roughly ten
   agents reported it, several while peers were blocked behind them.  Every command
   string this binding emits is checked against the parser by a test.
8. *The manual is a command.*  ``ampi man`` prints the operational guide, so an
   executor whose context was trimmed can re-read it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
    ENV_JOB,
    ENV_RANK,
    ENV_ROOT,
    PROTOCOL_VERSION,
)
from .core.payload import VIEW_SPECS
from .errors import AmpiError
from .runtime import Ampi, conformance

__all__ = ["main", "build_parser"]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def emit(payload: dict[str, Any], *, rank: int | None, job: str = "", pretty: bool = True) -> None:
    """Print a result, always naming the acting identity."""
    body = {"ok": True, **payload}
    if rank is not None:
        body.setdefault("acting_as", {"rank": rank, "job": job})
    text = json.dumps(body, indent=2 if pretty else None, ensure_ascii=False, default=str)
    print(text)


def fail(exc: AmpiError, *, rank: int | None, job: str = "") -> int:
    body = exc.to_dict()
    if rank is not None:
        body["acting_as"] = {"rank": rank, "job": job}
    print(json.dumps(body, indent=2, ensure_ascii=False, default=str), file=sys.stderr)
    if exc.retryable:
        print("AMPI_RETRY: this error is retryable; re-issue the identical command.",
              file=sys.stderr)
    return 2 if exc.terminal else 1


# --------------------------------------------------------------------------
# The manual
# --------------------------------------------------------------------------

MANUAL = f"""\
AgentMPI {PROTOCOL_VERSION} -- operational guide for an executor acting as a rank

WHO YOU ARE
  Your rank number and job root are in your environment ({ENV_RANK}, {ENV_ROOT}).
  Every command echoes "acting_as" so you can see who the runtime thinks you are.
  If that is ever not the rank you were told to be, STOP and report it; add
  --expect-rank N to any command to make the runtime check before it acts.

THE SHAPE OF EVERY CALL
  ampi <command> [--rank N] [--job-root DIR] [--expect-rank N] [--timeout S]
  Output is JSON on stdout.  Errors are JSON on stderr and carry:
    "error"     the stable class, e.g. AMPI_ERR_TIMEOUT
    "hint"      what to do about it -- read this first
    "retryable" if true, re-issuing the identical command is correct

THE RULES THAT MATTER
  1. If a blocking call times out, RE-ISSUE THE IDENTICAL COMMAND.  It resumes the
     same wait; it does not start a new one.  Do not give up after two tries.
  2. Before any step you expect to take more than a couple of minutes, run
     `ampi hb --extend 900`.  Otherwise your peers may conclude you have died.
  3. Always enter a collective you were told to enter, even if you have nothing
     good to contribute.  Contribute a degraded value and say so.  A rank that
     skips a collective blocks every other rank in it.
  4. Never invent a command.  If a command string is printed for you in a "commit"
     or "next" field, copy it exactly.
  5. Sizes are in tokens.  `ampi ctx` tells you your budget and what you have
     spent.  `ampi inbox` tells you what is waiting and what reading it would cost.
  6. Anything that hands you a payload accepts --out FILE, which writes it to disk
     and charges you nothing.  Prefer that for anything large.
  7. Record your progress after each phase: `ampi memo phase '<what you finished>'`.
     If you are replaced, your successor is given your memos and nothing else.

VIEWS (bounded projections; deterministic, and free of model calls)
{chr(10).join(f"  {k:<16} {v}" for k, v in VIEW_SPECS.items())}

WHEN SOMETHING IS WRONG
  ampi doctor       names the rank that has not arrived, the cycle, or the conflict
  ampi status       every rank's state, epoch and context usage
  ampi failed       who has been declared failed, and why
  ampi recover      what you were assigned, published, promised, and recorded
"""


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def _common(p: argparse.ArgumentParser) -> None:
    # SUPPRESS, not None: argparse applies a subparser's defaults *after* parsing
    # the top-level options, so a concrete default here would silently overwrite a
    # --rank given before the subcommand and reintroduce the bug this fixes.
    p.add_argument("--rank", type=int, default=argparse.SUPPRESS,
                   help="acting rank (default: $AMPI_RANK)")
    p.add_argument("--job-root", default=argparse.SUPPRESS,
                   help="job directory (default: $AMPI_ROOT)")
    p.add_argument("--expect-rank", type=int, default=argparse.SUPPRESS,
                   help="fail before acting if the ambient rank differs")
    p.add_argument("--expect-job", default=argparse.SUPPRESS,
                   help="fail before acting if the job id differs")
    p.add_argument("--comm", default="world", help="communicator name")
    p.add_argument("--compact", action="store_true", help="single-line JSON")


def _wait(p: argparse.ArgumentParser) -> None:
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                   help="internal retries on timeout; the binding does not rely on you")


def _payload(p: argparse.ArgumentParser) -> None:
    p.add_argument("--payload", default=None, help="inline payload (JSON, else treated as text)")
    p.add_argument("--payload-file", default=None, help="read the payload from a file")


def _take(p: argparse.ArgumentParser) -> None:
    p.add_argument("--materialize", action="store_true", help="deliver the body into your context")
    p.add_argument("--view", default="", help=f"bounded projection; one of {', '.join(VIEW_SPECS)}")
    p.add_argument("--budget", type=int, default=None, help="cap the delivery at N tokens")
    p.add_argument("--out", default="", help="write the body to a file and charge nothing")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser("ampi", description="AgentMPI command binding")
    ap.add_argument("--version", action="version", version=f"AgentMPI {PROTOCOL_VERSION}")
    # The identity flags are accepted *before* the subcommand as well as after it.
    # Every executor in the hundred-rank run wrote them before, because that is
    # where a reader expects a global option, and every one of them lost its first
    # call to "invalid choice: /workspace/runs/.../job".  The specification says a
    # binding must print only commands that exist; the corollary, learned here, is
    # that a binding whose identity flags are positional will be got wrong by
    # everyone.  Defaults are SUPPRESS so that a value given after the subcommand
    # is not silently overwritten by the global default.
    for flag, kw in (
        ("--rank", {"type": int}),
        ("--job-root", {}),
        ("--expect-rank", {"type": int}),
        ("--expect-job", {}),
    ):
        ap.add_argument(flag, default=argparse.SUPPRESS, **kw)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # -- job and lifecycle -------------------------------------------------
    p = sub.add_parser("new", help="create a job")
    p.add_argument("--root", required=True)
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--device", default="sqlite", choices=["sqlite", "journal", "memory", "git", "gitd"])
    p.add_argument("--ctx-budget", type=int, default=None)
    p.add_argument("--eager-threshold", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--compact", action="store_true")

    for name, helptext in [("init", "join the job"), ("fini", "leave the job")]:
        p = sub.add_parser(name, help=helptext)
        _common(p)
        if name == "init":
            p.add_argument("--role", default="")
            p.add_argument("--reinit", action="store_true")

    p = sub.add_parser("hb", help="renew your lease, optionally for longer")
    _common(p)
    p.add_argument("--extend", type=float, default=0.0,
                   help="seconds you expect the next step to take")

    for name, helptext in [
        ("info", "protocol, job and conformance"),
        ("whoami", "who the runtime thinks you are"),
        ("ctx", "your context budget and what you have spent"),
        ("status", "every rank's state"),
        ("doctor", "diagnose a wedged job"),
        ("failed", "who has failed, and why"),
        ("recover", "your recovery briefing"),
        ("inbox", "what is waiting, and what it would cost"),
        ("conformance", "what this implementation provides"),
        ("man", "this manual"),
    ]:
        p = sub.add_parser(name, help=helptext)
        _common(p)

    p = sub.add_parser("ctx-release", help="declare a fresh executor turn")
    _common(p)
    p.add_argument("--tokens", type=int, default=None)

    p = sub.add_parser("memo", help="record or read your own progress note")
    _common(p)
    p.add_argument("key")
    p.add_argument("value", nargs="?", default=None)

    # -- point to point -----------------------------------------------------
    p = sub.add_parser("send", help="send a payload to one rank")
    _common(p); _wait(p); _payload(p)
    p.add_argument("--dst", type=int, required=True)
    p.add_argument("--tag", default="0")
    p.add_argument("--mode", default="standard", choices=["standard", "synchronous", "ready"])
    p.add_argument("--delivery", default="auto", choices=["auto", "eager", "rendezvous"])
    p.add_argument("--contract", default=None, help="JSON contract the payload must satisfy")
    p.add_argument("--label", default="")

    p = sub.add_parser("recv", help="receive one payload")
    _common(p); _wait(p); _take(p)
    p.add_argument("--src", type=int, default=ANY_SOURCE)
    p.add_argument("--tag", default="any")
    p.add_argument("--contract", default=None)
    p.add_argument("--expect", action="append", default=[],
                   help="KEY=VALUE the payload must carry, with {rank} expanded")

    p = sub.add_parser("probe", help="inspect the next message without receiving it")
    _common(p); _wait(p)
    p.add_argument("--src", type=int, default=ANY_SOURCE)
    p.add_argument("--tag", default="any")
    p.add_argument("--blocking", action="store_true")

    # -- collectives --------------------------------------------------------
    p = sub.add_parser("barrier", help="synchronise")
    _common(p); _wait(p)
    p.add_argument("--label", required=True)
    p.add_argument("--quorum", type=float, default=1.0)
    p.add_argument("--policy", default="wait", choices=["wait", "raise", "proceed", "shrink", "revoke"])

    p = sub.add_parser("bcast", help="root publishes; everyone reads")
    _common(p); _wait(p); _payload(p); _take(p)
    p.add_argument("--label", required=True)
    p.add_argument("--root", type=int, default=0)

    p = sub.add_parser("scatter", help="root distributes one slice per rank")
    _common(p); _wait(p); _payload(p); _take(p)
    p.add_argument("--label", required=True)
    p.add_argument("--root", type=int, default=0)
    p.add_argument("--expect", action="append", default=[])

    for name, helptext in [("gather", "collect into the root"), ("allgather", "collect into everyone")]:
        p = sub.add_parser(name, help=helptext)
        _common(p); _wait(p); _payload(p); _take(p)
        p.add_argument("--label", required=True)
        p.add_argument("--root", type=int, default=0)
        p.add_argument("--quorum", type=float, default=1.0)

    for name, helptext in [("reduce", "reduce into the root"), ("allreduce", "reduce into everyone")]:
        p = sub.add_parser(name, help=helptext)
        _common(p); _wait(p); _payload(p)
        p.add_argument("--label", required=True)
        p.add_argument("--op", default="concat", help="a runtime operator, or agent:<label>")
        p.add_argument("--root", type=int, default=0)
        p.add_argument("--algorithm", default=None)
        p.add_argument("--quorum", type=float, default=1.0)
        p.add_argument("--operand-budget", type=int, default=None)
        p.add_argument("--out", default="")

    for name in ("scan", "exscan"):
        p = sub.add_parser(name, help="prefix reduction")
        _common(p); _wait(p); _payload(p)
        p.add_argument("--label", required=True)
        p.add_argument("--op", default="concat")

    p = sub.add_parser("alltoall", help="every rank sends one item to every rank")
    _common(p); _wait(p); _payload(p)
    p.add_argument("--label", required=True)

    p = sub.add_parser("op", help="agent-operator continuation and arbitration")
    _common(p); _wait(p)
    opsub = p.add_subparsers(dest="opcmd", required=True)
    q = opsub.add_parser("commit", help="record a merge result")
    q.add_argument("--label", required=True)
    q.add_argument("--step", type=int, required=True)
    q.add_argument("--result", default=None)
    q.add_argument("--result-file", default=None)
    q = opsub.add_parser("arbitrate", help="decide every lifted conflict, once")
    q.add_argument("--label", required=True)
    q.add_argument("--rulings", default=None, help="JSON object of key -> decision")
    q = opsub.add_parser("list", help="the operator catalogue and its algebra")

    # -- communicators and topology -----------------------------------------
    p = sub.add_parser("comm", help="communicator management")
    _common(p); _wait(p)
    csub = p.add_subparsers(dest="commcmd", required=True)
    csub.add_parser("list")
    q = csub.add_parser("dup"); q.add_argument("--name", default=None)
    q = csub.add_parser("create")
    q.add_argument("--name", required=True)
    q.add_argument("--members", required=True, help="comma-separated world ranks")
    q = csub.add_parser("split")
    q.add_argument("--colour", type=int, default=None)
    q.add_argument("--key", type=int, default=None)
    q.add_argument("--label", default="split")
    q = csub.add_parser("cart")
    q.add_argument("--dims", required=True, help="comma-separated")
    q.add_argument("--periodic", default="")
    q = csub.add_parser("shift")
    q.add_argument("--direction", type=int, default=0)
    q.add_argument("--disp", type=int, default=1)
    q = csub.add_parser("graph")
    q.add_argument("--edges", required=True, help='JSON {"0": [1,2], ...}')
    q.add_argument("--symmetric", action="store_true")
    csub.add_parser("neighbours")
    q = csub.add_parser("revoke"); q.add_argument("--reason", default="")
    q = csub.add_parser("shrink"); q.add_argument("--in-place", action="store_true")

    p = sub.add_parser("neighbor-allgather", help="exchange with declared neighbours only")
    _common(p); _wait(p); _payload(p); _take(p)
    p.add_argument("--label", required=True)

    # -- windows -------------------------------------------------------------
    p = sub.add_parser("win", help="shared state")
    _common(p); _wait(p)
    wsub = p.add_subparsers(dest="wincmd", required=True)
    wsub.add_parser("list")
    q = wsub.add_parser("create"); q.add_argument("name")
    q = wsub.add_parser("put")
    q.add_argument("win"); q.add_argument("key"); _payload(q)
    q.add_argument("--expect-version", type=int, default=None)
    q.add_argument("--lock-token", type=int, default=None)
    q = wsub.add_parser("get")
    q.add_argument("win"); q.add_argument("key"); _take(q)
    q.add_argument("--version", type=int, default=None)
    q = wsub.add_parser("acc")
    q.add_argument("win"); q.add_argument("key"); _payload(q)
    q.add_argument("--op", default="union")
    q = wsub.add_parser("cas")
    q.add_argument("win"); q.add_argument("key")
    q.add_argument("--expect", required=True); q.add_argument("--value", required=True)
    q = wsub.add_parser("claim"); q.add_argument("win"); q.add_argument("key")
    q.add_argument("--note", default="")
    q = wsub.add_parser("faop")
    q.add_argument("win"); q.add_argument("key")
    q.add_argument("--op", default="sum"); q.add_argument("--value", default="1")
    q = wsub.add_parser("ls"); q.add_argument("win"); q.add_argument("--prefix", default="")
    q = wsub.add_parser("hist"); q.add_argument("win"); q.add_argument("key")
    q.add_argument("--limit", type=int, default=20)
    q = wsub.add_parser("fence"); q.add_argument("win"); q.add_argument("--label", required=True)
    q.add_argument("--quorum", type=float, default=1.0)
    q = wsub.add_parser("lock"); q.add_argument("win"); q.add_argument("key")
    q.add_argument("--mode", default="exclusive", choices=["exclusive", "shared"])
    q.add_argument("--ttl", type=float, default=300.0)
    q = wsub.add_parser("unlock"); q.add_argument("lock_id")

    # -- interfaces ------------------------------------------------------------
    p = sub.add_parser("iface", help="declare, discover and verify interfaces")
    _common(p); _wait(p)
    isub = p.add_subparsers(dest="ifacecmd", required=True)
    q = isub.add_parser("publish"); q.add_argument("name"); _payload(q)
    q.add_argument("--iface-version", default="1")
    q = isub.add_parser("list"); q.add_argument("--name", default="")
    q = isub.add_parser("get"); q.add_argument("provider", type=int); q.add_argument("name")
    q.add_argument("--view", default="")
    q = isub.add_parser("wait"); q.add_argument("name")
    q.add_argument("--providers", type=int, default=1)
    q = isub.add_parser("verify")
    q.add_argument("provider", type=int); q.add_argument("name")
    q.add_argument("--holds", default="true", choices=["true", "false"])
    q.add_argument("--evidence", default="")
    isub.add_parser("report")

    # -- fault tolerance ---------------------------------------------------------
    p = sub.add_parser("agree", help="fault-tolerant agreement")
    _common(p); _wait(p)
    p.add_argument("--label", required=True)
    p.add_argument("--value", default="true")
    p.add_argument("--quorum", type=float, default=1.0)

    p = sub.add_parser("ack", help="acknowledge known failures, re-enabling wildcard receives")
    _common(p)

    p = sub.add_parser("kill", help="administratively declare a rank failed")
    _common(p)
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--reason", default="injected")

    p = sub.add_parser("respawn", help="allocate a fresh epoch for a rank")
    _common(p)
    p.add_argument("--target", type=int, required=True)

    p = sub.add_parser("supervise", help="one detect-and-replace pass")
    _common(p)
    p.add_argument("--max-restarts", type=int, default=3)

    # -- objects, views, planning, tracing -----------------------------------------
    p = sub.add_parser("obj", help="the content-addressed payload store")
    _common(p)
    osub = p.add_subparsers(dest="objcmd", required=True)
    q = osub.add_parser("get"); q.add_argument("handle"); _take(q)
    q = osub.add_parser("put"); _payload(q)

    p = sub.add_parser("view", help="project a file or payload into a bounded token count")
    p.add_argument("spec")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default="")
    p.add_argument("--compact", action="store_true")

    p = sub.add_parser("tokens", help="count tokens, with the counter the runtime uses")
    p.add_argument("--file", default=None)
    p.add_argument("--text", default=None)
    p.add_argument("--limit", type=int, default=None, help="exit non-zero if over this many")
    p.add_argument("--compact", action="store_true")

    p = sub.add_parser("plan", help="explain an algorithm selection before paying for it")
    p.add_argument("collective")
    p.add_argument("--size", type=int, required=True)
    p.add_argument("--tokens", type=int, default=4000)
    p.add_argument("--op", default=None)
    p.add_argument("--ctx-limit", type=int, default=None)
    p.add_argument("--compact", action="store_true")

    # -- the broker's worker side ------------------------------------------------
    p = sub.add_parser("worker", help="claim and submit work from a per-rank pull queue")
    _common(p)
    p.add_argument("--campaign", required=True)
    p.add_argument("--serve", default="",
                   help="comma-separated extra ranks this executor serves (oversubscription)")
    wksub = p.add_subparsers(dest="workercmd", required=True)
    q = wksub.add_parser("next", help="claim the next task, blocking server-side")
    q.add_argument("--timeout", type=float, default=240.0)
    q = wksub.add_parser("submit", help="submit the result file named by the task")
    q.add_argument("--aid", required=True)
    q = wksub.add_parser("give-up", help="abandon a task you cannot do")
    q.add_argument("--aid", required=True)
    q.add_argument("--reason", required=True)
    wksub.add_parser("stats", help="the campaign's task counts")

    p = sub.add_parser("trace", help="the event log")
    _common(p)
    p.add_argument("--kind", default=None)
    p.add_argument("--of-rank", type=int, default=None)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--export", default="")

    p = sub.add_parser("analyze", help="measure and visualise a run from its trace")
    _common(p)
    p.add_argument("--trace", default="", help="a .trace.jsonl file; omit to read the live job")
    p.add_argument("--name", default="", help="run name used in the report")
    p.add_argument("--out", default="", help="directory for metrics, figures and report")
    p.add_argument("--tex-prefix", default="", help="emit LaTeX macros with this prefix")
    p.add_argument("--format", default="pdf", choices=["pdf", "png", "svg"])
    p.add_argument("--json", action="store_true", help="print metrics instead of the digest")

    p = sub.add_parser("viewer", help="serve a live trace viewer over HTTP")
    _common(p)
    p.add_argument("--trace", default="", help="a finished .trace.jsonl; omit to read the live job")
    p.add_argument("--campaign", default="", help="also show this campaign's broker queue")
    p.add_argument("--name", default="")
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 - the point is to be reachable
    p.add_argument("--port", type=int, default=7842)
    p.add_argument("--refresh", type=float, default=5.0, help="browser poll interval, seconds")

    return ap


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def _payload_of(a: argparse.Namespace) -> Any:
    raw: str | None = None
    if getattr(a, "payload_file", None):
        raw = Path(a.payload_file).read_text(encoding="utf-8")
    elif getattr(a, "payload", None) is not None:
        raw = a.payload
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _expect_contract(a: argparse.Namespace) -> dict[str, Any] | None:
    """Build a contract from ``--expect KEY=VALUE`` pairs.

    The pragmatic form of a self-identifying payload.  ``--expect rank={rank}``
    on a scatter turns a misrouted slice into a loud error at the receiver rather
    than a plausible wrong answer several phases later.
    """
    pairs = getattr(a, "expect", None) or []
    if not pairs:
        return json.loads(a.contract) if getattr(a, "contract", None) else None
    expect = {}
    for item in pairs:
        k, _, v = item.partition("=")
        expect[k.strip()] = v.strip()
    base = json.loads(a.contract) if getattr(a, "contract", None) else {}
    base.setdefault("kind", "json")
    base["expect"] = expect
    return base


def _tag(value: str) -> int | str:
    if value in ("any", "*"):
        return ANY_TAG
    try:
        return int(value)
    except ValueError:
        return value


def _open(a: argparse.Namespace) -> Ampi:
    root = getattr(a, "job_root", None) or os.environ.get(ENV_ROOT)
    if not root:
        raise AmpiError(
            "AMPI_ERR_NO_JOB",
            f"no job root: pass --job-root or set {ENV_ROOT}",
            hint="Your rank card names the job root.",
        )
    return Ampi(
        root,
        rank=getattr(a, "rank", None),
        expect_rank=getattr(a, "expect_rank", None),
        expect_job=getattr(a, "expect_job", None) or os.environ.get(ENV_JOB) or None,
        allow_volatile=True,
    )


def _dispatch(a: argparse.Namespace) -> tuple[dict[str, Any], int | None, str]:
    cmd = a.cmd

    # Commands that need no job -------------------------------------------
    if cmd == "new":
        job = Ampi.create(
            a.root, a.size, device=a.device, force=a.force,
            **({"ctx_budget": a.ctx_budget} if a.ctx_budget else {}),
            **({"eager_threshold": a.eager_threshold} if a.eager_threshold else {}),
            allow_volatile=True,
        )
        tokens = {str(r): job.device.read("token", str(r)).value for r in range(a.size)}
        return ({"created": job.root, "job": job.manifest.job_id, "size": a.size,
                 "device": a.device, "launch_tokens": tokens,
                 "next": f"AMPI_ROOT={job.root} AMPI_RANK=0 ampi init"}, None, "")
    if cmd == "man":
        print(MANUAL)
        raise SystemExit(0)
    if cmd == "conformance":
        return conformance(), None, ""
    if cmd == "view":
        from .core.payload import apply_view
        raw = Path(a.file).read_text(encoding="utf-8") if a.file else (a.payload or "")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        got = apply_view(value, a.spec)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(
                got if isinstance(got, str) else json.dumps(got, indent=2), encoding="utf-8"
            )
            return {"spec": a.spec, "saved_to": a.out}, None, ""
        from .tokens import count_tokens
        return {"spec": a.spec, "tokens": count_tokens(got), "body": got}, None, ""
    if cmd == "tokens":
        from .tokens import count_tokens, counter_name
        text = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
        n = count_tokens(text)
        out = {"tokens": n, "counter": counter_name()}
        if a.limit is not None:
            out["limit"] = a.limit
            out["fits"] = n <= a.limit
            if n > a.limit:
                print(json.dumps(out, indent=2))
                raise SystemExit(3)
        return out, None, ""
    if cmd == "plan":
        from .core.algorithms import explain_selection, select_algorithm
        from .core.ops import get_op
        op = get_op(a.op) if a.op else None
        d = select_algorithm(a.collective, a.size, tokens=a.tokens, op=op, ctx_limit=a.ctx_limit)
        return ({**d.to_dict(),
                 "gamma_sweep": explain_selection(a.collective, a.size, tokens=a.tokens, op=op)},
                None, "")

    if cmd == "viewer":
        from ampitools.analysis.server import serve

        serve(
            job_root=None if a.trace else (a.job_root or os.environ.get(ENV_ROOT)),
            trace=a.trace or None,
            campaign=a.campaign,
            name=a.name,
            host=a.host,
            port=a.port,
            refresh=a.refresh,
        )
        return ({"served": True}, None, "")

    if cmd == "analyze":
        from ampitools.analysis import analyse, load_events, summary
        from ampitools.analysis.report import write_all

        if a.trace:
            events = load_events(a.trace)
            name = a.name or Path(a.trace).name.replace(".trace.jsonl", "")
        else:
            amp = _open(a)
            events = amp.events()
            name = a.name or amp.manifest.job_id
        an = analyse(events, name=name)
        out: dict[str, Any] = {"run": name, "metrics": an.to_dict()} if a.json else {}
        if a.out:
            written = write_all(an, a.out, tex_prefix=a.tex_prefix, fmt=a.format)
            out["written"] = {k: str(v) for k, v in written.items()}
        if not a.json:
            print(summary(an))
            return ({"run": name, **({"written": out["written"]} if a.out else {})}, None, "")
        return (out, None, "")

    # Everything else needs a job -----------------------------------------
    amp = _open(a)
    job = amp.manifest.job_id
    rank = None
    try:
        rank = amp.rank
    except AmpiError:
        pass

    if cmd == "init":
        return amp.init(role=a.role, reinit=a.reinit), amp.rank, job
    if cmd == "fini":
        return amp.finalize(), amp.rank, job
    if cmd == "hb":
        return amp.heartbeat(extend=a.extend), amp.rank, job
    if cmd == "info":
        return amp.info(), rank, job
    if cmd == "whoami":
        amp.assert_identity()
        v = amp._rankview()
        return {"rank": v.rank, "epoch": v.epoch, "state": v.state, "role": v.role,
                "job": job, "size": amp.size}, amp.rank, job
    if cmd == "ctx":
        return amp.ledger().to_dict(), amp.rank, job
    if cmd == "ctx-release":
        return amp.ctx_release(a.tokens), amp.rank, job
    if cmd == "memo":
        value = a.value
        if value is not None:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        return amp.memo(a.key, value), amp.rank, job
    if cmd == "status":
        return amp.status(), rank, job
    if cmd == "doctor":
        from ampitools.doctor import diagnose
        return diagnose(amp), rank, job
    if cmd == "failed":
        return amp.failures(comm=a.comm), rank, job
    if cmd == "recover":
        return amp.recover(), amp.rank, job
    if cmd == "inbox":
        return amp.inbox(comm=a.comm), amp.rank, job

    if cmd == "send":
        return (amp.send(a.dst, _payload_of(a), tag=_tag(a.tag), comm=a.comm, mode=a.mode,
                         delivery=a.delivery, contract=_expect_contract(a), label=a.label,
                         timeout=a.timeout), amp.rank, job)
    if cmd == "recv":
        return (amp.recv(a.src, tag=_tag(a.tag), comm=a.comm, timeout=a.timeout,
                         materialize=a.materialize or None, view=a.view, budget=a.budget,
                         contract=_expect_contract(a), out=a.out), amp.rank, job)
    if cmd == "probe":
        return (amp.probe(a.src, tag=_tag(a.tag), comm=a.comm, blocking=a.blocking,
                          timeout=a.timeout), amp.rank, job)

    if cmd == "barrier":
        return (amp.barrier(a.label, comm=a.comm, quorum=a.quorum, timeout=a.timeout,
                            policy=a.policy), amp.rank, job)
    if cmd == "bcast":
        return (amp.bcast(a.label, payload=_payload_of(a), root=a.root, comm=a.comm,
                          timeout=a.timeout, materialize=a.materialize, view=a.view,
                          out=a.out), amp.rank, job)
    if cmd == "scatter":
        return (amp.scatter(a.label, payload=_payload_of(a), root=a.root, comm=a.comm,
                            timeout=a.timeout, materialize=a.materialize or not a.out,
                            view=a.view, out=a.out, contract=_expect_contract(a)),
                amp.rank, job)
    if cmd in ("gather", "allgather"):
        return (amp.gather(a.label, payload=_payload_of(a), root=a.root, comm=a.comm,
                           quorum=a.quorum, timeout=a.timeout, materialize=a.materialize,
                           view=a.view, budget=a.budget, everyone=cmd == "allgather"),
                amp.rank, job)
    if cmd in ("reduce", "allreduce"):
        out = amp.reduce(a.label, payload=_payload_of(a), op=a.op, root=a.root, comm=a.comm,
                         everyone=cmd == "allreduce", algorithm=a.algorithm, quorum=a.quorum,
                         timeout=a.timeout, operand_budget=a.operand_budget)
        if a.out and "value" in out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(out["value"], indent=2), encoding="utf-8")
            out["saved_to"] = a.out
        return out, amp.rank, job
    if cmd in ("scan", "exscan"):
        return (amp.scan(a.label, payload=_payload_of(a), op=a.op, comm=a.comm,
                         exclusive=cmd == "exscan", timeout=a.timeout), amp.rank, job)
    if cmd == "alltoall":
        return (amp.alltoall(a.label, payload=_payload_of(a), comm=a.comm, timeout=a.timeout),
                amp.rank, job)

    if cmd == "op":
        if a.opcmd == "commit":
            raw = (Path(a.result_file).read_text(encoding="utf-8") if a.result_file
                   else (a.result or ""))
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw
            return amp.op_commit(a.label, a.step, value, comm=a.comm), amp.rank, job
        if a.opcmd == "arbitrate":
            rulings = json.loads(a.rulings) if a.rulings else None
            return amp.op_arbitrate(a.label, comm=a.comm, rulings=rulings), amp.rank, job
        from .core.ops import OPS
        return {"operators": [o.to_dict() for o in OPS.values()]}, rank, job

    if cmd == "comm":
        c = a.commcmd
        if c == "list":
            return {"communicators": amp.comm_list()}, rank, job
        if c == "dup":
            return amp.comm_dup(a.comm, name=a.name), amp.rank, job
        if c == "create":
            members = [int(x) for x in a.members.split(",") if x.strip()]
            return amp.comm_create(a.name, members, parent=a.comm), amp.rank, job
        if c == "split":
            return (amp.comm_split(a.colour, key=a.key, comm=a.comm, label=a.label,
                                   timeout=a.timeout), amp.rank, job)
        if c == "cart":
            dims = [int(x) for x in a.dims.split(",")]
            periodic = [x.strip().lower() in ("1", "true", "yes")
                        for x in a.periodic.split(",")] if a.periodic else None
            return amp.cart_create(dims, periodic=periodic, comm=a.comm), amp.rank, job
        if c == "shift":
            return amp.cart_shift(a.comm, a.direction, a.disp), amp.rank, job
        if c == "graph":
            edges = {int(k): v for k, v in json.loads(a.edges).items()}
            return amp.graph_create(edges, comm=a.comm, symmetric=a.symmetric), amp.rank, job
        if c == "neighbours":
            return amp.neighbours(a.comm), amp.rank, job
        if c == "revoke":
            return amp.comm_revoke(a.comm, reason=a.reason), rank, job
        if c == "shrink":
            return amp.comm_shrink(a.comm, in_place=a.in_place, timeout=a.timeout), amp.rank, job

    if cmd == "neighbor-allgather":
        return (amp.neighbor_allgather(a.label, payload=_payload_of(a), comm=a.comm,
                                       timeout=a.timeout, materialize=a.materialize,
                                       view=a.view), amp.rank, job)

    if cmd == "win":
        w = a.wincmd
        if w == "list":
            return {"windows": amp.win_list_windows(comm=a.comm)}, amp.rank, job
        if w == "create":
            return amp.win_create(a.name, comm=a.comm), amp.rank, job
        if w == "put":
            return (amp.put(a.win, a.key, _payload_of(a), comm=a.comm,
                            expect_version=a.expect_version, lock_token=a.lock_token),
                    amp.rank, job)
        if w == "get":
            return (amp.get(a.win, a.key, comm=a.comm, version=a.version, view=a.view,
                            budget=a.budget, out=a.out), amp.rank, job)
        if w == "acc":
            return amp.accumulate(a.win, a.key, _payload_of(a), op=a.op, comm=a.comm), amp.rank, job
        if w == "cas":
            def parse(x: str) -> Any:
                try:
                    return json.loads(x)
                except json.JSONDecodeError:
                    return x
            return (amp.compare_and_swap(a.win, a.key, parse(a.expect), parse(a.value),
                                         comm=a.comm), amp.rank, job)
        if w == "claim":
            return amp.claim(a.win, a.key, comm=a.comm, note=a.note), amp.rank, job
        if w == "faop":
            try:
                value = json.loads(a.value)
            except json.JSONDecodeError:
                value = a.value
            return amp.fetch_and_op(a.win, a.key, op=a.op, value=value, comm=a.comm), amp.rank, job
        if w == "ls":
            return amp.win_ls(a.win, prefix=a.prefix, comm=a.comm), amp.rank, job
        if w == "hist":
            return amp.win_history(a.win, a.key, comm=a.comm, limit=a.limit), amp.rank, job
        if w == "fence":
            return (amp.win_fence(a.win, a.label, comm=a.comm, timeout=a.timeout,
                                  quorum=a.quorum), amp.rank, job)
        if w == "lock":
            return (amp.win_lock(a.win, a.key, comm=a.comm, mode=a.mode, ttl=a.ttl,
                                 timeout=a.timeout), amp.rank, job)
        if w == "unlock":
            return amp.win_unlock(a.lock_id), amp.rank, job

    if cmd == "iface":
        i = a.ifacecmd
        if i == "publish":
            return (amp.iface_publish(a.name, _payload_of(a), comm=a.comm,
                                      version=a.iface_version), amp.rank, job)
        if i == "list":
            return amp.iface_list(comm=a.comm, name=a.name), amp.rank, job
        if i == "get":
            return amp.iface_get(a.provider, a.name, comm=a.comm, view=a.view), amp.rank, job
        if i == "wait":
            return (amp.iface_wait(a.name, comm=a.comm, providers=a.providers,
                                   timeout=a.timeout), amp.rank, job)
        if i == "verify":
            return (amp.iface_verify(a.provider, a.name, comm=a.comm,
                                     holds=a.holds == "true", evidence=a.evidence),
                    amp.rank, job)
        if i == "report":
            return amp.iface_report(comm=a.comm), amp.rank, job

    if cmd == "agree":
        try:
            value = json.loads(a.value)
        except json.JSONDecodeError:
            value = a.value
        return (amp.comm_agree(a.label, value, comm=a.comm, quorum=a.quorum,
                               timeout=a.timeout), amp.rank, job)
    if cmd == "ack":
        return amp.failure_ack(comm=a.comm), amp.rank, job
    if cmd == "kill":
        return amp.kill(a.target, reason=a.reason), rank, job
    if cmd == "respawn":
        return amp.respawn(a.target), rank, job
    if cmd == "supervise":
        return amp.supervise(max_restarts=a.max_restarts), rank, job

    if cmd == "obj":
        if a.objcmd == "get":
            body = amp.get_body(a.handle)
            if a.view:
                from .core.payload import apply_view
                body = apply_view(body, a.view)
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(
                    body if isinstance(body, str) else json.dumps(body, indent=2),
                    encoding="utf-8",
                )
                return {"handle": a.handle, "saved_to": a.out, "charged": 0}, amp.rank, job
            from .core.payload import canonical
            from .tokens import count_tokens
            charged, degraded = amp.charge(count_tokens(canonical(body)), what="obj.get")
            out = {"handle": a.handle, "body": body, "charged": charged}
            if degraded:
                from .core.payload import apply_view
                out["body"] = apply_view(body, degraded)
                out["degraded_to"] = degraded
            return out, amp.rank, job
        p = amp.put_payload(_payload_of(a))
        return {"handle": p.envelope.handle, **p.envelope.to_dict()}, amp.rank, job

    if cmd == "worker":
        from ampitools.executor import BrokerExecutor

        # The worker subcommand is the only surface the agents in our experiments
        # ever touch, and it skipped this call.  Two executors reported that
        # --expect-rank did not protect them and they were right: the assertion
        # was wired into the library API and the ordinary CLI operations, and not
        # into the one path that mattered.
        amp.assert_identity()
        serve = [int(x) for x in a.serve.split(",") if x.strip()] if a.serve else []
        if a.workercmd == "next":
            return (BrokerExecutor.next_task(amp, a.campaign, amp.rank, timeout=a.timeout,
                                             serve=serve), amp.rank, job)
        if a.workercmd == "submit":
            return (BrokerExecutor.submit(amp, a.campaign, amp.rank, a.aid, serve=serve),
                    amp.rank, job)
        if a.workercmd == "give-up":
            return (BrokerExecutor.give_up(amp, a.campaign, amp.rank, a.aid, a.reason),
                    amp.rank, job)
        broker = BrokerExecutor(amp, campaign=a.campaign, work_dir=Path(amp.root) / "broker")
        return broker.stats(), rank, job

    if cmd == "trace":
        events = amp.events(kind=a.kind, rank=a.of_rank, limit=a.limit)
        if a.export:
            Path(a.export).parent.mkdir(parents=True, exist_ok=True)
            with open(a.export, "w", encoding="utf-8") as fh:
                for e in amp.events():
                    fh.write(json.dumps(e, default=str) + "\n")
            return {"exported": a.export, "events": len(events)}, rank, job
        return {"events": events, "count": len(events)}, rank, job

    raise AmpiError("AMPI_ERR_ARG", f"unhandled command {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    retries = getattr(args, "retries", 0)
    rank_hint = getattr(args, "rank", None)
    if rank_hint is None:
        env = os.environ.get(ENV_RANK)
        rank_hint = int(env) if env and env.lstrip("-").isdigit() else None

    last: AmpiError | None = None
    for attempt in range(1 + max(0, retries)):
        try:
            payload, rank, job = _dispatch(args)
            if attempt:
                payload["retried"] = attempt
            emit(payload, rank=rank, job=job, pretty=not getattr(args, "compact", False))
            return 0
        except AmpiError as exc:
            last = exc
            if not exc.retryable or attempt == retries:
                return fail(exc, rank=rank_hint, job=getattr(args, "expect_job", "") or "")
            print(
                f"AMPI_RETRY: {exc.cls_name} on attempt {attempt + 1}; re-issuing internally.",
                file=sys.stderr,
            )
        except KeyboardInterrupt:  # pragma: no cover
            return 130
    assert last is not None  # pragma: no cover
    return fail(last, rank=rank_hint)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
