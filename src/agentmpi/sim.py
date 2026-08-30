"""In-process multi-rank driver.

Every MPI implementation ships something like this, and for the same three
reasons.  A correctness test for a collective needs *p* ranks running the
same code and comparing answers; a scaling study needs hundreds of ranks
without hundreds of machines; and a protocol claim needs to be checkable
without paying for inference.

The simulator runs each rank on a thread over a shared device, with a real
:class:`~agentmpi.runtime.Runtime` per rank.  Nothing about the protocol is
stubbed: the same matching engine, the same collectives, the same failure
detector.  Only the *executor* is replaced -- instead of an agent, each rank
runs a Python function, optionally with a synthetic think-time drawn from a
heavy-tailed distribution so that straggler behaviour is reproduced rather
than assumed away.
"""

from __future__ import annotations

import random
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from .comm import Communicator
from .constants import RankState
from .group import RankSpec
from .runtime import Runtime
from .trace import Event, Profiler
from .transport import Device, InprocDevice, JournalDevice

RankFn = Callable[[Communicator], Any]


@dataclass
class SimResult:
    results: dict[int, Any] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    wall_s: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    runtimes: dict[int, Runtime] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_errors(self) -> None:
        if self.errors:
            first = sorted(self.errors)[0]
            raise AssertionError(
                f"rank {first} failed:\n{self.errors[first]}\n"
                f"(failed ranks: {sorted(self.errors)})"
            )

    def ordered(self) -> list[Any]:
        return [self.results.get(r) for r in sorted(self.results)]


class ThinkTime:
    """Synthetic agent turn duration.

    Agent latency is not normally distributed; it has a long right tail
    driven by output length, retries, and provider queueing.  A lognormal
    body with an occasional multiplicative spike reproduces the shape well
    enough to study straggler mitigation, which is the property most of our
    scheduling claims depend on.
    """

    def __init__(self, median_s: float = 0.0, sigma: float = 0.6,
                 spike_p: float = 0.05, spike_x: float = 6.0, seed: int | None = None) -> None:
        self.median_s = median_s
        self.sigma = sigma
        self.spike_p = spike_p
        self.spike_x = spike_x
        self._rng = random.Random(seed)

    def sample(self) -> float:
        if self.median_s <= 0:
            return 0.0
        base = self.median_s * self._rng.lognormvariate(0.0, self.sigma)
        if self._rng.random() < self.spike_p:
            base *= self.spike_x
        return base

    def sleep(self) -> float:
        d = self.sample()
        if d > 0:
            time.sleep(d)
        return d


def run(
    size: int,
    fn: RankFn,
    *,
    device: Device | None = None,
    specs: Sequence[RankSpec] | None = None,
    cvars: dict[str, Any] | None = None,
    timeout: float = 120.0,
    root: str | None = None,
    kill: dict[int, float] | None = None,
) -> SimResult:
    """Run ``fn`` on ``size`` ranks concurrently.

    ``kill`` maps a rank to a delay after which its thread stops
    participating, which is how the fault-injection experiments produce a
    genuine mid-collective failure rather than a simulated one.
    """
    dev = device or (JournalDevice(root) if root else InprocDevice())
    specs = list(specs or [RankSpec(rank=i) for i in range(size)])
    result = SimResult()
    barrier_started = threading.Barrier(size)
    runtimes: dict[int, Runtime] = {}

    # Publish every rank's business card before any rank builds its world
    # communicator, so that the rank table is complete -- the same ordering
    # constraint PMIx solves with its pre-Init key-value exchange.
    for spec in specs:
        dev.kv_put(f"rank/{spec.rank}", _spec_json(spec))

    def worker(rank: int) -> None:
        rt = Runtime(dev, rank, size, spec=specs[rank], cvars=cvars, root=root)
        runtimes[rank] = rt
        rt.publish_spec()
        rt.heartbeat(force=True)
        # Ranks in the simulator are threads, so they can heartbeat while
        # they work; that is what lets the detector distinguish a long turn
        # from a dead rank.
        rt.start_heartbeat()
        try:
            barrier_started.wait(timeout=timeout)
        except threading.BrokenBarrierError:
            return
        if kill and rank in kill:
            threading.Timer(kill[rank], lambda: _die(rt)).start()
        try:
            result.results[rank] = fn(rt.world)
        except BaseException:  # noqa: BLE001 - report, do not propagate
            result.errors[rank] = traceback.format_exc()
        finally:
            try:
                rt.finalize()
            except Exception:
                pass

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(r,), name=f"ampi-rank-{r}", daemon=True)
               for r in range(size)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    result.wall_s = time.time() - t0
    result.runtimes = runtimes
    result.events = list(dev.read_journal("trace"))
    alive = [t.name for t in threads if t.is_alive()]
    if alive:
        result.errors.setdefault(-1, f"threads still running after {timeout}s: {alive}")
    return result


def _die(rt: Runtime) -> None:
    """Simulate an agent that stops without announcing anything.

    Deliberately silent: it does not finalize, does not publish a failure,
    and does not stop heartbeating gracefully.  It simply goes quiet, which
    is what a crashed agent, a revoked API key, and a killed container all
    look like from the outside, and the only thing the failure detector can
    actually observe.
    """
    rt.stop_heartbeat()
    rt.state = RankState.FAILED
    rt.heartbeat = lambda force=False: None  # type: ignore[assignment]
    rt.start_heartbeat = lambda period_s=None: None  # type: ignore[assignment]
    rt.device.kv_delete(f"hb/{rt.world_rank}")


def _spec_json(spec: RankSpec) -> str:
    import json

    return json.dumps(asdict(spec))


def run_scripted(
    size: int,
    script: Callable[[Communicator, int], Any],
    *,
    turns: int = 1,
    think: ThinkTime | None = None,
    **kw: Any,
) -> SimResult:
    """Run a multi-turn scripted agent on every rank."""
    think = think or ThinkTime()

    def body(comm: Communicator) -> Any:
        out = None
        for turn in range(turns):
            with comm.runtime.profiler.region("turn", context=comm.context):
                think.sleep()
                out = script(comm, turn)
            comm.runtime.note_progress()
        return out

    return run(size, body, **kw)
