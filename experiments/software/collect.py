"""Collect the collaborative software experiment and verify its artifact."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from prepare import SESSION

from agentmpi import Runtime


def command_result(command: list[str], cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return {
        "command": command,
        "returncode": result.returncode,
        "elapsed_seconds": time.monotonic() - started,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("experiments/results/software.db"))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("experiments/results/software_artifact"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/software_output.json"),
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("experiments/results/software_metrics.json"),
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("experiments/results/software_trace.json"),
    )
    parser.add_argument("--timeout", type=float, default=1_200)
    args = parser.parse_args()

    runtime = Runtime(args.db, SESSION, 0)
    final = runtime.recv(source=12, tag="FINAL", timeout=args.timeout).payload
    trace = runtime.trace()
    runtime.finalize()
    runtime.close()

    unit = command_result(
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
        args.workspace,
    )
    compile_result = command_result(["python3", "-m", "compileall", "-q", "."], args.workspace)
    sends = [event for event in trace if event["kind"] == "message.send"]
    receives = [event for event in trace if event["kind"] == "message.recv"]
    locks = [event for event in trace if event["kind"].startswith("lock.")]
    participating_ranks = sorted(
        {
            event["rank"]
            for event in trace
            if event["kind"] in {"agent.join", "message.send", "message.recv"}
        }
    )
    files = sorted(
        str(path.relative_to(args.workspace))
        for path in args.workspace.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    metrics = {
        "unit_tests_passed": unit["returncode"] == 0,
        "compile_passed": compile_result["returncode"] == 0,
        "participating_ranks": participating_ranks,
        "participating_rank_count": len(participating_ranks),
        "message_sends": len(sends),
        "message_receives": len(receives),
        "lock_events": len(locks),
        "trace_event_count": len(trace),
        "artifact_files": files,
        "integrator_report": final,
        "verification": {
            "unittest": unit,
            "compileall": compile_result,
        },
    }
    for path, value in (
        (args.output, final),
        (args.metrics, metrics),
        (args.trace, {"generated_at": time.time(), "events": trace}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
