# Strata watchdog

Keeps the Strata server (Qwen3.8-Flash-Next IQ2_XS, 128K context) running on port 8080 of the MI100 VM.

## Why it exists

The MI100 is passed through to this VM. Under load, the guest's amdgpu driver sometimes hits a page fault in the
GPU's DMA engine (`dmesg`: `[mmhub0] no-retry page fault ... Faulty UTCL2 client ID: SDMA1`, then `GPU reset`).
It happens with every engine on this VM (Strata, HotRod, llama.cpp), at random, roughly once per hour or two of
heavy use.

After such a reset, Strata's own server keeps trying to restart its engine, and every attempt crashes the GPU again
(`native head upload: unspecified launch failure`). The server then stays dead. What recovers it is a full stop,
dropping the page cache, and a fresh start. The watchdog does exactly that, automatically.

## What it does

Every 30 seconds it checks the server:

| Situation | Action |
|---|---|
| `GET /v1/models` lists a model as `"loaded"` | nothing (healthy) |
| the server process is gone | full restart at once |
| a new `GPU reset` in `dmesg` **and** the server log shows `the engine did not start` | full restart at once |
| no loaded model for 6 checks in a row (3 min) | full restart |
| within 5 min of a (re)start | no restart (the engine is still loading) |

A **full restart** is:

1. save the server log as `strata-server.out.crash-MMDD-HHMM` (the last 5 are kept),
2. stop the server and the `strata --serve` engine (kill -9 if needed after 60 s),
3. `sync` + drop the page cache,
4. start the server again with the 128K config.

A busy server is not touched: `/v1/models` answers instantly even during a long prompt read, so a 125K prompt
(about 3.5 min) does not count as a failure.

Expect about **1-2 minutes** of downtime per crash. The request that was running when the GPU crashed fails; the
client (Hermes, MSagent, ...) has to retry it. The prompt cache is lost, so the next request re-reads the whole
conversation (about 3.5 min at 125K tokens).

## Files

| Path | What |
|---|---|
| `/usr/local/bin/strata-watchdog` | the script |
| `engine/WATCHDOG.md in this repository` | this file |
| `/etc/systemd/system/strata-watchdog.service` | systemd unit (enabled: starts at boot) |
| `/etc/default/strata-watchdog` | optional settings overrides (not created by default) |
| `/var/log/strata-watchdog.log` | what the watchdog did, and why |
| `/var/log/strata-server.out` | the Strata server's own output |
| `/var/log/strata-server.out.crash-*` | server output saved at each restart |
| `$SERVE_DIR/$CONFIG (e.g. /opt/Strata/terminal/strata-128k.json)` | the Strata config it runs |

## Commands

```bash
sudo systemctl status strata-watchdog      # is it running
sudo tail -f /var/log/strata-watchdog.log  # its decisions
sudo tail -f /var/log/strata-server.out   # the server itself
sudo dmesg -T | grep "GPU reset("          # GPU crashes

sudo systemctl stop strata-watchdog        # pause the watchdog (the server keeps running)
sudo systemctl start strata-watchdog       # resume (adopts the running server, or starts one)
sudo systemctl disable --now strata-watchdog   # no watchdog, and nothing starts at boot
```

**Before running benchmarks or switching engines/configs, stop the watchdog first**, otherwise it will see the
server change or disappear and start its own config again after 30 s.

## Settings

Edit `/etc/default/strata-watchdog` (shell syntax), then `sudo systemctl restart strata-watchdog`:

```bash
CONFIG=strata-908.json      # e.g. IQ3_XXS instead (a file in SERVE_DIR)
SERVE_DIR=/opt/Strata/terminal
PORT=8080
INTERVAL=30                 # seconds between checks
FAIL_LIMIT=6                # failed checks before a restart
GRACE=300                   # seconds after a start with no restart
```

Changing `CONFIG` while a server with another config is running: stop that server first
(`sudo systemctl stop strata-watchdog`, kill the server, then start the watchdog), since the watchdog only
recognises servers started with its own `CONFIG`.

## Limits

- It recovers from the crash; it does not prevent it. The cause is the VM's GPU passthrough driver. Possible
  real fixes: a newer guest kernel / amdgpu driver, other IOMMU settings for the passthrough, or running Strata on
  the Proxmox host directly.
- If the MI100 itself gets stuck (it has no function-level reset, so this can happen after a VM shutdown), no
  restart helps: the **Proxmox host** has to be rebooted. Sign: restarts keep failing with resets in `dmesg`, and
  `/var/log/strata-watchdog.log` shows a `RESTART` every few minutes.
- Tested on 2026-11-20: killing the server -> back up in about 1 minute. A GPU crash could not be triggered on
  demand for a test; it takes the same full-restart path, at the latest after 3 minutes.
