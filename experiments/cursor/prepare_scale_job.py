#!/usr/bin/env python3
"""Bootstrap a COMM_WORLD and drop per-rank work packets for Cursor executors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentmpi.comm import Communicator
from agentmpi.transport.filesystem import FilesystemTransport
from experiments.data.build_corpus import shard as shard_fables


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    home = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "experiments/results/.ampi/cursor-scale"
    home.mkdir(parents=True, exist_ok=True)
    comm = Communicator(home, rank=0, size=n, bootstrap=True)
    comm.win_ensure("context", [])
    corpus = json.loads((ROOT / "experiments/data/aesop_fables.json").read_text())
    shards = shard_fables(corpus, n)
    work_dir = home / "work"
    work_dir.mkdir(exist_ok=True)
    transport = FilesystemTransport(home)
    for shard in shards:
        rank = shard["shard"]
        packet = {"rank": rank, "size": n, "home": str(home), "fables": shard["fables"][:2], "target": "es"}
        (work_dir / f"rank{rank}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))
        # Also post as a protocol message from rank 0 so agents can recv().
        if rank != 0:
            transport.post(0, rank, 1, packet)
    (home / "INSTRUCTIONS.md").write_text(
        f"""# Cursor rank instructions

You are one rank in an AgentMPI job. size={n}
AMPI_HOME={home}

1. Read $AMPI_HOME/work/rank$AMPI_RANK.json (or `python3 -m agentmpi recv --source 0 --tag 1`).
2. For each fable, write a faithful Spanish title and a one-sentence Spanish moral.
3. Write $AMPI_HOME/out/rank$AMPI_RANK.json
4. `python3 -m agentmpi send --dest 0 --tag 2 --file $AMPI_HOME/out/rank$AMPI_RANK.json`
5. `python3 -m agentmpi heartbeat --state finalized`

Do not modify other ranks' files.
"""
    )
    (home / "out").mkdir(exist_ok=True)
    print(f"prepared {n} ranks at {home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
