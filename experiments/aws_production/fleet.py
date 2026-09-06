"""Provision, drive and tear down the machines an AgentMPI job runs on.

``experiments/launch.py`` writes a launch plan naming every rank *requested*
before any of them starts, so the set the experiment intended is recorded
independently of the set that answered.  A fleet needs the same discipline one
level down: the set of machines the run intended, written before any of them
boots, so that a node which never came up is a visible absence rather than a
silent one.  ``runs/<name>/fleet.json`` is that record, and every instance
carries the tags that make it findable and, more importantly, terminable.

    python -m experiments.aws_production.fleet plan  --name e9-aws-p256 --nodes 32
    python -m experiments.aws_production.fleet up    --name e9-aws-p256 --nodes 32
    python -m experiments.aws_production.fleet status --name e9-aws-p256
    python -m experiments.aws_production.fleet env   --name e9-aws-p256 --api-key-env OPENROUTER_API_KEY
    python -m experiments.aws_production.fleet run   --name e9-aws-p256 --size 256
    python -m experiments.aws_production.fleet collect --name e9-aws-p256
    python -m experiments.aws_production.fleet down  --name e9-aws-p256

The topology is the one the hub device implies and the git device could not
have: one hub instance holding the job's state, ``--nodes`` worker instances
holding ranks, all in **one availability zone** --- for latency, and because
traffic between instances in one AZ over private addresses is not billed while
traffic across AZs is.  The hub runs no ranks.

Everything this creates is tagged ``ampi:job=<name>``, and ``down`` acts on
exactly that tag.  A fleet whose teardown depends on the operator remembering
thirty-three instance ids is a fleet that will still be running next month; the
$0.08 per GB-month on the volumes is charged whether or not anyone is using
them, and a stopped instance keeps its volume.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .costs import Plan, estimate, render

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = Path(__file__).with_name("bootstrap.sh")
#: Amazon Linux 2023, ARM64, resolved through the public SSM alias so the AMI id
#: is never pinned in this file: a pinned id rots and is region-specific.
AMI_ALIAS_ARM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
AMI_ALIAS_X86 = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
HUB_PORT = 7411
TAG_JOB = "ampi:job"
TAG_ROLE = "ampi:role"
TAG_NODE = "ampi:node"


class FleetError(RuntimeError):
    pass


def _boto3() -> Any:
    try:
        import boto3  # noqa: PLC0415 - optional; only the AWS paths need it
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise FleetError("this needs boto3: pip install -e '.[aws]'") from exc
    return boto3


def run_dir(name: str) -> Path:
    return REPO_ROOT / "runs" / name


def fleet_file(name: str) -> Path:
    return run_dir(name) / "fleet.json"


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------


@dataclass
class Node:
    index: int              # -1 for the hub: it holds no ranks
    role: str               # "hub" or "worker"
    instance_id: str = ""
    private_ip: str = ""
    public_ip: str = ""
    state: str = "requested"


@dataclass
class Fleet:
    name: str
    region: str
    az: str
    nodes: int
    worker_type: str
    hub_type: str
    repo_url: str
    repo_ref: str
    key_name: str
    vpc_id: str = ""
    subnet_id: str = ""
    sg_id: str = ""
    igw_id: str = ""
    rtb_id: str = ""
    created_network: bool = False
    requested_at: str = ""
    members: list[Node] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.members is None:
            self.members = []

    @property
    def hub(self) -> Node | None:
        return next((m for m in self.members if m.role == "hub"), None)

    @property
    def workers(self) -> list[Node]:
        return sorted((m for m in self.members if m.role == "worker"), key=lambda m: m.index)

    def save(self) -> Path:
        p = fleet_file(self.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        d["members"] = [asdict(m) if not isinstance(m, dict) else m for m in self.members]
        p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        return p

    @classmethod
    def load(cls, name: str) -> Fleet:
        p = fleet_file(name)
        if not p.exists():
            raise FleetError(f"no fleet record at {p}; has `up` been run for {name!r}?")
        d = json.loads(p.read_text(encoding="utf-8"))
        members = [Node(**m) for m in d.pop("members", [])]
        return cls(**d, members=members)


# --------------------------------------------------------------------------
# AWS
# --------------------------------------------------------------------------


class Aws:
    """The handful of EC2 calls this needs, and nothing more."""

    def __init__(self, region: str) -> None:
        b = _boto3()
        self.region = region
        self.ec2 = b.client("ec2", region_name=region)
        self.ssm = b.client("ssm", region_name=region)

    def ami(self, arch_arm: bool) -> str:
        alias = AMI_ALIAS_ARM if arch_arm else AMI_ALIAS_X86
        return self.ssm.get_parameter(Name=alias)["Parameter"]["Value"]

    def pick_az(self) -> str:
        zones = self.ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}])["AvailabilityZones"]
        if not zones:
            raise FleetError(f"no availability zone available in {self.region}")
        return zones[0]["ZoneName"]

    def tags(self, job: str, role: str, index: int, extra: dict[str, str] | None = None) -> list:
        t = {TAG_JOB: job, TAG_ROLE: role, "Name": f"ampi-{job}-{role}"
             + (f"-{index}" if index >= 0 else "")}
        if index >= 0:
            t[TAG_NODE] = str(index)
        t.update(extra or {})
        return [{"Key": k, "Value": v} for k, v in t.items()]

    # -- network ---------------------------------------------------------
    def create_network(self, job: str, az: str, ssh_cidr: str) -> dict[str, str]:
        """One VPC, one public subnet in one AZ, one security group.

        The security group is the whole of the hub's access control at the
        network layer: the hub port is reachable from inside the VPC and from
        nowhere else.  The shared token the hub also requires is defence in
        depth, for the case where this group is later widened by someone who
        did not read this comment.
        """
        vpc = self.ec2.create_vpc(CidrBlock="10.42.0.0/16",
                                  TagSpecifications=[{"ResourceType": "vpc",
                                                      "Tags": self.tags(job, "net", -1)}])["Vpc"]
        vpc_id = vpc["VpcId"]
        self.ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

        subnet = self.ec2.create_subnet(
            VpcId=vpc_id, CidrBlock="10.42.1.0/24", AvailabilityZone=az,
            TagSpecifications=[{"ResourceType": "subnet",
                                "Tags": self.tags(job, "net", -1)}])["Subnet"]
        subnet_id = subnet["SubnetId"]
        self.ec2.modify_subnet_attribute(SubnetId=subnet_id,
                                         MapPublicIpOnLaunch={"Value": True})

        igw = self.ec2.create_internet_gateway(
            TagSpecifications=[{"ResourceType": "internet-gateway",
                                "Tags": self.tags(job, "net", -1)}])["InternetGateway"]
        igw_id = igw["InternetGatewayId"]
        self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

        rtb = self.ec2.create_route_table(
            VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table",
                                              "Tags": self.tags(job, "net", -1)}])["RouteTable"]
        rtb_id = rtb["RouteTableId"]
        self.ec2.create_route(RouteTableId=rtb_id, DestinationCidrBlock="0.0.0.0/0",
                              GatewayId=igw_id)
        self.ec2.associate_route_table(RouteTableId=rtb_id, SubnetId=subnet_id)

        sg = self.ec2.create_security_group(
            GroupName=f"ampi-{job}", Description=f"AgentMPI fleet {job}", VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group",
                                "Tags": self.tags(job, "net", -1)}])
        sg_id = sg["GroupId"]
        self.ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
             "IpRanges": [{"CidrIp": ssh_cidr, "Description": "operator ssh"}]},
            {"IpProtocol": "tcp", "FromPort": HUB_PORT, "ToPort": HUB_PORT,
             "IpRanges": [{"CidrIp": "10.42.0.0/16", "Description": "ranks -> hub, VPC only"}]},
        ])
        return {"vpc_id": vpc_id, "subnet_id": subnet_id, "sg_id": sg_id,
                "igw_id": igw_id, "rtb_id": rtb_id}

    def destroy_network(self, f: Fleet) -> list[str]:
        done = []
        for call, kw, label in (
            (self.ec2.delete_security_group, {"GroupId": f.sg_id}, f"sg {f.sg_id}"),
            (self.ec2.delete_subnet, {"SubnetId": f.subnet_id}, f"subnet {f.subnet_id}"),
            (self.ec2.delete_route_table, {"RouteTableId": f.rtb_id}, f"rtb {f.rtb_id}"),
        ):
            if not list(kw.values())[0]:
                continue
            for attempt in range(12):  # dependencies clear only as ENIs disappear
                try:
                    call(**kw)
                    done.append(label)
                    break
                except Exception:  # noqa: BLE001 - retried, then reported as left behind
                    if attempt == 11:
                        done.append(f"{label} LEFT BEHIND")
                    time.sleep(10)
        if f.igw_id and f.vpc_id:
            for _ in range(6):
                try:
                    self.ec2.detach_internet_gateway(InternetGatewayId=f.igw_id, VpcId=f.vpc_id)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(10)
            try:
                self.ec2.delete_internet_gateway(InternetGatewayId=f.igw_id)
                done.append(f"igw {f.igw_id}")
            except Exception:  # noqa: BLE001
                done.append(f"igw {f.igw_id} LEFT BEHIND")
        if f.vpc_id:
            try:
                self.ec2.delete_vpc(VpcId=f.vpc_id)
                done.append(f"vpc {f.vpc_id}")
            except Exception:  # noqa: BLE001
                done.append(f"vpc {f.vpc_id} LEFT BEHIND")
        return done

    # -- instances -------------------------------------------------------
    def launch(self, *, job: str, role: str, index: int, count: int, itype: str, ami: str,
               subnet_id: str, sg_id: str, key_name: str, user_data: str, disk_gb: int,
               spot: bool) -> list[str]:
        market: dict[str, Any] = {}
        if spot:
            # "one-time" and no max price: the request is filled at the market
            # rate or not at all, and an unfilled request is a visible failure
            # rather than a fleet that is quietly half the size it should be.
            market = {"MarketType": "spot",
                      "SpotOptions": {"SpotInstanceType": "one-time",
                                      "InstanceInterruptionBehavior": "terminate"}}
        res = self.ec2.run_instances(
            ImageId=ami, InstanceType=itype, MinCount=count, MaxCount=count,
            KeyName=key_name, UserData=user_data,
            NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnet_id,
                                "Groups": [sg_id], "AssociatePublicIpAddress": True,
                                "DeleteOnTermination": True}],
            BlockDeviceMappings=[{"DeviceName": "/dev/xvda",
                                  "Ebs": {"VolumeSize": disk_gb, "VolumeType": "gp3",
                                          "DeleteOnTermination": True}}],
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            **({"InstanceMarketOptions": market} if market else {}),
            TagSpecifications=[{"ResourceType": "instance",
                                "Tags": self.tags(job, role, index)},
                               {"ResourceType": "volume",
                                "Tags": self.tags(job, role, index)}])
        return [i["InstanceId"] for i in res["Instances"]]

    def describe(self, job: str) -> list[dict[str, Any]]:
        out = []
        pages = self.ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": f"tag:{TAG_JOB}", "Values": [job]},
                     {"Name": "instance-state-name",
                      "Values": ["pending", "running", "stopping", "stopped", "shutting-down"]}])
        for page in pages:
            for r in page["Reservations"]:
                out.extend(r["Instances"])
        return out

    def terminate(self, ids: list[str]) -> None:
        for i in range(0, len(ids), 100):
            self.ec2.terminate_instances(InstanceIds=ids[i:i + 100])

    def ensure_key(self, name: str, save_to: Path) -> None:
        try:
            self.ec2.describe_key_pairs(KeyNames=[name])
            return
        except Exception:  # noqa: BLE001 - absent, so create it
            pass
        kp = self.ec2.create_key_pair(KeyName=name, KeyType="ed25519")
        save_to.parent.mkdir(parents=True, exist_ok=True)
        save_to.write_text(kp["KeyMaterial"], encoding="utf-8")
        save_to.chmod(0o600)


# --------------------------------------------------------------------------
# ssh
# --------------------------------------------------------------------------


def ssh_cmd(key: Path, host: str, command: str, *, user: str = "ec2-user",
            timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new",
         "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
         "-o", "ConnectTimeout=15", f"{user}@{host}", command],
        capture_output=True, text=True, timeout=timeout)


def fan_out(key: Path, hosts: list[tuple[str, str]], command: str | Any,
            *, timeout: int = 300) -> dict[str, subprocess.CompletedProcess]:
    """Run a command on every host at once.

    ``command`` is either one string for every host, or a callable taking the
    host's label and returning that host's command --- the node index differs
    per machine, and a step that has to be a loop is a step an operator does
    partially.  Sequential ssh to thirty-three machines is three minutes of
    waiting each time, which is long enough that steps start getting skipped.
    """
    from concurrent.futures import ThreadPoolExecutor

    def cmd_for(label: str) -> str:
        return command(label) if callable(command) else command

    out: dict[str, subprocess.CompletedProcess] = {}
    with ThreadPoolExecutor(max_workers=min(40, max(1, len(hosts)))) as pool:
        futures = {pool.submit(ssh_cmd, key, ip, cmd_for(label), timeout=timeout): label
                   for label, ip in hosts}
        for fut, label in futures.items():
            try:
                out[label] = fut.result()
            except Exception as exc:  # noqa: BLE001 - reported per host
                out[label] = subprocess.CompletedProcess([], 255, "", str(exc))
    return out


def key_path(name: str) -> Path:
    return Path(os.environ.get("AMPI_FLEET_KEYDIR", str(Path.home() / ".ssh"))) / f"{name}.pem"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def render_user_data(*, repo_url: str, repo_ref: str, hub_addr: str, index: int | str,
                     count: int, role: str) -> str:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    for token, value in (("__REPO_URL__", repo_url), ("__REPO_REF__", repo_ref),
                         ("__HUB_ADDR__", hub_addr), ("__NODE_INDEX__", str(index)),
                         ("__NODE_COUNT__", str(count)), ("__ROLE__", role)):
        text = text.replace(token, value)
    return text


def cmd_plan(a: argparse.Namespace) -> int:
    plan = Plan(nodes=a.nodes, worker=a.worker_type, hub=a.hub_type, hours=a.hours,
                region=a.region, disk_gb=a.disk_gb, spot=a.spot, api_spend=a.api_spend)
    print(render(estimate(plan)))
    print()
    print(f"`up` would create, all tagged {TAG_JOB}={a.name}:")
    print(f"  1 x {a.hub_type} hub (no ranks; holds the job's state on its own EBS volume)")
    print(f"  {a.nodes} x {a.worker_type} workers, {plan.size // max(a.nodes, 1)} ranks each "
          f"= {plan.size} ranks")
    print(f"  1 VPC, 1 public subnet in one AZ, 1 security group "
          f"(ssh from {a.ssh_cidr}; hub port {HUB_PORT} from the VPC only)")
    if a.spot:
        print("  workers on spot, hub on-demand: a reclaimed worker is a `rejoin`, "
              "a reclaimed hub is the end of the run")
    return 0


def cmd_up(a: argparse.Namespace) -> int:
    aws = Aws(a.region)
    az = a.az or aws.pick_az()
    key = key_path(a.key_name)
    aws.ensure_key(a.key_name, key)
    if not key.exists():
        raise FleetError(f"key pair {a.key_name!r} exists in AWS but {key} is not on this "
                         f"machine; use --key-name for a fresh one, or put the .pem there")

    f = Fleet(name=a.name, region=a.region, az=az, nodes=a.nodes, worker_type=a.worker_type,
              hub_type=a.hub_type, repo_url=a.repo_url, repo_ref=a.repo_ref,
              key_name=a.key_name, requested_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                              time.gmtime()))
    # The record of what was asked for, written before anything boots.
    f.members = [Node(index=-1, role="hub")] + [Node(index=i, role="worker")
                                                for i in range(a.nodes)]
    f.save()

    if a.subnet_id:
        f.subnet_id, f.sg_id, f.vpc_id = a.subnet_id, a.sg_id, a.vpc_id
        if not f.sg_id:
            raise FleetError("--subnet-id also needs --sg-id")
    else:
        print(f"creating network in {az} ...")
        net = aws.create_network(a.name, az, a.ssh_cidr)
        f.__dict__.update(net)
        f.created_network = True
    f.save()

    arm = a.worker_type.split(".")[0].endswith("g") or "g." in a.worker_type
    ami = aws.ami(arch_arm=arm)
    print(f"AMI {ami} ({'arm64' if arm else 'x86_64'})")

    # The hub first, and alone: every worker's user-data carries the hub's
    # address, so the address must exist before a worker is asked to boot.  This
    # is "node 0 first, always" made structural rather than remembered.
    hub_ud = render_user_data(repo_url=a.repo_url, repo_ref=a.repo_ref, hub_addr="",
                              index=-1, count=a.nodes, role="hub")
    hub_ami = aws.ami(arch_arm=a.hub_type.split(".")[0].endswith("g") or "g." in a.hub_type)
    [hub_id] = aws.launch(job=a.name, role="hub", index=-1, count=1, itype=a.hub_type,
                          ami=hub_ami, subnet_id=f.subnet_id, sg_id=f.sg_id,
                          key_name=a.key_name, user_data=hub_ud, disk_gb=a.disk_gb,
                          spot=False)
    print(f"hub {hub_id} launching; waiting for its private address ...")
    aws.ec2.get_waiter("instance_running").wait(InstanceIds=[hub_id])
    hub_desc = aws.describe(a.name)
    hub_ip = next(i["PrivateIpAddress"] for i in hub_desc if i["InstanceId"] == hub_id)
    hub_addr = f"{hub_ip}:{HUB_PORT}"
    print(f"hub at {hub_addr}")

    # One user-data for all the workers, so it cannot carry a per-machine index:
    # the index is assigned below from the launch order, tagged onto the
    # instance, and written into /etc/ampi.node by `env`.  Until then the file
    # says "unassigned" rather than a plausible wrong number.
    worker_ud = render_user_data(repo_url=a.repo_url, repo_ref=a.repo_ref, hub_addr=hub_addr,
                                 index="unassigned", count=a.nodes, role="worker")
    ids: list[str] = []
    if a.nodes:
        # One run_instances for the whole set: thirty-two separate calls is
        # thirty-two chances to be throttled halfway through.  The node index is
        # assigned below, from the order AWS reports them in.
        ids = aws.launch(job=a.name, role="worker", index=-1, count=a.nodes,
                         itype=a.worker_type, ami=ami, subnet_id=f.subnet_id, sg_id=f.sg_id,
                         key_name=a.key_name, user_data=worker_ud, disk_gb=a.disk_gb,
                         spot=a.spot)
        print(f"{len(ids)} workers launching ...")
        aws.ec2.get_waiter("instance_running").wait(InstanceIds=ids)

    # Node indices are assigned here, once, and tagged onto the instances, so
    # that the index a machine answers to is the index the record says it has
    # even after a reboot renames nothing.
    for i, iid in enumerate(sorted(ids)):
        aws.ec2.create_tags(Resources=[iid], Tags=[{"Key": TAG_NODE, "Value": str(i)},
                                                   {"Key": "Name",
                                                    "Value": f"ampi-{a.name}-worker-{i}"}])
    _refresh(aws, f)
    f.save()
    print(f"\n{len(f.members)} instances up; record at {fleet_file(a.name)}")
    print("bootstrap takes about 90 seconds more; `status` says when they are ready.")
    return 0


def _refresh(aws: Aws, f: Fleet) -> Fleet:
    """Reconcile the record against what AWS reports."""
    live = {i["InstanceId"]: i for i in aws.describe(f.name)}
    by_index: dict[int, dict[str, Any]] = {}
    hub = None
    for inst in live.values():
        tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
        if tags.get(TAG_ROLE) == "hub":
            hub = inst
        elif TAG_NODE in tags:
            by_index[int(tags[TAG_NODE])] = inst
    for m in f.members:
        inst = hub if m.role == "hub" else by_index.get(m.index)
        if inst is None:
            m.state = "absent"
            continue
        m.instance_id = inst["InstanceId"]
        m.private_ip = inst.get("PrivateIpAddress", "")
        m.public_ip = inst.get("PublicIpAddress", "")
        m.state = inst["State"]["Name"]
    return f


def cmd_status(a: argparse.Namespace) -> int:
    f = Fleet.load(a.name)
    aws = Aws(f.region)
    _refresh(aws, f)
    f.save()
    key = key_path(f.key_name)
    hosts = [(("hub" if m.role == "hub" else str(m.index)), m.public_ip)
             for m in f.members if m.public_ip]
    ready: dict[str, subprocess.CompletedProcess] = {}
    if hosts and not a.no_probe:
        ready = fan_out(key, hosts, "cat /var/lib/ampi/ready 2>/dev/null || echo -", timeout=40)
    print(f"{f.name}: {f.nodes} workers + 1 hub in {f.az}, ref {f.repo_ref}")
    n_ready = 0
    for m in f.members:
        label = "hub" if m.role == "hub" else f"node {m.index}"
        r = ready.get("hub" if m.role == "hub" else str(m.index))
        mark = (r.stdout.strip() if r and r.returncode == 0 else "unreachable")
        if mark not in ("-", "unreachable", ""):
            n_ready += 1
            mark = "ready " + mark
        elif mark == "-":
            mark = "booting"
        print(f"  {label:<10} {m.state:<12} {m.instance_id:<20} "
              f"{m.private_ip:<16} {m.public_ip:<16} {mark}")
    if not a.no_probe:
        print(f"\n{n_ready}/{len(f.members)} bootstrapped")
    return 0


def cmd_env(a: argparse.Namespace) -> int:
    """Push the credentials, then start the hub.

    The model-provider key and the hub token go over ssh into a root-owned file,
    never through user-data: user-data is readable by every process on the
    instance through the metadata service.
    """
    import secrets

    f = Fleet.load(a.name)
    aws = Aws(f.region)
    _refresh(aws, f)
    key = key_path(f.key_name)
    hub = f.hub
    if hub is None or not hub.private_ip:
        raise FleetError("the hub is not up")

    api_key = os.environ.get(a.api_key_env, "")
    if not api_key:
        raise FleetError(f"${a.api_key_env} is not set in this shell; the fleet needs the "
                         f"model provider's credential and this is how it travels")
    token_file = run_dir(f.name) / "hub_token"
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_urlsafe(32)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)

    body = (f"{a.api_key_env}={shlex.quote(api_key)}\n"
            f"AMPI_HUB_TOKEN={shlex.quote(token)}\n")
    secrets_push = ("sudo install -m 0600 /dev/stdin /etc/ampi.secrets <<'AMPI_EOF'\n"
                    + body + "AMPI_EOF\nsudo chown root:root /etc/ampi.secrets && "
                    "sudo chmod 0640 /etc/ampi.secrets && sudo chgrp ec2-user /etc/ampi.secrets")

    def push(label: str) -> str:
        # The index the machine answers to, made to agree with the index the
        # fleet record says it has.  `run` passes --node explicitly, so nothing
        # depends on this file being right --- which is exactly why it would go
        # unnoticed if it were wrong, and why anyone reading /etc/ampi.node to
        # find out which node they are on deserves the true answer.
        idx = "hub" if label == "hub" else label
        return (secrets_push + " && sudo sed -i "
                + shlex.quote(f"s/^AMPI_NODE_INDEX=.*/AMPI_NODE_INDEX={idx}/")
                + " /etc/ampi.node")

    hosts = [(("hub" if m.role == "hub" else str(m.index)), m.public_ip)
             for m in f.members if m.public_ip]
    res = fan_out(key, hosts, push, timeout=90)
    bad = [h for h, r in res.items() if r.returncode != 0]
    print(f"secrets on {len(res) - len(bad)}/{len(res)} instances"
          + (f"; failed: {', '.join(sorted(bad))}" if bad else ""))

    out = ssh_cmd(key, hub.public_ip,
                  "sudo systemctl restart ampi-hub && sleep 2 && "
                  "systemctl is-active ampi-hub && ss -ltn | grep 7411", timeout=90)
    print(f"hub service: {out.stdout.strip() or out.stderr.strip()}")
    if out.returncode != 0:
        print("  (check `journalctl -u ampi-hub` on the hub)", file=sys.stderr)
        return 1
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    """Start the job: the hub already holds the state, so node 0 has no privilege
    over the others in creating it --- but the harness still wants one creator,
    and that is node 0."""
    f = Fleet.load(a.name)
    aws = Aws(f.region)
    _refresh(aws, f)
    key = key_path(f.key_name)
    hub = f.hub
    if hub is None:
        raise FleetError("no hub in the record")
    workers = [m for m in f.workers if m.public_ip]
    if len(workers) < f.nodes:
        print(f"warning: {f.nodes - len(workers)} of {f.nodes} workers are not reachable; "
              f"the job will be created for {a.size} ranks and the missing ones will be "
              f"convicted", file=sys.stderr)

    def node_cmd(index: int) -> str:
        return (
            "set -a; . /etc/ampi.node; . /etc/ampi.secrets; set +a; "
            "cd $AMPI_REPO_DIR && mkdir -p work/aws && "
            f"nohup .venv/bin/python -m {a.module} run "
            f"--name {shlex.quote(a.name)} --size {a.size} "
            f"--nodes {f.nodes} --node {index} "
            f"--device hub --executor {a.executor} "
            + (f"--model {shlex.quote(a.model)} " if a.model else "")
            + f"{a.extra} "
            f">> work/aws/{shlex.quote(a.name)}-node{index}.log 2>&1 & "
            f"echo started $!")

    # Node 0 first, always: it creates the job.  The others wait for the world
    # communicator to appear, and a node that starts before node 0 joins
    # whatever was there before.
    zero = next((m for m in workers if m.index == 0), None)
    if zero is None:
        raise FleetError("node 0 is not reachable, and node 0 creates the job")
    r0 = ssh_cmd(key, zero.public_ip, node_cmd(0), timeout=180)
    print(f"node 0: {r0.stdout.strip() or r0.stderr.strip()}")
    if r0.returncode != 0:
        return 1
    time.sleep(a.stagger)

    rest = [(str(m.index), m.public_ip) for m in workers if m.index != 0]
    if rest:
        res = {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(40, len(rest))) as pool:
            futs = {pool.submit(ssh_cmd, key, ip, node_cmd(int(lbl)), timeout=180): lbl
                    for lbl, ip in rest}
            for fut, lbl in futs.items():
                res[lbl] = fut.result()
        bad = [h for h, r in res.items() if r.returncode != 0]
        print(f"{len(res) - len(bad)}/{len(res)} joining nodes started"
              + (f"; failed: {', '.join(sorted(bad, key=int))}" if bad else ""))
    print(f"\nlogs: fleet.py logs --name {a.name} --node K")
    return 0


def cmd_logs(a: argparse.Namespace) -> int:
    f = Fleet.load(a.name)
    _refresh(Aws(f.region), f)
    key = key_path(f.key_name)
    target = f.hub if a.node < 0 else next((m for m in f.workers if m.index == a.node), None)
    if target is None or not target.public_ip:
        raise FleetError(f"node {a.node} is not reachable")
    what = (f"sudo journalctl -u ampi-hub -n {a.lines} --no-pager" if a.node < 0 else
            f"tail -n {a.lines} $AMPI_REPO_DIR/work/aws/{shlex.quote(a.name)}-node{a.node}.log")
    cmd = "set -a; . /etc/ampi.node; set +a; " + what
    r = ssh_cmd(key, target.public_ip, cmd, timeout=90)
    print(r.stdout or r.stderr)
    return r.returncode


def cmd_collect(a: argparse.Namespace) -> int:
    """Bring the evidence home.

    Node 0 exports the trace and the diagnosis; every node has a launch record
    naming the machine it ran on, and the fleet record is only a claim about
    which machines were asked for until those are beside it.
    """
    f = Fleet.load(a.name)
    _refresh(Aws(f.region), f)
    key = key_path(f.key_name)
    dest = run_dir(a.name)
    (dest / "launch").mkdir(parents=True, exist_ok=True)
    zero = next((m for m in f.workers if m.index == 0 and m.public_ip), None)
    if zero is None:
        raise FleetError("node 0 is not reachable; it holds the exported run")
    scp = ["scp", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new",
           "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-r",
           f"ec2-user@{zero.public_ip}:/opt/ampi/AgentMPI/runs/{a.name}/.", str(dest)]
    r = subprocess.run(scp, capture_output=True, text=True, timeout=1800)
    print(f"node 0 run directory -> {dest}" if r.returncode == 0 else
          f"scp failed: {r.stderr.strip()}")

    for m in f.workers:
        if not m.public_ip:
            continue
        one = ["scp", "-i", str(key), "-o", "StrictHostKeyChecking=accept-new",
               "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
               f"ec2-user@{m.public_ip}:/opt/ampi/AgentMPI/work/aws/{a.name}-node{m.index}.log",
               str(dest / "launch" / f"node{m.index}.log")]
        subprocess.run(one, capture_output=True, text=True, timeout=300)
    print(f"per-node logs -> {dest / 'launch'}")
    return 0


def cmd_down(a: argparse.Namespace) -> int:
    f = Fleet.load(a.name)
    aws = Aws(f.region)
    live = aws.describe(f.name)
    ids = [i["InstanceId"] for i in live]
    if not ids and not f.created_network:
        print(f"nothing tagged {TAG_JOB}={f.name} is running")
        return 0
    print(f"{len(ids)} instances tagged {TAG_JOB}={f.name} in {f.region}:")
    for i in live:
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        print(f"  {i['InstanceId']:<20} {i['InstanceType']:<14} "
              f"{tags.get(TAG_ROLE, '?'):<7} {i['State']['Name']}")
    if not a.yes:
        reply = input(f"terminate these and delete their volumes? type the job name "
                      f"({f.name}) to confirm: ").strip()
        if reply != f.name:
            print("not confirmed; nothing done")
            return 1
    if ids:
        aws.terminate(ids)
        print("terminating; waiting for the instances to go away ...")
        aws.ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
    if f.created_network and not a.keep_network:
        print("deleting the network ...")
        for line in aws.destroy_network(f):
            print(f"  {line}")
    _refresh(aws, f)
    f.save()
    print("down.  Volumes were DeleteOnTermination, so nothing is still billing.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fleet",
                                 description="the machines an AgentMPI job runs on")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", required=True, help="the job name; also the resource tag")
        p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))

    p = sub.add_parser("plan", help="what it would create, and what it would cost")
    common(p)
    p.add_argument("--nodes", type=int, default=32)
    p.add_argument("--worker-type", default="t4g.small")
    p.add_argument("--hub-type", default="c7g.large")
    p.add_argument("--hours", type=float, default=4.0)
    p.add_argument("--disk-gb", type=int, default=20)
    p.add_argument("--spot", action="store_true")
    p.add_argument("--api-spend", type=float, default=0.0)
    p.add_argument("--ssh-cidr", default="0.0.0.0/0")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("up", help="create the network and launch the fleet")
    common(p)
    p.add_argument("--nodes", type=int, default=32)
    p.add_argument("--worker-type", default="t4g.small")
    p.add_argument("--hub-type", default="c7g.large")
    p.add_argument("--disk-gb", type=int, default=20)
    p.add_argument("--spot", action="store_true", help="workers on spot; the hub never is")
    p.add_argument("--az", default=None, help="pin the AZ; one AZ, for latency and for egress")
    p.add_argument("--key-name", default=None, help="EC2 key pair; created if absent")
    p.add_argument("--ssh-cidr", default=None,
                   help="who may ssh in; defaults to this machine's address /32")
    p.add_argument("--repo-url", default="https://github.com/agentmpi/AgentMPI")
    p.add_argument("--repo-ref", default="aws/production_exp")
    p.add_argument("--subnet-id", default="", help="use an existing subnet instead")
    p.add_argument("--sg-id", default="")
    p.add_argument("--vpc-id", default="")
    p.set_defaults(fn=cmd_up)

    p = sub.add_parser("status", help="what the fleet is doing")
    common(p)
    p.add_argument("--no-probe", action="store_true", help="skip the ssh readiness check")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("env", help="push credentials and start the hub")
    common(p)
    p.add_argument("--api-key-env", default="OPENROUTER_API_KEY",
                   help="the variable in THIS shell holding the provider credential")
    p.set_defaults(fn=cmd_env)

    p = sub.add_parser("run", help="start the job on every node")
    common(p)
    p.add_argument("--size", type=int, required=True, help="total ranks")
    p.add_argument("--module", default="experiments.e7_rawapi_book.harness")
    p.add_argument("--executor", default="model")
    p.add_argument("--model", default="")
    p.add_argument("--extra", default="--reasoning low --respawn 1 --task-timeout 1800 "
                                      "--phase-timeout 7200 --lease 1800 -q")
    p.add_argument("--stagger", type=float, default=20.0,
                   help="seconds to let node 0 create the job before the others join")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("logs", help="tail a node's log, or the hub's journal")
    common(p)
    p.add_argument("--node", type=int, default=-1, help="-1 for the hub")
    p.add_argument("--lines", type=int, default=80)
    p.set_defaults(fn=cmd_logs)

    p = sub.add_parser("collect", help="bring the run's evidence home")
    common(p)
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("down", help="terminate everything tagged with this job")
    common(p)
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.add_argument("--keep-network", action="store_true")
    p.set_defaults(fn=cmd_down)

    a = ap.parse_args(argv)
    if getattr(a, "key_name", None) is None and a.cmd == "up":
        a.key_name = f"ampi-{a.name}"
    if getattr(a, "ssh_cidr", None) is None:
        a.ssh_cidr = _my_cidr()
    try:
        return int(a.fn(a) or 0)
    except FleetError as exc:
        print(f"fleet: {exc}", file=sys.stderr)
        return 2


def _my_cidr() -> str:
    """This machine's public address as a /32, so the security group is not
    written open to the internet by default."""
    import urllib.request
    try:
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
            return r.read().decode().strip() + "/32"
    except Exception:  # noqa: BLE001 - the operator can pass --ssh-cidr
        print("could not determine this machine's address; pass --ssh-cidr explicitly",
              file=sys.stderr)
        return "0.0.0.0/0"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
