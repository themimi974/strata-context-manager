# Changelog

## 0.1.1 (2026-11-21)

Fixes found by deploying 0.1.0 from GitHub and Docker Hub on the reference lab.

* Janitor: delete orphaned snapshot files (no record points to them, e.g. after a database reset or a model
  change); before, they were never deleted while the Context Manager was reachable. `ORPHAN_MIN_AGE_MIN` (60).
* `GET /v1/snapshots/eviction-order` also returns `keep` (pinned files), so the janitor can tell known files apart.
* Save and restore thresholds are settings: `CTX_SAVE_MIN_TOKENS`, `CTX_SAVE_STEP_TOKENS`, `CTX_RESTORE_MIN_GAIN`.
* Docs: key creation reads the master key from `.env` and says the key is shown once; Hermes `api_key` placement.

## 0.1.0 (2026-11-21)

First public version.

* Context Manager: content-defined blocks, chain hashes under a validity tuple, request log (PostgreSQL, Alembic).
* Snapshot mode: saves long conversations with Strata's slot API and restores them when Strata lost them.
* Canonicalisation: volatile system-prompt lines (default: Hermes' date line) moved to the first user message.
* Value-based eviction with an engine-side janitor; watchdog and restart helper for Strata.
* LiteLLM hook and a LiteLLM image with the hook built in; compose deployment.
* Tier-0 pin available but off by default (Strata restores only the pinned prefix of a pinned request's snapshot).
* Benchmark: Hermes documenting a lab; 1.6x less prefill than Strata alone with one restart, 20x on the first call
  after a restart, 13.6x on the first call of a new day.
