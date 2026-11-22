# strata-context-manager

A persistent context cache for the [Strata](https://github.com/Niko1221/Strata) inference engine, plugged into the
[LiteLLM](https://github.com/BerriAI/litellm) proxy. It saves what the model has already read to disk and reloads it
when the engine has forgotten it, so AI agents wait far less for prompt reading (prefill).

Built and measured with [Hermes Agent](https://github.com/nousresearch/hermes-agent) and **Qwen3.8-Flash-Next**
(IQ2_XS) on one AMD MI100. Any OpenAI-compatible client that goes through LiteLLM benefits.

## In five sentences

An AI agent re-sends its whole conversation to the model at every step, so the model would normally re-read
everything from the start each time, which is slow on a home GPU. This system saves what the model has already read
to a fast disk, like a bookmark, so the next step only reads what is new. If the server restarts, crashes or a new day
starts, the bookmark is reloaded in about a second instead of re-reading the conversation for tens of seconds or
several minutes. For agents that work on the same project over long sessions or several days, that means far less
waiting: up to 20 times less on the first answer after a restart. It does not help when every task is new and
unrelated.

**And Strata's own cache?** Strata already remembers the last few conversations in GPU and RAM while it runs, so one
uninterrupted session is already fast. That memory is small and temporary: it is lost on a restart or crash, and older
conversations get pushed out. This project adds the long-term memory on disk, and smooths out details that would break
the bookmark, such as the date an agent writes at the top of every conversation.

## Measured (Hermes writing this lab's infrastructure docs, 21 Nov 2026)

| Prefill wait | No reuse | Strata cache only | Strata + this project |
|---|---:|---:|---:|
| Whole docs task with one Strata restart | 674.8 s | 135.0 s | **83.7 s** |
| One uninterrupted session (21 calls) | 516.6 s | 69.0 s | 67.7 s |
| First call after a restart / GPU reset (~38K tokens) | 47.7 s | 43.6 s | **2.2 s** |
| First call of a new day (10.5K-token system prompt) | 12.5 s | 12.5 s | **0.9 s** |

One run per setup; method, raw numbers and limits in [docs/BENCHMARK.md](docs/BENCHMARK.md).

## How it fits together

```mermaid
flowchart LR
  A[Agent<br/>Hermes, OpenCode, ...] -->|OpenAI API| L[LiteLLM<br/>+ strata_context_hook]
  L -->|observe / result| C[Context Manager]
  C --> P[(PostgreSQL)]
  L -->|chat request| S[Strata server]
  C -->|save / restore slot| S
  S --- D[(NVMe<br/>snapshot files)]
  J[janitor, cron] -->|eviction order| C
  J --> D
```

* **LiteLLM hook**: before each request, tells the Context Manager what is about to be sent; after it, reports tokens
  and timings. It never fails a request.
* **Context Manager** (FastAPI + Postgres): cuts each request into content-hashed blocks, recognises conversations it
  has seen, restores the deepest matching snapshot when Strata has lost the conversation, and saves new snapshots of
  long conversations.
* **Strata**: does the work. Snapshots use Strata's slot save/restore (`--slot-save-path`); no Strata patch is needed.
* **Engine-side tools** (optional): a watchdog that restarts Strata cleanly after a GPU reset, and a janitor that keeps
  the snapshot folder under a size budget.

Details: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Requirements

* **Strata** with `POST /slots/0?action=save|restore` and `GET /metrics` (tested: Strata v0.2.16, server build
  0.1.62), started with `--slot-save-path DIR` on a fast disk.
* **LiteLLM** proxy, tested with v1.110.2 (the image built here pins it).
* Docker with Compose for the gateway side; PostgreSQL 16 comes with it.
* Disk for snapshots: ~350 MB per 10K-token conversation, ~2 GB at 120K tokens (Qwen3.8-Flash-Next, int8 KV).

Why this model and this engine: [docs/WHY-QWEN3.8-FLASH-NEXT.md](docs/WHY-QWEN3.8-FLASH-NEXT.md).

## Quick start

On the Strata machine, add `--slot-save-path /nvme/strata-slots` to the server command
([engine/README.md](engine/README.md)). Then on the gateway machine:

```bash
git clone https://github.com/themimi974/strata-context-manager
cd strata-context-manager/deploy
cp .env.example .env        # set STRATA_URL, STRATA_MODEL and the secrets
docker compose up -d
```

Point your agent at `http://<gateway>:4000/v1`, model `qwen3.8-flash-next`, with a LiteLLM key, and tell it the
context size (131072). Hermes example and the other clients: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). All settings:
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## When it helps, and when it does not

* Helps: resuming a session after a restart, GPU reset or eviction; the first call of each day; several agents taking
  turns on one engine; long multi-day projects.
* Does not help: one uninterrupted session (Strata already reuses ~92% of each prompt); many short unrelated tasks;
  the same document read again inside a *new* conversation (needs composition of cached pieces, not built yet).
* After a context compaction (`/compact`), everything after the system prompt is new text and is read once.

Downsides and known issues: [docs/LIMITS.md](docs/LIMITS.md) · [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)

File state cache (research, off by default): [docs/FILE-CACHE.md](docs/FILE-CACHE.md).

## Repository

| Path | What |
|---|---|
| `strata_context_manager/` | the Context Manager service |
| `hook/` | the LiteLLM hook |
| `migrations/` | database schema (Alembic) |
| `deploy/` | compose file, LiteLLM config, `.env.example` |
| `docker/litellm/` | LiteLLM image with the hook built in |
| `engine/` | Strata-side watchdog, snapshot and file-cache janitors, restart helper |
| `bench/` | the benchmark scripts used for the numbers above |
| `docs/` | documentation |

Images: `dockerfan4life/strata-context-manager` (Context Manager) and `dockerfan4life/strata-context-manager-litellm`
(LiteLLM + hook).

## License

Apache-2.0. Strata, LiteLLM, Hermes Agent and the model are separate projects under their own licenses; nothing from
them is included here except the LiteLLM base image of the `-litellm` image.
