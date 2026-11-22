# Configuration

All settings are environment variables. With the compose deployment they come from `deploy/.env`
(see `deploy/.env.example`).

## Context Manager

| Variable | Default | Meaning |
|---|---|---|
| `CTX_DATABASE_URL` | required | PostgreSQL URL of the Context Manager's own database |
| `CTX_MODE` | `passthrough` (compose: `snapshot`) | `passthrough` records requests only; `snapshot` also saves and restores Strata state |
| `CTX_ENGINE_URL` | required in snapshot mode | Strata server, e.g. `http://strata-host:8080` (no `/v1`) |
| `CTX_REORDER` | `0` (compose: `1`) | move volatile system-prompt lines to the first user message |
| `CTX_VOLATILE_PATTERNS` | Hermes' `^Conversation started: .*$` | regexes (JSON list, or one per line), matched per line in the system prompt |
| `CTX_PIN_SYSTEM` | `0` | ask Strata to pin the system + tools prefix; keep off, see KNOWN-ISSUES.md |
| `CTX_PIN_MIN_TOKENS` | `2048` | minimum head size for a pin |
| `CTX_PIN_WINDOW_H` | `6` | only the most requested head over this window may ask for the pin |
| `CTX_ADMIN_TOKEN` | empty | token for the eviction endpoints used by the janitor (`X-Ctx-Token` header) |
| `CTX_PREFILL_TOK_PER_S` | `500` | your engine's cold prompt speed; used to value snapshots (recompute cost) |
| `CTX_RECORD` | empty | benchmark only: append every observed request to this JSONL file |

Snapshot thresholds:

| Variable | Default | Meaning |
|---|---|---|
| `CTX_SAVE_MIN_TOKENS` | `8192` | save a conversation only from this prompt size |
| `CTX_SAVE_STEP_TOKENS` | `8192` | save again once it grew this much since its last snapshot |
| `CTX_RESTORE_MIN_GAIN` | `2048` | restore only when it saves at least this many tokens of reading |

Smaller steps mean fresher snapshots after a restart (less to re-read) at the cost of more disk writes: a session that
ends between two steps is restored from its previous snapshot and the rest is read again.

### Validity tuple

A snapshot is reused only when all five values are identical to when it was saved. Set them once; change them whenever
the corresponding thing changes, and every old snapshot becomes unreachable (the janitor then deletes the files).

| Variable | What to put |
|---|---|
| `CTX_MODEL_ID` | the model name (compose: `MODEL_NAME`) |
| `CTX_QUANT` | the quantisation, e.g. `IQ2_XS` |
| `CTX_WEIGHTS_HASH` | a hash of the model files, e.g. `printf '%s|%s' $(sha256sum part1.gguf \| cut -d' ' -f1) $(sha256sum part2.gguf \| cut -d' ' -f1) \| sha256sum` |
| `CTX_ENGINE_VERSION` | your Strata version, e.g. `git -C /opt/Strata describe --always --dirty` |
| `CTX_TEMPLATE_VERSION` | a hash of the chat template the server uses, e.g. `sha256sum` of the template file |

Any stable identifier works; it only has to change when the thing changes. Leaving them at `unknown` works but then a
model or engine change is not detected (Strata still re-checks tokens on restore, so the cost is wasted restores).

## LiteLLM hook

| Variable | Default | Meaning |
|---|---|---|
| `CONTEXT_MANAGER_URL` | `http://context-manager:8090` | where the Context Manager listens |
| `CONTEXT_MANAGER_TIMEOUT_S` | `2` | timeout of the result report |
| `CONTEXT_MANAGER_OBSERVE_TIMEOUT_S` | `60` | timeout of the pre-call (it may restore a snapshot first) |

Enable it in the LiteLLM config with `litellm_settings: callbacks: strata_context_hook.handler`.

File state cache (research, needs the Strata build with `strata_segx`; see [FILE-CACHE.md](FILE-CACHE.md)):

| Variable | Default | Meaning |
|---|---|---|
| `CTX_SEG` | `0` | mark file reads in the conversation so Strata stores and reloads their state |
| `CTX_SEG_TOOLS` | `read_file` | tool names whose results are files (comma list or JSON list) |
| `CTX_SEG_MIN_CHARS` | `4000` | smaller tool results are just read |

## Compose (`deploy/.env`)

| Variable | Meaning |
|---|---|
| `STRATA_URL` | Strata server, e.g. `http://strata-host:8080`; LiteLLM gets `STRATA_URL/v1` |
| `STRATA_MODEL` | `openai/` + the `model_name` of your Strata config |
| `MODEL_NAME` | the name clients ask for; keep it equal to `model_name` in `deploy/litellm-config.yaml` |
| `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` | LiteLLM secrets (the master key must start with `sk-`; never change the salt key after first start) |
| `POSTGRES_PASSWORD`, `CTX_DB_PASSWORD` | database passwords (LiteLLM's and the Context Manager's) |
| `LITELLM_IMAGE`, `CONTEXT_MANAGER_IMAGE` | override the images (defaults: the published `0.1.1` tags; build from this repo for the file cache) |
| `LITELLM_BIND`, `LITELLM_PORT`, `CONTEXT_MANAGER_BIND`, `CONTEXT_MANAGER_PORT` | published ports; e.g. `LITELLM_BIND=127.0.0.1:` |

## Engine side (`/etc/default/strata-watchdog`, `/etc/default/strata-context`)

See [engine/README.md](../engine/README.md).

## API

| Endpoint | Used by |
|---|---|
| `POST /v1/observe` | the hook, before a request: `{call_id, model, messages, tools}` → `{messages?, restored?, pin?, ...}` |
| `POST /v1/result` | the hook, after a request: `{call_id, prompt_tokens, completion_tokens, cached_tokens, latency_ms, ttft_ms}` |
| `GET /v1/stats` | you: requests, hit rates, TTFT p50/p95, restores, saves, snapshot bytes |
| `GET /v1/snapshots/eviction-order` | the janitor (token) |
| `POST /v1/snapshots/evicted` | the janitor (token) |
| `GET /health` | Docker health check |
