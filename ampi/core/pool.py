"""Work pools: claim, dependency, reclaim, termination (spec S9.5).

A bag of tasks is the oldest parallel pattern and every program that builds one
on shared state gets one of four things wrong: two workers take the same item, a
dead worker keeps its item forever, an item runs before its inputs exist, or
nobody can say when the whole thing is finished.  Each is a general obligation
with nothing of the application in it, so the pool is protocol: the harness says
what an item is and the runtime says who holds it, whether its inputs are done,
and when there is nothing left.

The pool is a window with three prefixes.  ``item/<id>`` holds an item added
after creation (the seeds every member passes to ``pool_create`` are not
written, as a communicator's group is not).  ``claim/<id>`` is the claim record,
taken by compare-and-swap from absence, so no cell is posted before the
population may claim.  ``done/<id>`` is the result cell.  A claim held by a rank
the failure detector has convicted, or by an earlier epoch of its holder, is
taken over by compare-and-swap on the record; the take-over is traced as a
reclaim, which is how the trace distinguishes work that was redone from work
that was duplicated.

Every step is one read of the key list and one conditional write.  The key list
is read from the device directly and uncharged: which items exist is the
runtime's bookkeeping, not something a model reads.
"""

from __future__ import annotations

import time
from typing import Any

from ..constants import DEFAULT_TIMEOUT_S, STATE_FAILED, STATE_FENCED
from ..errors import err

__all__ = ["PoolMixin", "normalise_item"]

_POLL_S = 1.0
_POLL_MAX_S = 8.0


def normalise_item(item: dict[str, Any] | str) -> dict[str, Any]:
    """An item is a dict with an ``id``; everything else has a default."""
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict) or not item.get("id"):
        raise err("AMPI_ERR_ARG", "a pool item needs an 'id'", item=str(item)[:80])
    if "/" in str(item["id"]):
        raise err("AMPI_ERR_ARG", "a pool item id may not contain '/'", item=str(item["id"]))
    return {
        "id": str(item["id"]),
        "deps": [str(d) for d in (item.get("deps") or [])],
        "priority": int(item.get("priority", 0)),
        "group": str(item.get("group", "")),
        "payload": item.get("payload"),
    }


class PoolMixin:
    # -- lifecycle -----------------------------------------------------------
    def _pool_space(self, name: str, comm: str) -> str:
        return self._require_win(f"pool:{name}", comm)

    def pool_create(self, name: str, items: list[dict[str, Any] | str] | None = None, *,
                    comm: str = "world") -> dict[str, Any]:
        """Open a pool, seeding it with the items every member passes identically.

        Seeds are held locally, so opening a pool of a hundred items costs one
        conditional write for the window and none for the items.  A member that
        seeds a different list has written a different program, as with a
        communicator whose members disagree about the group.
        """
        self.assert_identity()
        seeds = [normalise_item(i) for i in (items or [])]
        ids = [s["id"] for s in seeds]
        if len(set(ids)) != len(ids):
            raise err("AMPI_ERR_ARG", "pool seeds must have distinct ids", pool=name)
        created = self.win_create(f"pool:{name}", comm=comm)["created"]
        # The seeds are written once, by whichever member gets there first, and
        # only so that a rank with no memory of them --- a successor, or the
        # command binding's fresh process --- can read them back.  Every member
        # passes the same list, so it does not matter whose write lands.
        if seeds:
            self.compare_and_swap(f"pool:{name}", "seeds", None, {"comm": comm, "items": seeds},
                                  comm=comm)
        pools = self.__dict__.setdefault("_pools", {})
        pools[name] = {"comm": comm, "seeds": {s["id"]: s for s in seeds}}
        self.trace("pool.create", rank=self.rank, pool=name, seeds=len(seeds), created=created)
        return {"pool": name, "seeds": len(seeds), "created": created}

    def _pool(self, name: str) -> dict[str, Any]:
        pools = self.__dict__.setdefault("_pools", {})
        if name not in pools:
            # Not opened in this process: read the seeds the population wrote.
            try:
                space = self._require_win(f"pool:{name}", "world")
            except Exception as exc:
                raise err("AMPI_ERR_ARG", f"pool {name!r} is not open",
                          hint="Call pool_create with the same seeds every member passes.",
                          pool=name) from exc
            cell = self.device.read(space, "seeds")
            items = (cell.value or {}).get("items", []) if cell is not None else []
            pools[name] = {"comm": "world", "seeds": {s["id"]: s for s in items}}
        return pools[name]

    def pool_add(self, name: str, item: dict[str, Any] | str) -> dict[str, Any]:
        """Add an item after creation.  Idempotent: two members adding the same
        id write one cell, so a seam both of its neighbours propose exists once."""
        self.assert_identity()
        pool = self._pool(name)
        spec = normalise_item(item)
        if spec["id"] in pool["seeds"]:
            return {"pool": name, "id": spec["id"], "added": False, "reason": "seeded"}
        got = self.compare_and_swap(f"pool:{name}", f"item/{spec['id']}", None, spec,
                                    comm=pool["comm"])
        self.trace("pool.add", rank=self.rank, pool=name, item=spec["id"], added=got["swapped"])
        return {"pool": name, "id": spec["id"], "added": got["swapped"]}

    # -- the view ------------------------------------------------------------
    def _pool_view(self, name: str) -> dict[str, Any]:
        pool = self._pool(name)
        space = self._pool_space(name, pool["comm"])
        items = dict(pool["seeds"])
        for c in self.device.keys(space, prefix="item/"):
            cell = self.device.read(space, c.key)
            if cell is not None and isinstance(cell.value, dict) and cell.value.get("id"):
                items.setdefault(cell.value["id"], cell.value)
        done = {c.key[len("done/"):] for c in self.device.keys(space, prefix="done/")}
        claims: dict[str, Any] = {}
        for c in self.device.keys(space, prefix="claim/"):
            cell = self.device.read(space, c.key)
            if cell is not None and isinstance(cell.value, dict) and not cell.value.get("released"):
                claims[c.key[len("claim/"):]] = cell.value
        return {"space": space, "items": items, "done": done, "claims": claims}

    def _holder_is_gone(self, claim: dict[str, Any]) -> bool:
        """A claim whose holder the detector convicted, or a stale epoch of a
        holder that came back, no longer protects the item."""
        holder = claim.get("claimed_by")
        if holder is None:
            return True
        try:
            me = self.rank
        except Exception:  # noqa: BLE001 - a rank-less observer (the driver) is nobody's holder
            me = None
        if me is not None and int(holder) == me and \
                int(claim.get("epoch", 0)) == int(self._rankview().epoch):
            return False
        try:
            view = self._rankview(int(holder))
        except Exception:  # noqa: BLE001 - no row: never joined, so never alive
            return True
        if view.state in (STATE_FAILED, STATE_FENCED):
            return True
        return int(view.epoch) > int(claim.get("epoch", 0))

    def pool_status(self, name: str) -> dict[str, Any]:
        v = self._pool_view(name)
        open_ = [i for i in v["items"] if i not in v["done"]]
        claimed = [i for i in open_ if i in v["claims"] and not self._holder_is_gone(v["claims"][i])]
        ready = [i for i in open_ if i not in claimed
                 and all(d in v["done"] for d in v["items"][i]["deps"])]
        return {"pool": name, "items": len(v["items"]), "done": len(v["done"]),
                "open": len(open_), "claimed": len(claimed), "ready": len(ready),
                "drained": not open_, "ready_ids": ready}

    # -- claiming ------------------------------------------------------------
    def pool_next(self, name: str, *, prefer: str = "", wait: bool = False,
                  timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        """Claim the next available item, or report why there is none.

        Available: not done, not held by a live holder, every dependency done.
        Order: priority, then the caller's preferred group, then id.  With
        ``wait`` the call blocks, discharging a blocked rank's obligations, until
        an item is available or the pool is drained; the time spent is traced as
        ``pool.wait`` because it is the idle time the pool exists to remove.
        """
        self.assert_identity()
        pool = self._pool(name)
        started = time.time()
        deadline = started + timeout
        sleep = _POLL_S
        while True:
            v = self._pool_view(name)
            open_ = [v["items"][i] for i in v["items"] if i not in v["done"]]
            if not open_:
                self._trace_wait(name, started, drained=True)
                return {"pool": name, "item": None, "drained": True, "open": 0}
            # An item this rank already holds and has not finished is its next
            # item: a rank re-entering its loop (a resumed process, a replayed
            # program) continues what it claimed rather than leaving it claimed
            # forever and taking another.
            for spec in open_:
                claim = v["claims"].get(spec["id"])
                if claim is not None and int(claim.get("claimed_by", -1)) == self.rank:
                    self.trace("pool.resume", rank=self.rank, pool=name, item=spec["id"],
                               epoch=claim.get("epoch"))
                    return {"pool": name, "item": spec, "reclaimed": False, "resumed": True,
                            "waited_s": 0.0}
            ready = [s for s in open_ if all(d in v["done"] for d in s["deps"])]
            ready.sort(key=lambda s: (s["priority"], 0 if prefer and s["group"] == prefer else 1,
                                      s["id"]))
            for spec in ready:
                claim = v["claims"].get(spec["id"])
                record = {"claimed_by": self.rank, "epoch": int(self._rankview().epoch),
                          "at": self.device.clock()}
                if claim is None:
                    got = self.compare_and_swap(f"pool:{name}", f"claim/{spec['id']}", None,
                                                record, comm=pool["comm"])
                    reclaimed = False
                elif self._holder_is_gone(claim):
                    got = self.compare_and_swap(f"pool:{name}", f"claim/{spec['id']}", claim,
                                                record, comm=pool["comm"])
                    reclaimed = True
                else:
                    continue
                if got["swapped"]:
                    waited = self._trace_wait(name, started)
                    if reclaimed:
                        self.trace("pool.reclaim", rank=self.rank, pool=name, item=spec["id"],
                                   from_rank=claim.get("claimed_by"), from_epoch=claim.get("epoch"))
                    self.trace("pool.claim", rank=self.rank, pool=name, item=spec["id"],
                               group=spec["group"], preferred=bool(prefer and spec["group"] == prefer),
                               reclaimed=reclaimed, waited_s=round(waited, 3))
                    return {"pool": name, "item": spec, "reclaimed": reclaimed,
                            "waited_s": round(waited, 3)}
            if not wait:
                return {"pool": name, "item": None, "drained": False, "open": len(open_),
                        "ready": len(ready)}
            if time.time() >= deadline:
                raise err("AMPI_ERR_TIMEOUT",
                          f"pool {name!r}: nothing claimable for {timeout:.0f}s "
                          f"({len(open_)} open, {len(ready)} ready)",
                          hint="Re-issue to keep waiting, or run 'ampi doctor'.", pool=name)
            self.touch()
            self.detect_failures()
            time.sleep(sleep)
            sleep = min(_POLL_MAX_S, sleep * 1.6)

    def _trace_wait(self, name: str, started: float, *, drained: bool = False) -> float:
        waited = time.time() - started
        if waited >= _POLL_S:
            self.trace("pool.wait", rank=self.rank, pool=name, waited_s=round(waited, 3),
                       drained=drained)
        return waited

    def pool_done(self, name: str, item: str, result: Any = None) -> dict[str, Any]:
        """Mark an item done.  The result is the harness's; a handle is the usual
        thing to leave here, the work itself belongs in a window of its own."""
        self.assert_identity()
        pool = self._pool(name)
        value = {"rank": self.rank, "epoch": int(self._rankview().epoch),
                 "at": self.device.clock(), "result": result}
        out = self.put(f"pool:{name}", f"done/{item}", value, comm=pool["comm"])
        self.trace("pool.done", rank=self.rank, pool=name, item=item)
        return {"pool": name, "id": item, "version": out.get("version")}

    def pool_release(self, name: str, item: str, *, reason: str = "") -> dict[str, Any]:
        """Give an item back unclaimed: the holder cannot do it."""
        self.assert_identity()
        pool = self._pool(name)
        out = self.put(f"pool:{name}", f"claim/{item}",
                       {"released": True, "by": self.rank, "reason": reason,
                        "at": self.device.clock()}, comm=pool["comm"])
        self.trace("pool.release", rank=self.rank, pool=name, item=item, reason=reason[:80])
        return {"pool": name, "id": item, "version": out.get("version")}

    def pool_wait_drained(self, name: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        """Block until every known item is done: the pool's termination condition."""
        self.assert_identity()
        started = time.time()
        self._await(lambda: self.pool_status(name)["drained"], timeout=timeout,
                    what=f"pool {name!r} to drain")
        status = self.pool_status(name)
        self.trace("pool.drained", rank=self.rank, pool=name, items=status["items"],
                   waited_s=round(time.time() - started, 3))
        return status
