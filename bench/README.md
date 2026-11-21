# Benchmark scripts

The scripts behind [docs/BENCHMARK.md](../docs/BENCHMARK.md), as run in the reference lab. They are lab tooling, not a
packaged tool: they assume a Proxmox host where `pct exec 104` reaches the Hermes container and `pct exec 105` the
gateway container, and `ssh` reaches the Strata machine. Adapt those lines to your setup.

| Script | What |
|---|---|
| `bench.sh session ARM REP` / `bench.sh resume ARM REP` | one Hermes run: ARM `B` (pass-through) or `C` (snapshot mode); saves engine log slices and Hermes output in `runs/` |
| `replay.py nonce REC OUT` | setup A: replays recorded requests straight to Strata with nothing reusable, measures prefill per call |
| `replay.py litellm REC OUT [DATE]` | replays through LiteLLM; `DATE` replaces Hermes' date line (the next-day case) |
| `analyze.py RUN ...` | sums prefill, reuse, restores and TTFT for runs or replay files |
| `pin_restore_test.py 0\|1` | reproduces the pin + restore issue (KNOWN-ISSUES.md) |

Environment: `STRATA_SSH` (default `ssh root@strata-host`), `STRATA_URL`, `LITELLM_URL`, `LITELLM_KEY`,
`STRATA_ENGINE_LOG` (the `log` file of your Strata config: the scripts read Strata's
`prompt N tokens = R reused + X read in T ms` lines from it).

Requests are recorded by the Context Manager with `CTX_RECORD=/bench/<name>.jsonl`.

The workload was 17 configuration files of the lab (passwords and keys removed) read by Hermes; the files and the raw
run logs are not published because they describe a private network.
