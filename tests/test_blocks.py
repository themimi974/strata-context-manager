from strata_context_manager.blocks import (CDC_MAX_CHARS, TIER_FILES, TIER_SYSTEM, TIER_TOOLS, Validity, cdc_split, chain,
                             request_blocks)

V = Validity("qwen3.8-flash-next", "w" * 64, "IQ2_XS", "strata-v0.2.16", "t" * 64)


def test_cdc_roundtrip_and_stability():
    paras = [f"Paragraph {i} " + ("word " * (40 + i % 7)) + "\n\n" for i in range(300)]
    text = "".join(paras)
    chunks = cdc_split(text)
    assert "".join(chunks) == text
    assert len(chunks) > 3
    # an edit near the end leaves the early chunks unchanged
    edited = "".join(paras[:-5]) + "CHANGED\n\n" + "".join(paras[-4:])
    chunks2 = cdc_split(edited)
    same = sum(1 for a, b in zip(chunks, chunks2) if a == b)
    assert same >= len(chunks) - 3


def test_cdc_hard_cut():
    text = "x" * (CDC_MAX_CHARS * 2 + 10)
    chunks = cdc_split(text)
    assert "".join(chunks) == text and max(map(len, chunks)) <= CDC_MAX_CHARS


def test_block_order_and_tiers():
    msgs = [{"role": "system", "content": "You are Hermes. Today is 2026-11-21."},
            {"role": "user", "content": "read the file"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                  "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "/root/app/main.py\nimport os\n"}]
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    b = request_blocks(msgs, tools)
    assert [x.kind for x in b] == ["system", "tools", "user", "assistant", "tool"]
    assert b[0].tier == TIER_SYSTEM and b[1].tier == TIER_TOOLS and b[-1].tier == TIER_FILES
    assert b[0].volatile == ["2026-11-21"]
    assert b[-1].source_path == "/root/app/main.py"


def test_chain_prefix_property():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "a"}]
    c1 = chain(V, request_blocks(msgs))
    c2 = chain(V, request_blocks(msgs + [{"role": "assistant", "content": "b"}]))
    assert c2[:2] == c1 and len(c2) == 3


def test_validity_changes_every_hash():
    msgs = [{"role": "user", "content": "a"}]
    v2 = Validity(V.model_id, V.weights_hash, "IQ3_XXS", V.engine_version, V.template_version)
    assert chain(V, request_blocks(msgs)) != chain(v2, request_blocks(msgs))


def test_tool_call_metadata_is_part_of_identity():
    a = request_blocks([{"role": "tool", "tool_call_id": "x1", "content": "ok"}])
    b = request_blocks([{"role": "tool", "tool_call_id": "x2", "content": "ok"}])
    assert a[0].hash != b[0].hash


def test_no_nul_in_stored_content():
    msgs = [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "terminal", "arguments": "{\"cmd\": \"ls\"}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "a\nb"}]
    assert all("\x00" not in b.content for b in request_blocks(msgs))
