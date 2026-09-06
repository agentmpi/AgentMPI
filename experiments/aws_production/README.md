# Production on real remote machines

E5 and E7 ran across machines that shared nothing but a git remote, because the
machines were cloud sandboxes: each one its own VM behind NAT, with no inbound
port and an egress policy admitting a handful of hosts. The git device was
built for that constraint and is honest about its ceiling — eight daemons on one
ref land about one push in ten, and four machines sit just inside it.

This directory is the same production job on machines that are not sandboxes.
Thirty-two EC2 instances in one VPC can reach each other directly, so the
constraint that forced a distributed CAS over a git ref does not apply, and the
transport becomes the obvious one the sandbox could not have: one process holds
the state and serves the six waist operations over TCP.

| | E5 / E7 (sandboxes) | here (EC2) |
|---|---|---|
| what a node is | a cloud sandbox session | an EC2 instance the operator owns |
| transport | `gitd`: a daemon per node over one git ref | `hub`: one server, TCP, inside one VPC |
| serialisation point | a ref on a hosted remote, ~3 s away | a mutex in one process, ~0.2 ms away |
| writers contending | one per node; 8 is the ceiling | one; there is no contest |
| the job's clock | each machine's own wall clock | the hub's, carried as an offset |
| state | one JSON document, 38 MB by the end | an indexed SQLite file |
| node failure | `rejoin`, replaying the program | unchanged — `rejoin`, replaying |
| single point of failure | the git host (someone else's problem) | **the hub (yours)** |

## What is here

```
fleet.py        provision, drive and tear down the machines; `plan up status
                env run logs collect down`.  Everything is tagged ampi:job=<name>
                and `down` acts on exactly that tag
bootstrap.sh    cloud-init user-data: prepare an instance and stop.  It carries
                no credential and starts no rank
costs.py        the cost model, over prices.json; every figure carries the date
                its prices were read
prices.json     list prices with an as_of date; `costs.py --refresh` re-reads
                them from the AWS Price List API
AWS.md          the runbook: the commands in order, and the cost table
```

The transport itself is not here — it is `ampi/device/hub.py`, because it is a
device below the waist and not an experiment. It passes the same conformance
suite as the other five transports; `tests/test_hub.py` covers the properties it
has *because* it is one authoritative process on a network: token
authentication, one clock across the fleet, a mutation resent after a dropped
connection applied exactly once, many clients writing at once with no contest,
and a client told an address refusing to become a second authority.

## Running it

See [`AWS.md`](AWS.md). The short form:

```bash
pip install -e '.[aws,tokens]'
export AWS_REGION=us-east-1 OPENROUTER_API_KEY=...
F="python -m experiments.aws_production.fleet"

$F plan --name e9-aws-p256 --nodes 32 --spot --api-spend 23   # no AWS calls
$F up   --name e9-aws-p256 --nodes 32 --spot
$F status --name e9-aws-p256          # wait for 33/33 bootstrapped
$F env  --name e9-aws-p256            # credentials over ssh, then start the hub
$F run  --name e9-aws-p256 --size 256 # node 0 first, always
$F collect --name e9-aws-p256
$F down --name e9-aws-p256
```

## Cost

32 workers + 1 hub for four hours is **$3.84** on demand and **$2.44** with the
workers on spot, against the **$23.38** the same book cost in models at p=256.
The machines are 9–14% of the run. Two lines in that breakdown are worth
knowing: the 33 public IPv4 addresses cost more than the hub instance does, and
EBS is billed until a volume is *deleted*, not until its instance is stopped.

The full table, how to choose a worker size, and why spot is safe for workers
and never for the hub are in [`AWS.md`](AWS.md#what-it-costs).

## Status

The device is tested; the fleet tooling is not yet proven against real hardware.
No run in `runs/` came from this code. `AWS.md` ends with the list of what is
unknown until one does — chiefly whether one hub process serves 256 clients
comfortably, given that the load which grew at p=256 on git was the population's
own polling. Start at `--nodes 4 --size 32` and read the transport's behaviour
before committing four hours of models to it.
