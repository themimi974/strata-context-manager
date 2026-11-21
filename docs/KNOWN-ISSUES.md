# Known issues

## Strata: a snapshot saved from a pinned request restores only the pinned prefix

`strata_prefix` lets a request pin a prefix (for example the system prompt and tools) so Strata never evicts it. When a
slot is saved after such a request and restored later, Strata reuses only the pinned part.

Reproduction (Strata v0.2.16, `bench/pin_restore_test.py`), 20,604-token prompt:

| The saved request had the pin | Restored, then the same prompt + 7 tokens | Reused | Read |
|---|---|---:|---:|
| yes | 10,316 tokens reused (the pinned prefix) | 10,316 | 10,295 (13 s) |
| no | full state reused | 20,599 | 12 (0.3 s) |

Workaround: the tier-0 pin is off by default (`CTX_PIN_SYSTEM=0`). For a single agent, snapshots are worth far more
than the pin. Report to upstream pending.

## LiteLLM: `extra_body` set in a pre-call hook is dropped

A field added to `data["extra_body"]` in `async_pre_call_hook` never reaches an `openai/` backend (checked by capturing
the request on the Strata side). Unknown top-level keys are forwarded, so the hook sends `strata_prefix` at the top
level. Relevant only when the pin is on.

## "conversation snapshot running-state copy: unspecified launch failure"

Seen in Strata's log on a VM with a passed-through MI100. This is a sticky HIP error left by an earlier GPU fault (the
VM's amdgpu driver sometimes faults in the DMA engine); the snapshot copy is just the first call that reports it. The
engine must be restarted; the watchdog in `engine/` does it, and the next request restores the latest snapshot.

## The engine's page-cache and heat matter for timings

On this hardware a GPU at 101 °C throttled the clock by 20–25% and every timing worsened; long benchmarks need the
temperature checked first.
