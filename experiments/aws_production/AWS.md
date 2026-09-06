# Running a production job on ~32 AWS machines

This is the operator's runbook, in the shape `NODES.md` established for the git
runs: the commands in order, what each one is for, and what went wrong the first
time so it does not have to go wrong again.

The short version, if you have read the rest before:

```bash
export AWS_REGION=us-east-1 OPENROUTER_API_KEY=...
F="python -m experiments.aws_production.fleet"
$F plan   --name e9-aws-p256 --nodes 32 --spot --api-spend 23
$F up     --name e9-aws-p256 --nodes 32 --spot
$F status --name e9-aws-p256              # until 33/33 bootstrapped
$F env    --name e9-aws-p256              # credentials, then start the hub
$F run    --name e9-aws-p256 --size 256
$F logs   --name e9-aws-p256 --node 0
$F collect --name e9-aws-p256
$F down   --name e9-aws-p256              # the step people forget
```

---

## Why the transport changed, and why it had to

Every previous multi-machine run used the git device: the machines shared
nothing but a git remote, because a cloud sandbox has no shared filesystem, no
inbound port, and an egress policy that admits a handful of hosts. A git ref is
a compare-and-swap cell, so the six waist operations became "fetch, apply,
commit, push, retry on rejection".

That transport has a measured ceiling, and it is well below thirty-two. From
`experiments/e7_rawapi_book/NODES.md`:

> eight daemons on one git ref land one push in ten (p=256 over eight machines,
> the transport's ceiling; four machines sit inside it)

Thirty-two daemons contending for one ref would not run a job; they would run a
push contest. So going to thirty-two machines is not a matter of asking for more
of the same instances. It needs a transport that does not serialise every
machine's writes through one remote cell.

**Real cloud machines are not sandboxes, and that is the whole point.** Thirty-two
EC2 instances in one VPC are not behind NAT from each other: every instance has
a routable private address, a security group is a firewall you write, and the
round trip between two instances in one availability zone is a few tenths of a
millisecond rather than three seconds. The reason to encode job state in a git
ref is gone.

`ampi/device/hub.py` is the transport that follows: one process holds the state
and answers the six operations over TCP; every rank on every node is a client.
There is no push contest because there is one writer and it is a mutex in one
process. It passes the same conformance suite as the other five transports, and
nothing above the waist changed — which is the claim the waist exists to make.

Two things fall out of it that no previous transport could offer:

* **One clock.** A lease is a time comparison and a conviction is a judgement
  about someone else's lease, so a fleet whose clocks differ convicts the
  living. Ranks read the *hub's* clock, sampled periodically and carried as an
  offset, so it costs a local `time.time()` between samples.
* **State of a bounded size.** The git device parses the whole job document on
  every commit — 38 MB by the end of the 256-rank run. Here it is a SQLite file
  with indices, so a poll is a lookup rather than a parse.

And one thing it costs, which must be said plainly: **the hub is a single point
of failure.** The git remote was a hosted service someone else kept up. If the
hub instance dies, the job stops. Its disk survives, so a hub restarted on the
same volume serves the same job and the population comes back with `rejoin` —
the recovery the frozen 128-rank run already used — but that is recovery, not
availability. Run the hub on its own instance, give it no ranks, and keep its
volume.

---

## Before you start

You need, on your own machine:

* **An AWS account and credentials.** `aws sts get-caller-identity` should
  answer. The identity needs EC2 (describe/run/terminate instances, create and
  delete VPC, subnet, internet gateway, route table, security group, key pair)
  and `ssm:GetParameter` for the public AMI alias. On a fresh account, check
  your **vCPU quota**: the default *Running On-Demand Standard instances* limit
  is often 5 vCPUs, and 32 × `t4g.small` is 64 vCPUs. Spot has its own separate
  quota. Raise both in Service Quotas *before* you launch — this is the single
  most common way a first fleet half-appears.
* **`boto3`**: `pip install -e '.[aws]'`.
* **A model-provider key** in your shell (`OPENROUTER_API_KEY`).
* **An ssh client.** `fleet.py` drives the fleet over ssh.

```bash
pip install -e '.[aws,tokens]'
export AWS_REGION=us-east-1
export OPENROUTER_API_KEY=...
aws sts get-caller-identity
```

---

## 1. Price it before you build it

```bash
python -m experiments.aws_production.fleet plan --name e9-aws-p256 --nodes 32 \
    --hours 4 --spot --api-spend 23
```

`plan` makes no AWS calls. It prints the cost breakdown and what `up` would
create. Read the breakdown before you launch; the section at the end of this
document explains the two lines that surprise people.

## 2. Bring the fleet up

```bash
python -m experiments.aws_production.fleet up --name e9-aws-p256 --nodes 32 --spot
```

This creates, all tagged `ampi:job=e9-aws-p256`:

* one VPC (`10.42.0.0/16`), one public subnet **in a single availability zone**,
  an internet gateway and a route table;
* one security group: ssh from your address only (`--ssh-cidr` to override,
  defaulting to this machine's `/32`), and the hub port reachable **from inside
  the VPC and nowhere else**;
* one EC2 key pair, saved to `~/.ssh/ampi-<name>.pem` if it did not exist;
* **the hub first, alone**, because every worker's user-data carries the hub's
  address and the address cannot exist before the hub does;
* then all 32 workers in one `run_instances` call.

One AZ is a deliberate choice twice over: it is the low-latency placement, and
traffic between instances in one AZ over private addresses is not billed, while
crossing AZs costs a cent per gigabyte in each direction.

`runs/e9-aws-p256/fleet.json` is written *before* anything boots, naming every
machine requested. That is the same discipline as the launch plan naming every
rank requested: a node that never came up is then a visible absence rather than
a silent one.

## 3. Wait for the bootstrap, not for the instances

```bash
python -m experiments.aws_production.fleet status --name e9-aws-p256
```

An instance in the `running` state is a booted machine, not a prepared one. The
cloud-init script installs Python, clones the repo at the pinned ref, builds the
venv, disciplines the clock against Amazon's link-local NTP service and raises
the file-descriptor limit; that is about ninety seconds after `running`. `status`
probes for `/var/lib/ampi/ready` and prints `33/33 bootstrapped` when the fleet
is actually usable. **Do not go on until it does.**

Under `--spot`, check the count as well as the readiness. A spot request that
could not be filled leaves you with fewer machines than you asked for, and a job
created for 256 ranks on 30 nodes' worth of processes will convict the missing
ranks rather than tell you the fleet was short.

## 4. Push the credentials and start the hub

```bash
python -m experiments.aws_production.fleet env --name e9-aws-p256
```

The provider key and a freshly generated hub token go over ssh into
`/etc/ampi.secrets`, root-owned and group-readable by `ec2-user`. **They never
go through user-data**: user-data is readable by any process on the instance
through the metadata service, so a key placed there is a key every rank process
can read and any bug can leak. The token is saved locally at
`runs/<name>/hub_token` — it is what lets you restart the hub later without
re-keying the fleet.

Then the hub service starts. It is a systemd unit with `Restart=always`, because
the job's state is behind it and a rank that cannot reach it is a rank that
stalls.

## 5. Start the job

```bash
python -m experiments.aws_production.fleet run --name e9-aws-p256 --size 256
```

**Node 0 first, always.** It creates the job; the others wait for the world
communicator to appear and then start their ranks. `run` starts node 0, waits
`--stagger` seconds, then starts the other 31 at once. This is the rule that
cost an attempt on the git runs, and here it is enforced by the tool rather than
remembered by the operator.

With `--size 256 --nodes 32` the launcher block-distributes: node *k* hosts ranks
`8k … 8k+7`.

To run something other than the book:

```bash
... run --name X --size 256 --module experiments.e8_adaptive_book.harness \
        --extra "--respawn 1 --lease 1800 -q"
```

## 6. Watch it

```bash
python -m experiments.aws_production.fleet logs --name e9-aws-p256 --node 0
python -m experiments.aws_production.fleet logs --name e9-aws-p256          # the hub's journal
```

Two things worth knowing, both learned on the git runs and both still true here:

*A rank at work is silent.* A research rank in a tool loop makes no runtime call
for minutes. Silence on the provider's dashboard with ranks alive means the ranks
are in the transport, and the trace says where. To see what one is doing, dump
its stack (`py-spy dump --pid`) and read its conversations with
`python -m ampitools.calls`.

*Verify from the device, not from the nodes' reports.* Which ranks renewed their
leases in the last two minutes, grouped by node, is the question; a node that
answers ssh is not a node whose ranks are progressing.

The hub is also the one place where the whole population's transport load is
visible at once: `ampi stats` through any rank returns the hub's request,
mutation and client counts.

## 7. Collect the evidence

```bash
python -m experiments.aws_production.fleet collect --name e9-aws-p256
```

Node 0's `runs/<name>/` (trace, diagnosis, report, the population's glossary and
findings) comes home, and every node's driver log lands in `runs/<name>/launch/`.
The fleet record is only a claim about which machines were asked for until those
are beside it.

## 8. Take it down — this is the step people forget

```bash
python -m experiments.aws_production.fleet down --name e9-aws-p256
```

It lists what it will destroy and makes you type the job name. It acts on
exactly the `ampi:job` tag, terminates every instance, then deletes the network.
Volumes are `DeleteOnTermination`, so nothing is left billing.

**Stopping an instance does not stop its bill.** EBS is charged by the
GB-month for as long as the volume exists, running or not: 33 × 20 GB left
stopped is about $53 a month for machines doing nothing. Terminate; do not stop.

Afterwards, confirm nothing is orphaned:

```bash
aws ec2 describe-instances --region $AWS_REGION \
    --filters "Name=tag:ampi:job,Values=e9-aws-p256" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
aws ec2 describe-volumes --region $AWS_REGION \
    --filters "Name=tag:ampi:job,Values=e9-aws-p256" --query 'Volumes[].VolumeId' --output text
```

---

## Doing it by hand

`fleet.py` is not magic and you may want to run inside an existing VPC. The
equivalent by hand, for one worker:

```bash
AMI=$(aws ssm get-parameter --region $AWS_REGION \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
    --query Parameter.Value --output text)

aws ec2 create-security-group --group-name ampi-demo --description "AgentMPI" --vpc-id $VPC
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 22 --cidr $MYIP/32
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 7411 --cidr 10.42.0.0/16

sed -e "s|__REPO_URL__|https://github.com/agentmpi/AgentMPI|" \
    -e "s|__REPO_REF__|aws/production_exp|" \
    -e "s|__HUB_ADDR__|10.42.1.10:7411|" \
    -e "s|__NODE_INDEX__|0|" -e "s|__NODE_COUNT__|32|" -e "s|__ROLE__|worker|" \
    experiments/aws_production/bootstrap.sh > /tmp/ud.sh

aws ec2 run-instances --image-id $AMI --instance-type t4g.small --count 1 \
    --key-name ampi-demo --security-group-ids $SG --subnet-id $SUBNET \
    --user-data file:///tmp/ud.sh \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=ampi:job,Value=demo}]'
```

And on the hub instance, if you are starting it yourself:

```bash
AMPI_HUB_TOKEN=... .venv/bin/python -m ampi.device.hub \
    --root /opt/ampi/state/job --host 0.0.0.0 --port 7411
```

Every rank then needs only three variables, which is the whole of the transport
configuration:

```bash
export AMPI_DEVICE=hub AMPI_HUB_ADDR=10.42.1.10:7411 AMPI_HUB_TOKEN=...
```

---

## What it costs

Generated, not asserted. `prices.json` holds list prices with the date they were
read, `costs.py` does the arithmetic, and every figure carries that date:

```bash
python -m experiments.aws_production.costs --nodes 32 --hours 4 --api-spend 23.38
python -m experiments.aws_production.costs --refresh          # re-read from AWS
```

**32 × `t4g.small` workers (256 ranks) + 1 × `c7g.large` hub, us-east-1, 4 hours,
on-demand**, at prices as of 2026-05-01:

| line | $ | note |
|---|---:|---|
| 32 × t4g.small | 2.15 | $0.0168/hr each |
| 1 × c7g.large hub | 0.29 | on-demand always |
| 33 × public IPv4 | 0.66 | $0.005/hr per address |
| EBS gp3, 660 GB | 0.29 | billed until *deleted*, not until stopped |
| egress to the provider | 0.45 | ~5 GB; hub↔node inside one AZ is free |
| **infrastructure** | **3.84** | **$0.96/hour** |
| model provider (E7 p=256 actual) | 23.38 | |
| **total** | **27.22** | machines are **14%** of it |

With `--spot` on the workers (the hub never): **$2.44**, or 9% of the total.

Two lines are worth staring at:

* **The IPv4 addresses cost more than the hub, and on spot they cost about as
  much as all thirty-two workers.** AWS has charged $0.005 per address per hour
  since February 2024. The alternative — a private subnet behind one NAT gateway
  — is genuinely cheaper, and the arithmetic says by $0.23 over four hours
  (`--egress nat`). That is not worth the extra setup at this size, and now you
  know it rather than assume it either way.
* **EBS is charged whether or not anything is running.** It is the smallest line
  during a run and the only line that keeps growing after you walk away.

### Choosing the worker

A raw-API rank is a Python process that spends nearly all of its life blocked on
an HTTPS call, so what bounds the count per node is memory, not CPU — about
70 MB resident each. Burstable `t4g` instances suit this exactly: the workload
is network-bound, so CPU credits are never the constraint. (This was *not* true
of the git runs, where a node's daemon ran at 85% of a core parsing a 38 MB
state file. Removing that is part of what the hub buys.)

```
$ python -m experiments.aws_production.costs --compare --nodes 32 --hours 4
  worker        mem    ranks/node   ranks   infra $   $/rank-hour
  t4g.micro       1Gi          4     128      2.76       0.00540
  t4g.small       2Gi          8     256      3.84       0.00375
  t4g.medium      4Gi         16     512      5.99       0.00292
  t4g.large       8Gi         32    1024     10.29       0.00251
  c7g.large       4Gi         16     512     10.97       0.00536
  m7g.large       8Gi         32    1024     12.13       0.00296
```

`t4g.small` is the default because 8 ranks a node × 32 nodes is 256, which is
the scale the git transport could not reach and the one E7 has a completed run
to compare against. If you want more ranks, buy them with a bigger worker rather
than more nodes: `$/rank-hour` falls monotonically with node size, and every
extra node is another address, another volume and another thing to fail.

### Spot

Workers on spot, the hub never. A reclaimed worker is a `rejoin`; a reclaimed
hub is the end of the run. This is safe for the workers precisely because the
recovery already exists and has been used in production: `ampirun --respawn`
brings a rank back, and a node whose machine was recycled re-enters the job with
its convicted ranks respawned on fresh epochs.

The spot figure in the table is a planning discount, not a quote. Check it:

```bash
aws ec2 describe-spot-price-history --instance-types t4g.small \
    --product-descriptions Linux/UNIX --max-results 5 --region $AWS_REGION
```

### The number that actually matters

Infrastructure is **9–14% of the run**. The models are the rest. Adding machines
is nearly free and adding ranks is not, because a rank is a model conversation
someone pays for — and E7 already showed that adding ranks buys little wall time,
since the run ends when its slowest model ends. The reason to go to thirty-two
machines is to remove the transport as the constraint, not to make the job
cheaper.

---

## What is not done yet

Honesty about the state of this branch: **the hub device is tested — the
conformance suite, plus the properties in `tests/test_hub.py` — but no
production run has been executed on real EC2 hardware from this code.** The
numbers above are a cost model over a price table, not a bill. What is unknown
until a run happens:

* Whether one hub process serves 256 clients comfortably. The load that grew at
  p=256 on git was the population's polling: a rank waiting in a collective reads
  every member's row, so a poll costs *p* reads and a polling population costs
  *p²*. Those reads are now microseconds against an indexed SQLite file rather
  than a parse of a 38 MB document, which should make it a non-issue — but
  `NODES.md`'s standing recommendation, that the runtime read the rank table
  once per poll rather than once per member, is still the right fix and is still
  above the waist.
* Whether 256 concurrent connections want an event loop rather than a thread
  each. A thread per connection is what gitd does and what this inherits.
* What spot reclamation actually looks like mid-phase at this width.

The first real run should be small — `--nodes 4 --size 32` — and should be read
for the transport's behaviour before anyone spends four hours of models on it.
