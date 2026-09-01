# E3 Durov production run: 32 ranks

Completed broker-backed run over all 99 source pages, with 32 durable ranks
oversubscribed across ten executor sessions. All 232 tasks finished and every
rank finalized. The run took longer than the 16-rank cell because physical
concurrency remained fixed while per-rank research and synchronization grew.

Committed artifacts contain systems/provenance records, trace analysis, and
figures only. Licensed source, prompts, translations, assembled text, and the
sealed journal are intentionally omitted; source coverage and output integrity
are represented by hashes in the public summary.
