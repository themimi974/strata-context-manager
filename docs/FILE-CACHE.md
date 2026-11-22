# File state cache (research)

When the agent reads a file it already read in an earlier session (or before a compaction), Strata can load the
state the model computed the first time instead of reading the file again. A 9K-token file then costs about 0.5 s
instead of about 9.5 s of prompt reading.

The result is **approximate**: the loaded state is not bit-identical to a normal read. See *Quality* below and decide
whether the cost is acceptable for your use. It is off by default.

## How it works

1. **Gateway.** With `CTX_SEG=1`, the Context Manager looks for tool results of the tools in `CTX_SEG_TOOLS`
   (default `read_file`) longer than `CTX_SEG_MIN_CHARS` (4000). Each one gets an id:
   `sha256(validity tuple | exact text)`, so a changed file or another model/engine gets a new id. The hook sends
   `strata_segx: {"files": [{message, start, end, id}]}` with the request.
2. **Strata** (patched, see below) resolves each file to a token span. If `<STRATA_SEGSTORE>/v2-<id>` exists and its
   stored tokens are exactly the request's tokens, it **loads** the file; otherwise it reads it normally and
   **stores** it for next time.
3. **Load.** Attention layers: the stored K/V are copied to the new position, keys re-rotated by the shift. DeltaNet
   layers: the memory after the file is `S0 + T · W`, where `W` is the current memory and `T`, `S0` were stored at
   capture (heads that forget everything store no `T`). The indexer keys are re-pooled at the new position. The file's
   first 8 tokens + 15 % and its last 4 tokens are read normally.
4. **Fallback.** Any mismatch (different tokens, missing or broken image) means a normal read. A request never fails
   because of the cache.

## Quality

Measured on Qwen3.8-Flash-Next IQ2_XS (int8 KV, 128K context, AMD MI100), with questions whose answers are known
(quote a line, find a line number, rename an identifier as a unified diff), each asked three ways: normal read,
normal read again with only a hidden nonce changed (engine noise), and loaded from the cache.

| | Normal | Normal again | Cache |
|---|---:|---:|---:|
| All (192 questions) | 60.4 % | 59.9 % | 57.8 % (−2.6 ± 4.4 points) |
| One file, short context | 65.5 % | 67.9 % | 65.5 % |
| Deep context, several files, after compaction | 56.5 % | 53.7 % | 51.9 % |

- Loading at the same place in a fresh context: 12 of 12 answers identical to a normal read.
- The further the new context is from the capture context (depth, other files, compaction), the larger the drift.
- Capturing in a "clean" context (system prompt only) is not better than capturing inside the conversation.

## Cost

| | |
|---|---|
| Disk per file | ~211 MB for a 7.7K-token file (≈ 70 MB fixed + ~18 KB per token) |
| Load | ~8 ms GPU, ~0.4 s with the disk read |
| First read of a file | about +25 % prompt-reading time (the capture) |
| Budget | `strata-segments-janitor` keeps the store under `SEG_BUDGET_GB` (40), least recently used first |

## Install

1. Strata: use the `file-state-cache` branch of the fork https://github.com/themimi974/Strata (or apply
   `engine/strata-patches/*.patch` on Strata `v0.2.16-4-g58ee32277` with `git am`), build, run with
   `STRATA_SEGSTORE=/nvme/strata-segments` in the config's `env`.
2. Janitor: copy `engine/strata-segments-janitor.sh` to `/usr/local/bin/strata-segments-janitor` and add the cron
   line from its header. Settings in `/etc/default/strata-context`: `SEG_DIR`, `SEG_BUDGET_GB`.
3. Gateway: `CTX_SEG=1` in `deploy/.env`, restart the stack.

Roll back: `CTX_SEG=0` (Strata then reads every file normally; the patched engine behaves like the original without
`strata_segx`).

## Limits

- One model, one quantisation, one GPU tested. The patches assume the Qwen3.8-Flash-Next geometry (36 DeltaNet layers,
  12 attention layers, int8 KV).
- The questions test exact reading of code, not whole agent tasks.
- Only tools whose result is the file text are useful in `CTX_SEG_TOOLS`.
