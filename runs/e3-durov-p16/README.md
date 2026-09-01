# E3 Durov pilot: claim lease too short

This intentionally retained failed pilot used a 30-second broker claim lease for
research operations taking roughly one to two minutes. Healthy work was therefore
requeued as if its executor had died. The trace records ten requeues, nine
accepted research results, and the deliberate executor death.

The failure established that a claim lease must bound the operation service-time
tail, not merely broker polling latency. Licensed prompts/results and the sealed
journal are omitted; the committed trace and analysis contain no book text.
