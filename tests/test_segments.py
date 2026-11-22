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


# ---- reads made with a shell tool (opt-in): cat and slices -------------------------------------------------------------

import json as _json

from strata_context_manager.segments import terminal_modes_from_env, terminal_read_kind

OUT = "line of source code\n" * 400            # 8000 chars of output


def shell(command, output=OUT, exit_code=0, name="terminal"):
    res = _json.dumps({"output": output, "exit_code": exit_code, "error": None, "cwd": "/w"})
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function",
                                                           "function": {"name": name, "arguments": _json.dumps({"command": command})}}]},
        {"role": "tool", "tool_call_id": "t1", "content": res},
    ]


def test_command_kinds():
    assert terminal_read_kind("cat crates/a/src/lib.rs") == "cat"
    assert terminal_read_kind("cd /home/h/repo && cat crates/a/src/lib.rs") == "cat"
    assert terminal_read_kind("sed -n '100,160p' src/model.rs") == "slice"
    assert terminal_read_kind("sed -n 100,160p src/model.rs") == "slice"
    assert terminal_read_kind("head -n 50 src/model.rs") == "slice"
    assert terminal_read_kind("head -50 src/model.rs") == "slice"
    assert terminal_read_kind("tail -n 30 src/model.rs") == "slice"
    assert terminal_read_kind("nl -ba src/model.rs | sed -n '10,40p'") == "slice"
    assert terminal_read_kind("cat src/model.rs | head -80") == "slice"
    assert terminal_read_kind("cd r && sed -n '1,30p' a.rs && echo --- && sed -n '1,30p' b.rs") == "slice"
    assert terminal_read_kind("cat a.rs && echo === && cat b.rs") == "cat"


def test_anything_else_is_not_a_read():
    for cmd in ("cat a.rs > b.rs", "cat a.rs >> log.txt", "cat <<EOF > a.rs", "cat a.rs | grep foo", "cat a.rs | sh",
                "cargo test -p x", "git diff", "grep -n foo a.rs", "sed -i 's/a/b/' a.rs", "sed -n 1,5p a.rs; rm -rf x",
                "cat a.rs && rm b.rs", "cat $(echo a.rs)", "cat `echo a.rs`", "cd x && cargo build", "ls", "cat", "",
                "cat a.rs | tee b.rs", "head -n 5 a.rs && curl http://x", "echo $(whoami)"):
        assert terminal_read_kind(cmd) is None, cmd


def test_terminal_reads_are_off_by_default_and_on_request():
    m = shell("cat src/lib.rs")
    assert file_segments(m, "root") == []                                         # default: only read_file
    s = file_segments(m, "root", terminal_modes=("cat",))
    assert len(s) == 1 and s[0]["tool"] == "terminal:cat" and s[0]["message"] == 3 and s[0]["end"] == len(m[3]["content"])
    assert file_segments(m, "root", terminal_modes=("slice",)) == []              # a cat is not a slice
    sl = shell("sed -n '10,300p' src/lib.rs")
    assert file_segments(sl, "root", terminal_modes=("cat",)) == []
    assert file_segments(sl, "root", terminal_modes=("slice",))[0]["tool"] == "terminal:slice"


def test_terminal_read_must_succeed_and_be_long_enough():
    assert file_segments(shell("cat a.rs", exit_code=1), "root", terminal_modes=("cat",)) == []
    assert file_segments(shell("cat a.rs", output="short\n" * 10), "root", terminal_modes=("cat",)) == []
    mid = "x" * 3500                                                                # slices from 3000 chars, files from 4000
    assert len(file_segments(shell("sed -n 1,90p a.rs", output=mid), "root", terminal_modes=("slice",))) == 1
    assert file_segments(shell("cat a.rs", output=mid), "root", terminal_modes=("cat",)) == []
    assert file_segments(shell("sed -n 1,90p a.rs", output=mid), "root", terminal_modes=("slice",), slice_min_chars=5000) == []
    assert file_segments(shell("cat a.rs", name="bash"), "root", terminal_modes=("cat",)) == []                  # other tool name
    assert len(file_segments(shell("cat a.rs", name="bash"), "root", terminal_modes=("cat",), terminal_tools=("bash",))) == 1


def test_same_text_twice_in_a_request_is_named_once():
    m = shell("cat a.rs")
    m2 = m + [{"role": "assistant", "content": "", "tool_calls": [{"id": "t2", "type": "function",
                "function": {"name": "terminal", "arguments": _json.dumps({"command": "cat a.rs"})}}]},
              {"role": "tool", "tool_call_id": "t2", "content": m[3]["content"]}]
    assert len(file_segments(m2, "root", terminal_modes=("cat",))) == 1


def test_modes_from_env():
    assert terminal_modes_from_env("cat, slice") == ("cat", "slice")
    assert terminal_modes_from_env("slice") == ("slice",)
    assert terminal_modes_from_env("") == () and terminal_modes_from_env(None) == () and terminal_modes_from_env("rm,cat") == ("cat",)
