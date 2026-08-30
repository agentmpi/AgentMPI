# MiniScheme 0.1 — specification

You are implementing **MiniScheme**, a small Scheme interpreter in Python 3, as a
team. Each rank owns specific files. The public behaviour below is fixed and is
what the held-out test suite exercises; the *internal* interfaces between your
modules are **not** specified here and are yours to agree on.

That split is deliberate. Getting the public behaviour right is ordinary
programming. Getting eight independently-running agents to agree on how a pair
is represented, what an environment lookup returns when a name is unbound, and
which exception type a primitive raises — that is the part this experiment is
about.

## Public API (fixed — do not change)

The package lives at `interp/` relative to the project root.

```python
# interp/__init__.py
def run(source: str) -> str:
    """Evaluate every top-level datum in `source` and return the printed form
    of the LAST expression's value, using `write` representation.

    Raises SchemeError (see below) with a human-readable message on any
    evaluation or read error.
    """

class SchemeError(Exception):
    """The single error type that escapes `run`. Its str() must be non-empty."""
```

Nothing else is part of the public API. `run` must be importable as
`from interp import run, SchemeError`.

## Printed representation (`write`)

| value | printed as |
|---|---|
| integer | `42`, `-7` |
| float | `1.5` (Python `repr`-like, but `2.0` not `2`) |
| boolean | `#t` / `#f` |
| string | `"hi"` with `\"` and `\\` escaped |
| character | `#\a`, `#\space`, `#\newline` |
| symbol | `foo` |
| empty list | `()` |
| pair | `(1 2 3)`; improper: `(1 2 . 3)` |
| vector | `#(1 2 3)` |
| procedure | `#<procedure>` or `#<procedure name>` |
| unspecified (e.g. value of `define`) | `#<unspecified>` |

## Reader

- Atoms: integers (`-12`), floats (`3.5`, `.5`, `-1e3`), booleans (`#t`, `#f`,
  `#true`, `#false`), characters (`#\a`, `#\space`, `#\newline`, `#\tab`),
  strings with escapes `\n \t \\ \" \r`, symbols (anything else; case-sensitive).
- Lists `( ... )` and `[ ... ]` (interchangeable), dotted pairs `(a . b)`,
  vectors `#( ... )`.
- Quote sugar: `'x` → `(quote x)`, `` `x `` → `(quasiquote x)`, `,x` →
  `(unquote x)`, `,@x` → `(unquote-splicing x)`.
- Comments: `;` to end of line, and `#|` ... `|#` block comments (nestable).
- Unbalanced parens or a bad token must raise `SchemeError`.

## Special forms

`quote`, `if` (2 or 3 operands; a missing alternative yields unspecified),
`define` (variable and `(define (f a b) body...)` shorthand), `set!`, `lambda`
(fixed args, rest args `(lambda args ...)`, and `(lambda (a b . rest) ...)`),
`begin`, `let`, `let*`, `letrec`, named `let`, `cond` (with `else` and `=>`),
`case` (with `else`), `and`, `or`, `when`, `unless`, `do`, `quasiquote`
(with `unquote` and `unquote-splicing`, nesting depth 1 is enough),
`delay`/`force` are **not** required.

**Tail calls must not grow the Python stack.** A loop written as a tail-recursive
named `let` with 100000 iterations must complete. This is a hard requirement and
the test suite checks it.

## Primitive procedures

Numeric: `+ - * / = < > <= >= abs min max quotient remainder modulo gcd lcm
expt sqrt floor ceiling round truncate exact->inexact inexact->exact
number? integer? zero? positive? negative? odd? even? number->string
string->number`

Pairs/lists: `cons car cdr set-car! set-cdr! caar cadr cdar cddr caddr list
length append reverse list-tail list-ref memq memv member assq assv assoc
list? pair? null? map for-each apply filter reduce`

Predicates/equality: `eq? eqv? equal? not boolean? symbol? procedure? string?
char? vector?`

Strings/chars/symbols: `string-length string-ref substring string-append
string->list list->string string->symbol symbol->string string=? string<?
string-upcase string-downcase char->integer integer->char char=? char<?
char-alphabetic? char-numeric? char-whitespace? string-copy string
make-string number->string`

Vectors: `vector make-vector vector-length vector-ref vector-set! vector->list
list->vector vector-fill!`

Control/misc: `error` (raises `SchemeError` with the message and the printed
irritants), `display`, `newline`, `write`. `display` and `write` append to an
output buffer; `run` does **not** return that buffer, but `(begin (display "x")
'ok)` must not crash.

Errors: applying a non-procedure, wrong argument count, unbound variable, car of
a non-pair, division by zero, and vector/string index out of range must all raise
`SchemeError` with a non-empty message.

## Constraints

- Python 3.10+, **standard library only**. No third-party packages.
- Do not use Python recursion for Scheme tail calls.
- Every file you own must be syntactically valid at all times: your teammates
  import your modules to test their own work, and a file with a syntax error
  blocks the whole team.
- Do not edit a file you do not own. If you need a change in someone else's
  file, message them.

## Visible smoke tests

`tests_visible.py` in the project root contains a small number of examples so you
can check yourself. It is **not** the grading suite; the held-out suite is
larger and covers everything above.
