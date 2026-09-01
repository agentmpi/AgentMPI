# E3 Durov production run: 16 ranks

Completed broker-backed run over all 99 source pages. The committed artifacts
contain the launch/provenance record, trace, trace analysis, systems report, and
figures. Licensed source text, prompts, model outputs, the assembled translation,
and the sealed SQLite journal are intentionally omitted; their integrity is bound
by the source and assembled-artifact hashes in the public summary.

The run injected one executor death after claim and continued with a replacement.
Later executor attrition at the final review fence was handled by two additional
sessions serving only the unfinished durable ranks. All 216 published tasks
finished and every rank finalized.
