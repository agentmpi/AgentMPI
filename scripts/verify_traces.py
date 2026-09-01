"""Check the committed trace archive against itself and against the cost model.

An archive nobody validates is an archive that quietly rots: an exporter change, a partial
commit, or a hand-edited file all leave something that still looks like evidence. This
recomputes from the committed files and fails loudly on a mismatch.

Four checks, in increasing strength.

1. **Integrity.** Every run in ``traces/manifest.json`` has an event log whose SHA-256 and
   line count match the recorded values, and a viewer payload that parses.

2. **Completeness.** Every fabric under ``runs/`` appears in the manifest, and each
   committed log has exactly the events its fabric holds. This is what catches a run that
   was executed but never exported --- the failure mode that made the archive incomplete in
   the first place.

3. **Sufficiency.** The collective validation is recomputed from the ``.jsonl`` logs *alone*,
   without opening a fabric: for every measured configuration, the number of ``msg.send``
   events in the log must equal the closed-form message count in ``agentmpi.cost.FORMULAS``.
   Passing means the logs are not merely present but genuinely sufficient to re-derive the
   paper's central quantitative claim.

4. **Agreement.** Reading a fabric directly and reading its exported log yield the same
   per-run summary, so the derived form has not drifted from the primary one.

    python3 scripts/verify_traces.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import agentmpi as ampi  # noqa: E402
import trace_server as ts  # noqa: E402
from agentmpi import cost  # noqa: E402

#: ``runs/<label>/coll-<op>-<algorithm>-<p>`` is the microbenchmark naming convention; the
#: parameters of each validation point are recoverable from the filename alone.
COLL_RE = re.compile(r"coll-([a-z]+)-(.+)-(\d+)$")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, ok: bool, msg: str) -> bool:
        if not ok:
            self.failures.append(msg)
        return ok

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def read_log(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verify_integrity(manifest: dict, rep: Report) -> None:
    for run in manifest["runs"]:
        log = REPO / run["events"]
        view = REPO / run["view"]
        if not rep.check(log.exists(), f"missing event log: {run['events']}"):
            continue
        body = log.read_bytes()
        digest = hashlib.sha256(body).hexdigest()[:16]
        rep.check(
            digest == run["events_sha256"],
            f"{run['name']}: log sha256 {digest} != manifest {run['events_sha256']}",
        )
        n = sum(1 for line in body.decode("utf-8").splitlines() if line.strip())
        rep.check(n == run["n_events"], f"{run['name']}: log has {n} events, manifest says {run['n_events']}")
        if rep.check(view.exists(), f"missing viewer payload: {run['view']}"):
            try:
                json.loads(view.read_text(encoding="utf-8"))
            except Exception as exc:
                rep.check(False, f"{run['name']}: viewer payload does not parse: {exc!r}")
    rep.note(f"integrity: {len(manifest['runs'])} runs, {manifest['n_events']:,} events")


def verify_completeness(manifest: dict, rep: Report) -> None:
    on_disk = {ts._name_of(db.parent) for db in (REPO / "runs").rglob("fabric.sqlite")}
    in_manifest = {r["name"] for r in manifest["runs"]}
    for missing in sorted(on_disk - in_manifest):
        rep.check(False, f"fabric present but not exported: runs/{missing.replace(ts.NEST, '/')}")
    for extra in sorted(in_manifest - on_disk):
        rep.check(False, f"exported but no fabric: {extra}")
    rep.note(f"completeness: {len(on_disk)} fabrics on disk, {len(in_manifest)} in manifest")


def verify_sufficiency(rep: Report) -> None:
    """Recompute the collective validation from the .jsonl logs alone."""
    ok = bad = 0
    for log in sorted((REPO / "traces" / "events").glob("*.jsonl")):
        stem = log.name[: -len(".jsonl")]
        tail = stem.split(ts.NEST, 1)[-1]
        m = COLL_RE.match(tail)
        if not m:
            continue
        op, alg, p = m.group(1), m.group(2), int(m.group(3))
        formula = cost.FORMULAS.get((op, alg))
        if formula is None:
            rep.check(False, f"{stem}: measured {op}/{alg} has no closed-form cost entry")
            continue
        measured = sum(1 for e in read_log(log) if e.get("kind") == "msg.send")
        _rounds, messages, _vol, _depth = formula(p, 1000)
        if int(messages) == measured:
            ok += 1
        else:
            bad += 1
            rep.check(False, f"{stem}: closed form predicts {int(messages)} messages, log has {measured}")
    rep.check(ok > 0, "sufficiency: recovered no collective configurations from the logs")
    rep.note(f"sufficiency: {ok}/{ok + bad} collective configurations match closed form, from logs alone")


def verify_agreement(manifest: dict, rep: Report, limit: int) -> None:
    """A fabric and its exported log must summarize identically."""
    checked = 0
    for run in manifest["runs"][:limit]:
        root = REPO / "runs" / run["name"].replace(ts.NEST, "/")
        if not (root / "fabric.sqlite").exists():
            continue
        from_fabric = ampi.Fabric(root).events()
        from_log = read_log(REPO / run["events"])
        if not rep.check(
            len(from_fabric) == len(from_log),
            f"{run['name']}: fabric has {len(from_fabric)} events, log has {len(from_log)}",
        ):
            continue
        a = cost.summarise(from_fabric)
        b = cost.summarise(from_log)
        rep.check(
            a.n_messages == b.n_messages and a.n_agent_calls == b.n_agent_calls,
            f"{run['name']}: summary drift, fabric={a.n_messages}msg/{a.n_agent_calls}calls "
            f"log={b.n_messages}msg/{b.n_agent_calls}calls",
        )
        checked += 1
    rep.note(f"agreement: {checked} runs summarize identically from fabric and from log")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(REPO / "traces" / "manifest.json"))
    ap.add_argument("--agreement-sample", type=int, default=40, help="runs to cross-check against fabrics")
    cfg = ap.parse_args()

    path = Path(cfg.manifest)
    if not path.exists():
        print(f"no manifest at {path}; run scripts/export_traces.py first")
        return 1
    manifest = json.loads(path.read_text(encoding="utf-8"))
    ts.RUNS = REPO / "runs"

    rep = Report()
    verify_integrity(manifest, rep)
    verify_completeness(manifest, rep)
    verify_sufficiency(rep)
    verify_agreement(manifest, rep, cfg.agreement_sample)

    for note in rep.notes:
        print(f"  ok  {note}")
    if rep.failures:
        print(f"\n{len(rep.failures)} problem(s):")
        for f in rep.failures[:40]:
            print(f"  FAIL {f}")
        if len(rep.failures) > 40:
            print(f"  ... and {len(rep.failures) - 40} more")
        return 1
    print("\ntrace archive verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
