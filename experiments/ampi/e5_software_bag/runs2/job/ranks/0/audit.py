"""Independent contract audit for the tokenbudget package.

Checks the behaviour the window contract specifies, without relying on the
implementers' own tests.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import pathlib
import random
import sys
import contextlib

ART = pathlib.Path("/workspace/experiments/ampi/e5_software_bag/runs2/job/artifact")
sys.path.insert(0, str(ART))

import tokenbudget
from tokenbudget import cli, compact, estimate, ledger, planner, policy

RESULTS: list[tuple[str, str, bool, str]] = []


def check(module: str, name: str, fn) -> None:
    try:
        ok = fn()
        detail = "" if ok is True else str(ok)
        RESULTS.append((module, name, ok is True, detail))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((module, name, False, f"{type(exc).__name__}: {exc}"))


def raises(exc_type, fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001
        return f"raised {type(exc).__name__} not {exc_type.__name__}"
    return "did not raise"


def sig(obj) -> str:
    return f"{obj.__name__}{inspect.signature(obj)}"


# ---------------------------------------------------------------- estimate
check("estimate", "exports count_tokens/estimate_messages",
      lambda: all(hasattr(estimate, n) for n in ("count_tokens", "estimate_messages")))
check("estimate", "count_tokens deterministic",
      lambda: all(estimate.count_tokens(s) == estimate.count_tokens(s)
                  for s in ("", "a", "hello world", "x" * 999)))
check("estimate", "count_tokens('')==0 and (None)==0",
      lambda: estimate.count_tokens("") == 0 and estimate.count_tokens(None) == 0)
check("estimate", "count_tokens monotone non-decreasing in len(text)",
      lambda: all(estimate.count_tokens("x" * i) <= estimate.count_tokens("x" * (i + 1))
                  for i in range(0, 400)))
check("estimate", "count_tokens never negative",
      lambda: all(estimate.count_tokens("q" * i) >= 0 for i in range(0, 400)))
check("estimate", "estimate_messages == sum(count_tokens(content)) + 4/message",
      lambda: all(
          estimate.estimate_messages(msgs)
          == sum(estimate.count_tokens(m["content"]) + 4 for m in msgs)
          for msgs in ([], [{"role": "u", "content": "hi"}],
                       [{"role": "u", "content": "a" * 37}, {"role": "a", "content": ""}])))
check("estimate", "leaf: imports no package module", lambda: (
    True if not [n for n in ast.walk(ast.parse((ART / "tokenbudget" / "estimate.py").read_text()))
                 if isinstance(n, (ast.Import, ast.ImportFrom))
                 and "tokenbudget" in (getattr(n, "module", "") or "" if isinstance(n, ast.ImportFrom)
                                       else " ".join(a.name for a in n.names))]
    else "imports a package module"))

# ------------------------------------------------------------------ policy
check("policy", "exports Budget/BudgetExceeded",
      lambda: hasattr(policy, "Budget") and issubclass(policy.BudgetExceeded, Exception))
check("policy", "Budget(limit, reserved=0) signature", lambda: (
    lambda s: str(s).replace("'", "") == "(self, limit: int, reserved: int = 0) -> None"
    or f"got {s}")(inspect.signature(policy.Budget.__init__)))
check("policy", "remaining() == max(0, limit-reserved-used)",
      lambda: all(policy.Budget(lim, res).remaining(used) == max(0, lim - res - used)
                  for lim in (0, 1, 100, 1000) for res in (0, 1)
                  if res <= lim for used in (0, 1, 99, 100, 5000)))
check("policy", "admits() iff incoming <= remaining(used)",
      lambda: all((policy.Budget(lim, res).admits(used, inc)
                   == (inc <= policy.Budget(lim, res).remaining(used)))
                  for lim in (0, 10, 100) for res in (0, 5) if res <= lim
                  for used in (0, 7, 100) for inc in (-1, 0, 1, 50, 200)))
check("policy", "negative limit -> ValueError", lambda: raises(ValueError, policy.Budget, -1))
check("policy", "negative reserved -> ValueError", lambda: raises(ValueError, policy.Budget, 10, -1))
check("policy", "reserved > limit -> ValueError", lambda: raises(ValueError, policy.Budget, 10, 11))

# ----------------------------------------------------------------- compact
check("compact", "exports head_tail/drop_oldest",
      lambda: all(hasattr(compact, n) for n in ("head_tail", "drop_oldest")))
check("compact", "head_tail returns text unchanged when it fits",
      lambda: all(compact.head_tail(t, estimate.count_tokens(t)) == t
                  for t in ("", "short", "z" * 200)))
MARKER = "\n...[elided]...\n"
check("compact", "head_tail uses marker '\\n...[elided]...\\n' when eliding",
      lambda: MARKER in compact.head_tail("y" * 4000, 100))
check("compact", "head_tail result fits budget per count_tokens", lambda: (
    True if all(estimate.count_tokens(compact.head_tail("w" * n, b)) <= b
                for n in (50, 500, 5000) for b in (0, 1, 3, 4, 5, 10, 60, 200))
    else "over budget"))
check("compact", "head_tail keeps head_frac prefix + suffix", lambda: (
    lambda r: (r.startswith("a") and r.endswith("b") and MARKER in r)
    or f"got {r[:20]!r}...{r[-20:]!r}")(compact.head_tail("a" * 2000 + "b" * 2000, 120)))
check("compact", "head_tail head_frac default 0.6",
      lambda: inspect.signature(compact.head_tail).parameters["head_frac"].default == 0.6)
check("compact", "drop_oldest drops from front until it fits", lambda: (
    lambda msgs: (compact.drop_oldest(msgs, 20)
                  == msgs[len(msgs) - len(compact.drop_oldest(msgs, 20)):]
                  and estimate.estimate_messages(compact.drop_oldest(msgs, 20)) <= 20)
    or "not a suffix / does not fit")([{"role": "u", "content": "m" * 40} for _ in range(6)]))
check("compact", "drop_oldest always keeps the last message",
      lambda: compact.drop_oldest([{"content": "x" * 10}, {"content": "y" * 4000}], 5)
      == [{"content": "y" * 4000}])
check("compact", "drop_oldest does not mutate input", lambda: (
    lambda msgs: (compact.drop_oldest(msgs, 1) is not None and len(msgs) == 4)
    or "mutated")([{"content": "c" * 40} for _ in range(4)]))

# ------------------------------------------------------------------ ledger
L = ledger.Ledger
check("ledger", "exports Ledger with charge/release/usage/total",
      lambda: all(callable(getattr(L, n, None)) for n in ("charge", "release", "usage", "total")))
check("ledger", "Ledger() takes no required args", lambda: L() is not None)
check("ledger", "charge returns new balance",
      lambda: (lambda g: g.charge("a", 5) == 5 and g.charge("a", 7) == 12)(L()))
check("ledger", "release returns new balance",
      lambda: (lambda g: (g.charge("a", 10), g.release("a", 4))[1] == 6)(L()))
check("ledger", "balances never go below zero",
      lambda: (lambda g: (g.charge("a", 3), g.release("a", 99))[1] == 0
               and g.usage()["a"] == 0 and g.total() == 0)(L()))
check("ledger", "usage() returns a copy", lambda: (
    lambda g: (g.charge("a", 5), g.usage().__setitem__("a", 999),
               g.usage()["a"] == 5)[-1] or "internal dict leaked")(L()))
check("ledger", "charge negative -> ValueError", lambda: raises(ValueError, L().charge, "a", -1))
check("ledger", "total() sums balances",
      lambda: (lambda g: (g.charge("a", 3), g.charge("b", 4), g.total() == 7)[-1])(L()))
check("ledger", "release negative -> ValueError (architect ruling)",
      lambda: raises(ValueError, L().release, "a", -1))
check("ledger", "release to 0 keeps key in usage() (architect ruling)",
      lambda: (lambda g: (g.charge("a", 2), g.release("a", 2), "a" in g.usage())[-1])(L()))

# ----------------------------------------------------------------- planner
pf = planner.plan_fanout
check("planner", "exports plan_fanout", lambda: callable(pf))
check("planner", "reserve_frac default 0.1",
      lambda: inspect.signature(pf).parameters["reserve_frac"].default == 0.1)
check("planner", "returns exactly n_agents non-negative ints", lambda: all(
    len(pf(t, n)) == n and all(isinstance(x, int) and x >= 0 for x in pf(t, n))
    for t in (0, 1, 7, 100, 1000, 99991) for n in (1, 2, 3, 7, 64)))
check("planner", "sums to at most total*(1-reserve_frac)", lambda: all(
    sum(pf(t, n, rf)) <= t * (1 - rf) + 1e-9
    for t in (0, 1, 7, 100, 1000, 99991) for n in (1, 2, 3, 7, 64)
    for rf in (0.0, 0.1, 0.25, 0.5, 1.0)))
check("planner", "as even as possible (max-min <= 1)", lambda: all(
    max(pf(t, n)) - min(pf(t, n)) <= 1
    for t in (0, 1, 7, 100, 1000, 99991) for n in (1, 2, 3, 7, 64)))
check("planner", "remainder goes to earliest agents (non-increasing)", lambda: all(
    pf(t, n) == sorted(pf(t, n), reverse=True)
    for t in (0, 1, 7, 100, 1000, 99991) for n in (1, 2, 3, 7, 64)))
check("planner", "n_agents < 1 -> ValueError", lambda: raises(ValueError, pf, 100, 0))
check("planner", "total_budget < 0 -> ValueError", lambda: raises(ValueError, pf, -1, 4))

# --------------------------------------------------------------------- cli
def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


TMP = pathlib.Path("/workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/0/_sample.txt")
TMP.write_text("hello world " * 60, encoding="utf-8")

check("cli", "exports main(argv=None) -> int",
      lambda: callable(cli.main)
      and inspect.signature(cli.main).parameters["argv"].default is None)
check("cli", "count --text prints JSON, exit 0", lambda: (
    lambda r: (r[0] == 0 and json.loads(r[1])["tokens"] == estimate.count_tokens("abcd efgh"))
    or f"exit={r[0]} out={r[1]!r}")(run(["count", "--text", "abcd efgh"])))
check("cli", "count --file prints JSON, exit 0", lambda: (
    lambda r: (r[0] == 0 and json.loads(r[1])["tokens"]
               == estimate.count_tokens(TMP.read_text())) or f"exit={r[0]} out={r[1]!r}")(
    run(["count", "--file", str(TMP)])))
check("cli", "plan prints a JSON list, exit 0", lambda: (
    lambda r: (r[0] == 0 and json.loads(r[1]) == pf(1000, 4)) or f"exit={r[0]} out={r[1]!r}")(
    run(["plan", "--total", "1000", "--agents", "4"])))
check("cli", "compact prints compacted text, exit 0", lambda: (
    lambda r: (r[0] == 0
               and r[1].rstrip("\n") == compact.head_tail(TMP.read_text(), 30).rstrip("\n"))
    or f"exit={r[0]} out!=head_tail")(run(["compact", "--file", str(TMP), "--budget", "30"])))
check("cli", "INT-1: compact stdout as printed fits the budget", lambda: (
    lambda bad: bad == []
    or f"printed stream exceeds budget by 1 token at budgets {bad[:6]}{'...' if len(bad) > 6 else ''}")(
    [b for b in range(1, 40)
     if estimate.count_tokens(run(["compact", "--file", str(TMP), "--budget", str(b)])[1]) > b]))
check("cli", "missing --file path -> exit 2",
      lambda: run(["count", "--file", "/nonexistent/nope.txt"])[0] == 2)
check("cli", "bad numeric arg -> exit 2",
      lambda: run(["plan", "--total", "abc", "--agents", "4"])[0] == 2)
check("cli", "unknown subcommand -> exit 2", lambda: run(["frobnicate"])[0] == 2)
check("cli", "no subcommand -> exit 2", lambda: run([])[0] == 2)
check("cli", "count with neither --text nor --file -> exit 2", lambda: run(["count"])[0] == 2)
check("cli", "never raises SystemExit", lambda: (
    True if not isinstance(run(["--help-nope"]), BaseException) else "raised"))

# ------------------------------------------------------------ package/rules
PUBLIC = ["count_tokens", "estimate_messages", "Budget", "BudgetExceeded",
          "head_tail", "drop_oldest", "Ledger", "plan_fanout", "main"]
check("package", "__init__ re-exports every contract name",
      lambda: [n for n in PUBLIC if not hasattr(tokenbudget, n)] == []
      or f"missing {[n for n in PUBLIC if not hasattr(tokenbudget, n)]}")
check("package", "__init__ has a module docstring",
      lambda: bool((tokenbudget.__doc__ or "").strip()))
check("package", "every module has a docstring",
      lambda: [m.__name__ for m in (cli, compact, estimate, ledger, planner, policy)
               if not (m.__doc__ or "").strip()] == [])
check("package", "no third-party imports", lambda: (
    lambda bad: bad == [] or f"third-party: {bad}")([
        f"{p.name}:{a.name if isinstance(n, ast.Import) else n.module}"
        for p in sorted((ART / "tokenbudget").glob("*.py"))
        for n in ast.walk(ast.parse(p.read_text()))
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
        if (mod := (a.name if isinstance(n, ast.Import) else (n.module or ""))).split(".")[0]
        not in sys.stdlib_module_names | {"tokenbudget", ""}]))
check("package", "no module imports another module's private names", lambda: (
    lambda bad: bad == [] or f"private imports: {bad}")([
        f"{p.name}:{a.name}"
        for p in sorted((ART / "tokenbudget").glob("*.py"))
        for n in ast.walk(ast.parse(p.read_text()))
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("tokenbudget")
        for a in n.names if a.name.startswith("_")]))
check("package", "public functions have type annotations", lambda: (
    lambda bad: bad == [] or f"unannotated: {bad}")([
        f"{m.__name__}.{name}"
        for m in (cli, compact, estimate, ledger, planner, policy)
        for name, obj in vars(m).items()
        if not name.startswith("_") and inspect.isfunction(obj)
        and obj.__module__ == m.__name__
        and (any(p.annotation is inspect.Parameter.empty
                 for pn, p in inspect.signature(obj).parameters.items() if pn != "self")
             or inspect.signature(obj).return_annotation is inspect.Signature.empty)]))
check("package", "pyproject.toml exists", lambda: (ART / "pyproject.toml").is_file())


def _fuzz_head_tail() -> object:
    rng = random.Random(20260830)
    alphabet = "abcdefg \n\t.?"
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 300)))
        budget = rng.randint(0, 90)
        frac = rng.choice([0.0, 0.1, 0.35, 0.6, 0.9, 1.0])
        out = compact.head_tail(text, budget, frac)
        if estimate.count_tokens(out) > budget:
            return f"over budget: len(text)={len(text)} budget={budget} frac={frac}"
        if estimate.count_tokens(text) <= budget and out != text:
            return f"altered text that already fit: budget={budget}"
    return True


def _fuzz_planner() -> object:
    rng = random.Random(1234)
    for _ in range(4000):
        total = rng.randint(0, 5_000_000)
        n = rng.randint(1, 200)
        frac = rng.choice([0.0, 0.05, 0.1, 0.5, 0.9, 1.0])
        shares = planner.plan_fanout(total, n, frac)
        if len(shares) != n or any(s < 0 for s in shares):
            return f"bad shape: total={total} n={n}"
        if sum(shares) > total * (1 - frac) + 1e-9:
            return f"over cap: total={total} n={n} frac={frac} sum={sum(shares)}"
        if max(shares) - min(shares) > 1 or shares != sorted(shares, reverse=True):
            return f"not even/earliest-first: total={total} n={n} frac={frac}"
    return True


def _fuzz_drop_oldest() -> object:
    rng = random.Random(99)
    for _ in range(2000):
        msgs = [{"role": "u", "content": "z" * rng.randint(0, 120)}
                for _ in range(rng.randint(1, 10))]
        budget = rng.randint(0, 200)
        kept = compact.drop_oldest(msgs, budget)
        if not kept or kept[-1] is not msgs[-1]:
            return "dropped the last message"
        if kept != msgs[len(msgs) - len(kept):]:
            return "kept messages are not a suffix"
        if len(kept) > 1 and estimate.estimate_messages(kept) > budget:
            return f"stopped dropping too early: budget={budget}"
    return True


check("compact", "fuzz: head_tail always fits budget (4000 cases)", _fuzz_head_tail)
check("compact", "fuzz: drop_oldest suffix/keeps-last (2000 cases)", _fuzz_drop_oldest)
check("planner", "fuzz: shape/cap/evenness (4000 cases)", _fuzz_planner)
check("package", "tests/ directory exists", lambda: (ART / "tests").is_dir())

TMP.unlink(missing_ok=True)

fails = [r for r in RESULTS if not r[2]]
by_mod: dict[str, list] = {}
for mod, name, ok, detail in RESULTS:
    by_mod.setdefault(mod, []).append((name, ok, detail))
for mod, rows in by_mod.items():
    npass = sum(1 for _, ok, _ in rows if ok)
    print(f"\n=== {mod}: {npass}/{len(rows)} ===")
    for name, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail else ''}")
print(f"\nTOTAL: {len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
sys.exit(1 if fails else 0)
