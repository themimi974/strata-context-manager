"""Engine adapter for Strata (Path B): snapshots through llama.cpp-style /slots save|restore, restarts via /metrics."""
from __future__ import annotations

import time

import httpx


class StrataAdapter:
    def __init__(self, base_url: str, timeout_s: float = 120.0):
        self.base = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_s)
        self._epoch: tuple | None = None
        self._epoch_at = 0.0

    async def epoch(self, max_age_s: float = 5.0) -> tuple | None:
        """(server start time, engine generation): changes whenever the engine lost its state."""
        if self._epoch is not None and time.monotonic() - self._epoch_at < max_age_s:
            return self._epoch
        try:
            m = (await self.client.get(f"{self.base}/metrics", timeout=5)).json()
            self._epoch = ((m.get("totals") or {}).get("since"), (m.get("engine") or {}).get("gen"))
            self._epoch_at = time.monotonic()
        except Exception:
            self._epoch = None
        return self._epoch

    async def save(self, filename: str) -> dict:
        r = await self.client.post(f"{self.base}/slots/0?action=save", json={"filename": filename})
        r.raise_for_status()
        return r.json()        # {"n_saved", "n_written", "timings": {"save_ms"}}

    async def restore(self, filename: str) -> dict:
        r = await self.client.post(f"{self.base}/slots/0?action=restore", json={"filename": filename})
        r.raise_for_status()
        return r.json()        # {"n_restored", "n_read", "timings": {"restore_ms"}}
