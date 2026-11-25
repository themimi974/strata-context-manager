"""The hook turns Strata's GPU-fault errors into 503 + Retry-After, and leaves other errors alone."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def hook(monkeypatch):
    # the hook imports LiteLLM's CustomLogger; a stub is enough here
    if "litellm" not in sys.modules:
        cl = types.ModuleType("litellm.integrations.custom_logger")
        cl.CustomLogger = type("CustomLogger", (), {"__init__": lambda self, *a, **k: None})
        monkeypatch.setitem(sys.modules, "litellm", types.ModuleType("litellm"))
        monkeypatch.setitem(sys.modules, "litellm.integrations", types.ModuleType("litellm.integrations"))
        monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", cl)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent / "hook"))
    sys.modules.pop("strata_context_hook", None)
    return importlib.import_module("strata_context_hook")


def run(hook, exc):
    return asyncio.run(hook.handler.async_post_call_failure_hook({}, exc, None))


def test_checkpoint_failure_becomes_503(hook):
    exc = Exception("litellm.BadRequestError: OpenAIException - saving a conversation checkpoint failed")
    r = run(hook, exc)
    assert r.status_code == 503 and r.headers["Retry-After"] == "60"
    assert "saving a conversation checkpoint failed" in r.detail


def test_launch_failure_becomes_503(hook):
    assert run(hook, Exception("prefill mmq: iota: unspecified launch failure")).status_code == 503


def test_other_errors_unchanged(hook):
    assert run(hook, Exception("context length exceeded: 140000 > 131072")) is None
