"""E6: a production book translation, one rank per cloud machine.

The experiment the protocol was designed for.  A Russian non-fiction book is
rendered into three languages by a population of sixteen, thirty-two or
sixty-four ranks, each a Claude Code session on its own virtual machine, with
the git device as the only thing the machines share.  Every coordination
decision --- who translates what, how a name is rendered everywhere, who
researches a term once for everyone, who reviews whom, how segments join at
their seams, what happens when a machine dies --- is made by harness code
calling AgentMPI; the agent's obligation is to turn a prompt file into a result
file and submit it.

See ``README.md`` in this directory for the phases, the mechanisms each one
exercises, and how to run and analyse the series.
"""
