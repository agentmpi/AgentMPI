# E3 Durov p64 pilot: staggered executor admission

This attempt began while prior executor sessions still occupied the host's
ten-session limit. Filling the 64 durable ranks took longer than the declared
task/rank lease, so early participants advanced after peers were marked failed.
The run is retained as a launcher precondition failure, not a scale result.

The completed p64 run starts all ten executor serve-sets in one wave. Licensed
payloads and the sealed journal are omitted.
