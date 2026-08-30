#!/usr/bin/env python3
"""Held-out grading suite for Experiment 2 (collaborative software development).

The agents never see this file. They see `spec.md` and a small `tests_visible.py`
with a handful of examples; this suite is what the paper reports. Keeping it
hidden matters because the failure mode we are measuring -- eight agents
disagreeing about an internal interface -- is invisible to a test suite the
agents could tune against.

Each case runs in a fresh subprocess with a wall-clock limit, so a module that
loops forever or blows the Python stack costs one test rather than the run. The
categories mirror the specification's sections, which lets the paper report *what
kind* of integration failed rather than only how much.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: (category, source, expected_written_form). ``None`` expected means the case
#: must raise SchemeError.
CASES: List[Tuple[str, str, Optional[str]]] = [
    # ---- reader -------------------------------------------------------
    ("reader", "42", "42"),
    ("reader", "-7", "-7"),
    ("reader", "1.5", "1.5"),
    ("reader", "#t", "#t"),
    ("reader", "#f", "#f"),
    ("reader", '"hi"', '"hi"'),
    ("reader", r'"a\"b"', r'"a\"b"'),
    ("reader", "'foo", "foo"),
    ("reader", "'()", "()"),
    ("reader", "'(1 2 3)", "(1 2 3)"),
    ("reader", "'(1 . 2)", "(1 . 2)"),
    ("reader", "'(1 2 . 3)", "(1 2 . 3)"),
    ("reader", "'#(1 2 3)", "#(1 2 3)"),
    ("reader", "[+ 1 2]", "3"),
    ("reader", "; a comment\n(+ 1 2)", "3"),
    ("reader", "#| block |# (+ 1 2)", "3"),
    ("reader", "#\\a", "#\\a"),
    ("reader", "#\\space", "#\\space"),
    ("reader", "(+ 1 2", None),
    ("reader", "'(1 2))", None),
    # ---- special forms ------------------------------------------------
    ("special", "(if #t 1 2)", "1"),
    ("special", "(if #f 1 2)", "2"),
    ("special", "(if (> 3 2) 'yes 'no)", "yes"),
    ("special", "(begin 1 2 3)", "3"),
    ("special", "(define x 5) x", "5"),
    ("special", "(define (f a b) (+ a b)) (f 2 3)", "5"),
    ("special", "(define x 1) (set! x 9) x", "9"),
    ("special", "((lambda (x) (* x x)) 7)", "49"),
    ("special", "((lambda args args) 1 2 3)", "(1 2 3)"),
    ("special", "((lambda (a . rest) rest) 1 2 3)", "(2 3)"),
    ("special", "(let ((a 1) (b 2)) (+ a b))", "3"),
    ("special", "(let* ((a 1) (b (+ a 1))) b)", "2"),
    ("special", "(letrec ((f (lambda (n) (if (= n 0) 1 (* n (f (- n 1))))))) (f 5))", "120"),
    ("special", "(let loop ((i 0) (acc 0)) (if (= i 5) acc (loop (+ i 1) (+ acc i))))", "10"),
    ("special", "(cond ((= 1 2) 'a) ((= 1 1) 'b) (else 'c))", "b"),
    ("special", "(cond ((= 1 2) 'a) (else 'c))", "c"),
    ("special", "(cond ((assv 2 '((1 a) (2 b))) => cadr) (else 'none))", "b"),
    ("special", "(case 3 ((1 2) 'low) ((3 4) 'mid) (else 'high))", "mid"),
    ("special", "(case 9 ((1 2) 'low) (else 'high))", "high"),
    ("special", "(and 1 2 3)", "3"),
    ("special", "(and 1 #f 3)", "#f"),
    ("special", "(and)", "#t"),
    ("special", "(or #f #f 5)", "5"),
    ("special", "(or)", "#f"),
    ("special", "(when #t 'yes)", "yes"),
    ("special", "(unless #f 'yes)", "yes"),
    ("special", "(do ((i 0 (+ i 1)) (s 0 (+ s i))) ((= i 5) s))", "10"),
    ("special", "`(1 2 ,(+ 1 2))", "(1 2 3)"),
    ("special", "(let ((xs '(2 3))) `(1 ,@xs 4))", "(1 2 3 4)"),
    ("special", "(quote (a b))", "(a b)"),
    # ---- tail calls ---------------------------------------------------
    ("tailcall",
     "(define (loop i) (if (= i 100000) 'done (loop (+ i 1)))) (loop 0)", "done"),
    ("tailcall",
     "(let go ((i 0)) (if (= i 100000) i (go (+ i 1))))", "100000"),
    ("tailcall",
     "(define (even2? n) (if (= n 0) #t (odd2? (- n 1))))"
     " (define (odd2? n) (if (= n 0) #f (even2? (- n 1))))"
     " (even2? 50000)", "#t"),
    # ---- numerics -----------------------------------------------------
    ("numeric", "(+ 1 2 3 4)", "10"),
    ("numeric", "(+)", "0"),
    ("numeric", "(* 2 3 4)", "24"),
    ("numeric", "(*)", "1"),
    ("numeric", "(- 10 3 2)", "5"),
    ("numeric", "(- 5)", "-5"),
    ("numeric", "(/ 12 3)", "4"),
    ("numeric", "(abs -4)", "4"),
    ("numeric", "(max 1 7 3)", "7"),
    ("numeric", "(min 1 7 3)", "1"),
    ("numeric", "(quotient 7 2)", "3"),
    ("numeric", "(remainder 7 2)", "1"),
    ("numeric", "(remainder -7 2)", "-1"),
    ("numeric", "(modulo -7 2)", "1"),
    ("numeric", "(gcd 12 18)", "6"),
    ("numeric", "(lcm 4 6)", "12"),
    ("numeric", "(expt 2 10)", "1024"),
    ("numeric", "(floor 2.7)", "2.0"),
    ("numeric", "(= 2 2 2)", "#t"),
    ("numeric", "(< 1 2 3)", "#t"),
    ("numeric", "(< 1 3 2)", "#f"),
    ("numeric", "(>= 3 3 2)", "#t"),
    ("numeric", "(zero? 0)", "#t"),
    ("numeric", "(even? 4)", "#t"),
    ("numeric", "(odd? 4)", "#f"),
    ("numeric", "(number? 1)", "#t"),
    ("numeric", "(number? 'a)", "#f"),
    ("numeric", "(number->string 42)", '"42"'),
    ("numeric", '(string->number "42")', "42"),
    ("numeric", "(/ 1 0)", None),
    # ---- pairs and lists ----------------------------------------------
    ("list", "(car '(1 2 3))", "1"),
    ("list", "(cdr '(1 2 3))", "(2 3)"),
    ("list", "(cons 1 2)", "(1 . 2)"),
    ("list", "(cons 1 '(2))", "(1 2)"),
    ("list", "(cadr '(1 2 3))", "2"),
    ("list", "(caddr '(1 2 3))", "3"),
    ("list", "(length '(1 2 3))", "3"),
    ("list", "(append '(1 2) '(3 4))", "(1 2 3 4)"),
    ("list", "(append '(1) '(2) '(3))", "(1 2 3)"),
    ("list", "(reverse '(1 2 3))", "(3 2 1)"),
    ("list", "(list 1 2 3)", "(1 2 3)"),
    ("list", "(list)", "()"),
    ("list", "(list-ref '(a b c) 1)", "b"),
    ("list", "(list-tail '(a b c) 1)", "(b c)"),
    ("list", "(memq 'b '(a b c))", "(b c)"),
    ("list", "(memq 'z '(a b c))", "#f"),
    ("list", "(assq 'b '((a 1) (b 2)))", "(b 2)"),
    ("list", "(assoc \"b\" '((\"a\" 1) (\"b\" 2)))", '("b" 2)'),
    ("list", "(null? '())", "#t"),
    ("list", "(null? '(1))", "#f"),
    ("list", "(pair? '(1))", "#t"),
    ("list", "(pair? '())", "#f"),
    ("list", "(list? '(1 2))", "#t"),
    ("list", "(list? '(1 . 2))", "#f"),
    ("list", "(map (lambda (x) (* x x)) '(1 2 3))", "(1 4 9)"),
    ("list", "(map + '(1 2) '(10 20))", "(11 22)"),
    ("list", "(apply + '(1 2 3))", "6"),
    ("list", "(apply + 1 '(2 3))", "6"),
    ("list", "(filter odd? '(1 2 3 4 5))", "(1 3 5)"),
    ("list", "(define p (cons 1 2)) (set-car! p 9) p", "(9 . 2)"),
    ("list", "(define p (cons 1 2)) (set-cdr! p 9) p", "(1 . 9)"),
    ("list", "(car '())", None),
    ("list", "(list-ref '(a b) 5)", None),
    # ---- equality and predicates --------------------------------------
    ("equality", "(eq? 'a 'a)", "#t"),
    ("equality", "(eq? '() '())", "#t"),
    ("equality", "(eqv? 1 1)", "#t"),
    ("equality", "(equal? '(1 2 (3)) '(1 2 (3)))", "#t"),
    ("equality", "(equal? '(1 2) '(1 3))", "#f"),
    ("equality", '(equal? "ab" "ab")', "#t"),
    ("equality", "(not #f)", "#t"),
    ("equality", "(not 0)", "#f"),
    ("equality", "(boolean? #t)", "#t"),
    ("equality", "(symbol? 'a)", "#t"),
    ("equality", "(procedure? car)", "#t"),
    ("equality", "(procedure? (lambda (x) x))", "#t"),
    ("equality", "(string? \"a\")", "#t"),
    ("equality", "(vector? '#(1))", "#t"),
    # ---- strings, chars, symbols --------------------------------------
    ("string", '(string-length "hello")', "5"),
    ("string", '(string-ref "hello" 1)', "#\\e"),
    ("string", '(substring "hello" 1 3)', '"el"'),
    ("string", '(string-append "ab" "cd" "ef")', '"abcdef"'),
    ("string", '(string->list "ab")', "(#\\a #\\b)"),
    ("string", "(list->string (list #\\a #\\b))", '"ab"'),
    ("string", '(string->symbol "abc")', "abc"),
    ("string", "(symbol->string 'abc)", '"abc"'),
    ("string", '(string=? "a" "a")', "#t"),
    ("string", '(string<? "a" "b")', "#t"),
    ("string", '(string-upcase "aB")', '"AB"'),
    ("string", '(string-downcase "aB")', '"ab"'),
    ("string", "(char->integer #\\A)", "65"),
    ("string", "(integer->char 65)", "#\\A"),
    ("string", "(char=? #\\a #\\a)", "#t"),
    ("string", "(char-alphabetic? #\\a)", "#t"),
    ("string", "(char-numeric? #\\5)", "#t"),
    ("string", "(char-whitespace? #\\space)", "#t"),
    ("string", '(string-ref "ab" 9)', None),
    # ---- vectors ------------------------------------------------------
    ("vector", "(vector 1 2 3)", "#(1 2 3)"),
    ("vector", "(vector-length (vector 1 2))", "2"),
    ("vector", "(vector-ref (vector 1 2) 1)", "2"),
    ("vector", "(define v (vector 1 2)) (vector-set! v 0 9) v", "#(9 2)"),
    ("vector", "(vector->list (vector 1 2))", "(1 2)"),
    ("vector", "(list->vector '(1 2))", "#(1 2)"),
    ("vector", "(make-vector 3 0)", "#(0 0 0)"),
    ("vector", "(vector-ref (vector 1) 5)", None),
    # ---- errors and control -------------------------------------------
    ("error", "(undefined-variable-xyz)", None),
    ("error", "(1 2 3)", None),
    ("error", "((lambda (x) x))", None),
    ("error", "((lambda (x) x) 1 2)", None),
    ("error", '(error "boom" 1 2)', None),
    ("error", "(begin (display \"x\") 'ok)", "ok"),
    ("error", "(begin (write \"x\") (newline) 'ok)", "ok"),
    # ---- integration: programs that use several modules at once -------
    ("integration",
     "(define (fact n) (if (= n 0) 1 (* n (fact (- n 1))))) (fact 10)", "3628800"),
    ("integration",
     "(define (fib n) (if (< n 2) n (+ (fib (- n 1)) (fib (- n 2))))) (fib 15)", "610"),
    ("integration",
     "(define (range a b) (if (>= a b) '() (cons a (range (+ a 1) b))))"
     " (apply + (map (lambda (x) (* x x)) (range 1 11)))", "385"),
    ("integration",
     "(define (make-counter) (let ((n 0)) (lambda () (set! n (+ n 1)) n)))"
     " (define c (make-counter)) (c) (c) (c)", "3"),
    ("integration",
     "(define (qsort xs)"
     "  (if (null? xs) '()"
     "    (let ((p (car xs)) (rest (cdr xs)))"
     "      (append (qsort (filter (lambda (x) (< x p)) rest))"
     "              (list p)"
     "              (qsort (filter (lambda (x) (>= x p)) rest))))))"
     " (qsort '(3 1 4 1 5 9 2 6))", "(1 1 2 3 4 5 6 9)"),
    ("integration",
     "(define (tree-sum t) (cond ((null? t) 0) ((pair? t) (+ (tree-sum (car t)) (tree-sum (cdr t)))) (else t)))"
     " (tree-sum '(1 (2 3) ((4) 5)))", "15"),
    ("integration",
     '(define (join xs sep) (if (null? xs) "" (if (null? (cdr xs)) (car xs)'
     ' (string-append (car xs) sep (join (cdr xs) sep)))))'
     ' (join (list "a" "b" "c") ",")', '"a,b,c"'),
    ("integration",
     "(define v (make-vector 5 0))"
     " (let fill ((i 0)) (if (= i 5) 'done (begin (vector-set! v i (* i i)) (fill (+ i 1)))))"
     " (vector->list v)", "(0 1 4 9 16)"),
    ("integration",
     "(define (assoc-update al k v)"
     "  (cond ((null? al) (list (list k v)))"
     "        ((equal? (caar al) k) (cons (list k v) (cdr al)))"
     "        (else (cons (car al) (assoc-update (cdr al) k v)))))"
     " (assoc-update '((a 1) (b 2)) 'b 9)", "((a 1) (b 9))"),
    ("integration",
     "(let loop ((i 0) (acc '())) (if (= i 5) (reverse acc) (loop (+ i 1) (cons (* i 2) acc))))",
     "(0 2 4 6 8)"),
]

RUNNER = r'''
import json, sys
sys.setrecursionlimit(20000)
sys.path.insert(0, sys.argv[1])
src = json.loads(sys.argv[2])
try:
    from interp import run, SchemeError
except Exception as exc:
    print(json.dumps({"status": "import_error", "detail": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)
try:
    out = run(src)
    print(json.dumps({"status": "ok", "value": out if isinstance(out, str) else repr(out)}))
except SchemeError as exc:
    print(json.dumps({"status": "scheme_error", "detail": str(exc)[:200]}))
except RecursionError as exc:
    print(json.dumps({"status": "recursion_error", "detail": str(exc)[:200]}))
except Exception as exc:
    print(json.dumps({"status": "python_error", "detail": f"{type(exc).__name__}: {exc}"[:200]}))
'''


def run_case(project: Path, source: str, timeout: float) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER, str(project), json.dumps(source)],
            capture_output=True, text=True, timeout=timeout, cwd=str(project),
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return {"status": "crash", "detail": (proc.stderr or "")[-200:]}
    try:
        return json.loads(line[-1])
    except Exception:
        return {"status": "crash", "detail": (proc.stdout + proc.stderr)[-200:]}


def normalise(v: str) -> str:
    return " ".join(str(v).split())


def grade(project: Path, *, timeout: float = 20.0, verbose: bool = False) -> Dict[str, Any]:
    by_cat: Dict[str, Counter] = {}
    results: List[Dict[str, Any]] = []
    import_error: Optional[str] = None
    for cat, src, expected in CASES:
        res = run_case(project, src, timeout)
        if res["status"] == "import_error" and import_error is None:
            import_error = res.get("detail")
        if expected is None:
            ok = res["status"] == "scheme_error"
        else:
            ok = res["status"] == "ok" and normalise(res.get("value", "")) == normalise(expected)
        by_cat.setdefault(cat, Counter())["total"] += 1
        by_cat[cat]["pass" if ok else "fail"] += 1
        by_cat[cat][f"st:{res['status']}"] += 1
        entry = {"category": cat, "source": src, "expected": expected,
                 "status": res["status"], "got": res.get("value"),
                 "detail": res.get("detail"), "pass": ok}
        results.append(entry)
        if verbose and not ok:
            print(f"FAIL [{cat}] {src[:70]!r} -> {res}")
    total = len(CASES)
    passed = sum(1 for r in results if r["pass"])
    status_hist = Counter(r["status"] for r in results)
    return {
        "project": str(project),
        "cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4),
        "import_error": import_error,
        "by_category": {
            c: {"total": v["total"], "passed": v["pass"],
                "pass_rate": round(v["pass"] / v["total"], 4)}
            for c, v in sorted(by_cat.items())
        },
        "status_histogram": dict(status_hist),
        "integration_failure_modes": {
            k: v for k, v in Counter(
                r["status"] for r in results if not r["pass"]
            ).items()
        },
        "failures": [r for r in results if not r["pass"]][:60],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("projects", nargs="+", help="project roots containing interp/")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    out: Dict[str, Any] = {"experiment": "e2_codev", "suite_size": len(CASES), "arms": {}}
    for p in args.projects:
        root = Path(p).resolve()
        name = root.name
        exp = root / "experiment.json"
        if exp.exists():
            try:
                name = json.loads(exp.read_text())["arm"]
            except Exception:
                pass
        out["arms"][name] = grade(root / "project", timeout=args.timeout, verbose=args.verbose)
    if len(out["arms"]) > 1:
        out["comparison"] = {
            "pass_rate": {k: v["pass_rate"] for k, v in out["arms"].items()},
            "by_category": {
                k: {c: d["pass_rate"] for c, d in v["by_category"].items()}
                for k, v in out["arms"].items()
            },
        }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        print(json.dumps({k: {"pass_rate": v["pass_rate"], "by_category":
                              {c: d["pass_rate"] for c, d in v["by_category"].items()}}
                          for k, v in out["arms"].items()}, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
