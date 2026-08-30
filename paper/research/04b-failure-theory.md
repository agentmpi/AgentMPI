# 04b — Failure Detection, Agreement, Recovery, and Idempotence Theory

*Background note for the AgentMPI paper. Scope: the classical distributed-systems results that
justify a lease-based failure detector, fencing-token-guarded effects, checkpoint/replay recovery,
and supervision-tree restart policy for LLM multi-agent harnesses.*

---

## 1. Failure detection theory

### 1.1 The FLP impossibility result

Fischer, Lynch and Paterson [flp1985impossibility] proved that in an **asynchronous** message-passing
system — no bound on message delay, no bound on relative process speed, no synchronized clocks — there
is **no deterministic algorithm that solves consensus** in the presence of even a *single* crash fault.
The result is remarkably strong: the failure model is the most benign possible (one process fails by
stopping), messages are never lost or reordered maliciously, and the agreement requirement is the
weakest interesting one (all correct processes decide the same value; the value is one that was
proposed; every correct process eventually decides). Even so, no protocol can guarantee **termination**.

The proof structure is worth restating because it is the source of the "slow vs. dead" intuition.
A configuration is *bivalent* if both decision values are still reachable from it. FLP shows (i) some
initial configuration is bivalent, and (ii) from any bivalent configuration an adversarial scheduler
can always delay one message so as to reach another bivalent configuration. The scheduler therefore
constructs an infinite non-deciding run. The adversary's only power is **delay** — it never crashes
anyone. This is exactly the operational point: an asynchronous protocol cannot tell "this process has
crashed" from "this process's message is still in flight," and any protocol that commits to one
interpretation can be made wrong.

Two standard escapes exist. The first is **randomization** (Ben-Or [benor1983another]), which trades
deterministic termination for termination with probability 1. The second — the one that matters for
AgentMPI — is to **augment the asynchronous model with a failure detector**, i.e. to encapsulate the
extra timing assumptions in a single oracle module rather than smearing timeouts through the protocol.

### 1.2 Chandra & Toueg: unreliable failure detectors

Chandra and Toueg [chandra1996unreliable] model a failure detector as a distributed oracle: each
process $p$ has a local module that outputs a set of processes it currently *suspects* to have
crashed. The module is **unreliable** — it may suspect correct processes and may fail to suspect
crashed ones, and different modules may disagree at the same instant. Detectors are classified by two
orthogonal properties.

**Completeness** (do crashed processes eventually get suspected?)

- **Strong completeness**: eventually *every* process that crashes is permanently suspected by *every*
  correct process.
- **Weak completeness**: eventually every process that crashes is permanently suspected by *some*
  correct process.

Chandra and Toueg show weak completeness can be boosted to strong completeness by a simple gossip
reduction that preserves the accuracy class, so completeness is not the interesting axis.

**Accuracy** (are correct processes wrongly suspected?)

- **Strong accuracy**: *no* process is suspected before it crashes (by anyone).
- **Weak accuracy**: *some* correct process is never suspected by anyone.
- **Eventual strong accuracy**: there is a time after which correct processes are not suspected by any
  correct process.
- **Eventual weak accuracy**: there is a time after which *some* correct process is not suspected by
  any correct process.

The eight combinations collapse (via the completeness boosting reduction) into the classical grid of
**eight named classes**, of which these are the canonical six:

| Class | Name | Completeness | Accuracy |
|---|---|---|---|
| **P** | Perfect | Strong | Strong |
| **Q** | (unnamed, rarely used) | Weak | Strong |
| **S** | Strong | Strong | Weak |
| **W** | Weak | Weak | Weak |
| **◇P** | Eventually Perfect | Strong | Eventual strong |
| **◇Q** | (unnamed) | Weak | Eventual strong |
| **◇S** | Eventually Strong | Strong | Eventual weak |
| **◇W** | Eventually Weak | Weak | Eventual weak |

Note carefully that "Strong"/"Weak" in the *class name* refers to **completeness**, while the
accuracy property is what distinguishes P from S and ◇P from ◇S. This is a persistent source of
confusion in secondary literature.

Key results from [chandra1996unreliable]:

1. **Consensus is solvable with S** (weak accuracy, strong completeness) for any number of faults
   $f < n$, and with **W** likewise, since W can be reduced to S.
2. **Consensus is solvable with ◇S** — and hence ◇W — but **only if a majority of processes are
   correct** ($f < n/2$). The majority requirement is not an artifact: with ◇S and $f \geq n/2$,
   consensus is impossible.
3. **◇W is the weakest failure detector for consensus** — this is the celebrated result of Chandra,
   Hadzilacos and Toueg [chandra1996weakest]: any failure detector sufficient to solve consensus can
   be transformed into ◇W. ◇W is equivalent to **Ω**, the eventual leader oracle that eventually
   outputs the same correct process at all correct processes; Ω is the formulation actually used by
   Paxos-family protocols [lamport1998parttime].

The deep point: **◇P and ◇S are implementable in a partially synchronous system**
[dwork1988consensus] — one in which bounds on message delay and processing speed exist but are
unknown, or hold only after some unknown Global Stabilization Time (GST). A timeout-based detector
whose timeout **increases** on every false suspicion will eventually stop making mistakes once the
actual bound is exceeded, giving eventual accuracy. **P is not implementable** in partial synchrony:
strong accuracy requires *never* wrongly suspecting, which requires a known, a-priori-correct upper
bound on delay — i.e. genuine synchrony.

### 1.3 Heartbeats and the phi-accrual detector

The canonical implementation is the **heartbeat** detector: each process periodically emits an
"I-am-alive" message with period $\Delta_i$; a monitor suspects $p$ if no heartbeat arrives within a
timeout $\Delta_{to}$. This yields the classic binary trade-off curve between **detection time**
$T_D$ and **mistake rate** $\lambda_M$ / **mistake duration** $T_M$, the QoS metrics formalized by
Chen, Toueg and Aguilera [chen2002quality]. Short timeouts detect fast but generate false
suspicions under transient load; long timeouts are accurate but slow. There is no setting that is
good at both, because the underlying delay distribution is heavy-tailed.

Hayashibara, Défago, Yared and Katayama [hayashibara2004phi] proposed the **φ-accrual failure
detector**, which decouples *monitoring* from *interpretation*. Instead of emitting a boolean
suspect/trust verdict, the detector outputs a continuous suspicion level

$$\varphi(t_{now}) = -\log_{10}\big(P_{later}(t_{now} - T_{last})\big)$$

where $T_{last}$ is the arrival time of the most recent heartbeat and $P_{later}(t)$ is the
probability, estimated from a sliding window of observed inter-arrival times (Hayashibara et al. fit a
normal distribution to the window), that a heartbeat will arrive more than $t$ time units after the
previous one. So $\varphi = 1$ means roughly a 10% chance the process is actually alive and merely
slow; $\varphi = 2$ means ~1%; $\varphi = 3$ means ~0.1%. Each *application* then chooses its own
threshold $\Phi$, so a latency-sensitive consumer and a safety-critical consumer can share one
monitoring stream with different aggressiveness. φ-accrual is widely deployed — Akka's cluster
membership and Cassandra's gossip failure detector both implement it [UNVERIFIED: exact Cassandra
implementation details have drifted across versions; verify against current source before citing a
specific formula].

The accrual formulation is *adaptive*: because $P_{later}$ is estimated from recent samples, the
detector automatically widens under sustained load, which is precisely the "increase the timeout after
a mistake" behaviour required to realize ◇P in partial synchrony.

### 1.4 The impossibility of distinguishing "slow" from "dead"

Stated sharply: **in a purely asynchronous system, the predicates "p has crashed" and "p's next
message has not yet arrived" are observationally indistinguishable at every finite prefix of a run.**
Any local decision procedure that outputs "crashed" at some finite time $t$ can be fed a run in which
$p$ is correct but every message from $p$ is delayed past $t$; the procedure produces a false
positive. Conversely, a procedure that never outputs "crashed" is trivially accurate but useless.
This is the operational content of FLP [flp1985impossibility] and the reason Chandra–Toueg's oracle
is defined to be *unreliable* rather than assumed away.

The engineering consequence is that failure detection is not a correctness mechanism — it is a
**liveness** mechanism. Correctness must be preserved even when the detector is wrong. This is the
single most important theorem for AgentMPI's design: the detector may declare an agent rank dead
while that rank is merely stalled in a long tool call, and the *safety* of the system must not depend
on that judgment being right. Safety comes from fencing (§2), not from detection.

### 1.5 What class does a lease-based, lazily-evaluated detector implement?

A **lease-based** detector grants each rank a lease with expiry time $T_{exp}$; the rank is considered
live iff `now < T_exp`, and it renews by heartbeating before expiry. **Lazily evaluated** means the
predicate is computed only when someone asks (at a collective boundary, at a scheduling decision, at
message delivery) rather than by a background sweeper.

Classification:

- **Completeness.** A crashed rank stops renewing, so its lease expires and *every* observer that
  evaluates the predicate after $T_{exp}$ suspects it. Under lazy evaluation this is
  **strong completeness only if every correct process eventually evaluates the predicate** — a
  liveness obligation on the *harness*, not on the detector. If a rank is never queried again, it is
  never suspected. In practice AgentMPI's scheduler evaluates leases at every dispatch, which
  discharges the obligation; but it should be stated as an explicit assumption. With that assumption,
  **strong completeness** holds.
- **Accuracy.** A slow-but-correct rank whose renewal is delayed past $T_{exp}$ is wrongly suspected.
  With a *fixed* lease duration and no adaptation, the detector has **no eventual accuracy guarantee
  at all** in an asynchronous system: an adversarial delay pattern can produce false suspicions
  forever. Its class is therefore only the trivial one. With an **adaptive** lease duration that grows
  after each false suspicion (or an accrual estimator [hayashibara2004phi]), and under a
  **partial-synchrony** assumption [dwork1988consensus] — an eventual unknown bound on renewal
  round-trip delay after GST — the lease eventually exceeds the true bound and false suspicions cease
  permanently at all observers.

**Therefore: a lease-based, lazily-evaluated detector with adaptive lease duration implements ◇P
(Eventually Perfect) under partial synchrony, provided the harness guarantees every correct rank is
eventually evaluated.** It does *not* implement P, because P requires strong accuracy — never
suspecting a correct process — which requires a known hard upper bound on the renewal path (clock
skew + network + agent scheduling + LLM inference latency). Bounding LLM inference latency a priori is
not defensible, so P is out of reach.

Two important corollaries. First, since ◇P ⊆ ◇S and ◇S solves consensus only with $f < n/2$
[chandra1996unreliable], any AgentMPI *agreement* built on this detector inherits a **majority-correct
requirement**. Second, ◇P is enough for **eventually-accurate membership**, but during the pre-GST
window the system must tolerate false suspicions without corrupting state — again pointing at fencing
(§2) rather than detection as the safety mechanism.

A useful sharpening: if the lease is enforced by the *grantor* (the coordinator refuses to accept work
from a rank whose lease it has expired) rather than merely observed by the grantee, the mechanism
becomes closer to a **failure detector + mutual exclusion primitive** than a pure oracle. That is what
makes lease-based designs safe in practice: the lease is simultaneously a suspicion signal *and* an
authorization that can be revoked, which is the bridge to §2.

---

## 2. Leases and fencing tokens

### 2.1 Leases (Gray & Cheriton, 1989)

Gray and Cheriton [gray1989leases] introduced the **lease** in the context of distributed file cache
consistency: a lease is *a lock on a resource that is valid for a bounded period of time*. A server
grants a client a lease on a cached datum; while the lease is valid the server promises not to modify
the datum without first notifying (or waiting out) the lease holder. The critical innovation over a
plain lock is **automatic expiry**: if the client crashes or becomes unreachable, the server does not
have to run a failure detector or a recovery protocol — it simply waits for the lease term to elapse
and then proceeds. Leases thereby convert an *unbounded* liveness problem (waiting for a possibly-dead
client to release a lock) into a *bounded* one (waiting out a timer), at the cost of introducing a
timing assumption.

The lease's correctness argument requires two things:

1. **Bounded clock drift** between grantor and grantee (or a conservative one-sided margin: the
   grantee treats its lease as expiring earlier than the grantor does, so that from the grantor's
   perspective the grantee has certainly stopped acting before the lease is reissued).
2. **Bounded delay between the grantee's decision to act and the effect landing at the resource.**

Requirement (2) is the one that breaks in practice, and it is what motivates fencing.

Gray and Cheriton also analyze the lease-term trade-off directly: short terms minimize the false-
sharing / write-delay cost when a holder fails, but increase renewal traffic; long terms amortize
renewal but lengthen recovery. This is the same $T_D$ / overhead trade-off as in §1.3, and the same
one AgentMPI faces in choosing rank lease durations against LLM inference latency.

### 2.2 Why a lease alone is unsafe

Consider a lease held by rank $A$ over resource $R$, with expiry $T_{exp}$. The intended protocol is:

```
1. A acquires lease, learns T_exp
2. A checks now < T_exp                 // "I still hold the lease"
3. A issues write W to R
```

The safety claim would be: at the moment $W$ is applied at $R$, $A$ held the lease. But **steps 2 and
3 are not atomic, and neither is the network path from $A$ to $R$.** Between the check and the effect,
an arbitrary delay $\delta$ can be inserted by:

- a stop-the-world GC pause (Kleppmann [kleppmann2016locking] emphasizes multi-minute JVM GC pauses as
  the canonical example);
- OS preemption, page-fault storms, swapping, a hypervisor live-migration stun;
- network delay or a retry of $W$ at a lower layer;
- **in the AgentMPI setting: a long-running tool call, a rate-limit backoff, or a multi-minute LLM
  inference stall.**

If $\delta$ pushes the arrival of $W$ past $T_{exp}$, the coordinator has by then granted the lease to
rank $B$, and $B$ has issued its own write $W'$. The resource now sees $W'$ then $W$ (or interleaved),
i.e. a write from a **zombie** holder that believes, sincerely and by a locally-correct check, that it
is still the owner. Mutual exclusion is violated *even though no component behaved incorrectly*.

This is not a bug in the lease; it is a **theorem-level consequence of §1.4**. The lease holder cannot
determine, locally and at finite time, whether its own effects will land before expiry, because that
would require an a-priori bound on its own scheduling delay — exactly the synchrony assumption that
does not hold. Kleppmann's sharpening [kleppmann2016locking, kleppmann2017ddia] is that a lock/lease
protocol that produces only a *boolean* ("you hold it") is structurally unable to be safe, and that
the failing is independent of how good the lock service's consensus is: he notes that Redlock's
deeper problem is not merely its clock assumptions but that "it does not have any facility for
generating fencing tokens," so that even an otherwise-perfect algorithm "would not be safe to use."

### 2.3 Fencing tokens: moving enforcement to the resource

The fix [kleppmann2016locking] is to have the lock service issue, with every grant, a
**monotonically increasing token** — a number that strictly increases on every acquisition of that
logical lock, cluster-wide. Every write to the protected resource carries its token, and **the
resource maintains the highest token it has ever accepted and rejects any write bearing a token less
than or equal to it.**

The safety argument is now purely order-theoretic and requires **no timing assumption whatsoever**:

- Let $t_A < t_B$ be the tokens of successive grants (guaranteed by monotonicity at the grantor,
  which is the only place a consensus/linearizability requirement remains).
- Suppose $B$'s write lands first. The resource's high-water mark becomes $\geq t_B > t_A$.
- $A$'s delayed zombie write arrives with $t_A$, is compared against the mark, and is **rejected**.
- If instead $A$'s write lands first, it is accepted (correctly — $A$ genuinely held the lease at that
  point in the resource's own serial order) and $B$'s later write with $t_B$ is also accepted.

In every interleaving, the resource's accepted-write sequence is consistent with a single serial order
of lease epochs. Note what changed: the enforcement point moved from the **client's local clock check**
(unsound, §1.4) to the **resource's own state** (sound, because the resource is the serialization point
for its own writes). Slogan: *the lock service is not the lock; the resource's rejection of stale
tokens is the lock.*

Three practical corollaries that AgentMPI must respect:

1. **The resource must participate.** A fencing token needs somewhere to land: a compare-and-swap on a
   version column, a conditional PUT (`If-Match` / object-store preconditions), or an explicit
   high-water-mark table. If a downstream side effect cannot do a conditional write, the lock is
   best-effort regardless of the lock service's quality [kleppmann2016locking].
2. **Monotonicity must be globally linearizable per logical lock.** Generating tokens from a
   non-linearizable store reintroduces the race at a different layer.
3. **Fencing composes with idempotency but is not the same thing.** An idempotency key deduplicates
   *retries of the same operation*; a fencing token *orders distinct epochs*. §4 needs both.

### 2.4 Chubby sequencers — fencing in production

Burrows' Chubby [burrows2006chubby] is the reference production implementation. A Chubby lock holder
may at any time call `GetSequencer`, which returns "an opaque byte-string that describes the state of
the lock immediately after acquisition. It contains the name of the lock, the mode in which it was
acquired (exclusive or shared), and the lock **generation number**." The client passes the sequencer
along with each request to a downstream server (e.g. a file server); the server validates it — either
against its own Chubby cache/session, or, if it does not want to maintain a Chubby session, **against
the most recent sequencer it has itself observed** — and rejects the request if the sequencer is stale.
That second option is precisely the high-water-mark rule of §2.3, and Burrows notes the mechanism
"requires only the addition of a string to affected messages."

Chubby also documents the fallback for downstreams that cannot be modified: **lock-delay**. If a lock
becomes free because its holder *failed or became inaccessible* (as opposed to releasing normally),
Chubby prevents any other client from claiming it for a configurable `lock-delay` period, bounded at
about one minute [burrows2006chubby]. Burrows is explicit that this is "imperfect": it merely makes
the zombie-write window unlikely rather than impossible, and it trades availability for a probabilistic
safety margin. The bound on the delay exists so that a faulty client cannot make a resource
unavailable for an arbitrarily long time. Chubby's own framing — sequencers are the correct mechanism,
lock-delay is the pragmatic degradation for legacy servers — is exactly the position AgentMPI should
adopt: fence where the effect target can be modified; use a delay/quarantine window only where it
cannot, and label that path as best-effort.

Chubby further illustrates the §1 story at the system level: Chubby's client sessions are themselves
**leases** maintained by KeepAlive RPCs, with a *grace period* during which a client whose session
lease has expired locally holds its handles in a "jeopardy" state before declaring the session expired.
That is a ◇P-flavored detector with an explicitly widened accuracy margin.

### 2.5 ZooKeeper and etcd: sessions, ephemerals, and where the token comes from

ZooKeeper [hunt2010zookeeper] provides the same primitives in a different shape. A client holds a
**session** kept alive by heartbeats; **ephemeral znodes** are automatically deleted when the session
expires, which gives lock release-on-failure without an explicit detector. The standard lock recipe has
each contender create an ephemeral **sequential** znode under a lock parent and take the lock when its
sequence number is lowest, watching its immediate predecessor to avoid a herd.

Crucially, ZooKeeper supplies a natural fencing token: every state change is assigned a
**zxid** (ZooKeeper transaction id), monotonically increasing and totally ordered by the ZAB atomic
broadcast protocol [junqueira2011zab]; the `czxid` of the created lock znode, or equivalently the
znode's sequence number, is a monotone per-lock epoch number. etcd exposes the same thing as the
cluster **revision** and lease IDs. The point for AgentMPI: *if a coordination service is used, take
the token from the service's own linearizable counter rather than inventing one*, since only the
service's counter is guaranteed monotone across the failover of the coordinator itself.

A frequently-missed subtlety [kleppmann2016locking]: ZooKeeper's session expiry is decided by the
*ensemble*, not by the client, and there is an unavoidable window in which a client believes its
session is alive while the ensemble has already expired it and handed the lock on. Ephemeral nodes
therefore give *automatic release*, not *automatic safety*. Safety still requires fencing at the
resource.

### 2.6 Summary of the argument for AgentMPI's design

- A lease bounds the **liveness** cost of a failed rank without requiring a perfect failure detector
  [gray1989leases]; this is why a lease-based ◇P detector (§1.5) is the right choice.
- A lease **cannot** provide safety alone, because no rank can bound the delay between its own
  liveness check and the landing of its side effects — a direct consequence of asynchrony
  [flp1985impossibility, kleppmann2016locking].
- A **monotone fencing token**, checked and stored at the effect target, closes the race with *no*
  timing assumption [kleppmann2016locking, burrows2006chubby]. It converts "was I alive?" (undecidable
  locally) into "am I the newest epoch this resource has seen?" (decidable at the resource).
- Therefore AgentMPI should attach a monotone **rank epoch** to every externally-visible effect a rank
  performs (tool invocation with side effects, artifact write, message emission into a collective), and
  every effect sink must reject stale epochs. Restarting a rank must **increment** its epoch, never
  reuse it.

---

## 3. Rollback recovery

### 3.1 The survey and its taxonomy

Elnozahy, Alvisi, Wang and Johnson [elnozahy2002survey] is the canonical survey of rollback-recovery
in message-passing systems. Its framing: a distributed computation's state is the set of process
states plus the state of the channels; recovery means restoring the system to a **consistent global
state** and resuming. A global state is *consistent* if, for every message recorded as **received**,
its **send** is also recorded — i.e. no **orphan messages** (received but not sent, which would imply
the receiver's state depends on a send that recovery has erased). Messages recorded as sent but not
received are **in-transit** messages, which are legal in a consistent cut and must be either replayed
or recorded in the channel state.

The survey's central organizing device is the **piecewise deterministic (PWD) assumption**: process
execution is a sequence of deterministic state intervals, each begun by a **nondeterministic event**
(most importantly, message receipt, but also random numbers, clock reads, and I/O). If the system can
capture and replay the *determinants* of all nondeterministic events, then re-executing from a
checkpoint reconstructs the pre-failure state exactly. PWD is the theoretical foundation for message
logging and, as §4 shows, for durable-execution replay. **PWD is precisely the assumption that a
stochastic LLM call violates unless the call's result is logged**, which is the single most important
mapping from this literature to AgentMPI.

### 3.2 Checkpoint-based protocols

**Uncoordinated checkpointing.** Each process checkpoints independently, whenever convenient (e.g.
when its state is small). Advantage: no coordination overhead, maximum autonomy. Disadvantages
[elnozahy2002survey]:

- **The domino effect** (Randell [randell1975system]): because checkpoints are not aligned across
  processes, recovery must search for a consistent *recovery line*. A failure can force a rollback that
  invalidates another process's checkpoint (orphan message), forcing that process back further, which
  in turn invalidates a third checkpoint, and so on cascading — in the worst case all the way to the
  **initial state**, discarding the entire computation.
- **Useless checkpoints** — checkpoints that can never be part of any recovery line, so their cost is
  pure waste.
- **Garbage collection is complex**: multiple checkpoints per process must be retained, and determining
  which are collectable requires computing the recovery line via a rollback-dependency graph or
  checkpoint graph.

**Coordinated checkpointing.** Processes synchronize so that the saved checkpoints jointly form a
consistent global state. Advantages: only the most recent checkpoint set need be retained (simple
garbage collection), **no domino effect**, and simple recovery (everyone restarts from the last
coordinated checkpoint). Disadvantage: the coordination itself has latency and forces a global
synchronization; on large machines the cost is dominated by simultaneous I/O to the parallel file
system. The classic blocking approach quiesces channels; the classic non-blocking approach is
Chandy–Lamport (§3.3). Variants include **checkpointing with synchronized clocks** (loosely
synchronized clocks trigger checkpoints at approximately the same time, reducing message exchange) and
**minimal-coordination** schemes that only involve processes that have communicated since the last
checkpoint.

**Communication-induced checkpointing (CIC).** A hybrid: processes take local (autonomous)
checkpoints but piggyback protocol information on application messages, and take additional *forced*
checkpoints when the piggybacked information indicates a checkpoint is needed to prevent useless
checkpoints / domino. CIC avoids explicit coordination messages but the survey notes its forced
checkpoints can be unpredictable and its practical performance disappointing at scale
[elnozahy2002survey].

### 3.3 Chandy–Lamport consistent global snapshots

Chandy and Lamport [chandy1985snapshots] give the foundational **non-blocking** algorithm for
recording a consistent global state of a distributed system whose channels are reliable and FIFO. The
algorithm:

1. An initiator records its own local state and then sends a **marker** on every outgoing channel
   before sending any further application message.
2. When process $p$ receives a marker on channel $c$: if $p$ has not yet recorded its state, it records
   its state, records the state of $c$ as **empty**, and sends markers on all its outgoing channels
   before further application messages. Otherwise, it records the state of $c$ as the sequence of
   application messages received on $c$ **after** $p$ recorded its state and **before** the marker
   arrived.
3. The snapshot is complete at $p$ once a marker has been received on every incoming channel.

The recorded global state is guaranteed consistent — it is a cut with no orphan messages — but,
critically, **it may never have actually occurred** as an instantaneous system state. Chandy and
Lamport show the recorded state $S^*$ is reachable from the pre-snapshot state $S_i$ and that the
post-snapshot state $S_\phi$ is reachable from $S^*$, so $S^*$ is a legitimate state of *some*
equivalent execution. That is exactly what is needed for recovery and for evaluating **stable
properties** (termination detection, deadlock detection, garbage collection) — and, notably for
AgentMPI, **deadlock inside a collective is a stable property and is therefore detectable by a
snapshot**. Chandy–Lamport is also the algorithmic core of Flink's asynchronous barrier snapshotting
[carbone2015flink], which is the closest modern streaming analogue of a checkpointed agent dataflow.

### 3.4 Log-based rollback recovery (message logging)

Message logging combines checkpointing with logging of nondeterministic event determinants, relying on
PWD. Its distinguishing benefit over pure checkpointing is that it supports the **output commit**
problem cheaply — a process may interact with the outside world (send output that cannot be un-sent)
once it can guarantee it can be reconstructed. Log-based protocols also generally avoid the domino
effect and permit recovery of a single failed process without rolling back survivors
[elnozahy2002survey].

The three families:

- **Pessimistic logging** (synchronous). The determinant of each nondeterministic event is logged to
  stable storage **before** the event is allowed to affect the process state (i.e. before the process
  sends any message that could reveal a dependency). Consequence: **no process ever becomes an
  orphan**, so only the failed process rolls back, recovery is simple, and output commit is nearly
  free. Cost: a stable-storage write on the critical path of every receive. Optimizations include
  sender-based logging and logging to volatile memory of another node.
- **Optimistic logging** (asynchronous). Determinants are buffered in volatile memory and flushed
  asynchronously, so the failure-free path is fast. But a crash can lose determinants, creating
  **orphan processes** that must be rolled back too. Recovery therefore requires tracking causal
  dependencies (typically vector-clock–style dependency tracking) and computing a maximum consistent
  recoverable state; output commit is expensive because it requires flushing all determinants the
  output causally depends on.
- **Causal logging** (e.g. Manetho [elnozahy1992manetho]). Attempts to get the best of both: it keeps
  determinants in volatile memory but piggybacks them, along with an antecedence graph, on application
  messages so that every determinant a process depends on is replicated at all processes that
  causally depend on it. This gives the **no-orphans property of pessimistic logging with the
  failure-free performance of optimistic logging**, at the price of larger message headers and a more
  complex recovery algorithm.

For AgentMPI, the classification is directly actionable: **an agent rank that performs an external
side effect must be pessimistically logged** (the effect is an output commit), while purely internal
reasoning steps can be optimistically or causally logged.

### 3.5 Optimal checkpoint interval: Young and Daly

Checkpointing has a cost, so there is an optimum frequency. Young [young1974first] derived the
first-order approximation

$$\tau_{opt} \approx \sqrt{2\,\delta\,M}$$

where $\delta$ is the time to write a checkpoint and $M$ is the mean time to interrupt (MTTI); $\tau$
is the *compute* interval between checkpoints. The intuition: expected rework per interrupt is
$\tau/2$, interrupts arrive at rate $1/M$, and checkpoint overhead is $\delta/\tau$ per unit compute;
minimizing $\tau/(2M) + \delta/\tau$ gives $\sqrt{2\delta M}$.

Daly [daly2006higher] extended this. His **first-order** model, which also accounts for restart
overhead $R$, gives

$$\tau_{opt} = \sqrt{2\delta(M+R)} \quad \text{for } \tau + \delta \ll M,$$

recovering Young exactly when $R = 0$. His **higher-order** perturbation model, however, shows that
**$R$ makes no contribution** to the optimum, and yields the recommended estimator

$$\tilde{\tau}_{opt} = \begin{cases}\sqrt{2\delta M}\left[1 + \frac{1}{3}\left(\frac{\delta}{2M}\right)^{1/2} + \frac{1}{9}\left(\frac{\delta}{2M}\right)\right] - \delta, & \delta < 2M \\[4pt] M, & \delta \geq 2M\end{cases}$$

which Daly shows keeps the relative error in total wall-clock solution time below **0.2%**. A simpler
usable form from the same analysis is $\tilde\tau_{opt} = \sqrt{2\delta M} - \delta$ for
$\delta < M/2$, and $M$ otherwise. The $\delta \geq 2M$ branch is the degenerate regime where
checkpointing is so expensive relative to the failure rate that one should simply checkpoint as
rarely as the failure rate permits.

**Relevance to AgentMPI.** The formula is directly reusable with an agent-native cost model: $\delta$
becomes the cost of serializing an agent's conversational context / scratchpad / tool state, and $M$
becomes the mean time to *any* rank-level fault, where fault causes include token-budget exhaustion,
provider rate-limit rejection, and tool errors — a much higher rate than HPC hardware MTTI. Since
$\tau_{opt} \propto \sqrt{M}$, a two-orders-of-magnitude smaller $M$ shrinks the optimal interval by
only one order of magnitude, but the *overhead fraction* $\approx \sqrt{2\delta/M}$ grows as
$1/\sqrt{M}$ — meaning agent checkpoints must be made cheap (incremental, deduplicated) rather than
merely infrequent.

### 3.6 Multilevel checkpointing: SCR and VeloC

At scale, checkpointing to the parallel file system (PFS) is prohibitively slow and the PFS becomes a
contention point and a failure domain of its own. **Multilevel checkpointing** [moody2010scr] writes
different checkpoint *levels* to different storage tiers: cheap, frequent checkpoints to node-local
storage (RAM disk, SSD, burst buffer), optionally with cross-node redundancy (partner copy or XOR/
Reed–Solomon encoding) to survive a single node loss; and rare, expensive checkpoints to the PFS to
survive severe/system-wide failures. Moody et al.'s Scalable Checkpoint/Restart (SCR) library is the
reference implementation; their modeling shows machine-efficiency gains of up to 35% and roughly a
2× reduction in PFS load, with checkpoint *scavenging* (only flushing to PFS on termination) reducing
PFS load by ~20× on then-current systems [moody2010scr].

**VeloC** [nicolae2019veloc] is the successor multilevel checkpoint-restart runtime, emphasizing
**asynchronous** checkpointing: the application blocks only for a fast write to node-local storage,
while a background engine flushes to external storage, with performance modeling plus lightweight
monitoring used to choose local devices adaptively under I/O variability.

The AgentMPI analogue is a natural three-level hierarchy: **L1** in-process context snapshot (cheap,
every step); **L2** replicated to a peer rank or local durable store (survives single-rank crash);
**L3** durable object store / database (survives whole-harness restart). Combined with §3.4, the
level required is determined by the *failure class* one wants to survive, and with §3.5 the frequency
at each level is determined by that level's $\delta$ and the MTTI of the failures it covers — a
per-level Young/Daly optimum, which is exactly the SCR modeling argument.

---

## 4. Idempotence and durable execution

### 4.1 Why exactly-once *delivery* is impossible

The **Two Generals Problem** (attributed to Akkoyunlu, Ekanadham and Huber [akkoyunlu1975constraints],
and named/popularized by Gray [gray1978notes]) establishes that **no protocol over a lossy channel can
guarantee common knowledge of agreement in a finite number of messages**. The standard argument: any
purportedly-correct protocol has a shortest message sequence; the last message in that sequence can be
lost; if the protocol is still correct without it, that message was unnecessary, contradicting
minimality. Therefore no finite protocol exists.

Applied to delivery: a sender cannot know whether a message was delivered without an acknowledgment,
and the acknowledgment can itself be lost. The sender's only options at the moment of uncertainty are

- **retransmit** — risking a duplicate delivery (**at-least-once**), or
- **give up** — risking no delivery (**at-most-once**).

There is no third option. **"Exactly-once delivery" is therefore not implementable over an unreliable
network**, and any system claiming it is either (a) redefining the term, or (b) implementing
exactly-once *effects* on top of at-least-once delivery. Related impossibility results reinforce this:
the coordinated attack problem [gray1978notes], and the fact that atomic commit protocols cannot be
non-blocking in the presence of network partitions [skeen1983formal].

### 4.2 Exactly-once *effects*

What is achievable is that the observable *effect* of a message occurs once. Three composable
mechanisms:

**(a) At-least-once delivery + idempotency keys.** The sender attaches a stable, deterministically
derived identifier (an *idempotency key* / *request id*) to each logical operation. The receiver
maintains a durable **dedup table** mapping key → result. On receipt: if the key is present, return
the stored result **without re-performing the effect**; otherwise perform the effect and record
`(key, result)` **atomically with the effect itself** (same transaction, or CAS on the same object).
Atomicity is the whole trick — if the effect and the key insertion can be separated by a crash, the
guarantee is lost. The dedup table needs a retention window; keys must therefore be scoped and expired
carefully, and the retention window bounds how long a delayed retry can be safely deduplicated. Note
the composition with §2: **the idempotency key deduplicates retries of one operation; the fencing
token orders distinct epochs.** Both are required, and neither substitutes for the other.

**(b) Transactional outbox.** The classic dual-write problem: a service must both update its database
and publish a message, and a crash between the two leaves them inconsistent — and no distributed
transaction is available (or desired) across a database and a broker. The **outbox pattern** records
the outgoing message into an `outbox` table **in the same local ACID transaction** as the state
change. A separate relay process (polling the table, or tailing the write-ahead log — *change data
capture*) reads committed outbox rows and publishes them, deleting/marking them after ack. The relay
may publish duplicates (it can crash after publishing and before marking), so the *publish* is
at-least-once and the **consumer must be idempotent per (a)**. The value of the outbox is not
exactly-once publishing; it is the elimination of the atomicity gap between state change and message
emission. The dual is the **inbox** pattern: record the consumed message id transactionally with the
effect. Both are catalogued in [richardson2018microservices] and [kleppmann2017ddia].

**(c) Write-ahead logging.** WAL [mohan1992aries] is the underlying durability primitive: intentions
are appended to a durable, ordered log *before* being applied to the target, so that after a crash the
log can be replayed to redo committed work and undo uncommitted work. ARIES formalizes the
repeating-history paradigm — redo *everything* in the log, including the actions of loser
transactions, and then undo the losers — plus per-page log sequence numbers (LSNs) for idempotent
redo. The idempotency mechanism there is exactly the high-water-mark rule of §2.3: a redo is applied
only if the page's recorded LSN is less than the log record's LSN. **Fencing tokens, idempotency keys,
and LSN-guarded redo are the same idea at three layers.**

### 4.3 Deterministic replay in durable-execution engines

Durable-execution engines — Temporal (and its predecessor Cadence) [temporal-docs], AWS Step Functions
[aws-stepfunctions-docs], and Azure Durable Functions / the Durable Task Framework
[azure-durable-docs] — implement a *programming model* in which ordinary imperative code survives
process crashes. The mechanism is event sourcing plus deterministic replay, and it is the direct
descendant of PWD-based message logging (§3.4).

**The mechanism, precisely (Temporal formulation):**

1. Workflow state is not snapshotted. Instead the service maintains a per-execution **append-only
   Event History**: an ordered, immutable log of everything that has happened
   (`WorkflowExecutionStarted`, `ActivityTaskScheduled`, `ActivityTaskCompleted`, `TimerFired`,
   `WorkflowExecutionSignaled`, …) [temporal-docs].
2. All effectful, nondeterministic work is confined to **Activities** — the unit that is allowed to
   call the network, the filesystem, a database, or an LLM. Workflow code itself performs no I/O.
3. When a worker needs to advance the workflow, it **re-executes the workflow function from the
   first line**. As the code reaches each `await` on an Activity/timer/signal, the SDK consults the
   history:
   - if a *completed* result for that position exists, it is **returned immediately from the history —
     the Activity is not re-invoked**;
   - if the Activity was scheduled but has not completed, the code blocks (it is not rescheduled);
   - if nothing exists at that position, the SDK emits a **Command** (e.g. `ScheduleActivityTask`)
     and the workflow suspends; the service appends the corresponding events and dispatches the
     actual work [temporal-docs].
4. The worker compares each Command generated during replay against the recorded history at the same
   position. **A mismatch is a non-determinism error and fails the workflow task** rather than
   silently diverging [temporal-docs].
5. Workers cache workflow state in memory (Temporal's Workflow Cache / Sticky Queues) so a full replay
   from the start is only needed on cache eviction or worker restart; the semantics are identical
   either way [temporal-docs].

Azure Durable Functions is architecturally the same [azure-durable-docs]: an orchestrator function
`await`s, which yields control to the Durable Task Framework dispatcher; the dispatcher commits newly
scheduled actions by appending to a **History table** and enqueuing work messages, then **unloads the
orchestrator from memory entirely**. When new work arrives (an activity result, a timer expiry, an
external event), the orchestrator "wakes up and re-executes the entire function from the start to
rebuild the local state," and each `await` that already has a result in the execution history has
that result replayed rather than re-performed. Microsoft's documentation explicitly labels this the
**event sourcing** pattern and notes that the history is loaded into memory with a single-partition
range query. AWS Step Functions attains the same durability with a different surface — the state
machine is declarative (Amazon States Language) rather than replayed imperative code, and each state
transition and its result are persisted in an execution history — so it sidesteps the determinism
constraint by not letting user code carry control-flow state across transitions
[aws-stepfunctions-docs].

**Determinism requirements.** For replay to reconstruct state faithfully, the workflow function must
emit the *same sequence of commands given the same history*. The standard prohibitions
[temporal-docs, azure-durable-docs]:

- **No wall-clock reads** (`DateTime.Now`, `time.Now()`) — use the context-supplied deterministic
  clock (`context.CurrentUtcDateTime`, `workflow.Now()`), whose value is recorded in history.
- **No unseeded randomness or UUID generation** — use `context.NewGuid()` / the SDK's
  replay-safe random source, which is derived deterministically from the execution.
- **No direct I/O, network calls, or database queries** in workflow code — move them into Activities.
  Temporal's docs call this out explicitly for **LLM/AI invocations**.
- **No unbounded/unordered iteration** over structures with nondeterministic ordering (hash-map
  iteration order), no thread/goroutine nondeterminism outside the SDK's deterministic scheduler,
  no blocking on external synchronization primitives.
- **Code changes must be versioned.** Changing workflow code changes the command sequence and breaks
  in-flight executions; engines provide **patching / versioning APIs** (`workflow.GetVersion()`,
  `patched()`, Worker Versioning) to branch on a recorded version marker, and **replay tests** against
  archived histories to detect incompatible changes before deployment [temporal-docs].

**The composition with §4.2.** Because a worker can crash after an Activity's side effect but before
its completion event is appended, **Activities are at-least-once**; the workflow's *state transitions*
are effectively exactly-once (they are determined by the history), but the *effects* are not.
Therefore **Activities must be idempotent**, typically via an idempotency key derived deterministically
from the workflow id plus the activity's position in the history [temporal-docs]. This is the exact
same layering as §4.2(a) and is the design AgentMPI should copy.

**Why this is the right model for agent recovery.** An LLM call is the paradigm nondeterministic event
of §3.1: sampling makes it irreproducible, it is expensive, and it may have external side effects
through tools. Recording the completion into an event history and replaying it converts an agent's
reasoning trajectory into a **piecewise deterministic** computation, which is precisely the assumption
[elnozahy2002survey] requires for log-based recovery to work. The harness's orchestration logic
(control flow between ranks, collectives, routing) plays the role of workflow code and must be
deterministic; every model call and tool call plays the role of an Activity and must be logged and
idempotent. The known frictions to state honestly in the paper: (i) provider-side nondeterminism means
a *replayed* trajectory is only faithful if the completion was logged, never if it is re-sampled;
(ii) agent "code" is often itself model-generated and therefore changes between runs, which is the
workflow-versioning problem in its most severe form [UNVERIFIED: I am not aware of published work
applying durable-execution versioning to model-generated orchestration code — check before claiming
novelty]; and (iii) context-window growth makes the history both the recovery log *and* the input to
the next model call, coupling recovery cost to inference cost in a way classical message logging does
not model.

---

## 5. Byzantine and silent failures

### 5.1 The Byzantine Generals Problem

Lamport, Shostak and Pease [lamport1982byzantine] introduced the **Byzantine** failure model: a
faulty component may behave *arbitrarily* — including sending different, inconsistent messages to
different peers, i.e. actively lying. This strictly subsumes crash failures and omission failures. The
central results:

- With **oral messages** (unauthenticated, but reliably delivered, with the receiver knowing the
  sender and the absence of a message detectable), the problem is solvable **iff $n \geq 3m + 1$**,
  where $m$ is the number of traitors. The algorithm `OM(m)` requires $m+1$ rounds and is exponential
  in message count. The impossibility of $n = 3m$ is shown by the classic three-general argument: with
  one traitor and three generals, a loyal lieutenant cannot distinguish a traitorous commander from a
  traitorous fellow lieutenant.
- With **signed messages** (unforgeable signatures, any process can verify any signature), the bound
  collapses: `SM(m)` solves the problem for **any $n \geq m + 2$**, i.e. any number of traitors, since
  a traitor can no longer lie about what it was told without detection.

The practical reading: **cryptographic authentication buys you enormously more fault tolerance than
redundancy alone**, and the $3f+1$ bound is the price of not having it.

### 5.2 PBFT

Castro and Liskov [castro1999pbft] made Byzantine agreement practical: **Practical Byzantine Fault
Tolerance** is a state-machine-replication protocol tolerating $f$ Byzantine faults with $n = 3f + 1$
replicas, in a partially synchronous model, with performance close to unreplicated service in the
normal case. Structure: a primary assigns sequence numbers; the three-phase
**pre-prepare → prepare → commit** protocol establishes a total order; a replica commits when it has
$2f+1$ matching messages (guaranteeing quorum intersection in at least one correct replica); a
**view change** replaces a faulty or slow primary. Safety holds in full asynchrony; **liveness relies
on partial synchrony** (an eventual message-delay bound), which is again FLP-consistent —
[castro1999pbft] cannot circumvent [flp1985impossibility], only relocate the assumption. Note the
$3f+1$ quorum requirement is significantly worse than the $2f+1$ of crash-tolerant consensus, and
message complexity is $O(n^2)$ per operation in the normal case.

### 5.3 Silent data corruption in HPC

**Silent data corruption (SDC)** — also called *soft errors* or *silent errors* — is corruption that
is not detected by hardware error-detection mechanisms and therefore propagates into the application's
results without any signal. Sources include particle-induced bit flips in unprotected SRAM/logic,
DRAM errors that escape ECC (multi-bit errors beyond SECDED capacity), and firmware/link errors.
SDC is the canonical *non-crash, non-malicious* Byzantine-adjacent failure in HPC: the machine does
not stop, it just computes the wrong answer. It is what makes checkpoint/restart insufficient — a
checkpoint taken after corruption faithfully preserves the corruption, so **rollback cannot fix a
fault it did not detect**, and error *detection latency* becomes a first-class design parameter
(hence the multi-level checkpoint retention of §3.6: you must keep old enough checkpoints to roll back
past the corruption). [UNVERIFIED: specific published SDC rates for particular machines (e.g.
Blue Waters, Titan, Cielo, Jaguar) vary substantially across studies and reporting methodologies —
cite a specific field study with its exact numbers rather than a general rate claim.]

### 5.4 LLM agents are *partially* Byzantine

This is the argument the paper needs to make carefully.

An LLM agent rank does not fail like a crash-stop process. It fails like an SDC event with a
persuasive user interface: it returns a **syntactically well-formed, protocol-conformant,
plausible-looking, and wrong** result. Characteristics:

- **Not fail-stop.** The rank remains responsive, its heartbeats continue, its lease renews. Every
  detector in §1 reports it healthy. Crash-failure detection is structurally blind to it.
- **Not fully Byzantine either.** The failure is not adversarial or strategically coordinated; it is
  not selecting messages to maximize damage; it does not equivocate *deliberately* (though it may
  equivocate *incidentally*: sampling means two ranks given the same prompt can return contradictory
  answers, which is observationally equivalent to equivocation). It also does not attack the transport
  or the signature layer, so [lamport1982byzantine]'s signed-message assumption is cheap to satisfy.
- **Correlated, not independent.** This is the crucial deviation from the classical model. BFT's
  $n \geq 3f+1$ derivation assumes faults are *bounded in number* and, in practice, independent. Ranks
  running the same base model on the same prompt share failure modes: the same misconception, the same
  hallucinated API, the same misread of an ambiguous spec. Naive $N$-way voting therefore does **not**
  deliver the independence that replication-based masking assumes — the analogue of common-mode
  failure in N-version programming (Knight and Leveson [knight1986experimental] showed empirically
  that independently developed program versions fail on correlated inputs far more often than
  independence assumptions predict; the same critique applies with more force to replicas of one
  model). [UNVERIFIED: quantitative correlation of errors across LLM samples/models is
  workload-dependent; cite a specific measurement rather than asserting a general figure.]

So: **LLM ranks are Byzantine in the *content* dimension but crash-stop-benign in the *protocol*
dimension.** They corrupt values, not the message-passing layer.

**The cost of voting/quorum mitigation.** If one nonetheless replicates: $k$-way sampling multiplies
token cost and latency by $k$ (or by $k$ in cost and ~1 in latency if run in parallel, at $k\times$
concurrency and rate-limit pressure). Full BFT-style masking at $n \geq 3f+1$ [castro1999pbft,
lamport1982byzantine] means $\geq 4\times$ inference cost to tolerate one wrong rank, plus an
agreement protocol over free-form text for which "matching messages" is not even well-defined —
semantic equivalence is not syntactic equality, so the quorum predicate itself requires a judge, which
is itself an LLM and itself partially Byzantine. This regress is the strongest argument that
**system-level Byzantine masking is the wrong primitive for agent harnesses**.

### 5.5 ABFT: application-level verification instead of system-level masking

The cheaper alternative, and the one with the better HPC pedigree, is **Algorithm-Based Fault
Tolerance**. Huang and Abraham [huang1984abft] showed that for matrix operations one can extend
matrices with **checksum rows/columns** such that the checksum relationship is *invariant under the
algorithm*: a checksum matrix multiplied by a checksum matrix yields a checksum matrix. Errors are
then detected — and, with enough redundancy, located and corrected — by verifying the invariant at low
cost, **with no checkpoint and no replication of the computation**. The overhead is $O(n)$ against an
$O(n^3)$ computation, versus $\geq 2\times$ for duplication.

The modern HPC composition is **ABFT + ULFM**. MPI's **User Level Failure Mitigation** interface
[bland2013ulfm] gives applications failure notification plus repair primitives —
`MPIX_Comm_revoke` to invalidate a broken communicator, `MPIX_Comm_agree` to reach uniform agreement
on a value/state despite failures, `MPIX_Comm_shrink` to build a new communicator excluding failed
ranks — while leaving *recovery policy* to the application. Failures do not implicitly abort the job
and do not alter communicator state; errors are raised only where an operation is actually disrupted
[losada2020ulfm]. Combining the two yields **forward recovery**: ABFT checksums reconstruct the lost
or corrupted data mathematically while ULFM repairs the communication substrate, avoiding a global
rollback entirely [losada2020ulfm, bland2013ulfm]. [UNVERIFIED: a portion of ULFM was
incorporated into the MPI standard (MPI 4.x/5.0 era); confirm exactly which constructs and which
standard version before stating it.]

**Why this is the right analogy for AgentMPI.** ABFT's insight is that *the application knows an
invariant the system does not*, and checking that invariant is far cheaper than replicating the
computation. The agent analogue: instead of $k$-way voting on free-form output, verify the output
against a cheap, application-specific, **checkable** invariant — the code compiles, the tests pass,
the JSON validates against the schema, the SQL parses and returns a plausible row count, the cited
file actually contains the cited line, the arithmetic re-checks under a calculator, the claimed edit
is present in the diff. These are *asymmetric verification*: cheap to check, expensive to produce.
Where such an invariant exists, use it (ABFT-style forward recovery: reject and retry the rank, repair
the "communicator" by re-forming the collective without the bad rank, per ULFM). Where no invariant
exists, only then fall back to redundancy/voting, and pay for it knowingly. And in both cases, the
detector is *not* the failure detector of §1 — silent wrongness needs a **verifier**, which is a
different subsystem with a different placement in the protocol.

---

## 6. Supervision trees and "let it crash"

### 6.1 Armstrong's thesis

Joe Armstrong's doctoral thesis, *Making reliable distributed systems in the presence of software
errors* [armstrong2003thesis], is the design rationale for Erlang/OTP and the canonical statement of
the supervision-tree architecture. Its premises:

1. **Software errors are inevitable**, and a large fraction are transient/Heisenbugs triggered by rare
   states or timing, not deterministic Bohrbugs. Restarting from a known-good state fixes the former.
2. **Concurrency + isolation**: the unit of failure is a lightweight process with *no shared mutable
   state*; processes communicate only by asynchronous message passing. This means a failing process
   cannot corrupt another's state — it can only fail to reply. This is the property that makes
   restarting *sound*: there is a clean state boundary to restart to.
3. **Failure must be detectable and attributable across the boundary**, via *links* (bidirectional
   failure propagation) and *monitors* (unidirectional failure notification), so that some other
   process learns of the crash and can act. Note this is a failure-detection mechanism of exactly the
   §1 kind, made easy by the fact that the VM can observe process death locally (synchronously and
   accurately) and only becomes ◇P-like across a network.
4. **Fault handling is a separate concern from the happy path.** The process that fails is not the
   process that decides what to do about it.

### 6.2 "Let it crash" — precisely what it means

The slogan is widely misread as "don't handle errors." Its actual content [armstrong2003thesis] is
narrower and stronger:

- **Do not write defensive code for errors you cannot correctly handle.** Programming defensively
  against every unexpected condition produces code paths that are never tested, that obscure the
  happy path, and that most often *continue running in a corrupted state* — turning a clean crash into
  a silent failure. Erlang code is written "for the correct case" and allowed to fail otherwise.
- **Crash early and cleanly**, so the error is contained at the smallest granularity and the state is
  reset to something known-good rather than something plausible-but-wrong. This is the same argument
  as §5.3: a detected failure is vastly better than a silent one.
- **Handle the failure somewhere else, in a component whose job that is** — the supervisor. Armstrong
  distinguishes *workers*, which do the work and are permitted to crash, from *supervisors*, whose
  only job is to observe workers and restart them per a declared policy. Supervisors are simple,
  generic, and heavily tested precisely because they contain no application logic.
- **The corollary is that state must be recoverable**: either it is derivable from a durable source,
  or it was checkpointed, or losing it is acceptable. "Let it crash" without a story for state is just
  data loss. This is why §3 and §4 are prerequisites for §6, not alternatives to it.

### 6.3 Supervision trees and restart strategies

A **supervision tree** is a tree whose internal nodes are supervisors and whose leaves are workers.
Supervisors may supervise other supervisors, so failure escalates up the tree: a supervisor that
cannot fix its subtree crashes itself, and its own supervisor applies its policy to the whole subtree.
Startup and shutdown are ordered by the tree structure. Each child is declared with a **restart type**
— `permanent` (always restarted), `transient` (restarted only on abnormal termination), or
`temporary` (never restarted) — and a **shutdown** specification (brutal kill vs. a timeout for
graceful termination).

The OTP **restart strategies** [armstrong2003thesis, otp-supervisor-docs]:

- **`one_for_one`** — if a child terminates, *only that child* is restarted. Correct when children are
  mutually independent. This is the default and by far the most common.
- **`one_for_all`** — if any child terminates, **all other children are terminated and then all are
  restarted**. Correct when children are mutually dependent, so that a survivor holding a reference to
  the dead child would itself be in an inconsistent state.
- **`rest_for_one`** — if a child terminates, that child **and all children started after it** (i.e.
  the rest of the ordered child list) are terminated and restarted. Correct for a linear dependency
  chain where later children depend on earlier ones but not vice versa.
- **`simple_one_for_one`** — a supervisor of many dynamically added instances of a *single* child
  specification, used for per-request/per-connection worker pools. [UNVERIFIED: modern OTP releases
  supersede this with the `dynamic` supervisor `auto_shutdown`/child-spec machinery; check the current
  `supervisor` manual page before describing it as the recommended form.]

The choice of strategy is a **declaration of the dependency structure of the state**, which is exactly
the information AgentMPI needs when a rank in a collective dies: whether to restart just that rank
(`one_for_one`), tear down and restart the whole collective (`one_for_all`), or restart the failed
rank and everything downstream of it in a pipeline (`rest_for_one`).

### 6.4 Max restart intensity and why bounding restarts matters

An OTP supervisor is configured with a **restart intensity**: a pair `(MaxRestarts, MaxSeconds)` (in
older docs `MaxR`, `MaxT`). If more than `MaxRestarts` child restarts occur within any `MaxSeconds`
window, **the supervisor gives up: it terminates all children and terminates itself**, escalating the
failure to *its* supervisor.

The reason this matters is fundamental, not operational:

1. **Restarting only fixes transient faults.** If the fault is deterministic — a poison-pill input, a
   malformed configuration, an unreachable dependency — restarting reproduces it immediately. Without
   a bound, the system enters an unbounded **crash-restart loop**, burning resources and generating log
   volume while making zero progress, and, crucially, *never signalling that it is broken*.
2. **The bound converts a livelock into a failure.** Exceeding the intensity is the supervisor's way
   of *detecting* that the fault is not transient, and escalation gives a higher, more capable
   component (with a wider view and coarser remedies — restart a subsystem, fail over, alert a human)
   the chance to fix it. Escalation is thus a form of hierarchical fault diagnosis.
3. **It bounds the blast radius of the retry itself.** In a distributed setting, unbounded restarts of
   many clients are the classic metastable-failure amplifier: retries add load, load causes timeouts,
   timeouts cause more retries. Bounding restarts (and, in the wider literature, exponential backoff
   plus jitter, retry budgets, and circuit breakers [nygard2007release]) is what keeps a local failure
   from becoming a global one.

For AgentMPI, item (1) is the load-bearing point: a restarted agent rank re-reads the same context,
re-issues the same prompt, and is likely to make the same mistake, while each restart costs real
money in tokens. **The restart-intensity bound is therefore also the cost-control mechanism**, and
exceeding it must escalate to a policy change (different model, reduced context, decomposed task,
human) rather than to another identical retry.

---

## 7. LLM-agent failure modes: detector and recovery primitive

Before the design implications, the failure taxonomy AgentMPI must cover. "Detector" answers *how do
we learn it happened*; "recovery primitive" answers *what do we do*. Note that only rows 1–2 are
addressable by a classical failure detector [chandra1996unreliable]; the rest require either a
resource monitor, a protocol checker, or an application-level verifier (§5.5).

| # | Failure mode | Description | Detector | Recovery primitive |
|---|---|---|---|---|
| F1 | **Crash / fail-stop** | Rank process dies, container OOM-killed, host lost. | Lease expiry (◇P, §1.5); transport reset gives fast-path hint. | Fence old epoch (§2.3); restart rank from last L1/L2 checkpoint (§3.6); replay event history (§4.3). `one_for_one`. |
| F2 | **Silent stall / hang** | Rank alive but making no progress: blocked tool call, provider hang, infinite `await`. | Lease non-renewal **plus** a *progress* predicate (no new event appended to the rank's history within a bound) — heartbeats alone are insufficient, since a heartbeat thread can outlive a stuck work loop. φ-accrual [hayashibara2004phi] on inter-event times. | Revoke lease, increment epoch, fence in-flight effects, restart from checkpoint. Never assume the stalled rank is dead (§1.4) — fencing, not detection, provides safety. |
| F3 | **Context exhaustion** | Conversation/scratchpad exceeds the model's context window; further calls hard-fail or silently truncate. | Deterministic, *predictable* resource check: token accounting before each call. This is a **predicate on local state, not a failure detector** — it can be evaluated exactly. | Not a crash: compaction/summarization checkpoint. Write a compacted L2 checkpoint, `ContinueAsNew`-style history truncation (§4.3) with the summary as new initial state, preserving the durable event history separately from the model-visible prompt. |
| F4 | **Cost / budget exhaustion** | Rank exceeds its token or dollar allowance. | Budget accounting at the coordinator, checked at dispatch (lazy evaluation, §1.5). | Deny further leases to that rank (the lease *is* the budget grant); escalate per restart-intensity rules (§6.4). Do **not** restart — restarting re-spends. |
| F5 | **Tool loop / livelock** | Rank repeatedly invokes the same tool with the same or cyclically varying arguments, making no progress. | Progress/novelty detector over the event history: repeated identical `(tool, args)` determinants, or no change in a task-specific progress metric over $k$ steps. Structurally a **stable-property detection** problem (§3.3). | Bounded restart with policy change (§6.4): abort the step, inject the loop evidence into context, or escalate to the supervisor. Restart alone is useless — the loop is deterministic. |
| F6 | **Protocol violation** | Rank emits a malformed message, wrong collective tag, unexpected type, or calls a collective out of order. | Cheap, exact, **synchronous** schema/protocol check at the message boundary (an ABFT-style invariant, §5.5). | Reject at the boundary before the message enters the collective; retry the rank's step with the validation error as feedback; count against restart intensity. Never let a malformed message into the collective's state. |
| F7 | **Plausible-but-wrong output** | Well-formed, on-protocol, semantically incorrect result — the partially-Byzantine mode (§5.4). | **Not detectable by any liveness detector.** Requires an application verifier: tests, compiler, schema+semantic check, citation check, re-derivation; only where no invariant exists, $k$-way sampling with a quorum predicate — at $k\times$ cost. | ABFT-style forward recovery (§5.5): reject the result, re-form the collective excluding the bad rank (ULFM `shrink`/`revoke` analogue [bland2013ulfm]), retry with the verifier's counterexample. Rollback is wrong here — the checkpoint contains the corruption. |
| F8 | **Zombie / duplicate rank** | A rank presumed dead resumes and emits effects; or a restart runs concurrently with the original (split-brain). | **Undetectable in general** (§1.4) — this is precisely the case the detector gets wrong. | **Fencing tokens** (§2.3): every externally-visible effect carries the rank epoch; effect sinks reject stale epochs [kleppmann2016locking, burrows2006chubby]. Plus idempotency keys (§4.2a) for duplicate retries of the *same* epoch. |
| F9 | **Deadlock inside a collective** | Ranks mutually waiting: a barrier one rank never reaches, a gather awaiting a crashed contributor, a cyclic request/response. | Deadlock is a **stable property** → Chandy–Lamport consistent snapshot [chandy1985snapshots] plus wait-for-graph cycle detection; cheaper approximation: per-collective deadline plus participant-set liveness check. | Revoke the collective (ULFM `MPIX_Comm_revoke` analogue), fence all participants' epochs, then either shrink the participant set and re-run, or roll the whole collective back to its last coordinated checkpoint — this is the `one_for_all` case (§6.3). |
| F10 | **Provider-side transient failure** | Rate limit, 5xx, timeout, model deprecation/rollout. | Explicit error code on the call path — the *easy* case: the failure is self-reporting. | At-least-once retry with backoff + jitter behind an idempotency key (§4.2a); the retry must not double-charge downstream effects. Counts against restart intensity but with a distinct budget from F5/F7. |
| F11 | **Non-deterministic replay divergence** | On recovery, replayed orchestration emits a different command sequence than the recorded history (changed harness code, unlogged randomness, wall-clock read). | Command-vs-history comparison at replay [temporal-docs, azure-durable-docs] — fails loudly by construction. | Fail the workflow task rather than diverge silently; version/patch the orchestration [temporal-docs]; treat archived histories as replay-regression tests. |
| F12 | **Byzantine/compromised rank** | Prompt injection or a compromised tool causes a rank to act against the protocol's intent. | Signed messages [lamport1982byzantine] + capability/permission checks at effect sinks; anomaly detection on the effect stream. | Least-privilege effect sinks + fencing; revoke the rank's lease and capabilities. Full BFT masking ($n\ge 3f+1$, [castro1999pbft]) is available but usually not cost-justified (§5.4). |

---

## 8. Design implications for AgentMPI

1. **Adopt ◇P as the target detector class and state the timing assumption explicitly.** AgentMPI's
   lease-based, lazily-evaluated detector is at best **eventually perfect** under partial synchrony
   [chandra1996unreliable, dwork1988consensus], and only if the lease duration adapts after false
   suspicions and the scheduler guarantees every correct rank is eventually evaluated. It is not P:
   claiming P would require an a-priori bound on LLM inference plus tool latency, which is
   indefensible [flp1985impossibility]. Say this in the paper rather than letting a reviewer say it.

2. **Never let safety depend on the detector.** Because "slow" and "dead" are indistinguishable at any
   finite prefix of an asynchronous run [flp1985impossibility], the detector will sometimes declare a
   merely-stalled rank dead. The correct architectural response is to make the *consequence* of a
   wrong suspicion harmless: detection drives liveness (reclaim work, restart), and a separate
   mechanism — fencing — provides safety.

3. **Attach a monotone epoch to every rank and fence every externally-visible effect with it.** A
   lease alone cannot be safe, because a rank cannot bound the delay between its own liveness check
   and the landing of its effect [kleppmann2016locking]. A monotone token checked against a
   high-water-mark at the effect sink closes the race with **no timing assumption**, exactly as
   Chubby's sequencers do [burrows2006chubby]. Restarting a rank must increment its epoch; epochs must
   come from one linearizable counter per logical rank identity.

4. **Design effect sinks to be fenceable, and treat unfenceable sinks as a documented degradation.**
   A fencing token needs somewhere to land: a conditional write, a CAS, or an explicit epoch table
   [kleppmann2016locking]. Tools that cannot do a conditional write (a shell command with side
   effects, an email send, an external API without preconditions) can only be protected by a
   Chubby-style *lock-delay* quarantine window, which Burrows himself calls "imperfect"
   [burrows2006chubby]. The paper should classify AgentMPI's tool surface into fenceable and
   quarantine-only, and be honest that the latter is best-effort.

5. **Use coordinated checkpointing at collective boundaries and uncoordinated checkpointing within a
   rank's local reasoning.** Uncoordinated checkpointing risks the domino effect and useless
   checkpoints [elnozahy2002survey, randell1975system]; coordinated checkpointing eliminates both at
   the cost of a synchronization, and a collective (barrier, gather, allreduce) is already a
   synchronization point, so the coordination is free there. Between collectives, ranks are
   independent and can checkpoint autonomously.

6. **Treat every model call and tool call as a logged nondeterministic event, making the agent
   piecewise deterministic.** Log-based recovery requires PWD [elnozahy2002survey]; a sampled LLM
   completion violates it unless the completion itself is recorded as the determinant. Recording
   completions converts an unreproducible trajectory into a replayable one and is what makes
   single-rank recovery (rather than global rollback) possible.

7. **Log pessimistically at output-commit points and optimistically elsewhere.** Pessimistic logging
   guarantees no rank ever becomes an orphan, so only the failed rank rolls back, and it makes output
   commit nearly free — at the price of a stable write on every event [elnozahy2002survey]. Since an
   agent's externally-visible tool effect *is* an output commit, force a durable determinant write
   before it; internal reasoning steps can use optimistic or causal logging for speed.

8. **Copy the durable-execution replay mechanism verbatim, including its determinism discipline.**
   Temporal and Durable Functions re-execute orchestration code from the top and serve recorded
   results at each already-completed `await` instead of re-performing the effect, comparing generated
   commands against the recorded history and failing loudly on mismatch [temporal-docs,
   azure-durable-docs]. AgentMPI's orchestration layer must therefore forbid wall-clock reads,
   unseeded randomness, and direct I/O outside "activities," and must provide versioning/patching for
   harness code changes — otherwise every deploy corrupts in-flight runs.

9. **Exactly-once is a property of effects, not deliveries — build for at-least-once plus
   idempotency keys.** Two Generals rules out exactly-once delivery [gray1978notes,
   akkoyunlu1975constraints]; the achievable target is at-least-once delivery with a durable dedup
   table whose insertion is atomic with the effect, plus a transactional outbox where a state change
   and a message emission must not diverge [kleppmann2017ddia, richardson2018microservices]. Note the
   division of labour: **idempotency keys deduplicate retries within an epoch; fencing tokens order
   epochs.** AgentMPI needs both, and the paper should not conflate them.

10. **Size checkpoint frequency with a Young/Daly calculation using agent-native parameters.** With
    $\delta$ = context/state serialization cost and $M$ = mean time to any rank fault, the optimum
    compute interval is $\tilde\tau_{opt} \approx \sqrt{2\delta M} - \delta$ [young1974first,
    daly2006higher]. Because agent MTTI is orders of magnitude smaller than HPC hardware MTTI, the
    overhead fraction $\approx\sqrt{2\delta/M}$ is what dominates — so the actionable engineering
    lever is *reducing $\delta$* (incremental/differential context checkpoints, content-addressed
    dedup of the transcript) rather than merely tuning the interval.

11. **Implement a multilevel checkpoint hierarchy and pick the level per failure class.** Following
    SCR/VeloC [moody2010scr, nicolae2019veloc]: L1 in-process context snapshot (frequent, near-zero
    cost, survives a step-level error); L2 replicated to a peer rank or local durable store (survives
    a single-rank crash); L3 durable object store (survives whole-harness loss). Each level gets its
    own Young/Daly optimum from its own $\delta$ and the MTTI of the faults it covers, and L1/L2 can
    be flushed to L3 asynchronously to keep it off the critical path [nicolae2019veloc].

12. **Do not build system-level Byzantine masking; build application-level verification.** LLM ranks
    are partially Byzantine — they corrupt values while remaining protocol-compliant and heartbeat-
    healthy (§5.4) — but their errors are *correlated*, so the independence assumption underlying
    replication-based masking fails [knight1986experimental], and $n \geq 3f+1$ replication
    [lamport1982byzantine, castro1999pbft] costs $\geq 4\times$ inference with a quorum predicate that
    itself needs a fallible judge. Instead use ABFT-style checkable invariants [huang1984abft] —
    compiles, tests pass, schema validates, citation resolves, arithmetic re-checks — which are
    asymmetrically cheap to verify, and reserve $k$-way voting for the residue where no invariant
    exists.

13. **Prefer forward recovery over rollback for silent-wrongness faults.** A checkpoint taken after a
    plausible-but-wrong result faithfully preserves the error, exactly as a checkpoint taken after
    silent data corruption does (§5.3); rollback cannot repair a fault it did not detect. The
    ABFT+ULFM composition is the right model: reconstruct the bad result at the application level and
    *repair the communicator* — revoke, agree, shrink the participant set — rather than rolling the
    whole job back [bland2013ulfm, losada2020ulfm].

14. **Structure the harness as a supervision tree with explicit restart strategies.** Isolated ranks
    with no shared mutable state and message-only communication make restart *sound*, and separating
    workers (may crash) from supervisors (decide policy) keeps fault handling out of the happy path
    [armstrong2003thesis]. Map the OTP strategies onto agent topologies: `one_for_one` for
    independent ranks, `one_for_all` for a collective whose participants hold mutual references
    (F9), `rest_for_one` for a pipeline where downstream ranks consumed the failed rank's output.

15. **Bound restart intensity, and make exceeding the bound escalate to a *policy change*, not
    another retry.** OTP supervisors terminate themselves when more than `MaxRestarts` occur within
    `MaxSeconds`, escalating to a higher supervisor [armstrong2003thesis]. For agents this bound does
    double duty: restarting a rank that re-reads the same context will likely reproduce a
    deterministic fault (F5, F7) while spending real tokens each time, so the intensity bound is
    simultaneously the loop-breaker and the cost cap. Escalation should change something — model,
    context, decomposition, or human involvement — and retry budgets with backoff and jitter should
    prevent retry storms from turning a local failure into a global one [nygard2007release].

16. **Separate the three detection subsystems in the architecture and in the paper.** Liveness
    detection (leases, §1) catches F1–F2; resource/budget accounting (exact local predicates) catches
    F3–F4; protocol and semantic verification (§5.5) catches F6–F7. Conflating them is the most
    common design error in agent harnesses: a heartbeat cannot detect a wrong answer, and a verifier
    cannot detect a dead process. Stating this separation explicitly is likely AgentMPI's clearest
    conceptual contribution.

---

## BibTeX

```bibtex
@article{flp1985impossibility,
  author  = {Fischer, Michael J. and Lynch, Nancy A. and Paterson, Michael S.},
  title   = {Impossibility of Distributed Consensus with One Faulty Process},
  journal = {Journal of the ACM},
  volume  = {32},
  number  = {2},
  pages   = {374--382},
  year    = {1985},
  doi     = {10.1145/3149.214121}
}

@inproceedings{benor1983another,
  author    = {Ben-Or, Michael},
  title     = {Another Advantage of Free Choice: Completely Asynchronous Agreement Protocols},
  booktitle = {Proceedings of the 2nd Annual ACM Symposium on Principles of Distributed Computing (PODC '83)},
  pages     = {27--30},
  year      = {1983},
  publisher = {ACM},
  doi       = {10.1145/800221.806707}
}

@article{chandra1996unreliable,
  author  = {Chandra, Tushar Deepak and Toueg, Sam},
  title   = {Unreliable Failure Detectors for Reliable Distributed Systems},
  journal = {Journal of the ACM},
  volume  = {43},
  number  = {2},
  pages   = {225--267},
  month   = mar,
  year    = {1996},
  doi     = {10.1145/226643.226647}
}

@article{chandra1996weakest,
  author  = {Chandra, Tushar Deepak and Hadzilacos, Vassos and Toueg, Sam},
  title   = {The Weakest Failure Detector for Solving Consensus},
  journal = {Journal of the ACM},
  volume  = {43},
  number  = {4},
  pages   = {685--722},
  year    = {1996},
  doi     = {10.1145/234533.234549}
}

@article{dwork1988consensus,
  author  = {Dwork, Cynthia and Lynch, Nancy and Stockmeyer, Larry},
  title   = {Consensus in the Presence of Partial Synchrony},
  journal = {Journal of the ACM},
  volume  = {35},
  number  = {2},
  pages   = {288--323},
  year    = {1988},
  doi     = {10.1145/42282.42283}
}

@article{lamport1998parttime,
  author  = {Lamport, Leslie},
  title   = {The Part-Time Parliament},
  journal = {ACM Transactions on Computer Systems},
  volume  = {16},
  number  = {2},
  pages   = {133--169},
  year    = {1998},
  doi     = {10.1145/279227.279229}
}

@article{chen2002quality,
  author  = {Chen, Wei and Toueg, Sam and Aguilera, Marcos Kawazoe},
  title   = {On the Quality of Service of Failure Detectors},
  journal = {IEEE Transactions on Computers},
  volume  = {51},
  number  = {5},
  pages   = {561--580},
  year    = {2002},
  doi     = {10.1109/TC.2002.1004595}
}

@inproceedings{hayashibara2004phi,
  author    = {Hayashibara, Naohiro and D{\'e}fago, Xavier and Yared, Rami and Katayama, Takuya},
  title     = {The $\varphi$ Accrual Failure Detector},
  booktitle = {Proceedings of the 23rd IEEE International Symposium on Reliable Distributed Systems (SRDS 2004)},
  pages     = {66--78},
  year      = {2004},
  publisher = {IEEE},
  doi       = {10.1109/RELDIS.2004.1353004}
}

@inproceedings{gray1989leases,
  author    = {Gray, Cary G. and Cheriton, David R.},
  title     = {Leases: An Efficient Fault-Tolerant Mechanism for Distributed File Cache Consistency},
  booktitle = {Proceedings of the 12th ACM Symposium on Operating Systems Principles (SOSP '89)},
  pages     = {202--210},
  year      = {1989},
  publisher = {ACM},
  doi       = {10.1145/74850.74870}
}

@misc{kleppmann2016locking,
  author       = {Kleppmann, Martin},
  title        = {How to Do Distributed Locking},
  howpublished = {Blog post, \url{https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html}},
  month        = feb,
  year         = {2016},
  note         = {Published 8 February 2016; accessed 2026}
}

@book{kleppmann2017ddia,
  author    = {Kleppmann, Martin},
  title     = {Designing Data-Intensive Applications: The Big Ideas Behind Reliable, Scalable, and Maintainable Systems},
  publisher = {O'Reilly Media},
  address   = {Sebastopol, CA},
  year      = {2017},
  isbn      = {978-1449373320}
}

@inproceedings{burrows2006chubby,
  author    = {Burrows, Mike},
  title     = {The {Chubby} Lock Service for Loosely-Coupled Distributed Systems},
  booktitle = {Proceedings of the 7th USENIX Symposium on Operating Systems Design and Implementation (OSDI '06)},
  pages     = {335--350},
  address   = {Seattle, WA},
  month     = nov,
  year      = {2006},
  publisher = {USENIX Association}
}

@inproceedings{hunt2010zookeeper,
  author    = {Hunt, Patrick and Konar, Mahadev and Junqueira, Flavio P. and Reed, Benjamin},
  title     = {{ZooKeeper}: Wait-Free Coordination for Internet-Scale Systems},
  booktitle = {Proceedings of the 2010 USENIX Annual Technical Conference (USENIX ATC '10)},
  address   = {Boston, MA},
  year      = {2010},
  publisher = {USENIX Association}
}

@inproceedings{junqueira2011zab,
  author    = {Junqueira, Flavio P. and Reed, Benjamin C. and Serafini, Marco},
  title     = {{Zab}: High-Performance Broadcast for Primary-Backup Systems},
  booktitle = {Proceedings of the 41st IEEE/IFIP International Conference on Dependable Systems and Networks (DSN 2011)},
  pages     = {245--256},
  year      = {2011},
  publisher = {IEEE},
  doi       = {10.1109/DSN.2011.5958223}
}

@article{elnozahy2002survey,
  author  = {Elnozahy, E. N. (Mootaz) and Alvisi, Lorenzo and Wang, Yi-Min and Johnson, David B.},
  title   = {A Survey of Rollback-Recovery Protocols in Message-Passing Systems},
  journal = {ACM Computing Surveys},
  volume  = {34},
  number  = {3},
  pages   = {375--408},
  month   = sep,
  year    = {2002},
  doi     = {10.1145/568522.568525}
}

@article{randell1975system,
  author  = {Randell, Brian},
  title   = {System Structure for Software Fault Tolerance},
  journal = {IEEE Transactions on Software Engineering},
  volume  = {SE-1},
  number  = {2},
  pages   = {220--232},
  year    = {1975},
  doi     = {10.1109/TSE.1975.6312842}
}

@article{chandy1985snapshots,
  author  = {Chandy, K. Mani and Lamport, Leslie},
  title   = {Distributed Snapshots: Determining Global States of Distributed Systems},
  journal = {ACM Transactions on Computer Systems},
  volume  = {3},
  number  = {1},
  pages   = {63--75},
  month   = feb,
  year    = {1985},
  doi     = {10.1145/214451.214456}
}

@article{elnozahy1992manetho,
  author  = {Elnozahy, E. N. and Zwaenepoel, Willy},
  title   = {{Manetho}: Transparent Rollback-Recovery with Low Overhead, Limited Rollback, and Fast Output Commit},
  journal = {IEEE Transactions on Computers},
  volume  = {41},
  number  = {5},
  pages   = {526--531},
  year    = {1992},
  doi     = {10.1109/12.142678}
}

@article{carbone2015flink,
  author  = {Carbone, Paris and F{\'o}ra, Gyula and Ewen, Stephan and Haridi, Seif and Tzoumas, Kostas},
  title   = {Lightweight Asynchronous Snapshots for Distributed Dataflows},
  journal = {arXiv preprint arXiv:1506.08603},
  year    = {2015},
  url     = {https://arxiv.org/abs/1506.08603}
}

@article{young1974first,
  author  = {Young, John W.},
  title   = {A First Order Approximation to the Optimum Checkpoint Interval},
  journal = {Communications of the ACM},
  volume  = {17},
  number  = {9},
  pages   = {530--531},
  year    = {1974},
  doi     = {10.1145/361147.361115}
}

@article{daly2006higher,
  author  = {Daly, John T.},
  title   = {A Higher Order Estimate of the Optimum Checkpoint Interval for Restart Dumps},
  journal = {Future Generation Computer Systems},
  volume  = {22},
  number  = {3},
  pages   = {303--312},
  year    = {2006},
  doi     = {10.1016/j.future.2004.11.016}
}

@inproceedings{moody2010scr,
  author    = {Moody, Adam and Bronevetsky, Greg and Mohror, Kathryn and de Supinski, Bronis R.},
  title     = {Design, Modeling, and Evaluation of a Scalable Multi-Level Checkpointing System},
  booktitle = {Proceedings of the 2010 ACM/IEEE International Conference for High Performance Computing, Networking, Storage and Analysis (SC '10)},
  pages     = {1--11},
  year      = {2010},
  publisher = {IEEE},
  doi       = {10.1109/SC.2010.18}
}

@inproceedings{nicolae2019veloc,
  author    = {Nicolae, Bogdan and Moody, Adam and Gonsiorowski, Elsa and Mohror, Kathryn and Cappello, Franck},
  title     = {{VeloC}: Towards High Performance Adaptive Asynchronous Checkpointing at Large Scale},
  booktitle = {Proceedings of the 2019 IEEE International Parallel and Distributed Processing Symposium (IPDPS 2019)},
  pages     = {911--920},
  address   = {Rio de Janeiro, Brazil},
  year      = {2019},
  publisher = {IEEE},
  doi       = {10.1109/IPDPS.2019.00099}
}

@inproceedings{akkoyunlu1975constraints,
  author    = {Akkoyunlu, E. A. and Ekanadham, K. and Huber, R. V.},
  title     = {Some Constraints and Tradeoffs in the Design of Network Communications},
  booktitle = {Proceedings of the 5th ACM Symposium on Operating Systems Principles (SOSP '75)},
  pages     = {67--74},
  year      = {1975},
  publisher = {ACM},
  doi       = {10.1145/800213.806523}
}

@incollection{gray1978notes,
  author    = {Gray, Jim},
  title     = {Notes on Data Base Operating Systems},
  booktitle = {Operating Systems: An Advanced Course},
  series    = {Lecture Notes in Computer Science},
  volume    = {60},
  pages     = {393--481},
  publisher = {Springer},
  year      = {1978},
  doi       = {10.1007/3-540-08755-9_9}
}

@article{skeen1983formal,
  author  = {Skeen, Dale and Stonebraker, Michael},
  title   = {A Formal Model of Crash Recovery in a Distributed System},
  journal = {IEEE Transactions on Software Engineering},
  volume  = {SE-9},
  number  = {3},
  pages   = {219--228},
  year    = {1983},
  doi     = {10.1109/TSE.1983.236608}
}

@article{mohan1992aries,
  author  = {Mohan, C. and Haderle, Don and Lindsay, Bruce and Pirahesh, Hamid and Schwarz, Peter},
  title   = {{ARIES}: A Transaction Recovery Method Supporting Fine-Granularity Locking and Partial Rollbacks Using Write-Ahead Logging},
  journal = {ACM Transactions on Database Systems},
  volume  = {17},
  number  = {1},
  pages   = {94--162},
  year    = {1992},
  doi     = {10.1145/128765.128770}
}

@book{richardson2018microservices,
  author    = {Richardson, Chris},
  title     = {Microservices Patterns: With Examples in Java},
  publisher = {Manning Publications},
  address   = {Shelter Island, NY},
  year      = {2018},
  isbn      = {978-1617294549}
}

@misc{temporal-docs,
  author       = {{Temporal Technologies}},
  title        = {Temporal Documentation: Workflow Definition, Deterministic Constraints, and Replay},
  howpublished = {\url{https://docs.temporal.io/workflow-definition}},
  year         = {2026},
  note         = {Accessed 2026-08-30}
}

@misc{azure-durable-docs,
  author       = {{Microsoft}},
  title        = {Durable Functions Orchestrations and Orchestrator Function Code Constraints},
  howpublished = {\url{https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-orchestrations}},
  year         = {2026},
  note         = {Accessed 2026-08-30}
}

@misc{aws-stepfunctions-docs,
  author       = {{Amazon Web Services}},
  title        = {{AWS Step Functions} Developer Guide},
  howpublished = {\url{https://docs.aws.amazon.com/step-functions/latest/dg/}},
  year         = {2026},
  note         = {Accessed 2026-08-30}
}

@article{lamport1982byzantine,
  author  = {Lamport, Leslie and Shostak, Robert and Pease, Marshall},
  title   = {The {Byzantine} Generals Problem},
  journal = {ACM Transactions on Programming Languages and Systems},
  volume  = {4},
  number  = {3},
  pages   = {382--401},
  month   = jul,
  year    = {1982},
  doi     = {10.1145/357172.357176}
}

@inproceedings{castro1999pbft,
  author    = {Castro, Miguel and Liskov, Barbara},
  title     = {Practical {Byzantine} Fault Tolerance},
  booktitle = {Proceedings of the 3rd USENIX Symposium on Operating Systems Design and Implementation (OSDI '99)},
  pages     = {173--186},
  address   = {New Orleans, LA},
  month     = feb,
  year      = {1999},
  publisher = {USENIX Association}
}

@article{knight1986experimental,
  author  = {Knight, John C. and Leveson, Nancy G.},
  title   = {An Experimental Evaluation of the Assumption of Independence in Multiversion Programming},
  journal = {IEEE Transactions on Software Engineering},
  volume  = {SE-12},
  number  = {1},
  pages   = {96--109},
  year    = {1986},
  doi     = {10.1109/TSE.1986.6312924}
}

@article{huang1984abft,
  author  = {Huang, Kuang-Hua and Abraham, Jacob A.},
  title   = {Algorithm-Based Fault Tolerance for Matrix Operations},
  journal = {IEEE Transactions on Computers},
  volume  = {C-33},
  number  = {6},
  pages   = {518--528},
  year    = {1984},
  doi     = {10.1109/TC.1984.1676475}
}

@article{bland2013ulfm,
  author  = {Bland, Wesley and Bouteiller, Aurelien and Herault, Thomas and Bosilca, George and Dongarra, Jack},
  title   = {Post-Failure Recovery of {MPI} Communication Capability: Design and Rationale},
  journal = {International Journal of High Performance Computing Applications},
  volume  = {27},
  number  = {3},
  pages   = {244--254},
  year    = {2013},
  doi     = {10.1177/1094342013488238}
}

@article{losada2020ulfm,
  author  = {Losada, Nuria and Bosilca, George and Bouteiller, Aur{\'e}lien and Gonz{\'a}lez, Patricia and Mart{\'i}n, Mar{\'i}a J.},
  title   = {Fault Tolerance of {MPI} Applications in Exascale Systems: The {ULFM} Solution},
  journal = {Future Generation Computer Systems},
  volume  = {106},
  pages   = {467--481},
  year    = {2020},
  doi     = {10.1016/j.future.2020.01.026},
  note    = {[UNVERIFIED] Author order and full author list should be confirmed against the published version}
}

@phdthesis{armstrong2003thesis,
  author  = {Armstrong, Joe},
  title   = {Making Reliable Distributed Systems in the Presence of Software Errors},
  school  = {Royal Institute of Technology (KTH)},
  address = {Stockholm, Sweden},
  year    = {2003}
}

@misc{otp-supervisor-docs,
  author       = {{Ericsson AB}},
  title        = {Erlang/OTP {\tt supervisor} Behaviour --- Reference Manual},
  howpublished = {\url{https://www.erlang.org/doc/apps/stdlib/supervisor.html}},
  year         = {2026},
  note         = {Accessed 2026-08-30}
}

@book{nygard2007release,
  author    = {Nygard, Michael T.},
  title     = {Release It! Design and Deploy Production-Ready Software},
  publisher = {Pragmatic Bookshelf},
  year      = {2007},
  isbn      = {978-0978739218}
}
```

