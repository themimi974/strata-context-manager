from strata_context_manager.canon import move_volatile_lines

SYS = "You are Hermes.\nRules...\nConversation started: Saturday, November 21, 2026 (CET, UTC+01:00)\nMore rules."


def conv(sys_text):
    return [{"role": "system", "content": sys_text}, {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}, {"role": "user", "content": "next"}]


def test_line_moves_to_first_user_message():
    out, moved = move_volatile_lines(conv(SYS))
    assert moved == ["Conversation started: Saturday, November 21, 2026 (CET, UTC+01:00)"]
    assert "Conversation started" not in out[0]["content"]
    assert out[0]["content"] == "You are Hermes.\nRules...\nMore rules."
    assert out[1]["content"].startswith("Conversation started: Saturday") and out[1]["content"].endswith("hello")
    assert out[3]["content"] == "next"


def test_system_prefix_identical_across_days():
    a, _ = move_volatile_lines(conv(SYS))
    b, _ = move_volatile_lines(conv(SYS.replace("Saturday, November 21", "Sunday, November 22")))
    assert a[0] == b[0]


def test_deterministic_every_turn():
    a, _ = move_volatile_lines(conv(SYS))
    b, _ = move_volatile_lines(conv(SYS) + [{"role": "assistant", "content": "x"}])
    assert a[:4] == b[:4]


def test_input_not_modified_and_no_match_passthrough():
    msgs = conv("plain system")
    out, moved = move_volatile_lines(msgs)
    assert out is msgs and moved == []
    original = conv(SYS)
    move_volatile_lines(original)
    assert "Conversation started" in original[0]["content"]


def test_list_content():
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    out, _ = move_volatile_lines(msgs)
    assert out[1]["content"][0]["text"].startswith("Conversation started") and out[1]["content"][1]["text"] == "hello"
