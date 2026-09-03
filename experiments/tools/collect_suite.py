"""Record the size of the test suites and the implementation, from the artifacts.

Read from the suite itself rather than typed into the paper, because a claim about
how much is tested is exactly the kind of number that rots.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def collected(target: str) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", target, "--collect-only", "-q"],
        capture_output=True, text=True, cwd=ROOT,
    ).stdout
    for line in reversed(out.strip().splitlines()):
        if "test" in line and ("passed" in line or "collected" in line):
            for tok in line.replace("/", " ").split():
                if tok.isdigit():
                    return int(tok)
    return sum(1 for line in out.splitlines() if "::" in line)


def lines(*globs: str) -> int:
    total = 0
    for g in globs:
        for p in ROOT.glob(g):
            if "__pycache__" in str(p):
                continue
            total += len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
    return total


def main() -> None:
    from ampi.device import available_devices

    data = {
        "nProtocolTests": collected("conformance/test_protocol.py"),
        "nDeviceTests": collected("conformance/test_device.py"),
        "nUnitTests": collected("tests"),
        "nDevices": len(available_devices()),
        "implLines": lines("ampi/*.py", "ampi/core/*.py", "ampi/device/*.py"),
        "confLines": lines("conformance/*.py"),
        "testLines": lines("tests/*.py"),
        "specLines": lines("spec/*.md"),
    }
    data["nTests"] = data["nProtocolTests"] + data["nDeviceTests"] + data["nUnitTests"]
    out = ROOT / "experiments" / "results" / "suite.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
