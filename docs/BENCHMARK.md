# Benchmark: Hermes documents a lab, with and without strata-context-manager

Run on 21 Nov 2026, 12:26–13:19 CET. Scripts: [bench/](../bench/).

## Question

How much less time does an agent spend waiting for prompt reading (prefill) with this project, compared with Strata's
own cache alone and with no reuse at all? The hypothesis going in was 1.5–3× less.

## Setup

| | |
|---|---|
| GPU | AMD Instinct MI100 32 GB (gfx908), passed through to a VM; 50 GB RAM, models and snapshots on NVMe |
| Engine | Strata v0.2.16 (server 0.1.62), Qwen3.8-Flash-Next GSQ-RCO IQ2_XS, 131,072-token context, int8 KV |
| Gateway | LiteLLM v1.110.2 + hook, Context Manager, PostgreSQL 16 |
| Agent | Hermes Agent v0.23.3, reasoning effort *medium* (its default), 18 tools |
| GPU state | checked before the runs: 77 °C maximum under load, no clock throttling |

**Task.** Hermes reads 17 configuration files of the lab (34 KB, secrets removed) one by one and writes
`INFRASTRUCTURE.md`: overview with a diagram, one section per machine, runbook, known issues. Then Strata is
restarted (as after a GPU reset, or the next morning) and `hermes --resume` adds a Troubleshooting section.

**Setups.**

* **A, no reuse.** The recorded calls of run C-2 replayed straight to Strata, each with a random first line so nothing
  can be reused, generating 1 token.
* **B, Strata's cache only.** The Context Manager in pass-through mode.
* **C, with this project.** Snapshot mode with canonicalisation; tier-0 pin off from C-2 on.

**Runs.** `s-` = session from a fresh Strata process (snapshots deleted, one throwaway request so all setups start with
the same expert cache); `r-` = resume after a Strata restart; the number is the repetition.

**Measure.** Prefill wait = sum over all calls of Strata's prompt-read time (from its log line
`prompt N tokens = R reused + X read in T ms`), plus snapshot restore time.

## Results

| Run | Calls | Prompt tokens | Reused | Prefill wait | Restore | Whole task |
|---|---:|---:|---:|---:|---:|---:|
| s-B-1 session, Strata cache only | 21 | 433,404 | 91.6% | 69.0 s | — | 267 s |
| s-C-2 session, with this project | 21 | 429,243 | 91.6% | 67.7 s | — | 302 s |
| A, the 21 calls of s-C-2 with no reuse | 21 | 430,746 | 0% | 516.6 s | — | — |
| r-B-1 resume, Strata cache only | 7 | 304,200 | 83.5% | 66.0 s | — | 211 s |
| r-C-2 resume, with this project | 3 | 132,556 | 92.2% | 16.0 s | 1.4 s | 141 s |
| A, the 3 calls of r-C-2 with no reuse | 3 | 132,769 | 0% | 158.2 s | — | — |
| next day, Strata cache only | 1 | 10,536 | 0% | 12.5 s | — | — |
| next day, with this project | 1 | 10,536 | 97.9% | 0.9 s | — | — |

Session + resume: A 674.8 s, B 135.0 s, C 83.7 s.

### Per situation

| Situation | A | B | C | C vs B |
|---|---:|---:|---:|---:|
| Session without interruption | 516.6 s | 69.0 s | 67.7 s | 1.0× |
| First call after a restart (~38K tokens) | 47.7 s | 43.6 s | 2.2 s | 20× |
| Whole resume part | 158.2 s | 66.0 s | 16.0 s | 4.1× |
| First call of a new day | 12.5 s | 12.5 s | 0.9 s | 13.6× |
| Whole task with one restart | 674.8 s | 135.0 s | 83.7 s | 1.6× |

### Per call, same 21 calls (s-C-2 vs its no-reuse replay)

| Call | 1 | 2 | 5 | 10 | 15 | 20 | 21 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prompt tokens | 10,498 | 10,812 | 13,612 | 19,288 | 24,436 | 29,017 | 38,345 |
| No reuse | 13.0 s | 13.3 s | 16.0 s | 23.6 s | 29.5 s | 34.6 s | 45.0 s |
| With reuse | 13.2 s | 1.2 s | 3.1 s | 2.1 s | 1.7 s | 3.3 s | 12.6 s |

Call 1 reads the system prompt and tools (10.5K tokens) in both cases; call 21 adds 9.3K new tokens at once.

## Reading the numbers

* Inside one uninterrupted session this project adds nothing: Strata's own cache already reuses 92%.
* It pays off when Strata has lost the conversation (20× on the first call after a restart) and on a new day (13.6×,
  because Hermes' date line sits near the top of the system prompt and is moved by canonicalisation).
* Over this task with one restart: 1.6× less prefill than Strata alone, 8× less than no reuse. The hypothesis holds in
  the situations the project targets.
* Whole-task time is dominated by generation (about 70% at reasoning *medium*), so tasks are not 1.6× faster overall.

## Limits

* One run per setup. Hermes took different paths: the resume used 7 calls in B and 3 in C, so the 4.1× resume figure
  is less solid than the per-call ones.
* The B and C session numbers come from different repetitions (B-1, C-2); they agree within 2%.
* Run C-1 is excluded: the tier-0 pin was on and hit a Strata issue (a snapshot saved from a pinned request restores
  only the pinned prefix). Its restore gave back 10.3K of 38K tokens. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
* The no-reuse baseline is a replay generating 1 token, so it measures prefill only.
