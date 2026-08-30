"""Experiment 2: collaborative development of one software system.

The complement to the translation experiment.  Translating a book is *nearly*
independent work with a little global agreement; building a program is genuinely
coupled work in which every module's correctness depends on interfaces owned by
other modules.  It is the case where naive parallelism fails outright: eight agents
each handed one module of a specification produce eight modules that do not
compose, because each invented the interfaces of its neighbours.

The harness is organised around that single failure:

============================  =====================================================
phase                          AgentMPI operation
============================  =====================================================
publish the specification      ``bcast`` (handle-forwarding, so every rank reads
                               byte-identical text)
declare interfaces             ``Window.put`` on a per-module slot, then
                               ``Window.fence`` -- an exposure epoch
read dependencies' interfaces  ``Window.get`` in a fresh access epoch
implement                      agent call, contract-checked
integrate                      ``barrier`` then the acceptance suite at rank 0
distribute blame               ``scatter`` of per-module failures (not a broadcast
                               of everything: a rank should receive its own
                               failures, not all p ranks' failures)
cross-review                   ``neighbor_allgather`` on a circulant review graph
                               -- degree 2, not p, so review cost is O(p)
renegotiate an interface       ``Window.critical`` -- an exclusive lock, because a
                               semantic update to shared state cannot be an
                               ``accumulate``
decide                         ``agree`` on whether the build is green
============================  =====================================================

Ablations
---------
``--no-shared-interfaces``  no window: each rank must guess its neighbours'
                            interfaces.  This is the "cannot share information
                            between executors" failure, measured.
``--no-locks``              interface writes are blind ``put`` calls with no lock,
                            so concurrent renegotiation loses updates.
``--no-review``             skip the neighbourhood review exchange.
``--ranks 1``               one agent writes every module: the baseline that a
                            multi-agent system has to beat.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentmpi as ampi
from agentmpi.constants import BarrierPolicy, LockType, Mode
from common import make_executor_factory, provenance, write_result  # noqa: E402

SPEC_DIR = Path(__file__).resolve().parent / "minidb"

#: The module DAG.  ``deps`` are the modules whose published interfaces a module
#: must read before it can be implemented, and they define both the read set of the
#: RMA window and the edges of the pipeline.
MODULES: tuple[dict[str, Any], ...] = (
    {"name": "errors", "path": "minidb/errors.py", "deps": (), "brief": "QueryError only"},
    {"name": "tokens", "path": "minidb/tokens.py", "deps": ("errors",), "brief": "Token dataclass and tokenize()"},
    {"name": "nodes", "path": "minidb/nodes.py", "deps": (), "brief": "AST dataclasses"},
    {"name": "functions", "path": "minidb/functions.py", "deps": ("errors",), "brief": "SCALARS, AGGREGATES, like_match, compare"},
    {"name": "parser", "path": "minidb/parser.py", "deps": ("tokens", "nodes", "errors"), "brief": "parse() -> Select"},
    {"name": "planner", "path": "minidb/planner.py", "deps": ("nodes", "errors"), "brief": "plan() -> Plan"},
    {"name": "engine", "path": "minidb/engine.py", "deps": ("planner", "functions", "errors"), "brief": "execute() -> rows"},
    {"name": "api", "path": "minidb/api.py", "deps": ("parser", "planner", "engine", "errors"), "brief": "query() entry point"},
)

INIT_PY = '''"""minidb: a small SQL query engine over in-memory tables."""

from .api import query
from .errors import QueryError

__all__ = ["query", "QueryError"]
'''

#: A module table that names responsibilities but *not* signatures.
#:
#: The first version of this experiment produced a near-null ablation: withholding the
#: shared interface window cost one acceptance case out of sixty. The reason is a
#: confound in the design rather than a fact about the protocol -- the broadcast
#: specification already pins every module's exported signatures, so it *is* a shared
#: interface, and the window had nothing left to contribute.
#:
#: Interface publication can only pay to the extent that the specification
#: underdetermines the boundaries. This layout deliberately underdetermines them: it
#: says what each module is responsible for and what it may import, and leaves every
#: signature to be negotiated. With it, the window is the only channel through which a
#: rank can learn what its dependencies actually expose, which is the condition the
#: mechanism was designed for.
VAGUE_LAYOUT = """## Module layout

Each module is owned by exactly one implementer. Do not create files you do not own,
and do not modify files you do not own.

| module | responsibility | may import |
| --- | --- | --- |
| `minidb/errors.py` | the error type this system raises | stdlib only |
| `minidb/tokens.py` | turning SQL text into a sequence of tokens | `errors` |
| `minidb/nodes.py` | the abstract syntax tree representation | stdlib only |
| `minidb/parser.py` | turning tokens into a syntax tree | `tokens`, `nodes`, `errors` |
| `minidb/functions.py` | scalar and aggregate function behaviour, pattern matching, value comparison | `errors` |
| `minidb/planner.py` | name resolution, aggregate classification, validation | `nodes`, `errors` |
| `minidb/engine.py` | evaluating a validated query against tables | `planner`, `functions`, `errors` |
| `minidb/api.py` | the public `query` entry point that ties the above together | all of the above |

`minidb/__init__.py` is provided by the harness and re-exports `query` and
`QueryError`; do not write it.

The exported names and signatures of each module are **not specified here**. They are
yours to choose and to publish, and the modules that depend on you must be written
against what you publish. Do not guess at another module's interface: use the
interface that module has published, and if it is insufficient, work within it and say
so rather than inventing a different signature for someone else's module.
"""


def apply_vague_layout(spec: str) -> str:
    """Replace the specification's signature-bearing module table with a vague one."""
    start = spec.find("## Module layout")
    end = spec.find("## Examples")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("could not locate the module-layout section to replace")
    return spec[:start] + VAGUE_LAYOUT + "\n" + spec[end:]


INTERFACE = ampi.Contract(
    name="Interface",
    kind="json",
    required=("module", "exports", "notes"),
    nonempty=("module", "exports"),
    max_tokens=2500,
    semantics=(
        "exports is a list of the public names this module provides, each with its exact "
        "Python signature and a one-line description of its contract, including what it "
        "raises. notes records any decision a dependent module must know about."
    ),
)

IMPLEMENTATION = ampi.Contract(
    name="Implementation",
    kind="json",
    required=("path", "code", "exports"),
    nonempty=("path", "code"),
    must_match=(r"(def |class )",),
    semantics="code is the complete file contents. Nothing outside this one file.",
)

REVIEW = ampi.Contract(
    name="Review",
    kind="json",
    required=("target", "findings"),
    semantics="findings is a list of concrete interface or behaviour problems, each with a short fix.",
)


# ------------------------------------------------------------------- prompts


def prompt_interface(mod: dict[str, Any], spec: str) -> str:
    return f"""You own ONE module of the `minidb` system: `{mod['path']}` ({mod['brief']}).

Before anyone writes code, publish the interface your module will provide, so that the
modules depending on you can be written against it. Do not write the implementation yet.

Read the specification below. Your interface must be exactly what the specification
requires of your module: do not invent extra features, and do not omit anything a
dependent module needs.

Return ONLY a JSON object:
{{"module": "{mod['name']}",
  "exports": ["<exact Python signature> - <what it does, what it raises>", ...],
  "notes": "<anything a dependent module must know: data shapes, invariants, error behaviour>"}}

--- SPECIFICATION ---
{spec}
--- END SPECIFICATION ---"""


def prompt_implement(mod: dict[str, Any], spec: str, deps: dict[str, Any], own_iface: Any, round_no: int, failures: list[dict[str, Any]], reviews: list[Any]) -> str:
    dep_text = (
        "\n\n".join(
            f"### {name} ({next(m['path'] for m in MODULES if m['name'] == name)})\n"
            + json.dumps(iface, indent=2, ensure_ascii=False)
            for name, iface in sorted(deps.items())
        )
        or "(this module has no dependencies)"
    )
    fail_text = ""
    if failures:
        lines = [
            f"- {f['name']}: {f['sql']}\n    {f.get('reason', '')}" + (
                "\n    frames: " + " | ".join(f.get("traceback_tail") or []) if f.get("traceback_tail") else ""
            )
            for f in failures[:14]
        ]
        fail_text = (
            "\n\nFAILING ACCEPTANCE CASES ATTRIBUTED TO YOUR MODULE (round "
            f"{round_no}). Fix these; they are the definition of done:\n" + "\n".join(lines)
        )
    review_text = ""
    if reviews:
        flat = [f for r in reviews if isinstance(r, dict) for f in (r.get("findings") or [])]
        if flat:
            review_text = "\n\nPEER REVIEW OF YOUR MODULE:\n" + "\n".join(f"- {x}" for x in flat[:10])
    own = f"\n\nTHE INTERFACE YOU PUBLISHED (you must honour it; other modules were written against it):\n{json.dumps(own_iface, indent=2, ensure_ascii=False)}" if own_iface else ""

    return f"""Implement exactly one file of the `minidb` system: `{mod['path']}` ({mod['brief']}).

Rules:
- Pure Python 3.11+, standard library only.
- Write ONLY this file. Do not write, mention, or assume the contents of files you do
  not own beyond the published interfaces given below.
- Import from sibling modules using relative imports, e.g. `from .errors import QueryError`.
- Use the published interfaces below EXACTLY as given. If a dependency's published
  interface is insufficient, work within it anyway and say so in "concerns"; do not
  invent a different signature for someone else's module.
- The code must be complete and syntactically valid. No placeholders, no `pass` bodies,
  no `TODO`, no `NotImplementedError`.
{own}

PUBLISHED INTERFACES OF YOUR DEPENDENCIES:
{dep_text}
{fail_text}{review_text}

Return ONLY a JSON object:
{{"path": "{mod['path']}", "code": "<complete file contents>",
  "exports": ["<name>", ...], "concerns": ["<optional>", ...]}}

--- SPECIFICATION ---
{spec}
--- END SPECIFICATION ---"""


def _excerpt(code: str, max_chars: int = 9000) -> str:
    """Truncate source at a line boundary and say so.

    Two reviewers reported that the code they were asked to review stopped mid-function
    --- inside ``_and_expression`` in one case --- because an earlier version sliced the
    source at a fixed character count. A reviewer handed a syntactically incomplete file
    cannot tell a real defect from the cut, and both of them correctly flagged the
    problem instead of the code, which is a review round wasted.

    Truncating at a line boundary and marking the elision costs nothing and makes the
    omission legible. The protocol has a first-class facility for this
    (:class:`agentmpi.View`), and the right long-term fix is to send the source by
    handle and let the reviewer fetch the projection it wants rather than have the
    sender guess.
    """
    if len(code) <= max_chars:
        return code
    # Counted in lines, not characters. Character arithmetic around the boundary
    # newline is off by one in a way that is easy to get wrong and hard to notice,
    # and the number is stated to a reader who will rely on it.
    lines = code.splitlines()
    kept_lines: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > max_chars and kept_lines:
            break
        kept_lines.append(line)
        used += len(line) + 1
    omitted = len(lines) - len(kept_lines)
    if omitted <= 0:
        return code
    kept = "\n".join(kept_lines)
    return f"{kept}\n# ... {omitted} further lines of this file were not included in this review excerpt ...\n"


def prompt_review(mod: dict[str, Any], targets: list[dict[str, Any]]) -> str:
    bodies = "\n\n".join(
        f"### {t['path']} (published exports: {json.dumps(t.get('exports', []))})\n"
        f"```python\n{_excerpt(t.get('code', ''))}\n```"
        for t in targets
    )
    return f"""You own `{mod['path']}`. Review the peer modules below for problems that would
break integration. Be specific and terse; ignore style.

Look for exactly these:
- A function or class whose signature differs from what its published interface promised.
- A call into another module that uses a name or signature that module does not provide.
- Behaviour that contradicts the specification's NULL, aggregate, ordering or naming rules.
- Missing error wrapping: something that would escape as KeyError/TypeError instead of QueryError.

Return ONLY a JSON object:
{{"target": "<module names reviewed>", "findings": ["<file>: <problem> -> <fix>", ...]}}

{bodies}"""


def prompt_single(spec: str, failures: list[dict[str, Any]], round_no: int) -> str:
    fail_text = ""
    if failures:
        lines = [f"- {f['name']}: {f['sql']}\n    {f.get('reason','')}" for f in failures[:24]]
        fail_text = f"\n\nFAILING ACCEPTANCE CASES (round {round_no}):\n" + "\n".join(lines)
    files = "\n".join(f"  {m['path']} - {m['brief']}" for m in MODULES)
    return f"""Implement the whole `minidb` system yourself, as these files:
{files}

Pure Python 3.11+, standard library only, relative imports between the modules.
Complete and syntactically valid: no placeholders.
{fail_text}

Return ONLY a JSON object:
{{"files": [{{"path": "<path>", "code": "<complete contents>"}}, ...]}}

--- SPECIFICATION ---
{spec}
--- END SPECIFICATION ---"""


SINGLE = ampi.Contract(name="Bundle", kind="json", required=("files",), nonempty=("files",))


# ------------------------------------------------------------------ acceptance


def run_acceptance(workdir: Path, timeout: float = 180.0) -> dict[str, Any]:
    """Run the acceptance suite against the generated tree, out of process."""
    shutil.copy(SPEC_DIR / "acceptance.py", workdir / "acceptance.py")
    try:
        proc = subprocess.run(
            [sys.executable, "acceptance.py"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"importable": False, "import_error": "acceptance suite timed out", "n_total": 0, "n_passed": 0, "cases": [], "timeout": True}
    report = parse_report(proc.stdout, proc.stderr)
    report.setdefault("cases", [])
    return report


def parse_report(stdout: str, stderr: str = "") -> dict[str, Any]:
    """Parse the suite's JSON report from its stdout.

    The suite prints exactly one JSON object, so this is ``json.loads``. It is a
    named function with a test because an earlier version sliced from
    ``out.rfind("{")`` -- the *last* brace in the output, which lands inside a nested
    per-case object and never parses. Every run was then reported as unimportable
    with zero passes, and because this report is exactly what the harness scatters
    back to the population as the definition of done, the agents were told a build
    that passed 58 of 59 cases had failed to import and spent a repair round on a
    phantom. A bug in the plumbing around an oracle is as damaging as a bug in the
    oracle.
    """
    out = (stdout or "").strip()
    if not out:
        return {"importable": False, "import_error": (stderr or "")[-2000:], "n_total": 0, "n_passed": 0}
    for candidate in (out, out[out.find("{") :] if "{" in out else ""):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"importable": False, "import_error": out[-2000:], "n_total": 0, "n_passed": 0}


def write_tree(workdir: Path, files: dict[str, str]) -> None:
    (workdir / "minidb").mkdir(parents=True, exist_ok=True)
    (workdir / "minidb" / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    for path, code in files.items():
        target = workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")


# --------------------------------------------------------------------- harness


def build_harness(cfg: argparse.Namespace, spec: str, workdir: Path) -> Any:
    n_mods = len(MODULES)

    def rank_main(comm: ampi.Communicator) -> Any:
        t_start = time.time()
        failures_log: list[dict[str, Any]] = []
        stats: dict[str, Any] = {"rank": comm.rank, "rounds": [], "phases": {}}

        # ---- phase 0: publish the specification --------------------------
        t0 = time.time()
        spec_text = comm.bcast(spec if comm.rank == 0 else None, root=0, admit=False, label="spec")
        stats["phases"]["spec"] = round(time.time() - t0, 3)
        stats["spec_digest"] = comm.fabric.blobs.put(spec_text).digest[:12]

        if comm.size == 1:
            return _single_rank(comm, cfg, spec_text, workdir, stats, failures_log, t_start)

        # Assign modules: rank r owns modules r, r+p, ... so a population smaller
        # than the module count still covers every module.
        mine = [m for i, m in enumerate(MODULES) if i % comm.size == comm.rank]
        stats["modules"] = [m["name"] for m in mine]

        win = ampi.win_create(comm, f"interfaces-{comm.ctx}") if cfg.shared_interfaces else None

        # ---- phase 1: declare interfaces (exposure epoch) ----------------
        t0 = time.time()
        own_ifaces: dict[str, Any] = {}
        for m in mine:
            iface = _safe(
                comm,
                prompt_interface(m, spec_text),
                fallback={"module": m["name"], "exports": [], "notes": ""},
                log=failures_log,
                label=f"iface:{m['name']}",
                contract=INTERFACE,
                retries=2,
                max_tokens=1200,
            )
            own_ifaces[m["name"]] = iface
            if win is not None:
                win.put(m["name"], iface)
        if win is not None:
            win.fence(label="interfaces-published")
        else:
            comm.barrier(timeout=cfg.barrier_timeout, policy=BarrierPolicy.PROCEED, label="interfaces-skipped")
        stats["phases"]["interfaces"] = round(time.time() - t0, 3)

        # ---- phase 2..n: implement, integrate, repair --------------------
        code: dict[str, str] = {}
        exports: dict[str, list[str]] = {}
        my_failures: list[dict[str, Any]] = []
        my_reviews: list[Any] = []

        for round_no in range(1, cfg.rounds + 1):
            r_stats: dict[str, Any] = {"round": round_no}
            t0 = time.time()
            # Fresh access epoch: re-read dependencies rather than trusting the
            # copy from the previous round, which a peer may have renegotiated.
            deps_by_mod: dict[str, dict[str, Any]] = {}
            if win is not None:
                win.sync()
                for m in mine:
                    deps_by_mod[m["name"]] = {d: win.get(d, default={}, admit=False) for d in m["deps"]}
            else:
                deps_by_mod = {m["name"]: {} for m in mine}
            r_stats["read_interfaces_s"] = round(time.time() - t0, 3)

            t0 = time.time()
            for m in mine:
                impl = _safe(
                    comm,
                    prompt_implement(
                        m,
                        spec_text,
                        deps_by_mod[m["name"]],
                        own_ifaces.get(m["name"]),
                        round_no,
                        [f for f in my_failures if f.get("_module") == m["name"]],
                        my_reviews,
                    ),
                    fallback={"path": m["path"], "code": code.get(m["path"], ""), "exports": []},
                    log=failures_log,
                    label=f"impl:{m['name']}:r{round_no}",
                    contract=IMPLEMENTATION,
                    retries=2,
                    max_tokens=cfg.max_code_tokens,
                )
                if impl.get("code"):
                    code[m["path"]] = str(impl["code"])
                    exports[m["name"]] = list(impl.get("exports") or [])
            r_stats["implement_s"] = round(time.time() - t0, 3)

            # ---- integration barrier and the oracle ---------------------
            t0 = time.time()
            bres = comm.barrier(timeout=cfg.barrier_timeout, policy=BarrierPolicy.PROCEED, label=f"integrate-r{round_no}")
            r_stats["absent_at_barrier"] = list(bres.absent)
            bundle = comm.gather({"code": code, "exports": exports}, root=0, mode=Mode.RENDEZVOUS, admit=False, label=f"collect-r{round_no}")

            report: dict[str, Any] = {}
            blame: list[list[dict[str, Any]]] = [[] for _ in range(comm.size)]
            if comm.rank == 0:
                all_code: dict[str, str] = {}
                for chunk in bundle or []:
                    if chunk:
                        all_code.update(chunk.get("code") or {})
                rd = workdir / f"round{round_no}"
                rd.mkdir(parents=True, exist_ok=True)
                write_tree(rd, all_code)
                report = run_acceptance(rd, timeout=cfg.acceptance_timeout)
                # Attribute each failure to the rank that owns the blamed module,
                # falling back to the module the case was written to exercise.
                owner_of = {m["name"]: i % comm.size for i, m in enumerate(MODULES)}
                for c in report.get("cases", []):
                    if c.get("passed"):
                        continue
                    mod = c.get("module")
                    frames = c.get("traceback_tail") or []
                    for line in reversed(frames):
                        if "minidb/" in line:
                            cand = line.split("minidb/", 1)[1].split('"', 1)[0].replace(".py", "")
                            if cand in owner_of:
                                mod = cand
                            break
                    owner = owner_of.get(mod, 0)
                    blame[owner].append({**{k: v for k, v in c.items() if k != "traceback_tail"}, "_module": mod, "traceback_tail": frames})
                if not report.get("importable"):
                    # An import failure is everyone's problem: broadcast it whole.
                    for i in range(comm.size):
                        blame[i].append({"name": "IMPORT", "sql": "", "reason": report.get("import_error", "")[:1200], "_module": "?"})
            summary = comm.bcast(
                {k: v for k, v in report.items() if k != "cases"} if comm.rank == 0 else None, root=0, admit=False, label=f"report-r{round_no}"
            )
            my_failures = comm.scatter(blame if comm.rank == 0 else None, root=0, label=f"blame-r{round_no}")
            r_stats["integrate_s"] = round(time.time() - t0, 3)
            r_stats["report"] = summary
            r_stats["n_my_failures"] = len(my_failures or [])

            green = bool(summary and summary.get("n_passed") == summary.get("n_total") and summary.get("n_total"))
            try:
                r_stats["agreed_green"] = ampi.agree(comm, green, timeout=cfg.barrier_timeout)
            except ampi.AmpiError as exc:
                r_stats["agreed_green"] = None
                r_stats["agree_error"] = str(exc)[:200]

            # ---- peer review over a degree-2 circulant graph -------------
            if cfg.review and round_no < cfg.rounds and not r_stats["agreed_green"]:
                t0 = time.time()
                # Two topologies, and needing both is the whole subtlety. `edges` is
                # (author -> reviewer): a rank's *sources* are the authors whose code
                # it reviews. A review must travel back to the author, i.e. along the
                # reversed edges, so the return path needs the transpose.
                #
                # An earlier version returned reviews over the forward topology, whose
                # destinations are the ranks this rank *sends code to* rather than the
                # ranks it reviewed. Every author therefore received critiques of other
                # people's modules, and the review phase was a silent no-op -- findings
                # never reached anyone who could act on them. A worker rank noticed,
                # reporting that its reviews only ever named a file it did not own.
                edges = ampi.review_edges(comm.size, fanout=min(2, comm.size - 1))
                topo = ampi.dist_graph_create(comm, edges)
                transpose = ampi.dist_graph_create(comm, [(b, a) for a, b in edges])
                mine_payload = [
                    {"path": m["path"], "code": _excerpt(code.get(m["path"], "")), "exports": exports.get(m["name"], [])}
                    for m in mine
                ]
                incoming = ampi.neighbor_allgather(topo, mine_payload, admit=False, label=f"review-r{round_no}")
                targets = [t for chunk in incoming if chunk for t in chunk]
                rev = {"target": "", "findings": []}
                if targets:
                    rev = _safe(
                        comm,
                        prompt_review(mine[0], targets),
                        fallback={"target": "", "findings": []},
                        log=failures_log,
                        label=f"review:r{round_no}",
                        contract=REVIEW,
                        retries=1,
                        max_tokens=900,
                    )
                returned = ampi.neighbor_allgather(
                    transpose, rev, admit=False, label=f"reviewback-r{round_no}"
                )
                # A reviewer may legitimately raise a cross-module concern, so keep only
                # the findings that name a file this rank owns; the rest are recorded
                # for the trace rather than acted on by the wrong owner.
                my_paths = [m["path"] for m in mine]
                mine_findings: list[str] = []
                foreign = 0
                for chunk in returned:
                    for finding in (chunk or {}).get("findings") or []:
                        if any(path in str(finding) or path.split("/")[-1] in str(finding) for path in my_paths):
                            mine_findings.append(str(finding))
                        else:
                            foreign += 1
                my_reviews = [{"target": ",".join(my_paths), "findings": mine_findings}] if mine_findings else []
                r_stats["review_findings_mine"] = len(mine_findings)
                r_stats["review_findings_foreign"] = foreign
                r_stats["review_s"] = round(time.time() - t0, 3)

            # ---- interface renegotiation under an exclusive lock ---------
            if win is not None and my_failures and round_no < cfg.rounds:
                t0 = time.time()
                for m in mine:
                    iface = own_ifaces.get(m["name"])
                    if not iface:
                        continue
                    if cfg.locks:
                        with win.critical(m["name"], timeout=cfg.lock_timeout):
                            cur = win.get(m["name"], default=iface, admit=False)
                            merged = dict(cur or {})
                            merged["exports"] = list(exports.get(m["name"]) or (cur or {}).get("exports") or [])
                            win.put(m["name"], merged, expect_version=win.state(m["name"]).version)
                            own_ifaces[m["name"]] = merged
                    else:
                        cur = win.get(m["name"], default=iface, admit=False)
                        merged = dict(cur or {})
                        merged["exports"] = list(exports.get(m["name"]) or (cur or {}).get("exports") or [])
                        win.put(m["name"], merged)
                        own_ifaces[m["name"]] = merged
                win.fence(label=f"renegotiate-r{round_no}")
                r_stats["renegotiate_s"] = round(time.time() - t0, 3)

            stats["rounds"].append(r_stats)
            if r_stats["agreed_green"]:
                break

        stats["wall_s"] = round(time.time() - t_start, 3)
        stats["context"] = comm.rt.context.snapshot()
        stats["cost"] = comm.rt.cost.snapshot()
        stats["degraded"] = failures_log
        if win is not None:
            stats["staleness_violations"] = win.n_staleness_violations
        if comm.rank == 0:
            stats["final_report"] = stats["rounds"][-1]["report"] if stats["rounds"] else {}
        return stats

    return rank_main


def _single_rank(comm: ampi.Communicator, cfg: argparse.Namespace, spec: str, workdir: Path, stats: dict[str, Any], log: list[dict[str, Any]], t_start: float) -> Any:
    """The p=1 baseline: one agent writes the entire system."""
    failures: list[dict[str, Any]] = []
    for round_no in range(1, cfg.rounds + 1):
        t0 = time.time()
        bundle = _safe(
            comm,
            prompt_single(spec, failures, round_no),
            fallback={"files": []},
            log=log,
            label=f"single:r{round_no}",
            contract=SINGLE,
            retries=2,
            max_tokens=cfg.max_code_tokens * 4,
        )
        files = {str(f["path"]): str(f.get("code", "")) for f in (bundle.get("files") or []) if f.get("path")}
        rd = workdir / f"round{round_no}"
        rd.mkdir(parents=True, exist_ok=True)
        write_tree(rd, files)
        report = run_acceptance(rd, timeout=cfg.acceptance_timeout)
        failures = [c for c in report.get("cases", []) if not c.get("passed")]
        if not report.get("importable"):
            failures = [{"name": "IMPORT", "sql": "", "reason": report.get("import_error", "")[:1500]}]
        stats["rounds"].append(
            {"round": round_no, "report": {k: v for k, v in report.items() if k != "cases"}, "wall_s": round(time.time() - t0, 2), "n_my_failures": len(failures)}
        )
        if report.get("n_total") and report.get("n_passed") == report.get("n_total"):
            break
    stats["wall_s"] = round(time.time() - t_start, 3)
    stats["cost"] = comm.rt.cost.snapshot()
    stats["context"] = comm.rt.context.snapshot()
    stats["degraded"] = log
    stats["final_report"] = stats["rounds"][-1]["report"] if stats["rounds"] else {}
    return stats


def _safe(comm: ampi.Communicator, prompt: str, *, fallback: Any, log: list[dict[str, Any]], **kw: Any) -> Any:
    """Degrade on a local agent failure instead of leaving the collective."""
    try:
        return comm.agent(prompt, **kw)
    except ampi.AmpiError as exc:
        log.append({"label": kw.get("label", ""), "error_class": getattr(exc, "cls_name", type(exc).__name__), "message": str(exc)[:300]})
        comm.fabric.emit("harness.degraded", rank=comm.rt.wrank, ctx=comm.ctx, label=str(kw.get("label", "")))
        return fallback


# ------------------------------------------------------------------ synthetic


def synthetic_agent(prompt: str, **meta: Any) -> Any:
    """Deterministic stand-in so the harness is testable without agents."""
    label = str(meta.get("label", ""))
    if label.startswith("iface"):
        name = label.split(":")[1]
        return {"module": name, "exports": [f"def stub_{name}() -> None - placeholder"], "notes": ""}
    if label.startswith("impl"):
        name = label.split(":")[1]
        path = next(m["path"] for m in MODULES if m["name"] == name)
        return {"path": path, "code": f"def stub_{name}():\n    return None\n", "exports": [f"stub_{name}"]}
    if label.startswith("review"):
        return {"target": "peer", "findings": []}
    if label.startswith("single"):
        return {"files": [{"path": m["path"], "code": f"def stub_{m['name']}():\n    return None\n"} for m in MODULES]}
    return {}


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AgentMPI experiment 2: collaborative software development")
    ap.add_argument("--ranks", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--executor", choices=["broker", "simulated", "function"], default="function")
    ap.add_argument("--root", default=None)
    ap.add_argument("--label", default="software")
    ap.add_argument("--no-shared-interfaces", dest="shared_interfaces", action="store_false")
    ap.add_argument("--no-locks", dest="locks", action="store_false")
    ap.add_argument("--no-review", dest="review", action="store_false")
    ap.add_argument(
        "--vague-spec",
        action="store_true",
        help="withhold module signatures from the specification, so the interface window is the only channel",
    )
    ap.add_argument("--barrier-timeout", type=float, default=2400.0)
    ap.add_argument("--lock-timeout", type=float, default=600.0)
    ap.add_argument("--acceptance-timeout", type=float, default=180.0)
    ap.add_argument("--job-timeout", type=float, default=14400.0)
    ap.add_argument("--max-code-tokens", type=int, default=6000)
    ap.add_argument("--context-budget", type=int, default=120_000)
    ap.set_defaults(shared_interfaces=True, locks=True, review=True)
    cfg = ap.parse_args(argv)

    spec = (SPEC_DIR / "SPEC.md").read_text(encoding="utf-8")
    if cfg.vague_spec:
        spec = apply_vague_layout(spec)
    root = Path(cfg.root) if cfg.root else Path("runs") / f"{cfg.label}-p{cfg.ranks}"
    workdir = root / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    fabric = ampi.create_job(root, cfg.ranks, label=cfg.label)
    fabric.set_meta("experiment", "software")
    fabric.set_meta("config", json.dumps(vars(cfg)))

    factory = make_executor_factory(
        cfg.executor,
        fabric_root=root,
        fn=synthetic_agent if cfg.executor == "function" else None,
        timeout=cfg.job_timeout,
    )
    job = ampi.launch(
        build_harness(cfg, spec, workdir),
        size=cfg.ranks,
        root=root,
        fabric=fabric,
        executor_factory=factory,
        context_budget=cfg.context_budget,
        strict_context=False,
        label=cfg.label,
        timeout=cfg.job_timeout,
    )

    head = job.value(0) or {}
    final = head.get("final_report") or {}
    win_name = f"interfaces-{0}"
    contention = ampi.rma.contention_report(fabric, win_name) if cfg.shared_interfaces else {}
    payload = {
        "provenance": provenance(experiment="software"),
        "config": vars(cfg),
        "job": job.totals(),
        "failed_ranks": job.failed_ranks,
        "rank_errors": {o.rank: o.error for o in job.outcomes if not o.ok},
        "acceptance": {
            "importable": final.get("importable"),
            "n_total": final.get("n_total"),
            "n_passed": final.get("n_passed"),
            "pass_rate": final.get("pass_rate"),
            "by_module": final.get("by_module"),
            "blame": final.get("blame"),
            "import_error": (final.get("import_error") or "")[:1500],
        },
        "per_round": [
            {"round": r["round"], **{k: v for k, v in (r.get("report") or {}).items() if k in ("n_passed", "n_total", "pass_rate", "importable")},
             "agreed_green": r.get("agreed_green")}
            for r in head.get("rounds", [])
        ],
        "contention": contention,
        "calibration": ampi.cost.calibrate(fabric).as_dict(),
        "runtime_summary": ampi.cost.summarise(fabric).as_dict(),
        "per_rank_stats": {
            o.rank: {k: v for k, v in (o.value or {}).items() if k not in ("final_report",)}
            for o in job.outcomes
            if o.value
        },
        "fabric_root": str(root),
    }
    variant = (
        f"p{cfg.ranks}-{'shared' if cfg.shared_interfaces else 'noshared'}"
        f"-{'locks' if cfg.locks else 'nolocks'}-{'review' if cfg.review else 'noreview'}"
        f"-{'vague' if cfg.vague_spec else 'precise'}-r{cfg.rounds}"
    )
    path = write_result(f"{cfg.label}-{variant}", payload, subdir="software")
    print(json.dumps({"result": str(path), "acceptance": payload["acceptance"], "per_round": payload["per_round"], "job": payload["job"]}, indent=2))
    return 0 if job.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
