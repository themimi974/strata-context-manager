# File state cache (research)

When the agent reads a file it already read in an earlier session (or before a compaction), Strata can load the
state the model computed the first time instead of reading the file again. A 9K-token file then costs about 0.5 s
instead of about 9.5 s of prompt reading.

The result is **approximate**: the loaded state is not bit-identical to a normal read. See *Quality* below and decide
whether the cost is acceptable for your use. It is off by default.

## How it works

1. **Gateway.** With `CTX_SEG=1`, the Context Manager looks for tool results of the tools in `CTX_SEG_TOOLS`
   (compose default `read_file,skill_view`: files and Hermes skills) longer than `CTX_SEG_MIN_CHARS` (4000). Each one gets an id:
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

## Reads made with a shell tool (opt-in)

Some agents read files with the shell (`cat file`, `sed -n '100,160p' file`) instead of a file tool. With
`CTX_SEG_TERMINAL=cat`, `slice` or `cat,slice` the gateway also names the output of such a call, when the command only
prints files: every part of the command (split on `&&` and `;`) must be `cat`, `sed -n 'a,bp'`, `head`, `tail`,
`nl | sed -n` or `cat | head`, optionally after `cd` and `echo` separators. A redirection, a pipe into another tool, a
build, a search or a write makes the whole command not a read. The call must have succeeded (exit code 0). The shell tool's
name is `CTX_SEG_TERMINAL_TOOLS` (default `terminal`; `bash` for some agents). A whole file must reach
`CTX_SEG_MIN_CHARS` (4000), a slice `CTX_SEG_SLICE_MIN_CHARS` (3000).

Off by default, because it rarely pays. A stored image costs about 70 MB plus 18 KB per token whatever the size of the
text, so a 1,000-token slice costs the same disk as a 7,000-token file for a gain of under a second. Measured on a
3 h 11 Hermes session on a Rust codebase (57 file reads: 14 `cat`, 7 `read_file`, 24 `sed -n` slices, 12 `head`/`tail`):

| | |
|---|---|
| Reads whose output came back as identical text | 2 of 57 (about 6 s of prompt reading) |
| Stored images this detection would have added | 8 for `cat` (1.1 GB), 12 for slices (1.1 GB) |
| Loads it would have produced inside the session | 1 for `cat`, 0 for slices |

It can only help when the same unchanged text comes back: a file re-read after a compaction or in a later session. Turn it
on for `cat` if your agent re-reads whole files across sessions; leave `slice` off unless your slices are large and repeated.
`bench/hermes-local/exact_repeats.py` measures this on your own recordings.

## Install

1. Strata: use the `file-state-cache-0.2.19` branch of the fork https://github.com/themimi974/Strata (Strata v0.2.19;
   the older `file-state-cache` branch is on v0.2.16, or apply `engine/strata-patches/*.patch` on Strata
   `v0.2.16-4-g58ee32277` with `git am`), build (MI100 build options: `FILE-STATE-CACHE.md` in that branch), run with
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
- Only whole-file reads are cached. When the agent reads part of a file (Hermes' `read_file` with an offset or a
  line range, e.g. for "what does line 123 say"), that text differs from the whole file, so it is read normally.
