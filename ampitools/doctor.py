"""``ampi doctor``: say what is wrong, and name the rank responsible.

MPI's answer to a mismatched collective is undefined behaviour, which in practice
means a hang with no output.  That is survivable when you can attach a debugger to
every process.  Here the population is a dozen LLM agents on someone else's
infrastructure, several of them blocked inside a collective, all of them billing by
the token, and the only artefact is a pile of unordered transcripts.

So the diagnostic is part of the protocol rather than a tool built on top of it.
Everything it reports is derived from the journal, so it works on a job whose
executors are all gone, and every finding names a rank and a next action.  In
priority order it looks for: ranks that never started, ranks convicted of failing,
collectives waiting on a named rank, cycles in the wait-for graph, contested
interfaces, stale locks, and ranks near their context limit.
"""

from __future__ import annotations

from typing import Any

from ampi.constants import STATE_FAILED, STATE_FENCED, STATE_REQUESTED, STATE_RUNNING, STATE_SUSPECT

__all__ = ["diagnose"]


def diagnose(amp: Any) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    now = amp.device.clock()
    ranks = [amp._rankview(r) for r in range(amp.size)]

    # 1. Ranks that were requested and never arrived.  Structurally different from
    #    a crash: nothing failed, the launcher simply could not start them, and a
    #    detector keyed on liveness will never notice.
    no_shows = [v for v in ranks if v.state == STATE_REQUESTED]
    if no_shows:
        # Before the join deadline this is expected -- executors start at different
        # times -- and calling it an error would make every healthy job look wedged
        # during launch.  After it, nothing distinguishes these ranks from dead ones
        # and the job cannot make progress, so it is the most urgent finding there is.
        overdue = [v for v in no_shows if now > v.join_deadline]
        findings.append({
            "severity": "error" if overdue else "info",
            "what": "ranks were requested but never initialised",
            "ranks": [v.rank for v in no_shows],
            "detail": [
                {"rank": v.rank, "role": v.role,
                 "join_deadline_in": round(v.join_deadline - now, 1)}
                for v in no_shows
            ],
            "overdue": [v.rank for v in overdue],
            "do": (
                "Start these executors, or shrink the communicator so the rest can proceed."
                if overdue
                else "Nothing yet: their join deadline has not passed. Executors start at "
                     "different times, and a rank that has not called in is not yet late."
            ),
        })

    # 2. Convicted failures.
    failed = [v for v in ranks if v.state in (STATE_FAILED, STATE_FENCED)]
    if failed:
        findings.append({
            "severity": "error",
            "what": "ranks have been declared failed",
            "ranks": [v.rank for v in failed],
            "detail": [
                {"rank": v.rank, "kind": v.failure_kind, "epoch": v.epoch,
                 "restarts": v.restarts, "context_used": (v.ctx or {}).get("used", 0)}
                for v in failed
            ],
            "do": "Run 'ampi ack' to re-enable wildcard receives, then either "
                  "'ampi respawn --target N' or 'ampi comm shrink'. A rank that died of "
                  "ctx_exhausted must be respawned with a *smaller* assignment.",
        })

    suspect = [v for v in ranks if v.state == STATE_SUSPECT]
    if suspect:
        findings.append({
            "severity": "warning",
            "what": "ranks are suspected but not yet convicted",
            "ranks": [v.rank for v in suspect],
            "detail": [{"rank": v.rank, "silent_for": round(now - v.last_seen, 1)} for v in suspect],
            "do": "Nothing yet. A thinking executor looks exactly like a dead one; the second "
                  "phase exists so that a long step is not fatal. If these are alive, they "
                  "should call 'ampi hb --extend 900' before long steps.",
        })

    # 3. Open collectives, and who has not arrived.  The single most useful thing a
    #    wedged agent job can be told.
    for comm in [c["name"] for c in amp.comm_list() if c.get("state") == "live"]:
        try:
            for entry in amp.coll_status(comm=comm):
                if entry["closed"] or not entry["missing"]:
                    continue
                blocking = [
                    {"rank": r, "state": amp._rankview(r).state,
                     "silent_for": round(now - amp._rankview(r).last_seen, 1)}
                    for r in entry["missing"]
                ]
                findings.append({
                    "severity": "error",
                    "what": f"collective {entry['label']!r} on {comm!r} is open and waiting",
                    "ranks": entry["missing"],
                    "detail": {"kind": entry["kind"], "arrived": entry["arrived"],
                               "blocking": blocking},
                    "do": f"Rank(s) {entry['missing']} have not called it. Every member must "
                          f"enter with the same label, contributing a degraded value if "
                          f"necessary. If they are gone, 'ampi comm shrink' lets the rest close it.",
                })
        except Exception:  # pragma: no cover - a freed or malformed communicator
            continue

    # 4. Cycles in the wait-for graph.
    cycle = _wait_cycle(amp)
    if cycle:
        findings.append({
            "severity": "error",
            "what": "a cycle in the wait-for graph",
            "ranks": cycle,
            "do": f"Ranks {cycle} are each blocked on the next. One of them must abandon its "
                  "wait, or the sends must be declared rendezvous. A conditional send paired "
                  "with an unconditional receive is the usual cause.",
        })

    # 5. Interfaces nobody verified, or that two ranks claim.
    try:
        report = amp.iface_report()
        if report.get("contested"):
            findings.append({
                "severity": "warning",
                "what": "more than one rank claims the same interface name",
                "detail": report["contested"],
                "do": "Consumers must not assume a single provider. Decide who owns each name.",
            })
        if report.get("refuted"):
            findings.append({
                "severity": "error",
                "what": "a published interface was refuted by a consumer's probe",
                "detail": report["refuted"],
                "do": "The declaration and the behaviour disagree. Fix one of them before "
                      "any further consumer builds against the declaration.",
            })
    except Exception:  # pragma: no cover
        pass

    # 6. Locks held by ranks that are not running.
    stale = [
        lk.to_dict()
        for lk in amp.device.leases(include_expired=True)
        if amp._rankview(lk.holder).state != STATE_RUNNING or lk.expires_at <= now
    ]
    if stale:
        findings.append({
            "severity": "warning",
            "what": "locks held by ranks that are not running, or already expired",
            "detail": stale,
            "do": "They are reclaimable; the next acquirer gets a higher fencing token and any "
                  "write from the old holder will be rejected. No action is usually needed.",
        })

    # 7. Context pressure.
    pressed = [
        {"rank": v.rank, "used": (v.ctx or {}).get("used", 0),
         "budget": (v.ctx or {}).get("budget", 0),
         "degradations": (v.ctx or {}).get("degradations", 0)}
        for v in ranks
        if (v.ctx or {}).get("budget") and (v.ctx or {}).get("used", 0) > 0.8 * v.ctx["budget"]
    ]
    if pressed:
        findings.append({
            "severity": "warning",
            "what": "ranks are near their context budget",
            "ranks": [p["rank"] for p in pressed],
            "detail": pressed,
            "do": "Deliver by rendezvous or with --view. A rank that exhausts its budget will "
                  "produce confident wrong output rather than an error.",
        })

    # 8. Stalls attributable to eager credit.
    stalls = amp.events(kind="ctx.stall", limit=50)
    if stalls:
        findings.append({
            "severity": "warning",
            "what": "senders stalled waiting for a receiver's unexpected-message budget",
            "detail": {"count": len(stalls),
                       "recent": [{"src": s.get("rank"), "dst": s.get("dst"),
                                   "tokens": s.get("tokens")} for s in stalls[-5:]]},
            "do": "This harness depends on the receiver having spare context, which is what "
                  "context-safety forbids. Declare those sends rendezvous.",
        })

    verdict = (
        "wedged" if any(f["severity"] == "error" for f in findings)
        else "degraded" if any(f["severity"] == "warning" for f in findings)
        else "starting" if findings
        else "healthy"
    )
    return {
        "verdict": verdict,
        "findings": findings,
        "summary": _summary(verdict, findings),
        "ranks": {
            "running": [v.rank for v in ranks if v.state == STATE_RUNNING],
            "finalised": [v.rank for v in ranks if v.state == "finalised"],
            "failed": [v.rank for v in failed],
            "never_started": [v.rank for v in no_shows],
        },
    }


def _wait_cycle(amp: Any) -> list[int]:
    """Cycles among ranks blocked on a specific peer.

    Only receives naming a specific source can participate: a wildcard receive is
    not waiting on anybody in particular, so including it would manufacture cycles
    that do not exist.
    """
    waits: dict[int, int] = {}
    for q in amp.device.scan("recvq", {"state": "open", "run": amp.manifest.job_id}):
        if q["src_want"] >= 0:
            waits.setdefault(q["dst"], q["src_want"])
    for start in sorted(waits):
        path: list[int] = []
        node, seen = start, set()
        while node in waits and node not in seen:
            seen.add(node)
            path.append(node)
            node = waits[node]
        if node in seen:
            return path[path.index(node) :]
    return []


def _summary(verdict: str, findings: list[dict[str, Any]]) -> str:
    if verdict == "healthy":
        return "No problems found. Every rank is running or finalised and no collective is stuck."
    lead = (
        [f for f in findings if f["severity"] == "error"]
        or [f for f in findings if f["severity"] == "warning"]
        or findings
    )
    first = lead[0]
    ranks = first.get("ranks")
    who = f" (rank(s) {ranks})" if ranks else ""
    return f"{verdict}: {first['what']}{who}. {first['do'].split('.')[0]}."
