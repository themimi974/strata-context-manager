# Deployment

Two machines in the reference setup (they can be one):

* **Strata machine**: the GPU, Strata, the snapshot folder on a fast disk, optionally the watchdog and the janitor.
* **Gateway**: Docker with LiteLLM (+ hook), the Context Manager and PostgreSQL.

## 1. Strata machine

1. Install Strata and run its server with your model, for example Qwen3.8-Flash-Next IQ2_XS at 128K context. Note
   the server's `model_name` (used as `STRATA_MODEL=openai/<model_name>` below).
2. Create the snapshot folder on the fastest disk you have and add it to the server command:

   ```bash
   mkdir -p /nvme/strata-slots
   # server.py ... --slot-save-path /nvme/strata-slots
   ```

3. Optional but recommended: install the watchdog (restarts Strata cleanly after a GPU reset) and the janitor (keeps
   the snapshot folder under a budget). See [engine/README.md](../engine/README.md).

The gateway must reach Strata's port (default 8080), and the janitor must reach the Context Manager's port (8090).

## 2. Gateway

```bash
git clone https://github.com/themimi974/strata-context-manager
cd strata-context-manager/deploy
cp .env.example .env
```

Edit `.env`: `STRATA_URL`, `STRATA_MODEL`, and the secrets (`openssl rand -hex 24` for each; the LiteLLM master key
must start with `sk-`). Fill in the validity tuple if you can ([CONFIGURATION.md](CONFIGURATION.md)). Then:

```bash
docker compose up -d          # pulls the published images from Docker Hub
# or: docker compose up -d --build   to build them from this checkout
docker compose ps             # three services, healthy after ~1 min
curl -s localhost:8090/health
```

If your model name differs from `qwen3.8-flash-next`, change `model_name` in `deploy/litellm-config.yaml` and
`MODEL_NAME` in `.env` to the same value.

### Create a key for each client

From the `deploy/` folder (the master key is read from `.env`):

```bash
M=$(grep -oP '^LITELLM_MASTER_KEY=\K\S+' .env)
curl -s http://localhost:4000/key/generate -H "Authorization: Bearer $M" \
  -H "Content-Type: application/json" -d '{"key_alias":"hermes-agent","models":["qwen3.8-flash-next"]}'
```

The answer contains `"key": "sk-..."`. LiteLLM stores keys hashed: copy it now, it cannot be shown again.

You can also create keys in LiteLLM's web UI at `http://<gateway>:4000/ui` → Virtual Keys.

### Already running LiteLLM?

Use only the parts you need: run the Context Manager container (it needs a PostgreSQL database of its own), copy
`hook/strata_context_hook.py` next to your LiteLLM config (or use the `-litellm` image), add
`callbacks: strata_context_hook.handler` under `litellm_settings`, and set `CONTEXT_MANAGER_URL` in LiteLLM's
environment.

## 3. Clients

Any OpenAI-compatible client: base URL `http://<gateway>:4000/v1`, model `qwen3.8-flash-next`, a LiteLLM key, and the
context size (LiteLLM cannot tell a custom client what Strata allows).

### Hermes Agent

`~/.hermes/config.yaml`:

```yaml
model:
  default: "qwen3.8-flash-next"
  provider: "custom"
  base_url: "http://<gateway>:4000/v1"
  api_key: "sk-..."               # the LiteLLM key
  context_length: 131072          # = Strata --max-context
```

The easiest way is `hermes model` (choose *custom*), which writes these values for you. If you edit the file by
hand, note that Hermes may keep the active `api_key:` line far below the rest of the `model:` section, after
comment blocks: search for the line that is not commented out. Keep context compression at a high threshold (Hermes' `threshold: 0.85`):
each compaction makes everything after the system prompt new text.

### Other agents

Add the lines your agent changes between sessions near the top of its system prompt (dates, session ids) to
`CTX_VOLATILE_PATTERNS`; otherwise each new session re-reads the system prompt. To see what an agent sends, set
`CTX_RECORD=/bench/requests.jsonl` for a few calls and mount a folder at `/bench`.

## 4. Check it works

```bash
curl -s http://<gateway>:8090/v1/stats
```

After a few calls you should see `engine_hit` close to 0.9 and, once a conversation passes 8K tokens, `saves` > 0 and
files in the snapshot folder. Restart Strata and continue the conversation: `restores` goes up and the first answer
comes back in seconds.
