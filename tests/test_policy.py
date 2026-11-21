from strata_context_manager.policy import choose_restore, engine_depth, save_matches, should_save, snapshot_value

CH = [f"h{i}" for i in range(6)]
OFF = [0, 4000, 10000, 20000, 30000, 40000, 41000]   # tokens before each block, last = total


def test_no_snapshot_no_restore():
    assert choose_restore(CH, {}, [], OFF) is None


def test_restore_after_restart():
    # snapshot at block 4 (30K..40K tokens), engine restarted: nothing served since
    assert choose_restore(CH, {"h4": 40000}, [], OFF) == "h4"


def test_engine_still_holds_it():
    assert choose_restore(CH, {"h4": 40000}, [CH[:5]], OFF) is None


def test_engine_holds_only_shared_prefix():
    # engine served another conversation sharing the system prompt (2 blocks): restoring still gains ~30K tokens
    other = CH[:2] + ["x2", "x3"]
    assert choose_restore(CH, {"h4": 40000}, [other], OFF) == "h4"
    assert engine_depth(CH, [other]) == 2


def test_small_gain_skipped():
    off = [0, 100, 200, 300, 400, 500, 600]
    assert choose_restore(CH, {"h4": 500}, [], off) is None


def test_save_rules():
    assert not should_save(5000, CH, {})
    assert should_save(12000, CH, {})
    assert not should_save(12000, CH, {"h5": 12000})            # this exact prompt already saved
    assert not should_save(14000, CH, {"h3": 10000})            # grew only 4K past the last snapshot
    assert should_save(20000, CH, {"h3": 10000})


def test_save_matches():
    assert save_matches(10100, 10000, 100)
    assert not save_matches(5000, 10000, 100)                    # another conversation was saved
    assert not save_matches(None, 10000, 100)


def test_snapshot_value_ordering():
    big_cold = snapshot_value(hits=0, age_hours=48, recompute_ms=200000, size_bytes=2 << 30, pinned=False)
    small_hot = snapshot_value(hits=5, age_hours=1, recompute_ms=20000, size_bytes=500 << 20, pinned=False)
    assert small_hot > big_cold
    assert snapshot_value(0, 1000, 1, 1 << 30, pinned=True) == float("inf")
    # same snapshot loses value as it ages
    assert snapshot_value(1, 1, 50000, 1 << 30, False) > snapshot_value(1, 30, 50000, 1 << 30, False)


def test_pin_only_most_used_head():
    from strata_context_manager.policy import pin_allowed
    counts = {"hermes": 40, "oneoff": 1}
    assert pin_allowed("hermes", counts)
    assert not pin_allowed("oneoff", counts)                     # a one-off client cannot steal Hermes' pin
    assert pin_allowed("a", {"a": 3, "b": 3})                    # tie: both may ask
    assert not pin_allowed(None, counts)
    assert not pin_allowed("new", {})                            # not even counted: no pin
