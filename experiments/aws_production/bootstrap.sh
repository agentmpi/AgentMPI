#!/usr/bin/env bash
# Cloud-init user-data: bring one fleet instance to the point where it can be
# given a job, and no further.
#
# What this script deliberately does NOT do:
#
#   * It does not carry a credential.  User-data is readable by anything that
#     can reach the instance metadata service, and it is stored unencrypted on
#     the instance, so a model-provider key placed here would be readable by
#     every process on the box.  `fleet.py env` pushes the key over SSH after
#     the instance is up, into a root-owned file; `fleet.py run` sources it.
#   * It does not start a rank.  A node that starts work when it happens to
#     finish booting joins whatever job is on the hub, which is how the git
#     runs joined the previous job by accident (NODES.md, "Node 0 first,
#     always").  The operator starts the job, once, when the fleet is up.
#
# It writes /var/lib/ampi/ready when it is done, and `fleet.py status` reads it:
# an instance in the "running" state is a booted machine, not a prepared one,
# and the difference is about ninety seconds of package installation.
set -euxo pipefail

REPO_URL="__REPO_URL__"
REPO_REF="__REPO_REF__"
HUB_ADDR="__HUB_ADDR__"
NODE_INDEX="__NODE_INDEX__"
NODE_COUNT="__NODE_COUNT__"
ROLE="__ROLE__"

exec > >(tee -a /var/log/ampi-bootstrap.log) 2>&1
mkdir -p /var/lib/ampi
echo "bootstrap starting: role=$ROLE node=$NODE_INDEX/$NODE_COUNT ref=$REPO_REF"

dnf -y update --security || true
dnf -y install git python3.11 python3.11-pip python3.11-devel gcc tar gzip jq

# The clone is the operator's own repository at a pinned ref, because every node
# must run the same bytes: a fleet where one node is a commit behind is a fleet
# whose ranks disagree about the protocol, and the job manifest will refuse it.
install -d -o ec2-user -g ec2-user /opt/ampi
sudo -u ec2-user git clone --depth 50 "$REPO_URL" /opt/ampi/AgentMPI
cd /opt/ampi/AgentMPI
sudo -u ec2-user git checkout "$REPO_REF"
sudo -u ec2-user git rev-parse HEAD > /var/lib/ampi/commit

sudo -u ec2-user python3.11 -m venv .venv
sudo -u ec2-user .venv/bin/pip -q install --upgrade pip
sudo -u ec2-user .venv/bin/pip -q install -e '.[tokens]'

# The node's identity, read by fleet.py run and by the harness.  AMPI_HUB_ADDR
# is the whole of the transport configuration: there is no remote, no branch and
# no working tree, because the state is one process at that address.
cat > /etc/ampi.node <<EOF
AMPI_NODE_INDEX=$NODE_INDEX
AMPI_NODE_COUNT=$NODE_COUNT
AMPI_NODE_ROLE=$ROLE
AMPI_DEVICE=hub
AMPI_HUB_ADDR=$HUB_ADDR
AMPI_REPO_DIR=/opt/ampi/AgentMPI
EOF
chmod 0644 /etc/ampi.node

# Clock discipline.  A lease is a time comparison and a conviction is a
# judgement about someone else's lease, so a fleet whose clocks disagree
# convicts the living.  The hub device reads the hub's clock rather than the
# local one, which makes this belt-and-braces --- but a machine whose clock is
# minutes out also writes misleading timestamps into the trace, and the trace is
# the evidence.  Amazon's link-local NTP service needs no egress.
dnf -y install chrony
sed -i '1i server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4' /etc/chrony.conf
systemctl enable --now chronyd
chronyc -a makestep || true

# Ranks are processes and each holds a socket to the hub; the default 1024
# descriptors is under what a 32-rank node wants once the interpreter, the
# trace files and the TLS sessions are counted.
cat > /etc/security/limits.d/90-ampi.conf <<'EOF'
ec2-user soft nofile 65535
ec2-user hard nofile 65535
EOF
echo 'DefaultLimitNOFILE=65535' >> /etc/systemd/system.conf

sysctl -w net.ipv4.tcp_keepalive_time=60 >/dev/null
echo 'net.ipv4.tcp_keepalive_time = 60' > /etc/sysctl.d/90-ampi.conf

if [ "$ROLE" = "hub" ]; then
    # The hub is a service, not a command someone typed: it must come back if it
    # crashes and it must come back if the instance reboots, because the job's
    # state is behind it and a rank that cannot reach it is a rank that stalls.
    # AMPI_HUB_TOKEN is written later by `fleet.py env`; the unit reads it then.
    cat > /etc/systemd/system/ampi-hub.service <<'EOF'
[Unit]
Description=AgentMPI hub: the job's state, served to every node
After=network-online.target chronyd.service
Wants=network-online.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/ampi/AgentMPI
EnvironmentFile=/etc/ampi.node
EnvironmentFile=-/etc/ampi.secrets
ExecStart=/opt/ampi/AgentMPI/.venv/bin/python -m ampi.device.hub \
    --root /opt/ampi/state/job --host 0.0.0.0 --port 7411
Restart=always
RestartSec=2
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
    install -d -o ec2-user -g ec2-user /opt/ampi/state
    systemctl daemon-reload
    systemctl enable ampi-hub.service
    # Not started here: it is started once the token exists, by `fleet.py env`.
fi

install -d -o ec2-user -g ec2-user /opt/ampi/work
date -Is > /var/lib/ampi/ready
echo "bootstrap complete"
