# Rank 0 (architect) --- Step 4 integration audit

Package: `tokenbudget` at `runs2/job/artifact`. Audited against the `contract`
cell of the `build` window (version 1, seeded by the harness), which matches
`runs2/job/contract.json` byte for byte.

Method: the six implementers wrote their own tests, so passing tests alone do
not prove contract conformance. I re-derived 63 checks straight from the
contract text in `ranks/0/audit.py` and ran them independently of `tests/`,
including 10,000 randomised cases against the three properties most likely to
have edge-case holes (head_tail budget fit, drop_oldest suffix/keep-last,
planner shape and cap).

## Module by module

### estimate (rank 1) --- 7/7, all requirements met
- `count_tokens` deterministic, `0` for `""` and `None`, monotone
  non-decreasing in `len(text)`, never negative: all verified.
- `estimate_messages` equals `sum(count_tokens(content)) + 4` per message.
- Leaf rule holds: AST scan confirms `estimate.py` imports no package module.
- Beyond contract (not a gap): also exports `CHARS_PER_TOKEN` and
  `PER_MESSAGE_OVERHEAD`, and raises `TypeError` on non-str input.

### policy (rank 2) --- 7/7, all requirements met
- `remaining()` equals `max(0, limit - reserved - used)` across the grid.
- `admits()` is true exactly when `incoming <= remaining(used)`.
- `ValueError` on negative `limit`, negative `reserved`, and `reserved > limit`.
- `Budget(limit: int, reserved: int = 0)` and `BudgetExceeded(Exception)` present.

### compact (rank 3) --- 11/11, all requirements met
- Returns text unchanged when it already fits.
- Elides with the exact marker `"\n...[elided]...\n"`, head prefix + tail suffix.
- Result fits the budget per `count_tokens` in all 4,000 fuzz cases, including
  the degenerate budgets (0-4 tokens) where the marker itself does not fit and
  the implementation falls back to the longest fitting prefix.
- `drop_oldest` drops only from the front, always keeps the last message even
  when it alone exceeds the budget, and does not mutate the caller's list.

### ledger (rank 4) --- 10/10, all requirements met
- `charge`/`release` return the new balance; balances clamp at zero.
- `usage()` returns a copy --- mutating the returned dict does not affect the
  ledger.
- `charge` with negative tokens raises `ValueError`.
- The two points rank 4 asked about (question tag 10, answered before the
  audit) are implemented the way I ruled: `release` with negative tokens also
  raises `ValueError`, and releasing down to 0 keeps the agent key in `usage()`.
- Beyond contract (not a gap): extra `balance()` accessor, `TypeError` on
  non-int tokens.

### planner (rank 5) --- 9/9, all requirements met
- Exactly `n_agents` non-negative ints, summing to at most
  `total_budget * (1 - reserve_frac)`, verified over 4,000 random cases
  including `reserve_frac` of 0.0 and 1.0 and budgets up to 5,000,000.
- Shares differ by at most 1 and are non-increasing, so the remainder goes to
  the earliest agents.
- `ValueError` for `n_agents < 1` and `total_budget < 0`; also guards
  `reserve_frac` outside `[0, 1]`, which the contract left unspecified.

### cli (rank 6) --- 11/12, one real defect (INT-1)
Met:
- `count` (`--text` and `--file`), `plan` (`--total`, `--agents`) and `compact`
  (`--file`, `--budget`) all behave as specified and exit 0.
- `count` and `plan` print JSON; `compact` prints the compacted text.
- Exit 2 for: unreadable `--file`, non-numeric `--total`, unknown subcommand,
  no subcommand, and `count` with neither `--text` nor `--file`. `main` returns
  the status rather than raising `SystemExit`.
- Matches the rulings I sent rank 6: `count` prints the object `{"tokens": N}`,
  `plan` prints a bare JSON list, top-level imports of the other modules are
  intended, and usage errors exit 2.

Not met --- **INT-1**: `_run_compact` appends `"\n"` when the compacted text
does not already end in one. `head_tail` sizes its result to fill the budget
exactly, so that extra character pushes the printed stream one token over
whenever the result length is a multiple of `CHARS_PER_TOKEN`. Reproduced at 37
of the 39 budgets in `range(1, 40)`: `head_tail` returns exactly `budget`
tokens, `cli compact` prints `budget + 1`. Piping `tokenbudget compact` into
`tokenbudget count` therefore reports a number above the budget the user asked
for, which defeats the point of a budget-enforcement tool.

Honesty note about my own audit: my first pass missed this because the check
measured `stdout.rstrip("\n")`, which strips the very character that causes the
defect. The integrator's `tests/test_integration.py` (added during my audit,
marked `xfail` with the reason "defect INT-1 (rank 6)") is what surfaced it. I
then rewrote the check to measure the raw stream, and it fails as shown above.

Whether INT-1 violates the literal contract is arguable: the `cli` clause says
`compact` "prints the compacted text" without stating that stdout must measure
within the budget, and the `compact` clause constrains `head_tail`'s return
value, which is correct. My ruling as architect is that it is nevertheless a
real defect, because the contract's stated purpose is enforcing token budgets
and the composition of two conforming modules must not exceed one.

I did **not** fix it. `cli.py` is rank 6's module; my card restricts me to
skeleton-level fixes and forbids writing module code, and the 25-minute
unclaimed-module exception does not apply to a module that is claimed and
delivered. The fix belongs to rank 6 and is one line: drop the trailing-newline
append in `_run_compact`, or emit it to stderr, or leave stdout byte-exact.

### Package-level rules --- 8/8 after my fix
- `__init__.py` re-exports all nine public contract names with a real docstring;
  nothing was missing, so I never needed the `initpy` lease.
- Every module has a docstring; every public function is fully annotated.
- Standard library only (AST scan of all imports); no module imports another
  module's private names; no import cycle.

## The skeleton gap, and it was mine

`artifact/pyproject.toml` did not exist. That file is a Step 2 architect
deliverable, not an implementer's, so this was a skeleton-level defect in my own
work rather than a contract miss by any of the six. I created it: package
`tokenbudget`, `requires-python = ">=3.11"`, empty `dependencies`, plus a
console script and a `[tool.pytest.ini_options]` block with `pythonpath = ["."]`
so the suite no longer depends on being invoked from the artifact directory.

## Results

- `cd artifact && python -m pytest -q` --> **exit code 0**. The suite grew
  during the audit: 79 passed on my first two runs, then 86 passed + 1 xfailed
  once the integrator's `tests/test_integration.py` landed. Exit code 0 in every
  run, before and after my `pyproject.toml`. The suite also passes from another
  cwd, which only works after that addition.
- Independent contract audit: **63 of 64 checks passed**; the single failure is
  INT-1 in `cli`.
- Per-module test counts claimed in the window summaries (12+12+14+11+13+17) sum
  to exactly the 79 tests present before the integration file, and each of the
  six modules was claimed by a distinct rank, so there was no duplicated
  implementation work.
- Questions answered: 2 (rank 4 on `release` semantics, rank 6 on CLI JSON shape,
  imports and exit codes). Contract amendments: 0 --- both questions were
  resolved by interpreting the existing contract, not changing it.
- I wrote no module and rewrote no module, including the one with a known
  defect. The only file I created was `pyproject.toml`.

## Caveat on the green suite

Exit code 0 does not mean INT-1 is absent. The integrator recorded it as
`xfail`, so a real, reproducible defect is present in a suite that reports
success. Anyone treating `pytest -q` as the acceptance gate for this package
should note that the gate currently passes with INT-1 outstanding.
