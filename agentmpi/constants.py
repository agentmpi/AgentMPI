"""Protocol constants. Names follow MPI so harness authors can transfer intuition."""

PROTOCOL_VERSION = "agentmpi/1.0"
COMM_WORLD_NAME = "world"

ANY_SOURCE = -1
ANY_TAG = -1

# MPI-style reserved tags for runtime control. User tags are >= 0 and < TAG_UB.
TAG_UB = 32767
TAG_HEARTBEAT = TAG_UB + 1
TAG_RTS = TAG_UB + 2  # request-to-send (rendezvous)
TAG_CTS = TAG_UB + 3  # clear-to-send
TAG_COLLECTIVE = TAG_UB + 10
TAG_FAULT = TAG_UB + 20
TAG_SPAWN = TAG_UB + 30
TAG_CONTEXT = TAG_UB + 40

LOCK_SHARED = "shared"
LOCK_EXCLUSIVE = "exclusive"

# Default eager threshold: payloads larger than this use rendezvous
# (envelope + out-of-band artifact), mirroring MPI eager/rendezvous.
DEFAULT_EAGER_BYTES = 8192

# Default per-rank context budget in estimated tokens (OOM analog).
DEFAULT_CONTEXT_BUDGET = 16_000

# Heartbeat / failure detector.
DEFAULT_HEARTBEAT_S = 2.0
DEFAULT_FAILURE_TIMEOUT_S = 20.0

# File-transport poll interval.
DEFAULT_POLL_S = 0.02
