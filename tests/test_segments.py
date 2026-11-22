from strata_context_manager.segments import engine_spec, file_segments, tools_from_env

BIG = '{"content": "1|' + "x = 1\\n2|" * 600 + '"}'


def conv(content=BIG, name="read_file"):
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                           "function": {"name": name, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": content},
    ]


def test_finds_a_file_read():
    segs = file_segments(conv(), "root")
    assert len(segs) == 1 and segs[0]["message"] == 3 and segs[0]["start"] == 0 and segs[0]["end"] == len(BIG)


def test_id_follows_content_and_validity():
    a = file_segments(conv(), "root")[0]["id"]
    assert file_segments(conv(), "root")[0]["id"] == a
    assert file_segments(conv(BIG + " "), "root")[0]["id"] != a          # changed file: new id
    assert file_segments(conv(), "other-model")[0]["id"] != a             # other validity tuple: new id


def test_skips_other_tools_and_small_results():
    assert file_segments(conv(name="terminal"), "root") == []
    assert file_segments(conv(content='{"content": "1|x"}'), "root") == []


def test_engine_spec_and_tools_env():
    spec = engine_spec(file_segments(conv(), "root"))
    assert set(spec["files"][0]) == {"message", "start", "end", "id"}
    assert tools_from_env("read_file,view") == ("read_file", "view")
    assert tools_from_env('["read_file"]') == ("read_file",)
    assert tools_from_env(None) == ("read_file",)
