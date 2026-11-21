# How it works

## The idea in one picture

When a model reads a prompt, it builds an internal state: what it "has in mind" after reading it. Keeping that state
means the text does not have to be read again. The state of a word depends on everything before it, so a saved state
can only be reused if the new prompt **starts with exactly the same tokens**, from the first one. Think of a bookmark:
"I have read up to here, word for word."

An agent re-sends its whole conversation on every call: system prompt, tool definitions, every message and every file
read so far, plus the new step. So call N+1 always starts exactly like call N. That is why reuse is so high in agentic
use (about 92% of every prompt in our benchmark).

## Three layers keep the bookmark alive

| Layer | Where | Lives | Lost when |
|---|---|---|---|
| Strata's prompt cache | GPU + RAM | while the Strata process runs; ~4 conversations | restart, GPU reset, pushed out by other conversations |
| Snapshots (this project) | files on a fast disk (`--slot-save-path`) | until evicted (size budget) | evicted, or the model / engine / template changed |
| Canonicalisation (this project) | applied to every request | always | n/a |

### Snapshots

After each successful call the Context Manager decides whether to save the engine's state:

* the prompt is at least **8,192 tokens** (shorter ones are cheap to re-read), and
* it grew by at least **8,192 tokens** since this conversation's last snapshot, and
* this exact prompt is not already saved.

Saving takes about 1 s for 10K tokens and 2 s for 120K, in the background after the answer. The saved token count is
checked: if another request ran in between and the engine saved the wrong conversation, the snapshot is marked stale.

Before each call it decides whether to restore one:

* Strata has **lost** this conversation: it restarted (its start time or engine generation changed), or it served
  other conversations since and may have pushed this one out;
* a snapshot exists for the **deepest matching prefix** of this request;
* restoring saves at least **2,048 tokens** of reading.

A restore takes 0.7 s (10K tokens) to 3 s (120K). Strata re-checks the tokens of a restored state and re-reads any
mismatch, so a wrong restore costs time, never correctness.

### Canonicalisation

Some agents write changing details near the top of the system prompt. Hermes writes
`Conversation started: Saturday, November 21, 2026 (CET, UTC+01:00)` above ~8.5K tokens of tool definitions, so every
new day would re-read all of it. With `CTX_REORDER=1` such lines are moved from the system prompt to the start of the
first user message: the model still sees them, and the 10K-token start stays identical across days. The move is
deterministic, so every call of a conversation renders the same bytes. Patterns are configurable
(`CTX_VOLATILE_PATTERNS`).

## How a request is recognised

Each request is cut into **blocks**: the system message, the tool definitions, each message, with long texts split at
paragraph breaks by content-defined chunking. A block's id is the sha256 of its kind and exact text.

Blocks are chained: `chain(i) = sha256(chain(i-1) | block(i))`, starting from a **validity root** made of the model
id, weights hash, quantisation, engine version and chat-template hash. Two requests share a chain node exactly when
they start with the same blocks under the same validity tuple. A snapshot is filed under the chain node where its
prompt ends; changing the model or updating Strata gives new chains, so an old state is never used.

## What is stored

* PostgreSQL (`context` database): blocks (with their text), chain nodes, snapshot records, a request log.
* Disk (Strata machine): one `.bin` file per snapshot, named after its chain hash.
* The janitor keeps the folder under a budget (default 80 GB), deleting the lowest-value files first:
  `value = P(reuse) × recompute cost / size`, where P(reuse) grows with past hits and halves every 24 h without use.

## What it means in practice

| You do this | What happens |
|---|---|
| Keep working in one session | Strata's own cache does the work; snapshots are a safety net |
| Strata restarts or the GPU resets | next call restores the latest snapshot: seconds instead of minutes |
| Start a new session the next day | the system prompt + tools are reused thanks to canonicalisation |
| Resume yesterday's session | its snapshot is restored if Strata no longer has it |
| Compact the conversation | everything after the system prompt is new and read once; caching resumes afterwards |
| Open a document already read in another conversation | read again: it comes after different text (see LIMITS.md) |

Good habits: resume sessions rather than start new ones on the same topic, keep the agent's toolset and system prompt
stable, put stable reference material into the system context, and compact rarely (a high threshold such as 85%).
