----------------------------- MODULE AgentMPI -----------------------------
\* A bounded abstract model of the AgentMPI safety kernel.
\*
\* This model deliberately excludes model inference and external effects. It
\* checks communicator epochs, scoped send ordering, collective mismatch
\* diagnosis, crash/revoke/repair, and persistent fencing high-water marks.

EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS Agents, Tags, Values, AnySource, AnyTag, NoAgent, NoKind, MaxOperations

ASSUME /\ Agents # {}
       /\ Tags # {}
       /\ AnySource \notin Agents
       /\ AnyTag \notin Tags
       /\ NoAgent \notin Agents
       /\ NoKind \notin Tags
       /\ MaxOperations \in Nat
       /\ MaxOperations > 0

VARIABLES
    epoch,                 \* immutable communicator generation
    active,                \* immutable members of the current generation
    failed,                \* failures observed in the current generation
    revoked,               \* current generation rejects new work
    nextSequence,          \* per source/destination issue sequence
    messages,              \* admitted, unmatched messages
    delivered,             \* messages matched in this generation
    collectiveSlot,        \* one sequence across every collective kind
    collectiveKind,        \* descriptor chosen by first entrant
    collectiveEntries,     \* current slot's entered members
    collectiveError,       \* mismatched descriptor was diagnosed
    fence,                 \* persistent lock high-water mark
    lockOwner              \* current advisory lock owner

vars ==
    <<epoch, active, failed, revoked, nextSequence, messages, delivered,
      collectiveSlot, collectiveKind, collectiveEntries, collectiveError,
      fence, lockOwner>>

MessageType ==
    [id: 1..MaxOperations,
     source: Agents,
     destination: Agents,
     tag: Tags,
     value: Values,
     sequence: 0..MaxOperations,
     messageEpoch: 0..MaxOperations]

Init ==
    /\ epoch = 0
    /\ active = Agents
    /\ failed = {}
    /\ revoked = FALSE
    /\ nextSequence = [a \in Agents |-> [b \in Agents |-> 0]]
    /\ messages = {}
    /\ delivered = {}
    /\ collectiveSlot = 0
    /\ collectiveKind = NoKind
    /\ collectiveEntries = {}
    /\ collectiveError = FALSE
    /\ fence = 0
    /\ lockOwner = NoAgent

SentCount == Cardinality(messages \union delivered)

Send(a, b, t, v) ==
    /\ ~revoked
    /\ a \in active \ failed
    /\ b \in active \ failed
    /\ t \in Tags
    /\ v \in Values
    /\ SentCount < MaxOperations
    /\ LET message ==
               [id |-> SentCount + 1,
                source |-> a,
                destination |-> b,
                tag |-> t,
                value |-> v,
                sequence |-> nextSequence[a][b],
                messageEpoch |-> epoch]
       IN messages' = messages \union {message}
    /\ nextSequence' = [nextSequence EXCEPT ![a][b] = @ + 1]
    /\ UNCHANGED <<epoch, active, failed, revoked, delivered, collectiveSlot,
                   collectiveKind, collectiveEntries, collectiveError,
                   fence, lockOwner>>

Matches(message, receiver, sourceSelector, tagSelector) ==
    /\ message.destination = receiver
    /\ message.messageEpoch = epoch
    /\ (sourceSelector = AnySource \/ sourceSelector = message.source)
    /\ (tagSelector = AnyTag \/ tagSelector = message.tag)

EarlierEligible(candidate, receiver, sourceSelector, tagSelector) ==
    \E earlier \in messages:
        /\ earlier.source = candidate.source
        /\ earlier.destination = candidate.destination
        /\ earlier.messageEpoch = candidate.messageEpoch
        /\ earlier.sequence < candidate.sequence
        /\ Matches(earlier, receiver, sourceSelector, tagSelector)

Receive(receiver, sourceSelector, tagSelector) ==
    /\ ~revoked
    /\ receiver \in active \ failed
    /\ sourceSelector \in Agents \union {AnySource}
    /\ tagSelector \in Tags \union {AnyTag}
    /\ \E candidate \in messages:
           /\ Matches(candidate, receiver, sourceSelector, tagSelector)
           /\ ~EarlierEligible(candidate, receiver, sourceSelector, tagSelector)
           /\ messages' = messages \ {candidate}
           /\ delivered' = delivered \union {candidate}
    /\ UNCHANGED <<epoch, active, failed, revoked, nextSequence, collectiveSlot,
                   collectiveKind, collectiveEntries, collectiveError,
                   fence, lockOwner>>

EnterCollective(a, kind) ==
    /\ ~revoked
    /\ a \in active \ failed
    /\ kind \in Tags
    /\ collectiveSlot < MaxOperations
    /\ a \notin collectiveEntries
    /\ IF collectiveKind = NoKind
          THEN /\ collectiveKind' = kind
               /\ collectiveEntries' = collectiveEntries \union {a}
               /\ collectiveError' = FALSE
               /\ revoked' = revoked
          ELSE IF collectiveKind = kind
                  THEN /\ collectiveKind' = collectiveKind
                       /\ collectiveEntries' = collectiveEntries \union {a}
                       /\ collectiveError' = collectiveError
                       /\ revoked' = revoked
                  ELSE /\ collectiveKind' = collectiveKind
                       /\ collectiveEntries' = collectiveEntries
                       /\ collectiveError' = TRUE
                       /\ revoked' = TRUE
    /\ UNCHANGED <<epoch, active, failed, nextSequence, messages, delivered,
                   collectiveSlot, fence, lockOwner>>

FinishCollective ==
    /\ ~revoked
    /\ collectiveKind # NoKind
    /\ collectiveEntries = active
    /\ collectiveSlot < MaxOperations
    /\ collectiveSlot' = collectiveSlot + 1
    /\ collectiveKind' = NoKind
    /\ collectiveEntries' = {}
    /\ collectiveError' = FALSE
    /\ UNCHANGED <<epoch, active, failed, revoked, nextSequence, messages,
                   delivered, fence, lockOwner>>

Crash(a) ==
    /\ a \in active \ failed
    /\ failed' = failed \union {a}
    /\ revoked' = TRUE
    /\ lockOwner' = IF lockOwner = a THEN NoAgent ELSE lockOwner
    /\ UNCHANGED <<epoch, active, nextSequence, messages, delivered, collectiveSlot,
                   collectiveKind, collectiveEntries, collectiveError, fence>>

Repair ==
    /\ revoked
    /\ active \ failed # {}
    /\ epoch < MaxOperations
    /\ epoch' = epoch + 1
    /\ active' = active \ failed
    /\ failed' = {}
    /\ revoked' = FALSE
    /\ nextSequence' = [a \in Agents |-> [b \in Agents |-> 0]]
    /\ messages' = {}
    /\ delivered' = {}
    /\ collectiveSlot' = 0
    /\ collectiveKind' = NoKind
    /\ collectiveEntries' = {}
    /\ collectiveError' = FALSE
    /\ UNCHANGED <<fence, lockOwner>>

Acquire(a) ==
    /\ a \in active \ failed
    /\ lockOwner = NoAgent
    /\ fence < MaxOperations
    /\ lockOwner' = a
    /\ fence' = fence + 1
    /\ UNCHANGED <<epoch, active, failed, revoked, nextSequence, messages, delivered,
                   collectiveSlot, collectiveKind, collectiveEntries,
                   collectiveError>>

Release(a) ==
    /\ lockOwner = a
    /\ lockOwner' = NoAgent
    /\ UNCHANGED <<epoch, active, failed, revoked, nextSequence, messages, delivered,
                   collectiveSlot, collectiveKind, collectiveEntries,
                   collectiveError, fence>>

Next ==
    \/ \E a, b \in Agents, t \in Tags, v \in Values: Send(a, b, t, v)
    \/ \E b \in Agents, s \in Agents \union {AnySource},
          t \in Tags \union {AnyTag}: Receive(b, s, t)
    \/ \E a \in Agents, kind \in Tags: EnterCollective(a, kind)
    \/ FinishCollective
    \/ \E a \in Agents: Crash(a)
    \/ Repair
    \/ \E a \in Agents: Acquire(a)
    \/ \E a \in Agents: Release(a)

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ epoch \in 0..MaxOperations
    /\ active \subseteq Agents
    /\ failed \subseteq active
    /\ revoked \in BOOLEAN
    /\ nextSequence \in [Agents -> [Agents -> 0..MaxOperations]]
    /\ messages \subseteq MessageType
    /\ delivered \subseteq MessageType
    /\ collectiveSlot \in 0..MaxOperations
    /\ collectiveKind \in Tags \union {NoKind}
    /\ collectiveEntries \subseteq Agents
    /\ collectiveError \in BOOLEAN
    /\ fence \in 0..MaxOperations
    /\ lockOwner \in Agents \union {NoAgent}

UniqueMessageIds ==
    \A left, right \in messages \union delivered:
        left.id = right.id => left = right

NoCrossEpochDelivery ==
    \A message \in messages \union delivered:
        message.messageEpoch = epoch

CollectiveEntriesAreMembers == collectiveEntries \subseteq active

MismatchRevokes == collectiveError => revoked

FenceHeldByMember == lockOwner = NoAgent \/ lockOwner \in active

Safety ==
    /\ TypeOK
    /\ UniqueMessageIds
    /\ NoCrossEpochDelivery
    /\ CollectiveEntriesAreMembers
    /\ MismatchRevokes
    /\ FenceHeldByMember

=============================================================================
