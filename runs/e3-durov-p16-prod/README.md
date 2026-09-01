# E3 Durov pilot: rank lease not extended

This retained failed attempt corrected the broker claim lease but exposed a
different lifecycle defect. Harness ranks entered long broker waits after a
default heartbeat; early ranks reaching `allreduce` later treated peers still
performing research as failed. The run was stopped rather than reported as a
scale result.

The harness now extends each rank lease to the declared task timeout before every
external model call. Licensed prompts/results and the sealed journal are omitted;
the committed trace and analysis contain no book text.
