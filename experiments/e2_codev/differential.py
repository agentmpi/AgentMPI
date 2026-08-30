#!/usr/bin/env python3
"""Differential testing between the two E2 arms.

The held-out suite saturated: both arms passed all 174 cases, so it bounds each
arm from below and measures neither. Extending it by hand after seeing one arm's
failures would not be an experiment, it would be a way of getting the answer we
wanted -- and both teams' own agents reported residual defects, so we know there
are differences the suite missed.

Differential testing gives a way out that is symmetric between the arms by
construction. We generate programs from a grammar written against the
specification, run each program under both interpreters, and compare. Every
disagreement is a defect in at least one arm, and no program was chosen because
we already knew it would fail. The generator is seeded, so the whole run is
reproducible.

Attribution is deliberately conservative. When the two arms disagree we report
the disagreement; we attribute blame only where the specification settles the
question unambiguously, and we quote the clause when we do. Where the
specification is silent -- and it is silent about several things, which is itself
a finding -- we record the disagreement as *underspecified* rather than pretending
one arm is wrong.

Usage:
    python3 experiments/e2_codev/differential.py runs/e2_ampi runs/e2_naive \\
        --n 3000 --seed 7 --out results/e2_differential.json
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# A typed grammar over the specification's primitives.
#
# Generating well-typed expressions rather than random tokens matters: a
# generator that mostly produces type errors would compare the two arms' error
# paths and little else. Each generator returns (source_text, type_tag).
# --------------------------------------------------------------------------

SMALL_INTS = [0, 1, 2, 3, 7, -1, -3, 10, 100]
FLOATS = ["1.5", "-0.5", "2.0", "0.25", "0.1", "1e21", "1e-7", "-1e10",
          "1234567890.125", "3.0", "1e100"]
STRINGS = ['""', '"a"', '"abc"', '"hello world"', r'"a\"b"']
CHARS = ["#\\a", "#\\Z", "#\\space", "#\\newline", "#\\0"]
SYMBOLS = ["'foo", "'bar", "'x"]


class Gen:
    def __init__(self, rng: random.Random, depth: int = 0) -> None:
        self.rng = rng
        self.depth = depth

    def deeper(self) -> "Gen":
        return Gen(self.rng, self.depth + 1)

    def pick(self, options: List[Callable[[], str]]) -> str:
        return self.rng.choice(options)()

    # ---- leaves ---------------------------------------------------------
    def int_lit(self) -> str:
        return str(self.rng.choice(SMALL_INTS))

    def num(self) -> str:
        if self.depth > 3 or self.rng.random() < 0.45:
            return self.rng.choice([self.int_lit(), self.rng.choice(FLOATS)])
        g = self.deeper()
        return self.pick([
            lambda: f"(+ {g.num()} {g.num()})",
            lambda: f"(- {g.num()} {g.num()})",
            lambda: f"(* {g.num()} {g.num()})",
            lambda: f"(abs {g.num()})",
            lambda: f"(max {g.num()} {g.num()})",
            lambda: f"(min {g.num()} {g.num()})",
            lambda: f"(quotient {g.int_lit()} {self.rng.choice([1, 2, 3, 7])})",
            lambda: f"(remainder {g.int_lit()} {self.rng.choice([1, 2, 3, 7])})",
            lambda: f"(modulo {g.int_lit()} {self.rng.choice([1, 2, 3, 7])})",
            lambda: f"(gcd {abs(self.rng.choice(SMALL_INTS))} {abs(self.rng.choice(SMALL_INTS))})",
            lambda: f"(lcm {abs(self.rng.choice(SMALL_INTS)) or 1} {abs(self.rng.choice(SMALL_INTS)) or 1})",
            lambda: f"(expt {self.rng.choice([0,1,2,3])} {self.rng.choice([0,1,2,3,4])})",
            lambda: f"(floor {self.rng.choice(FLOATS)})",
            lambda: f"(ceiling {self.rng.choice(FLOATS)})",
            lambda: f"(round {self.rng.choice(FLOATS)})",
            lambda: f"(truncate {self.rng.choice(FLOATS)})",
            lambda: f"(length {g.lst()})",
            lambda: f"(string-length {g.string()})",
            lambda: f"(char->integer {self.rng.choice(CHARS)})",
            lambda: f"(if {g.boolean()} {g.num()} {g.num()})",
            lambda: f"(let ((n {g.num()})) (+ n {g.int_lit()}))",
        ])

    def boolean(self) -> str:
        if self.depth > 3 or self.rng.random() < 0.35:
            return self.rng.choice(["#t", "#f"])
        g = self.deeper()
        return self.pick([
            lambda: f"(= {g.num()} {g.num()})",
            lambda: f"(< {g.num()} {g.num()})",
            lambda: f"(> {g.num()} {g.num()})",
            lambda: f"(<= {g.num()} {g.num()})",
            lambda: f"(>= {g.num()} {g.num()})",
            lambda: f"(zero? {g.num()})",
            lambda: f"(even? {g.int_lit()})",
            lambda: f"(odd? {g.int_lit()})",
            lambda: f"(number? {g.any_val()})",
            lambda: f"(string? {g.any_val()})",
            lambda: f"(symbol? {g.any_val()})",
            lambda: f"(pair? {g.any_val()})",
            lambda: f"(null? {g.lst()})",
            lambda: f"(list? {g.any_val()})",
            lambda: f"(procedure? {self.rng.choice(['car', 'cdr', '+', '(lambda (x) x)'])})",
            lambda: f"(boolean? {g.any_val()})",
            lambda: f"(char? {self.rng.choice(CHARS)})",
            lambda: f"(vector? {g.vector()})",
            lambda: f"(not {g.boolean()})",
            lambda: f"(and {g.boolean()} {g.boolean()})",
            lambda: f"(or {g.boolean()} {g.boolean()})",
            lambda: f"(eq? {g.symbol()} {g.symbol()})",
            lambda: f"(eqv? {g.int_lit()} {g.int_lit()})",
            lambda: f"(equal? {g.lst()} {g.lst()})",
            lambda: f"(string=? {g.string()} {g.string()})",
            lambda: f"(string<? {g.string()} {g.string()})",
            lambda: f"(char=? {self.rng.choice(CHARS)} {self.rng.choice(CHARS)})",
            lambda: f"(char-alphabetic? {self.rng.choice(CHARS)})",
            lambda: f"(char-numeric? {self.rng.choice(CHARS)})",
            lambda: f"(char-whitespace? {self.rng.choice(CHARS)})",
        ])

    def lst(self) -> str:
        if self.depth > 3 or self.rng.random() < 0.4:
            n = self.rng.randint(0, 3)
            return "'(" + " ".join(str(self.rng.choice(SMALL_INTS)) for _ in range(n)) + ")"
        g = self.deeper()
        return self.pick([
            lambda: f"(list {g.num()} {g.num()})",
            lambda: f"(cons {g.num()} {g.lst()})",
            lambda: f"(append {g.lst()} {g.lst()})",
            lambda: f"(reverse {g.lst()})",
            lambda: f"(cdr {g.nonempty_list()})",
            lambda: f"(map (lambda (x) (* x 2)) {g.lst()})",
            lambda: f"(map + {g.lst()} {g.lst()})",
            lambda: f"(filter odd? {g.lst()})",
            lambda: f"(string->list {g.string()})",
            lambda: f"(vector->list {g.vector()})",
            lambda: f"(list-tail {g.nonempty_list()} 1)",
            lambda: f"(memq {g.symbol()} '(foo bar x))",
            lambda: f"(assq {g.symbol()} '((foo 1) (bar 2)))",
            lambda: f"(let loop ((i 0) (acc '())) "
                    f"(if (= i {self.rng.randint(0, 4)}) (reverse acc) (loop (+ i 1) (cons i acc))))",
        ])

    def nonempty_list(self) -> str:
        n = self.rng.randint(1, 3)
        return "'(" + " ".join(str(self.rng.choice(SMALL_INTS)) for _ in range(n)) + ")"

    def string(self) -> str:
        if self.depth > 3 or self.rng.random() < 0.5:
            return self.rng.choice(STRINGS)
        g = self.deeper()
        return self.pick([
            lambda: f"(string-append {g.string()} {g.string()})",
            lambda: f"(substring \"abcdef\" {self.rng.randint(0,3)} {self.rng.randint(3,6)})",
            lambda: f"(string-upcase {g.string()})",
            lambda: f"(string-downcase {g.string()})",
            lambda: f"(symbol->string {g.symbol()})",
            lambda: f"(number->string {g.num()})",
            lambda: f"(list->string (list {self.rng.choice(CHARS)} {self.rng.choice(CHARS)}))",
            lambda: f"(string-copy {g.string()})",
            lambda: f"(make-string {self.rng.randint(0,3)} #\\a)",
        ])

    def symbol(self) -> str:
        return self.rng.choice(SYMBOLS + ["(string->symbol \"abc\")"])

    def vector(self) -> str:
        if self.depth > 3 or self.rng.random() < 0.5:
            n = self.rng.randint(0, 3)
            return "(vector " + " ".join(str(self.rng.choice(SMALL_INTS)) for _ in range(n)) + ")"
        g = self.deeper()
        return self.pick([
            lambda: f"(list->vector {g.lst()})",
            lambda: f"(make-vector {self.rng.randint(0,3)} {g.num()})",
            lambda: f"(vector {g.num()} {g.num()})",
        ])

    def any_val(self) -> str:
        return self.pick([self.num, self.boolean, self.lst, self.string,
                          self.symbol, self.vector,
                          lambda: self.rng.choice(CHARS)])

    # ---- whole programs -------------------------------------------------
    def degenerate(self) -> str:
        """Forms at the edge of the grammar the specification defines.

        Added as a whole production after both teams' agents independently
        reported defects in regions the original grammar never reached. We add the
        region, not the instances they found.
        """
        g = self.deeper()
        return self.rng.choice([
            lambda: "(cond)",
            lambda: "(and)",
            lambda: "(or)",
            lambda: f"(cond (else {g.num()}))",
            lambda: f"(case {g.int_lit()})",
            lambda: f"(case {g.int_lit()} (else {g.num()}))",
            lambda: f"(let () {g.num()})",
            lambda: f"(let* () {g.num()})",
            lambda: f"(letrec () {g.num()})",
            lambda: f"((lambda () {g.num()}))",
            lambda: f"(begin {g.num()})",
            lambda: "(list)",
            lambda: "(append)",
            lambda: f"(append {g.lst()})",
            lambda: "(vector)",
            lambda: "(string-append)",
            lambda: "(+)",
            lambda: "(*)",
            lambda: f"(max {g.num()})",
            lambda: f"(= {g.num()})",
            lambda: f"(do ((i 0 (+ i 1))) ((= i 0) {g.num()}))",
            lambda: f"(expt 10 {self.rng.choice([200, 400, 1500])})",
            lambda: f"(number->string {self.rng.choice(FLOATS)})",
            lambda: f"(number->string (expt 10 {self.rng.choice([25, 400])}))",
            lambda: f"(string->number \"{self.rng.choice(['1e21', '1e-7', '1_0', '0.1', '#xff'])}\")",
            lambda: f"(let ((n (expt 2 {self.rng.choice([64, 200, 4000])}))) (number->string n))",
            lambda: f"(substring \"abc\" {self.rng.randint(0,3)} {self.rng.randint(0,3)})",
            lambda: f"(list-tail {g.lst()} 0)",
            lambda: f"(make-vector 0 {g.num()})",
            lambda: "(make-string 0 #\\a)",
            lambda: "(reverse '())",
            lambda: "(map (lambda (x) x) '())",
            lambda: "(apply + '())",
        ])()

    def program(self) -> Tuple[str, str]:
        """Return (source, category)."""
        kind = self.rng.choices(
            ["value", "special", "closure", "tail", "mutate", "error", "degenerate"],
            weights=[32, 17, 10, 7, 7, 11, 16],
        )[0]
        g = Gen(self.rng, 0)
        if kind == "value":
            return g.any_val(), "value"
        if kind == "degenerate":
            return self.degenerate(), "degenerate"
        if kind == "special":
            return self.rng.choice([
                lambda: f"(cond ((= 1 2) 'a) ((= 1 1) {g.num()}) (else 'c))",
                lambda: f"(cond (#f 'a) (else {g.num()}))",
                lambda: f"(case {self.rng.choice([1,2,3,9])} ((1 2) 'low) ((3 4) 'mid) (else 'high))",
                lambda: f"(when {g.boolean()} {g.num()})",
                lambda: f"(unless {g.boolean()} {g.num()})",
                lambda: f"(begin {g.num()} {g.num()})",
                lambda: f"(let* ((a {g.num()}) (b (+ a 1))) b)",
                lambda: f"(letrec ((f (lambda (n) (if (= n 0) 1 (* n (f (- n 1))))))) "
                        f"(f {self.rng.randint(0,6)}))",
                lambda: f"(do ((i 0 (+ i 1)) (s 0 (+ s i))) ((= i {self.rng.randint(0,5)}) s))",
                lambda: f"`(1 ,{g.num()} 3)",
                lambda: f"(let ((xs {g.lst()})) `(0 ,@xs 9))",
                lambda: f"(if {g.boolean()} {g.num()})",
                lambda: f"(and {g.num()} {g.num()})",
                lambda: f"(or #f {g.num()})",
            ])(), "special"
        if kind == "closure":
            return self.rng.choice([
                lambda: f"((lambda (x) (* x x)) {g.num()})",
                lambda: f"((lambda args (length args)) {g.num()} {g.num()})",
                lambda: f"((lambda (a . rest) rest) {g.num()} {g.num()} {g.num()})",
                lambda: f"(define (f a b) (+ a b)) (f {g.num()} {g.num()})",
                lambda: "(define (mk) (let ((n 0)) (lambda () (set! n (+ n 1)) n))) "
                        "(define c (mk)) (c) (c)",
                lambda: f"(apply + {g.lst()})",
                lambda: f"(apply max (list {g.num()} {g.num()}))",
            ])(), "closure"
        if kind == "tail":
            n = self.rng.choice([1000, 5000, 20000])
            return self.rng.choice([
                lambda: f"(let go ((i 0)) (if (= i {n}) i (go (+ i 1))))",
                lambda: f"(define (loop i) (if (= i {n}) 'done (loop (+ i 1)))) (loop 0)",
                lambda: f"(let go ((i 0)) (cond ((= i {n}) i) (else (go (+ i 1)))))",
                lambda: f"(let go ((i 0)) (and #t (if (= i {n}) i (go (+ i 1)))))",
            ])(), "tail"
        if kind == "mutate":
            return self.rng.choice([
                lambda: f"(define p (cons 1 2)) (set-car! p {g.num()}) p",
                lambda: f"(define p (cons 1 2)) (set-cdr! p {g.num()}) p",
                lambda: f"(define v (vector 1 2 3)) (vector-set! v 1 {g.num()}) v",
                lambda: f"(define x {g.num()}) (set! x {g.num()}) x",
                lambda: f"(define v (make-vector 3 0)) (vector-fill! v {g.num()}) v",
            ])(), "mutate"
        # Programs that the specification says must raise SchemeError.
        return self.rng.choice([
            lambda: "(car '())",
            lambda: "(cdr '())",
            lambda: "(undefined-variable-xyz)",
            lambda: f"({g.num()} 1 2)",
            lambda: "((lambda (x) x))",
            lambda: "((lambda (x) x) 1 2)",
            lambda: "(/ 1 0)",
            lambda: "(vector-ref (vector 1) 5)",
            lambda: '(string-ref "ab" 9)',
            lambda: "(list-ref '(a b) 5)",
            lambda: '(error "boom" 1 2)',
            lambda: "(+ 1",
            lambda: "'(1 2))",
        ])(), "error"


RUNNER = r'''
import json, sys
sys.setrecursionlimit(20000)
sys.path.insert(0, sys.argv[1])
src = json.loads(sys.argv[2])
try:
    from interp import run, SchemeError
except Exception as exc:
    print(json.dumps({"s": "import_error", "d": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)
try:
    print(json.dumps({"s": "ok", "v": str(run(src))}))
except SchemeError as exc:
    print(json.dumps({"s": "scheme_error", "d": str(exc)[:120]}))
except RecursionError:
    print(json.dumps({"s": "recursion_error"}))
except Exception as exc:
    print(json.dumps({"s": "python_error", "d": f"{type(exc).__name__}: {exc}"[:120]}))
'''


def run_one(project: Path, src: str, timeout: float) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER, str(project), json.dumps(src)],
            capture_output=True, text=True, timeout=timeout, cwd=str(project),
        )
    except subprocess.TimeoutExpired:
        return {"s": "timeout"}
    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        return {"s": "crash", "d": (proc.stderr or "")[-120:]}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"s": "crash", "d": (proc.stdout + proc.stderr)[-120:]}


def norm(v: Optional[str]) -> str:
    return " ".join(str(v or "").split())


def classify(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """How do the two results relate? Symmetric in the arms."""
    if a["s"] == b["s"] == "ok":
        return "agree_value" if norm(a.get("v")) == norm(b.get("v")) else "disagree_value"
    if a["s"] == b["s"] == "scheme_error":
        return "agree_error"
    if a["s"] == b["s"]:
        return f"agree_other:{a['s']}"
    if {a["s"], b["s"]} == {"ok", "scheme_error"}:
        return "disagree_error_vs_value"
    return "disagree_status"


#: Cases where the specification settles which behaviour is correct. Keyed by the
#: category the generator assigned, with the clause quoted so a reader can check
#: the attribution rather than take our word for it.
SPEC_RULES = {
    "float_format": (
        "must print floats the way Python repr does",
        'spec.md, "Printed representation": "float | `1.5` (Python `repr`-like, but '
        '`2.0` not `2`)." Python renders 1e21 as "1e+21" and 1e-7 as "1e-07".',
    ),
    "error": (
        "must raise SchemeError",
        'spec.md, "Errors": "applying a non-procedure, wrong argument count, unbound '
        'variable, car of a non-pair, division by zero, and vector/string index out of '
        'range must all raise SchemeError with a non-empty message."',
    ),
    "tail": (
        "must complete without exhausting the stack",
        'spec.md, "Reader/Special forms": "Tail calls must not grow the Python stack. '
        'A loop written as a tail-recursive named let with 100000 iterations must '
        'complete."',
    ),
}


def _float_repr_disagreement(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[str]:
    """If the two arms differ only in float formatting, say which matches Python.

    Checked structurally rather than by pattern: we ask Python to render the value
    each arm produced and see which one it agrees with. That keeps the referee
    outside our judgement.
    """
    if a.get("s") != "ok" or b.get("s") != "ok":
        return None
    va, vb = norm(a.get("v")), norm(b.get("v"))
    if va == vb:
        return None
    try:
        fa, fb = float(va), float(vb)
    except (TypeError, ValueError):
        return None
    if fa != fb:
        return None  # genuinely different numbers, not a formatting difference
    canonical = repr(fa)
    if va == canonical and vb != canonical:
        return "ampi"
    if vb == canonical and va != canonical:
        return "naive"
    return None


def attribute(category: str, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Assign blame only where the specification is unambiguous."""
    who = _float_repr_disagreement(a, b)
    if who is not None:
        expectation, clause = SPEC_RULES["float_format"]
        return {"attributable": True, "expectation": expectation, "clause": clause,
                "conforming_arm": who, "kind": "float_format"}
    rule = SPEC_RULES.get(category)
    if rule is None:
        return {"attributable": False, "reason": "specification does not settle this"}
    expectation, clause = rule
    if category == "error":
        good = {"scheme_error"}
    else:
        good = {"ok"}
    a_ok = a["s"] in good
    b_ok = b["s"] in good
    if a_ok == b_ok:
        return {"attributable": False, "reason": "both arms equally (non-)conforming"}
    return {
        "attributable": True,
        "expectation": expectation,
        "clause": clause,
        "conforming_arm": "ampi" if a_ok else "naive",
    }


def _fingerprint(rel: str, att: Dict[str, Any], a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """A coarse signature of *what kind* of difference this is.

    Two programs share a fingerprint when they differ in the same way, so the
    count of distinct fingerprints is a far better estimate of how many defects
    were found than the count of disagreeing programs.
    """
    if att.get("kind") == "float_format":
        return "float_format"
    if rel == "disagree_error_vs_value":
        err = a if a.get("s") == "scheme_error" else b
        msg = str(err.get("d", ""))[:40]
        return f"error_vs_value:{msg}"
    if rel == "disagree_value":
        return f"value:{str(a.get('v'))[:12]}|{str(b.get('v'))[:12]}"
    return rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm_a", help="project root of the first arm (reported as 'ampi')")
    ap.add_argument("arm_b", help="project root of the second arm (reported as 'naive')")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pa = Path(args.arm_a).resolve() / "project"
    pb = Path(args.arm_b).resolve() / "project"
    rng = random.Random(args.seed)
    gen = Gen(rng)

    seen: set[str] = set()
    programs: List[Tuple[str, str]] = []
    guard = 0
    while len(programs) < args.n and guard < args.n * 25:
        guard += 1
        src, cat = gen.program()
        if src in seen:
            continue
        seen.add(src)
        programs.append((src, cat))

    outcomes = Counter()
    by_category = Counter()
    disagreements: List[Dict[str, Any]] = []
    attributed = Counter()
    attribution_kinds = Counter()
    # Program-level disagreement counts overstate the number of *defects*: one
    # wrong printer shows up in every generated program that happens to print a
    # float. Fingerprinting the observed difference lets us report distinct root
    # causes alongside the raw count.
    fingerprints = Counter()

    for i, (src, cat) in enumerate(programs, 1):
        ra = run_one(pa, src, args.timeout)
        rb = run_one(pb, src, args.timeout)
        rel = classify(ra, rb)
        outcomes[rel] += 1
        by_category[(cat, rel)] += 1
        if rel.startswith("disagree") or "python_error" in rel or "crash" in rel:
            att = attribute(cat, ra, rb)
            if att.get("attributable"):
                attributed[att["conforming_arm"]] += 1
                attribution_kinds[att.get("kind") or att["expectation"]] += 1
            fingerprints[_fingerprint(rel, att, ra, rb)] += 1
            disagreements.append(
                {"source": src, "category": cat, "relation": rel,
                 "ampi": ra, "naive": rb, "attribution": att}
            )
        if i % 250 == 0:
            print(f"  {i}/{len(programs)}  disagreements so far: {len(disagreements)}",
                  flush=True)

    total = len(programs)
    result = {
        "experiment": "e2_differential",
        "method": (
            "Programs generated from a grammar written against spec.md, run under both "
            "arms, results compared. Symmetric in the arms by construction: no program "
            "was selected because we knew it would fail. Seeded and reproducible. Blame "
            "is attributed only where the specification is unambiguous, with the clause "
            "quoted."
        ),
        "seed": args.seed,
        "programs": total,
        "arms": {"ampi": str(pa), "naive": str(pb)},
        "outcomes": dict(outcomes),
        "agreement_rate": round(
            sum(v for k, v in outcomes.items() if k.startswith("agree")) / max(1, total), 4
        ),
        "disagreement_count": len(disagreements),
        "attributable_disagreements": dict(attributed),
        "attribution_kinds": dict(attribution_kinds),
        "distinct_fingerprints": len(fingerprints),
        "fingerprints": dict(fingerprints.most_common()),
        "by_category": {f"{c}|{r}": n for (c, r), n in sorted(by_category.items())},
        "disagreements": disagreements[:400],
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwrote {args.out}")
    print(f"\n{total} programs, agreement {result['agreement_rate']:.4f}, "
          f"{len(disagreements)} disagreement(s)")
    for k, v in sorted(outcomes.items()):
        print(f"  {k:<28} {v}")
    if attributed:
        print(f"  attributable to a spec clause -> conforming arm: {dict(attributed)}")
    for d in disagreements[:12]:
        print(f"\n  [{d['category']}/{d['relation']}] {d['source'][:90]}")
        print(f"    ampi : {d['ampi']}")
        print(f"    naive: {d['naive']}")
        if d["attribution"].get("attributable"):
            print(f"    -> {d['attribution']['conforming_arm']} conforms "
                  f"({d['attribution']['expectation']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
