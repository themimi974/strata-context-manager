# Changelog

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
