# rank 8 — scatter mismatch

The step-2 scatter delivered me a chunk containing:

    {"rank": 2, "module": "tinyq/csvio.py", "depends_on": ["schema"], "also": []}

My rank is 8. The frozen spec assigns `tinyq/cli.py` and `tinyq/__main__.py`
to rank 8, and `tinyq/csvio.py` to rank 2.

Resolution: I am following the spec, which is normative, and implementing
`tinyq/cli.py` + `tinyq/__main__.py`. I am NOT writing `tinyq/csvio.py`.

If another rank also received a chunk labelled with a rank that is not its
own, use the spec's owner table, not `assignment.json`. If nobody ends up
owning `tinyq/csvio.py`, that is the gap to close — flag it to rank 0.
