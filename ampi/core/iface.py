"""Interface declaration, discovery, and verification.

This chapter exists because of a controlled comparison whose result was, to us,
the most informative thing any of the experiments produced.

Eight agents were given the protocol's messages and windows but *no* prescribed
coordination phase, and asked to build a language interpreter together.  Beginning
about thirteen minutes in, and within roughly ninety seconds of one another, seven
of the eight independently sent an identical interface-declaration message to every
peer --- a hand-rolled allgather.  The integrator then published an agreed
interface into a shared cell and broadcast integration status twice.  Every
consumer additionally built *runtime discovery*: probing a producer's exported
handlers with synthetic inputs and settling the calling convention by majority vote
over the results, resolving class names by searching several plausible spellings
across two peers' modules, and validating a candidate environment frame by binding
a probe into it.  The shipped source carried roughly five times as many probe- and
detection-related identifiers as the arm that had a negotiated contract, and about
twice the total lines for the same externally graded behaviour.  Two of the eight
introduced defects *inside* their own detection logic.

A protocol whose users independently reimplement a mechanism, at double the cost
and with defects in the reimplementation, is a protocol missing that mechanism.

The two arms also showed that the halves are complementary rather than
alternatives.  *Declaration* alone reproduces the failure of the reduction chapter:
a single agreed artefact can be internally inconsistent, because the agreement was
reached by local merges.  *Verification* alone cannot be inconsistent but pushes
the whole cost into every consumer, which is what the ablated arm paid.  So both
are provided, and the verification result is *published* so that the ``n``-th
consumer pays for a probe the first consumer already ran.

The design deliberately stops short of a type system.  AgentMPI does not interpret
a declaration --- it is a string with a name, a provider, and a shape --- because
interpreting it would make the protocol have opinions about languages.  What the
protocol contributes is that the declaration has a *place*, an owner, a version,
and a mechanically checkable record of whether anybody confirmed it.
"""

from __future__ import annotations

from typing import Any

from ..constants import DEFAULT_TIMEOUT_S
from ..errors import err
from ..tokens import count_tokens
from .payload import canonical, digest_of, summarise

__all__ = ["IfaceMixin"]

IFACE_WIN = "_ampi_iface"


class IfaceMixin:
    def _iface_space(self, comm: str) -> str:
        return f"iface/{self.comm_context(comm)}"

    # -- declaration -----------------------------------------------------------
    def iface_publish(
        self,
        name: str,
        declaration: Any,
        *,
        comm: str = "world",
        version: str = "1",
        supersedes: str = "",
    ) -> dict[str, Any]:
        """Publish a typed interface, keyed by its provider.

        Keyed by provider, not by name, so that two ranks claiming the same
        interface name is a visible fact rather than a last-writer-wins race.  The
        thing that goes wrong without this is not that nobody declares an
        interface --- they all do, seven of eight within ninety seconds --- it is
        that there is nowhere for the declaration to live, so it is broadcast as a
        message, read once, and then unavailable to the rank that arrives late.
        """
        self.assert_identity()
        self._fence_check()
        space = self._iface_space(comm)
        key = f"{self.rank}/{name}"
        body = {
            "provider": self.rank,
            "name": name,
            "version": version,
            "declaration": declaration,
            "digest": digest_of(declaration),
            "supersedes": supersedes,
            "published_at": self.device.clock(),
        }
        _, cell = self.device.cas(
            space, key, None, body, writer=self.rank, epoch=self._rankview().epoch,
            meta={"tokens": count_tokens(canonical(declaration)),
                  "summary": summarise(declaration), "name": name},
        )
        self.device.append(
            "iface",
            {"provider": self.rank, "name": name, "state": "published",
             "run": self.manifest.job_id, "version": version, "digest": body["digest"]},
        )
        self.trace("iface.publish", rank=self.rank, name=name, version=version,
                   digest=body["digest"], comm=comm)
        return {"name": name, "provider": self.rank, "version": version,
                "digest": body["digest"], "cell_version": cell.version}

    def iface_list(self, *, comm: str = "world", name: str = "") -> dict[str, Any]:
        """What exists, and who claims it, without reading any declaration.

        The enumeration is deliberately cheap: a consumer needs to know *whether*
        an interface exists and who provides it before deciding to spend context on
        the text.  This is the same argument as ``win ls`` and the same argument as
        the rendezvous envelope.
        """
        self.assert_identity()
        space = self._iface_space(comm)
        cells = self.device.keys(space)
        items = []
        for c in cells:
            if name and not c.key.endswith(f"/{name}"):
                continue
            provider, _, iname = c.key.partition("/")
            items.append({
                "provider": int(provider),
                "name": iname,
                "version": c.version,
                "tokens": c.meta.get("tokens", 0),
                "summary": c.meta.get("summary", ""),
                "verified_by": self._verifications(comm, int(provider), iname),
            })
        contested = _contested(items)
        charged, _ = self.charge(min(40 * len(items), 1200), what="iface.list")
        out = {"interfaces": sorted(items, key=lambda i: (i["name"], i["provider"])),
               "charged": charged}
        if contested:
            out["contested"] = contested
            out["note"] = (
                "More than one rank claims these interface names. Nothing is wrong yet, but "
                "consumers must not assume there is one provider."
            )
        return out

    def iface_get(
        self, provider: int, name: str, *, comm: str = "world", view: str = ""
    ) -> dict[str, Any]:
        self.assert_identity()
        cell = self.device.read(self._iface_space(comm), f"{provider}/{name}")
        if cell is None:
            raise err(
                "AMPI_ERR_ARG",
                f"rank {provider} has not published an interface named {name!r}",
                hint="Run 'ampi iface list' to see what exists.",
            )
        from .payload import apply_view

        decl = cell.value["declaration"]
        if view:
            decl = apply_view(decl, view)
        charged, degraded = self.charge(count_tokens(canonical(decl)), what="iface.get")
        return {
            **{k: v for k, v in cell.value.items() if k != "declaration"},
            "declaration": apply_view(decl, degraded) if degraded else decl,
            "charged": charged,
            "verified_by": self._verifications(comm, provider, name),
        }

    def iface_wait(
        self,
        name: str,
        *,
        comm: str = "world",
        providers: int = 1,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Block until ``providers`` ranks have published ``name``.

        The operation that replaces "message everybody and hope".  A late-arriving
        consumer gets the same answer as an early one, which a broadcast cannot
        give it.
        """
        self.assert_identity()
        self._await(
            lambda: len(self.device.keys(self._iface_space(comm)))
            and sum(1 for c in self.device.keys(self._iface_space(comm))
                    if c.key.endswith(f"/{name}")) >= providers,
            timeout=timeout,
            what=f"{providers} provider(s) of interface {name!r}",
        )
        return self.iface_list(comm=comm, name=name)

    # -- verification -----------------------------------------------------------
    def iface_verify(
        self,
        provider: int,
        name: str,
        *,
        comm: str = "world",
        holds: bool,
        evidence: str = "",
        probe: Any = None,
    ) -> dict[str, Any]:
        """Record that a consumer checked a declaration against actual behaviour.

        Declaration alone is not enough: the agreed artefact can be internally
        inconsistent, because agreement was reached by local merges.  Verification
        alone is not enough either: it pushes the whole cost into every consumer,
        which is what the ablated arm paid, at five times the detection code and
        twice the lines.  Publishing the *result* of a verification is what makes
        the pair cheaper than either --- the second consumer reads an answer
        instead of running a probe.
        """
        self.assert_identity()
        space = self._iface_space(comm)
        if self.device.read(space, f"{provider}/{name}") is None:
            raise err("AMPI_ERR_ARG", f"no interface {name!r} from rank {provider} to verify")
        key = f"verify/{provider}/{name}/{self.rank}"
        self.device.cas(
            space, key, None,
            {"verifier": self.rank, "provider": provider, "name": name, "holds": holds,
             "evidence": evidence, "probe": probe, "at": self.device.clock()},
            writer=self.rank,
        )
        self.device.append(
            "iface",
            {"provider": provider, "name": name, "state": "verified" if holds else "refuted",
             "run": self.manifest.job_id, "verifier": self.rank},
        )
        self.trace("iface.verify", rank=self.rank, provider=provider, name=name, holds=holds)
        return {"provider": provider, "name": name, "holds": holds, "verifier": self.rank}

    def _verifications(self, comm: str, provider: int, name: str) -> list[dict[str, Any]]:
        space = self._iface_space(comm)
        out = []
        for c in self.device.keys(space, prefix=f"verify/{provider}/{name}/"):
            cell = self.device.read(space, c.key)
            if cell is not None:
                out.append({"verifier": cell.value["verifier"], "holds": cell.value["holds"],
                            "evidence": cell.value.get("evidence", "")[:120]})
        return out

    def iface_report(self, *, comm: str = "world") -> dict[str, Any]:
        """A whole-job view: who declared what, who checked it, and what disagrees.

        The report exists so that the *harness*, not each consumer, can notice the
        two structural problems: a declaration nobody verified, and an interface
        two ranks claim differently.
        """
        listing = self.iface_list(comm=comm)
        unverified = [i for i in listing["interfaces"] if not i["verified_by"]]
        refuted = [
            {**i, "refuted_by": [v["verifier"] for v in i["verified_by"] if not v["holds"]]}
            for i in listing["interfaces"]
            if any(not v["holds"] for v in i["verified_by"])
        ]
        return {
            "declared": len(listing["interfaces"]),
            "contested": listing.get("contested", []),
            "unverified": [{"provider": i["provider"], "name": i["name"]} for i in unverified],
            "refuted": refuted,
            "healthy": not unverified and not refuted and not listing.get("contested"),
        }


def _contested(items: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, set[int]] = {}
    for i in items:
        seen.setdefault(i["name"], set()).add(i["provider"])
    return sorted(name for name, providers in seen.items() if len(providers) > 1)
