"""AgentMPI tooling: what a harness author uses around the protocol.

The protocol itself is :mod:`ampi` (the runtime over the six-operation device
interface).  This package is everything that is useful but not normative:

* :mod:`ampitools.harness` --- the SPMD driver that runs a rank program over a job;
* :mod:`ampitools.executor` --- function, replay and broker (pull-queue) executors;
* :mod:`ampitools.model` --- an executor over a chat-completions endpoint with a
  bounded tool loop, contract repair and per-call accounting;
* :mod:`ampitools.tools` --- the minimal research tools a raw endpoint lacks;
* :mod:`ampitools.launcher` --- ``ampirun``, the process manager: one process per
  rank, on one machine or across several;
* :mod:`ampitools.doctor` --- the diagnosis of a wedged job;
* :mod:`ampitools.analysis` --- trace analysis, figures, reports and the viewer.

Nothing here is required by the specification, and nothing in :mod:`ampi`
depends on it except the convenience subcommands of the command binding.
"""
