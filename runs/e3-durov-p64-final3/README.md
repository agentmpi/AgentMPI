# E3 Durov production run: 64 ranks

Completed broker-backed run over all 99 pages, with 64 durable ranks served by
ten concurrent executors rotated through 27 bounded-lifetime sessions. All 263
tasks finished, one stale claim was transparently requeued, and every rank
finalized.

Committed artifacts contain systems/provenance records, trace analysis, and
figures only. Licensed source, prompts, translations, assembled text, and the
sealed journal are omitted. Source coverage, schema validity, and output
integrity are represented by hashes and normalized coverage in the public
summary.
