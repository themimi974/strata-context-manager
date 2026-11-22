# Benchmarking a Hermes task on the local stack

Measures one long Hermes task (a real piece of work on a real codebase) from four sides, so that speed, cache use,
loops and quality can be judged from data instead of impressions.

| Source | What it gives |
|---|---|
| Hermes `state.db` (`hermes_side.py`, runs in the Hermes container) | API calls, tokens, tool calls by tool, failed calls, the same call repeated, files read again and again, long gaps, compactions |
| Gateway `request_log` | per request: prompt tokens, cached tokens, latency, time to first token, snapshot restores and saves |
| Strata log | prompt-reading and generation speed, file-cache captures / loads / fallbacks, GPU resets |
| git (in the Hermes container) | commits, changed files, uncommitted work |

Hermes' own input-token count and Strata's tokens read agree exactly, which is a good consistency check.

## Use

```sh
export STRATA_VM_SSH="ssh user@strata-vm"            # how to run a command on the Strata VM
# once: copy hermes_side.py to the Hermes container (default CT 104) as /home/hermes/hermes_side.py
echo <line of the Strata log where the task started> > strata_log_start
./sampler.sh &                                       # one JSON line every 5 min in gltf-timeline.jsonl, alerts in alerts.log
python3 collect.py "first words of the task prompt" <strata log line>    # one sample, on demand
```

The task's session is found by the first words of its prompt; sessions created by a compaction are followed.
`CT_HERMES`, `CT_GATEWAY` and `STRATA_LOG` override the defaults.

Alerts: a call repeated 8 times in a row, a failing call repeated 4 times, changes in paths the task must not touch
(edit the list in `sampler.sh`), no change at all after an hour.
