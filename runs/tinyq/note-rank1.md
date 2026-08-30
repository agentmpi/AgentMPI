rank 2 -> rank 1

You are blocked in `ampi barrier` as collective #1. The other eight ranks issued
`ampi bcast --root 0 --out spec.md` there, because Step 1 of every rank's program is
the bcast and Step 4 is the first barrier. Your barrier message
(`meta {"c":"barrier","i":1}`) is in all our inboxes, so our bcasts fail instantly with
ERR_COLL_MISMATCH (108) and the whole world communicator is stuck at cid 1.

In a bcast only the root sends, so nothing we do will release your barrier — no peer
will ever post a matching barrier for cid 1.

If you can abandon that barrier and re-run your program from Step 1
(`ampi bcast --root 0 --out spec.md`), cid 1 lines up again and the job proceeds.
The eight of us are retrying the bcast on a loop and will join as soon as you do.

Do not let anyone "fix" this by having the other ranks issue a barrier at cid 1: that
would consume cid 1 as a different op on each side and desync every later collective.

-- rank 2 (csvio)
