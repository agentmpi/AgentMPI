# minidag

`minidag` is a dependency-free Python 3.11 library and command-line tool for
running a directed acyclic graph (DAG) of subprocesses. It validates the whole
graph before execution and uses lexicographic task-name ordering whenever more
than one task can run, so the scheduling policy is reproducible.

## Run the example

From the project directory:

```console
python3 -m minidag examples/build.json --jobs 2
```

The installed console script is equivalent:

```console
minidag examples/build.json --jobs 2
```

The process exits with status `0` only when every task succeeds. A malformed
graph, an invalid `--jobs` value, or any failed task produces a nonzero status.

## Graph JSON

Input is UTF-8 JSON with this shape:

```json
{
  "tasks": [
    {
      "name": "compile",
      "command": ["python3", "-c", "print('compiled')"],
      "deps": []
    },
    {
      "name": "test",
      "command": ["python3", "-c", "print('tested')"],
      "deps": ["compile"]
    }
  ]
}
```

The schema is:

- The document is an object containing `tasks`, an array of task objects.
- Each task has a non-empty string `name` and a non-empty `command` array of
  non-empty strings. The optional `deps` field is an array of non-empty
  task-name strings and defaults to `[]`.
- Task names must be unique. Every dependency must name another declared task.
- Dependencies must form a DAG; cycles are rejected before commands run.
- Unknown fields, duplicate JSON object keys, duplicate dependencies, and
  values of the wrong JSON type are rejected.

See [`examples/build.json`](examples/build.json) for an executable graph with
parallel work.

## Python API

### Define and inspect a graph

```python
from minidag.graph import Graph, Task

graph = Graph()
graph.add(
    Task(
        name="compile",
        command=("python3", "-c", "print('compiled')"),
        deps=(),
    )
)
graph.add(
    Task(
        name="test",
        command=("python3", "-c", "print('tested')"),
        deps=("compile",),
    )
)
graph.validate()

assert graph.topological_order() == ["compile", "test"]
ready = graph.ready(completed=set(), running=set())
```

`Task` is a frozen dataclass with `name: str`, `command: tuple[str, ...]`, and
`deps: tuple[str, ...]`. `Graph.add(task)` adds one task and rejects duplicate
names. `Graph.validate()` rejects unknown dependencies and cycles.
`Graph.topological_order()` returns a deterministic dependency-first
`list[str]`. `Graph.ready(completed, running)` returns a lexicographically
ordered `list[Task]` whose dependencies are complete and which are neither
complete nor currently running. The read-only `Graph.tasks` mapping and the
usual `len`, iteration, membership, and name lookup operations expose the
graph's tasks without allowing the mapping to be changed.

### Load JSON

```python
from pathlib import Path

from minidag.parser import load

graph = load(Path("examples/build.json"))
```

`load(path: Path) -> Graph` strictly parses the JSON document, constructs all
tasks, validates the completed graph, and raises a useful exception for invalid
JSON or graph structure.

### Schedule work

```python
from minidag.scheduler import Scheduler

scheduler = Scheduler(graph)
task = scheduler.claim()
if task is not None:
    scheduler.complete(task.name, success=True)
state = scheduler.snapshot()
```

`Scheduler.claim()` chooses the lexicographically first ready task.
`Scheduler.complete(name, success)` records its outcome, and
`Scheduler.snapshot()` returns a dictionary whose `pending`, `running`,
`completed`, `failed`, and `blocked` values are sorted tuples of task names. A
failed task blocks every task that depends on it, directly or transitively.

### Execute a graph

```python
from minidag.executor import RunResult, execute

result = execute(graph, jobs=2)
if not result.success:
    raise SystemExit(1)
```

`execute(graph, jobs=1) -> RunResult` validates the graph, runs at most `jobs`
subprocesses concurrently, waits for running subprocesses to finish, and
returns their aggregate result. `RunResult` is a frozen dataclass containing
sorted `completed`, `failed`, and `blocked` tuples of task names; its `success`
property is true when the latter two tuples are empty. A nonzero exit status or
failure to start a command marks its task as failed. Independent tasks are
claimed in lexicographic order, and subprocess output is inherited by the
calling process.

## Safety and deterministic behavior

- Commands are JSON arrays and are passed directly as executable plus
  arguments. `minidag` never evaluates a shell command string and does not
  enable a shell.
- Graph validation happens before subprocess execution. Duplicate tasks,
  missing dependencies, and cycles cannot result in partial graph execution.
- A task runs only after every dependency succeeds. Failure prevents dependent
  tasks from running; independent tasks may still complete.
- Ready-task tie-breaking and topological ordering are lexicographic by task
  name. With `--jobs 1`, command start order is deterministic. With parallel
  jobs, start selection remains deterministic, while completion and output
  ordering naturally depend on operating-system scheduling and subprocess
  duration.
- JSON files are data, but commands in a graph still launch programs with the
  current user's permissions. Review untrusted graph files before running
  them.
