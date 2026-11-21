# Limits and downsides

## What it does not do

* **No gain inside one uninterrupted session.** Strata's own cache already reuses ~92% of each agent prompt.
* **No reuse of a document in a new conversation.** A cached state depends on everything before it, so the same file
  read after different text is read again. Reusing it would need composition of separately cached pieces (as in the
  HYPIC research for DeltaNet models); not built.
* **Compaction resets the reuse.** After `/compact` everything after the system prompt is new text and is read once.
* **Exact matches only.** Changing the agent's tools or system prompt, the model, the quantisation, the chat template or
  the Strata version makes every snapshot unusable. This is deliberate: a wrong state would give wrong answers.

## Costs

* **Disk.** ~350 MB per 10K-token conversation, ~2 GB at 120K. Snapshots of one conversation overlap (the DeltaNet
  state cannot be truncated, so each snapshot is complete). Default budget 80 GB.
* **Disk wear.** A long 120K-token session writes about 15 GB of snapshots. Harmless for a normal NVMe, but not zero.
* **Time when idle.** A save takes 1–2 s after an answer, in the background; a request arriving during it waits.
* **Privacy.** PostgreSQL keeps the full text of every block of every request, and snapshot files encode the
  conversations. Everything stays on your machines, but it is an archive of everything sent through the gateway.
  There is no retention limit yet beyond the snapshot budget.

## Concurrency

Strata answers one request at a time and keeps about 4 conversations in RAM. With several agents at once, requests
queue in Strata. This project helps them take turns (a pushed-out conversation is restored instead of re-read), but each
switch costs 1–3 s, and a save that collides with another agent's request is detected and discarded. Parallel agents
are limited by the engine long before disk space.

## Moving parts

A LiteLLM hook, a service, a database, a cron job, and Strata's slot API. A failure costs time, never correctness:
the hook passes requests through unchanged when the Context Manager is down, and Strata re-checks restored tokens.
