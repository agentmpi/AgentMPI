"""What a fleet costs, from a price table rather than from an assertion.

The paper's rule is that every number is generated from data.  A cost estimate
is a number, so it is generated too: :mod:`prices.json` holds the list prices
with the date they were read, this module does the arithmetic, and every figure
it prints carries that date.  A stale table is then visible in the output rather
than hidden in it.

    python -m experiments.aws_production.costs --nodes 32 --hours 4
    python -m experiments.aws_production.costs --nodes 32 --hours 4 --spot --json
    python -m experiments.aws_production.costs --compare            # the sizing table
    python -m experiments.aws_production.costs --refresh            # re-read AWS

What the model counts, and it is worth knowing which of these surprised us at
thirty-two nodes:

* **Instance hours.**  The obvious one, and at this size not the largest.
* **Public IPv4 addresses.**  Charged per address per hour since February 2024.
  Thirty-three of them cost more per hour than thirty-two ``t4g.small``
  instances do.  The alternative --- a private subnet behind one NAT gateway ---
  is cheaper per hour but only by cents, and costs a good deal more setup; the
  model prices both so the choice is made on arithmetic (see ``--egress``).
* **EBS.**  A 20 GB root volume per instance is billed by the GB-month whether
  the instance is running or not, so a fleet left stopped rather than terminated
  keeps costing.  This is the line that catches people.
* **Data transfer.**  Between instances in *one availability zone* over private
  addresses it is free, which is why the runbook puts the whole fleet in one AZ;
  across AZs it is a cent a gigabyte each way.  Egress to the model provider is
  charged at the internet rate, but a run's egress is prompts, which are small.

What the model does not count, because AgentMPI cannot know it: the model
provider's bill.  That dominates.  E7's 256-rank run over four machines spent
$23.38 with the models; the arithmetic below says the machines under it cost a
few dollars.  Pass ``--api-spend`` to put the two side by side, which is the
comparison that decides whether any of this matters.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRICES = Path(__file__).with_name("prices.json")
HOURS_PER_MONTH = 730.0

#: How many rank processes a node of each size can host.  A raw-API rank is a
#: Python process that spends nearly all of its life blocked on an HTTPS call,
#: so what bounds the count is memory, not CPU --- the same thing that bounded
#: Claude Code sessions on a sandbox VM, at a tenth of the footprint.  Measured
#: at about 70 MB resident per rank with the runtime loaded; these leave room
#: for the interpreter, the page cache and a margin.
RANKS_PER_NODE = {
    "t4g.nano": 2, "t4g.micro": 4, "t4g.small": 8, "t4g.medium": 16, "t4g.large": 32,
    "t3.micro": 4, "t3.small": 8, "t3.medium": 16,
    "c7g.medium": 8, "c7g.large": 16, "c7g.xlarge": 32,
    "m7g.medium": 16, "m7g.large": 32,
    "c6g.large": 16, "m6g.large": 32,
}

MEMORY_GIB = {
    "t4g.nano": 0.5, "t4g.micro": 1, "t4g.small": 2, "t4g.medium": 4, "t4g.large": 8,
    "t3.micro": 1, "t3.small": 2, "t3.medium": 4,
    "c7g.medium": 2, "c7g.large": 4, "c7g.xlarge": 8,
    "m7g.medium": 4, "m7g.large": 8,
    "c6g.large": 4, "m6g.large": 8,
}


class PriceError(RuntimeError):
    """The table cannot price what was asked for."""


def load_prices(path: Path = PRICES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Plan:
    """A fleet, as the operator would describe it at the shell."""

    nodes: int = 32
    worker: str = "t4g.small"
    hub: str = "c7g.large"
    hours: float = 4.0
    region: str = "us-east-1"
    disk_gb: int = 20
    spot: bool = False
    egress: str = "public-ip"     # or "nat"
    egress_gb: float = 5.0
    api_spend: float = 0.0
    ranks_per_node: int | None = None

    @property
    def instances(self) -> int:
        """Workers plus the hub.  The hub runs no ranks: it is the job's state,
        and a machine that is also translating a book is a machine whose state
        server is competing with it for a core."""
        return self.nodes + 1

    @property
    def size(self) -> int:
        per = self.ranks_per_node or RANKS_PER_NODE.get(self.worker, 8)
        return self.nodes * per


@dataclass
class Estimate:
    plan: Plan
    as_of: str
    lines: list[tuple[str, float, str]] = field(default_factory=list)

    def add(self, label: str, amount: float, note: str = "") -> None:
        self.lines.append((label, amount, note))

    @property
    def total(self) -> float:
        return sum(a for _, a, _ in self.lines)

    @property
    def per_hour(self) -> float:
        return self.total / self.plan.hours if self.plan.hours else 0.0

    def to_dict(self) -> dict[str, Any]:
        p = self.plan
        return {
            "as_of": self.as_of,
            "plan": {"nodes": p.nodes, "worker": p.worker, "hub": p.hub, "hours": p.hours,
                     "region": p.region, "spot": p.spot, "egress": p.egress,
                     "instances": p.instances, "ranks": p.size,
                     "ranks_per_node": p.ranks_per_node or RANKS_PER_NODE.get(p.worker, 8)},
            "lines": [{"item": k, "usd": round(v, 4), "note": n} for k, v, n in self.lines],
            "infrastructure_usd": round(self.total, 2),
            "infrastructure_usd_per_hour": round(self.per_hour, 4),
            "api_spend_usd": round(p.api_spend, 2),
            "total_usd": round(self.total + p.api_spend, 2),
            "infrastructure_share": (round(self.total / (self.total + p.api_spend), 4)
                                     if p.api_spend else None),
        }


def estimate(plan: Plan, prices: dict[str, Any] | None = None) -> Estimate:
    """Price one plan.  Every line is an arithmetic statement about the table."""
    pr = prices or load_prices()
    if plan.region not in pr["regions"]:
        raise PriceError(f"no prices for region {plan.region!r}; "
                         f"have {', '.join(sorted(pr['regions']))}")
    reg = pr["regions"][plan.region]
    rates = reg["instance_hour"]
    for role, itype in (("worker", plan.worker), ("hub", plan.hub)):
        if itype not in rates:
            raise PriceError(f"no price for {role} instance {itype!r} in {plan.region}; "
                             f"have {', '.join(sorted(rates))}")

    est = Estimate(plan, pr["as_of"])
    h = plan.hours
    discount = float(pr.get("spot_discount", 0.0))
    factor = (1.0 - discount) if plan.spot else 1.0

    # Workers.  Spot is safe for these and only these: ampirun --respawn brings
    # a rank back, and a whole node that vanishes comes back with `rejoin`,
    # which is the recovery the frozen 128-rank run already used.
    worker_rate = rates[plan.worker] * factor
    est.add(f"{plan.nodes} x {plan.worker} worker" + (" (spot, est.)" if plan.spot else ""),
            plan.nodes * worker_rate * h,
            f"${worker_rate:.4f}/hr x {plan.nodes} x {h:g}h")

    # The hub is on-demand whatever the workers are: it holds the job's state,
    # and a spot reclamation of the hub is the end of the run, not a respawn.
    est.add(f"1 x {plan.hub} hub (on-demand)", rates[plan.hub] * h,
            f"${rates[plan.hub]:.4f}/hr x {h:g}h" + (" - never spot: it is the job's state"
                                                     if plan.spot else ""))

    # Addresses, or the gateway that replaces them.
    if plan.egress == "nat":
        est.add("1 x NAT gateway", reg["nat_gateway_hour"] * h,
                f"${reg['nat_gateway_hour']:.3f}/hr x {h:g}h")
        est.add("NAT data processing", reg["nat_gateway_gb"] * plan.egress_gb,
                f"${reg['nat_gateway_gb']:.3f}/GB x {plan.egress_gb:g} GB")
        est.add("1 x public IPv4 (the gateway's)", reg["public_ipv4_hour"] * h,
                f"${reg['public_ipv4_hour']:.3f}/hr x {h:g}h")
    else:
        est.add(f"{plan.instances} x public IPv4", plan.instances * reg["public_ipv4_hour"] * h,
                f"${reg['public_ipv4_hour']:.3f}/hr x {plan.instances} x {h:g}h")

    # Storage, billed by the GB-month for as long as the volume exists.
    gb = plan.instances * plan.disk_gb
    ebs_hour = reg["ebs_gp3_gb_month"] / HOURS_PER_MONTH
    est.add(f"EBS gp3, {gb} GB", gb * ebs_hour * h,
            f"${reg['ebs_gp3_gb_month']:.3f}/GB-month -> ${gb * ebs_hour:.4f}/hr; "
            f"billed until the volume is deleted, not until the instance stops")

    # Egress to the model provider.  Between instances in one AZ it is free,
    # which is the reason the runbook pins one AZ.
    est.add("egress to the provider", reg["egress_gb"] * plan.egress_gb,
            f"${reg['egress_gb']:.2f}/GB x {plan.egress_gb:g} GB; "
            f"hub<->node traffic inside one AZ is free")
    return est


def render(est: Estimate, *, width: int = 78) -> str:
    p, out = est.plan, []
    per = p.ranks_per_node or RANKS_PER_NODE.get(p.worker, 8)
    out.append(f"{p.nodes} x {p.worker} + 1 x {p.hub} hub in {p.region}, {p.hours:g} hours")
    out.append(f"{p.instances} instances, {p.size} ranks ({per} per node)"
               + (", workers on spot" if p.spot else ""))
    out.append("-" * width)
    for label, amount, note in est.lines:
        out.append(f"  {label:<34} {amount:>9.2f}   {note}")
    out.append("-" * width)
    out.append(f"  {'infrastructure':<34} {est.total:>9.2f}   ${est.per_hour:.3f}/hour")
    if p.api_spend:
        total = est.total + p.api_spend
        share = est.total / total
        out.append(f"  {'model provider (given)':<34} {p.api_spend:>9.2f}")
        out.append(f"  {'total':<34} {total:>9.2f}   "
                   f"machines are {share:.0%} of it")
    out.append("")
    out.append(f"List prices as of {est.as_of}; refresh with --refresh."
               + ("  Spot figures are a planning discount, not a quote: check "
                  "describe-spot-price-history." if p.spot else ""))
    return "\n".join(out)


def compare(plan: Plan, prices: dict[str, Any] | None = None,
            types: list[str] | None = None) -> str:
    """The sizing table: what each worker type costs for the same wall time.

    The column that matters is the last one.  Ranks are not free to add --- each
    one is a model conversation someone pays for --- but the *machine* under a
    rank is nearly free, and the table is how you see that.
    """
    pr = prices or load_prices()
    rates = pr["regions"][plan.region]["instance_hour"]
    types = types or [t for t in ("t4g.micro", "t4g.small", "t4g.medium", "t4g.large",
                                  "c7g.large", "m7g.large") if t in rates]
    rows = ["  worker        mem    ranks/node   ranks   infra $   $/rank-hour",
            "  " + "-" * 66]
    for t in types:
        p = Plan(**{**plan.__dict__, "worker": t, "ranks_per_node": None})
        try:
            e = estimate(p, pr)
        except PriceError:
            continue
        per_rank = e.total / (p.size * p.hours) if p.size and p.hours else 0.0
        rows.append(f"  {t:<12} {MEMORY_GIB.get(t, 0):>4g}Gi {RANKS_PER_NODE.get(t, 8):>10}"
                    f" {p.size:>7} {e.total:>9.2f} {per_rank:>13.5f}")
    head = (f"{plan.nodes} nodes for {plan.hours:g}h in {plan.region}"
            + (", workers on spot" if plan.spot else "") + f" (prices as of {pr['as_of']})")
    return head + "\n" + "\n".join(rows)


def refresh(region: str, path: Path = PRICES) -> str:
    """Re-read on-demand instance prices from the AWS Price List API.

    Needs boto3 and credentials allowed ``pricing:GetProducts``.  The Price List
    API lives in us-east-1 and eu-central-1 only, whatever region is being
    priced.  Only the instance-hour rates are refreshed: the storage, address
    and transfer rates change rarely and are not exposed by the same filter
    shape, so they stay under review by hand.
    """
    try:
        import boto3  # noqa: PLC0415 - optional, and only for this path
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PriceError("refreshing prices needs boto3: pip install -e '.[aws]'") from exc

    data = load_prices(path)
    if region not in data["regions"]:
        raise PriceError(f"{region!r} is not in the table; add it by hand first")
    client = boto3.client("pricing", region_name="us-east-1")
    rates = data["regions"][region]["instance_hour"]
    updated = 0
    for itype in sorted(rates):
        flt = [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in (
            ("instanceType", itype), ("regionCode", region), ("operatingSystem", "Linux"),
            ("tenancy", "Shared"), ("preInstalledSw", "NA"), ("capacitystatus", "Used"))]
        pages = client.get_paginator("get_products").paginate(ServiceCode="AmazonEC2",
                                                              Filters=flt)
        price = None
        for page in pages:
            for raw in page["PriceList"]:
                doc = json.loads(raw)
                for term in doc.get("terms", {}).get("OnDemand", {}).values():
                    for dim in term.get("priceDimensions", {}).values():
                        usd = float(dim.get("pricePerUnit", {}).get("USD", 0) or 0)
                        if usd > 0:
                            price = usd if price is None else min(price, usd)
        if price:
            rates[itype] = round(price, 6)
            updated += 1
    import datetime as _dt

    data["as_of"] = _dt.date.today().isoformat()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"refreshed {updated} instance rates for {region}; as_of {data['as_of']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="aws-costs", description="what an AgentMPI fleet costs to run")
    ap.add_argument("--nodes", type=int, default=32, help="worker instances (the hub is extra)")
    ap.add_argument("--worker", default="t4g.small")
    ap.add_argument("--hub", default="c7g.large")
    ap.add_argument("--hours", type=float, default=4.0, help="billed wall time, including setup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--disk-gb", type=int, default=20)
    ap.add_argument("--ranks-per-node", type=int, default=None,
                    help="override the memory-derived default")
    ap.add_argument("--spot", action="store_true", help="price the workers at the spot discount")
    ap.add_argument("--egress", choices=("public-ip", "nat"), default="public-ip")
    ap.add_argument("--egress-gb", type=float, default=5.0, help="GB out to the model provider")
    ap.add_argument("--api-spend", type=float, default=0.0,
                    help="the model bill, to show what share the machines are")
    ap.add_argument("--compare", action="store_true", help="print the sizing table instead")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-read prices from AWS, then exit")
    a = ap.parse_args(argv)

    try:
        if a.refresh:
            print(refresh(a.region))
            return 0
        plan = Plan(nodes=a.nodes, worker=a.worker, hub=a.hub, hours=a.hours, region=a.region,
                    disk_gb=a.disk_gb, spot=a.spot, egress=a.egress, egress_gb=a.egress_gb,
                    api_spend=a.api_spend, ranks_per_node=a.ranks_per_node)
        if a.compare:
            print(compare(plan))
            return 0
        est = estimate(plan)
    except PriceError as exc:
        print(f"aws-costs: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(est.to_dict(), indent=2) if a.json else render(est))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
