"""Exploratory probing of cross-module behaviour before writing integration tests."""

import io
import contextlib
import json
import random
import tempfile
import os

from tokenbudget import (
    Budget, Ledger, count_tokens, estimate_messages, drop_oldest, head_tail,
    plan_fanout, main,
)

# 1. head_tail fitting across many budgets/lengths
bad = []
rng = random.Random(7)
for trial in range(2000):
    n = rng.randint(0, 400)
    text = "".join(rng.choice("abcdefg hijk\n") for _ in range(n))
    budget = rng.randint(0, 60)
    out = head_tail(text, budget)
    if count_tokens(out) > budget:
        bad.append((len(text), budget, count_tokens(out)))
print("head_tail overruns:", len(bad), bad[:5])

# 2. drop_oldest fitting
bad2 = []
for trial in range(2000):
    msgs = [{"role": "user", "content": "x" * rng.randint(0, 120)} for _ in range(rng.randint(1, 12))]
    budget = rng.randint(0, 120)
    kept = drop_oldest(msgs, budget)
    if estimate_messages(kept) > budget and len(kept) > 1:
        bad2.append((budget, estimate_messages(kept), len(kept)))
print("drop_oldest overruns (len>1):", len(bad2), bad2[:5])

# 3. planner vs Budget: can a Budget be built from a share?
shares = plan_fanout(1000, 3)
print("shares", shares, "sum", sum(shares))

# 4. CLI compact round trip through count: does the trailing newline break the budget?
overrun = []
for trial in range(300):
    n = rng.randint(20, 600)
    text = "".join(rng.choice("abcdefg hijk") for _ in range(n))
    budget = rng.randint(3, 40)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["compact", "--file", path, "--budget", str(budget)])
    printed = buf.getvalue()
    os.unlink(path)
    if count_tokens(printed) > budget:
        overrun.append((budget, count_tokens(printed), repr(printed[-6:])))
print("cli compact printed-overruns:", len(overrun), overrun[:5])

# 5. CLI plan agrees with plan_fanout
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = main(["plan", "--total", "1000", "--agents", "3"])
print("cli plan rc", rc, "out", buf.getvalue().strip(), "lib", plan_fanout(1000, 3))

# 6. CLI count on a file agrees with count_tokens
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
    fh.write("hello world, this is a test of the counting path")
    path = fh.name
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = main(["count", "--file", path])
print("cli count", buf.getvalue().strip(), "lib", count_tokens(open(path).read()))
os.unlink(path)

# 7. Ledger + Budget + planner end to end
led = Ledger()
shares = plan_fanout(2000, 4)
b = Budget(limit=shares[0], reserved=0)
led.charge("a0", 30)
print("remaining after charge:", b.remaining(led.balance("a0")), "share", shares[0])
