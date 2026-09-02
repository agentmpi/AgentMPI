# E3 Durov p64 pilot: physical-rank contract substitution

This run exposed an oversubscription bug in `BrokerExecutor.submit`: output
contracts expanded `{rank}` with the executor's primary rank instead of the
durable task rank. Correct rank-12 output could be rejected, rewritten as rank 2,
accepted by the broker, and then rejected by the harness.

Broker validation now substitutes the task record's rank and has a regression
test. The succeeding run also rotates sessions after 12 tasks to avoid executor
context drift. Licensed payloads and the sealed journal are omitted.
