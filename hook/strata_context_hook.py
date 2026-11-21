"""LiteLLM proxy hook for strata-context-manager.

Enable it in the LiteLLM config:

    litellm_settings:
      callbacks: strata_context_hook.handler

and make this file importable by the proxy (the `litellm` image of this project already contains it at
/app/strata_context_hook.py; otherwise mount it there).

Before each chat request the hook sends the messages and tools to the Context Manager (`POST /v1/observe`). The
Context Manager may restore a saved conversation into Strata first, and may return rewritten messages (volatile
system-prompt lines moved, see CTX_REORDER). After a successful request the hook reports token counts and timings
(`POST /v1/result`), which lets the Context Manager decide whether to save a snapshot.

The hook never fails a request: if the Context Manager is down or slow, the request goes to Strata unchanged.

Environment:
  CONTEXT_MANAGER_URL            default http://context-manager:8090
  CONTEXT_MANAGER_TIMEOUT_S      default 2      (result reports)
  CONTEXT_MANAGER_OBSERVE_TIMEOUT_S default 60  (the pre-call may restore a snapshot first: ~3 s for 120K tokens)
"""
import os
import uuid

import httpx
from litellm.integrations.custom_logger import CustomLogger

CM_URL = os.environ.get("CONTEXT_MANAGER_URL", "http://context-manager:8090").rstrip("/")
TIMEOUT = float(os.environ.get("CONTEXT_MANAGER_TIMEOUT_S", "2"))
OBSERVE_TIMEOUT = float(os.environ.get("CONTEXT_MANAGER_OBSERVE_TIMEOUT_S", "60"))
ID_KEY = "strata_context_id"


def _ms(a, b):
    try:
        return (b - a).total_seconds() * 1000.0
    except Exception:
        return None


class StrataContextHook(CustomLogger):
    def __init__(self):
        super().__init__()
        self.client = httpx.AsyncClient(timeout=TIMEOUT)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        try:
            if "messages" not in data:
                return data
            cid = str(uuid.uuid4())
            data.setdefault("metadata", {})[ID_KEY] = cid
            r = await self.client.post(f"{CM_URL}/v1/observe", json={
                "call_id": cid, "model": data.get("model", ""), "messages": data["messages"],
                "tools": data.get("tools")}, timeout=OBSERVE_TIMEOUT)
            body = r.json() if r.status_code == 200 else {}
            if isinstance(body.get("messages"), list) and body["messages"]:   # volatile lines moved (CTX_REORDER=1)
                data["messages"] = body["messages"]
            if isinstance(body.get("pin"), dict):                               # tier-0 pin (CTX_PIN_SYSTEM=1)
                # top level, not extra_body: LiteLLM's proxy drops an extra_body set in a pre-call hook, while it
                # forwards unknown top-level keys to an openai/ backend
                data["strata_prefix"] = body["pin"]
        except Exception:
            pass
        return data

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            md = (kwargs.get("litellm_params") or {}).get("metadata") or {}
            cid = md.get(ID_KEY)
            if not cid:
                return
            usage = getattr(response_obj, "usage", None)
            details = getattr(usage, "prompt_tokens_details", None) if usage else None
            await self.client.post(f"{CM_URL}/v1/result", json={
                "call_id": cid,
                "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
                "cached_tokens": getattr(details, "cached_tokens", None) if details else None,
                "latency_ms": _ms(start_time, end_time),
                "ttft_ms": _ms(start_time, kwargs.get("completion_start_time")),
            })
        except Exception:
            pass


handler = StrataContextHook()
