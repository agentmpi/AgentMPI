from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import agentmpi_sql.runtime as runtime_module
from agentmpi_sql import (
    ANY_SOURCE,
    ANY_TAG,
    Communicator,
    CommunicatorRevoked,
    DeliveryMode,
    LockUnavailable,
    ProcessFailed,
    ProtocolViolation,
    ReduceOp,
    ResourceExhausted,
    Runtime,
    Timeout,
)


def runtimes(
    tmp_path: Path,
    size: int,
    *,
    context_budget: int = 32_000,
    mailbox_bytes: int = 8 * 1024 * 1024,
    inline_token_limit: int = 2_048,
) -> list[Runtime]:
    db = tmp_path / "session.db"
    Runtime.initialize(
        db,
        size=size,
        session_id="test",
        context_budget=context_budget,
        mailbox_bytes=mailbox_bytes,
        inline_token_limit=inline_token_limit,
    )
    return [Runtime.attach(db, "test", rank) for rank in range(size)]


def close_all(items: list[Runtime]) -> None:
    for item in items:
        item.close()


def test_ordered_point_to_point_and_wildcards(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 3)
    try:
        rs[0].send({"step": 1}, 2, tag="work")
        rs[0].send({"step": 2}, 2, tag="work")
        rs[1].send({"step": "other"}, 2, tag="control")

        first = rs[2].recv(source=0, tag="work", timeout=1)
        second = rs[2].recv(source=0, tag="work", timeout=1)
        third = rs[2].recv(source=ANY_SOURCE, timeout=1)

        assert first.payload == {"step": 1}
        assert second.payload == {"step": 2}
        assert first.status.sequence == 0
        assert second.status.sequence == 1
        assert third.status.source == 1
    finally:
        close_all(rs)


def test_cross_tag_order_ignores_wall_clock_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rs = runtimes(tmp_path, 2)
    original_time = runtime_module.time.time
    try:
        rs[0].send("first", 1, tag="a")
        monkeypatch.setattr(runtime_module.time, "time", lambda: original_time() - 60)
        rs[0].send("second", 1, tag="b")

        first = rs[1].recv(source=0, tag=ANY_TAG, timeout=1)
        second = rs[1].recv(source=0, tag=ANY_TAG, timeout=1)
        assert (first.payload, second.payload) == ("first", "second")
        assert (first.status.sequence, second.status.sequence) == (0, 1)

        rs[0].send("literal-star", 1, tag="*")
        assert rs[1].recv(source=0, tag="*", timeout=1).payload == "literal-star"
    finally:
        close_all(rs)


def test_earlier_posted_receive_wins_match(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2)
    second_receiver = Runtime(rs[1].db_path, "test", 1, poll_interval=0.001)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(rs[1].recv, source=ANY_SOURCE, timeout=1)
            time.sleep(0.03)
            second = pool.submit(
                second_receiver.recv,
                source=0,
                tag="work",
                timeout=0.15,
            )
            time.sleep(0.03)
            rs[0].send("owned-by-first", 1, tag="work")
            assert first.result(timeout=1).payload == "owned-by-first"
            with pytest.raises(Timeout):
                second.result(timeout=1)
    finally:
        second_receiver.close()
        close_all(rs)


def test_unjoined_and_stale_incarnations_cannot_operate(tmp_path: Path) -> None:
    db = tmp_path / "session.db"
    Runtime.initialize(db, size=2, session_id="test")
    unjoined = Runtime(db, "test", 0)
    try:
        with pytest.raises(ProcessFailed):
            unjoined.send("invalid", 1)
    finally:
        unjoined.close()

    first = Runtime.attach(db, "test", 0)
    observer = Runtime.attach(db, "test", 1)
    try:
        observer.fail_rank(0)
        replacement = Runtime.attach(db, "test", 0)
        try:
            with pytest.raises(ProcessFailed):
                first.heartbeat()
            replacement.heartbeat()
        finally:
            replacement.close()
    finally:
        first.close()
        observer.close()


def test_forged_communicator_membership_is_rejected(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 3)
    try:
        canonical = rs[0].create_communicator((0, 1), name="private")
        forged = Communicator(
            id=canonical.id,
            session_id=canonical.session_id,
            generation=canonical.generation,
            members=(0, 1, 2),
            name=canonical.name,
        )
        with pytest.raises(ProtocolViolation, match="canonical membership"):
            rs[2].send("injected", 0, comm=forged)
        with pytest.raises(ProtocolViolation, match="canonical membership"):
            rs[0].revoke(forged)
    finally:
        close_all(rs)


def test_synchronous_send_waits_for_receive(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2)
    completed = threading.Event()

    def sender() -> None:
        rs[0].send(
            "rendezvous",
            1,
            mode=DeliveryMode.SYNCHRONOUS,
            timeout=2,
        )
        completed.set()

    thread = threading.Thread(target=sender)
    thread.start()
    time.sleep(0.1)
    assert not completed.is_set()
    assert rs[1].recv(timeout=1).payload == "rendezvous"
    thread.join(timeout=1)
    assert completed.is_set()
    close_all(rs)


def test_collectives_have_rank_consistent_results(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 4)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            gathered = list(
                pool.map(
                    lambda pair: pair[1].allgather({"rank": pair[0]}, timeout=2),
                    enumerate(rs),
                )
            )
        expected = [{"rank": rank} for rank in range(4)]
        assert gathered == [expected] * 4

        with ThreadPoolExecutor(max_workers=4) as pool:
            totals = list(
                pool.map(
                    lambda pair: pair[1].allreduce(pair[0] + 1, op=ReduceOp.SUM, timeout=2),
                    enumerate(rs),
                )
            )
        assert totals == [10, 10, 10, 10]

        with ThreadPoolExecutor(max_workers=4) as pool:
            broadcasts = list(
                pool.map(
                    lambda pair: (
                        pair[1].bcast(
                            {"policy": "bounded"},
                            root=2,
                            timeout=2,
                        )
                        if pair[0] == 2
                        else pair[1].bcast(root=2, timeout=2)
                    ),
                    enumerate(rs),
                )
            )
        assert broadcasts == [{"policy": "bounded"}] * 4
    finally:
        close_all(rs)


def test_subcommunicator_uses_local_ranks_for_messages_and_collectives(
    tmp_path: Path,
) -> None:
    rs = runtimes(tmp_path, 3)
    try:
        comm = rs[1].create_communicator((1, 2), name="workers")
        rs[1].send("local-rank-message", 1, tag="work", comm=comm)
        received = rs[2].recv(source=0, tag="work", comm=comm, timeout=1)
        assert received.payload == "local-rank-message"
        assert received.status.source == 0

        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(
                pool.map(
                    lambda runtime: runtime.bcast(
                        "from-local-root-1" if runtime.rank == 2 else None,
                        root=1,
                        comm=comm,
                        timeout=2,
                    ),
                    rs[1:],
                )
            )
        assert values == ["from-local-root-1", "from-local-root-1"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda runtime: runtime.barrier(comm=comm, timeout=2),
                    rs[1:],
                )
            )
    finally:
        close_all(rs)


def test_collective_order_mismatch_fails_all_participants(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            barrier = pool.submit(rs[0].barrier, timeout=1)
            broadcast = pool.submit(rs[1].bcast, "value", root=1, timeout=1)
            with pytest.raises(ProtocolViolation, match="collective ordinal 0 mismatch"):
                broadcast.result(timeout=2)
            with pytest.raises(ProtocolViolation, match="collective ordinal 0 mismatch"):
                barrier.result(timeout=2)
    finally:
        close_all(rs)


def test_artifact_spill_and_context_admission(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2, context_budget=40, inline_token_limit=4)
    try:
        original = {"text": "large payload " * 100}
        status = rs[0].send(original, 1)
        assert status.artifact_ref is not None
        received = rs[1].recv(timeout=1)
        assert received.payload["_agentmpi_artifact"] == status.artifact_ref

        with pytest.raises(ResourceExhausted):
            rs[1].get_artifact(status.artifact_ref)
        rs[1].reset_context()
        raw = rs[1].get_artifact(status.artifact_ref, charge_context=False)
        assert json.loads(raw) == original
    finally:
        close_all(rs)


def test_mailbox_backpressure(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2, mailbox_bytes=20, inline_token_limit=10_000)
    try:
        rs[0].send("1234567890", 1)
        with pytest.raises(ResourceExhausted):
            rs[0].send("abcdefghij", 1)
        assert rs[1].recv(timeout=1).payload == "1234567890"
        rs[0].send("abcdefghij", 1)
    finally:
        close_all(rs)


def test_revoke_and_shrink_exclude_failed_rank(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 3)
    try:
        old_world = rs[0].world
        rs[0].fail_rank(2)
        with pytest.raises(CommunicatorRevoked):
            rs[1].send("blocked", 0, comm=old_world)

        repaired0 = rs[0].shrink(old_world)
        repaired1 = rs[1].shrink(old_world)
        assert repaired0.id == repaired1.id
        assert repaired0.members == (0, 1)
        rs[0].send("recovered", 1, comm=repaired0)
        assert rs[1].recv(source=0, comm=repaired1, timeout=1).payload == "recovered"
    finally:
        close_all(rs)


def test_shrink_is_idempotent_and_preserves_parent_lineage(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 4)
    try:
        first = rs[0].create_communicator((0, 1, 2), name="first")
        second = rs[0].create_communicator((0, 1, 3), name="second")
        rs[0].fail_rank(2)
        first_child = rs[0].shrink(first)
        rs[0].fail_rank(3)
        second_child = rs[0].shrink(second)

        assert first_child.id != second_child.id
        assert first_child.members == second_child.members == (0, 1)
        assert rs[0].shrink(first_child).id == first_child.id
    finally:
        close_all(rs)


def test_lease_locks_use_fencing_tokens(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2)
    try:
        token1 = rs[0].acquire_lock("workspace", lease_seconds=0.05)
        with pytest.raises(LockUnavailable):
            rs[1].acquire_lock("workspace")
        time.sleep(0.06)
        token2 = rs[1].acquire_lock("workspace")
        assert token2 > token1
        with pytest.raises(LockUnavailable):
            rs[0].release_lock("workspace", token1)
        rs[1].release_lock("workspace", token2)
        token3 = rs[0].acquire_lock("workspace")
        assert token3 > token2
        rs[0].release_lock("workspace", token3)
    finally:
        close_all(rs)


def test_context_accounting_rejects_invalid_reset(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 1, context_budget=10)
    try:
        with pytest.raises(ValueError):
            rs[0].reset_context(used=-1)
        with pytest.raises(ValueError):
            rs[0].reset_context(used=11)
    finally:
        close_all(rs)


def test_trace_is_auditable_and_monotonic(tmp_path: Path) -> None:
    rs = runtimes(tmp_path, 2)
    try:
        rs[0].send({"task": "review"}, 1, tag="assignment")
        rs[1].recv(source=0, tag="assignment", timeout=1)
        events = rs[0].trace()
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(sequences)
        assert {"agent.join", "message.send", "message.recv"} <= {
            event["kind"] for event in events
        }
    finally:
        close_all(rs)
