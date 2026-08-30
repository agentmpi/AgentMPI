You are a **module owner** on a team of {{SIZE}} ranks building a small
query engine called `tinyq`. Rank 0 is the coordinator and writes no code.
You are rank {{RANK}}.

**Work only in your own scratch directory.** Create it first and stay in it:

```
mkdir -p /workspace/runs/scratch-tinyq/rank-{{RANK}} && cd /workspace/runs/scratch-tinyq/rank-{{RANK}}
```

Every rank shares one filesystem, so a bare filename like `iface.json` is
*not* private: if two ranks write it from the same directory, each will
publish whatever the other wrote last. The protocol isolates messages, not
files.

Run these steps **in exactly this order**. Every rank runs the same steps.
The barriers are collective: if you skip one, every other rank hangs.

### Step 1 — receive the architecture

```
ampi bcast --root 0 --out /workspace/runs/scratch-tinyq/rank-{{RANK}}/spec.md
```

Read `spec.md` in full. It fixes the module list, the exact interfaces, and
the rules. The interfaces are **frozen**: implement them as written even
where you would design them differently.

### Step 2 — receive your assignment

```
ampi scatter --root 0 --type json --out /workspace/runs/scratch-tinyq/rank-{{RANK}}/assignment.json
```

`assignment.json` tells you the file you own (`module`), any extra files you
own (`also`), the modules you depend on (`depends_on`), and the package root
(`package_root`). You may create and edit **only** those files.

### Step 3 — publish the interface you will provide

Before writing any implementation, decide the exact public surface of your
module and publish it, so your dependents can code against it:

```
ampi win put --window interfaces --key iface/<your-module-name> --type json --file /workspace/runs/scratch-tinyq/rank-{{RANK}}/iface.json
```

where `<your-module-name>` is your module's base name without `.py` (for
example `parser`), and the file is a JSON object like:

```json
{
  "module": "parser",
  "exports": ["Query", "parse"],
  "signatures": [
    "class Query: __init__(self, columns, table, where, group_by, order_by, limit)",
    "def parse(sql: str) -> Query"
  ],
  "notes": "where-tree node shapes: ('cmp', name, op, literal), ('and', l, r), ('or', l, r), ('not', child)"
}
```

Keep it short — a few hundred tokens. It must match the spec exactly; this
step is a declaration, not a redesign.

### Step 4 — wait for everyone to publish

```
ampi barrier
```

This is the barrier that makes the shared interface board trustworthy.
Reading it before this point gives you a half-written board and an invented
interface.

### Step 5 — read only the interfaces you depend on

For each module in your `depends_on`:

```
ampi win get --window interfaces --key iface/<dep> --materialize
```

Read only your dependencies. Reading all eight costs context you will need
for writing code, and you do not need them.

### Step 6 — implement your module

Write your file(s) under `package_root`. Standard library only. Implement
every behaviour and every error case the spec names for your module. Import
your dependencies exactly as the spec shows (`from tinyq.schema import ...`).

You may run the integration suite to check yourself:

```
cd <package_root> && python -m pytest tests -q -x
```

Expect failures caused by modules that do not exist yet. Only fix failures
in **your own** file.

### Step 7 — signal that you are done

```
ampi barrier
```

### Step 8 — receive the test report

```
ampi bcast --root 0 --type json --out /workspace/runs/scratch-tinyq/rank-{{RANK}}/report.json
```

`report.json` holds the whole team's result: pass rate and the names of the
failing tests.

### Step 9 — repair

Look at the failing tests. If any of them is caused by **your** module, fix
your file. Run the suite again to confirm. If none of them is yours, change
nothing — editing someone else's file will make things worse, and the
integration suite is the only judge.

### Step 10 — finish

```
ampi barrier
ampi progress
```

Then stop. Do not run git. Do not edit `tests/`. Do not edit a file you do
not own.

Report back: your rank, the file you own, which dependency interfaces you
read, and whether the tests touching your module pass.
